"""The child-process runner — U4 LC-U4-6 (eval/runner.py), LC-U4-7 (eval/cell_harness.py).

The integration tier for execution: most tests here spawn REAL `sys.executable -I -S`
workers running the real harness file by path. Every allowed cell, synthetic included, runs
in a child with an empty environment, a fresh temporary cwd, allowlisted builtins and a
per-cell wall-clock timeout. Synthetic cells share one worker per runner; model-output cells
get a fresh worker each. The child is NOT a sandbox: restricted builtins can be escaped by
object introspection, and an escaped candidate can read the user's files (BR4.5-BR4.11).

  T-EXE-U4-5   an AST scan of src/delphi/ finds `exec(` exactly once, in eval/cell_harness.py,
               and `eval(` nowhere; a seeded `exec` in scoring.py is reported (BR4.5)
  T-EXE-U4-6   through a spawner fake: argv holds -I, -S and the harness path; the env passed
               is {} although the parent holds AWS_ACCESS_KEY_ID; cwd is under
               tempfile.gettempdir() and is gone after close(); start_new_session is true
               (D-CG-U4-2); the harness's AST imports lie within NR-4's set (BR4.6, NR-4)
  T-EXE-U4-6r  on the real spawner, with AWS_ACCESS_KEY_ID, AWS_SESSION_TOKEN and
               DELPHI_U4_SENTINEL set in the parent: the handshake reports isolated 1 and
               no_site 1; the leak check runs first, then env_keys must lie within
               PLATFORM_ADDED, each message naming the keys; realpath(cwd) is under
               realpath(gettempdir()) and gone after close(); boto3 and delphi are not
               importable; two model_output cells run in different pids, two synthetic cells
               in the same pid (NFR11.3, functional-design R-03, nfr-requirements R-04)
  T-EXE-U4-6c  seeded control: a test spawner that passes env {"DELPHI_U4_SENTINEL": "x"} to
               the real harness makes the leak assertion fail, naming DELPHI_U4_SENTINEL
               (SD-U4-11)
  T-EXE-U4-7   opted-in model_output cells: `import os` gives exec_raised; open(...) and
               eval("1") inside the function give call_raised; a name defined by an earlier
               cell is not visible to a later one; `raise SystemExit(0)` gives call_raised and
               the next cell on the same per_run worker still passes; every synthetic reference
               body passes (BR4.7, ND-3, NFR11.4)
  T-EXE-U4-8   `while True: pass` with timeout_s=0.5, for both provenances: timed_out, the call
               joined on a thread within timeout_s + 2.0 (a test-runtime margin, not a figure);
               the worker has exited; the next cell passes (BR4.8, NFR11.5)
  T-EXE-U4-10  a spawner fake whose child exits 1 with empty stdout: ScoringHarnessError
               runner_failed, one ERROR scoring_harness_error line, the child's stderr tail in
               the detail (BR4.9, P-4)
  T-EXE-U4-11  a tuple expected value passes against a tuple-returning candidate; a battery
               holding an object() raises test_case_not_literal with zero spawns (BR4.10)
  T-EXE-U4-12  through a recording spawner: 20 synthetic cells give 1 spawn; 20 opted-in
               model_output cells give 20 spawns (BR4.11, BR4.13)
  T-EXE-U4-15  after a synthetic build raises BudgetExceeded, its runner is closed and its
               worker has exited (NFR11.5, FM-U4-17)
  T-EXE-U4-16  test-owned misbehaving harnesses through the harness-path seam: (a) 1 MiB to
               stderr then an answer is answered; (b) 2 MiB with no newline is runner_failed;
               (c) a valid line plus a second line in the same write is runner_failed; (d) a
               second line arriving after a delay makes the NEXT request runner_failed, never
               read as its answer; (e) a harness that starts a grandchild and hangs: after
               timed_out the grandchild is gone (NFR11.6, SD-U4-14, nfr-design R-02, D-CG-U4-2,
               D-CG-U4-3)
  guard        the autouse socket guard is active (NFR4.3)

A module-scoped fixture kills and reaps every worker a recording spawner saw and fails if any
was still alive (D-CG-U4-9). No test here sleeps: waits are real timeouts or bounded joins.

Partition: stdlib and the package. The candidate code here is OURS. No SDK call, no account.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import logging
import os
import pathlib
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import sys
import tempfile
import threading

import boto3  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import botocore.config  # noqa: F401 - as above
import pytest
from u2_support import failure_lines  # noqa: F401 - a fixture
from u4_support import (
    EXIT_1_CHILD,
    MISBEHAVING,
    SpawnerFake,
    code_dataset,
    lock_released_within,
    reap_leaked,
    synth_pool,
)

from delphi.data.schema import Query, TaskType
from delphi.data.synthetic_provider import SyntheticCouncil
from delphi.eval.scoring import SyntheticJudge

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "delphi"
HARNESS = PKG / "eval" / "cell_harness.py"
ENV = "DELPHI_ALLOW_UNSANDBOXED_EXEC"
NR4_IMPORTS = {"ast", "contextlib", "io", "json", "sys", "os", "importlib.util", "importlib"}
#: Keys the platform adds to an empty environment (measured on macOS, Python 3.12.10). A new
#: platform key is a named edit here; a parent key is a leak (SD-U4-11).
PLATFORM_ADDED = frozenset({"__CF_USER_TEXT_ENCODING", "LC_CTYPE"})
PARENT_SENTINELS = frozenset({"AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN", "DELPHI_U4_SENTINEL"})
SPAWNER = SpawnerFake()
SQUARE = "def f(x):\n    return x * x\n"
HANG = "def f(x):\n    while True:\n        pass\n"


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_code_runner.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


@pytest.fixture(autouse=True)
def _record_spawns(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    if importlib.util.find_spec("delphi.eval.runner") is not None:
        monkeypatch.setattr(importlib.import_module("delphi.eval.runner"),
                            "_default_spawner", SPAWNER)


_FAKES: list[SpawnerFake] = []


def _fake(**kwargs) -> SpawnerFake:
    fake = SpawnerFake(**kwargs)
    _FAKES.append(fake)
    return fake


@pytest.fixture(autouse=True, scope="module")
def _no_live_worker():
    yield
    leaked = reap_leaked(SPAWNER, *_FAKES)
    assert leaked == [], f"workers still alive after the module: {leaked}"


def _runner_mod():
    return importlib.import_module("delphi.eval.runner")


def _scoring():
    return importlib.import_module("delphi.eval.scoring")


def _runner(provenance="synthetic", *, allow_exec=True, **kwargs):
    scoring = _scoring()
    return _runner_mod().CodeRunner(provenance, scoring.resolve_opt_in(allow_exec), **kwargs)


def _query(tests=None, entry="f", qid="q-run-1") -> Query:
    return Query(qid, "write f", TaskType.CODE, "craft", answer=entry,
                 metadata={"entry_point": entry, "tests": tests or [((2,), 4), ((3,), 9)]})


def _cell(runner, code, query=None):
    q = query or _query()
    return _scoring().classify_pass_at_1(code, q, provenance=runner.provenance, runner=runner)


# --------------------------------------------------------------------- T-EXE-U4-5
def exec_sites(sources: dict[str, str]) -> dict[str, list[tuple[str, int]]]:
    out: dict[str, list[tuple[str, int]]] = {"exec": [], "eval": []}
    for path, source in sorted(sources.items()):
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in out):
                out[node.func.id].append((path, node.lineno))
    return out


def test_t_exe_u4_5_one_execution_site_in_the_child_harness():
    sources = {str(p.relative_to(PKG)): p.read_text() for p in sorted(PKG.rglob("*.py"))}
    sites = exec_sites(sources)
    assert [path for path, _ in sites["exec"]] == ["eval/cell_harness.py"], sites
    assert sites["eval"] == []
    seeded = dict(sources)
    seeded["eval/scoring.py"] += "\n\ndef _seeded(p):\n    exec(p)\n"
    assert len(exec_sites(seeded)["exec"]) == 2


# --------------------------------------------------------------------- T-EXE-U4-6
def harness_imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_t_exe_u4_6_the_spawn_is_isolated_by_construction(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDPARENTONLY")
    fake = _fake()
    runner = _runner(spawner=fake)
    try:
        assert _cell(runner, SQUARE).outcome == "passed"
    finally:
        runner.close()
    (call,) = fake.calls
    assert call["argv"][1:3] == ["-I", "-S"] and call["argv"][0] == sys.executable
    assert pathlib.Path(call["argv"][3]).resolve() == HARNESS.resolve()
    assert call["env"] == {}
    cwd = pathlib.Path(call["cwd"])
    assert os.path.realpath(cwd).startswith(os.path.realpath(tempfile.gettempdir()))
    assert not cwd.exists(), "the worker's temp dir is removed at close()"
    assert call["start_new_session"] is True
    assert harness_imports(HARNESS.read_text()) <= NR4_IMPORTS
    assert "delphi" not in str(harness_imports(HARNESS.read_text()))
    assert harness_imports("import boto3\n") - NR4_IMPORTS == {"boto3"}


# --------------------------------------------------------------------- T-EXE-U4-6r / 6c
def assert_isolated_env(handshake: dict) -> None:
    keys = set(handshake["env_keys"])
    leaked = keys & PARENT_SENTINELS
    assert not leaked, f"parent environment leaked into the child: {sorted(leaked)}"
    extra = keys - PLATFORM_ADDED
    assert not extra, (f"child env has keys outside PLATFORM_ADDED {sorted(PLATFORM_ADDED)}: "
                       f"{sorted(extra)}. A platform addition is a named edit to "
                       f"PLATFORM_ADDED; a parent key is a leak")


def test_t_exe_u4_6r_isolation_holds_on_a_real_process(monkeypatch):
    for name in PARENT_SENTINELS:
        monkeypatch.setenv(name, f"parent-only-{name.lower()}")
    pids = {}
    for provenance in ("model_output", "synthetic"):
        runner = _runner(provenance)
        try:
            seen = []
            for _ in range(2):
                assert _cell(runner, SQUARE).outcome == "passed"
                seen.append(runner.last_handshake["pid"])
            hs = runner.last_handshake
            assert (hs["isolated"], hs["no_site"]) == (1, 1)
            assert_isolated_env(hs)
            cwd = hs["cwd"]
            assert os.path.realpath(cwd).startswith(os.path.realpath(tempfile.gettempdir()))
            assert hs["importable"] == {"boto3": False, "delphi": False}
        finally:
            runner.close()
        assert not os.path.exists(cwd)
        pids[provenance] = seen
    assert pids["model_output"][0] != pids["model_output"][1], "model output: a fresh worker"
    assert pids["synthetic"][0] == pids["synthetic"][1], "synthetic: one worker per run"


def test_t_exe_u4_6c_a_leaked_key_is_named(monkeypatch):
    runner = _runner(spawner=_fake(env={"DELPHI_U4_SENTINEL": "x"}))
    try:
        assert _cell(runner, SQUARE).outcome == "passed"
        hs = runner.last_handshake
    finally:
        runner.close()
    with pytest.raises(AssertionError, match="DELPHI_U4_SENTINEL"):
        assert_isolated_env(hs)


# --------------------------------------------------------------------- T-EXE-U4-7
CANDIDATES_7 = {
    "import at module level": ("import os\n" + SQUARE, "exec_raised"),
    "open inside f": ("def f(x):\n    return open('/etc/hosts').read()\n", "call_raised"),
    "eval inside f": ("def f(x):\n    return eval('1')\n", "call_raised"),
    "exec inside f": ("def f(x):\n    exec('y = 1')\n    return x * x\n", "call_raised"),
    "__import__ inside f": ("def f(x):\n    return __import__('os')\n", "call_raised"),
}


@pytest.mark.parametrize("case", sorted(CANDIDATES_7))
def test_t_exe_u4_7_denied_builtins_are_unreachable_by_name(case):
    code, reason = CANDIDATES_7[case]
    runner = _runner("model_output")
    try:
        outcome = _cell(runner, code)
    finally:
        runner.close()
    assert (outcome.outcome, outcome.reason, outcome.score) == ("candidate_failed", reason, 0.0)


def test_t_exe_u4_7_cells_share_nothing_and_survive_system_exit():
    runner = _runner("synthetic")
    try:
        assert _cell(runner, "def g(x):\n    return x * x\nf = g\n").outcome == "passed"
        later = _cell(runner, "def f(x):\n    return g(x)\n")
        assert (later.outcome, later.reason) == ("candidate_failed", "call_raised")
        pid = runner.last_handshake["pid"]
        exits = _cell(runner, "def f(x):\n    raise SystemExit(0)\n")
        assert (exits.outcome, exits.reason) == ("candidate_failed", "call_raised")
        assert _cell(runner, SQUARE).outcome == "passed"
        assert runner.last_handshake["pid"] == pid, "the same per_run worker survived"
    finally:
        runner.close()


def test_t_exe_u4_7_every_synthetic_reference_body_passes():
    from delphi.data import synthetic
    from delphi.data.synthetic_provider import _reference_code

    runner = _runner("synthetic")
    try:
        for i, (_desc, fn, cases) in enumerate(synthetic._CODE_TASKS):
            q = _query(cases, entry=fn, qid=f"ref-{i}")
            outcome = _cell(runner, _reference_code(q), q)
            assert outcome.outcome == "passed", (fn, outcome)
    finally:
        runner.close()


# --------------------------------------------------------------------- T-EXE-U4-8
@pytest.mark.parametrize("provenance", ["synthetic", "model_output"])
def test_t_exe_u4_8_a_hanging_candidate_times_out(provenance):
    fake = _fake()
    runner = _runner(provenance, spawner=fake, timeout_s=0.5)
    box: dict = {}

    def call():
        box["outcome"] = _cell(runner, HANG)

    try:
        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        worker.join(0.5 + 2.0)
        assert not worker.is_alive(), "the timeout did not fire within its bound"
        assert (box["outcome"].outcome, box["outcome"].reason) == (
            "candidate_failed", "timed_out")
        assert fake.procs[0].poll() is not None, "the hung worker was killed and reaped"
        assert _cell(runner, SQUARE).outcome == "passed"
        assert len(fake.procs) == 2, "the next cell got a new worker"
    finally:
        runner.close()


# --------------------------------------------------------------------- T-EXE-U4-10
def test_t_exe_u4_10_a_dead_runner_is_our_failure(failure_lines):
    scoring = _scoring()
    runner = _runner(spawner=_fake(substitute=EXIT_1_CHILD))
    try:
        with pytest.raises(scoring.ScoringHarnessError) as info:
            _cell(runner, SQUARE)
    finally:
        runner.close()
    assert info.value.reason == "runner_failed"
    (line,) = failure_lines.records("delphi.eval")
    data = json.loads(line.getMessage())
    assert line.levelno == logging.ERROR
    assert (data["event"], data["reason"]) == ("scoring_harness_error", "runner_failed")
    assert "stand-in child failed" in data["detail"]


# --------------------------------------------------------------------- T-EXE-U4-11
def test_t_exe_u4_11_the_battery_crosses_as_a_literal():
    scoring = _scoring()
    fake = _fake()
    runner = _runner(spawner=fake)
    try:
        tup = _query([((2,), (2, 4)), ((3,), (3, 9))])
        assert _cell(runner, "def f(x):\n    return (x, x * x)\n", tup).outcome == "passed"
        spawned = len(fake.calls)
        with pytest.raises(scoring.ScoringHarnessError) as info:
            _cell(runner, SQUARE, _query([((2,), object())], qid="q-obj"))
        assert info.value.reason == "test_case_not_literal"
        assert len(fake.calls) == spawned, "no spawn for a battery that cannot cross"
    finally:
        runner.close()


# --------------------------------------------------------------------- T-EXE-U4-12
def test_t_exe_u4_12_worker_lifetime_follows_provenance():
    counts = {}
    for provenance in ("synthetic", "model_output"):
        fake = _fake()
        runner = _runner(provenance, spawner=fake)
        try:
            for i in range(20):
                assert _cell(runner, SQUARE, _query(qid=f"q{i}")).outcome == "passed"
        finally:
            runner.close()
        counts[provenance] = len(fake.calls)
    assert counts == {"synthetic": 1, "model_output": 20}


# --------------------------------------------------------------------- T-EXE-U4-15
def test_t_exe_u4_15_a_failed_build_closes_its_runner(monkeypatch):
    bm = importlib.import_module("delphi.eval.build_matrix")
    built = []

    class Captured(_runner_mod().CodeRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built.append(self)

    monkeypatch.setattr(bm, "CodeRunner", Captured)
    before = len(SPAWNER.procs)
    with pytest.raises(bm.BudgetExceeded):
        bm.build_response_matrix(code_dataset(3), synth_pool(), SyntheticCouncil(),
                                 SyntheticJudge(), cost_cap_usd=1e-9)
    (runner,) = built
    assert runner.closed
    started = SPAWNER.procs[before:]
    assert started and all(p.poll() is not None for p in started)


# --------------------------------------------------------------------- T-EXE-U4-16
def _misbehaving(tmp_path, name: str, **subst) -> pathlib.Path:
    source = MISBEHAVING[name]
    for token, value in subst.items():
        source = source.replace(token, value)
    path = tmp_path / f"{name}.py"
    path.write_text(source)
    return path


def test_t_exe_u4_16_a_a_stderr_flood_does_not_block_the_parent(tmp_path):
    runner = _runner(harness_path=_misbehaving(tmp_path, "stderr_flood"))
    try:
        assert _cell(runner, SQUARE).outcome == "passed"
    finally:
        runner.close()


@pytest.mark.parametrize("name", ["no_newline", "two_lines"])
def test_t_exe_u4_16_b_c_an_oversized_or_doubled_answer_is_runner_failed(tmp_path, name):
    scoring = _scoring()
    runner = _runner(harness_path=_misbehaving(tmp_path, name))
    try:
        with pytest.raises(scoring.ScoringHarnessError) as info:
            _cell(runner, SQUARE)
    finally:
        runner.close()
    assert info.value.reason == "runner_failed"


def test_t_exe_u4_16_d_a_late_line_is_never_the_next_answer(tmp_path):
    scoring = _scoring()
    runner = _runner(harness_path=_misbehaving(tmp_path, "late_line"))
    try:
        assert _cell(runner, SQUARE).outcome == "passed"
        with pytest.raises(scoring.ScoringHarnessError) as info:
            _cell(runner, SQUARE, _query(qid="q-second"))
    finally:
        runner.close()
    assert info.value.reason == "runner_failed"


def test_t_exe_u4_16_e_a_grandchild_dies_with_its_worker(tmp_path):
    pidfile, lock = tmp_path / "grandchild.pid", tmp_path / "grandchild.lock"
    harness = _misbehaving(tmp_path, "grandchild", PIDFILE_PATH=repr(str(pidfile)),
                           LOCK_PATH=repr(str(lock)))
    runner = _runner(harness_path=harness, timeout_s=3.0)
    try:
        outcome = _cell(runner, SQUARE)
    finally:
        runner.close()
    assert (outcome.outcome, outcome.reason) == ("candidate_failed", "timed_out")
    assert pidfile.exists(), "the grandchild started and took its lock"
    assert lock_released_within(lock, 2.0), (
        f"grandchild {pidfile.read_text()} outlived its worker")


# --------------------------------------------------------------------- guard
def test_u4_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
