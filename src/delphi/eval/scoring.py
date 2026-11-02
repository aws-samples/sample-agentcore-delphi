"""Quality scorers — native metrics (ground truth) and the judge path (T-STAT-5).

Native scorers return a quality score in [0, 100] so it lines up with the design's
"quality points" scale used by the statistical tests. Task-native where ground truth
exists (exact-match / pass@1 / F1); the judge is for open-ended tasks and is required to
sit outside the candidate pool (enforced in config, checked in tests).

Code execution for pass@1 is denied by default (U4, ADR-007, NFR11). Every code answer has a
provenance: `synthetic` only when the matrix builder's council is exactly SyntheticCouncil
(code we generated), otherwise `model_output`, the fail-closed default. Model output executes
only after an explicit opt-in - `allow_exec=True`, or, when allow_exec is not given, the
environment variable DELPHI_ALLOW_UNSANDBOXED_EXEC set to exactly "1"; an explicit False wins
over the environment. Without it UnsandboxedExecutionRefused is raised before anything is
validated, spawned or spent.

When execution is allowed, synthetic code included, the candidate runs only in a child
process (eval/runner.py, eval/cell_harness.py): `-I -S`, an empty environment, a temporary
working directory, allowlisted builtins and a per-cell wall-clock timeout (default 5.0 s).
That runner is not a sandbox: restricted builtins can be escaped by object introspection, and
an escaped candidate can read the user's files - including ~/.aws, the credential chain for
the production account on the author's machine. The replacement it needs before Phase 4
scores real model output is a real sandbox (seccomp or a microVM).

pass@1 integrity holds against INCORRECT code, not ADVERSARIAL code: a candidate that escapes
the builtins could forge its own `passed`. Per-cell workers stop one candidate tampering with
another's score, and nothing more (NR-5).
"""
from __future__ import annotations

import ast
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass

from delphi import failure_log
from delphi.data.schema import Query, TaskType

FULL = 100.0
ZERO = 0.0

#: The scoring path's failure channel (shared install policy with delphi.agent, LC-U2-7).
EVAL_LOGGER = "delphi.eval"

PASSED = "passed"
CANDIDATE_FAILED = "candidate_failed"

#: Where scored code came from (ENT-403). Unstated is model_output: fail closed (BR4.1).
SYNTHETIC = "synthetic"
MODEL_OUTPUT = "model_output"
PROVENANCES = (SYNTHETIC, MODEL_OUTPUT)

#: The opt-in variable (BR4.2). Only the exact string "1" opts in.
OPT_IN_ENV = "DELPHI_ALLOW_UNSANDBOXED_EXEC"

#: Per-cell wall-clock bound, seconds (P-3). A configuration value, not a measurement.
DEFAULT_TIMEOUT_S = 5.0

#: Which step of the candidate's run raised -> its candidate-failure reason (ENT-208).
_RAISED_AT = {"exec": "exec_raised", "call": "call_raised", "compare": "comparison_raised"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def exact_match(prediction: str, answer: str) -> float:
    """MC / short-answer exact match. Extracts a leading choice letter if present."""
    pred = _norm(prediction)
    ans = _norm(answer)
    # pull a bare A-D letter if the model wrapped it ("The answer is C.")
    m = re.search(r"\b([a-d])\b", pred)
    if len(ans) == 1 and ans in "abcd" and m:
        return FULL if m.group(1) == ans else ZERO
    return FULL if pred == ans else ZERO


def numeric_match(prediction: str, answer: str) -> float:
    """Math: compare the first integer in the prediction to the answer."""
    m = re.search(r"-?\d+", prediction.replace(",", ""))
    if not m:
        return ZERO
    return FULL if m.group(0) == answer.strip() else ZERO


def token_f1(prediction: str, answer: str) -> float:
    """Open-QA F1 over word tokens, scaled to [0,100]."""
    pred = _norm(prediction).split()
    gold = _norm(answer).split()
    if not pred or not gold:
        return ZERO
    common = Counter(pred) & Counter(gold)
    overlap = sum(common.values())
    if overlap == 0:
        return ZERO
    precision = overlap / len(pred)
    recall = overlap / len(gold)
    return 100.0 * (2 * precision * recall) / (precision + recall)


class ScoringHarnessError(Exception):
    """OUR harness or metadata is broken - never the candidate's fault, never a 0.0 (BR8.1)."""

    def __init__(self, reason: str, query_id: str):
        super().__init__(f"{reason} (query {query_id})")
        self.reason = reason
        self.query_id = query_id


@dataclass(frozen=True)
class ScoringOutcome:
    """One pass@1 evaluation (ENT-208): passed (100.0) or candidate_failed (0.0, a reason).

    U4 adds one candidate reason, timed_out (BR4.8). A harness error is not an outcome: it
    raises ScoringHarnessError instead, and so does a dead or garbled runner (runner_failed).
    """

    outcome: str
    score: float
    reason: str | None = None


class UnsandboxedExecutionRefused(Exception):
    """Model output was not executed: no opt-in was given (ENT-405, BR4.3).

    A policy refusal - neither a ScoringHarnessError nor a score. `query_id` is None when the
    matrix build's preflight raises it, before any answer is requested (BR4.4).
    """

    def __init__(self, query_id: str | None):
        where = f"query {query_id}" if query_id is not None else "this build"
        super().__init__(
            f"refusing to execute model output for {where}: the code runner is not a sandbox "
            "(restricted builtins in a child process can be escaped, and an escaped candidate "
            "can read your files and credentials). The required replacement is a real "
            "sandbox (seccomp / microVM). To run it anyway, at your own risk, pass "
            "allow_exec=True or set DELPHI_ALLOW_UNSANDBOXED_EXEC=1."
        )
        self.query_id = query_id


@dataclass(frozen=True)
class ExecOptIn:
    """The resolved decision to execute model output (ENT-404). source: param | env | none."""

    opted_in: bool
    source: str


def resolve_opt_in(allow_exec: bool | None) -> ExecOptIn:
    """BR4.2, read at call time and never at import: True or False is the answer (source
    param), whatever the environment says; None defers to DELPHI_ALLOW_UNSANDBOXED_EXEC,
    which opts in only when it is exactly "1" (source env). A non-bool raises TypeError
    (ND-4), so `allow_exec=1` cannot fall through to the environment."""
    if allow_exec is not None and not isinstance(allow_exec, bool):
        raise TypeError(f"allow_exec must be True, False or None, got {allow_exec!r}")
    if allow_exec is not None:
        return ExecOptIn(opted_in=allow_exec, source="param")
    env = os.environ.get(OPT_IN_ENV)
    return ExecOptIn(opted_in=(env == "1"), source="env" if env == "1" else "none")


def _harness_problem(query: Query) -> str | None:
    """Why the query's own metadata cannot be scored, or None. Runs no candidate code."""
    entry = query.metadata.get("entry_point")
    tests = query.metadata.get("tests")
    if entry is None or entry == "":
        return "entry_point_missing"
    if not isinstance(entry, str):
        return "entry_point_not_string"
    if not isinstance(tests, list):
        return "tests_not_a_list"
    if not tests:
        return "tests_empty"
    for case in tests:
        if not (isinstance(case, (list, tuple)) and len(case) == 2
                and isinstance(case[0], (list, tuple))):
            return "test_case_malformed"
    return None


def _harness_error(query: Query, reason: str) -> ScoringHarnessError:
    """Write the one ERROR scoring_harness_error line; return the error to raise (BR8.1)."""
    failure_log.write(failure_log.failure_logger(EVAL_LOGGER), logging.ERROR,
                      "scoring_harness_error", query_id=query.id, reason=reason,
                      detail=f"query {query.id}: {reason}")
    return ScoringHarnessError(reason, query.id)


def _battery_repr(query: Query) -> str:
    """The battery as the literal that crosses to the child (BR4.10, TS-U4-5).

    repr() must be accepted by ast.literal_eval and round-trip to an equal value; otherwise
    the battery would be altered in transit, which is OUR defect: test_case_not_literal.
    Tuples stay tuples, which JSON would not keep.
    """
    tests = query.metadata["tests"]
    text = repr(tests)
    try:
        back = ast.literal_eval(text)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError) as exc:
        raise _harness_error(query, "test_case_not_literal") from exc
    if back != tests:
        raise _harness_error(query, "test_case_not_literal")
    return text


def classify_pass_at_1(prediction: str, query: Query, *, provenance: str = MODEL_OUTPUT,
                       allow_exec: bool | None = None, runner=None,
                       timeout_s: float = DEFAULT_TIMEOUT_S) -> ScoringOutcome:
    """Run the candidate function against the query's test battery, and say why it failed.

    WARNING: the code runs in a child process that is NOT a sandbox (module docstring).
    Model output runs only with an opt-in; synthetic code (ours) needs none. Steps:

    0. Gate (BR4.1-BR4.3). `provenance` defaults to model_output. A model_output cell that is
       not opted in raises UnsandboxedExecutionRefused before anything is validated, spawned
       or logged. With a `runner`, the runner's provenance and resolved opt-in are used (a
       build resolves the opt-in once); passing `allow_exec` as well is a ValueError.
    1. The battery is validated FIRST (BR8.1, BR4.10). A broken one is our defect: it writes
       one ERROR scoring_harness_error line on delphi.eval and raises ScoringHarnessError,
       and no candidate code runs.
    2. The candidate then runs in the child (eval/runner.py). Any failure there is a measured
       outcome, a 0.0 with a reason - exec_raised, entry_point_undefined,
       entry_point_not_callable, call_raised, wrong_answer, comparison_raised, or (U4)
       timed_out - returned on the ScoringOutcome and deliberately not logged. A child that
       dies or answers garbage raises ScoringHarnessError("runner_failed") (BR4.9). Without a
       `runner`, a one-shot runner is opened and closed by this call.

    Two stated limits:
    * entry_point_undefined cannot be told apart from wrong entry_point metadata without a
      reference solution, so a misnamed entry point in OUR metadata is scored as the
      candidate's failure.
    * The reason is returned here but not recorded by the reader-run pipelines: pass_at_1,
      score_native and build_matrix keep their float results, so a pipeline's output says
      how many cells scored 0.0, not why (D-CG-U2-7, FM-21).
    """
    if runner is not None:
        if allow_exec is not None:
            raise ValueError("pass allow_exec or a runner, not both: a runner carries the "
                             "opt-in resolved for its build")
        if provenance != runner.provenance:
            raise ValueError(f"provenance {provenance!r} does not match the runner's "
                             f"{runner.provenance!r}")
        opt = runner.opt_in
    else:
        if provenance not in PROVENANCES:
            raise ValueError(f"provenance must be one of {PROVENANCES}, got {provenance!r}")
        opt = resolve_opt_in(allow_exec)
    if provenance == MODEL_OUTPUT and not opt.opted_in:
        raise UnsandboxedExecutionRefused(query.id)
    problem = _harness_problem(query)
    if problem is not None:
        raise _harness_error(query, problem)
    battery = _battery_repr(query)
    entry = query.metadata["entry_point"]
    if runner is not None:
        return runner.run_cell(prediction, entry, battery, query.id)
    from delphi.eval.runner import CodeRunner  # here: runner.py imports this module

    one_shot = CodeRunner(provenance, opt, timeout_s=timeout_s)
    try:
        return one_shot.run_cell(prediction, entry, battery, query.id)
    finally:
        one_shot.close()


def pass_at_1(prediction: str, query: Query, *, provenance: str = MODEL_OUTPUT,
              allow_exec: bool | None = None, runner=None,
              timeout_s: float = DEFAULT_TIMEOUT_S) -> float:
    """pass@1 as a score in [0, 100]: classify_pass_at_1(...).score, same keywords.

    Raises ScoringHarnessError when the query's own battery is broken (BR8.1), and
    UnsandboxedExecutionRefused for model output without an opt-in (BR4.3).
    """
    return classify_pass_at_1(prediction, query, provenance=provenance, allow_exec=allow_exec,
                              runner=runner, timeout_s=timeout_s).score


def score_native(prediction: str, query: Query, *, provenance: str = MODEL_OUTPUT,
                 allow_exec: bool | None = None, runner=None,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> float:
    """Dispatch to the native scorer for the query's task type. Raises for OPEN_ENDED
    (which has no ground truth and must go through the judge). The execution keywords
    reach the CODE branch only."""
    if query.task_type == TaskType.KNOWLEDGE_MC:
        return exact_match(prediction, query.answer or "")
    if query.task_type == TaskType.MATH:
        return numeric_match(prediction, query.answer or "")
    if query.task_type == TaskType.OPEN_QA:
        return token_f1(prediction, query.answer or "")
    if query.task_type == TaskType.CODE:
        return pass_at_1(prediction, query, provenance=provenance, allow_exec=allow_exec,
                         runner=runner, timeout_s=timeout_s)
    raise ValueError(f"{query.task_type} has no native scorer; use the judge")


class Judge:
    """Judge-scoring interface (T-STAT-5). Real judge is an out-of-pool model via Converse;
    the synthetic judge scores prophecies by a deterministic rubric so the dry-run exercises
    the dual-scoring code path without a live model."""

    def score(self, prediction: str, query: Query) -> float:  # pragma: no cover - overridden
        raise NotImplementedError


class SyntheticJudge(Judge):
    """Zeus arbitrates: a 'good' prophecy (the reference form) scores high, a 'silent
    smoke' non-answer scores low. Deterministic; no model call."""

    def score(self, prediction: str, query: Query) -> float:
        p = prediction.lower()
        if "smoke is silent" in p or "no omen" in p:
            return 15.0
        # reward cryptic-but-directive prophecies (contain a hedge + an imperative)
        has_hedge = any(w in p for w in ("when", "seek", "beware", "lies", "path"))
        return 88.0 if has_hedge else 50.0
