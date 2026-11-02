"""Shared helpers for U2's tests (plan D-CG-U2-5). Not collected: the name has no `test_`.

Hand-written fakes only, never a mocking library (team.md § Testing Posture). Each U2 test
module still carries its own copy of the autouse socket guard and its self-test (TS-U2-10),
so importing this module changes no other unit's tests.

* `c2_problems` - the hand-written C2 validator (TS-U2-9, LC-U2-16).
* `count_sends` and the three canned answers - the botocore `before-send` SendCounter
  (TS-U2-3, LC-U2-15). The hook fires once per attempt INSIDE botocore's retry loop and
  answers before any socket is opened, which is why it can see retries and a Stubber cannot.
* The fakes: clocks, Converse clients, routers and sinks.
* `failure_lines` - the caplog direct-attach fixture (SD-U2-13). It attaches
  `caplog.handler` to the named loggers itself, so no assertion depends on whether pytest
  happened to attach it to a non-propagating logger first (M-5).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ReadTimeoutError

from delphi.agent.bedrock import ConverseClient, ConverseResult, GenerationParams
from delphi.agent.telemetry import DecisionRecord, TelemetrySink
from delphi.router.base import Decision, Router, RouterError
from delphi.router.baselines import AlwaysModelRouter

# ---------------------------------------------------------------------------------- C2
C2_REQUIRED = ("schema_version", "request_id", "arm", "timestamp", "cold_start")
C2_ERROR_KINDS = {"request_invalid", "request_too_large", "routing_failed",
                  "generation_failed", "internal_error"}
_ARM = re.compile(r"^(router|pinned:[a-z0-9-]+|ipr)$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def c2_problems(record: dict) -> list[str]:
    """Every way `record` (a decoded JSON object) breaks contract C2. Empty means valid.

    additionalProperties is true: unknown keys are accepted, never reported.
    """
    out = [f"missing required {k}" for k in C2_REQUIRED if k not in record]

    def check(name, ok, nullable=True):
        if name in record and not (ok(record[name]) or (nullable and record[name] is None)):
            out.append(f"{name}: bad value {record[name]!r}")

    check("schema_version", lambda v: _is_int(v) and v >= 1, nullable=False)
    check("request_id", lambda v: isinstance(v, str) and 1 <= len(v) <= 256, nullable=False)
    check("arm", lambda v: isinstance(v, str) and bool(_ARM.match(v)), nullable=False)
    check("timestamp", lambda v: isinstance(v, str) and bool(_TIMESTAMP.match(v)),
          nullable=False)
    check("cold_start", lambda v: isinstance(v, bool), nullable=False)
    check("routing_overhead_ms", lambda v: _is_number(v) and v >= 0)
    check("embed_latency_ms", lambda v: _is_number(v) and v >= 0)
    check("decision_latency_ms", lambda v: _is_number(v) and v >= 0)
    check("error_kind", lambda v: v in C2_ERROR_KINDS)
    check("error_detail", lambda v: isinstance(v, str) and len(v) <= 512)
    check("prompt_sha256", lambda v: isinstance(v, str) and bool(_HEX64.match(v)))
    check("chosen_alias", lambda v: isinstance(v, str))
    check("chosen_model_id", lambda v: isinstance(v, str))
    check("scores", lambda v: isinstance(v, dict) and all(_is_number(x) for x in v.values()),
          nullable=False)
    check("input_tokens", lambda v: _is_int(v) and v >= 0)
    check("output_tokens", lambda v: _is_int(v) and v >= 0)
    check("cost_usd", lambda v: _is_number(v) and v >= 0)
    check("is_fallback", lambda v: isinstance(v, bool), nullable=False)
    check("artifact_hash", lambda v: isinstance(v, str))
    check("extra", lambda v: isinstance(v, dict), nullable=False)
    if ("error_kind" in record or "error_detail" in record) and (
            (record.get("error_kind") is None) != (record.get("error_detail") is None)):
        out.append("error_kind and error_detail must be both set or both null")
    return out


def record_dict(record: DecisionRecord) -> dict:
    """The record exactly as it travels: through its own JSON form."""
    return json.loads(record.to_json())


# ------------------------------------------------------------------------ botocore sends
class _Raw:
    """The minimal raw-body object AWSResponse.content reads."""

    def __init__(self, body: bytes):
        self._body = body

    def stream(self, **_kwargs):
        yield self._body


def throttling_429(request) -> AWSResponse:
    body = b'{"message":"Rate exceeded"}'
    headers = {"x-amzn-ErrorType": "ThrottlingException", "Content-Type": "application/json"}
    return AWSResponse(request.url, 429, headers, _Raw(body))


CONVERSE_200_BODY = {
    "output": {"message": {"role": "assistant", "content": [{"text": "a canned answer"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
    "metrics": {"latencyMs": 12},
}


def converse_200(request) -> AWSResponse:
    body = json.dumps(CONVERSE_200_BODY).encode()
    return AWSResponse(request.url, 200, {"Content-Type": "application/json"}, _Raw(body))


def read_timeout(request) -> AWSResponse:
    raise ReadTimeoutError(endpoint_url=request.url)


class SendCounter:
    """A `before-send` handler that counts attempts and answers each one."""

    def __init__(self, answer):
        self.n = 0
        self._answer = answer

    def __call__(self, request, **_kwargs):
        self.n += 1
        return self._answer(request)


def count_sends(client, answer) -> SendCounter:
    counter = SendCounter(answer)
    client.meta.events.register("before-send.bedrock-runtime.Converse", counter)
    return counter


@pytest.fixture
def dummy_aws(monkeypatch):
    """Dummy credentials, IMDS off, no profile (SD-U2-21). On the author's machine the
    real profile is the production account, so the chain must never reach it."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def no_backoff(monkeypatch):
    """botocore sleeps between retries in endpoint.py; replace that sleep with a no-op.

    An attribute assignment, not a call, so no test sleeps (NFR4.3, SD-U2-22).
    """
    import botocore.endpoint

    monkeypatch.setattr(botocore.endpoint.time, "sleep", lambda _seconds: None)


# ------------------------------------------------------------------------------- clocks
class FakeClock:
    """A monotonic clock in seconds that only moves when a fake advances it."""

    def __init__(self, start: float = 100.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance_ms(self, ms: float) -> None:
        self.t += ms / 1000.0


WALL = datetime(2026, 10, 19, 8, 15, 30, 123456, tzinfo=timezone.utc)


def fake_wall() -> datetime:
    return WALL


# ------------------------------------------------------------------------- converse fakes
class CountingConverse(ConverseClient):
    """Succeeds, counts calls, and optionally advances a fake clock inside the call."""

    def __init__(self, *, clock: FakeClock | None = None, advance_ms: float = 0.0,
                 input_tokens: int = 3, output_tokens: int = 2):
        self.calls: list[tuple[str, str, GenerationParams]] = []
        self._clock = clock
        self._advance = advance_ms
        self._tokens = (input_tokens, output_tokens)

    def converse(self, model_id: str, prompt: str, params: GenerationParams) -> ConverseResult:
        self.calls.append((model_id, prompt, params))
        if self._clock is not None:
            self._clock.advance_ms(self._advance)
        return ConverseResult(text="ok", input_tokens=self._tokens[0],
                              output_tokens=self._tokens[1], model_id=model_id)


class RaisingConverse(ConverseClient):
    """Raises what `make(prompt)` returns, the way the real client fails."""

    def __init__(self, make=lambda _prompt: RuntimeError("ThrottlingException: rate exceeded")):
        self.calls = 0
        self._make = make
        self.raised: list[BaseException] = []

    def converse(self, model_id: str, prompt: str, params: GenerationParams) -> ConverseResult:
        self.calls += 1
        exc = self._make(prompt)
        self.raised.append(exc)
        raise exc


# --------------------------------------------------------------------------- router fakes
class SpyRouter(Router):
    """Counts predict calls. Zero on every rejection is the "no spend" proof (BR3.5)."""

    def __init__(self, pool, alias: str = "mid"):
        self._inner = AlwaysModelRouter(pool, alias)
        self.pool_aliases = pool.aliases
        self.calls = 0

    def predict(self, query: str) -> Decision:
        self.calls += 1
        return self._inner.predict(query)


class RaisingRouter(Router):
    """A router that raises from predict, as any router may. After U4 the agent's fallback
    applies to it like every other router (BR1.1); it alone never yields routing_failed."""

    def __init__(self, pool, make=lambda q: RouterError("no route for this query")):
        self.pool_aliases = pool.aliases
        self._make = make

    def predict(self, query: str) -> Decision:
        raise self._make(query)


class TimedRouter(Router):
    """AlwaysModelRouter that spends `ms` of fake time deciding."""

    def __init__(self, pool, clock: FakeClock, ms: float, alias: str = "mid"):
        self._inner = AlwaysModelRouter(pool, alias)
        self.pool_aliases = pool.aliases
        self._clock = clock
        self._ms = ms

    def predict(self, query: str) -> Decision:
        self._clock.advance_ms(self._ms)
        return self._inner.predict(query)


class ClockedEmbedder:
    """An Embedder-shaped fake that spends `ms` of fake time per call."""

    dim = 2

    def __init__(self, clock: FakeClock, ms: float):
        self._clock = clock
        self._ms = ms

    def embed(self, query: str) -> list[float]:
        self._clock.advance_ms(self._ms)
        return [float(len(query)), 1.0]


def learned_router(pool, *, clock=None, embed_ms: float | None = 2.0, score_ms: float = 0.0,
                   scorer=None):
    """A real LearnedRouter over `pool`, with a fake embedder and scorer on a fake clock."""
    from delphi.router.learned import Artifact, LearnedRouter

    clock = clock or FakeClock()

    def _score(_features, aliases):
        clock.advance_ms(score_ms)
        return {a: float(i) for i, a in enumerate(aliases)}

    art = Artifact("linear", pool.aliases, "hashing", "local", "hash-u2-test")
    embedder = ClockedEmbedder(clock, embed_ms) if embed_ms is not None else None
    return LearnedRouter(pool, art, embedder=embedder, scorer=scorer or _score, mono=clock)


# ----------------------------------------------------------------------------- sink fakes
class OrderedSink(TelemetrySink):
    """Records by identity, and writes "emit" into a trace the test shares."""

    def __init__(self, trace: list[str] | None = None):
        self.records: list[DecisionRecord] = []
        self.trace = trace if trace is not None else []

    def emit(self, record: DecisionRecord) -> None:
        self.records.append(record)
        self.trace.append("emit")


class RaisingSink(TelemetrySink):
    """The sink is down. Keeps what it was handed so a test can compare."""

    def __init__(self, message: str = "sink down"):
        self.attempted: list[DecisionRecord] = []
        self.raised: list[BaseException] = []
        self._message = message

    def emit(self, record: DecisionRecord) -> None:
        self.attempted.append(record)
        exc = RuntimeError(self._message)
        self.raised.append(exc)
        raise exc


# ------------------------------------------------------------------------------- logging
class Lines:
    """Failure lines captured from named loggers, decoded."""

    def __init__(self, caplog):
        self._caplog = caplog

    def records(self, name: str = "delphi.agent") -> list[logging.LogRecord]:
        """One entry per LogRecord. caplog.handler is attached to the named logger AND, by
        pytest, to the root logger, so a record from a logger that still propagates reaches
        it twice; the same object twice is one emitted line, not two."""
        seen: set[int] = set()
        out = []
        for r in self._caplog.records:
            if r.name == name and id(r) not in seen:
                seen.add(id(r))
                out.append(r)
        return out

    def json(self, name: str = "delphi.agent") -> list[dict]:
        return [json.loads(r.getMessage()) for r in self.records(name)]


@pytest.fixture
def failure_lines(caplog):
    names = ("delphi.agent", "delphi.eval", "delphi.decision")
    loggers = [logging.getLogger(n) for n in names]
    for lg in loggers:
        lg.addHandler(caplog.handler)  # addHandler ignores a duplicate (SD-U2-13)
    caplog.handler.setLevel(logging.DEBUG)
    try:
        yield Lines(caplog)
    finally:
        for lg in loggers:
            lg.removeHandler(caplog.handler)
