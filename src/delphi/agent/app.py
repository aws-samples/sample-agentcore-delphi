"""Router agent entrypoint for AgentCore Runtime.

Pipeline per request (design §2): validate -> router.predict -> Converse(chosen) ->
emit decision telemetry. The identical container serves the pinned-model comparator arms
by bypassing the router (D4) — controlled by the DELPHI_ARM environment variable so the
only independent variable across arms is the routing decision (T-INF-1).

Every invocation leaves through one of two paths, and both build the DecisionRecord the
same way (U2 LC-U2-8):

* success - build the record, emit it, then return the C1 envelope with the same record
  nested in it (U1's envelope, unchanged by U2);
* failure - build a failure record from what the invocation had observed, emit it, write
  one `delphi.agent` line, then raise: RequestRejected for a rejected payload, otherwise the
  ORIGINAL exception (a 500 on the wire). Never a 200 that carries an error (D-1).

So exactly one record reaches the sink per invocation (BR1.5). The two named exceptions:
the sink itself fails (the record then survives as `record_json` in the record_emit_failed
line), and a defect in the failure-record build itself, which the suite's per-workflow C2
checks exist to catch before deploy (FM-14).

The BedrockAgentCoreApp wrapper is imported lazily so unit tests can exercise
`handle_request` directly without the AgentCore SDK installed.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from delphi import failure_log
from delphi.agent.bedrock import ConverseClient, GenerationParams
from delphi.agent.inbound import InboundRequest, RequestRejected, _Rejection, validate_inbound
from delphi.agent.telemetry import (
    REJECTION_KINDS,
    STAGE_BY_KIND,
    DecisionRecord,
    ErrorKind,
    TelemetrySink,
    failure_fields,
    format_detail,
    success_fields,
)
from delphi.config import Pool
from delphi.eval.cost import Usage, query_cost
from delphi.router.base import Decision, Router, RouterError

__all__ = ["RESPONSE_SCHEMA_VERSION", "RequestRejected", "RouterAgent", "build_agent_from_env"]

#: Version of the invoke RESPONSE envelope (contract C1). Additive changes keep this; a
#: breaking change bumps it and is stated in the published material. Distinct from the
#: record's own schema_version (C2), which agent/telemetry.py defines.
RESPONSE_SCHEMA_VERSION = 1

#: The failure channel's logger for the request path. Never "delphi.decision", so the
#: decision stream stays one C2 record per line (C2 § CloudWatch transport).
AGENT_LOGGER = "delphi.agent"

#: The furthest step an invocation reached -> the kind of a failure raised there. "received"
#: is reachable only through a validator defect: every rejection arrives as a _Rejection.
_KIND_BY_STEP = {
    "received": ErrorKind.REQUEST_INVALID,
    "validated": ErrorKind.ROUTING_FAILED,
    "selected": ErrorKind.GENERATION_FAILED,
    "generated": ErrorKind.INTERNAL_ERROR,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339_ms(moment: datetime) -> str:
    """RFC 3339, UTC, millisecond precision, "Z" suffix (BR5.1, TS-U2-5)."""
    text = moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return text.replace("+00:00", "Z")


def _prompt_digest(encoded: bytes) -> str:
    """prompt_sha256 (P-2). Called only after the routing window has closed (NFR1.4)."""
    return hashlib.sha256(encoded).hexdigest()


def _write(level: int, event: str, **fields) -> None:
    failure_log.write(failure_log.failure_logger(AGENT_LOGGER), level, event, **fields)


def _resolve_fallback_alias(pool: Pool, fallback_alias: str | None) -> str:
    """The alias the agent routes to when the router fails (ENT-401, BR1.3).

    The ONE derivation: an explicit alias is honoured; otherwise the strongest candidate by
    input + output price, the first in pool order on a tie (max keeps the first). An alias
    the pool does not hold raises ValueError at construction, so a bad configuration stops
    the container before /ping rather than at the first failing request.
    """
    if fallback_alias is None:
        return max(pool.candidates, key=lambda c: c.price.input + c.price.output).alias
    if fallback_alias not in pool.aliases:
        raise ValueError(f"fallback_alias {fallback_alias!r} is not in the pool {pool.aliases}")
    return fallback_alias


def _check_decision(d, pool: Pool) -> None:
    """The agent's own check of what a router returned (BR1.2). Pure pool lookups, no I/O.

    The agent does not trust a router to have called Router._validate: a result that is not
    a Decision, an alias outside the pool, a model id that is not that alias's id, or an
    embed_ms that is not a finite number >= 0 raises RouterError, which `_route` turns into
    the fallback. This is what keeps an arm from invoking a model outside its pool (D4)
    whatever router is wired in.
    """
    if not isinstance(d, Decision):
        raise RouterError(f"router returned {type(d).__name__}, not a Decision")
    if d.chosen_alias not in pool.aliases:
        raise RouterError(f"router chose {d.chosen_alias!r}, not in the pool {pool.aliases}")
    expected = pool.by_alias(d.chosen_alias).id
    if d.model_id != expected:
        raise RouterError(f"router model id {d.model_id!r} is not {d.chosen_alias!r}'s "
                          f"id {expected!r}")
    embed = d.metadata.get("embed_ms") if isinstance(d.metadata, dict) else None
    if embed is not None and not (isinstance(embed, (int, float)) and not isinstance(embed, bool)
                                  and math.isfinite(embed) and embed >= 0):
        raise RouterError(f"router embed_ms {embed!r} is not a finite number >= 0")


def _routing_facts(d: Decision) -> dict:
    """The routing step's record fields from a checked Decision (BR1.7: shape unchanged)."""
    embed = d.metadata.get("embed_ms")
    return dict(
        chosen_alias=d.chosen_alias, chosen_model_id=d.model_id,
        scores={k: v for k, v in d.scores.items() if not k.startswith("__")},
        embed_latency_ms=float(embed) if embed is not None else None,
        decision_latency_ms=d.latency_ms, is_fallback=d.is_fallback,
    )


def _build_fallback(agent: RouterAgent, req: InboundRequest, exc: Exception) -> dict:
    """The ONE fallback implementation (LC-U4-3, ENT-402), for every router type.

    Builds the FallbackDecision for the agent's fallback alias - every pool alias scored
    0.0, latency 0.0, is_fallback True, the same values the record carried before U4 - writes
    exactly one WARNING router_fallback line with the prompt redacted (BR1.4), and returns
    the routing facts. Nothing from the rejected result reaches the record. It reads no
    clock (SD-U4-7). It raises only on an agent defect; that exception leaves `_route` with
    the router's exception as its __context__, and is the one router-arm source of
    routing_failed (BR1.6, SD-U4-2). A module-level function, so a test can patch it.
    """
    alias = agent.fallback_alias
    detail = format_detail(exc, redact=(req.prompt,))
    fallback = Decision(
        model_id=agent.pool.by_alias(alias).id,
        chosen_alias=alias,
        scores={a: 0.0 for a in agent.pool.aliases},
        latency_ms=0.0,
        is_fallback=True,
        metadata={"policy": "fallback", "error": detail},
    )
    _write(logging.WARNING, "router_fallback", request_id=req.request_id, arm=agent.arm,
           error_kind=None, detail=detail)
    return _routing_facts(fallback)


class ColdStartLatch:
    """True for exactly one caller per agent instance, whatever its outcome (ENT-205).

    A test-and-set under a lock, so concurrent first invocations still produce one true.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consumed = False

    def consume(self) -> bool:
        with self._lock:
            first = not self._consumed
            self._consumed = True
            return first


class RouterAgent:
    """Framework-agnostic core. AgentCore's entrypoint (below) delegates to this."""

    def __init__(
        self,
        pool: Pool,
        router: Router,
        converse: ConverseClient,
        sink: TelemetrySink,
        *,
        arm: str = "router",
        pinned_alias: str | None = None,
        fallback_alias: str | None = None,
        params: GenerationParams | None = None,
        mono=time.perf_counter,  # monotonic clock, seconds (BR4.6)
        wall=_utc_now,  # wall clock, an aware datetime (BR4.6)
    ):
        self.pool = pool
        self.router = router
        self.converse = converse
        self.sink = sink
        self.arm = arm
        self.params = params or GenerationParams()
        self._mono = mono
        self._wall = wall
        self._latch = ColdStartLatch()
        # For pinned arms the router is bypassed entirely (D4).
        self._pinned_alias = pinned_alias
        if arm.startswith("pinned") and pinned_alias is None:
            raise ValueError("pinned arm requires pinned_alias")
        if pinned_alias is not None and pinned_alias not in pool.aliases:
            # ND-1: fail at construction, never as routing_failed at request time (SD-U4-2).
            raise ValueError(f"pinned_alias {pinned_alias!r} is not in the pool {pool.aliases}")
        # Resolved and validated on every arm, so construction behaves the same everywhere;
        # unused on a pinned arm, where the router is bypassed (ENT-401, D4).
        self.fallback_alias = _resolve_fallback_alias(pool, fallback_alias)
        failure_log.failure_logger(AGENT_LOGGER)

    @property
    def _is_pinned(self) -> bool:
        return self.arm.startswith("pinned")

    def _route(self, req: InboundRequest) -> dict:
        """The routing step's facts. On a pinned arm there is no router call, so the three
        latency figures are None and scores is {} (BR1.3, BR4.2).

        On a router arm this is the request path's fallback guarantee (ADR-005, BR1.1): it
        calls `router.predict` and nothing else on the router, then the agent's own decision
        check, both inside one guarded region. Any Exception from either - a RouterError, any
        other error, a malformed result - becomes the agent's fallback decision, for every
        router type, and the invocation continues to Converse with the fallback model.
        BaseException (KeyboardInterrupt, SystemExit) is never caught.

        When routing_failed is still emitted (D-CG-U4-11, SD-U4-2): on a router arm, only when
        `_build_fallback` itself raises, or when `_routing_facts` or the routing-window close
        that follows raises - each an agent defect. A defect inside `_check_decision` is
        reported as a fallback (is_fallback true), deliberately; NFR1.2's rejection of
        fallback records from a latency sample is the backstop for that case.
        """
        if self._is_pinned:
            c = self.pool.by_alias(self._pinned_alias)
            return dict(chosen_alias=self._pinned_alias, chosen_model_id=c.id, scores={},
                        embed_latency_ms=None, decision_latency_ms=None, is_fallback=False)
        try:
            d = self.router.predict(req.prompt)
            _check_decision(d, self.pool)
        except Exception as exc:  # noqa: BLE001 - any router failure becomes the fallback (BR1.1)
            return _build_fallback(self, req, exc)
        return _routing_facts(d)

    def handle_request(self, payload: dict) -> dict:
        # Receipt: the window opens and the latch is consumed before anything can fail.
        t_received = self._mono()
        facts: dict = dict(
            request_id=None, arm=self.arm, timestamp=_rfc3339_ms(self._wall()),
            cold_start=self._latch.consume(), routing_overhead_ms=None,
            artifact_hash=None if self._is_pinned else getattr(
                getattr(self.router, "artifact", None), "artifact_hash", None),
        )
        step, req = "received", None
        try:
            req = validate_inbound(payload)
            facts["request_id"], step = req.request_id, "validated"
            facts.update(self._route(req))
            if not self._is_pinned:
                # The window closes here: routing and pool lookup are in it, nothing after.
                facts["routing_overhead_ms"] = (self._mono() - t_received) * 1000.0
            step = "selected"
            result = self.converse.converse(facts["chosen_model_id"], req.prompt, self.params)
            step = "generated"
            facts.update(input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                         prompt_sha256=_prompt_digest(req.encoded))
            facts["cost_usd"] = query_cost(
                self.pool, Usage(facts["chosen_alias"], result.input_tokens,
                                 result.output_tokens))
            record = DecisionRecord(**success_fields(**facts))
        except _Rejection as rejection:
            facts["request_id"] = rejection.request_id
            self._fail(rejection.error_kind, rejection.detail, facts)
            raise RequestRejected(rejection.error_kind, rejection.detail) from None
        except Exception as exc:
            if req is not None:
                facts.setdefault("prompt_sha256", _prompt_digest(req.encoded))
            redact = (req.prompt,) if req is not None else ()
            self._fail(_KIND_BY_STEP[step], format_detail(exc, redact=redact), facts)
            raise
        # Emit BEFORE assembling the response, so the telemetry stream is never missing a
        # record the caller already holds (BR1.2).
        self._emit_or_log(record)
        return self._envelope(record, result)

    def _fail(self, kind: ErrorKind, detail: str, facts: dict) -> None:
        """Build the failure record, emit it, write the one request line (BR2.4).

        If the emit itself fails, _emit_or_log writes record_emit_failed INSTEAD of the
        request line and raises the sink's exception (FM-12): still one line per failure.
        """
        facts["request_id"] = facts.get("request_id") or uuid.uuid4().hex
        record = DecisionRecord(**failure_fields(stage=STAGE_BY_KIND[kind], error_kind=kind,
                                                 detail=detail, **facts))
        self._emit_or_log(record)
        if kind in REJECTION_KINDS:
            level, event = logging.WARNING, "request_rejected"
        else:
            level, event = logging.ERROR, "request_failed"
        _write(level, event, request_id=record.request_id, arm=self.arm,
               error_kind=record.error_kind, detail=record.error_detail)

    def _emit_or_log(self, record: DecisionRecord) -> None:
        """Hand the record to the sink. If the sink fails, the record survives in the
        record_emit_failed line as an opaque JSON string, and the sink's error propagates."""
        try:
            self.sink.emit(record)
        except Exception as exc:
            _write(logging.ERROR, "record_emit_failed", request_id=record.request_id,
                   arm=record.arm, error_kind=record.error_kind, detail=format_detail(exc),
                   record_json=record.to_json())
            raise

    def _envelope(self, record: DecisionRecord, result) -> dict:
        """C1's envelope, unchanged by U2 (BR10.3): same keys, same order, same values."""
        return {
            "request_id": record.request_id,
            "arm": self.arm,
            "chosen_model": record.chosen_alias,
            "scores": dict(record.scores),
            # C1 types this a number, so a pinned arm keeps 0.0 here while the RECORD
            # carries null: two owners, two contracts (BR4.2).
            "decision_latency_ms": (record.decision_latency_ms
                                    if record.decision_latency_ms is not None else 0.0),
            "output": result.text,
            "usage": {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
            "cost_usd": record.cost_usd,
            "is_fallback": record.is_fallback,
            # --- the two additive keys U1 owns (contract C1, ADR-010) ---
            # Envelope version. NOT the record's: the record carries its own
            # schema_version (C2, agent/telemetry.py). Two versions, independent.
            "schema_version": RESPONSE_SCHEMA_VERSION,
            # The SAME instance emitted above, serialised as a nested OBJECT. Not
            # `record.to_json()`, which returns a string - a JSON-encoded string inside a
            # JSON response would force a double decode and break both C1's $ref and the
            # `delphi-verify --json` contract a reader pipes into jq (BR1.1, BR1.3).
            "decision_record": dataclasses.asdict(record),
        }


def build_agent_from_env() -> RouterAgent:
    """Construct the agent from environment/config. Called by the AgentCore entrypoint.

    DELPHI_ARM selects the arm: "router" (default), "pinned:<alias>", or "ipr".
    Router artifact + encoder are pulled from S3 (DELPHI_ARTIFACT_URI) at cold start,
    or baked into the image.

    Fail fast is unchanged: a construction error still kills the container before /ping.
    It now leaves one CRITICAL agent_construction_failed line first (BR6.3), so the arm
    does not die with only an interpreter traceback.
    """
    arm = os.environ.get("DELPHI_ARM", "router")
    try:
        return _build_agent(arm)
    except Exception as exc:
        _write(logging.CRITICAL, "agent_construction_failed", request_id=None, arm=arm,
               error_kind=None, detail=format_detail(exc))
        raise


def _build_agent(arm: str) -> RouterAgent:
    from delphi.agent.bedrock import BedrockConverseClient, RegionNotSetError
    from delphi.agent.telemetry import OTelSink
    from delphi.config import load_pool

    pool = load_pool()
    region = os.environ.get("AWS_REGION")
    if not region:
        # No default region (U6 BR11.7): the stack sets AWS_REGION on every arm, so its
        # absence is a broken environment, reported before /ping rather than guessed past.
        raise RegionNotSetError("AWS_REGION is unset; the agent has no default region")
    converse = BedrockConverseClient(region=region)
    sink = OTelSink()

    if arm.startswith("pinned"):
        from delphi.router.baselines import AlwaysModelRouter

        alias = arm.split(":", 1)[1]
        return RouterAgent(
            pool, AlwaysModelRouter(pool, alias), converse, sink,
            arm=arm, pinned_alias=alias,
        )

    if arm == "ipr":
        raise NotImplementedError("IPR comparator arm is wired in a later phase")

    artifact_uri = os.environ.get("DELPHI_ARTIFACT_URI")
    if not artifact_uri:
        raise RuntimeError(
            "router arm requires DELPHI_ARTIFACT_URI (s3://... or a local path)"
        )
    return _load_router_arm(pool, artifact_uri, converse, sink)


def _load_router_arm(
    pool: Pool, artifact_uri: str, converse: ConverseClient, sink: TelemetrySink
) -> RouterAgent:
    """Learned router arm (default). Load the trained artifact from DELPHI_ARTIFACT_URI
    (an s3:// URI or a local path baked into the image) and build a LearnedRouter. The
    fallback alias is the agent's (BR1.3), so nothing here derives one. Exercised offline by
    T-BND-U4-9 over a locally generated artifact."""
    from delphi.train.artifact_store import load_learned_router

    router = load_learned_router(artifact_uri, pool)
    return RouterAgent(pool, router, converse, sink, arm="router")
