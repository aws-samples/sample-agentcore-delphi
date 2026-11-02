"""pass@1 separates our harness errors from the candidate's failures — U2 LC-U2-11 (ENT-208).

Before U2 one `except Exception: return ZERO` folded four outcomes into the score 0.0:
invalid candidate code, a raising candidate, wrong entry_point metadata, and a malformed
test battery of OURS - inside the number published as quality dominance. Now the battery is
validated BEFORE any candidate code runs, and a harness error raises; a candidate failure
is a measured 0.0 with a reason. U2 added no timeout, builtins restriction or opt-in: those
are U4's (BR8.5). After U4 (BRK-14) every call here states `provenance="synthetic"`, because
the code is ours, and the candidate runs in U4's child harness; the assertions are U2's.

  T-SCO-U2-1  for each of the five harness reasons (entry_point_missing,
              entry_point_not_string, tests_not_a_list, test_case_malformed, tests_empty):
              ScoringHarnessError carrying that reason, and one ERROR scoring_harness_error
              line on delphi.eval (BR8.1, FM-20)
  T-SCO-U2-2  for each of the six candidate reasons (exec_raised, entry_point_undefined,
              entry_point_not_callable, call_raised, wrong_answer, comparison_raised):
              ScoringOutcome(candidate_failed, reason) with score 0.0, and NO log line
              (BR8.2 as refined by SD-U2-14, ENT-208)
  T-SCO-U2-3  every case passing gives outcome passed and 100.0; pass_at_1 returns
              outcome.score as a float (LC-U2-11)
  T-SCO-U2-4  a malformed battery with a candidate that sets a sentinel on builtins:
              ScoringHarnessError is raised and the sentinel is absent - no candidate code ran
              (BR8.1, BR8.3)
  T-SCO-U2-5  RETIRED by U4 (BR4.14, BRK-14): U2's BR8.5 named U4 its successor, and
              T-EXE-U4-5 to -8 in tests/test_code_runner.py take its place
  T-SCO-U2-6  re-pointed by U4 (BR4.14): across the candidate-execution files (scoring.py,
              runner.py, cell_harness.py) exactly one broad except exists, in the child
              harness's per-cell function, and it carries "# noqa: BLE001 - candidate code may
              raise anything; scored 0.0 by definition" (BR8.3)
  T-SCO-U2-7  classify_pass_at_1's docstring states the entry_point_undefined limit and the
              FM-21 limit - reasons are not recorded by the reader-run pipelines (BR8.2,
              D-CG-U2-7, nfr-design review R-04)
  T-SCO-U2-8  the autouse socket guard is active (NFR4.2)

Partition: stdlib and the package only. The candidate code here is OURS, written in this
file; no model output is executed. No SDK call, no account.
"""
from __future__ import annotations

import ast
import builtins
import importlib
import json
import logging
import pathlib
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest
from u2_support import failure_lines  # noqa: F401 - a fixture

from delphi.data.schema import Query, TaskType

EVAL_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "delphi" / "eval"
#: The candidate-execution files after U4 (BR4.14): the gate, the parent runner, the child.
EXECUTION_FILES = ("scoring.py", "runner.py", "cell_harness.py")
#: The code here is ours (U4 BRK-14).
SYN = {"provenance": "synthetic"}
NOQA ="# noqa: BLE001 - candidate code may raise anything; scored 0.0 by definition"
GOOD_TESTS = [((2,), 4), ((3,), 9)]
SQUARE = "def f(x):\n    return x * x\n"

HARNESS = {
    "entry_point_missing": {"tests": GOOD_TESTS},
    "entry_point_not_string": {"entry_point": 3, "tests": GOOD_TESTS},
    "tests_not_a_list": {"entry_point": "f", "tests": "2 -> 4"},
    "test_case_malformed": {"entry_point": "f", "tests": [((2,), 4, "extra")]},
    "tests_empty": {"entry_point": "f", "tests": []},
}
CANDIDATE = {
    "exec_raised": "def f(x:\n",
    "entry_point_undefined": "def g(x):\n    return x * x\n",
    "entry_point_not_callable": "f = 4\n",
    "call_raised": "def f(x):\n    raise ValueError('no')\n",
    "wrong_answer": "def f(x):\n    return x + 1\n",
    # The class lives inside f: exec puts top-level names in its separate locals dict, which
    # f's own globals cannot see.
    "comparison_raised": (
        "def f(x):\n"
        "    class Opaque:\n"
        "        def __ne__(self, other):\n"
        "            raise TypeError('cannot compare')\n"
        "    return Opaque()\n"
    ),
}


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_scoring_split.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _scoring():
    return importlib.import_module("delphi.eval.scoring")


def _query(metadata: dict, qid: str = "q-code-1") -> Query:
    return Query(qid, "write f", TaskType.CODE, domain="craft", answer="f", metadata=metadata)


@pytest.mark.parametrize("reason", sorted(HARNESS))
def test_t_sco_u2_1_harness_errors_raise_and_log(reason, failure_lines):
    scoring = _scoring()
    with pytest.raises(scoring.ScoringHarnessError) as info:
        scoring.classify_pass_at_1(SQUARE, _query(HARNESS[reason], qid=f"q-{reason}"), **SYN)
    assert info.value.reason == reason
    (line,) = failure_lines.records("delphi.eval")
    data = json.loads(line.getMessage())
    assert line.levelno == logging.ERROR
    assert (data["event"], data["reason"], data["query_id"]) == (
        "scoring_harness_error", reason, f"q-{reason}")


@pytest.mark.parametrize("reason", sorted(CANDIDATE))
def test_t_sco_u2_2_candidate_failures_score_zero_with_a_reason(reason, failure_lines):
    scoring = _scoring()
    outcome = scoring.classify_pass_at_1(CANDIDATE[reason],
                                         _query({"entry_point": "f", "tests": GOOD_TESTS}), **SYN)
    assert (outcome.outcome, outcome.reason, outcome.score) == ("candidate_failed", reason, 0.0)
    assert failure_lines.records("delphi.eval") == []


def test_t_sco_u2_3_a_passing_candidate_scores_full():
    scoring = _scoring()
    query = _query({"entry_point": "f", "tests": GOOD_TESTS})
    outcome = scoring.classify_pass_at_1(SQUARE, query, **SYN)
    assert (outcome.outcome, outcome.reason, outcome.score) == ("passed", None, 100.0)
    score = scoring.pass_at_1(SQUARE, query, **SYN)
    assert score == 100.0 and isinstance(score, float)
    assert scoring.pass_at_1(CANDIDATE["wrong_answer"], query, **SYN) == 0.0


def test_t_sco_u2_4_no_candidate_code_runs_on_a_broken_battery():
    scoring = _scoring()
    sneaky = "import builtins\nbuiltins.U2_SCORING_SENTINEL = True\n" + SQUARE
    try:
        with pytest.raises(scoring.ScoringHarnessError):
            scoring.classify_pass_at_1(sneaky, _query(HARNESS["test_case_malformed"]), **SYN)
        assert not hasattr(builtins, "U2_SCORING_SENTINEL")
    finally:
        if hasattr(builtins, "U2_SCORING_SENTINEL"):
            del builtins.U2_SCORING_SENTINEL


def _broad_handlers(source: str) -> list[tuple[str, int]]:
    tree = ast.parse(source)
    out = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for h in [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]:
            broad = h.type is None or (isinstance(h.type, ast.Name)
                                       and h.type.id in ("Exception", "BaseException"))
            if broad:
                out.append((fn.name, h.lineno))
    return out


def test_t_sco_u2_6_one_broad_catch_with_its_reason():
    found = []
    for name in EXECUTION_FILES:
        source = (EVAL_DIR / name).read_text()
        found += [(name, fn, source.splitlines()[lineno - 1])
                  for fn, lineno in _broad_handlers(source)]
    assert [(name, fn) for name, fn, _ in found] == [("cell_harness.py", "run_cell")]
    assert NOQA in found[0][2]


def test_t_sco_u2_7_the_classifier_states_its_limits():
    doc = (_scoring().classify_pass_at_1.__doc__ or "").lower()
    assert "entry_point_undefined" in doc and "wrong entry_point metadata" in doc
    assert "not recorded" in doc and "pipeline" in doc


def test_t_sco_u2_8_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
