# Deployment — AgentCore Runtime (current tooling)

This project targets the **current** Amazon Bedrock AgentCore stack. It deliberately avoids the deprecated/legacy paths:

| Concern | We use | We do NOT use |
|---|---|---|
| Agent SDK | `bedrock-agentcore` (`BedrockAgentCoreApp`, `@app.entrypoint`, `app.run()`) | `bedrock-agentcore-starter-toolkit` (older Python deploy helper) |
| IaC | AWS CDK L1 `aws_cdk.aws_bedrockagentcore.CfnRuntime` (`AWS::BedrockAgentCore::Runtime`) | classic `AWS::Bedrock::Agent` |
| CLI | `@aws/agentcore` (npm, Node 20+) — `create/dev/deploy/invoke/logs` | `agentcore configure/launch` from the starter toolkit |
| Data plane | `bedrock-agentcore` `invoke_agent_runtime` | `bedrock-agent-runtime` `InvokeAgent` |

Confirmed against AWS docs on 2026-07-14: the AgentCore CLI is distributed as `@aws/agentcore`, `CfnRuntime` ships in aws-cdk-lib ≥ 2.261.0, and `invoke_agent_runtime` is the `bedrock-agentcore` service data-plane call.

`infra/` is a standalone CDK app. It builds the agent into an **ARM64** container (AgentCore
Runtime requires arm64) via `DockerImageAsset` and registers one `CfnRuntime` per arm,
differentiated only by the `DELPHI_ARM` env var (design D4). A deployed arm proves the mechanism
(route, then Bedrock Converse, then a decision record), not routing quality: the router is
synthetic-trained.

**Published sample.** The author's own deploy records (`evidence/deploy/`) and AI-DLC design
record (`aidlc/`) are not shipped. Where this runbook names a record path such as
`evidence/deploy/<step>-<stack>.json`, that is where your own run writes its record; the author's
observed results are quoted inline. The NFR1 latency sample (`evidence/nfr1/`) is shipped.

There are two paths through this document. A **reader** in a clean account follows
[Reader path (create mode)](#reader-path-create-mode) and, when finished,
[Teardown](#teardown-a-readers-dedicated-account-only). The **author's account**, which already
holds the artifact bucket and two live arms, is changed only by the gated, human-run
[Migration runbook](#migration-runbook). No pipeline deploys anything: CI holds no AWS credential.

## Arms

The app defines **2** arms, in `ARMS` in `infra/delphi_infra/arms.py`, and each is deployed by
its stack name, one stack per command:

- `router` — stack `Delphi-router`
- `pinned:nova-lite` — stack `Delphi-pinned-nova-lite`

These are the two arms known to serve. Never deploy every stack at once.

### Not yet available (U8)

These arms were defined before and are not in `ARMS`, so the app synthesizes no stack for them.
Unit U8, which was to restore each once its model access was confirmed (dependency D1), was
dropped on 2026-10-20 through its designed fallback, so none of them is deployed in this
release and the published count stays two. They are not counted above.

- `pinned:nova-pro`
- `pinned:llama-3-3-70b`
- `pinned:mistral-large`
- `pinned:deepseek-v3-1`
- `pinned:claude-haiku-4-5`
- `pinned:claude-sonnet-4-6`
- `ipr` (also not implemented: it fails at container start)

## Prerequisites

- **CDK CLI.** `aws-cdk-lib` is 2.261.0 or later (the venv records the exact version). The CLI
  used in the author's deploys is `npx --yes aws-cdk@2.1145.0`, observed working on
  2026-10-19; an older CLI may refuse the cloud assembly that library writes.
- **The app runs from `infra/.venv`.** Create it with
  `python3 -m venv infra/.venv && infra/.venv/bin/python -m pip install -r infra/requirements.txt`.
  `infra/cdk.json`'s app line is `.venv/bin/python3 app.py`, so the CLI uses that interpreter;
  pass `--app ".venv/bin/python3 app.py"` explicitly if you run the CLI some other way.
- **The account is whatever your credentials resolve to.** The stacks are environment-agnostic
  (no fixed account or region), so the credential chain decides where a deploy lands. Read the
  diff header before every deploy: it must name `aws://<your account>/<region>`. A diff against
  the wrong account shows every resource as new.
- **Region.** The region you deploy to is the one your environment resolves (`AWS_REGION`, or
  `--profile`'s region); the stack passes it to each arm as `AWS_REGION`. The prices in
  `config/` come from a pinned snapshot region per pool file (see README's Cost basis): if your
  region differs from a snapshot region, re-price before relying on any cost.
- **The bucket mode is required.** Every synth, diff and deploy takes
  `-c artifactBucket=existing` or `-c artifactBucket=create`; there is no default
  ([Artifact bucket](#artifact-bucket)).
- **Model access** for every model id each arm may call: [Model access](#model-access).
- **Docker**, running, for any step that builds the image.

## Model access

Each arm may invoke only its own candidates. Your account needs Bedrock model access for every id
below, in every region the inference profile routes to; an account without it deploys fine and
then fails on its first invocation (assumption A6: a reader's account may lack this access, in
which case the walkthrough proves the pattern only for equivalent access). The ids are derived
from the same `ArmSpec` the stack uses, and the destination regions are those the stack grants,
from `infra/profile_routing.json` (documented for source region `us-west-2`; if you deploy from
another region, run [PROFILE-ROUTING-CONFIRM](#profile-routing-confirm) there first and correct
the file).

| Arm | Model ids | Access needed | Destination regions |
|---|---|---|---|
| `router` | `us.amazon.nova-lite-v1:0`, `us.amazon.nova-pro-v1:0`, `us.meta.llama3-3-70b-instruct-v1:0` | Bedrock model access for each id, in each destination region of its profile | `us-east-1`, `us-east-2`, `us-west-2` |
| `pinned:nova-lite` | `us.amazon.nova-lite-v1:0` | Bedrock model access for the id, in each destination region of its profile | `us-east-1`, `us-east-2`, `us-west-2` |

## Artifact bucket

The router arm reads its trained artifact from one S3 bucket, named by
`config/artifact-location.json` (the only declaration of the name). How the stacks relate to it is
the required synth context `artifactBucket`.

### Existing mode

`-c artifactBucket=existing`: the bucket already exists in this account (the author's account).
The stacks reference it by name and never manage it: no bucket resource is synthesized, so no
deploy, rollback or teardown of an arm stack can modify it, and a diff never shows a bucket line.

What this mode does **not** give you: the live bucket was created outside this repository, so
its public access block, encryption, TLS enforcement, object ownership and versioning are
not declared by the repository at all. Inspecting them is a read-only human step; changing any of
them is a separate, explicitly confirmed change, outside these steps.

### Create mode

`-c artifactBucket=create`: a clean account. A separate `Delphi-artifacts` stack provisions the
bucket with its removal policy stated in code as RETAIN (destroying the stack keeps the bucket
and its data), public access fully blocked, SSE-S3 encryption, TLS enforced by bucket policy,
BucketOwnerEnforced object ownership, and versioning on. The arm stacks are identical in both
modes: they name the bucket and take no reference from `Delphi-artifacts`, so deploy it first.

## Reader path (create mode)

For a reader's clean, dedicated account. Every resource it creates appears in a step, and
[Teardown](#teardown-a-readers-dedicated-account-only) removes them. This path is valid only once
`config/artifact-location.json` has `serving_form: pointer`: before that the router reads the
legacy prefix, which no tool in this repository writes.

1. Prerequisites: everything in [Prerequisites](#prerequisites), with credentials for your own
   account and `<region>` exported as `AWS_REGION`.
2. Bootstrap the account and region once with `cdk bootstrap`, from the repository root:
   ```bash
   npx --yes aws-cdk@2.1145.0 bootstrap aws://<account>/<region>
   ```
3. Check, read-only, that the bucket name is free. A success or a 403 means the name is already
   in use: stop, and use existing mode instead. Only a 404 means proceed (documented behaviour,
   unverified here):
   ```bash
   aws s3api head-bucket --bucket "delphi-artifacts-<account>-<region>"
   ```
4. Create the artifact bucket stack:
   ```bash
   cd infra && npx --yes aws-cdk@2.1145.0 deploy Delphi-artifacts \
     --app ".venv/bin/python3 app.py" -c artifactBucket=create
   ```
5. Build the router artifact for the router's own pool:
   ```bash
   .venv/bin/delphi-artifact build --pool config/models_realpool.yaml
   ```
6. Plan the publication (a dry run; nothing is written), and read the plan:
   ```bash
   .venv/bin/delphi-artifact publish <artifact dir> --pool config/models_realpool.yaml
   ```
7. Publish it with `--execute`:
   ```bash
   .venv/bin/delphi-artifact publish <artifact dir> --pool config/models_realpool.yaml --execute
   ```
8. Check the published pointer and triple against the router's pool with verify-remote; it must
   exit 0:
   ```bash
   .venv/bin/delphi-artifact verify-remote --pool config/models_realpool.yaml
   ```
9. Deploy each arm by name:
   ```bash
   cd infra && npx --yes aws-cdk@2.1145.0 deploy Delphi-router \
     --app ".venv/bin/python3 app.py" -c artifactBucket=create
   cd infra && npx --yes aws-cdk@2.1145.0 deploy Delphi-pinned-nova-lite \
     --app ".venv/bin/python3 app.py" -c artifactBucket=create
   ```
10. Verify each arm with delphi-verify, using the `AgentRuntimeArn` output of its stack; each run
    is one billed invocation:
    ```bash
    .venv/bin/delphi-verify --arn "$ROUTER_ARN"
    .venv/bin/delphi-verify --arn "$PINNED_ARN"
    ```
11. Record the walkthrough ([Walkthrough record](#walkthrough-record)), then tear down.

In your own account the approval receipts of the migration runbook are not required; they bind
the author's production deploys.

## Migration runbook

The author's account, existing mode, one named stack per deploy. **Every step below is run by a
person.** An agent designs the steps and reads what the person relays; it runs no live command,
read-only included, and writes no approval. The commands are the author's; `<region>`,
`<profile>` and the other angle-bracket values are the operator's. `$PINNED_ARN` and `$ROUTER_ARN`
are shell variables filled from stack outputs and never written into a record.

U6 lands in three commits, each deployed before the next, so every deploy carries one class of
change: U6a (the image, outputs, override and arm set; grants unchanged; steps G0–S5), U6b
(grants and cdk-nag; steps S6–S9), U6c (the pointer flip; steps S10–S12). U6b touches no path
inside the image build context, so its deploys change no image.
No command in this runbook removes a stack or touches the bucket's contents or configuration;
narrowing `ARMS` removes nothing (S1 reports, a person decides anything further, separately).

### How a step is read

Each step is a table. **Credentials** is one of `none`, `read_only`, `data_plane_invoke`,
`bucket_write`, `deploy`; a read-only step prefers a read-only profile. **Continuity** states the
serving branch and its basis: no in-flight invocation across an in-place AgentCore update has ever
been observed, so every deploy is an `outage_window` with basis `unobserved`, from the start of
the deploy for that stack to the first post-deploy `delphi-verify` exit 0, or until rollback
completes. **Expected diff** is the template-delta shape; any other line (a replacement, a
`may be replaced`, a whole resource added or removed, a bucket line, a trust-policy change, a
`Parameters` or `Conditions` change) is a DIFF-MATCH stop. Stop at the first failed check and run
that step's rollback.

### Receipts (human-only approvals)

Four checkpoints need the person's own answer before a guarded action: `OQ6-ENTRY` (G0),
`CONTINUITY-APPROVAL` (every deploy, a rollback deploy included), `BUCKET-WRITE-CONFIRM` (S9) and
`SPEND-CONFIRM` (S12). Each is asked of the person directly, never answered by an automated
agent on their behalf. The author recorded these in an AI-DLC audit log; any append-only log that
timestamps the question and the answer serves:

1. Write the statement (window, start, end, basis `unobserved`, recovery path; at S9 the dry
   run's statement block; at S12 the spend bound) to
   `evidence/deploy/<step>-<stack>-a<n>.statement.md`, scrub-safe (no identifier), and compute
   its digest with `shasum -a 256 <file>`.
2. Log the decision: the text `<checkpoint> sha256:<hex> — <one-line question>`, with the options `Approve` and `Stop`.
3. Show the question. Its text **begins with the exact Decision text** (the checkpoint token and
   the digest), then the statement verbatim; the options are `Approve` and `Stop`.
4. After the person answers, log the answer: the label chosen.
5. Put the receipt (audit shard, checkpoint, statement digest, the two row timestamps, and which
   reply evidence backs it) into the step's DeployRecord.

Checkpoint grammar, with an attempt suffix so a re-ask after an abort is a new checkpoint:

```
^(OQ6-ENTRY|CONTINUITY-APPROVAL|BUCKET-WRITE-CONFIRM|SPEND-CONFIRM):(G0|S\d{1,2}(-rollback)?):([A-Za-z0-9-]+):a\d+$
```

`infra/delphi_infra/deploy_record.py` checks every committed record against the audit trail
afterwards (the suite runs it). It proves the session showed a question naming the checkpoint and
statement, and that the human-turn hook saw a reply, before the action started on AWS's clock. It
does not prove who was at the keyboard.

### P-DEPLOY

The common procedure every deploy step follows. Recipe, for both `diff` and `deploy`:

```bash
cd infra && AWS_REGION=<region> CDK_DEFAULT_REGION=<region> \
  npx --yes aws-cdk@2.1145.0 <diff|deploy> <Stack> \
  --app ".venv/bin/python3 app.py" --profile <profile> \
  -c artifactBucket=existing [-c imageOverride:<Stack>=<64-hex tag>]
```

1. Pre-state, read-only, before the first diff. Confirm the account with
   `aws sts get-caller-identity --profile <read-only-profile> --query Account --output text`.
   Read the stack's `AgentRuntimeArn` output into a shell variable (never recorded):
   `aws cloudformation describe-stacks --stack-name <Stack> --profile <read-only-profile> --region <region> --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text`.
   Read `StackStatus`; it must be `CREATE_COMPLETE` or `UPDATE_COMPLETE`. Confirm the
   `tag_before` image exists in the asset repository with
   `aws ecr describe-images --repository-name <asset repository> --image-ids imageTag=<tag_before>`
   (command shape from documentation, unverified). The tree must be clean over the serving paths:
   `git status --porcelain -- src config Dockerfile .dockerignore requirements-agent.txt pyproject.toml infra`
   prints nothing; record `git rev-parse HEAD` as the deployed commit.
2. Run the cdk diff (recipe above, `diff`, adding `--method=template`) and read it: DIFF-MATCH.
   The flag matters: without it the CLI's default change-set mode creates a change set and
   writes a template to the CDK bootstrap bucket, so the diff is not read-only (observed at S2's
   first diff in the author's deploy). First the diff header must name the intended account and
   `<region>`; then every line must be in the step's expected diff.
3. Record `tag_before`: the 64-hex part after the final `:` of the `ContainerUri` output, or, on
   a stack's first U6 deploy (no output yet), of the `[-]` `ContainerUri` line of the diff you just
   ran, recorded as observed.
4. Write the statement file and ask `CONTINUITY-APPROVAL:<step>:<Stack>:a<n>` (Receipts above).
   `Stop` means the step is aborted.
5. Deploy: the same command with `deploy`, the same context. Answer CDK's approval prompt only
   when its table equals the expected diff. If the stack ends in `UPDATE_ROLLBACK_FAILED`, stop
   and go to the person; run no command that continues or skips the rollback, because that is a
   separate production change needing its own confirmation with its impact stated.
6. Read `started_at` (the stack's newest `UPDATE_IN_PROGRESS` event) and re-read `AgentRuntimeArn`:
   a changed ARN means the runtime was replaced, and you stop.
7. Smoke test: `.venv/bin/delphi-verify --arn "$<ARM>_ARN" --profile <invoke-profile> --json`,
   C5-PER-ARM (exit 0, `arm` matching, `schema_version` 1, no `error_kind`); one retry only on a
   first-call `transport_error`. `window_end_at` is the first exit-0 record's `timestamp`.
8. Record the DeployRecord at `evidence/deploy/<step>-<stack>.json` (tags, commits, dates, check
   outcomes, receipt references; no account id, ARN or repository URI), and commit it.

### PROFILE-ROUTING-CONFIRM

A read-only check, run by the person at S6 before any grant deploy (credentials `read_only`),
once per inference-profile id in every arm's pool, in the region you deploy to. The U6b grant
gives each arm foundation-model invoke only in the destination regions
`infra/profile_routing.json` lists; that list was taken from the AWS documentation (each model's
Geo: US table), not from this account, and a region the live profile routes to but the file
omits would be denied. Command shape from the Bedrock API reference, unverified and not run here:

```bash
aws bedrock get-inference-profile --inference-profile-identifier <profile_id> \
  --region <region> --profile <read-only-profile> \
  --query "{status:status,type:type,models:models[].modelArn}"
```

Compare four fields with the routing file's entry for that id:

| From the response | Compared with | Pass |
|---|---|---|
| the region segment of each `models[].modelArn` | `destination_regions` | the sets are equal |
| the model segment of each `models[].modelArn` | `underlying_model_id` | equal for every entry |
| `status` | — | `ACTIVE` |
| `type` | — | `SYSTEM_DEFINED` |

Record the region set read and the outcome per id, and nothing else: `inferenceProfileArn` is
never recorded, because it carries the account id. On a match, the routing file's
`provenance.read_date` is set to the date of this read in the U6b commit (it is null until then).
On a mismatch, S7 and S8 are blocked: correct the routing file, re-derive the expected diffs, and
run the check again.

### IMAGE-NEUTRAL

An offline check (credentials `none`) that a commit changes no image: at S6 for U6b and at S10
for U6c.

```bash
git diff --name-only <U6a commit> HEAD -- src config pyproject.toml requirements-agent.txt Dockerfile .dockerignore
CDK_OUTDIR=<tmpdir> CDK_CONTEXT_JSON='{"artifactBucket":"existing"}' \
  AWS_PROFILE= AWS_EC2_METADATA_DISABLED=true infra/.venv/bin/python3 infra/app.py
```

The asset hash is the `dockerImages` key in `<tmpdir>/<Stack>.assets.json`. Which result passes
at S10 depends on `.dockerignore`, read as it stands:

| Branch | `.dockerignore` | Pass at S6 | Pass at S10 |
|---|---|---|---|
| accepted | re-excludes `config/artifact-location.json` after `!config/` | the diff prints nothing; the asset hash equals S2's and S5's `tag_after` | the same |
| declined | re-admits all of `config/` | the same | the diff is exactly `config/artifact-location.json`; the new asset hash differs by design and is recorded as `expected_asset_hash_s10`; the override comparison passes instead: an offline synth with `-c imageOverride:Delphi-router=<S5 tag_after>` has a router ContainerUri tag equal to S5's `tag_after`, and the S11 live diff has no ContainerUri line |

### G0 — OQ6 entry gate

| Field | Value |
|---|---|
| **Stack** | all |
| **Change class** | none |
| **Credentials** | none |
| **Continuity** | no runtime change |
| **Preconditions** | none; this is the first live section, and no live step, read-only included, runs before it has passed |
| **Approval** | receipt `OQ6-ENTRY:G0:all:a<n>`; statement: every U6 deploy states an outage window, basis unobserved, approved per deploy |
| **Command** | the receipt question (Receipts above) |
| **Expected diff** | none (no deploy) |
| **Pass** | the answer is `Approve`; `Stop`, or any other answer, means this runbook cannot run as written |
| **Rollback** | none |
| **Record** | `evidence/deploy/G0-all.json` holding the receipt; every later record names it as `g0_receipt_key` |

### S0 — U6a offline

| Field | Value |
|---|---|
| **Stack** | none |
| **Change class** | none |
| **Credentials** | none |
| **Continuity** | no runtime change |
| **Preconditions** | G0 receipt recorded |
| **Approval** | none |
| **Command** | `bash scripts/ci.sh`; the offline suite; an offline synth in both bucket modes; then commit U6a |
| **Expected diff** | none (no deploy) |
| **Pass** | suite green; synth succeeds in both modes; `Delphi-artifacts` absent in existing mode; both arms' managed-policy statements equal those at `b6cb537` (U6a changes no grant) |
| **Rollback** | none |
| **Record** | the U6a commit id; the predicted S2/S5 `[+]` tag, the offline asset hash |

### S1 — Read-only stack inventory

| Field | Value |
|---|---|
| **Stack** | none |
| **Change class** | none |
| **Credentials** | read_only |
| **Continuity** | no runtime change |
| **Preconditions** | G0 receipt recorded: S1 is a live call and waits for G0 |
| **Approval** | none |
| **Command** | `aws cloudformation list-stacks --profile <read-only-profile> --region <region> --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE --query "StackSummaries[?starts_with(StackName,'Delphi-')].StackName"` |
| **Expected diff** | none (no deploy) |
| **Pass** | both `ARMS` stacks are present; any other `Delphi-*` stack is reported to the person and never deleted |
| **Rollback** | none (read-only) |
| **Record** | the stack names found |

### S2 — Delphi-pinned-nova-lite image (U6a)

| Field | Value |
|---|---|
| **Stack** | Delphi-pinned-nova-lite |
| **Change class** | image |
| **Credentials** | deploy |
| **Continuity** | branch outage_window, basis unobserved: from the start of the deploy for this stack to the first post-deploy delphi-verify exit 0, or rollback complete |
| **Preconditions** | T-IMG-U4-1 and the image tests on the U6a tree with Docker running: `.venv/bin/python -m pytest -m image`; baseline `.venv/bin/delphi-verify --arn "$PINNED_ARN" --profile <invoke-profile> --json` exits 0, or exits 1 with kind `record_absent` (the expected pre-U1 signature); any other kind stops |
| **Approval** | `CONTINUITY-APPROVAL:S2:Delphi-pinned-nova-lite:a<n>`; the statement also says that no exercised recovery path exists yet, and that this deploy moves the arm across every unit since its last deploy at once |
| **Command** | P-DEPLOY with Stack = Delphi-pinned-nova-lite, no override |
| **Expected diff** | `[~] AWS::BedrockAgentCore::Runtime Runtime`, ContainerUri tag only (`[-]` observed, `[+]` the S0 predicted hash); `[+] Output ContainerUri`; a `CDKMetadata` line only if the derived delta lists it; no environment line, no IAM table |
| **Smoke** | C5-PER-ARM through delphi-verify: `arm` `pinned:nova-lite`, `chosen_alias` `nova-lite`, routing fields null |
| **Rollback** | override to `tag_before` (`-c imageOverride:Delphi-pinned-nova-lite=<tag_before>`), with its own receipt; not yet exercised, as the approval states |
| **Record** | `evidence/deploy/S2-Delphi-pinned-nova-lite.json` |

### S3 — Rollback exercise on the pinned arm

| Field | Value |
|---|---|
| **Stack** | Delphi-pinned-nova-lite |
| **Change class** | rollback |
| **Credentials** | deploy |
| **Continuity** | branch outage_window, basis unobserved: from the start of the deploy for this stack to the first post-deploy delphi-verify exit 0, or rollback complete |
| **Preconditions** | S2 verified; the router's current tag: run the diff only (no deploy) for Delphi-router and read its `[-]` ContainerUri tag, `router_tag_pre_u6` |
| **Approval** | `CONTINUITY-APPROVAL:S3:Delphi-pinned-nova-lite:a<n>` |
| **Command** | P-DEPLOY with Stack = Delphi-pinned-nova-lite and `-c imageOverride:Delphi-pinned-nova-lite=<router_tag_pre_u6>` |
| **Expected diff** | `[~]` ContainerUri tag to `router_tag_pre_u6`; `[~] Output ContainerUri`; nothing else. Under an override no image is built or pushed |
| **Smoke** | C5-PER-ARM through delphi-verify exits 0; the ContainerUri output tag equals `router_tag_pre_u6` |
| **Rollback** | roll forward (S4) |
| **Record** | `evidence/deploy/S3-Delphi-pinned-nova-lite.json` |

### S4 — Roll forward on the pinned arm

| Field | Value |
|---|---|
| **Stack** | Delphi-pinned-nova-lite |
| **Change class** | roll_forward |
| **Credentials** | deploy |
| **Continuity** | branch outage_window, basis unobserved: from the start of the deploy for this stack to the first post-deploy delphi-verify exit 0, or rollback complete |
| **Preconditions** | S3 verified |
| **Approval** | `CONTINUITY-APPROVAL:S4:Delphi-pinned-nova-lite:a<n>` |
| **Command** | P-DEPLOY with Stack = Delphi-pinned-nova-lite, no override |
| **Expected diff** | `[~]` ContainerUri back to the S2 tag, and the output; nothing else |
| **Smoke** | C5-PER-ARM through delphi-verify exits 0; the output equals the S2 tag. Then record the RollbackExercise: verify exit 0, output equal to the target, diff ContainerUri-only |
| **Rollback** | override to the S3 tag, stop, investigate |
| **Record** | `evidence/deploy/S4-Delphi-pinned-nova-lite.json` and the RollbackExercise |

### S5 — Delphi-router image (U6a)

| Field | Value |
|---|---|
| **Stack** | Delphi-router |
| **Change class** | image |
| **Credentials** | deploy |
| **Continuity** | branch outage_window, basis unobserved: from the start of the deploy for this stack to the first post-deploy delphi-verify exit 0, or rollback complete |
| **Preconditions** | RollbackExercise recorded (S3, S4); T-IMG-U4-1 and the image tests on the U6a tree: `.venv/bin/python -m pytest -m image`; baseline `.venv/bin/delphi-verify --arn "$ROUTER_ARN" --profile <invoke-profile> --json` exits 0, its `artifact_hash` recorded |
| **Approval** | `CONTINUITY-APPROVAL:S5:Delphi-router:a<n>`; recovery path: override to `tag_before`, exercised at S3/S4 |
| **Command** | P-DEPLOY with Stack = Delphi-router, no override |
| **Expected diff** | `[~]` ContainerUri tag only (its `[-]` must equal S3's `router_tag_pre_u6`; a difference is a stop); `[+] Output ContainerUri`; `CDKMetadata` only if derived; no EnvironmentVariables line (`serving_form` is still `legacy_prefix`), no IAM table |
| **Smoke** | C5-PER-ARM through delphi-verify: `arm` `router`, `chosen_alias` in the realpool set, `artifact_hash` equal to the baseline; the latency fields present and nested, with no value written down |
| **Post-checks** | DS-U4-1: `git merge-base --is-ancestor 0fd4cf6 <deployed commit>` exits 0 (until it does, the agent-level fallback stays repository-level, not yet a documented property of the running router). DS-U2-1 (a): `.venv/bin/delphi-verify --arn "$ROUTER_ARN" --profile <invoke-profile> --prompt "$(python3 -c 'print("a"*102401, end="")')"` exits 1; record the printed `request_id` (an AgentCore payload rejection is recorded as such). Then LOG-GROUP-CONFIRM and ENVELOPE-CONFIRM (read-only, [Retention and signals](#retention-and-signals)), then DS-U2-1 (b): exactly one decision line with that `request_id` and `error_kind` `request_too_large` |
| **Rollback** | override to `tag_before` (`-c imageOverride:Delphi-router=<tag_before>`), exercised at S3/S4, with its own receipt |
| **Record** | `evidence/deploy/S5-Delphi-router.json`, including the check outcomes above |

### S6 — U6b offline and PROFILE-ROUTING-CONFIRM

| Field | Value |
|---|---|
| **Stack** | none |
| **Change class** | none |
| **Credentials** | read_only |
| **Continuity** | no runtime change |
| **Preconditions** | S5 recorded; G0 receipt recorded (the profile reads are live calls and wait for G0) |
| **Approval** | none |
| **Command** | `bash scripts/ci.sh` and the offline suite (grants and cdk-nag green in both bucket modes); [PROFILE-ROUTING-CONFIRM](#profile-routing-confirm) for every profile id in every arm's pool, read-only; commit U6b (`infra/`, `tests/`, `docs/` only); then [IMAGE-NEUTRAL](#image-neutral) against the U6a commit. Only the profile reads need credentials; the rest needs none |
| **Expected diff** | none (no deploy) |
| **Pass** | suite and cdk-nag green; every profile matches its routing entry; the image-context diff since U6a is empty and the asset hash equals S2's and S5's `tag_after` |
| **Rollback** | none (nothing deployed); a mismatch is corrected in the routing file before U6b is committed |
| **Record** | the U6b commit id; the region set read per profile and the outcome; the IMAGE-NEUTRAL result |

### S7 — Delphi-pinned-nova-lite grants (U6b)

| Field | Value |
|---|---|
| **Stack** | Delphi-pinned-nova-lite |
| **Change class** | iam_and_outputs |
| **Credentials** | deploy |
| **Continuity** | branch outage_window, basis unobserved: from the start of the deploy for this stack to the first post-deploy delphi-verify exit 0, or rollback complete |
| **Preconditions** | S6 passed: IMAGE-NEUTRAL, and PROFILE-ROUTING-CONFIRM for `us.amazon.nova-lite-v1:0` |
| **Approval** | `CONTINUITY-APPROVAL:S7:Delphi-pinned-nova-lite:a<n>`; the statement names the recovery path (revert U6b), not yet exercised |
| **Command** | P-DEPLOY with Stack = Delphi-pinned-nova-lite, no override |
| **Expected diff** | the execution policy only: its statements and its new cdk-nag suppression metadata, exactly the derived list below. The IAM table removes the every-model, every-region invoke and the bucket-wide read, and adds `bedrock:InvokeModel` on nova-lite's profile and its foundation model in each routed region. No ContainerUri line, no environment line, no trust-policy change |
| **Smoke** | C5-PER-ARM through delphi-verify exits 0: `arm` `pinned:nova-lite`, nova-lite answering through its profile |
| **Rollback** | `git revert --no-edit <U6b commit>`, then P-DEPLOY for this stack (the image is unchanged), with its own receipt |
| **Record** | `evidence/deploy/S7-Delphi-pinned-nova-lite.json`, with `checks` naming PROFILE-ROUTING-CONFIRM and IMAGE-NEUTRAL |

Derived expected diff (the author's templates `u6a` to `u6b`, synthesized in `infra/.venv`; those
historical template sets are not shipped in this sample):

- `[+] RuntimeExecPolicy5BBDFEE5 Metadata.cdk_nag`
- `[~] RuntimeExecPolicy5BBDFEE5 Properties.PolicyDocument.Statement`
- `[-] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel,bedrock:InvokeModelWithResponseStream on arn:aws:bedrock:*::foundation-model/*`
- `[-] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel,bedrock:InvokeModelWithResponseStream on arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/*`
- `[-] RuntimeExecPolicy5BBDFEE5 s3:GetObject on arn:aws:s3:::delphi-artifacts-<AWS::AccountId>-<AWS::Region>/*`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/us.amazon.nova-lite-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-lite-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-lite-v1:0`

### S8 — Delphi-router grants (U6b)

| Field | Value |
|---|---|
| **Stack** | Delphi-router |
| **Change class** | iam_and_outputs |
| **Credentials** | deploy |
| **Continuity** | branch outage_window, basis unobserved: from the start of the deploy for this stack to the first post-deploy delphi-verify exit 0, or rollback complete |
| **Preconditions** | S7 verified; S6 passed: IMAGE-NEUTRAL, and PROFILE-ROUTING-CONFIRM for the three realpool profiles |
| **Approval** | `CONTINUITY-APPROVAL:S8:Delphi-router:a<n>`; the statement names the recovery path (revert U6b), not yet exercised |
| **Command** | P-DEPLOY with Stack = Delphi-router, no override |
| **Expected diff** | the execution policy only, exactly the derived list below: invoke narrowed to the three realpool profiles and their foundation models in each routed region; `s3:GetObject` narrowed from the whole bucket to `router/*`; the cdk-nag suppression metadata, including the single `router/*` entry. No ContainerUri line, no environment line, no trust-policy change |
| **Smoke** | C5-PER-ARM through delphi-verify exits 0; then one invocation per demo prompt (`--prompt`), none `generation_failed`. This exercises only the routes those prompts take; S12 is the broader exercise |
| **Rollback** | `git revert --no-edit <U6b commit>`, then P-DEPLOY for this stack (the image is unchanged), with its own receipt |
| **Record** | `evidence/deploy/S8-Delphi-router.json`, with `checks` naming PROFILE-ROUTING-CONFIRM and IMAGE-NEUTRAL |

Derived expected diff (the author's templates `u6a` to `u6b`, synthesized in `infra/.venv`; those
historical template sets are not shipped in this sample):

- `[+] RuntimeExecPolicy5BBDFEE5 Metadata.cdk_nag`
- `[~] RuntimeExecPolicy5BBDFEE5 Properties.PolicyDocument.Statement`
- `[-] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel,bedrock:InvokeModelWithResponseStream on arn:aws:bedrock:*::foundation-model/*`
- `[-] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel,bedrock:InvokeModelWithResponseStream on arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/*`
- `[-] RuntimeExecPolicy5BBDFEE5 s3:GetObject on arn:aws:s3:::delphi-artifacts-<AWS::AccountId>-<AWS::Region>/*`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/us.amazon.nova-lite-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/us.amazon.nova-pro-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/us.meta.llama3-3-70b-instruct-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-east-1::foundation-model/meta.llama3-3-70b-instruct-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-lite-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-pro-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-east-2::foundation-model/meta.llama3-3-70b-instruct-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-lite-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-pro-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 bedrock:InvokeModel on arn:aws:bedrock:us-west-2::foundation-model/meta.llama3-3-70b-instruct-v1:0`
- `[+] RuntimeExecPolicy5BBDFEE5 s3:GetObject on arn:aws:s3:::delphi-artifacts-<AWS::AccountId>-<AWS::Region>/router/*`

### S9 — Publish the artifact to the bucket

| Field | Value |
|---|---|
| **Stack** | none (the artifact bucket) |
| **Change class** | publish |
| **Credentials** | bucket_write |
| **Continuity** | no runtime change |
| **Preconditions** | S8 verified; the dry run (below) has printed its plan and you have read it |
| **Approval** | receipt `BUCKET-WRITE-CONFIRM:S9:bucket:a<n>`; the statement is the dry run's `statement (scrub-safe):` block, verbatim |
| **Command** | the commands below, in order: build, dry run, the receipt, `--execute`, verify-remote |
| **Expected diff** | none (no deploy). The impact statement: a new immutable `router/<hash>/` prefix and the first write of the pointer `router/current.json`; no existing key is overwritten; the legacy prefix and the bucket's configuration are untouched; the bucket's versioning state is unknown, so nothing here promises that an old object can be recovered |
| **Stop** | stop, and write nothing, unless the dry run's pointer line reads `would write pointer router/current.json: creates (no previous pointer)`, or it reads `would write pointer router/current.json: REPLACES the previous pointer above; ...` and your statement explicitly approves that replacement with the previous pointer body (printed above that line) copied into it. Lines `present, identical: <key>` are legal on a resume; any other existing key in the plan is a stop. The statement file is the `statement (scrub-safe):` block exactly as printed |
| **Pass** | verify-remote exits 0 with the router's pool |
| **Rollback** | none needed: nothing serves the new keys before S11, and no deletion is part of recovery |
| **Record** | `evidence/deploy/S9-bucket.json` with `bucket_write_receipt`; `started_at` is the earliest `LastModified` of the written keys, read with `aws s3api head-object --bucket <bucket> --key <key> --query LastModified` (read-only); if the pointer is written last that bound is weaker, and the record says so |

```bash
.venv/bin/delphi-artifact build --pool config/models_realpool.yaml
AWS_PROFILE=<read-only-profile> AWS_REGION=<region> \
  .venv/bin/delphi-artifact publish <artifact dir> --pool config/models_realpool.yaml
  # BUCKET-WRITE-CONFIRM:S9:bucket:a<n> receipt here; the statement is the dry run's statement block
AWS_PROFILE=<bucket-write-profile> AWS_REGION=<region> \
  .venv/bin/delphi-artifact publish <artifact dir> --pool config/models_realpool.yaml --execute
AWS_PROFILE=<read-only-profile> AWS_REGION=<region> \
  .venv/bin/delphi-artifact verify-remote --pool config/models_realpool.yaml
```

### S10 — U6c offline

| Field | Value |
|---|---|
| **Stack** | none |
| **Change class** | none |
| **Credentials** | none |
| **Continuity** | no runtime change |
| **Preconditions** | S9 verify-remote exited 0 with `--pool config/models_realpool.yaml`, relayed |
| **Approval** | none |
| **Command** | edit `config/artifact-location.json` `serving_form` from `legacy_prefix` to `pointer` (its only change); the offline suite; commit U6c; then [IMAGE-NEUTRAL](#image-neutral) against the U6a commit |
| **IMAGE-NEUTRAL branch** | accepted: `.dockerignore` re-excludes `config/artifact-location.json` after `!config/`, so the image-context diff since U6a is empty and the asset hash equals S5's `tag_after` |
| **Expected diff** | none (no deploy) |
| **Pass** | suite green; IMAGE-NEUTRAL passes for the branch above |
| **Rollback** | none (nothing deployed) |
| **Record** | the U6c commit id and the IMAGE-NEUTRAL result |

### S11 — The pointer flip (U6c)

| Field | Value |
|---|---|
| **Stack** | Delphi-router |
| **Change class** | artifact_uri_flip |
| **Credentials** | deploy |
| **Continuity** | branch outage_window, basis unobserved: from the start of the deploy for this stack to the first post-deploy delphi-verify exit 0, or rollback complete |
| **Preconditions** | S10 passed; S9 verify-remote exited 0 with `--pool config/models_realpool.yaml`, the router's DELPHI_MODELS_FILE |
| **Approval** | `CONTINUITY-APPROVAL:S11:Delphi-router:a<n>`; the statement names the recovery path (revert U6c) |
| **Command** | P-DEPLOY with Stack = Delphi-router, no override (the accepted branch; only in the declined branch, add `-c imageOverride:Delphi-router=<S5 tag_after>`) |
| **Expected diff** | the router's `DELPHI_ARTIFACT_URI` alone, from `.../router/current` to `.../router/current.json`, exactly the derived list below; nothing else: no ContainerUri line, no IAM table |
| **Smoke** | C5-PER-ARM through delphi-verify exits 0, and the first record's `artifact_hash` equals the one verify-remote printed at S9 |
| **Rollback** | `git revert --no-edit <U6c commit>`, then P-DEPLOY for this stack (with the same override in the declined branch); the legacy prefix is retained, so the revert needs no data repair |
| **Record** | `evidence/deploy/S11-Delphi-router.json` |

Derived expected diff (the author's templates `u6b` to `u6c-deployed`, the U6b tree with
`serving_form: pointer`, synthesized in `infra/.venv`; `u6c-deployed` is the set S11 deployed.
Those historical template sets are not shipped in this sample; `infra/baseline/templates/` holds
only the current `u6c-predicted`):

- `[~] Runtime Properties.EnvironmentVariables.DELPHI_ARTIFACT_URI`

### S12 — NFR1 capture on the router

| Field | Value |
|---|---|
| **Stack** | Delphi-router |
| **Change class** | capture |
| **Credentials** | data_plane_invoke |
| **Continuity** | no runtime change |
| **Preconditions** | S11 verified; the DS-U4-1 check has held; the SPEND-CONFIRM receipt, whose statement says: at most 226 and at least 201 billed invocations of the router arm, in the production account; no cost estimate in advance; cost is reported afterwards from the records' `cost_usd` through the single cost path, with tokens by model so it can be re-priced; the run stops early on an AccessDeniedException |
| **Approval** | `SPEND-CONFIRM:S12:Delphi-router:a<n>` |
| **Command** | `.venv/bin/delphi-nfr1-capture --arn "$ROUTER_ARN" --profile <invoke-profile>`; it runs U2's sample checks and the scrub check itself and writes `evidence/nfr1/` only if both hold |
| **Expected diff** | none (no deploy) |
| **Stop** | on the first `generation_failed` record whose error is an AccessDeniedException the capture stops, writes nothing and names the alias. Then revert the U6b model-invoke grant on the router in an `infra/`-only commit (keeping `serving_form: pointer` and the `router/*` read), deploy it as an iam_and_outputs step with its own receipt, correct the routing file, and rerun from the start. Invocation 227 is refused by the hard counter |
| **Pass** | the files are written; the person reads them and commits `evidence/nfr1/` |
| **Rollback** | none needed: discard the files |
| **Record** | `evidence/deploy/S12-Delphi-router.json` with `spend_receipt`; `started_at` is the earliest record `timestamp` in `records.jsonl` |

**Result, observed 2026-10-20** (the committed sample, `evidence/nfr1/manifest.json` with its
`records.jsonl`, recomputed by U2's sample checks in the suite): the warm, in-process routing
window on the router arm in us-west-2, from receipt in the handler to just before the Converse
call, has a nearest-rank P90 of **0.3 ms** over 200 warm invocations of the demo workload (five
prompts, 40 each, one session), against the 150 ms P90 routing budget (D2). It does not measure
end-to-end latency, Converse time or cold start, and it is one capture on one day, not a
service-level figure. The five demo prompts are short (9 to 58 input tokens), so routing cost
for longer prompts is unmeasured, and the comparison with the 150 ms budget is a window-only
check, not a headroom claim for real traffic.

## Retention and signals

**Health** is C5-PER-ARM: `delphi-verify --arn <output ARN>` exits 0 and the record's `arm`
matches the stack, run per arm after every deploy. **Error rate** is the share of decision
records with a non-null `error_kind`, by kind, read from the runtime log group. Its query is
published here only after ENVELOPE-CONFIRM has recorded what wraps each record, because a query
written against a guessed field path reads nothing. No CloudWatch alarm, threshold or dashboard
is created: there is no live consumer, no observed baseline and no on-call recipient to set one
against.

LOG-GROUP-CONFIRM and ENVELOPE-CONFIRM are read-only, after S5 (command shapes from
documentation, unverified):

```bash
aws logs describe-log-groups --log-group-name-prefix /aws/bedrock-agentcore/ \
  --profile <read-only-profile> --region <region> --query "logGroups[].logGroupName"
aws logs filter-log-events --log-group-name <confirmed name> --limit 1 \
  --profile <read-only-profile> --region <region>
```

Record a name pattern with `<runtime-id>` placeholders and a description of the envelope, never an
identifier.

### Log retention

Each arm stack sets a 90-day retention (`LOG_RETENTION` in `infra/delphi_infra/runtime_stack.py`)
on its runtime's application log group, `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`.
The AgentCore service creates that group, so the stack does not own it: it uses aws-cdk-lib's
`LogRetention` construct, which sets the policy whether or not the group exists yet and leaves
the group in place when the stack is deleted. Retention deletes every event older than the
value, and those events are the experiment's decision records: change the value before the first
deploy if you need them longer. Shipped in the published sample and synthesized offline; not yet
observed against a deployed runtime.

## Teardown (a reader's dedicated account only)

This is the reader's procedure for a clean account dedicated to the walkthrough. Run it there
only, never against the author's account: there, removing the live arms is a production deletion that
needs its own explicit confirmation. In existing mode the teardown has no bucket step: it
never touches the bucket. Each destructive step states what is lost first; no command skips a
confirmation prompt.

### Teardown inventory

| Resource class | Origin | Removed by | Lost | Accrues cost if left | Mode | Bound |
|---|---|---|---|---|---|---|
| `AWS::BedrockAgentCore::Runtime` | arm_stack | cdk destroy of its arm stack | the runtime and its ARN, for good | yes | both | — |
| `AWS::IAM::Role` | arm_stack | cdk destroy of its arm stack | the execution role and the LogRetention function role | no | both | — |
| `AWS::IAM::ManagedPolicy` | arm_stack | cdk destroy of its arm stack | the execution policy | no | both | — |
| `Custom::LogRetention` | arm_stack | cdk destroy of its arm stack (the log group and its retention stay) | nothing | no | both | — |
| `AWS::Lambda::Function` | arm_stack | cdk destroy of its arm stack | the LogRetention singleton function | no | both | — |
| `AWS::IAM::Policy` | arm_stack | cdk destroy of its arm stack | the LogRetention function's inline policy | no | both | — |
| `AWS::CDK::Metadata` | arm_stack | cdk destroy of its stack | nothing | no | both | — |
| `AWS::S3::Bucket` | artifacts_stack | empty it, delete it, then destroy Delphi-artifacts (RETAIN keeps it otherwise); the same for the access log bucket | every artifact and every noncurrent version, and the server access logs | yes | create_only | — |
| `AWS::S3::BucketPolicy` | artifacts_stack | cdk destroy of Delphi-artifacts | the TLS-only policies and the log delivery grant | no | create_only | — |
| uploaded artifact objects | operator_upload | emptied with the bucket | the router artifact (rebuildable with `delphi-artifact build`) | yes | create_only | — |
| service log groups (runtime logs) | service_created | a delete per listed log group | every decision record the arms emitted | yes | both | name pattern `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`, confirmed by LOG-GROUP-CONFIRM at S5; delete what the inventory lists under the prefix |
| asset images (recorded tags) | cdk_bootstrap | a delete per recorded tag | every rollback target | yes | both | the asset repository is shared by every CDK app in the account and region: delete only the tags your stacks output |
| CDK bootstrap stack | cdk_bootstrap | removal of the bootstrap stack, dedicated account only | every CDK app's assets in that account and region | yes | create_only | shared by every CDK app; remove it only in an account dedicated to this walkthrough |

### Teardown sequence

1. Inventory, read-only: list the `Delphi-*` stacks, the runtime log groups under the confirmed
   prefix and (create mode only) the bucket's object versions:
   ```bash
   aws cloudformation list-stacks --region <region> --query "StackSummaries[?starts_with(StackName,'Delphi-')].[StackName,StackStatus]"
   aws logs describe-log-groups --log-group-name-prefix /aws/bedrock-agentcore/ --region <region>
   aws s3api list-object-versions --bucket "delphi-artifacts-<account>-<region>"   # create mode only
   ```
2. Record each arm stack's ContainerUri output tag (the 64-hex part after the final `:`), so the
   images can be removed by tag later:
   ```bash
   aws cloudformation describe-stacks --stack-name <Stack> --region <region> --query "Stacks[0].Outputs[?OutputKey=='ContainerUri'].OutputValue" --output text
   ```
3. **Lost:** both runtimes, their roles and policies, and their ARNs for good. Then run
   `cdk destroy Delphi-pinned-nova-lite`, then `cdk destroy Delphi-router`, each by name, answering
   the confirmation prompt yourself:
   ```bash
   cd infra && npx aws-cdk@2.1145.0 destroy Delphi-pinned-nova-lite --app ".venv/bin/python3 app.py" -c artifactBucket=create
   cd infra && npx aws-cdk@2.1145.0 destroy Delphi-router --app ".venv/bin/python3 app.py" -c artifactBucket=create
   ```
4. **Lost:** every decision record in CloudWatch, the experiment's telemetry. Then delete each
   runtime log group the inventory listed:
   ```bash
   aws logs delete-log-group --log-group-name <listed name> --region <region>
   ```
5. **Lost:** every rollback target. Then delete, by tag only, the image tags recorded in step 2
   from the shared asset repository:
   ```bash
   aws ecr batch-delete-image --repository-name <asset repository> --image-ids imageTag=<recorded tag> --region <region>
   ```
6. Create mode only. **Lost:** every artifact and every noncurrent version in the bucket. List the
   objects and versions first (step 1), then empty the bucket (all versions), delete it, and
   destroy `Delphi-artifacts` (RETAIN means the destroy alone keeps the bucket):
   ```bash
   aws s3api delete-objects --bucket "delphi-artifacts-<account>-<region>" --delete file://<versions list>   # create mode only
   aws s3api delete-bucket --bucket "delphi-artifacts-<account>-<region>"                                   # create mode only
   cd infra && npx aws-cdk@2.1145.0 destroy Delphi-artifacts --app ".venv/bin/python3 app.py" -c artifactBucket=create   # create mode only
   ```
7. Optional, dedicated account only. **Lost:** every CDK app's assets in that account and region.
   Then remove the CDK bootstrap stack:
   ```bash
   aws cloudformation delete-stack --stack-name CDKToolkit --region <region>
   ```
8. Inventory again, read-only, with each command's expected result:
   ```bash
   aws cloudformation list-stacks --region <region> --query "StackSummaries[?starts_with(StackName,'Delphi-') && StackStatus!='DELETE_COMPLETE'].StackName"   # expected: empty
   aws bedrock-agentcore-control list-agent-runtimes --region <region> --query "agentRuntimes[?starts_with(agentRuntimeName,'delphi_')]"   # expected: empty
   aws logs describe-log-groups --log-group-name-prefix /aws/bedrock-agentcore/ --region <region> --query "logGroups[].logGroupName"   # expected: empty
   aws ecr describe-images --repository-name <asset repository> --image-ids imageTag=<recorded tag> --region <region>   # expected: empty (ImageNotFound)
   aws s3api head-bucket --bucket "delphi-artifacts-<account>-<region>"   # create mode only; expected: empty (404)
   ```
   Anything left is either inside a stated bound (the shared asset repository, the bootstrap
   stack) or a gap to record in the walkthrough record. Command shapes are from documentation and
   unverified.

## Walkthrough record

After a clean-account walkthrough, commit `evidence/walkthrough/record.json`. Its versions are
read from the image the walkthrough built, not from the declared floors, because the serving
dependencies and the base image tag float on purpose:

```json
{
  "date": "<YYYY-MM-DD>",
  "base_image_digest": "sha256:<64 hex, from the local image metadata>",
  "serving_packages": {"bedrock-agentcore": "<v>", "boto3": "<v>", "numpy": "<v>", "pyyaml": "<v>"},
  "aws_cdk_lib": "<v>", "cdk_cli": "<v>", "cdk_nag": "<v>",
  "python_container": "<v>",
  "arms_verified": ["router", "pinned:nova-lite"],
  "gaps_found": [],
  "version_provenance": "pip list inside the built image (the image listing); base digest from docker image inspect"
}
```

## Container

`Dockerfile` (repo root) builds `linux/arm64`, installs `requirements-agent.txt` + the `delphi` package, and runs `python -m delphi.agent.entrypoint`, which serves the AgentCore HTTP protocol contract (`POST /invocations`, `GET /ping` on :8080) via the SDK.

The router artifact is pulled from `DELPHI_ARTIFACT_URI` (S3) at cold start (design D1), so an artifact change needs no image rebuild. Build type is `Container`, not `CodeZip`.

## Invoking a deployed arm (eval driver)

`src/delphi/agent/runtime_client.py` wraps the data-plane call:

```python
from delphi.agent.runtime_client import AgentCoreRuntimeInvoker

invoker = AgentCoreRuntimeInvoker(agent_runtime_arn=arn)   # region read from the ARN
resp = invoker.invoke("What is 2+2?")   # -> RuntimeResponse(payload, status_code, session_id)
```

Each arm is a separate deployed runtime, so the Phase 4 driver holds one invoker per ARN and replays the held-out test set through all of them (T-INF-1 arm parity is already guaranteed upstream: same image, same generation params, routing is the only delta).

## AgentCore CLI (local iteration only)

```bash
npm install -g @aws/agentcore
agentcore dev                     # local server + agent inspector on :8080
agentcore invoke "your prompt"    # invoke a deployed runtime
agentcore logs --follow           # stream runtime logs
```

The CLI is convenient for iterating on `entrypoint.py`. The experiment's arms are deployed only
through the in-repo CDK stacks above, so every arm's infra is versioned and diffable.

## IAM

Each arm has one execution role and one managed policy; no `AdministratorAccess` and no
`*FullAccess`. The synthesized template is the authority on what the policy grants; the tables
below are compared with it by the suite (T-INF-U6-19), with the account, region and partition
shown as placeholders. Each arm may invoke only its own candidates
([Model access](#model-access)), with `bedrock:InvokeModel` alone (the agent calls Converse, no
stream API). Only the router reads the artifact bucket, and only under `router/*`. The trust
policy is `sts:AssumeRole` for `bedrock-agentcore.amazonaws.com` with the confused-deputy
conditions the AgentCore documentation gives for a runtime execution role (IAM Permissions for
AgentCore Runtime, read 2026-10-28): `aws:SourceAccount` equal to this account, and
`aws:SourceArn` like `arn:aws:bedrock-agentcore:<region>:<account>:*`.

### Execution policy statements

| Arm | Actions | Resources |
|---|---|---|
| `router` | `bedrock:InvokeModel` | `arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/us.amazon.nova-lite-v1:0`, `arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/us.amazon.nova-pro-v1:0`, `arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/us.meta.llama3-3-70b-instruct-v1:0`, `arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0`, `arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-pro-v1:0`, `arn:aws:bedrock:us-east-1::foundation-model/meta.llama3-3-70b-instruct-v1:0`, `arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-lite-v1:0`, `arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-pro-v1:0`, `arn:aws:bedrock:us-east-2::foundation-model/meta.llama3-3-70b-instruct-v1:0`, `arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-lite-v1:0`, `arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-pro-v1:0`, `arn:aws:bedrock:us-west-2::foundation-model/meta.llama3-3-70b-instruct-v1:0` |
| `router` | `cloudwatch:PutMetricData`, `ecr:GetAuthorizationToken`, `xray:PutTelemetryRecords`, `xray:PutTraceSegments` | `*` |
| `router` | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | `arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*`, `arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*` |
| `router` | `ecr:BatchCheckLayerAvailability`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` | `arn:<AWS::Partition>:ecr:<AWS::Region>:<AWS::AccountId>:repository/cdk-hnb659fds-container-assets-<AWS::AccountId>-<AWS::Region>` |
| `router` | `s3:GetObject` | `arn:aws:s3:::delphi-artifacts-<AWS::AccountId>-<AWS::Region>/router/*` |
| `pinned:nova-lite` | `bedrock:InvokeModel` | `arn:aws:bedrock:<AWS::Region>:<AWS::AccountId>:inference-profile/us.amazon.nova-lite-v1:0`, `arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0`, `arn:aws:bedrock:us-east-2::foundation-model/amazon.nova-lite-v1:0`, `arn:aws:bedrock:us-west-2::foundation-model/amazon.nova-lite-v1:0` |
| `pinned:nova-lite` | `cloudwatch:PutMetricData`, `ecr:GetAuthorizationToken`, `xray:PutTelemetryRecords`, `xray:PutTraceSegments` | `*` |
| `pinned:nova-lite` | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | `arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*`, `arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*` |
| `pinned:nova-lite` | `ecr:BatchCheckLayerAvailability`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` | `arn:<AWS::Partition>:ecr:<AWS::Region>:<AWS::AccountId>:repository/cdk-hnb659fds-container-assets-<AWS::AccountId>-<AWS::Region>` |

The image-layer reads are on the CDK bootstrap's shared asset repository, which every CDK app in
the account and region pushes to: it is not a repository for this app alone.

### cdk-nag suppressions

The AwsSolutions rule pack runs as an Aspect at every synth, and `infra/app.py` exits non-zero on
any error it reports that no suppression covers. Each suppression is attached to one resource,
names the exact finding it covers, and states its reason and the gate that would remove it. The
model-invoke statement has none.

| Arm | Rule | Applies to | Reason |
|---|---|---|---|
| `router` | `AwsSolutions-IAM5` | `Resource::*` | the one merged `*` statement: cloudwatch:PutMetricData, ecr:GetAuthorizationToken, xray:PutTelemetryRecords, xray:PutTraceSegments; none of these actions supports resource-level permissions |
| `router` | `AwsSolutions-IAM5` | `Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*`, `Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*` | the logs actions, scoped to the AgentCore runtime log groups and their streams by the documented prefix: the one runtime's group is named by its runtime id, which exists only after the policy |
| `router` | `AwsSolutions-IAM5` | `Resource::arn:aws:s3:::delphi-artifacts-<AWS::AccountId>-<AWS::Region>/router/*` | the content-addressed prefixes under router/ are produced at publish time, so the one prefix is granted; router arm only; gate: none (proposal P-U6-13) |
| `pinned:nova-lite` | `AwsSolutions-IAM5` | `Resource::*` | the one merged `*` statement: cloudwatch:PutMetricData, ecr:GetAuthorizationToken, xray:PutTelemetryRecords, xray:PutTraceSegments; none of these actions supports resource-level permissions |
| `pinned:nova-lite` | `AwsSolutions-IAM5` | `Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*`, `Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*` | the logs actions, scoped to the AgentCore runtime log groups and their streams by the documented prefix: the one runtime's group is named by its runtime id, which exists only after the policy |

**The logs grant is scoped to the AgentCore runtime log groups.** Each runtime writes to
`/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` (the pattern LOG-GROUP-CONFIRM recorded at
S5, held in `infra/delphi_infra/grants.py` as `CONFIRMED_LOG_GROUP_PATTERN`). The policy grants
the prefix, `/aws/bedrock-agentcore/runtimes/*` and its log streams, as the AgentCore
execution-role documentation does, because the runtime id that names the one group exists only
after the policy. This reverses the 2026-10-20 decision to keep logs on `*` (`NARROW_LOGS =
True`). A live deploy on 2026-10-28 confirmed that decision records still reach CloudWatch with
the scoped grant. The remaining `Resource::*` row is one statement: after `minimizePolicies` the
X-Ray, PutMetricData and GetAuthorizationToken actions are merged into a single
`Resource: "*"` statement, and cdk-nag matches the finding, not the action. T-INF-U6-73 pins that
statement's exact action set, so an action added to it fails the suite.

Each arm stack also carries aws-cdk-lib's `LogRetention` singleton function (see
[Log retention](#log-retention)). Its role gets two suppressions, both attached to that
function's own role and policy: `AwsSolutions-IAM4` on `AWSLambdaBasicExecutionRole`, which the
construct attaches for the function's own logs, and `AwsSolutions-IAM5` on the log-group prefix for
`logs:PutRetentionPolicy`/`logs:DeleteRetentionPolicy`, which `runtime_stack.py` scopes from
`*` to the `/aws/bedrock-agentcore/runtimes/*` prefix and the function's own
`/aws/lambda/<stack>-*` log group (the construct caps that one too; a first live test without it
failed with AccessDenied and rolled back). The target names hold a runtime id and a function name
that exist only after deploy.

In create mode the `Delphi-artifacts` artifact bucket logs server access to a second bucket in
the same stack. Both buckets block public access, use SSE-S3, enforce TLS, are
BucketOwnerEnforced and versioned, and are retained on stack deletion; the log bucket expires
logs after 365 days. cdk-nag reports no finding on this stack, so it carries no suppression
(the pack accepts a bucket that is itself a server access log destination).
