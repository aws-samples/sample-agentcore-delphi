"""Generated-code execution is denied by default — U4 LC-U4-4, LC-U4-5 (ADR-007, NFR11).

Provenance defaults to `model_output`; only a council whose type IS SyntheticCouncil is
`synthetic`. Model output executes only after an exact opt-in (`allow_exec=True`, or
DELPHI_ALLOW_UNSANDBOXED_EXEC=1 when allow_exec is not given; an explicit False wins). Without
it the named UnsandboxedExecutionRefused is raised before any spawn, and a matrix build refuses
before its first paid answer (BR4.1-BR4.4, BR4.12). The runner is NOT a sandbox.

  T-EXE-U4-1   SyntheticCouncil gives synthetic; a subclass of it and a duck-typed council
               give model_output and are refused; a direct classify_pass_at_1 call that states
               no provenance is refused (BR4.1, AC3.2.1)
  T-EXE-U4-2   resolve_opt_in over (allow_exec in True/False/None) x (env unset, "1", "true",
               "0", ""): the expected opted_in and source; allow_exec=1 and allow_exec="yes"
               raise TypeError (BR4.2, ND-4)
  T-EXE-U4-3   the refusal is the named error, with zero spawns through a spawner fake and no
               marker left by the candidate; its message says "not a sandbox" and names
               allow_exec=True and DELPHI_ALLOW_UNSANDBOXED_EXEC=1; it is not a
               ScoringHarnessError (BR4.3)
  T-EXE-U4-4   a counting duck-typed council gets 0 answer calls before the build raises; with
               no CODE query the same council builds normally (BR4.4)
  T-EXE-U4-9   no module, class or function docstring under src/delphi/eval/ says
               "in-process"; scoring.py's module docstring says "not a sandbox" and names
               DELPHI_ALLOW_UNSANDBOXED_EXEC; docs/phase1_pipeline.md does not say
               "in-process `exec`" (BR2.4)
  T-EXE-U4-13  three opted-in model_output cells on one runner write exactly one WARNING
               unsandboxed_exec_opted_in line on delphi.eval, reason equal to the opt-in
               source; a second runner writes one more; the event's keys are exactly
               query_id, reason, detail (BR4.12)
  T-EXE-U4-14  (a) build_response_matrix takes keyword-only allow_exec=None and timeout_s=5.0;
               (b) a duck council with allow_exec=True builds and the line's reason is
               "param"; (c) allow_exec=False with the variable at "1" refuses with 0 answers;
               (d) with the variable at "1", a council that sets it to "0" inside answer
               changes nothing: the build completes, reason "env" (NFR11.2, functional-design
               R-05)
  T-EXE-U4-17  no spawn-time or run-time figure from the design stages ("55 ms", "47.8 ms",
               "26 s", spaced or not) appears in src/, scripts/, docs/ or README.md
               (FR5.2.1). Keep-fixed; negative control: seeded text is reported
  guard        the autouse socket guard is active (NFR4.3)

A module-scoped fixture kills and reaps every worker the recording spawner saw and fails if
any was still alive (D-CG-U4-9). New symbols are reached through the module object inside
each test (D-CG-U4-5).

Partition: stdlib and the package. The candidate code here is OURS. No SDK call, no account.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import logging
import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import botocore.config  # noqa: F401 - as above
import pytest
from u2_support import failure_lines  # noqa: F401 - a fixture
from u4_support import (
    CountingCouncil,
    EnvFlippingCouncil,
    SpawnerFake,
    SyntheticSubCouncil,
    code_dataset,
    qa_dataset,
    reap_leaked,
    synth_pool,
)

from delphi.data.schema import Query, TaskType
from delphi.data.synthetic_provider import SyntheticCouncil
from delphi.eval.scoring import SyntheticJudge

ROOT = pathlib.Path(__file__).resolve().parents[1]
EVAL = ROOT / "src" / "delphi" / "eval"
ENV = "DELPHI_ALLOW_UNSANDBOXED_EXEC"
SPAWNER = SpawnerFake()
SQUARE = "def f(x):\n    return x * x\n"
BANNED_FIGURES = re.compile(r"\b55 ?ms\b|\b47\.8 ?ms\b|\b26 ?s\b")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_exec_gate.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


@pytest.fixture(autouse=True)
def _record_spawns(monkeypatch):
    """Route the runner's default spawner through SPAWNER, so the leak check sees it."""
    monkeypatch.delenv(ENV, raising=False)
    if importlib.util.find_spec("delphi.eval.runner") is not None:
        monkeypatch.setattr(importlib.import_module("delphi.eval.runner"),
                            "_default_spawner", SPAWNER)


@pytest.fixture(autouse=True, scope="module")
def _no_live_worker():
    yield
    leaked = reap_leaked(SPAWNER)
    assert leaked == [], f"workers still alive after the module: {leaked}"


def _scoring():
    return importlib.import_module("delphi.eval.scoring")


def _bm():
    return importlib.import_module("delphi.eval.build_matrix")


def _code_query(qid="q-exe-1") -> Query:
    return Query(qid, "write f", TaskType.CODE, "craft", answer="f",
                 metadata={"entry_point": "f", "tests": [((2,), 4), ((3,), 9)]})


def _build(council, ds=None, **kwargs):
    return _bm().build_response_matrix(ds or code_dataset(1), synth_pool(), council,
                                       SyntheticJudge(), **kwargs)


# --------------------------------------------------------------------- T-EXE-U4-1
def test_t_exe_u4_1_only_the_synthetic_council_is_synthetic():
    bm, scoring = _bm(), _scoring()
    assert bm.answer_provenance(SyntheticCouncil()) == "synthetic"
    assert bm.answer_provenance(SyntheticSubCouncil()) == "model_output"
    assert bm.answer_provenance(CountingCouncil()) == "model_output"
    assert _build(SyntheticCouncil()).native_pairs == len(synth_pool().aliases)
    for council in (SyntheticSubCouncil(), CountingCouncil()):
        with pytest.raises(scoring.UnsandboxedExecutionRefused):
            _build(council)
    with pytest.raises(scoring.UnsandboxedExecutionRefused):
        scoring.classify_pass_at_1(SQUARE, _code_query())


# --------------------------------------------------------------------- T-EXE-U4-2
OPT_IN_TABLE = [
    (True, None, True, "param"), (True, "0", True, "param"), (True, "1", True, "param"),
    (False, None, False, "param"), (False, "1", False, "param"),
    (False, "true", False, "param"),
    (None, None, False, "none"), (None, "1", True, "env"), (None, "true", False, "none"),
    (None, "0", False, "none"), (None, "", False, "none"),
]


@pytest.mark.parametrize("allow_exec,env,opted_in,source", OPT_IN_TABLE)
def test_t_exe_u4_2_the_opt_in_is_exact(allow_exec, env, opted_in, source, monkeypatch):
    if env is not None:
        monkeypatch.setenv(ENV, env)
    got = _scoring().resolve_opt_in(allow_exec)
    assert (got.opted_in, got.source) == (opted_in, source)


@pytest.mark.parametrize("bad", [1, "yes"])
def test_t_exe_u4_2_a_non_bool_allow_exec_is_a_type_error(bad, monkeypatch):
    monkeypatch.setenv(ENV, "1")
    with pytest.raises(TypeError):
        _scoring().resolve_opt_in(bad)


# --------------------------------------------------------------------- T-EXE-U4-3
def test_t_exe_u4_3_refusal_is_named_and_spawns_nothing(tmp_path, monkeypatch):
    scoring = _scoring()
    fake = SpawnerFake()
    if importlib.util.find_spec("delphi.eval.runner") is not None:
        monkeypatch.setattr(importlib.import_module("delphi.eval.runner"),
                            "_default_spawner", fake)
    marker = tmp_path / "ran.txt"
    candidate = f"open({str(marker)!r}, 'w').write('ran')\n" + SQUARE
    with pytest.raises(scoring.UnsandboxedExecutionRefused) as info:
        scoring.classify_pass_at_1(candidate, _code_query())
    assert fake.calls == [] and not marker.exists()
    message = str(info.value)
    for phrase in ("not a sandbox", "allow_exec=True", "DELPHI_ALLOW_UNSANDBOXED_EXEC=1"):
        assert phrase in message, phrase
    assert not isinstance(info.value, scoring.ScoringHarnessError)
    assert info.value.query_id == "q-exe-1"


# --------------------------------------------------------------------- T-EXE-U4-4
def test_t_exe_u4_4_the_build_refuses_before_it_spends():
    scoring = _scoring()
    council = CountingCouncil()
    with pytest.raises(scoring.UnsandboxedExecutionRefused) as info:
        _build(council, code_dataset(2, with_open_qa=True))
    assert council.calls == 0, "no answer before the refusal"
    assert info.value.query_id is None
    qa = CountingCouncil()
    result = _build(qa, qa_dataset())
    assert qa.calls == len(synth_pool().aliases) and result.native_pairs == qa.calls


# --------------------------------------------------------------------- T-EXE-U4-9
def in_process_docstrings(sources: dict[str, str]) -> list[str]:
    hits = []
    for name, source in sources.items():
        tree = ast.parse(source)
        nodes = [tree] + [n for n in ast.walk(tree)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        for node in nodes:
            doc = ast.get_docstring(node) or ""
            if "in-process" in doc:
                hits.append(f"{name}:{getattr(node, 'name', '<module>')}")
    return hits


def test_t_exe_u4_9_statements_about_exec_say_what_the_runner_is():
    sources = {p.name: p.read_text() for p in sorted(EVAL.glob("*.py"))}
    assert in_process_docstrings(sources) == []
    seeded = {"seeded.py": '"""Runs in-process."""\n'}
    assert in_process_docstrings(seeded) == ["seeded.py:<module>"]
    doc = ast.get_docstring(ast.parse(sources["scoring.py"])) or ""
    assert "not a sandbox" in doc and ENV in doc
    assert "in-process `exec`" not in (ROOT / "docs" / "phase1_pipeline.md").read_text()


# --------------------------------------------------------------------- T-EXE-U4-13
def test_t_exe_u4_13_an_opt_in_announces_itself_once_per_runner(failure_lines):
    scoring = _scoring()
    runner_mod = importlib.import_module("delphi.eval.runner")
    from delphi import failure_log

    assert failure_log.EVENT_KEYS["unsandboxed_exec_opted_in"] == ("query_id", "reason",
                                                                  "detail")
    opt = scoring.resolve_opt_in(True)
    for expected in (1, 2):
        runner = runner_mod.CodeRunner("model_output", opt)
        try:
            for i in range(3):
                outcome = scoring.classify_pass_at_1(SQUARE, _code_query(f"q-{expected}-{i}"),
                                                     provenance="model_output", runner=runner)
                assert outcome.outcome == "passed"
        finally:
            runner.close()
        lines = [d for d in failure_lines.json("delphi.eval")
                 if d["event"] == "unsandboxed_exec_opted_in"]
        assert len(lines) == expected
    assert [d["reason"] for d in lines] == ["param", "param"]
    assert all(set(d) == {"event", "query_id", "reason", "detail"} for d in lines)
    assert [r.levelno for r in failure_lines.records("delphi.eval")] == [logging.WARNING] * 2


# --------------------------------------------------------------------- T-EXE-U4-14
def test_t_exe_u4_14_a_the_build_signature():
    params = inspect.signature(_bm().build_response_matrix).parameters
    for name, default in (("allow_exec", None), ("timeout_s", 5.0)):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default == default, name


def test_t_exe_u4_14_b_a_param_opt_in_builds(failure_lines):
    council = CountingCouncil()
    _build(council, allow_exec=True)
    assert council.calls == len(synth_pool().aliases)
    (line,) = [d for d in failure_lines.json("delphi.eval")
               if d["event"] == "unsandboxed_exec_opted_in"]
    assert line["reason"] == "param"


def test_t_exe_u4_14_c_an_explicit_false_beats_the_environment(monkeypatch):
    monkeypatch.setenv(ENV, "1")
    council = CountingCouncil()
    with pytest.raises(_scoring().UnsandboxedExecutionRefused):
        _build(council, allow_exec=False)
    assert council.calls == 0


def test_t_exe_u4_14_d_the_opt_in_is_resolved_once(monkeypatch, failure_lines):
    monkeypatch.setenv(ENV, "1")
    council = EnvFlippingCouncil()
    result = _build(council, code_dataset(2))
    assert council.calls == 2 * len(synth_pool().aliases)
    assert result.native_pairs == council.calls
    reasons = [d["reason"] for d in failure_lines.json("delphi.eval")
               if d["event"] == "unsandboxed_exec_opted_in"]
    assert reasons == ["env"]


# --------------------------------------------------------------------- T-EXE-U4-17
def banned_figure_lines(text: str) -> list[int]:
    return [n for n, line in enumerate(text.splitlines(), 1) if BANNED_FIGURES.search(line)]


def test_t_exe_u4_17_no_spawn_figure_is_published():
    paths = [ROOT / "README.md"]
    for d in ("src", "scripts", "docs"):
        paths += sorted(p for p in (ROOT / d).rglob("*")
                        if p.suffix in (".py", ".md", ".yaml", ".txt") and p.is_file()
                        and "__pycache__" not in p.parts)
    hits = [f"{p.relative_to(ROOT)}:{n}" for p in paths for n in banned_figure_lines(p.read_text())]
    assert hits == []
    seeded = "a fresh spawn costs 55ms per cell\nabout 26 s per run\nand 47.8 ms each\nfine"
    assert banned_figure_lines(seeded) == [1, 2, 3]


# --------------------------------------------------------------------- guard
def test_u4_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
