"""The child harness: the ONE place candidate code executes (U4 LC-U4-7, BR4.5-BR4.7, ENT-408).

This file is run BY PATH as `sys.executable -I -S cell_harness.py` by eval/runner.py, with an
empty environment and a fresh temporary working directory. Nothing in `delphi` imports it,
and it imports nothing from `delphi` or any third party - only the standard-library modules
{ast, contextlib, io, json, sys, os, importlib.util} (NR-4). That allowlist is also why this
is the one module under src/ without `from __future__ import annotations`. `-I` keeps
PYTHON* variables, the user site and the script's directory off sys.path; `-S` skips `site`,
so the venv's packages (boto3, delphi) cannot be imported.

Protocol (TS-U4-5, TS-U4-6), one JSON object per line on stdout:
  1. the handshake, written first: isolated and no_site flags, the environment's KEYS (never
     its values), the cwd, the pid, and whether boto3 / delphi are importable;
  2. then, per request line on stdin {rid, code, entry_point, tests} - where `tests` is the
     battery's repr, parsed here with ast.literal_eval so tuples stay tuples - exactly one
     response line echoing `rid`: {"outcome": "passed"}, or {"outcome": "candidate_failed"}
     with either `reason` (entry_point_undefined, entry_point_not_callable, wrong_answer) or
     `raised_at` (exec, call, compare). The parent maps `raised_at` to U2's reasons.

Each cell is exec'd with a FRESH builtins dict built from SAFE_BUILTINS and a fresh
namespace, so no cell sees another's names. The candidate's own stdout and stderr are
captured and never share the response channel.

This is NOT a sandbox. Restricted builtins can be escaped by object introspection, and an
escaped candidate can read the user's files - including ~/.aws - and reach the network. That
is why model output runs only after an explicit opt-in (eval/scoring.py), and why a real
sandbox (seccomp or a microVM) is still required before Phase 4 scores real model output.

Coverage: these functions run only in the `-I -S` child, which `--cov` does not measure, so
each carries a pragma with that reason rather than reporting a false zero (TS-U4-8).
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
import sys

#: The builtins candidate code may name (ENT-408, D-CG-U4-6). Declared once. Everything else
#: is absent by construction: __import__, open, exec, eval, compile, input, breakpoint, help,
#: globals, locals, vars, exit, quit, memoryview, getattr, setattr, delattr, hasattr, type,
#: object, super, dir and id among them.
SAFE_BUILTINS = (
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float",
    "frozenset", "int", "isinstance", "iter", "len", "list", "map", "max", "min", "next",
    "pow", "range", "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "zip", "__build_class__",
    "Exception", "ArithmeticError", "IndexError", "KeyError", "StopIteration", "TypeError",
    "ValueError", "ZeroDivisionError",
)


def _safe_builtins() -> dict:  # pragma: no cover - runs in the -I -S child (T-EXE-U4-6r/-7)
    real = vars(sys.modules["builtins"])
    return {name: real[name] for name in SAFE_BUILTINS}


def handshake() -> dict:  # pragma: no cover - runs in the -I -S child (T-EXE-U4-6r/-6c)
    return {
        "isolated": sys.flags.isolated,
        "no_site": sys.flags.no_site,
        "env_keys": sorted(os.environ),
        "cwd": os.getcwd(),
        "pid": os.getpid(),
        "importable": {m: importlib.util.find_spec(m) is not None for m in ("boto3", "delphi")},
    }


def run_cell(  # pragma: no cover - runs in the -I -S child (T-EXE-U4-6r/-7/-8/-16)
    rid: int, code: str, entry_point: str, tests_repr: str
) -> dict:
    """One cell: exec, look up the entry point, run the battery. Never raises."""
    tests = ast.literal_eval(tests_repr)
    glb = {"__builtins__": _safe_builtins(), "__name__": "__candidate__"}
    ns: dict = {}
    step = "exec"
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            # The one execution site, reached only after the explicit opt-in (scoring.py); not a
            # sandbox, as README.md and SECURITY.md state.
            exec(code, glb, ns)  # noqa: S102 - opt-in only, documented  # nosec B102
            fn = ns.get(entry_point)
            if fn is None:
                return {"rid": rid, "outcome": "candidate_failed",
                        "reason": "entry_point_undefined"}
            if not callable(fn):
                return {"rid": rid, "outcome": "candidate_failed",
                        "reason": "entry_point_not_callable"}
            for args, expected in tests:
                step = "call"
                got = fn(*args)
                step = "compare"
                if got != expected:
                    return {"rid": rid, "outcome": "candidate_failed", "reason": "wrong_answer"}
        return {"rid": rid, "outcome": "passed"}
    except BaseException:  # noqa: BLE001 - candidate code may raise anything; scored 0.0 by definition
        return {"rid": rid, "outcome": "candidate_failed", "raised_at": step}


def _send(out, obj: dict) -> None:  # pragma: no cover - runs in the -I -S child (T-EXE-U4-6r)
    out.write(json.dumps(obj) + "\n")
    out.flush()


def main() -> None:  # pragma: no cover - runs in the -I -S child (T-EXE-U4-6r/-7/-8/-16)
    out = sys.stdout
    _send(out, handshake())
    while True:
        line = sys.stdin.readline()
        if not line:
            return
        req = json.loads(line)
        _send(out, run_cell(req["rid"], req["code"], req["entry_point"], req["tests"]))


if __name__ == "__main__":  # pragma: no cover - runs in the -I -S child
    main()
