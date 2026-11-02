"""AgentCore Runtime container entrypoint.

This is the file the AgentCore Runtime container runs. It uses the current
`bedrock-agentcore` SDK (BedrockAgentCoreApp + @app.entrypoint + app.run()), which
implements the HTTP protocol contract AgentCore Runtime expects (POST /invocations,
GET /ping on port 8080). It is NOT the deprecated `bedrock-agentcore-starter-toolkit`
Python deployment helper and NOT classic Bedrock Agents.

Deployment is handled out-of-band by the AgentCore CLI (`@aws/agentcore`) / the CDK
stack in infra/ (aws_bedrockagentcore.CfnRuntime), which builds this into an ARM64
container image and registers it as an AWS::BedrockAgentCore::Runtime.

Local dev: `agentcore dev` (from the AgentCore CLI) or `python -m delphi.agent.entrypoint`.
"""
from __future__ import annotations

from bedrock_agentcore import BedrockAgentCoreApp  # type: ignore

from delphi.agent.app import build_agent_from_env

app = BedrockAgentCoreApp()
_agent = build_agent_from_env()


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Single request handler. `payload` is the JSON body from InvokeAgentRuntime.

    AgentCore delivers the request body as `payload`; the arm (router / pinned / ipr)
    is fixed per deployed runtime via the DELPHI_ARM env var, so one image + config
    produces every experimental arm with routing as the only variable (design D4/T-INF-1).
    """
    return _agent.handle_request(payload)


if __name__ == "__main__":  # pragma: no cover - container / local dev only
    app.run()
