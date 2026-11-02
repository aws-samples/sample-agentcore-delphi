"""No region default on a deployed or reader-facing path — U6a (BR11.7; NFR6.1).

  T-INF-U6-47  No 'us-east-1' or 'us-west-2' literal anywhere in src/delphi/agent/, nor in a
               fenced code block of docs/deployment.md. A Bedrock client constructed with no
               region raises RegionNotSetError; building the agent with AWS_REGION unset
               raises it too, and U2's CRITICAL agent_construction_failed line still fires,
               naming it. The data-plane invoker takes its region from the runtime ARN; an ARN
               without one, and no region argument, raises RegionNotSetError. Pricing-snapshot
               regions in config/ are provenance, not defaults, and are out of scope.
  guard        the autouse socket guard is active.

Partition: stdlib, delphi, boto3 imported only to be loaded before the guard. No AWS call.
"""
from __future__ import annotations

import json
import logging
import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest
from u2_support import failure_lines  # noqa: F401 - failure_lines is a fixture

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGIONS = re.compile(r"us-east-1|us-west-2")
FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.M | re.S)


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_region_defaults.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def test_t_inf_u6_47_no_region_default_survives(monkeypatch, failure_lines):  # noqa: F811 - fixture
    from delphi.agent import bedrock, runtime_client
    from delphi.agent.app import build_agent_from_env
    from delphi.config import load_pool

    hits = [str(p.relative_to(ROOT)) for p in sorted((ROOT / "src/delphi/agent").rglob("*.py"))
            if REGIONS.search(p.read_text())]
    assert hits == []
    blocks = FENCE.findall((ROOT / "docs" / "deployment.md").read_text())
    assert blocks and not [b for b in blocks if REGIONS.search(b)]

    with pytest.raises(bedrock.RegionNotSetError):
        bedrock.BedrockConverseClient(region="")

    monkeypatch.setenv("DELPHI_ARM", "pinned:nova-lite")
    monkeypatch.delenv("DELPHI_MODELS_FILE", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    load_pool.cache_clear()
    try:
        with pytest.raises(bedrock.RegionNotSetError, match="AWS_REGION"):
            build_agent_from_env()
    finally:
        load_pool.cache_clear()
    (line,) = failure_lines.records()
    data = json.loads(line.getMessage())
    assert line.levelno == logging.CRITICAL and data["event"] == "agent_construction_failed"
    assert data["detail"].startswith("RegionNotSetError")

    acct = "1" * 12
    arn = f"arn:aws:bedrock-agentcore:eu-west-1:{acct}:runtime/delphi_router-x"
    assert runtime_client.AgentCoreRuntimeInvoker(arn).region == "eu-west-1"
    assert runtime_client.AgentCoreRuntimeInvoker(arn, region="ap-south-1").region == "ap-south-1"
    with pytest.raises(bedrock.RegionNotSetError):
        _ = runtime_client.AgentCoreRuntimeInvoker("arn:...:runtime/abc").region


def test_u6_region_defaults_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
