"""Failure classifier — LC-9, the single exception-to-kind mapping.

Added because no component owned this, which left six kinds with no producer:
`credentials_unavailable`, `access_denied`, `runtime_not_found`, `agent_error` and the
timeout branch of `transport_error`. Three concrete consequences it closes:

1. `ProfileNotFound` and `NoCredentialsError` arise inside LC-2, before LC-3 exists at
   all, so no transport-layer classifier could ever see them.
2. The detection logic would otherwise sit inside `AgentCoreRuntimeInvoker`, which carries
   `# pragma: no cover` at `runtime_client.py:31` - so the coverage floor would credit none
   of it. This is a pure function in `verify/`, inside the measurement.
3. A raised exception carries no timing, so `elapsed_ms` comes in as an argument and the
   failure path reports a round trip like the success path does.

It matches on exception TYPE NAME and, for a ClientError, on the service's own error code,
so it imports no `boto3` and stays testable with hand-written fakes.

**Totality is the contract.** It never returns a kind it invented and never raises: an
unrecognised exception type maps to `transport_error` with the type name quoted verbatim,
so an unknown failure still reaches the reader as something specific rather than as a
generic message.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from delphi.verify.failures import FailureKind

#: Matched on type name so botocore need not be importable.
_BY_TYPE_NAME: dict[str, FailureKind] = {
    "ProfileNotFound": FailureKind.CREDENTIALS_UNAVAILABLE,
    "NoCredentialsError": FailureKind.CREDENTIALS_UNAVAILABLE,
    "PartialCredentialsError": FailureKind.CREDENTIALS_UNAVAILABLE,
    "NoRegionError": FailureKind.CREDENTIALS_UNAVAILABLE,
    "ReadTimeoutError": FailureKind.TRANSPORT_ERROR,
    "ConnectTimeoutError": FailureKind.TRANSPORT_ERROR,
    "ConnectionError": FailureKind.TRANSPORT_ERROR,
    "EndpointConnectionError": FailureKind.TRANSPORT_ERROR,
    "JSONDecodeError": FailureKind.UNDECODABLE_PAYLOAD,
    # Raised by botocore before the request is sent, so nothing was transmitted and no
    # retry can help. Found by deployment: a 32-character session id was refused here.
    "ParamValidationError": FailureKind.CLIENT_DEFECT,
}

#: RuntimeTransportError.kind, decided once by the data-plane client that refused the
#: response. Check order lives there: status -> empty -> decode, so a 403 carrying a JSON
#: error body is a STATUS failure, not a decode failure.
_BY_TRANSPORT_KIND: dict[str, FailureKind] = {
    "non_success_status": FailureKind.NON_SUCCESS_STATUS,
    "empty_payload": FailureKind.EMPTY_PAYLOAD,
    "undecodable_payload": FailureKind.UNDECODABLE_PAYLOAD,
}

#: Matched on the service's own error code, quoted verbatim in the detail either way.
_BY_ERROR_CODE: dict[str, FailureKind] = {
    "AccessDeniedException": FailureKind.ACCESS_DENIED,
    "UnrecognizedClientException": FailureKind.ACCESS_DENIED,
    "ExpiredTokenException": FailureKind.CREDENTIALS_UNAVAILABLE,
    "ResourceNotFoundException": FailureKind.RUNTIME_NOT_FOUND,
    "ValidationException": FailureKind.RUNTIME_NOT_FOUND,
    "ThrottlingException": FailureKind.TRANSPORT_ERROR,
    "ServiceQuotaExceededException": FailureKind.TRANSPORT_ERROR,
    "InternalServerException": FailureKind.AGENT_ERROR,
}


@dataclass(frozen=True)
class Failure:
    """One classified failure, with the evidence the renderer needs."""

    kind: FailureKind
    detail: str
    elapsed_ms: float


def _error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error")
        if isinstance(error, dict):
            code = error.get("Code")
            if isinstance(code, str) and code:
                return code
    return None


def _excerpt(body: bytes, limit: int = 240) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def classify(
    *,
    exc: BaseException | None,
    outcome: Any | None,
    elapsed_ms: float,
) -> Failure | None:
    """Map a raised exception or a transport outcome to exactly one failure, or None.

    Returning None means "no failure here" - totality cuts both ways, so success is never
    invented as a failure either.

    Check ORDER for an outcome is status -> empty -> decode. A 403 carrying a JSON error
    body is a status failure, NOT a decode failure, and getting that backwards would tell
    a reader to go looking in the wrong place.
    """
    if exc is not None:
        # Unwrap the transport's elapsed-carrying wrapper, keeping its timing.
        original = getattr(exc, "original", exc)
        elapsed_ms = getattr(exc, "elapsed_ms", elapsed_ms)

        # The data-plane client raises its own typed error for the three response shapes
        # it refuses, carrying the kind it already decided. Re-deciding here would mean
        # two implementations of the same judgement (R-48).
        kind_name = getattr(original, "kind", None)
        if isinstance(kind_name, str) and kind_name in _BY_TRANSPORT_KIND:
            return Failure(
                _BY_TRANSPORT_KIND[kind_name],
                str(original),
                elapsed_ms,
            )

        code = _error_code(original)
        if code is not None:
            kind = _BY_ERROR_CODE.get(code, FailureKind.TRANSPORT_ERROR)
            return Failure(kind, f"service error code {code}: {original}", elapsed_ms)

        name = type(original).__name__
        kind = _BY_TYPE_NAME.get(name)
        if kind is not None:
            detail = f"{name}: {original}"
            if name == "JSONDecodeError":
                detail = f"could not decode the response body as JSON ({original})"
            return Failure(kind, detail, elapsed_ms)

        # Unrecognised: still specific, never generic.
        return Failure(
            FailureKind.TRANSPORT_ERROR,
            f"unrecognised failure {name}: {original}",
            elapsed_ms,
        )

    if outcome is None:
        return None

    # Reaching here means the data-plane client already accepted the response: a 2xx
    # status, a non-empty body, and a body that decoded. Those three shapes arrive as
    # RuntimeTransportError and are handled above, so re-checking them here would be the
    # duplicate judgement R-48 removed. One defensive check is kept, because a non-2xx
    # outcome object reaching this point would mean the invoker's contract was broken
    # rather than that the response was bad.
    status = outcome.status_code
    if not 200 <= status < 300:
        return Failure(
            FailureKind.NON_SUCCESS_STATUS,
            f"statusCode {status} reached the classifier as a successful outcome, which "
            "means the data-plane client did not refuse it as it should have",
            outcome.elapsed_ms,
        )
    return None
