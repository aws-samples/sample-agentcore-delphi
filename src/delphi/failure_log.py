"""The failure channel — application-authored evidence on failure paths (U2 LC-U2-7, ENT-206).

Before U2 a failed invocation left no application-authored trace at all. This module is the
one place `delphi.agent` (the request path) and `delphi.eval` (scoring) write a failure
line, so both loggers follow one install policy.

* One line is one bare JSON object, through a `%(message)s` formatter: the same transport
  shape C2 fixes for `delphi.decision`. A failure line always carries an `event` key and never
  a top-level `schema_version`, so a query cannot mistake it for a DecisionRecord.
* The key set is fixed per event. The writer adds no key and refuses an unexpected one.
* `failure_logger(name)` ensures, on every call, exactly one handler carrying the marker
  attribute `_delphi_failure_channel`, level INFO and `propagate = False`. A handler without
  the marker (a test's, pytest's) neither suppresses the install nor is counted (NR-4).
* The marker handler resolves `sys.stderr` when it EMITS, not when it is created - the
  pattern of `logging.lastResort`. In the container stderr never changes and reaches
  CloudWatch; in a test, a handler installed earlier still writes to the current stderr.

Standard library only, and nothing from `delphi`: both `delphi.agent` and `delphi.eval`
import this module, so it must not create an edge between them
(`u2-decision-telemetry/nfr-design/logical-components.md` § 3 rule 1).
"""
from __future__ import annotations

import json
import logging
import sys

#: The attribute that identifies this module's handler on a logger.
MARKER = "_delphi_failure_channel"

_REQUEST_KEYS = ("request_id", "arm", "error_kind", "detail")

#: Each event's keys, in line order after `event`. ENT-206 for the agent; LC-U2-11 for eval.
EVENT_KEYS: dict[str, tuple[str, ...]] = {
    "request_rejected": _REQUEST_KEYS,
    "request_failed": _REQUEST_KEYS,
    "router_fallback": _REQUEST_KEYS,
    "agent_construction_failed": _REQUEST_KEYS,
    # The one stated exception to "a line carries no scores": the record survives a sink
    # failure as an opaque STRING, so the line itself can never parse as a record (NR-1).
    "record_emit_failed": _REQUEST_KEYS + ("record_json",),
    "scoring_harness_error": ("query_id", "reason", "detail"),
    # U4 BR4.12: an opted-in model-output run announces itself, once per CodeRunner.
    "unsandboxed_exec_opted_in": ("query_id", "reason", "detail"),
}


class _StderrAtEmit(logging.StreamHandler):
    """A StreamHandler whose stream is whatever sys.stderr is at the moment it emits."""

    _delphi_failure_channel = True

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logging.Formatter("%(message)s"))

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        # StreamHandler.__init__ assigns a stream; resolving at emit time ignores it.
        pass


def failure_logger(name: str) -> logging.Logger:
    """The named logger with exactly one marker handler, at INFO, not propagating."""
    log = logging.getLogger(name)
    ours = [h for h in log.handlers if getattr(h, MARKER, False)]
    for extra in ours[1:]:
        log.removeHandler(extra)
    if not ours:
        log.addHandler(_StderrAtEmit())
    log.setLevel(logging.INFO)
    log.propagate = False
    return log


def write(logger: logging.Logger, level: int, event: str, **fields) -> None:
    """Write one failure line: `event` first, then exactly that event's keys.

    A missing key is written as null; an unknown event or an unexpected key is a defect in
    the caller and raises ValueError, so the key set cannot drift.
    """
    keys = EVENT_KEYS.get(event)
    if keys is None:
        raise ValueError(f"unknown failure event {event!r}; known: {sorted(EVENT_KEYS)}")
    unexpected = set(fields) - set(keys)
    if unexpected:
        raise ValueError(f"{event} does not carry {sorted(unexpected)}; its keys are {keys}")
    line = {"event": event, **{k: fields.get(k) for k in keys}}
    logger.log(level, json.dumps(line, default=str))
