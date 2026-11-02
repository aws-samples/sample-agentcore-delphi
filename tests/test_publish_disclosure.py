"""The S9 pointer disclosure in `delphi-artifact publish` — U6a (D-CG-U6-6; infra-design R-01;
NFR8.5).

The S9 stop rule is written in terms of what `publish` actually prints, so the publisher must
name the pointer write as a write, and must print a statement the BUCKET-WRITE-CONFIRM question
can show verbatim without a resolved bucket name in it. Driven through U3's hand-written
FakeS3 / FakeSTS / FakeClients, imported from tests/test_artifact_publish.py, not copied.

  T-INF-U6-71a  No previous pointer, dry run: a line
                `would write pointer router/current.json: creates (no previous pointer)`.
  T-INF-U6-71b  A previous pointer naming another artifact, dry run: a line
                `would write pointer router/current.json: REPLACES the previous pointer above;
                bucket versioning unknown, the old body is printed above`, and the old body is
                printed before it.
  T-INF-U6-71c  A `statement (scrub-safe):` block whose bucket line is the location file's
                TEMPLATE (`delphi-artifacts-{account}-{region}`), never the resolved name, and
                which names the prefix, the objects, the pointer outcome and unknown versioning;
                the block passes the U5 scrub check.
  T-INF-U6-71d  With --execute the pointer line reads `write pointer <key>: ...`, and an
                unchanged pointer reads `pointer <key>: unchanged ...` in both modes.
  guard         the autouse socket guard is active.
"""
from __future__ import annotations

import json
import pathlib
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest
from test_artifact_publish import (  # noqa: F401 - realpool and art are fixtures
    BUCKET,
    POINTER,
    _hash,
    _publish,
    _run,
    _seeded_with_previous,
    art,
    realpool,
)

from delphi.tools.scrub_check import scan_blob

ROOT = pathlib.Path(__file__).resolve().parents[1]
CREATES = f"would write pointer {POINTER}: creates (no previous pointer)"
REPLACES = (f"would write pointer {POINTER}: REPLACES the previous pointer above; bucket "
            "versioning unknown, the old body is printed above")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_publish_disclosure.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _statement(out: str) -> list[str]:
    lines = out.splitlines()
    start = lines.index("statement (scrub-safe):")
    block = []
    for line in lines[start + 1:]:
        if not line.startswith("  "):
            break
        block.append(line.strip())
    return block


def test_t_inf_u6_71a_first_pointer_write_is_disclosed(art, fake_s3_cls, capsys):  # noqa: F811 - fixture
    code, _ = _run(_publish(art), fake_s3_cls())
    out = capsys.readouterr().out
    assert code == 0
    assert CREATES in out.splitlines()


def test_t_inf_u6_71b_a_replacement_names_itself(art, make_artifact, realpool,  # noqa: F811 - fixture
                                                 fake_s3_cls, capsys):
    s3, old_hash = _seeded_with_previous(make_artifact, realpool, fake_s3_cls)
    code, _ = _run(_publish(art), s3)
    out = capsys.readouterr().out
    assert code == 0 and s3.writes == []
    assert REPLACES in out.splitlines()
    assert out.index(old_hash) < out.index(REPLACES)


def test_t_inf_u6_71c_statement_block_is_scrub_safe(art, fake_s3_cls, capsys):  # noqa: F811 - fixture
    _run(_publish(art), fake_s3_cls())
    block = _statement(capsys.readouterr().out)
    loc = json.loads((ROOT / "config" / "artifact-location.json").read_text())
    assert f"bucket: {loc['bucket']}" in block
    text = "\n".join(block)
    assert BUCKET not in text
    assert f"prefix: router/{_hash(art)}/" in block
    assert any(line.startswith("objects to write:") for line in block)
    assert f"pointer {POINTER}: creates (no previous pointer)" in block
    assert "bucket versioning: unknown" in block
    assert scan_blob("statement", text.encode()) == []


def test_t_inf_u6_71d_execute_wording_and_unchanged_pointer(art, fake_s3_cls, capsys):  # noqa: F811 - fixture
    s3 = fake_s3_cls()
    code, _ = _run(_publish(art, "--execute"), s3)
    out = capsys.readouterr().out
    assert code == 0
    assert f"write pointer {POINTER}: creates (no previous pointer)" in out.splitlines()
    for extra in ((), ("--execute",)):
        code, _ = _run(_publish(art, *extra), s3)
        lines = capsys.readouterr().out.splitlines()
        assert code == 0
        assert any(ln.startswith(f"pointer {POINTER}: unchanged") for ln in lines), lines


def test_u6_publish_disclosure_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
