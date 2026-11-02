# Smart Routing on Amazon Bedrock AgentCore
## Goal / TDD / Delivery Plan — testing whether a learned router outperforms single-model backends

**Status:** Draft v0.1 · **Date:** 2026-07-14

This is the source-of-truth design. Implementation scope: [`agent_scoping.md`](agent_scoping.md). Budget/power: [`phase1_budget.md`](phase1_budget.md). Phase 0 status: [`phase0_checklist.md`](phase0_checklist.md).

---

## 1. Goal

Build and evaluate a **learned, cross-model query router** deployed on **AgentCore Runtime** that, per request, predicts which candidate foundation model will produce the best response and routes accordingly — then **rigorously test** whether this router outperforms any single-model backend on a quality/cost/latency frontier.

This deliberately fills a gap: no public reference architecture combines a learned quality-predicting router (RouteLLM/LLMRouter lineage) with AgentCore's serverless agent hosting. Bedrock's native Intelligent Prompt Routing is same-family only (e.g., Haiku↔Sonnet); this design routes **across families and providers** — the "fusion" claim.

### 1.1 Falsifiable hypotheses

| ID | Hypothesis | Falsified when |
|----|-----------|----------------|
| **H1 — Cost dominance** | Router quality **non-inferior** (margin δ) to best single model, at **≥30% lower cost/query**. | Non-inferiority fails at δ=2, OR cost saving < 30%. |
| **H2 — Quality dominance** | Router quality **superior to *every* single model**. Needs **non-nested error sets**. | Paired test vs strongest model: Δ ≤ 0, or CI includes 0. |
| **H3 — Learnability** | Router captures **≥60% of the oracle gap**: (router−random)/(oracle−random) ≥ 0.6. | Capture ratio < 0.6 on held-out test. |

**Critical structural fact:** H2 is bounded above by the oracle router (per-query argmax). If the oracle barely beats the strongest single model, H2 is dead regardless of router quality. **Oracle analysis is a gate before any router training (Phase 1).**

### 1.2 Non-goals

- Not a production gateway (LiteLLM/Portkey exist). Not mid-conversation routing (single-turn first). Not competing with Bedrock IPR on same-family turf — IPR is a comparator baseline.

---

## 2. Architecture

See `agent_scoping.md` for the module map and `deployment.md` for tooling. Components: AgentCore Runtime hosts the router agent (embed → predict → converse → telemetry) via the current `bedrock-agentcore` SDK, deployed with AWS CDK L1 `CfnRuntime` (not the deprecated starter-toolkit, not classic Bedrock Agents); candidates behind Bedrock Converse; offline S3 + SageMaker/EC2 for matrix generation + training; Athena/QuickSight for analytics; pinned-model + Bedrock-IPR comparator arms.

Key decisions: **D1** router as in-process library (not a service); **D2** embedding is the latency bottleneck, choose deliberately (a target of <150ms P90 overhead); **D3** all candidates behind Bedrock Converse where possible; **D4** baseline arms are the identical container with routing bypassed; **D5** offline training stays off AgentCore; **D6** decision telemetry is first-class.

### 2.3 Candidate pool

Heterogeneous across families to maximise non-nested error sets. Price spread should exceed 10× (confirmed: pool spread is 60× on the current snapshot). See `config/models.yaml`.

---

## 3. Test-Driven Development plan

Full test specifications (T-STAT-1..6, T-RTR-1..6, T-INF-1..5, T-CMP-1..2) are implemented in `tests/` and summarised in `agent_scoping.md`. The statistical tests are the heart: complementarity gate, H2 superiority (paired bootstrap + Holm-Bonferroni), H1 non-inferiority + cost, H3 capture ratio, judge-bias dual-scoring, contamination guard.

---

## 4. Delivery plan (phased, with kill criteria)

- **Phase 0** — workload + pool definition. Exit: manifests hashed in S3; tests exist and fail. (See `phase0_checklist.md`.)
- **Phase 1** — response matrix + oracle gate. **Kill/pivot on T-STAT-1.** Budget: $333.30 planned, $499.95 cap (`phase1_budget.md`).
- **Phase 2** — router training + offline selection (≥3 router families; select on validation by capture ratio subject to latency budget).
- **Phase 3** — AgentCore deployment (agent + pinned arms + IPR comparator + OTEL telemetry).
- **Phase 4** — evaluation run + verdict (all arms; each hypothesis gets corroborated/falsified/underpowered with CIs).
- **Phase 5 (stretch)** — multi-turn routing, online learning loop, shadow-mode.

---

## 5. Threats to validity

1. **Benchmark overfitting** — domain-representative set is the defence; report separately.
2. **Judge circularity** — dual scoring mandatory; judge outside the pool.
3. **Oracle optimism** — single-sample generations; optionally re-sample top-2 on a subset.
4. **Price non-stationarity** — cost pinned to snapshot; report tokens-by-model to re-price.
5. **Same-family confound in IPR comparison** — say so explicitly.
6. **Selection-inference leakage** — router family selected on validation, verdicts on test; never iterate on test.

---

## 6. What success and failure both look like

- **Success (H2):** router CI-above every single model, ≥60% capture, ≤150ms overhead (the D2 target) — novel fusion result on AgentCore.
- **Success (H1):** quality parity within δ at ≥30% saving — cross-family RouteLLM-style result, productionised.
- **Honest failure:** nested error sets on this workload — a valuable contrarian finding (routing-for-quality overclaimed for this model generation; only routing-for-cost survives).

All three produce a defensible artefact. The design's job is to make the verdict trustworthy, not to guarantee the router wins.

---

## 7. Related work / prior art

Two open-source routing projects are the intellectual lineage for Delphi's router families. **We use neither project's code** — our router families (`linear`, `prototype`, `knn` in `train/routers.py`) are original pure-numpy implementations conceptually in their lineage. The taxonomies and APIs below were **verified from source on 2026-07-15** (identifiers quoted exactly); both move quickly, so re-pull before citing in a dated artefact.

### 7.1 RouteLLM — `lm-sys/RouteLLM` (LMSYS)

- **License:** Apache-2.0.
- **Router taxonomy (exactly 5):** `mf` (matrix factorization — the recommended router), `sw_ranking` (similarity-weighted / Elo ranking), `bert` (BERT classifier), `causal_llm` (LLM-based classifier), `random`.
- **Core model — binary strong-vs-weak routing.** `Controller(routers=[...], strong_model=..., weak_model=...)` from `routellm.controller`, invoked through an **OpenAI-compatible** `client.chat.completions.create(model="router-<name>-<threshold>", ...)`. Model access via **LiteLLM**.
- **Extension point:** subclass `Router` and implement `calculate_strong_win_rate(prompt) -> float`.
- **Training signal:** **preference / pairwise** data — Chatbot Arena (`lmsys/lmsys-arena-human-preference-55k`), augmented with a GPT-4 judge.
- **CLI/server:** `python -m routellm.openai_server`, `routellm.calibrate_threshold`, `routellm.evals.evaluate`; benchmarks `mmlu`, `gsm8k`, `mt-bench`.
- Repo: <https://github.com/lm-sys/RouteLLM>

**Why not adopted.** RouteLLM is structurally **binary** (route between one strong and one weak model, threshold-calibrated) and learns from **pairwise preferences**. Delphi routes over an **N-candidate pool** and builds a **dense response matrix** `M[query][candidate]` as the Phase 1 oracle-gate step, which turns routing into per-candidate quality regression + argmax — a richer signal that RouteLLM's two-model preference frame does not fit. Adopting it would mean importing a framework to discard its core assumption.

### 7.2 LLMRouter — `ulab-uiuc/LLMRouter` (ULab @ UIUC)

- **License:** MIT. Installable as `llmrouter-lib`. Docs: <https://ulab-uiuc.github.io/LLMRouter/>
- **Router taxonomy — 16+ routers across 5 categories:**
  - *Single-round:* `knnrouter`, `svmrouter`, `mlprouter`, `mfrouter`, `elorouter`, `routerdc`, `automix`, `hybrid_llm`, `graphrouter`, `causallm_router`, plus trivial baselines `smallest_llm` / `largest_llm`.
  - *Multi-round:* `router_r1`. *Multimodal:* `tsrouter`. *Personalized:* `gmtrouter`, `personalizedrouter`. *Agentic:* `knnmultiroundrouter`, `llmmultiroundrouter`.
- **Extension point:** subclass `MetaRouter` (`llmrouter.models.meta_router`) and implement `route_single(query_input: dict) -> dict` (+ `route_batch`).
- **Embeddings:** `get_longformer_embedding(...)` from `llmrouter.utils` — the default is Longformer (informs design D2's "embedding is the latency bottleneck").
- **Data pipeline:** generation from 11 benchmark datasets (`data/` includes `chatbot_arena`, `human_eval`, `mbpp`, `mt_bench`, `charades_ego`, `tsrbench`, …).
- **CLI/UI:** `llmrouter train|infer|chat` (also `python -m llmrouter.cli.router_train|router_inference|router_chat`); Gradio chat UI; a **ComfyUI** visual pipeline.
- Repo: <https://github.com/ulab-uiuc/LLMRouter>

**Relationship to Delphi.** Our `linear` / `prototype` / `knn` families are close analogues of LLMRouter's `mlprouter` / `mfrouter` / `knnrouter`. LLMRouter's `route_single(query_input) -> dict` contract maps cleanly onto our `TrainedRouter` interface, so a real `mfrouter`/`graphrouter` could be dropped in later as an **additional family** competing on capture ratio — see §7.3 for why that dependency stays offline.

### 7.3 Why heavier routers stay offline: "torch in the `train` extra, never the serving image"

This is a packaging boundary, not a modelling choice. Delphi has two distinct Python environments:

| Environment | What it is | Where it runs | Dependencies |
|---|---|---|---|
| **Serving image** | The ARM64 container in ECR (`Dockerfile`) the AgentCore Runtime executes on the request path | AgentCore microVM, per request | `bedrock-agentcore`, `boto3`, `numpy`, `pyyaml` — the `agent` extra. **No ML training libs.** |
| **`train` extra** | Offline training deps declared in `pyproject.toml` (`[project.optional-dependencies].train`) | SageMaker / EC2 batch, or a laptop — never AgentCore (design D5) | may include `torch`, `scikit-learn`, etc. |

The rule "torch in the `train` extra only, never the serving image" means: if we add a heavier learned router (a real RouteLLM `bert` classifier, or an LLMRouter `graphrouter` needing PyTorch), the PyTorch dependency is installed **only** in the offline training environment where the router is *fit*. Training produces a small, framework-independent **artifact** (weights + manifest) written to S3. The serving container then loads that artifact and runs **inference only** — it never imports torch.

Why this matters:

- **Cold-start latency & size (D1/D2).** The routing decision runs under a 150 ms P90 budget in a microVM that cold-starts per session. A multi-hundred-MB torch install in the image inflates image pull and import time for zero request-path benefit. Our numpy-only serving stack keeps the container small, and routing microsecond-scale was a design expectation, not measured on the target architecture when this section was written.
- **Routing overhead, measured since (D2).** One capture on the deployed router arm (2026-10-20, `evidence/nfr1/manifest.json`): the warm, in-process routing window, from receipt in the handler to just before the Converse call, on the router arm in us-west-2 has a nearest-rank P90 of **0.3 ms** over 200 warm invocations of the demo workload, against the 150 ms P90 budget. It does not measure end-to-end latency, Converse time or cold start. The five demo prompts are short (9 to 58 input tokens), so routing cost for longer prompts is unmeasured, and the comparison with the 150 ms budget is a window-only check, not a headroom claim for real traffic.
- **Separation of concerns (D1, D5).** Training is batch compute; serving is latency-sensitive inference. Coupling them in one image means every retrain-driven dependency bump forces a serving-image rebuild, and vice versa.
- **Immutable, swappable artifacts (D1).** Because the artifact is just arrays + a manifest, you can retrain offline and swap the S3 artifact **without rebuilding or redeploying the container**. A torch-based router would still obey this: its *trained output* must serialize to something the numpy/torch-free — or at most torch-inference-only — serving path can load. The heavy training framework never crosses into the request path.

Concretely in the repo: `pyproject.toml` keeps `train` deps optional and separate from the `agent` extra; `requirements-agent.txt` (what the `Dockerfile` installs) lists only the four serving dependencies. Adding a torch router touches `train/routers.py` and the `train` extra — not `requirements-agent.txt`.
