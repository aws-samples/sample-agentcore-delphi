"""Client factory tests — U1 LC-2 (verify/client.py).

The command builds its own bedrock-agentcore client so --profile has somewhere to land,
and passes it to AgentCoreRuntimeInvoker through the existing client= parameter. A client
built with no botocore config inherits boto3's legacy retry mode, so one printed
invocation could be several runtime calls and several Bedrock charges (DD-1, NFR1.2).

  T-VC-1  the BUILT client resolves to one total attempt  [needs the SDK, no account]
  T-VC-2  effective_total_attempts({"max_attempts": 1}) == 2 - the wrong-spelling trap
  T-VC-3  a perturbed constant raises VerifyConfigError at construction
  T-VC-4  read_timeout appears in the kwargs passed to Config, not merely resolved >= 60
  T-VC-5  no profile_name is passed when profile is None       [needs the SDK]
  T-VC-6  the module does not import botocore.config at module scope
  T-VC-7  the only service name the command constructs is bedrock-agentcore
"""
from __future__ import annotations

import pathlib

import pytest

from delphi.verify import client as client_mod
from delphi.verify.client import (
    RETRIES,
    VerifyConfigError,
    build_client,
    build_client_config,
    config_kwargs,
    effective_total_attempts,
)

boto3 = pytest.importorskip("boto3", reason="partition B: needs the SDK, no account")


def test_built_client_allows_exactly_one_total_attempt():
    """T-VC-1. The PRIMARY check, and it is on the built client, not the Config.

    botocore normalises the retries dict at client construction, so max_attempts: 1 would
    show up here as total_max_attempts: 2. Reading the Config object instead would pass on
    the wrong spelling.
    """
    client = build_client(region="eu-west-1", profile=None)
    assert client.meta.config.retries["total_max_attempts"] == 1


def test_effective_total_attempts_counts_the_initial_request():
    """T-VC-2. Tested against a literal the helper does not own, so it is not circular."""
    assert effective_total_attempts({"max_attempts": 1, "mode": "standard"}) == 2
    assert effective_total_attempts({"total_max_attempts": 1, "mode": "standard"}) == 1
    assert effective_total_attempts({"mode": "legacy"}) == 5


def test_a_wrong_spelling_fails_fast_at_construction(monkeypatch):
    """T-VC-3. Fail fast rather than quietly double-billing a reader."""
    monkeypatch.setitem(RETRIES, "max_attempts", 1)
    monkeypatch.delitem(RETRIES, "total_max_attempts", raising=False)
    with pytest.raises(VerifyConfigError):
        build_client_config()


def test_read_timeout_is_pinned_explicitly():
    """T-VC-4. botocore's default IS 60, so "resolved >= 60" cannot detect a dropped pin."""
    assert "read_timeout" in config_kwargs()
    assert config_kwargs()["read_timeout"] >= 60


def test_no_profile_name_when_no_profile_is_given():
    """T-VC-5. The author's "bedrock" profile must not be a fallback."""
    captured = {}

    class RecordingSession:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def client(self, service, **kwargs):
            captured["service"] = service
            return boto3.client(service, region_name="eu-west-1", config=kwargs.get("config"))

    build_client(region="eu-west-1", profile=None, session_factory=RecordingSession)
    assert "profile_name" not in captured


def test_module_does_not_import_the_sdk_at_module_scope():
    """T-VC-6. The repository convention, and what makes AC1.5.4 strictly stronger."""
    source = pathlib.Path(client_mod.__file__).read_text()
    head = source.split("def ")[0]
    assert "import boto3" not in head
    assert "import botocore" not in head


def test_only_the_agentcore_service_is_constructed():
    """T-VC-7. No logs or cloudwatch client on any path - ADR-010 puts the record in the body."""
    seen = []

    class RecordingSession:
        def __init__(self, **kwargs):
            pass

        def client(self, service, **kwargs):
            seen.append(service)
            return object()

    build_client(region="eu-west-1", profile=None, session_factory=RecordingSession)
    assert seen == ["bedrock-agentcore"]
