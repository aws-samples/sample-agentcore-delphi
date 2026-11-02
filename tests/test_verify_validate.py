"""Response validation tests — U1 LC-4 (verify/validate.py).

C5's exit 0 is "the invocation succeeded AND a decision record was returned" — both
clauses. A 200 with no record is the single most likely failure a reader meets between U1
landing and their own arm being redeployed (BR1.11, BR1.12).

  T-VV-1  a 200 with no decision_record -> RECORD_ABSENT, next action names redeploy
  T-VV-2  a mismatched request_id -> CORRELATION_MISMATCH
  T-VV-3  a matching record validates
  T-VV-4  a record whose request_id is absent is a correlation failure, not a pass
"""
from __future__ import annotations

from delphi.verify.failures import FAILURES, FailureKind
from delphi.verify.validate import validate

TODAYS_SHAPE = {
    "request_id": "r1",
    "arm": "router",
    "chosen_model": "nova-lite",
    "scores": {"nova-lite": 7.0},
    "decision_latency_ms": 1.5,
    "output": "hello",
    "usage": {"input_tokens": 3, "output_tokens": 2},
    "cost_usd": 0.0001,
    "is_fallback": False,
}


def test_a_two_hundred_with_no_record_is_a_failure():
    """T-VV-1. Both live arms return exactly this shape today."""
    failure = validate(TODAYS_SHAPE, correlation_id="r1")
    assert failure is not None
    assert failure.kind is FailureKind.RECORD_ABSENT
    assert "redeploy" in FAILURES[FailureKind.RECORD_ABSENT].next_action.lower()


def test_a_mismatched_request_id_is_a_failure():
    """T-VV-2. Without this, "for that invocation" is unverifiable."""
    payload = dict(TODAYS_SHAPE, decision_record={"request_id": "someone-else"})
    failure = validate(payload, correlation_id="r1")
    assert failure is not None
    assert failure.kind is FailureKind.CORRELATION_MISMATCH


def test_a_matching_record_validates():
    """T-VV-3."""
    payload = dict(TODAYS_SHAPE, decision_record={"request_id": "r1", "arm": "router"})
    assert validate(payload, correlation_id="r1") is None


def test_a_record_with_no_request_id_is_a_correlation_failure():
    """T-VV-4. Absent is not the same as matching."""
    payload = dict(TODAYS_SHAPE, decision_record={"arm": "router"})
    failure = validate(payload, correlation_id="r1")
    assert failure is not None
    assert failure.kind is FailureKind.CORRELATION_MISMATCH
