"""The failure channel — U2 LC-U2-7 (delphi/failure_log.py) and LC-U2-9 (ConstructionGuard).

`delphi.agent` and `delphi.eval` write their failure lines through one stdlib-only helper.
It installs exactly one handler carrying a marker attribute, at INFO, with propagate off,
and that handler resolves sys.stderr when it EMITS - so a line reaches whatever stderr is
current, in a test and in the container alike (SD-U2-13, M-5, M-6).

  T-LOG-U2-1  after three agent constructions and five failure writes, with caplog.handler
              attached, exactly one marker-bearing handler is on delphi.agent (FR4.5.3 (a),
              NR-4)
  T-LOG-U2-2  in a child interpreter (sys.executable -I, src on sys.path, no pytest, no
              logging configuration) a forced generation failure writes exactly one parseable
              delphi.agent JSON line to stderr, nothing to stdout, and delphi.agent has one
              handler (SD-U2-13 (b), SD-U2-24 (a'))
  T-LOG-U2-3  after the first write propagate is False, and a recording handler on the ROOT
              logger receives no delphi.agent record (SD-U2-13 (c))
  T-LOG-U2-4  among delphi.agent's handlers whose class is defined in a delphi.* module there
              is exactly one, with the marker; a line written now reaches the CURRENT stderr
              even though the handler was installed while a different stderr was current
              (SD-U2-24 (a), nfr-design review R-03)
  T-LOG-U2-5  write emits exactly the fixed key set; record_json is accepted only on
              record_emit_failed (LC-U2-7, FR4.5.1)
  T-LOG-U2-6  delphi/failure_log.py imports only json, logging and sys, and nothing from
              delphi (`u2-decision-telemetry/nfr-design/logical-components.md` § 3 rule 1,
              SD-U2-18)
  T-LOG-U2-7  build_agent_from_env with DELPHI_ARM=router and no DELPHI_ARTIFACT_URI raises
              RuntimeError and writes one CRITICAL agent_construction_failed line with
              request_id null and arm router; DELPHI_ARM=ipr raises NotImplementedError with
              the same line (BR6.3, FM-19, D-CG-U2-8)
  T-LOG-U2-8  the autouse socket guard is active (NFR4.2)

Partition: stdlib, the package, and one child interpreter. No SDK call, no account.
"""
from __future__ import annotations

import ast
import importlib
import io
import json
import logging
import pathlib
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import subprocess
import sys

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest
from u2_support import (  # noqa: F401 - failure_lines is a fixture
    CountingConverse,
    RaisingConverse,
    failure_lines,
)

from delphi.agent.telemetry import InMemorySink
from delphi.router.baselines import AlwaysModelRouter

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
MARKER = "_delphi_failure_channel"

CHILD = """
import json, logging, sys
sys.path.insert(0, sys.argv[1])
from delphi.agent.app import RouterAgent
from delphi.agent.bedrock import ConverseClient
from delphi.agent.telemetry import InMemorySink
from delphi.config import Candidate, Pool, Price
from delphi.router.baselines import AlwaysModelRouter

class Boom(ConverseClient):
    def converse(self, model_id, prompt, params):
        raise RuntimeError("throttled")

pool = Pool((Candidate("mid", "vendor.mid-v1", "famB", Price(0.0008, 0.0032), True),),
            judge_id="ext.judge", judge_alias="judge", snapshot_date="2026-07-07")
agent = RouterAgent(pool, AlwaysModelRouter(pool, "mid"), Boom(), InMemorySink())
try:
    agent.handle_request({"prompt": "hi", "request_id": "child-1"})
except RuntimeError:
    pass
else:
    sys.exit(4)
sys.exit(0 if len(logging.getLogger("delphi.agent").handlers) == 1 else 3)
"""


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_failure_log.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _flog():
    return importlib.import_module("delphi.failure_log")


def _agent(pool, converse=None):
    from delphi.agent.app import RouterAgent

    return RouterAgent(pool, AlwaysModelRouter(pool, "mid"), converse or CountingConverse(),
                       InMemorySink())


def _markers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if getattr(h, MARKER, False)]


def test_t_log_u2_1_exactly_one_marker_handler(pool, failure_lines):
    flog = _flog()
    log = logging.getLogger("delphi.agent")
    for _ in range(3):
        _agent(pool)
    for i in range(5):
        flog.write(flog.failure_logger("delphi.agent"), logging.ERROR, "request_failed",
                   request_id=f"r{i}", arm="router", error_kind="generation_failed",
                   detail="RuntimeError: x")
    assert len(_markers(log)) == 1
    assert len(failure_lines.records()) == 5


def test_t_log_u2_2_the_line_reaches_stderr_with_no_logging_config():
    res = subprocess.run([sys.executable, "-I", "-c", CHILD, str(SRC)], capture_output=True,  # noqa: S603 - fixed argv: this interpreter + CHILD  # nosemgrep
                         text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    assert res.stdout == ""
    lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
    assert len(lines) == 1, res.stderr
    data = json.loads(lines[0])
    assert (data["event"], data["request_id"], data["error_kind"]) == (
        "request_failed", "child-1", "generation_failed")


def test_t_log_u2_3_lines_never_propagate_to_the_root_logger(pool):
    flog = _flog()

    class Recorder(logging.Handler):
        def __init__(self):
            super().__init__(logging.DEBUG)
            self.seen: list[logging.LogRecord] = []

        def emit(self, record):
            self.seen.append(record)

    recorder = Recorder()
    root = logging.getLogger()
    root.addHandler(recorder)
    try:
        with pytest.raises(RuntimeError):
            _agent(pool, RaisingConverse()).handle_request({"prompt": "hi"})
        flog.write(flog.failure_logger("delphi.agent"), logging.WARNING, "router_fallback",
                   request_id="r", arm="router", error_kind=None, detail="x")
    finally:
        root.removeHandler(recorder)
    assert logging.getLogger("delphi.agent").propagate is False
    assert [r for r in recorder.seen if r.name.startswith("delphi.agent")] == []


def test_t_log_u2_4_the_handler_resolves_stderr_when_it_emits(capsys, monkeypatch):
    flog = _flog()
    log = logging.getLogger("delphi.agent")
    for h in _markers(log):
        log.removeHandler(h)
    elsewhere = io.StringIO()
    with monkeypatch.context() as m:
        m.setattr(sys, "stderr", elsewhere)
        flog.failure_logger("delphi.agent")  # installed while another stderr is current
    flog.write(flog.failure_logger("delphi.agent"), logging.ERROR, "request_failed",
               request_id="r4", arm="router", error_kind="internal_error",
               detail="KeyError: x")
    ours = [h for h in log.handlers if type(h).__module__.startswith("delphi.")]
    assert len(ours) == 1 and getattr(ours[0], MARKER, False)
    assert ours[0].stream is sys.stderr
    err = capsys.readouterr().err
    assert '"request_id": "r4"' in err
    assert elsewhere.getvalue() == ""


def test_t_log_u2_5_the_key_set_is_fixed(failure_lines):
    flog = _flog()
    log = flog.failure_logger("delphi.agent")
    flog.write(log, logging.WARNING, "request_rejected", request_id="r", arm="router",
               error_kind="request_invalid", detail="prompt: missing")
    flog.write(log, logging.ERROR, "record_emit_failed", request_id="r", arm="router",
               error_kind=None, detail="RuntimeError: sink down", record_json="{}")
    first, second = failure_lines.json()
    assert set(first) == {"event", "request_id", "arm", "error_kind", "detail"}
    assert set(second) == set(first) | {"record_json"}
    with pytest.raises(ValueError):
        flog.write(log, logging.ERROR, "request_failed", request_id="r", arm="router",
                   error_kind="internal_error", detail="x", record_json="{}")
    with pytest.raises(ValueError):
        flog.write(log, logging.ERROR, "request_failed", request_id="r", arm="router",
                   error_kind="internal_error", detail="x", scores={"mid": 1.0})


def test_t_log_u2_6_the_channel_imports_only_the_standard_library():
    tree = ast.parse((SRC / "delphi" / "failure_log.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names - {"__future__"} == {"json", "logging", "sys"}


@pytest.mark.parametrize("arm,error", [("router", RuntimeError),
                                        ("ipr", NotImplementedError)])
def test_t_log_u2_7_a_construction_failure_is_logged_critical(arm, error, monkeypatch,
                                                              failure_lines):
    from delphi.agent.app import build_agent_from_env
    from delphi.config import load_pool

    monkeypatch.setenv("DELPHI_ARM", arm)
    monkeypatch.setenv("DELPHI_MODELS_FILE", str(ROOT / "config" / "models_realpool.yaml"))
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    monkeypatch.delenv("DELPHI_ARTIFACT_URI", raising=False)
    load_pool.cache_clear()
    try:
        with pytest.raises(error):
            build_agent_from_env()
    finally:
        load_pool.cache_clear()
    (line,) = failure_lines.records()
    data = json.loads(line.getMessage())
    assert line.levelno == logging.CRITICAL
    assert (data["event"], data["request_id"], data["arm"]) == (
        "agent_construction_failed", None, arm)
    assert data["detail"].startswith(error.__name__)


def test_t_log_u2_8_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
