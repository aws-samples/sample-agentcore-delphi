"""Response validation — LC-4, serving BR1.11 and BR1.12.

C5's exit 0 is "the invocation succeeded AND a decision record was returned" — both
clauses. So a 200 carrying no record is a failure, not a success with less in it.

That case is not hypothetical: both live arms return the `app.py:92-105` shape today,
which has no `decision_record`, so it is the single most likely failure a reader meets in
the window between this unit landing and their own arm being redeployed. Exiting 0 there
would make the walking-skeleton checkpoint pass against a deployment that lacks the change.

AC1.5.1 says "the telemetry record FOR THAT INVOCATION", which is unverifiable unless the
caller supplies an id and checks it back. An absent id is treated as a mismatch: absent is
not the same as matching.
"""
from __future__ import annotations

from delphi.verify.classify import Failure
from delphi.verify.failures import FailureKind


def validate(payload: dict, *, correlation_id: str, elapsed_ms: float = 0.0) -> Failure | None:
    """Return a Failure when the payload cannot be trusted as this invocation's, else None."""
    record = payload.get("decision_record")
    if not isinstance(record, dict) or not record:
        return Failure(
            FailureKind.RECORD_ABSENT,
            "the response carried no decision_record",
            elapsed_ms,
        )

    returned = record.get("request_id")
    if returned != correlation_id:
        return Failure(
            FailureKind.CORRELATION_MISMATCH,
            f"sent request_id {correlation_id!r}, record carried {returned!r}",
            elapsed_ms,
        )

    return None
