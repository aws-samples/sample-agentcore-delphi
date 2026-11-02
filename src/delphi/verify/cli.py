"""CLI entry — LC-7, the straight-line orchestration behind `delphi-verify`.

Walks functional-spec.md's Workflow 1: resolve (steps 1-5, before any network call), build
the client (6), invoke exactly once (7), inspect the transport result (8), require the
record (9), correlate it (10), render (11), exit 0 (12).

Exit codes are C5's and there are only three: 0 success, 1 invocation failure, 2 usage
error. Every exit-1 and exit-2 message carries TWO clauses - what failed and the next
action - because AC1.5.3 asks for both and a single-clause message is a bug.

stdout stays empty on failure, so a reader piping `--json` never gets half a record.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from delphi.verify.classify import classify
from delphi.verify.client import build_client
from delphi.verify.failures import FailureKind, exit_code_for, render_failure
from delphi.verify.render import render_human, render_json
from delphi.verify.resolve import UsageError, resolve
from delphi.verify.transport import invoke_once
from delphi.verify.validate import validate


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
    id_factory: Callable[[], str] | None = None,
) -> int:
    """Run one verification. Returns the exit code rather than calling sys.exit directly,
    so the whole path is testable without subprocesses."""
    import os

    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    make_client = client_factory or build_client

    # Steps 1-5: resolution. Every exit-2 case is decided here, before any network call.
    try:
        request = resolve(argv, env=env, id_factory=id_factory)
    except UsageError as exc:
        print(render_failure(exc.kind, detail=exc.detail), file=sys.stderr)
        return exit_code_for(exc.kind)

    # Step 6: the one client this command ever builds.
    try:
        client = make_client(region=request.region, profile=request.profile)
    except Exception as exc:  # noqa: BLE001 - classified, not swallowed
        failure = classify(exc=exc, outcome=None, elapsed_ms=0.0)
        print(render_failure(failure.kind, detail=failure.detail), file=sys.stderr)
        return exit_code_for(failure.kind)

    # Step 7: exactly one invocation.
    try:
        outcome = invoke_once(
            client,
            arn=request.runtime_arn,
            prompt=request.prompt,
            request_id=request.correlation_id,
            wrap_errors=True,
        )
    except Exception as exc:  # noqa: BLE001 - classified, not swallowed
        failure = classify(exc=exc, outcome=None, elapsed_ms=0.0)
        print(render_failure(failure.kind, detail=failure.detail), file=sys.stderr)
        return exit_code_for(failure.kind)

    # Step 8: status, empty body, decode - in that order.
    failure = classify(exc=None, outcome=outcome, elapsed_ms=outcome.elapsed_ms)
    if failure is None:
        # Steps 9-10: the record must be present AND belong to this invocation.
        failure = validate(
            outcome.payload,
            correlation_id=request.correlation_id,
            elapsed_ms=outcome.elapsed_ms,
        )
    if failure is not None:
        print(render_failure(failure.kind, detail=failure.detail), file=sys.stderr)
        return exit_code_for(failure.kind)

    # Steps 11-12.
    if request.json_mode:
        print(render_json(outcome.payload))
    else:
        print(render_human(outcome.payload, elapsed_ms=outcome.elapsed_ms))
    return 0


def _entry() -> None:  # pragma: no cover - console-script shim
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover - module execution shim
    _entry()

__all__ = ["main", "FailureKind"]
