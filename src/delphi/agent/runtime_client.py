"""Data-plane client for a DEPLOYED AgentCore Runtime (design §4 Phase 4, eval driver).

The eval harness (Phase 4) drives each deployed arm through this client:
`boto3.client("bedrock-agentcore").invoke_agent_runtime(...)`. This is the current
AgentCore data-plane API (service "bedrock-agentcore"), not the classic
bedrock-agent-runtime InvokeAgent used by legacy Bedrock Agents.

Kept behind an interface so the driver is testable without a live runtime.
"""
from __future__ import annotations

import abc
import json
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeResponse:
    payload: dict
    status_code: int
    session_id: str


class RuntimeTransportError(RuntimeError):
    """A non-2xx status, an empty body, or a body that would not decode.

    Raised rather than returned, because `status_code` was previously written at `:68`,
    declared on `RuntimeResponse`, and read by NO production caller - so a non-2xx came
    back as an apparently successful response and an empty error body became `{}`. A
    return value a caller can forget to check is how that happened; an exception cannot be
    ignored by accident.
    """

    def __init__(self, kind: str, status_code: int | None, body_excerpt: str):
        super().__init__(f"{kind}: statusCode={status_code} body={body_excerpt!r}")
        self.kind = kind
        self.status_code = status_code
        self.body_excerpt = body_excerpt


class RuntimeInvoker(abc.ABC):
    @abc.abstractmethod
    def invoke(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> RuntimeResponse:
        """Invoke the runtime once.

        `request_id` is a CORRELATION id travelling in the payload so a caller can prove
        the returned record belongs to this invocation (contract C1). It is keyword-only
        with a None default, so every existing caller and the hand-written fake in
        `tests/test_runtime_client.py` stay source-compatible.
        """
        raise NotImplementedError


class AgentCoreRuntimeInvoker(RuntimeInvoker):  # pragma: no cover - needs live runtime
    """Invokes a deployed runtime by ARN (or agent id + account).

    Each experimental arm is a separate deployed runtime (or a qualifier/endpoint on
    one), so the driver holds one invoker per arm and the arm identity lives in the ARN.
    """

    def __init__(self, agent_runtime_arn: str, region: str | None = None, client=None,
                 qualifier: str | None = None):
        self._arn = agent_runtime_arn
        self._region = region
        self._client = client
        self._qualifier = qualifier

    @property
    def region(self) -> str:
        """The region argument, else the region segment of the runtime ARN (U6 BR11.7).

        Resolved only when a real client is built, so an injected client needs none. There is
        no default region: an ARN without one, and no argument, raises RegionNotSetError.
        """
        from delphi.agent.bedrock import RegionNotSetError

        if self._region:
            return self._region
        parts = self._arn.split(":")
        if len(parts) >= 6 and parts[0] == "arn" and parts[3]:
            return parts[3]
        raise RegionNotSetError(f"no region given and none in the runtime ARN {self._arn!r}")

    def invoke(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> RuntimeResponse:
        if self._client is None:
            import boto3

            self._client = boto3.client("bedrock-agentcore", region_name=self.region)

        # The service requires runtimeSessionId >= 33 characters. `uuid4().hex` is 32, and
        # was rejected client-side on the first live run; the hyphenated form is 36.
        session_id = session_id or f"delphi-{uuid.uuid4()}"
        body_in: dict[str, str] = {"prompt": prompt}
        if request_id is not None:
            body_in["request_id"] = request_id
        kwargs = {
            "agentRuntimeArn": self._arn,
            "runtimeSessionId": session_id,
            "contentType": "application/json",
            "accept": "application/json",
            "payload": json.dumps(body_in).encode("utf-8"),
        }
        if self._qualifier:
            kwargs["qualifier"] = self._qualifier

        resp = self._client.invoke_agent_runtime(**kwargs)
        # `response` is a streaming body; read + decode it
        body = resp["response"].read()
        status_code = resp.get("statusCode", 200)

        # Raise rather than hand back a status nobody checks. Order matters: a 403 carrying
        # a JSON error body is a STATUS failure, not a decode failure, and getting that
        # backwards would send a reader looking in the wrong place.
        excerpt = body.decode("utf-8", errors="replace")[:240]
        if not 200 <= status_code < 300:
            raise RuntimeTransportError("non_success_status", status_code, excerpt)
        if not body:
            raise RuntimeTransportError("empty_payload", status_code, "")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeTransportError("undecodable_payload", status_code, excerpt) from exc

        return RuntimeResponse(
            payload=payload,
            status_code=status_code,
            session_id=resp.get("runtimeSessionId", session_id),
        )
