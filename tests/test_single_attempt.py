"""One converse() is one model call — U2 LC-U2-10 (agent/bedrock.py; ENT-207; BR7.1-BR7.3).

Integration tier: a REAL botocore `bedrock-runtime` client, intercepted at `before-send`.
That hook fires once per attempt inside botocore's retry loop and answers before any socket
is opened, so it counts sends - not configuration keywords - and works under the socket
guard (TS-U2-3). botocore's back-off sleep is replaced by an attribute assignment, so no test
sleeps. Every client-building test runs with dummy credentials, IMDS off and no profile.

  T-GEN-U2-1  with AWS_MAX_ATTEMPTS=5 and AWS_RETRY_MODE=adaptive set, the lazily built
              client under a hook answering HTTP 429 ThrottlingException makes exactly ONE
              send, and ClientError is raised (NFR7.2, BR7.3, SD-U2-6)
  T-GEN-U2-2  sanity: a client built with no Config, under the same hook, makes MORE than one
              send - the instrument sees retries. Keep-fixed by design: it measures botocore,
              not U2, and is T-GEN-U2-1's own negative control (TS-U2-3)
  T-GEN-U2-3  build_runtime_client(region).meta.config.retries ==
              {"total_max_attempts": 1, "mode": "standard"} (BR7.1, TS-U2-2)
  T-GEN-U2-4  injected clients: no Config -> ValueError naming the required config;
              max_attempts 1 -> ValueError; total_max_attempts 1 -> accepted; max_attempts 0
              -> accepted AND exactly one send (BR7.2, NFR7.3, SD-U2-7, review R-03)
  T-GEN-U2-5  a canned HTTP 200 Converse body parses into a ConverseResult with the body's
              tokens and text (SD-U2-19)
  T-GEN-U2-6  a hook raising ReadTimeoutError gives one send and surfaces ReadTimeoutError
              (M-3, NFR7.4)
  T-GEN-U2-7  RouterAgent over a real BedrockConverseClient under the 429 hook: one send, and
              the emitted record carries generation_failed (BR7.3, agent half)
  T-GEN-U2-8  the autouse socket guard is active; the dummy-credential fixture sets the
              credentials, disables IMDS and removes any profile (NFR4.2, SD-U2-21)

Partition: botocore and the package, offline. No send leaves the process, no account.
"""
from __future__ import annotations

import importlib
import os
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)

import boto3  # loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError
from u2_support import (  # noqa: F401 - dummy_aws and no_backoff are fixtures
    CONVERSE_200_BODY,
    converse_200,
    count_sends,
    dummy_aws,
    no_backoff,
    read_timeout,
    throttling_429,
)

from delphi.agent.bedrock import GenerationParams
from delphi.agent.telemetry import InMemorySink
from delphi.router.baselines import AlwaysModelRouter

REGION = "us-west-2"
SINGLE = {"total_max_attempts": 1, "mode": "standard"}


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_single_attempt.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _bedrock():
    return importlib.import_module("delphi.agent.bedrock")


def _raw_client(config: Config | None = None):
    kwargs = {"region_name": REGION}
    if config is not None:
        kwargs["config"] = config
    return boto3.client("bedrock-runtime", **kwargs)


def _converse(gen):
    return gen.converse("vendor.mid-v1", "hi", GenerationParams())


def test_t_gen_u2_1_one_send_under_throttling_whatever_the_environment(
        dummy_aws, no_backoff, monkeypatch):
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("AWS_RETRY_MODE", "adaptive")
    gen = _bedrock().BedrockConverseClient(region=REGION)
    sends = count_sends(gen._ensure_client(), throttling_429)
    with pytest.raises(ClientError) as info:
        _converse(gen)
    assert info.value.response["Error"]["Code"] == "ThrottlingException"
    assert sends.n == 1


def test_t_gen_u2_2_the_instrument_sees_retries(dummy_aws, no_backoff):
    client = _raw_client()
    sends = count_sends(client, throttling_429)
    with pytest.raises(ClientError):
        client.converse(modelId="vendor.mid-v1",
                        messages=[{"role": "user", "content": [{"text": "hi"}]}])
    assert sends.n > 1


def test_t_gen_u2_3_the_lazy_client_is_built_single_attempt(dummy_aws):
    client = _bedrock().build_runtime_client(REGION)
    assert client.meta.config.retries == SINGLE
    assert client.meta.region_name == REGION


def test_t_gen_u2_4_an_injected_client_cannot_bypass_the_single_attempt(dummy_aws, no_backoff):
    bedrock = _bedrock()
    for config in (None, Config(retries={"max_attempts": 1})):
        with pytest.raises(ValueError) as info:
            bedrock.BedrockConverseClient(region=REGION, client=_raw_client(config))
        assert "total_max_attempts" in str(info.value)
    bedrock.BedrockConverseClient(region=REGION, client=_raw_client(Config(retries=SINGLE)))
    zero = _raw_client(Config(retries={"max_attempts": 0}))
    gen = bedrock.BedrockConverseClient(region=REGION, client=zero)
    sends = count_sends(zero, throttling_429)
    with pytest.raises(ClientError):
        _converse(gen)
    assert sends.n == 1


def test_t_gen_u2_5_a_200_body_parses_into_the_result(dummy_aws):
    gen = _bedrock().BedrockConverseClient(region=REGION)
    sends = count_sends(gen._ensure_client(), converse_200)
    result = _converse(gen)
    usage = CONVERSE_200_BODY["usage"]
    assert (result.input_tokens, result.output_tokens) == (usage["inputTokens"],
                                                           usage["outputTokens"])
    assert result.text == "a canned answer" and result.model_id == "vendor.mid-v1"
    assert sends.n == 1


def test_t_gen_u2_6_a_read_timeout_is_one_send_and_surfaces(dummy_aws, no_backoff):
    gen = _bedrock().BedrockConverseClient(region=REGION)
    sends = count_sends(gen._ensure_client(), read_timeout)
    with pytest.raises(ReadTimeoutError):
        _converse(gen)
    assert sends.n == 1


def test_t_gen_u2_7_the_agent_records_one_failed_call(pool, dummy_aws, no_backoff):
    from delphi.agent.app import RouterAgent

    gen = _bedrock().BedrockConverseClient(region=REGION)
    sends = count_sends(gen._ensure_client(), throttling_429)
    sink = InMemorySink()
    agent = RouterAgent(pool, AlwaysModelRouter(pool, "mid"), gen, sink, arm="router")
    with pytest.raises(ClientError):
        agent.handle_request({"prompt": "hi"})
    assert sends.n == 1
    (rec,) = sink.records
    assert rec.error_kind == "generation_failed"
    # botocore raises its modelled subclass, so the type name repeats the code (SD-U2-9).
    assert rec.error_detail.startswith("ThrottlingException[ThrottlingException]: ")


def test_t_gen_u2_8_offline_and_on_dummy_credentials(dummy_aws):
    with pytest.raises(NetworkBlocked):
        socket.socket()
    assert os.environ["AWS_ACCESS_KEY_ID"] == "testing"
    assert os.environ["AWS_EC2_METADATA_DISABLED"] == "true"
    assert "AWS_PROFILE" not in os.environ
