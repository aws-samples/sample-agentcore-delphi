"""The parent side of candidate-code execution (U4 LC-U4-6, SM-1; BR4.5-BR4.12, NFR11.3-NFR11.6).

`CodeRunner` is the only path by which candidate code executes. It spawns the child harness
(eval/cell_harness.py) BY PATH as

    sys.executable -I -S <cell_harness.py>   env={}  cwd=<fresh tempfile.mkdtemp()>
    stdin/stdout pipes, stderr to a file in that directory, start_new_session=True

and exchanges one JSON request line and one JSON response line per cell (TS-U4-2, TS-U4-5).

* Isolation: nothing of the parent's environment is inherited, so no AWS_* credential
  variable reaches the child; the child cannot import boto3 or delphi (`-S`); its cwd is
  removed when the worker ends. The worker's first line is a handshake whose SHAPE is checked
  here; a missing or malformed handshake is runner_failed (TS-U4-6).
* Time: every cell has a wall-clock deadline, `timeout_s` (default 5.0, finite and > 0), read
  through a selector on the child's stdout. On expiry the worker's whole process group is
  killed, so a grandchild the candidate started dies with it (D-CG-U4-2); the worker is
  reaped within _REAP_TIMEOUT_S and its directory removed, and the cell scores
  candidate_failed timed_out (BR4.8). The next cell gets a new worker.
* Bounded I/O: one response line of at most MAX_RESPONSE_BYTES (NR-3). stderr goes to a file,
  so the parent never blocks on it. Every request carries a sequence id `rid` that the child
  echoes; before each request a zero-timeout poll of stdout must find nothing pending. A
  stale line, a second line, a wrong rid, EOF, non-JSON or a bad shape is runner_failed
  (D-CG-U4-3, SD-U4-14).
* runner_failed is OUR failure, never a 0.0 (D-U4-6): one ERROR scoring_harness_error line on
  delphi.eval, with the tail of the child's stderr, then ScoringHarnessError("runner_failed").
* Lifetime follows provenance and the caller cannot choose (BR4.11): `synthetic` keeps one
  worker per runner (per_run); `model_output` gets a fresh worker per cell (per_cell), so a
  hostile candidate cannot affect a later candidate's score.
* The first model_output cell a runner executes writes one WARNING unsandboxed_exec_opted_in
  line (BR4.12). The state is this instance's, never module-level.

Stated limits. The child is NOT a sandbox (eval/cell_harness.py). The stderr file is unbounded
within the deadline, and the child has no CPU, memory or file-size limit (OQ-U4-1). POSIX only
(P-8): selectors on pipes and process-group kill. The constants below are configuration
bounds with stated purposes, not measurements.
"""
from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import pathlib
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from delphi import failure_log
from delphi.eval.scoring import (
    _RAISED_AT,
    CANDIDATE_FAILED,
    DEFAULT_TIMEOUT_S,
    EVAL_LOGGER,
    FULL,
    MODEL_OUTPUT,
    PASSED,
    PROVENANCES,
    SYNTHETIC,
    ZERO,
    ExecOptIn,
    ScoringHarnessError,
    ScoringOutcome,
    UnsandboxedExecutionRefused,
)

#: The child harness, run by path and imported by nothing (D-CG-U4-1).
HARNESS_PATH = pathlib.Path(__file__).resolve().with_name("cell_harness.py")
#: Upper bound on one response line, newline included (NR-3).
MAX_RESPONSE_BYTES = 1 << 20
#: How long to wait for a worker to be reaped after SIGKILL; expiry is runner_failed (ND-2).
_REAP_TIMEOUT_S = 2.0
#: How long a fresh worker may take to write its handshake; expiry is runner_failed.
_HANDSHAKE_TIMEOUT_S = 10.0
#: How much of the child's stderr goes into a runner_failed detail.
_STDERR_TAIL_CHARS = 512
#: The non-raising candidate-failure reasons the child may report (ENT-407).
_CHILD_REASONS = frozenset({"entry_point_undefined", "entry_point_not_callable",
                            "wrong_answer"})
_HANDSHAKE_SHAPE = {"isolated": int, "no_site": int, "env_keys": list, "cwd": str, "pid": int,
                    "importable": dict}

PER_RUN = "per_run"
PER_CELL = "per_cell"


def _default_spawner(argv, **kwargs) -> subprocess.Popen:
    """The real spawn. A seam, so tests can record what was asked for (LC-U4-12)."""
    return subprocess.Popen(argv, **kwargs)  # noqa: S603 - fixed argv: this interpreter + harness  # nosemgrep


def _check_timeout(timeout_s) -> None:
    if (not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool)
            or not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError(f"timeout_s must be a finite number > 0, got {timeout_s!r}")


def _shape_problems(handshake: dict) -> list[str]:
    def ok(value, kind) -> bool:
        return isinstance(value, kind) and not (kind is int and isinstance(value, bool))

    return sorted(k for k, kind in _HANDSHAKE_SHAPE.items() if not ok(handshake.get(k), kind))


class _Worker:
    """One live child: its process, temp dir, stderr file and a selector on its stdout."""

    def __init__(self, proc, tmp: str, stderr_path: str, stderr_fh):
        self.proc = proc
        self.tmp = tmp
        self.stderr_path = stderr_path
        self.stderr_fh = stderr_fh
        self.fd = proc.stdout.fileno()
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.fd, selectors.EVENT_READ)


class CodeRunner:
    """Executes candidate cells in a child process. Not a sandbox (see the module docstring)."""

    def __init__(self, provenance: str, opt_in: ExecOptIn, *,
                 timeout_s: float = DEFAULT_TIMEOUT_S, spawner=None, harness_path=None):
        if provenance not in PROVENANCES:
            raise ValueError(f"provenance must be one of {PROVENANCES}, got {provenance!r}")
        if not isinstance(opt_in, ExecOptIn):
            raise TypeError("opt_in must be an ExecOptIn from scoring.resolve_opt_in")
        _check_timeout(timeout_s)
        if provenance == MODEL_OUTPUT and not opt_in.opted_in:
            raise UnsandboxedExecutionRefused(None)
        self.provenance = provenance
        self.opt_in = opt_in
        self.timeout_s = float(timeout_s)
        self.recycle = PER_RUN if provenance == SYNTHETIC else PER_CELL
        # Looked up here, not at import, so a test can route spawns through a recorder.
        self._spawner = spawner if spawner is not None else _default_spawner
        self._harness = str(harness_path or HARNESS_PATH)
        self._worker: _Worker | None = None
        self._rid = 0
        self._announced = False
        self.closed = False
        self.last_handshake: dict | None = None

    @property
    def state(self) -> str:
        if self.closed:
            return "closed"
        return "ready" if self._worker is not None else "not_started"

    def run_cell(self, code: str, entry_point: str, battery_repr: str,
                 query_id: str) -> ScoringOutcome:
        """One exchange (ENT-407): passed, candidate_failed (timed_out included), or raise
        ScoringHarnessError("runner_failed")."""
        if self.closed:
            raise ValueError("this CodeRunner is closed")
        self._announce(query_id)
        if self._worker is None:
            self._start(query_id)
        if self._worker.sel.select(0):
            raise self._runner_failed(query_id, "stale bytes on stdout before the request")
        self._rid += 1
        rid = self._rid
        self._send(query_id, json.dumps({"rid": rid, "code": code, "entry_point": entry_point,
                                         "tests": battery_repr}) + "\n")
        line = self._read_line(query_id, self.timeout_s)
        if line is None:
            self._stop(query_id)
            return ScoringOutcome(CANDIDATE_FAILED, ZERO, "timed_out")
        outcome = self._outcome(query_id, line, rid)
        if self.recycle == PER_CELL:
            self._stop(query_id)
        return outcome

    def close(self) -> None:
        """Kill and reap any live worker and remove its directory. Idempotent."""
        self.closed = True
        self._stop(None)

    def _announce(self, query_id: str) -> None:
        if self.provenance == MODEL_OUTPUT and not self._announced:
            self._announced = True
            failure_log.write(failure_log.failure_logger(EVAL_LOGGER), logging.WARNING,
                              "unsandboxed_exec_opted_in", query_id=query_id,
                              reason=self.opt_in.source,
                              detail="executing model output without a sandbox")

    def _start(self, query_id: str) -> None:
        tmp = tempfile.mkdtemp(prefix="delphi-cell-")
        stderr_path = os.path.join(tmp, "stderr.txt")
        stderr_fh = open(stderr_path, "wb")  # closed by _cleanup
        proc = self._spawner(
            [sys.executable, "-I", "-S", self._harness],
            env={}, cwd=tmp, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr_fh, close_fds=True, start_new_session=True,
        )
        self._worker = _Worker(proc, tmp, stderr_path, stderr_fh)
        line = self._read_line(query_id, max(self.timeout_s, _HANDSHAKE_TIMEOUT_S))
        if line is None:
            raise self._runner_failed(query_id, "no handshake from the worker in time")
        handshake = self._decode(query_id, line, "handshake")
        problems = _shape_problems(handshake)
        if problems:
            raise self._runner_failed(query_id, f"malformed handshake: {problems}")
        self.last_handshake = handshake

    def _send(self, query_id: str, request: str) -> None:
        stdin = self._worker.proc.stdin
        try:
            stdin.write(request.encode())
            stdin.flush()
        except OSError as exc:  # BrokenPipeError included: the worker died
            raise self._runner_failed(query_id, f"cannot write to the worker: {exc!r}") from exc

    def _read_line(self, query_id: str, timeout_s: float) -> bytes | None:
        """One line from the worker, or None when the deadline passes (SD-U4-14)."""
        worker = self._worker
        deadline = time.perf_counter() + timeout_s
        buf = bytearray()
        while b"\n" not in buf:
            left = deadline - time.perf_counter()
            if left <= 0:
                return None
            if not worker.sel.select(left):
                continue
            chunk = os.read(worker.fd, 65536)
            if not chunk:
                raise self._runner_failed(query_id, "the worker closed stdout before answering")
            buf += chunk
            if len(buf) > MAX_RESPONSE_BYTES:
                raise self._runner_failed(query_id, f"line over {MAX_RESPONSE_BYTES} bytes")
        line, _, rest = bytes(buf).partition(b"\n")
        if rest:
            raise self._runner_failed(query_id, "bytes after the line (one line per cell)")
        return line

    def _decode(self, query_id: str, line: bytes, what: str) -> dict:
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise self._runner_failed(query_id, f"{what} is not JSON") from exc
        if not isinstance(value, dict):
            raise self._runner_failed(query_id, f"{what} is not a JSON object")
        return value

    def _outcome(self, query_id: str, line: bytes, rid: int) -> ScoringOutcome:
        resp = self._decode(query_id, line, "response")
        if resp.get("rid") != rid:
            raise self._runner_failed(query_id, f"response rid {resp.get('rid')!r} is not "
                                                f"request {rid}")
        keys, outcome = set(resp), resp.get("outcome")
        if outcome == PASSED and keys == {"rid", "outcome"}:
            return ScoringOutcome(PASSED, FULL)
        if outcome == CANDIDATE_FAILED:
            if keys == {"rid", "outcome", "reason"} and resp["reason"] in _CHILD_REASONS:
                return ScoringOutcome(CANDIDATE_FAILED, ZERO, resp["reason"])
            if keys == {"rid", "outcome", "raised_at"} and resp["raised_at"] in _RAISED_AT:
                return ScoringOutcome(CANDIDATE_FAILED, ZERO, _RAISED_AT[resp["raised_at"]])
        raise self._runner_failed(query_id, f"garbled response {line[:120]!r}")

    def _runner_failed(self, query_id: str | None, why: str) -> ScoringHarnessError:
        """Stop the worker and write the one ERROR line; return the error for the caller to
        raise (BR4.9). The detail carries the tail of the worker's stderr."""
        tail = self._stderr_tail()
        self._stop(query_id)
        detail = f"query {query_id}: runner_failed: {why}"
        if tail:
            detail += f"; worker stderr tail: {tail}"
        failure_log.write(failure_log.failure_logger(EVAL_LOGGER), logging.ERROR,
                          "scoring_harness_error", query_id=query_id, reason="runner_failed",
                          detail=detail[:1024])
        return ScoringHarnessError("runner_failed", query_id)

    def _stderr_tail(self) -> str:
        if self._worker is None:
            return ""
        self._worker.stderr_fh.flush()
        data = pathlib.Path(self._worker.stderr_path).read_bytes()
        return data.decode("utf-8", "replace")[-_STDERR_TAIL_CHARS:]

    def _stop(self, query_id: str | None) -> None:
        """Kill the worker's process group, reap it within the bound, remove its directory."""
        worker, self._worker = self._worker, None
        if worker is None:
            return
        proc = worker.proc
        # The group may already be empty (worker and children exited), or hold only a
        # zombie leader, which some platforms report as a permission error: either way there
        # is nothing left to kill, which is not a failure. A live leader is killed below.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=_REAP_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            self._cleanup(worker)
            failure_log.write(failure_log.failure_logger(EVAL_LOGGER), logging.ERROR,
                              "scoring_harness_error", query_id=query_id,
                              reason="runner_failed",
                              detail=f"worker pid {proc.pid} not reaped after SIGKILL")
            raise ScoringHarnessError("runner_failed", str(query_id)) from exc
        self._cleanup(worker)

    @staticmethod
    def _cleanup(worker: _Worker) -> None:
        worker.sel.close()
        for stream in (worker.proc.stdin, worker.proc.stdout):
            if stream is not None:
                stream.close()
        worker.stderr_fh.close()
        shutil.rmtree(worker.tmp)


__all__ = ["CodeRunner", "HARNESS_PATH", "MAX_RESPONSE_BYTES", "PER_CELL", "PER_RUN"]
