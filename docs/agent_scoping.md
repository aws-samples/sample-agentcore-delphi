# AgentCore Agent — Implementation Scope

Maps the design's architecture (§2) to the code in `src/delphi/`. This document covers the **request path** hosted on AgentCore Runtime: `delphi.agent`, `delphi.router`, `delphi.config`, `delphi.failure_log`, and the parts of `delphi.eval` and `delphi.train` the request path imports, as the package docstring in `src/delphi/__init__.py` lists. Offline data generation, scoring and router training (Phase 1/2) are `delphi.data` and the rest of `delphi.eval` and `delphi.train`, driven by `scripts/` on SageMaker/EC2 (design D5).

## Request pipeline

```
InvokeAgentRuntime(payload) ─▶ RouterAgent.handle_request        src/delphi/agent/app.py
                                 │
                                 ├─ 1. route:   Router.predict()  src/delphi/router/
                                 │      embed(query) ─▶ scores ─▶ Decision
                                 ├─ 2. generate: ConverseClient   src/delphi/agent/bedrock.py
                                 │      bedrock converse(chosen_model)
                                 ├─ 3. cost:    query_cost()      src/delphi/eval/cost.py
                                 └─ 4. emit:    DecisionRecord    src/delphi/agent/telemetry.py
```

## Module map

| Design element | Code | State |
|---|---|---|
| Router as in-process lib (D1) | `router/base.py` `Router`/`Decision` contract | ✅ interface + guards |
| Trivial baselines / oracle (T-CMP-2) | `router/baselines.py` | ✅ fully implemented |
| Learned router wrapper (D1) | `router/learned.py` `LearnedRouter` | ✅ adapter; scorer wired Phase 2 |
| Embedding strategy (D2) | `router/embedding.py` Local/Bedrock | ⏳ interface only; encoder chosen Phase 2 |
| Bedrock Converse (D3) | `agent/bedrock.py` | ✅ adapter; live client needs boto3 |
| Container entrypoint (current SDK) | `agent/entrypoint.py` `BedrockAgentCoreApp` | ✅ `@app.entrypoint` + `app.run()` |
| Deployed-runtime data plane | `agent/runtime_client.py` `invoke_agent_runtime` | ✅ adapter for the eval driver |
| CDK infra (L1 CfnRuntime) | `infra/` `DelphiRuntimeStack` | ✅ one stack per arm, ARM64 image |
| Pinned-arm bypass (D4) | `agent/app.py` `arm="pinned:<alias>"` | ✅ same container, router bypassed |
| Decision telemetry (D6) | `agent/telemetry.py` `DecisionRecord` | ✅ schema + sink; OTEL export Phase 3 |
| Cost ground truth (T-INF-2) | `eval/cost.py` | ✅ from token counts + pinned prices |
| Response matrix / oracle (T-STAT-1) | `eval/matrix.py` | ✅ fully implemented |
| Statistical tests (T-STAT-2/3/4) | `eval/stats.py` | ✅ bootstrap, Holm, non-inf, power |
| Contamination guard (T-STAT-6) | `eval/contamination.py` | ✅ exact + MinHash |

## Key implementation decisions

- **Arm parity is enforced structurally (T-INF-1).** The pinned comparator arms are the *same* `RouterAgent` class and the *same* `ConverseClient` with identical `GenerationParams`; only the chosen `model_id` differs. `DELPHI_ARM` env var selects the arm. There is no separate pinned-model codepath that could drift.
- **A router failure never escapes the routing step (T-RTR-3).** `RouterAgent._route` applies one fallback to any exception from any router, and to a malformed result (an alias outside the pool, a mismatched model id): it routes to the agent's fallback alias (the strongest candidate by price unless one is configured) with `is_fallback=True` and writes one `router_fallback` line. Routers do not recover from their own failures.
- **Pool-drift is impossible by construction (T-RTR-6).** `LearnedRouter.__init__` refuses to build if the artifact was trained on aliases absent from the live pool; `Router._validate` refuses to emit an unknown alias at predict time.
- **Telemetry is not optional.** Every `handle_request` emits exactly one `DecisionRecord` before returning; T-INF-3 asserts 100% coverage. Without it the statistical eval has no data.

## What is deliberately deferred (and to which phase)

- **Encoder choice (D2)** — Phase 2 resolves Local vs. Bedrock vs. no-embedding empirically against the 150ms P90 budget on the target CPU. `embedding.py` fixes only the interface.
- **Scorer** — the trained routing model (RouteLLM BERT / LLMRouter mlprouter/graphrouter/mfrouter) is a callable injected into `LearnedRouter`; Phase 2 produces it.
- **OTEL export** — `OTelSink` is stubbed; Phase 3 wires it to AgentCore Observability → CloudWatch → S3/Athena.
- **Live Bedrock + AgentCore entrypoint** — `BedrockConverseClient` and `_register_agentcore_entrypoint()` need `boto3`/`bedrock-agentcore` (the `agent` extra) and only run in the container; unit tests exercise `handle_request` with a fake client.

## Deployment shape (Phase 3)

**Tooling (current, not deprecated).** The deployment targets:
- **SDK:** `bedrock-agentcore` (`BedrockAgentCoreApp`, `@app.entrypoint`, `app.run()`) — implements the AgentCore HTTP protocol contract (`POST /invocations`, `GET /ping` on :8080). Entrypoint: `src/delphi/agent/entrypoint.py`.
- **IaC:** AWS CDK L1 `aws_cdk.aws_bedrockagentcore.CfnRuntime` (CloudFormation `AWS::BedrockAgentCore::Runtime`), in `infra/`. Confirmed present in aws-cdk-lib ≥ 2.261.0.
- **CLI (optional, for iteration):** the standalone AgentCore CLI `@aws/agentcore` (npm, Node 20+) — `agentcore create/dev/deploy/invoke/logs`. It drives CDK under the hood; our in-repo `infra/` stack is the explicit equivalent.
- **Data plane:** `boto3.client("bedrock-agentcore").invoke_agent_runtime(agentRuntimeArn=…, runtimeSessionId=…, payload=…)` — wrapped in `src/delphi/agent/runtime_client.py` for the eval driver.

Explicitly **NOT** used: the older `bedrock-agentcore-starter-toolkit` Python deploy helper, and classic Bedrock Agents (`AWS::Bedrock::Agent` / `bedrock-agent-runtime` `InvokeAgent`).

**Arms.** One ARM64 container image; `infra/app.py` instantiates one `CfnRuntime` per arm, differentiated only by the `DELPHI_ARM` env var (design D4):
- `router` — the learned router (default),
- `pinned:<alias>` — a pinned single-model comparator,
- `ipr` — Bedrock Intelligent Prompt Routing comparator (T-CMP-1).

Router artifact + encoder pulled from `DELPHI_ARTIFACT_URI` (S3) at cold start, or baked into the image for immutability/audit (D1). Cold-start budget: artifact load < 3s, total cold invoke < 10s (T-RTR-5). Build type is `Container`/ARM64 (not `CodeZip`) precisely because D1 wants an immutable, auditable artifact.
