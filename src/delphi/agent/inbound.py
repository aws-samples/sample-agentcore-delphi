"""Request-boundary validation (U2 LC-U2-3; ENT-202; BR3.1-BR3.5; FR4.6.1-FR4.6.3).

The invoke payload is untrusted input. It is validated once, in a fixed order, BEFORE any
router or Converse call, so a rejected request costs nothing (BR3.5):

  1. the payload is a mapping                                          (BR3.1)
  2. request_id, when present, is a string of at most 256 characters   (BR3.4, A-2)
  3. the prompt is present, a string, non-blank and UTF-8-encodable    (BR3.2)
  4. the prompt is at most 102,400 UTF-8 BYTES, inclusive               (BR3.3, C1 R-22)

The first failure wins. This is robustness, cost control and telemetry integrity, not
prompt-injection defence: the request path has no tool use, retrieval or privileged action.

A rejection never echoes the rejected value (FR4.6.2). Its detail is built from field names,
check names and lengths only, and a rejected caller `request_id` is replaced by a generated
one, so neither the 100 KB prompt bound nor the record can be bypassed through request_id.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from delphi.agent.telemetry import ErrorKind, rejection_detail

#: The prompt bound, in UTF-8 bytes. Characters are not the measure.
REQUEST_PROMPT_MAX_BYTES = 102_400
#: The request_id bound, in characters (A-2). It narrows C1, which leaves request_id unbounded.
REQUEST_ID_MAX_CHARS = 256

_REJECTED_ID_NOTE = "the caller's request_id was rejected; a generated one is used"


@dataclass(frozen=True)
class InboundRequest:
    """The payload after validation (ENT-202). Keys other than these two are ignored."""

    request_id: str
    prompt: str
    #: The prompt encoded once, reused for the digest after the routing window closes.
    encoded: bytes = field(repr=False)

    @property
    def prompt_bytes(self) -> int:
        return len(self.encoded)


class _Rejection(Exception):
    """Internal: why validation failed. The agent turns it into a record, a line, and a
    RequestRejected; it never leaves the agent."""

    def __init__(self, error_kind: ErrorKind, detail: str, request_id: str):
        super().__init__(detail)
        self.error_kind = error_kind
        self.detail = detail
        self.request_id = request_id


class RequestRejected(Exception):
    """The defined error for a malformed or oversized request (FR4.6.3).

    Raised only after the failure record was emitted and the request_rejected line written.
    `error_kind` is request_invalid or request_too_large, the record's own value; it is the
    hook an entrypoint 4XX mapping would read (OQ-U1-1, U1's), with no change here.
    """

    def __init__(self, error_kind: str, detail: str):
        super().__init__(f"{error_kind}: {detail}")
        self.error_kind = str(error_kind)
        self.detail = detail


def _new_request_id() -> str:
    return uuid.uuid4().hex


def _request_id(payload: Mapping) -> str:
    value = payload.get("request_id")
    if value is None or value == "":
        return _new_request_id()
    if not isinstance(value, str):
        raise _Rejection(
            ErrorKind.REQUEST_INVALID,
            rejection_detail("request_id", f"expected string, got {type(value).__name__}",
                             note=_REJECTED_ID_NOTE),
            _new_request_id(),
        )
    if len(value) > REQUEST_ID_MAX_CHARS:
        raise _Rejection(
            ErrorKind.REQUEST_INVALID,
            rejection_detail("request_id", size=len(value), bound=REQUEST_ID_MAX_CHARS,
                             unit="characters", note=_REJECTED_ID_NOTE),
            _new_request_id(),
        )
    return value


def _encoded_prompt(payload: Mapping, request_id: str) -> tuple[str, bytes]:
    def invalid(problem: str) -> _Rejection:
        return _Rejection(ErrorKind.REQUEST_INVALID, rejection_detail("prompt", problem),
                          request_id)

    if "prompt" not in payload:
        raise invalid("missing")
    prompt = payload["prompt"]
    if not isinstance(prompt, str):
        raise invalid(f"expected string, got {type(prompt).__name__}")
    if not prompt.strip():
        raise invalid("empty after stripping whitespace")
    try:
        encoded = prompt.encode("utf-8")
    except UnicodeEncodeError:
        raise invalid("not encodable as UTF-8 (for example a lone surrogate)") from None
    if len(encoded) > REQUEST_PROMPT_MAX_BYTES:
        raise _Rejection(
            ErrorKind.REQUEST_TOO_LARGE,
            rejection_detail("prompt", size=len(encoded), bound=REQUEST_PROMPT_MAX_BYTES),
            request_id,
        )
    return prompt, encoded


def validate_inbound(payload: object) -> InboundRequest:
    """Return the validated request, or raise _Rejection on the first failing check."""
    if not isinstance(payload, Mapping):
        raise _Rejection(
            ErrorKind.REQUEST_INVALID,
            rejection_detail("payload", f"expected object, got {type(payload).__name__}"),
            _new_request_id(),
        )
    request_id = _request_id(payload)
    prompt, encoded = _encoded_prompt(payload, request_id)
    return InboundRequest(request_id=request_id, prompt=prompt, encoded=encoded)
