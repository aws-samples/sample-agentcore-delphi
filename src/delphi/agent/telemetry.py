"""Decision telemetry (design D6) — first-class, or the statistical eval is impossible.

Every invocation emits exactly one DecisionRecord, on the success path and on every failure
path: chosen model, per-candidate scores, the three nested latency figures, token counts,
computed cost, the fallback flag, and - on a failure - what failed (ENT-201, ENT-203). In
production the record is one JSON line on the `delphi.decision` logger, which the runtime
ships to CloudWatch. Here we define the record schema, the field builders the agent uses,
and a pluggable sink so tests can assert completeness (T-INF-3) without standing up OTEL.

Two record shapes exist in the wild, and a reader tells them apart by `schema_version`:

* A record WITHOUT `schema_version` is legacy (emitted before U2). Its `embed_latency_ms`
  is the structural 0.0 the old request path wrote, and it is not a measurement.
* A record WITH `schema_version` (this module's DECISION_RECORD_SCHEMA_VERSION) is
  versioned. Its `embed_latency_ms` is the router's own timing of the encoding step, or
  null where there is no encoding step (pinned arms, routers without an embedder).

The change between them is additive only (ADR-004, BR1.2): no field was removed, renamed or
narrowed, and seven fields were widened to nullable where the presence matrix needs it.
"""
from __future__ import annotations

import abc
import dataclasses
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum

#: The RECORD's version (contract C2). Independent of the response envelope's own version,
#: which lives in agent/app.py and is U1's. Neither is derived from the other.
DECISION_RECORD_SCHEMA_VERSION = 1

#: The one field that carries the measured embed figure (P-1, TS-U2-8). The record build and
#: delphi-verify's renderer both read this name, so the two cannot drift apart.
EMBED_FIGURE_FIELD = "embed_latency_ms"

#: Upper bound on error_detail and on a failure line's detail (BR2.2).
DETAIL_MAX_CHARS = 512

#: Shorter redaction targets are not removed: they collide with ordinary words in an error
#: message and would corrupt the detail without protecting anything worth protecting.
_REDACT_MIN_CHARS = 8
_REDACTED = "[redacted]"


class ErrorKind(StrEnum):
    """Agent-side invocation failures (ENT-203, OQ2). A closed set of five.

    It shares no member name with delphi-verify's client-side FailureKind (R-29, BR2.7):
    the client sees `agent_error` when the agent raised, and this field says why.
    """

    REQUEST_INVALID = "request_invalid"
    REQUEST_TOO_LARGE = "request_too_large"
    ROUTING_FAILED = "routing_failed"
    GENERATION_FAILED = "generation_failed"
    INTERNAL_ERROR = "internal_error"


#: The rejections a caller caused. They are raised as RequestRejected, never re-raised raw.
REJECTION_KINDS = frozenset({ErrorKind.REQUEST_INVALID, ErrorKind.REQUEST_TOO_LARGE})

#: How far an invocation got before each kind of failure (ENT-203 stage_reached). It decides
#: which fields a failure record can know (BR2.3).
STAGE_BY_KIND = {
    ErrorKind.REQUEST_INVALID: "received",
    ErrorKind.REQUEST_TOO_LARGE: "received",
    ErrorKind.ROUTING_FAILED: "received",
    ErrorKind.GENERATION_FAILED: "selected",
    ErrorKind.INTERNAL_ERROR: "generated",
}


@dataclass(frozen=True)
class DecisionRecord:
    """One invocation's telemetry (ENT-201). Null always means UNKNOWN, never zero.

    Each field's comment FOLLOWS the field, so a field's definition is its line plus the
    comment lines up to the next field. The suite reads definitions that way (SD-U2-2).
    """

    request_id: str
    arm: str  # "router" | "pinned:<alias>" | "ipr"
    chosen_alias: str | None
    # Null only when the failure preceded selection (request_*, routing_failed).
    chosen_model_id: str | None
    # As chosen_alias. On generation_failed it names the model that failed.
    scores: dict[str, float]
    # Always a mapping, never null: {} on pinned arms and before routing (C1/C2).
    embed_latency_ms: float | None
    # Versioned records: the router's timing of the encoding step, or null where there is
    # none. Legacy records: the structural 0.0, not a measurement.
    decision_latency_ms: float | None
    # The router's own wall time (embed plus score). Null on pinned arms. Nested inside
    # routing_overhead_ms; the three figures are never summed (BR4.4).
    input_tokens: int | None
    # Populated if and only if the Converse call returned.
    output_tokens: int | None
    cost_usd: float | None
    # Populated if and only if the single cost path (config.Price.cost -> query_cost) ran.
    is_fallback: bool
    # ROUTER fallback only, never a generation failure (BR1.6).
    # ---- replayability manifest inputs (T-INF-4)
    artifact_hash: str | None = None
    extra: dict[str, object] = field(default_factory=dict)
    # ---- added by U2: optional with defaults, so the pre-U2 thirteen still construct it.
    # Every record the agent emits sets all of them; C2's required set is checked on every
    # emitted record by the suite's C2 validator rather than here (D-CG-U2-9).
    schema_version: int = DECISION_RECORD_SCHEMA_VERSION
    timestamp: str | None = None
    # Wall-clock receipt instant: RFC 3339, UTC, milliseconds, "Z" suffix.
    cold_start: bool | None = None
    # True on exactly the first invocation this agent instance handled, whatever its outcome.
    routing_overhead_ms: float | None = None
    # Monotonic milliseconds from receipt in handle_request to immediately before the
    # Converse call. Router arm only; null on pinned arms and on a failure before the
    # window closed. Generation, cost, emission and artifact load are outside the window.
    # A single value is not evidence about the NFR1 budget.
    # Only the committed on-architecture sample, recomputed, can speak to that (BR9.x).
    error_kind: str | None = None
    # Null exactly when the invocation succeeded (BR2.1). One of ErrorKind.
    error_detail: str | None = None
    # "<Type>: <msg>" or "<Type>[<Code>]: <msg>", at most DETAIL_MAX_CHARS, no prompt text.
    prompt_sha256: str | None = None
    # Lowercase hex SHA-256 of the UTF-8 prompt, computed after the routing window closes.
    # It lets the NFR1 sample check confirm which prompt a record answered (P-2, BR9.2).
    # It is NOT anonymisation: anyone reading the log can confirm a guess at a guessable
    # prompt by hashing it. Disclosure in the published material is U7's (NR-5).

    def __post_init__(self) -> None:
        if self.error_kind is not None and self.error_kind not in set(ErrorKind):
            raise ValueError(f"error_kind {self.error_kind!r} is not one of "
                             f"{sorted(k.value for k in ErrorKind)}")
        if (self.error_kind is None) != (self.error_detail is None):
            raise ValueError("error_kind and error_detail are both set or both null "
                             f"(got {self.error_kind!r} / {self.error_detail!r})")

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True)


def success_fields(
    *,
    request_id: str,
    arm: str,
    timestamp: str,
    cold_start: bool,
    chosen_alias: str,
    chosen_model_id: str,
    scores: dict[str, float],
    embed_latency_ms: float | None,
    decision_latency_ms: float | None,
    routing_overhead_ms: float | None,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    is_fallback: bool,
    artifact_hash: str | None,
    prompt_sha256: str | None,
) -> dict:
    """Keyword arguments for a success DecisionRecord (BR1.1, BR1.3).

    A dict rather than the record, so the agent constructs the record itself through the
    module-level name `DecisionRecord` - which is what U1's T-AR-4 counts (D-CG-U2-1).
    """
    return _record_fields(
        request_id=request_id, arm=arm, timestamp=timestamp, cold_start=cold_start,
        chosen_alias=chosen_alias, chosen_model_id=chosen_model_id, scores=scores,
        embed_latency_ms=embed_latency_ms, decision_latency_ms=decision_latency_ms,
        routing_overhead_ms=routing_overhead_ms, input_tokens=input_tokens,
        output_tokens=output_tokens, cost_usd=cost_usd, is_fallback=is_fallback,
        artifact_hash=artifact_hash, prompt_sha256=prompt_sha256,
        error_kind=None, error_detail=None,
    )


def failure_fields(
    *,
    stage: str,
    error_kind: str,
    detail: str,
    request_id: str,
    arm: str,
    timestamp: str,
    cold_start: bool,
    artifact_hash: str | None = None,
    chosen_alias: str | None = None,
    chosen_model_id: str | None = None,
    scores: dict[str, float] | None = None,
    embed_latency_ms: float | None = None,
    decision_latency_ms: float | None = None,
    routing_overhead_ms: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    is_fallback: bool = False,
    prompt_sha256: str | None = None,
) -> dict:
    """Keyword arguments for a failure DecisionRecord, with BR2.3's presence rules applied.

    Facts the failure stage cannot have observed are dropped to null, never kept and never
    turned into zero: before selection nothing is chosen, scored or timed; before
    generation returned there are no tokens and no cost. A stage that disagrees with the
    kind is a defect in the caller and raises.
    """
    kind = ErrorKind(error_kind)
    if STAGE_BY_KIND[kind] != stage:
        raise ValueError(f"{kind.value} happens at stage {STAGE_BY_KIND[kind]!r}, not {stage!r}")
    if stage == "received":
        chosen_alias = chosen_model_id = None
        scores = {}
        embed_latency_ms = decision_latency_ms = routing_overhead_ms = None
        is_fallback = False
    if stage in ("received", "selected"):
        input_tokens = output_tokens = cost_usd = None
    if kind in REJECTION_KINDS:
        prompt_sha256 = None  # the prompt did not validate, so there is nothing to digest
    return _record_fields(
        request_id=request_id, arm=arm, timestamp=timestamp, cold_start=cold_start,
        chosen_alias=chosen_alias, chosen_model_id=chosen_model_id, scores=scores or {},
        embed_latency_ms=embed_latency_ms, decision_latency_ms=decision_latency_ms,
        routing_overhead_ms=routing_overhead_ms, input_tokens=input_tokens,
        output_tokens=output_tokens, cost_usd=cost_usd, is_fallback=is_fallback,
        artifact_hash=artifact_hash, prompt_sha256=prompt_sha256,
        error_kind=kind.value, error_detail=detail[:DETAIL_MAX_CHARS],
    )


def _record_fields(*, scores: dict[str, float], **values) -> dict:
    """The one place both builders assemble a record's keyword arguments."""
    return dict(values, scores=dict(scores), schema_version=DECISION_RECORD_SCHEMA_VERSION)


def _service_code(exc: BaseException) -> str | None:
    """The service error code a botocore ClientError carries, if any. Never raises."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    error = response.get("Error")
    code = error.get("Code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code else None


def format_detail(exc: BaseException, *, redact: tuple[str, ...] = ()) -> str:
    """`"<Type>: <msg>"`, or `"<Type>[<Code>]: <msg>"`, at most DETAIL_MAX_CHARS (BR2.2).

    The type name always leads, so a botocore ReadTimeoutError stays countable (NFR7.4).
    `redact` names strings to remove before truncation: the agent passes the prompt here,
    because a fake or a library can echo it in an exception message. Each target is removed
    verbatim and in its escaped echo forms - repr()-escaped (what str(KeyError(prompt))
    produces for a multi-line or quote-carrying prompt) and JSON-escaped, with and without
    ensure_ascii. Stated limit: targets shorter than eight characters are left alone, and any
    other transformed or partial echo (a truncated, re-cased or differently encoded copy)
    still gets through.
    """
    code = _service_code(exc)
    head = type(exc).__name__ + (f"[{code}]" if code else "")
    return bound_detail(f"{head}: {exc}", redact=redact)


def bound_detail(text: str, *, redact: tuple[str, ...] = ()) -> str:
    """`text` with each redaction target removed, then cut to DETAIL_MAX_CHARS (BR2.2).

    Redaction runs first, so truncation can never leave a partial copy that no longer
    matches. Used directly for a reason that is already text (a router's fallback reason).
    """
    for target in redact:
        if target and len(target) >= _REDACT_MIN_CHARS:
            for form in _echo_forms(target):
                text = text.replace(form, _REDACTED)
    return text[:DETAIL_MAX_CHARS]


def _echo_forms(target: str) -> list[str]:
    """`target` and its repr- and JSON-escaped bodies, longest first (review R-02).

    Longest first, so a shorter form that is a substring of a longer one cannot be replaced
    first and leave the rest of the longer form unredacted.
    """
    forms = {
        target,
        repr(target)[1:-1],
        json.dumps(target)[1:-1],
        json.dumps(target, ensure_ascii=False)[1:-1],
    }
    return sorted(forms, key=len, reverse=True)


def rejection_detail(
    field_name: str,
    problem: str | None = None,
    *,
    size: int | None = None,
    bound: int | None = None,
    unit: str = "bytes",
    note: str | None = None,
) -> str:
    """A rejection detail built from a field name, a check and lengths only (FR4.6.2).

    It never receives the rejected value, so it cannot echo it.
    """
    if size is not None and bound is not None:
        text = f"{field_name} is {size} {unit}; bound is {bound}"
    else:
        text = f"{field_name}: {problem}"
    if note:
        text = f"{text} ({note})"
    return text[:DETAIL_MAX_CHARS]


class TelemetrySink(abc.ABC):
    @abc.abstractmethod
    def emit(self, record: DecisionRecord) -> None:
        raise NotImplementedError


class InMemorySink(TelemetrySink):
    """Test/dev sink. In prod, replace with an OTEL span exporter."""

    def __init__(self):
        self.records: list[DecisionRecord] = []

    def emit(self, record: DecisionRecord) -> None:
        self.records.append(record)


class OTelSink(TelemetrySink):
    """Decision telemetry for AgentCore Observability (design D6).

    Emits each decision as a single structured JSON log line on a dedicated logger. The
    AgentCore runtime ships stdout/stderr to CloudWatch Logs, from which the records are
    exported to S3/Athena for the statistical eval — so the log line IS the transport, no
    extra exporter needed. If an OTEL tracer is supplied, we additionally stamp the current
    span's attributes so traces carry the routing decision too.
    """

    def __init__(self, tracer=None, logger_name: str = "delphi.decision"):
        self._tracer = tracer
        self._log = logging.getLogger(logger_name)
        if not self._log.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._log.addHandler(handler)
            self._log.setLevel(logging.INFO)
            self._log.propagate = False

    def emit(self, record: DecisionRecord) -> None:
        self._log.info(record.to_json())
        if self._tracer is not None:  # pragma: no cover - only when a tracer is injected
            span = getattr(self._tracer, "get_current_span", lambda: None)()
            if span is not None:
                attributes = {
                    "delphi.arm": record.arm,
                    "delphi.chosen_model": record.chosen_alias,
                    "delphi.cost_usd": record.cost_usd,
                    "delphi.is_fallback": record.is_fallback,
                }
                # Failure records carry nulls, which span attributes do not accept.
                for key, value in attributes.items():
                    if value is not None:
                        span.set_attribute(key, value)
