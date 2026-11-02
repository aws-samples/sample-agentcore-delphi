"""Static guards over U2's failure paths — U2 LC-U2-13 (HandlerInventory), LC-U2-17.

AC4.2.6 says any failure path in src/ produces log output. Its testable reading (BR6.4,
FR4.5.4, SD-U2-14): every `except` handler under src/delphi/ SURFACES the error, or matches
exactly one anchored allowlist entry that records why it converts the error into a value.
The allowlist fails closed - an entry that matches nothing, matches twice, or matches a
handler that now surfaces is itself a failure.

A handler surfaces when its body contains a `raise`, a reference to `sys.stderr`, or a call
to `info` / `warning` / `error` / `exception` / `critical`, or to `log` with a literal level
of INFO or above. A `debug` call never counts: every logger in the package is installed at
INFO, so a DEBUG call cannot emit (nfr-requirements review R-01). A call to a helper that
raises does not count either, which is why every handler U2 adds carries its own literal
`raise` (D-CG-U2-2, nfr-design review R-01). Stated limits, carried from upstream: a handler
that raises on one branch and swallows on another passes; method names are matched, not
receivers, so `parser.error(...)` would count.

  T-FP-U2-1  every handler under src/delphi/ surfaces or matches exactly one of the SEVEN
             anchored entries, and every entry matches exactly one non-surfacing handler
             (FR4.5.4, BR6.4, SD-U2-14). U4 re-anchored two entries and kept the count at
             seven: the learned-router entry became RouterAgent._route (BR1.8), and the
             scoring entry moved to the child harness's run_cell (BR4.14)
  T-FP-U2-2  predicate self-test on synthetic sources: `log.debug(...)` alone does not
             surface; `log(logging.DEBUG, ...)` does not; `log(logging.WARNING, ...)` does; a
             literal raise does; a handler that only calls a helper which raises does not
             (SD-U2-14, nfr-design review R-01). Keep-fixed: the cases are the controls
  T-FP-U2-3  `.converse` / `.converse_stream` calls appear only in agent/bedrock.py and once,
             at the seam, in agent/app.py; the client name "bedrock-runtime" only in
             agent/bedrock.py (NFR6.1, SD-U2-23, D3). Keep-fixed; negative control: a seeded
             `.converse(` elsewhere fails
  T-FP-U2-4  an AST import scan, function-local imports included, of delphi.agent.app,
             .telemetry, .bedrock, .inbound and delphi.failure_log finds no requests, httpx,
             urllib3, http.client or urllib.request (NFR6.2 (b), SD-U2-24 (b)). Negative
             control: a seeded `import httpx` fails
  T-FP-U2-5  no time.sleep call in U2's new test modules or tests/u2_support.py (NFR4.3,
             SD-U2-22). Negative control: a seeded source fails
  T-FP-U2-6  every `# pragma: no cover` in the files U2 edits has a ` - <why>` reason
             (NFR3.2, SD-U2-19). Keep-fixed; negative control: a seeded bare pragma fails
  T-FP-U2-7  router/learned.py imports neither logging nor failure_log - the router stays
             pure (`u2-decision-telemetry/nfr-design/logical-components.md` § 3 rule 2,
             BR4.3). Keep-fixed; negative control: a seeded import fails
  T-FP-U2-8  no xfail reason anywhere in tests/ names U2 or ADR-003, and verify/render.py does
             not say "U2 adds one" - the gates U2 opens are closed in U2's change (BR10.4)
  T-FP-U2-9  the autouse socket guard is active (NFR4.2)

Partition: stdlib `ast` only, no SDK, no account.
"""
from __future__ import annotations

import ast
import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
from dataclasses import dataclass

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "delphi"
TESTS = ROOT / "tests"
LOG_METHODS = {"info", "warning", "error", "exception", "critical"}
LEVELS_AT_LEAST_INFO = {"INFO", "WARNING", "WARN", "ERROR", "CRITICAL", "FATAL"}


@dataclass(frozen=True)
class Anchor:
    path: str        # relative to src/delphi/
    qualname: str    # enclosing function / class path
    exc_type: str    # the handler's exception-type source text


#: Seven entries. 1 is U2's, 2 is U4's (it replaces U2's learned-router entry, BR1.8); 3-7
#: sit in U3-owned files and are recorded here, test-side only, with reasons read from U3's
#: code (NR-2: U3 has not reviewed them).
ALLOWLIST: dict[Anchor, str] = {
    # U4 BR4.14 (BRK-22): the broad catch moved with the exec into the child harness, and it
    # catches BaseException there (ND-3) so a candidate's SystemExit stays the candidate's.
    Anchor("eval/cell_harness.py", "run_cell", "BaseException"):
        "A candidate failure is a measured outcome, returned as "
        "ScoringOutcome(candidate_failed, reason) and asserted per reason by the BR8.2 tests. "
        "Harness errors are validated before this try and raise (BR8.1).",
    # U4 BR1.8 (BRK-10): the learned router's own catch is gone; the agent's replaces it.
    Anchor("agent/app.py", "RouterAgent._route", "Exception"):
        "The router's error becomes the FallbackDecision and is logged as router_fallback "
        "through _write (BR1.4); _build_fallback raises only on an agent defect (SD-U4-2).",
    Anchor("train/artifact_contract.py", "_load", "(ValueError, OSError)"):
        "Converted into a reported Violation('missing_file', ...), which the caller surfaces.",
    Anchor("train/artifact_contract.py", "_artifact_hash",
           "(KeyError, TypeError, ValueError, AttributeError)"):
        "Converted into a reported Violation('artifact_hash', ...).",
    Anchor("train/publish.py", "_differences", "(ValueError, OSError, EOFError)"):
        "Converted into a reported difference line ('remote object is unreadable').",
    Anchor("train/publish.py", "_cmd_publish", "ValueError"):
        "An unparseable previous pointer is treated as 'not already published', so the "
        "command plans a publish rather than skipping. A deliberate default.",
    Anchor("train/publish.py", "main", "SystemExit"):
        "argparse has already written its usage message to stderr before raising; the "
        "handler only converts the exit code.",
}

#: U2's new test modules, plus the shared helpers (T-FP-U2-5).
U2_TEST_FILES = (
    "u2_support.py", "test_decision_record.py", "test_inbound_validation.py",
    "test_request_path.py", "test_routing_timing.py", "test_failure_log.py",
    "test_single_attempt.py", "test_scoring_split.py", "test_latency_sample.py",
    "test_failure_paths.py", "test_record_compat.py",
)
#: The src files U2 creates or edits (T-FP-U2-6).
U2_SRC_FILES = (
    "agent/app.py", "agent/telemetry.py", "agent/bedrock.py", "agent/inbound.py",
    "failure_log.py", "router/learned.py", "eval/scoring.py", "eval/latency_sample.py",
    "verify/render.py",
)
HTTP_MODULES = ("requests", "httpx", "urllib3", "http.client", "urllib.request")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_failure_paths.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


# ------------------------------------------------------------------------ the predicate
def _level_at_least_info(arg: ast.expr) -> bool:
    if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
        return arg.value.id == "logging" and arg.attr in LEVELS_AT_LEAST_INFO
    if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
        return arg.value >= 20
    return False


def surfaces(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Attribute) and node.attr == "stderr":
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in LOG_METHODS:
                return True
            if node.func.attr == "log" and node.args and _level_at_least_info(node.args[0]):
                return True
    return False


def handlers(source: str) -> list[tuple[str, str, ast.ExceptHandler]]:
    """(qualname, exception-type text, handler) for every except handler in `source`."""
    tree = ast.parse(source)
    found: list[tuple[str, str, ast.ExceptHandler]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visit(child, f"{prefix}.{child.name}" if prefix else child.name)
                continue
            if isinstance(child, ast.ExceptHandler):
                text = ast.get_source_segment(source, child.type) if child.type else ""
                found.append((prefix, text or "", child))
            visit(child, prefix)

    visit(tree, "")
    return found


def inventory_problems(files: dict[str, str], allowlist: dict[Anchor, str]) -> list[str]:
    """Every handler that neither surfaces nor is allowlisted, plus every stale entry."""
    problems: list[str] = []
    hits: dict[Anchor, int] = {a: 0 for a in allowlist}
    for path, source in sorted(files.items()):
        for qualname, exc_type, h in handlers(source):
            anchor = Anchor(path, qualname, exc_type)
            if anchor in allowlist:
                hits[anchor] += 1
                if surfaces(h):
                    problems.append(f"stale entry, handler now surfaces: {anchor}")
            elif not surfaces(h):
                problems.append(f"silent handler: {path}:{h.lineno} {qualname} {exc_type}")
    for anchor, n in hits.items():
        if n != 1:
            problems.append(f"entry matches {n} handlers (must be exactly 1): {anchor}")
    return problems


def _package_sources() -> dict[str, str]:
    return {str(p.relative_to(PKG)): p.read_text() for p in sorted(PKG.rglob("*.py"))}


def test_t_fp_u2_1_every_handler_surfaces_or_is_allowlisted():
    assert len(ALLOWLIST) == 7
    assert all(reason.strip() for reason in ALLOWLIST.values())
    assert inventory_problems(_package_sources(), ALLOWLIST) == []


def _one(body: str) -> ast.ExceptHandler:
    src = "import logging, sys\ndef f():\n    try:\n        g()\n    except Exception:\n"
    src += "".join(f"        {line}\n" for line in body.splitlines())
    ((_, _, h),) = handlers(src)
    return h


def test_t_fp_u2_2_predicate_self_test():
    assert not surfaces(_one("log.debug('swallowed')"))
    assert not surfaces(_one("log.log(logging.DEBUG, 'swallowed')"))
    assert not surfaces(_one("log.log(10, 'swallowed')"))
    assert surfaces(_one("log.log(logging.WARNING, 'seen')"))
    assert surfaces(_one("log.warning('seen')"))
    assert surfaces(_one("raise"))
    assert surfaces(_one("print('x', file=sys.stderr)"))
    assert not surfaces(_one("_fail_and_raise()"))
    assert not surfaces(_one("return None"))
    # the allowlist fails closed: a stale entry and an unknown silent handler are both found
    silent = {"m.py": "def f():\n    try:\n        g()\n    except ValueError:\n        pass\n"}
    assert inventory_problems(silent, {}) == ["silent handler: m.py:4 f ValueError"]
    stale = {Anchor("m.py", "gone", "ValueError"): "reason"}
    assert inventory_problems(silent, stale)[-1].startswith("entry matches 0 handlers")


def generation_call_sites(files: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path, source in files.items():
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("converse", "converse_stream")):
                counts[path] = counts.get(path, 0) + 1
    return counts


def runtime_client_name_sites(files: dict[str, str]) -> set[str]:
    return {path for path, source in files.items()
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and node.value == "bedrock-runtime"}


def test_t_fp_u2_3_converse_stays_the_only_generation_path():
    files = _package_sources()
    sites = generation_call_sites(files)
    assert set(sites) <= {"agent/bedrock.py", "agent/app.py"}, sites
    assert sites.get("agent/app.py") == 1, "exactly one seam call in the agent"
    assert runtime_client_name_sites(files) == {"agent/bedrock.py"}
    seeded = dict(files, **{"eval/rogue.py": "def f(c):\n    return c.converse(m)\n"})
    assert "eval/rogue.py" in generation_call_sites(seeded)


def http_imports(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
        for name in names:
            found |= {m for m in HTTP_MODULES if name == m or name.startswith(m + ".")}
    return found


def test_t_fp_u2_4_no_http_client_on_the_request_path():
    for rel in ("agent/app.py", "agent/telemetry.py", "agent/bedrock.py", "agent/inbound.py",
                "failure_log.py"):
        assert http_imports((PKG / rel).read_text()) == set(), rel
    assert http_imports("def f():\n    import httpx\n") == {"httpx"}
    assert http_imports("from urllib.request import urlopen\n") == {"urllib.request"}


def sleep_calls(source: str) -> int:
    n = 0
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "sleep":
                n += 1
            elif isinstance(f, ast.Name) and f.id == "sleep":
                n += 1
    return n


def test_t_fp_u2_5_no_test_sleeps():
    for name in U2_TEST_FILES:
        assert sleep_calls((TESTS / name).read_text()) == 0, name
    assert sleep_calls("import time\ntime.sleep(0.1)\n") == 1


BARE_PRAGMA = re.compile(r"#\s*pragma:\s*no cover(?!\s+-\s+\S)")


def test_t_fp_u2_6_every_pragma_carries_a_reason():
    for rel in U2_SRC_FILES:
        path = PKG / rel
        if path.exists():
            bare = [ln for ln in path.read_text().splitlines() if BARE_PRAGMA.search(ln)]
            assert bare == [], (rel, bare)
    assert BARE_PRAGMA.search("x = 1  # pragma: no cover")
    assert not BARE_PRAGMA.search("x = 1  # pragma: no cover - needs live Bedrock")


def module_imports(source: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out |= {a.name for a in node.names}
    return out


def _impure(source: str) -> set[str]:
    return {n for n in module_imports(source)
            if n == "logging" or n.endswith("failure_log") or n.startswith("logging.")}


def test_t_fp_u2_7_the_router_stays_pure():
    assert _impure((PKG / "router" / "learned.py").read_text()) == set()
    assert _impure("import logging\n") == {"logging"}
    assert _impure("from delphi import failure_log\n") == {"failure_log"}


def xfail_reasons(source: str) -> list[str]:
    reasons = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "xfail"):
            for kw in node.keywords:
                if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                    reasons.append(str(kw.value.value))
    return reasons


def test_t_fp_u2_8_the_gates_u2_opens_are_closed():
    named = [(p.name, r) for p in sorted(TESTS.glob("*.py"))
             for r in xfail_reasons(p.read_text()) if re.search(r"\bU2\b|ADR-003", r)]
    assert named == []
    assert "U2 adds one" not in (PKG / "verify" / "render.py").read_text()
    seeded = '@pytest.mark.xfail(reason="U2 adds the field", strict=False)\ndef t(): pass\n'
    assert xfail_reasons(seeded) == ["U2 adds the field"]


def test_t_fp_u2_9_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
