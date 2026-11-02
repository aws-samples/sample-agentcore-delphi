"""Failure classifier tests — U1 LC-9 (verify/classify.py).

LC-9 is the single mapping from a raised exception or a transport outcome to one failure
kind. Added because no component owned it, which left six kinds with no producer. It
matches on exception TYPE NAME and, for ClientError, on the service's error code, so it
imports no boto3 and stays pure.

  T-VX-1..8  one test per mapping row
  T-VX-9     totality: never returns None and never raises on an unknown type
  T-VX-10    ordering: a 403 with a JSON body is a status failure, not a decode failure
  T-VX-11    the module imports with boto3 unavailable
  T-VX-13    found by deployment: ParamValidationError -> client_defect, never retry advice
  T-VX-14    an undecodable body -> undecodable_payload, no longer folded into transport_error
"""
from __future__ import annotations

import json
import pathlib

import pytest

from delphi.verify import classify as classify_mod
from delphi.verify.classify import classify
from delphi.verify.failures import FailureKind


class FakeClientError(Exception):
    """Stands in for botocore's ClientError without importing botocore."""

    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code, "Message": f"fake {code}"}}


def _named(name: str) -> Exception:
    """Build an exception whose type NAME is what the classifier matches on."""
    return type(name, (Exception,), {})("boom")


@pytest.mark.parametrize(
    "exc,expected",
    [
        (_named("ProfileNotFound"), FailureKind.CREDENTIALS_UNAVAILABLE),
        (_named("NoCredentialsError"), FailureKind.CREDENTIALS_UNAVAILABLE),
        (_named("PartialCredentialsError"), FailureKind.CREDENTIALS_UNAVAILABLE),
        (_named("ReadTimeoutError"), FailureKind.TRANSPORT_ERROR),
        (_named("ConnectTimeoutError"), FailureKind.TRANSPORT_ERROR),
        (_named("EndpointConnectionError"), FailureKind.TRANSPORT_ERROR),
    ],
)
def test_exception_type_names_map_to_their_kind(exc, expected):
    """T-VX-1..6."""
    assert classify(exc=exc, outcome=None, elapsed_ms=1.0).kind is expected


@pytest.mark.parametrize(
    "code,expected",
    [
        ("AccessDeniedException", FailureKind.ACCESS_DENIED),
        ("UnrecognizedClientException", FailureKind.ACCESS_DENIED),
        ("ResourceNotFoundException", FailureKind.RUNTIME_NOT_FOUND),
        ("ValidationException", FailureKind.RUNTIME_NOT_FOUND),
        ("ThrottlingException", FailureKind.TRANSPORT_ERROR),
    ],
)
def test_client_error_codes_map_to_their_kind(code, expected):
    """T-VX-7. Matched on the service's own error code."""
    assert classify(exc=FakeClientError(code), outcome=None, elapsed_ms=1.0).kind is expected


def test_json_decode_error_is_a_decode_failure():
    """T-VX-8 / T-VX-14. A bare JSONDecodeError gets the same name as the client's own
    refusal, so one cause never reaches the reader under two kinds."""
    try:
        json.loads("{not json")
    except json.JSONDecodeError as exc:
        failure = classify(exc=exc, outcome=None, elapsed_ms=1.0)
    assert failure.kind is FailureKind.UNDECODABLE_PAYLOAD
    assert "decode" in failure.detail.lower() or "json" in failure.detail.lower()


def test_classify_is_total_for_an_unrecognised_exception():
    """T-VX-9. An unknown failure must still reach the reader as something specific."""
    failure = classify(exc=_named("SomeFutureBotocoreError"), outcome=None, elapsed_ms=1.0)
    assert failure is not None
    assert failure.kind is FailureKind.TRANSPORT_ERROR
    assert "SomeFutureBotocoreError" in failure.detail


def test_elapsed_is_carried_on_the_failure_path():
    """T-VX-9. A raised exception returns no outcome, so timing must come in as an argument."""
    failure = classify(exc=_named("ReadTimeoutError"), outcome=None, elapsed_ms=1234.5)
    assert failure.elapsed_ms == pytest.approx(1234.5)


def test_a_403_with_a_json_body_is_a_status_failure():
    """T-VX-10. The data-plane client decides this, in the order status -> empty -> decode,
    and the classifier maps its kind rather than re-deciding (R-48)."""
    from delphi.agent.runtime_client import RuntimeTransportError

    exc = RuntimeTransportError("non_success_status", 403, '{"message":"denied"}')
    failure = classify(exc=exc, outcome=None, elapsed_ms=5.0)
    assert failure.kind is FailureKind.NON_SUCCESS_STATUS
    assert "403" in failure.detail


def test_an_empty_body_is_an_empty_payload_failure():
    """T-VX-10."""
    from delphi.agent.runtime_client import RuntimeTransportError

    exc = RuntimeTransportError("empty_payload", 200, "")
    assert classify(exc=exc, outcome=None, elapsed_ms=5.0).kind is FailureKind.EMPTY_PAYLOAD


def test_an_undecodable_body_is_an_undecodable_payload_failure():
    """T-VX-14. R-29: the decode case has its own name. The client's refusal kind and the
    reader-facing kind are now the same string, so the mapping is identity."""
    from delphi.agent.runtime_client import RuntimeTransportError

    exc = RuntimeTransportError("undecodable_payload", 200, "{not json")
    failure = classify(exc=exc, outcome=None, elapsed_ms=5.0)
    assert failure.kind is FailureKind.UNDECODABLE_PAYLOAD
    assert failure.kind.value == exc.kind


def test_a_client_side_validation_error_is_a_client_defect():
    """T-VX-13. Found by deployment, 2026-10-18: the first live run failed with
    `ParamValidationError: Invalid length for parameter runtimeSessionId, value: 32, valid
    min length: 33` and was reported as transport_error with "Retry once ... may be a cold
    start". The request never left the machine, so no retry can ever fix it: the fault is in
    this command. Asserting on the next action, not only the kind, is what stops the advice
    regressing.
    """
    from delphi.verify.failures import FAILURES

    exc = _named("ParamValidationError")
    failure = classify(exc=exc, outcome=None, elapsed_ms=0.5)
    assert failure.kind is FailureKind.CLIENT_DEFECT
    advice = FAILURES[failure.kind].next_action.lower()
    assert "retry" not in advice or "will not help" in advice
    assert "cold start" not in advice


def test_a_successful_outcome_classifies_as_no_failure():
    """T-VX-9. Totality cuts both ways: success must not be invented as a failure."""
    from delphi.verify.transport import TransportOutcome

    outcome = TransportOutcome(
        status_code=200,
        payload={"decision_record": {}},
        session_id="s",
        elapsed_ms=5.0,
    )
    assert classify(exc=None, outcome=outcome, elapsed_ms=5.0) is None


def test_module_does_not_import_the_sdk():
    """T-VX-11. It matches on type names, so it must not need botocore."""
    head = pathlib.Path(classify_mod.__file__).read_text().split("def ")[0]
    assert "import boto3" not in head
    assert "import botocore" not in head
