# Phase 0 Exit Checklist

Phase 0 exit (design §4): *dataset manifests hashed in S3; tests exist and fail.* Tracking against that below.

## Done in this workspace

- [x] Project structure, packaging (`pyproject.toml`), venv, CI-ready test layout.
- [x] Candidate pool + **real** pinned price snapshot (`config/models.yaml`, Price List API 2026-07-07).
- [x] Workload definition (`config/workload.yaml`) — benchmark mix + domain set + splits.
- [x] All §3 tests written. The math/behaviour layer runs for real, and the `xfail` markers are the later-phase data gates (correct "exist and fail" state).
- [x] Phase 1 token budget grounded in real prices → **$499.95 hard cap** (`docs/phase1_budget.md`).
- [x] Power analysis done → test split needs ~442 queries/comparison for a 2-pt effect at SD=15.
- [x] Agent implementation scoped and skeletoned (`docs/agent_scoping.md`).

## Still required to actually exit Phase 0 (needs AWS creds / decisions)

- [ ] **Confirm the two unverified Anthropic prices** (Haiku 4.5 / Sonnet 4.6) — absent from the Price List API `model` enum on 2026-07-14; currently placeholders in `models.yaml`.
- [ ] **Freeze the domain-representative dataset** — `workload.yaml` `domain_set.source` is `TBD_traffic_sample`. This is the single biggest external-validity lever (Threat #1); benchmark-only results routinely fail to transfer.
- [ ] **Raise Bedrock quotas** for the eval QPS (T-INF-5) before Phase 1/4.
- [ ] **Hash the dataset manifests into S3** and record the hashes (T-INF-4 replayability).
- [ ] **Confirm region** (ap-southeast-2 vs us-east-1) — pricing snapshot is us-east-1; re-pull if the eval region differs.
- [ ] **Validate the judge** (gpt-5.4) correlates with ground-truth on benchmarks that have it, before trusting its open-ended scores (T-STAT-5).

## Decisions captured

- **Judge = `openai.gpt-5.4`** (design left this open; my review flagged it must be chosen now). It is outside every routed family — Anthropic is in the pool so Claude can't judge; Nova/Llama/Mistral/DeepSeek are all in the pool too. GPT-5.4 and Grok-4.3 are the clean external options on Bedrock; GPT-5.4 chosen as primary.
- **Sample sizing = pool the test set across benchmarks for the headline H2 verdict** (option 2 in the budget doc); per-benchmark verdicts reported as directional. Avoids pushing Phase 1 into the $667 "thorough" tier just to power per-benchmark tests.
- **Deployment tooling = current AgentCore stack** (`bedrock-agentcore` SDK + AWS CDK L1 `CfnRuntime` + `@aws/agentcore` CLI + `invoke_agent_runtime` data plane). Explicitly not the deprecated `bedrock-agentcore-starter-toolkit` or classic Bedrock Agents. Verified against AWS docs 2026-07-14; see `docs/deployment.md`. `CfnRuntime` confirmed in aws-cdk-lib ≥ 2.261.0.
