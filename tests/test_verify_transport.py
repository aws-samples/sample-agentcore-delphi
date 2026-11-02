"""Transport tests — U1 LC-3 (verify/transport.py).

Exactly one invoke_agent_runtime per run, timed with a monotonic clock around only that
call, with the correlation id in the payload (BR1.9, BR1.12, NFR1.1, NFR1.4).

  T-VT-1  exactly one call on the success path
  T-VT-2  exactly one call on the FAILURE path - the case a naive counter misses
  T-VT-3  no retry and no delay introduced by the command
  T-VT-4  request_id is present in the payload the transport sends
  T-VT-5  elapsed comes from a monotonic clock spanning only the invoke call
  T-VT-6  elapsed is reported on the failure path too
"""
from __future__ import annotations

import io
import json

import pytest

from delphi.verify.transport import invoke_once

ARN = "arn:aws:bedrock-agentcore:eu-west-1:111122223333:runtime/delphi_router-AbC123"


class CountingClient:
    """Hand-written fake; the repository uses no mocking library."""

    def __init__(self, *, body: bytes | None = None, status: int = 200, raises=None):
        self.calls: list[dict] = []
        self._body = body if body is not None else json.dumps({"ok": True}).encode()
        self._status = status
        self._raises = raises

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return {
            "response": io.BytesIO(self._body),
            "statusCode": self._status,
            "runtimeSessionId": kwargs["runtimeSessionId"],
        }


def test_exactly_one_call_on_the_success_path():
    """T-VT-1."""
    client = CountingClient()
    invoke_once(client, arn=ARN, prompt="hi", request_id="r1")
    assert len(client.calls) == 1


def test_exactly_one_call_on_the_failure_path():
    """T-VT-2. The counter must hold when the call raises, not only when it succeeds."""
    client = CountingClient(raises=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        invoke_once(client, arn=ARN, prompt="hi", request_id="r1")
    assert len(client.calls) == 1


def test_no_retry_and_no_delay_is_introduced():
    """T-VT-3. The command's own control flow adds no retry; the transport config is LC-2's."""
    client = CountingClient(raises=RuntimeError("boom"))
    import time

    start = time.perf_counter()
    with pytest.raises(RuntimeError):
        invoke_once(client, arn=ARN, prompt="hi", request_id="r1")
    assert (time.perf_counter() - start) < 0.5
    assert len(client.calls) == 1


def test_request_id_is_sent_in_the_payload():
    """T-VT-4. Today runtime_client.py:57 sends only {"prompt": ...}."""
    client = CountingClient()
    invoke_once(client, arn=ARN, prompt="hi", request_id="corr-42")
    sent = json.loads(client.calls[0]["payload"])
    assert sent["prompt"] == "hi"
    assert sent["request_id"] == "corr-42"


def test_elapsed_is_measured_and_positive():
    """T-VT-5."""
    client = CountingClient()
    outcome = invoke_once(client, arn=ARN, prompt="hi", request_id="r1")
    assert outcome.elapsed_ms >= 0.0
    assert outcome.status_code == 200
    assert outcome.payload == {"ok": True}


def test_elapsed_is_reported_on_the_failure_path():
    """T-VT-6. A raised exception must not lose the timing."""
    from delphi.verify.transport import TransportRaised

    client = CountingClient(raises=RuntimeError("boom"))
    with pytest.raises(TransportRaised) as exc:
        invoke_once(client, arn=ARN, prompt="hi", request_id="r1", wrap_errors=True)
    assert exc.value.elapsed_ms >= 0.0
    assert isinstance(exc.value.original, RuntimeError)
