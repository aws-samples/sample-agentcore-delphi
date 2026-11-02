"""Failure catalogue — LC-6, serving BR1.10 and AC1.5.3.

AC1.5.3 asks the command to report "what failed AND the next action I can take". A string
written at each raise site cannot be checked exhaustively, so the text lives in one table
that a test can iterate.

The table is keyed by a SEPARATE enum. That split is the point: a test that iterates
`FAILURES` to check `FAILURES` cannot fail, whereas one that iterates `FailureKind` fails
the moment a kind has no row. Two declarations with equality enforced by a test is the fix
`team.md` § Code Style prescribes for this repository's characteristic weakness.

Kinds mirror ENT-005's closed `failure_kind` set from the unit's entities.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureKind(StrEnum):
    """ENT-005's closed set. Adding a member without a FAILURES row fails T-VF-1."""

    MISSING_ARN = "missing_arn"
    MALFORMED_ARN = "malformed_arn"
    #: Added at code generation (R-50). ENT-005's first draft had only the two ARN-shaped
    #: usage kinds, so a flag typo was reported as an unparseable ARN with a "supply the
    #: full ARN" next action - precisely the wrong-place misdirection BR1.10 exists to
    #: prevent. ENT-005 is U1's OWN entity, so widening it is this unit's to do; the
    #: addition is recorded in entities.md beside the enum.
    USAGE_ERROR = "usage_error"
    CREDENTIALS_UNAVAILABLE = "credentials_unavailable"
    ACCESS_DENIED = "access_denied"
    RUNTIME_NOT_FOUND = "runtime_not_found"
    TRANSPORT_ERROR = "transport_error"
    NON_SUCCESS_STATUS = "non_success_status"
    EMPTY_PAYLOAD = "empty_payload"
    #: R-29: the decode case has its own name, identical to RuntimeTransportError.kind, so
    #: the client's refusal and the reader-facing kind are one vocabulary, not two.
    UNDECODABLE_PAYLOAD = "undecodable_payload"
    RECORD_ABSENT = "record_absent"
    CORRELATION_MISMATCH = "correlation_mismatch"
    AGENT_ERROR = "agent_error"
    #: Found by deployment (2026-10-18). The SDK refused the request client-side, before it
    #: was sent, so the fault is in this command - not the network, not the runtime. It was
    #: first reported as transport_error with retry advice that could never have worked.
    CLIENT_DEFECT = "client_defect"


@dataclass(frozen=True)
class FailureText:
    """Two clauses, deliberately separate fields so neither can be silently omitted."""

    summary: str
    next_action: str


#: Usage errors exit 2 per C5; everything else exits 1.
_USAGE_KINDS = frozenset(
    {FailureKind.MISSING_ARN, FailureKind.MALFORMED_ARN, FailureKind.USAGE_ERROR}
)


FAILURES: dict[FailureKind, FailureText] = {
    FailureKind.MISSING_ARN: FailureText(
        summary="No runtime ARN was supplied.",
        next_action=(
            "Pass --arn <runtime-arn>, or export DELPHI_RUNTIME_ARN. There is deliberately "
            "no default: a built-in ARN would point at somebody else's account."
        ),
    ),
    FailureKind.MALFORMED_ARN: FailureText(
        summary="The runtime ARN could not be parsed.",
        next_action=(
            "Supply the full ARN from your stack's output, shaped like "
            "arn:aws:bedrock-agentcore:<region>:<account>:runtime/<name>."
        ),
    ),
    FailureKind.USAGE_ERROR: FailureText(
        summary="The command line could not be parsed.",
        next_action=(
            "Check the flag spelling and that every option has its value; run "
            "delphi-verify --help for the accepted set. The parser's own message is "
            "printed above."
        ),
    ),
    FailureKind.CREDENTIALS_UNAVAILABLE: FailureText(
        summary="No AWS credentials could be resolved.",
        next_action=(
            "Configure the standard credential chain, or name a profile with --profile. "
            "This command sets no profile of its own."
        ),
    ),
    FailureKind.ACCESS_DENIED: FailureText(
        summary="The runtime refused the call as unauthorised.",
        next_action=(
            "Check that the identity your credential chain resolves to holds "
            "bedrock-agentcore:InvokeAgentRuntime on this runtime ARN. Inbound auth is IAM "
            "SigV4, so this is a permissions problem rather than a network one."
        ),
    ),
    FailureKind.RUNTIME_NOT_FOUND: FailureText(
        summary="No runtime matched that ARN in that region.",
        next_action=(
            "Confirm the ARN and its region field against your deployed stack's output; the "
            "region is read from the ARN itself unless --region overrides it."
        ),
    ),
    FailureKind.TRANSPORT_ERROR: FailureText(
        summary="The invocation did not complete.",
        next_action=(
            "Retry once. A first call after a deploy can be a cold start, and the container "
            "may still be starting; if it keeps failing, check the runtime is READY."
        ),
    ),
    FailureKind.NON_SUCCESS_STATUS: FailureText(
        summary="The runtime answered with a non-2xx status.",
        next_action=(
            "Read the status and body excerpt printed above, then check the runtime's log "
            "group for the matching request."
        ),
    ),
    FailureKind.EMPTY_PAYLOAD: FailureText(
        summary="The runtime answered with an empty body.",
        next_action=(
            "Check the runtime's log group: an empty body usually means the container failed "
            "before it could serialise a reply."
        ),
    ),
    FailureKind.UNDECODABLE_PAYLOAD: FailureText(
        summary="The runtime answered with a body that is not valid JSON.",
        next_action=(
            "Read the body excerpt printed above, then check the runtime's log group: a "
            "non-JSON reply usually means something other than the agent answered."
        ),
    ),
    FailureKind.RECORD_ABSENT: FailureText(
        summary="The invocation succeeded but carried no decision record.",
        next_action=(
            "Redeploy the arm. This runtime predates the change that returns the record in "
            "the response, so there is nothing for this command to show you."
        ),
    ),
    FailureKind.CORRELATION_MISMATCH: FailureText(
        summary="The returned record belongs to a different invocation.",
        next_action=(
            "Retry. The record's request_id did not match the one just sent, which can mean "
            "a cached or misrouted reply rather than a fault in your deployment."
        ),
    ),
    FailureKind.AGENT_ERROR: FailureText(
        summary="The agent raised while handling the request.",
        next_action=(
            "Check the runtime's log group. Before raising, the agent writes one delphi.agent "
            "line naming the error_kind and emits a decision record on delphi.decision with "
            "the same error_kind and an error_detail; the request_id joins them to this call."
        ),
    ),
    FailureKind.CLIENT_DEFECT: FailureText(
        summary="The AWS SDK rejected the request before sending it.",
        next_action=(
            "This is a bug in delphi-verify, not in your deployment, and retrying will not "
            "help: report it with the message printed above. Nothing was sent or billed."
        ),
    ),
}


def exit_code_for(kind: FailureKind) -> int:
    """C5's codes: 2 for a usage error, 1 for everything else. 0 is never a failure."""
    return 2 if kind in _USAGE_KINDS else 1


def render_failure(kind: FailureKind, *, detail: str = "") -> str:
    """Both clauses, always. A single-clause message is a bug, not a style choice."""
    text = FAILURES[kind]
    lines = [f"{kind.value}: {text.summary}"]
    if detail:
        lines.append(f"  {detail}")
    lines.append(f"Next: {text.next_action}")
    return "\n".join(lines)
