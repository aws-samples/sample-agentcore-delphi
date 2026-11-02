"""Shared helpers for U4's tests (plan D-CG-U4-5, D-CG-U4-9). Not collected: no `test_` prefix.

Hand-written fakes only, never a mocking library (team.md § Testing Posture). Each U4 test
module still carries its own autouse socket guard and its self-test (TS-U4-7), so importing
this module changes no other unit's tests. U2's fakes and C2 validator are imported from
`u2_support`, never copied.

Routers (the agent-level fallback, BR1.1 and BR1.2):
* `ReaderRouter` - a reader-style `Router` subclass that knows nothing of any fallback and
  raises an ordinary exception of its own.
* `MalformedRouter` - returns a bad result WITHOUT calling `Router._validate`: an out-of-pool
  alias, a model id that does not match its alias, a non-`Decision`, or a bad `embed_ms`.
* `RaisingTimedRouter` - spends fake time inside `predict`, then raises.
* `wider_pool(pool)` - the pool plus one extra candidate, so a router built over it can
  return a `Decision` its own `_validate` accepts and the agent's pool does not hold.

Execution (BR4, the runner's spawner seam, LC-U4-12):
* `SpawnerFake` - records what the runner asked for (argv, env, cwd, start_new_session) and
  then starts a real stand-in process: by default the very argv it was given, or a
  `substitute` program (an exit-1 child, T-EXE-U4-10), or the given argv with a test-chosen
  `env` (the seeded leak control, T-EXE-U4-6c). Every Popen is kept in `procs`.
* `reap_leaked(spawner)` - kills and reaps every recorded process still alive and returns
  the pids that were alive: the module-scoped no-live-child check (D-CG-U4-9).
* Councils: `CountingCouncil` (duck-typed, counts `answer` calls), `SyntheticSubCouncil` (a
  subclass, which must NOT be granted synthetic), `EnvFlippingCouncil`.
* `code_dataset(n)` - a small Dataset of CODE queries with the synthetic batteries.
* `MISBEHAVING` - source text of the test-owned harnesses for T-EXE-U4-16, each writing a
  well-formed handshake first and then misbehaving in one way.
"""
from __future__ import annotations

import math
import os
import pathlib
import signal
import subprocess
import sys

from u2_support import FakeClock

from delphi.config import Candidate, Pool, Price, load_pool
from delphi.data.schema import Dataset, Query, TaskType
from delphi.data.synthetic_provider import SyntheticCouncil
from delphi.router.base import Decision, Router

ROOT = pathlib.Path(__file__).resolve().parents[1]
SYNTH_POOL = ROOT / "config" / "models_synthetic.yaml"


class ReaderError(Exception):
    """An exception type no part of `delphi` knows about."""


class ReaderRouter(Router):
    """What a reader might plug into the documented seam: predict only, and it raises."""

    def __init__(self, pool: Pool):
        self.pool_aliases = pool.aliases

    def predict(self, query: str) -> Decision:
        raise ReaderError("reader router has no answer")


class MalformedRouter(Router):
    """Returns `kind`'s malformed result and never calls `_validate` (BR1.2)."""

    KINDS = ("out_of_pool", "id_mismatch", "not_a_decision", "embed_ms_negative",
             "embed_ms_nan")

    def __init__(self, pool: Pool, kind: str):
        if kind not in self.KINDS:
            raise ValueError(kind)
        self.pool = pool
        self.pool_aliases = pool.aliases
        self.kind = kind

    def predict(self, query: str):
        mid = self.pool.by_alias("mid")
        scores = {a: 0.0 for a in self.pool_aliases}
        if self.kind == "out_of_pool":
            return Decision("vendor.ghost-v1", "ghost", scores, 1.0)
        if self.kind == "id_mismatch":
            return Decision("vendor.not-mid-v9", "mid", scores, 1.0)
        if self.kind == "not_a_decision":
            return {"chosen_alias": "mid", "model_id": mid.id}
        embed = -1.0 if self.kind == "embed_ms_negative" else math.nan
        return Decision(mid.id, "mid", scores, 1.0, metadata={"embed_ms": embed})


class RaisingTimedRouter(Router):
    """Advances the shared fake clock by `ms`, then raises (T-BND-U4-10)."""

    def __init__(self, pool: Pool, clock: FakeClock, ms: float):
        self.pool_aliases = pool.aliases
        self._clock = clock
        self._ms = ms

    def predict(self, query: str) -> Decision:
        self._clock.advance_ms(self._ms)
        raise RuntimeError("timed router failed")


def wider_pool(pool: Pool) -> Pool:
    """`pool` plus one `ghost` candidate the agent's own pool does not hold."""
    ghost = Candidate("ghost", "vendor.ghost-v1", "famZ", Price(0.0001, 0.0001), True)
    return Pool(pool.candidates + (ghost,), judge_id=pool.judge_id,
                judge_alias=pool.judge_alias, snapshot_date=pool.snapshot_date)


# ------------------------------------------------------------------------- spawning
class SpawnerFake:
    """The runner's spawner seam, recorded. Each call starts a real stand-in process."""

    def __init__(self, *, substitute: list[str] | None = None, env: dict | None = None):
        self.calls: list[dict] = []
        self.procs: list[subprocess.Popen] = []
        self._substitute = substitute
        self._env = env

    def __call__(self, argv, **kwargs):
        self.calls.append({"argv": list(argv), **kwargs})
        if self._substitute is not None:
            argv = self._substitute
        if self._env is not None:
            kwargs = dict(kwargs, env=dict(self._env))
        proc = subprocess.Popen(argv, **kwargs)  # noqa: S603 - argv is the runner's fixed child argv  # nosemgrep
        self.procs.append(proc)
        return proc


def reap_leaked(*spawners: SpawnerFake) -> list[int]:
    """Kill and reap every recorded process still alive; return the pids that were."""
    leaked = []
    for spawner in spawners:
        for proc in spawner.procs:
            if proc.poll() is None:
                leaked.append(proc.pid)
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
    return leaked


def lock_released_within(lock_path: pathlib.Path, bound_s: float) -> bool:
    """True when an exclusive flock on `lock_path` is granted within `bound_s`.

    The grandchild harness holds that lock for its whole life, and the kernel drops it when
    the process dies, so this waits on the death itself. A thread bounds the wait.
    """
    import fcntl
    import threading

    done = threading.Event()

    def take() -> None:
        with open(lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            done.set()

    threading.Thread(target=take, daemon=True).start()
    return done.wait(bound_s)


#: A stand-in child that dies before its handshake (T-EXE-U4-10).
EXIT_1_CHILD = [sys.executable, "-I", "-S", "-c",
                "import sys; sys.stderr.write('stand-in child failed'); sys.exit(1)"]


# ------------------------------------------------------------------------- councils
class CountingCouncil:
    """Duck-typed: answers like the complementary SyntheticCouncil, and counts."""

    def __init__(self):
        self._inner = SyntheticCouncil(mode="complementary")
        self.calls = 0

    def answer(self, advisor: str, q: Query):
        self.calls += 1
        return self._inner.answer(advisor, q)


class SyntheticSubCouncil(SyntheticCouncil):
    """A subclass of SyntheticCouncil: it could call Bedrock, so it is never `synthetic`."""


class EnvFlippingCouncil(CountingCouncil):
    """Sets the opt-in variable to "0" from inside answer (T-EXE-U4-14 (d))."""

    def answer(self, advisor: str, q: Query):
        os.environ["DELPHI_ALLOW_UNSANDBOXED_EXEC"] = "0"
        return super().answer(advisor, q)


def synth_pool() -> Pool:
    load_pool.cache_clear()
    try:
        return load_pool(SYNTH_POOL)
    finally:
        load_pool.cache_clear()


_TASKS = [
    ("laurels", [((5,), 15), ((1,), 1), ((10,), 55)]),
    ("echo", [(("delphi",), "ihpled"), (("a",), "a")]),
    ("omens", [(([1, 2, 3, 4],), [2, 4]), (([5, 7],), [])]),
]


def code_dataset(n: int = 3, *, with_open_qa: bool = False) -> Dataset:
    queries = []
    for i in range(n):
        fn, cases = _TASKS[i % len(_TASKS)]
        queries.append(Query(f"u4-code-{i}", f"write {fn} #{i}", TaskType.CODE, "craft",
                             answer=fn, metadata={"entry_point": fn, "tests": cases}))
    if with_open_qa:
        queries.append(Query("u4-qa-0", "where is the oracle", TaskType.OPEN_QA, "lore",
                             answer="Mount Parnassus"))
    return Dataset("u4-fixture", tuple(queries))


def qa_dataset() -> Dataset:
    return Dataset("u4-qa", (Query("u4-qa-0", "where is the oracle", TaskType.OPEN_QA,
                                   "lore", answer="Mount Parnassus"),))


# --------------------------------------------------------------- misbehaving harnesses
_PRELUDE = '''\
import json, os, subprocess, sys, time
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()
def request():
    return json.loads(sys.stdin.readline())
send({"isolated": sys.flags.isolated, "no_site": sys.flags.no_site,
      "env_keys": sorted(os.environ), "cwd": os.getcwd(), "pid": os.getpid(),
      "importable": {"boto3": False, "delphi": False}})
'''

#: Each harness writes a well-formed handshake, then misbehaves in exactly one way.
MISBEHAVING = {
    # (a) a large stderr write, then a correct answer: the parent must not block on stderr
    "stderr_flood": _PRELUDE + '''\
while True:
    r = request()
    sys.stderr.write("x" * (1 << 20))
    sys.stderr.flush()
    send({"rid": r["rid"], "outcome": "passed"})
''',
    # (b) 2 MiB with no newline: the parent stops reading at its bound
    "no_newline": _PRELUDE + '''\
r = request()
sys.stdout.write("x" * (2 << 20))
sys.stdout.flush()
time.sleep(60)
''',
    # (c) a valid line and a second line in the same write
    "two_lines": _PRELUDE + '''\
r = request()
line = json.dumps({"rid": r["rid"], "outcome": "passed"})
sys.stdout.write(line + "\\n" + line + "\\n")
sys.stdout.flush()
time.sleep(60)
''',
    # (d) a correct answer, then the same answer again after a delay
    "late_line": _PRELUDE + '''\
r = request()
send({"rid": r["rid"], "outcome": "passed"})
time.sleep(0.3)
send({"rid": r["rid"], "outcome": "passed"})
while True:
    request()
''',
    # (e) start a grandchild that holds an exclusive lock on LOCK_PATH and then writes its
    # pid to PIDFILE_PATH; wait for that, then hang. The test replaces both tokens with the
    # repr of paths under its tmp_path. The lock is released only when the grandchild dies,
    # so the test can WAIT for its death rather than poll for it.
    "grandchild": _PRELUDE + '''\
r = request()
body = ("import fcntl, os, time\\n"
        "f = open(" + repr(LOCK_PATH) + ", 'w')\\n"
        "fcntl.flock(f, fcntl.LOCK_EX)\\n"
        "open(" + repr(PIDFILE_PATH) + ", 'w').write(str(os.getpid()))\\n"
        "time.sleep(60)\\n")
g = subprocess.Popen([sys.executable, "-c", body])
while not os.path.exists(PIDFILE_PATH):
    pass
while True:
    pass
''',
}
