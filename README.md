# Delphi — Smart Routing on Amazon Bedrock AgentCore

A learned, cross-model query router deployed on AgentCore Runtime that predicts, per request, which candidate foundation model will produce the best response — and a rigorous test harness for whether it beats any single-model backend on the quality/cost/latency frontier.

Amazon Bedrock already ships a native router,
[Intelligent Prompt Routing](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-routing.html),
which routes each request between foundation models within the same model family, so its choice
is a same-family one. Delphi routes across model families: its candidate pool is cross-family, and
the router learns which family's model to call for each request. The reasoning behind that
choice is in [`docs/design.md`](docs/design.md).

A deployed Delphi arm proves the mechanism (route, then Bedrock Converse, then a decision
record), not routing quality. The router is synthetic-trained today, so nothing in this
repository is a verdict on whether routing pays off.

Full goal / TDD / delivery plan: [`docs/design.md`](docs/design.md).

## Status

**Phases 0–2 built and exercised offline** against a synthetic dataset (no AWS required):
- **Phase 0** — scaffolding, statistical core, agent skeleton, grounded Phase 1 budget.
- **Phase 1** — response-matrix pipeline + oracle gate; runs end to end on the synthetic "Delphi Trials".
- **Phase 2** — router training (linear/prototype/knn), validation selection, artifact round-trip into the request path.

**Phase 3** — deployment is built: the CDK app defines one stack per arm in `ARMS`, and two arms
deploy to AgentCore Runtime, each by name: `Delphi-router` and `Delphi-pinned-nova-lite`. The other
pinned arms are not part of this release (the unit that would have added them was dropped), and
the `ipr` arm is not implemented; the app defines no stack for any of them. `delphi-verify` checks a deployed arm end to end (see [Verify a deployment](#verify-a-deployment)).
That proves the mechanism, not routing quality, because the deployed router is synthetic-trained.

Real-data runs (Phase 1 proper, Phase 4 verdict) are gated on the domain dataset + Bedrock creds; the code swaps the synthetic answer source for Bedrock Converse with the rest of the pipeline unchanged. Later-phase data-gated tests are wired and marked `xfail`.

## Layout

```
config/            models.yaml (pinned price snapshot + candidate pool), workload.yaml
src/delphi/
  config.py        candidate pool + pricing model (single source of cost truth)
  router/          Router contract, trivial baselines + oracle, learned-router wrapper, embedders
  agent/           app.py (core), entrypoint.py (BedrockAgentCoreApp), bedrock.py (Converse),
                   runtime_client.py (invoke_agent_runtime), telemetry.py (decision records)
  data/            query schema, "Delphi Trials" synthetic generator + council, split
  eval/            response matrix, statistical tests, cost, contamination, scoring, gate, build_matrix
  train/           router families (linear/prototype/knn), training+selection harness, artifact store
infra/             CDK app — L1 aws_bedrockagentcore.CfnRuntime, one stack per arm
Dockerfile         ARM64 container image (runs the SDK entrypoint on :8080)
tests/             T-STAT / T-RTR / T-INF / T-CMP suites (design §3)
scripts/           estimate_phase1_budget.py (grounded token-budget model)
docs/              design.md, agent_scoping.md, deployment.md, phase0_checklist.md,
                   phase1_budget.md, phase1_pipeline.md, phase2_training.md
```

## Cost basis

Read this before you deploy. Every cost Delphi reports can be re-priced: each `DecisionRecord`
(`src/delphi/agent/telemetry.py`) carries `chosen_model_id`, `input_tokens` and `output_tokens`,
and `tokens_by_model` in `delphi.eval.cost` (`src/delphi/eval/cost.py`) totals the tokens per
model, so you can apply your own prices to the same token counts.

Model prices come from pinned snapshots, one per pool file, and each snapshot names the region
it was pulled for:

- `config/models_realpool.yaml` prices the router arm; its snapshot region is us-west-2, pulled
  2026-07-15.
- `config/models.yaml` prices the pinned arms and `scripts/estimate_phase1_budget.py`; its
  snapshot region is us-east-1, pulled 2026-07-14, which differs from the author's deployment
  region, us-west-2.
- `config/models_synthetic.yaml` prices no deployed arm: only `scripts/run_phase1_synthetic.py`
  and `scripts/run_phase2_synthetic.py` read it, and its region, mount-parnassus-1, is fictional
  (pulled 2026-07-14). Those synthetic runs illustrate the method and are not evidence about
  model quality.

Nothing in the code compares a snapshot's region with the region an arm is deployed to. The
region you deploy to is whatever your AWS environment resolves at deploy time, and the stack
passes it to each arm as `AWS_REGION`. If it is not a snapshot's region, re-price from current
AWS pricing for your own region before you rely on any cost.

A deployment accrues cost in five places: model inference through Bedrock Converse;
AgentCore Runtime for each deployed arm; ECR for the container image; CloudWatch and X-Ray for
logs, metrics and traces; and S3 for the router artifact, whose bucket the stacks provision in
create mode (a separate stack that retains it) or reference by name in existing mode, where it
already exists and is never managed. Only model inference is priced
by a pinned snapshot in this repository; price the other categories from current AWS pricing for
your region. This section states no amount on purpose.

These are running costs of the mechanism, not a measure of routing quality: the router is
synthetic-trained, so the bill tells you what the mechanism costs, not whether routing pays off.

## Deployment tooling

Targets the **current** AgentCore stack, deliberately not the deprecated paths:

- **SDK** `bedrock-agentcore` (`BedrockAgentCoreApp`/`@app.entrypoint`) — *not* `bedrock-agentcore-starter-toolkit`.
- **IaC** AWS CDK L1 `aws_bedrockagentcore.CfnRuntime` (`AWS::BedrockAgentCore::Runtime`), aws-cdk-lib ≥ 2.261.0.
- **CLI** `@aws/agentcore` (npm) for scaffold/dev/deploy iteration; drives CDK under the hood.
- **Data plane** `bedrock-agentcore` `invoke_agent_runtime` — *not* classic Bedrock Agents `InvokeAgent`.

The serving image is pinned: its base image by digest in the `Dockerfile`, and its four direct
Python dependencies to exact releases in `requirements-agent.txt`. Their own transitive
dependencies are not locked, so two builds months apart can still resolve different versions of
those. To take upstream fixes, bump the pins deliberately and re-run `pip-audit`. The image runs
as a non-root user.

See [`docs/deployment.md`](docs/deployment.md).

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest                       # xfails are the later-phase deferral register
PATH="$PWD/.venv/bin:$PATH" bash scripts/ci.sh   # the whole gate: ruff, then suite + floor
.venv/bin/python scripts/estimate_phase1_budget.py

# Phase 1 offline dry-run over the synthetic "Delphi Trials" — no AWS needed:
.venv/bin/python scripts/run_phase1_synthetic.py                # gate PASSES (gap 27.55)
.venv/bin/python scripts/run_phase1_synthetic.py --mode nested  # gate also PASSES (gap 3.53)
.venv/bin/python scripts/run_phase1_synthetic.py --mode nested --n 40 --seed 8  # gate FAILS (gap 0.00)

# Phase 2: train router families, select on validation, prove artifact round-trip:
.venv/bin/python scripts/run_phase2_synthetic.py                # H3 capture 0.779 on test
```

Each figure in these comments is what the command prints at its defaults, and a test
(`tests/test_evidence_figures.py`) runs the commands and fails if a figure here stops matching.
Nested mode is unstable across `--n` and `--seed`: at the defaults it still passes the gate, and
the third command is the failing case the design's kill/pivot gate exists for.

The synthetic run exercises the entire Phase 1 pipeline end to end (generate → split →
contamination check → response matrix with real ground-truth scoring → oracle gate →
artifacts) against a fictional council of six advisors. When the real dataset + Bedrock
creds arrive, only the answer source changes (`SyntheticCouncil` → Converse). See
[`docs/phase1_pipeline.md`](docs/phase1_pipeline.md).

These synthetic runs illustrate the method on a fictional pool; they are not evidence about
model quality.

## Run the method offline

You can run the whole method on your own machine before you spend anything: it needs no AWS
account, no credentials and no network. Two scripts replay Phase 1 and Phase 2 over the
synthetic "Delphi Trials", a fictional council of six advisors:

```bash
.venv/bin/python scripts/run_phase1_synthetic.py   # generate, split, score, oracle gate
.venv/bin/python scripts/run_phase2_synthetic.py   # train, select, artifact round-trip
```

Each writes only below the directory you run it from (`data/phase1_synthetic/` and
`artifacts/phase2_synthetic/`). The pipeline is deterministic by hashing, so a repeat run gives
the same verdict. These runs illustrate the method on a synthetic pool; they are not evidence
about model quality. The figures they print, and the command that makes the nested pool fail
the gate, are under [Develop](#develop).

## Verify a deployment

After you deploy an arm (see [`docs/deployment.md`](docs/deployment.md)), check it end to end
with one command:

```bash
delphi-verify --arn <your-runtime-arn>
```

It invokes that one runtime and prints the routing decision plus the decision record for the
invocation. The ARN is required, as `--arn` or the `DELPHI_RUNTIME_ARN` environment variable,
and has no default. The region is read from the ARN, and credentials come from your standard
AWS credential chain. Inbound auth is IAM SigV4, so those credentials need
`bedrock-agentcore:InvokeAgentRuntime` on that ARN. Add `--prompt` to send your own prompt, or
`--json` to print the decision record alone. Each run is one real, billed invocation (see
[Cost basis](#cost-basis)).

A passing check proves the mechanism (route, then Bedrock Converse, then a decision record),
not routing quality: the deployed router is synthetic-trained.

## The three hypotheses (design §1.1)

- **H1 — cost dominance:** quality non-inferior (δ=2pt) to best single model at ≥30% lower cost.
- **H2 — quality dominance:** quality superior to *every* single model (needs non-nested error sets).
- **H3 — learnability:** router captures ≥60% of the oracle gap.

**H2 is gated by the oracle analysis (T-STAT-1) before any router is trained** — if the pool's error sets are nested, H2 is unreachable and no ML fixes it. That gate runs in Phase 1.

The H1/H2/H3 verdict is pending: it is gated on a Phase 4 run over a real domain dataset, which
is out of scope for this release, so the thresholds above are pre-registered design targets,
not results.

## What runs where

- **On AgentCore Runtime:** the request path, which is `src/delphi/agent`, `src/delphi/router`,
  `src/delphi/config.py`, `src/delphi/failure_log.py`, and parts of `src/delphi/eval` and
  `src/delphi/train` (partial: only the modules the request path imports, as the package
  docstring lists). A test compares this list with the code.
- **On the reader's machine:** the verification client (`src/delphi/verify`), which invokes
  an AgentCore runtime; the container never imports it.
- **Offline (SageMaker/EC2):** dataset generation, response-matrix builds, scoring and router
  training (`src/delphi/data` and the rest of `src/delphi/eval` and `src/delphi/train`, driven by
  `scripts/`, Phase 1/2).
- **Analytics:** invocation telemetry → CloudWatch → S3/Athena (Phase 3+).

## Executing generated code

The `pass_at_1` scorer (`src/delphi/eval/scoring.py`) checks a model's code answer by running
it. It is **off by default**: it refuses to execute model output unless you opt in, with
`allow_exec=True` or `DELPHI_ALLOW_UNSANDBOXED_EXEC=1`, and `scripts/ci.sh` always unsets that
variable. When enabled, the code runs in a child process (`src/delphi/eval/runner.py`,
`src/delphi/eval/cell_harness.py`) with an empty environment, a restricted set of builtins and a
per-cell timeout. **That child process is not a sandbox.** Restricted builtins can be escaped,
and the child runs with your user's file system and network access. Do not enable it on
untrusted model output outside a disposable, isolated environment, such as a microVM or a
seccomp-confined container with no credentials and no network. The offline synthetic demos do not
need it.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
