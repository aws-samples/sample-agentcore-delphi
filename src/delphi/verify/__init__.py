"""Reader-facing verification command (ADR-002, contract C5; unit u1-verifiable-invocation).

`delphi-verify --arn <runtime-arn>` invokes ONE deployed AgentCore runtime, prints the
routing decision and the telemetry record for that invocation, and exits 0 / 1 / 2 per C5.
It replaces `scripts/drive_router.py`, whose module-level default pointed at the author's
own account (FR9.1, AC1.5.2).

Nine modules, one per logical component, so argument resolution is a separable step that
decides every exit-2 case before any network call:

    resolve   LC-1  argv + environ -> VerificationRequest
    client    LC-2  the botocore Config and the one lazy boto3 call
    transport LC-3  exactly one invoke_agent_runtime, monotonic-timed
    validate  LC-4  decision_record presence, request_id correlation
    render    LC-5  the rendered view and --json; label discipline
    failures  LC-6  FailureKind enum + the catalogue, as two declarations
    classify  LC-9  the single exception -> FailureKind mapping
    cli       LC-7  straight-line orchestration, exit codes, stdout/stderr split

`boto3` is never imported at module scope anywhere in this package, so every module here
imports with no AWS SDK present (AC1.5.4, and the repository's own convention).
"""
from __future__ import annotations
