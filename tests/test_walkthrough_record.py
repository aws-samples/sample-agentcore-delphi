"""The clean-account walkthrough record — ENT-616 (U6a; NFR10.5; BR13.1, BR13.2; AC4.7.3).

  T-INF-U6-54  strict xfail until the human performs the walkthrough and commits
               evidence/walkthrough/record.json. Then it EXECUTES: the record holds the ENT-616
               field set (date, base image digest, the four serving packages' installed
               versions, aws-cdk-lib, CDK CLI and cdk-nag versions, the container's Python, the
               arms verified, the gaps found, the version provenance); its serving package keys
               equal requirements-agent.txt's four names; its provenance names the package
               listing run inside the built image, not the declared floors. Because the marker
               is strict, the first passing run fails the suite, so the marker is removed in the
               change that commits the record (team.md, deferral register).
  guard        the autouse socket guard is active.
"""
from __future__ import annotations

import json
import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
RECORD = ROOT / "evidence" / "walkthrough" / "record.json"
FIELDS = {"date", "base_image_digest", "serving_packages", "aws_cdk_lib", "cdk_cli", "cdk_nag",
          "python_container", "arms_verified", "gaps_found", "version_provenance"}


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_walkthrough_record.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _serving_names() -> set[str]:
    names = set()
    for line in (ROOT / "requirements-agent.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            names.add(re.split(r"[<>=!~\s\[]", line)[0].lower())
    return names


@pytest.mark.xfail(strict=True, reason="walkthrough not yet performed (NFR10, human act)")
def test_t_inf_u6_54_walkthrough_record_states_what_floated():
    if not RECORD.exists():
        raise AssertionError("walkthrough not yet performed: no evidence/walkthrough/record.json")
    rec = json.loads(RECORD.read_text())
    assert set(rec) == FIELDS, sorted(set(rec) ^ FIELDS)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", rec["date"])
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", rec["base_image_digest"])
    assert {k.lower() for k in rec["serving_packages"]} == _serving_names()
    assert rec["arms_verified"] and isinstance(rec["gaps_found"], list)
    assert "image" in rec["version_provenance"] and "listing" in rec["version_provenance"]


def test_u6_walkthrough_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
