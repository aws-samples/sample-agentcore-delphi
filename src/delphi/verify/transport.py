"""Transport — LC-3, serving BR1.9, BR1.12 and NFR1.4.

Exactly ONE `invoke_agent_runtime` per run (C5), timed with a monotonic clock around only
that call, with the correlation id in the payload so the returned record can be proved to
belong to this invocation.

**This delegates to `AgentCoreRuntimeInvoker` rather than calling boto3 itself** (R-48).
The first draft duplicated the status / empty-body / decode checks here, which left the
same logic in `runtime_client.py` with no caller at all — untested dead code behind that
class's `# pragma: no cover`, and a summary that wrongly called it load-bearing. One
implementation, in the class that owns the data-plane call, is the honest shape: this
module adds only the timing and the correlation id, which are genuinely its own.

Timing matters on the failure path too: a raised exception returns no outcome object, so
`TransportRaised` carries the elapsed figure rather than losing it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from delphi.agent.runtime_client import AgentCoreRuntimeInvoker


@dataclass(frozen=True)
class TransportOutcome:
    """What one successful invocation returned (ENT-002).

    Reaching this type means the invoker already accepted the response: a 2xx status, a
    non-empty body, and a body that decoded. The failure shapes arrive as exceptions.
    """

    status_code: int
    payload: dict
    session_id: str
    elapsed_ms: float


class TransportRaised(Exception):
    """Wraps an exception from the one call, preserving the elapsed time it consumed."""

    def __init__(self, original: BaseException, elapsed_ms: float):
        super().__init__(str(original))
        self.original = original
        self.elapsed_ms = elapsed_ms


def invoke_once(
    client: Any,
    *,
    arn: str,
    prompt: str,
    request_id: str,
    session_id: str | None = None,
    wrap_errors: bool = False,
    region: str = "",
) -> TransportOutcome:
    """Invoke the runtime exactly once, through the data-plane client that owns that call.

    `request_id` travels in the payload. Before this unit, `runtime_client.py` sent only
    the prompt, so the agent minted its own id and the caller had nothing to correlate
    against — which would let a cached or misrouted reply read as success.

    `client` is the already-built boto3 client (LC-2 builds it so `--profile` has somewhere
    to land); it is handed to the invoker through that class's existing `client=` parameter,
    so no boto3 import happens here.
    """
    invoker = AgentCoreRuntimeInvoker(arn, region=region, client=client)

    start = time.perf_counter()
    try:
        response = invoker.invoke(prompt, session_id=session_id, request_id=request_id)
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000.0
        if wrap_errors:
            raise TransportRaised(exc, elapsed) from exc
        raise
    elapsed = (time.perf_counter() - start) * 1000.0

    return TransportOutcome(
        status_code=response.status_code,
        payload=response.payload,
        session_id=response.session_id,
        elapsed_ms=elapsed,
    )
