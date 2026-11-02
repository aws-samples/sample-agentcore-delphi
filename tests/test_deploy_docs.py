"""The deployment documentation, asserted — LC-U6-5 DeployRunbook and LC-U6-8 DocsSync (U6a
part; BR1.2, BR1.7, BR2.3, BR2.6, BR2.7, BR5.4, BR5.5, BR6.1-BR6.5, BR7.1-BR7.2, BR9.1-BR9.5,
BR11.1-BR11.6; NFR8.6-NFR8.8, NFR8.10, NFR8.12, NFR8.16, NFR8.18-NFR8.20; FR11.3.2; D-CG-U6-7,
-8).

docs/deployment.md is parsed (u6_support.section / cards / numbered / table), never read by
eye. Each runbook step is a `### <id> — <title>` heading with a `| **Field** | value |` table.

  T-INF-U6-2   The Arms section lists exactly ARMS, by arm and stack name, and states their
               count; the arms not yet available are listed only under a heading that says so,
               each naming U8 and D1, and are not counted (BR1.2, BR1.6).
  T-INF-U6-20  No command in the migration runbook destroys a stack or deletes, empties or
               reconfigures the bucket; S1 reports stacks outside ARMS and deletes none; in the
               teardown, bucket commands appear only in a step marked create mode only, and the
               existing-mode bucket is never touched (BR1.7, BR2.3, BR12.6, NFR8.6, NFR8.10).
  T-INF-U6-21  The create-mode path's first bucket step is a read-only head-bucket, followed by
               the instruction to stop and use existing mode (BR2.6).
  T-INF-U6-22  The existing-mode section names the five bucket controls as not declared by
               this repository, and inspecting them as a read-only human step (BR2.7, NFR8.7).
  T-INF-U6-26  The RollbackExercise (S3, S4) comes before the first router image step (S5); S2
               states that no exercised recovery path exists yet; S5 requires the exercise and
               names it as its recovery path (BR5.4, FR8.2.3).
  T-INF-U6-27  No `cdk gc` anywhere; teardown deletes images by recorded tag only (BR5.5).
  T-INF-U6-28  Every step carries exactly one credentials label from {none, read_only,
               data_plane_invoke, bucket_write, deploy} (BR6.1, NFR8.8).
  T-INF-U6-29  The runbook's pre-deploy check is `git status --porcelain` over the serving
               paths; the Dockerfile has no LABEL, ARG or ENV carrying a commit (BR6.2).
  T-INF-U6-30  Every step declares one change class and an expected diff, and every live step a
               rollback line naming a tag source, a revert or a roll forward (BR6.3, FR8.2.4).
  T-INF-U6-31  The document never shows a forced deploy or a disabled approval prompt (BR6.4).
  T-INF-U6-32  For every change class run on both arms, the pinned step precedes the router
               step (BR6.5, FR11.4.1).
  T-INF-U6-34  Every deploy step's continuity line names outage_window and basis unobserved; no
               step pairs keeps_serving with unobserved; other steps state no runtime change
               (BR7.1, BR7.2, FR11.3.1).
  T-INF-U6-39  Image steps require `pytest -m image` (T-IMG-U4-1); S5's post-checks run DS-U4-1,
               DS-U2-1 (a) with a 102,401-byte prompt expecting exit 1, LOG-GROUP-CONFIRM,
               ENVELOPE-CONFIRM, then DS-U2-1 (b); every deploy step's smoke test is C5-PER-ARM
               through delphi-verify (BR9.1-BR9.5).
  T-INF-U6-41  The reader's create-mode path is one ordered sequence (prerequisites, bootstrap,
               existence check, Delphi-artifacts, build, publish dry run, publish --execute,
               verify-remote, each arm by name, delphi-verify) that names every create-mode
               stack and states it is valid only once serving_form is pointer (BR11.1, NFR10.1).
  T-INF-U6-42  No `deploy --all` in docs/, README.md or infra/app.py (BR11.2).
  T-INF-U6-43  README's Status names the ARMS stacks as the ones that deploy and says to deploy
               by name; its S3 sentences describe both bucket modes (BR11.3).
  T-INF-U6-45  The manual sequence is documented (reader path and runbook); U5's forbidden CI
               pattern still rejects a `cdk deploy` line (keep-fixed half; negative control: a
               seeded deploy line is rejected) (BR11.5, FR8.3.1).
  T-INF-U6-46  The prerequisites state the CDK CLI version observed working and its date, that
               stacks are environment-agnostic (read the diff header), the infra/.venv
               interpreter, the reader's region and the snapshot regions, and the model access
               table (BR11.6).
  T-INF-U6-57  G0 is the first step; S1 waits for G0; every deploy step's Approval names its
               CONTINUITY-APPROVAL:<step>:<stack>:a<n> checkpoint and precedes its Command; in
               P-DEPLOY the receipt item precedes the deploy item (FR11.3.2, D-CG-U6-7).
  T-INF-U6-61  UPDATE_ROLLBACK_FAILED is a stop, and the document shows no command that
               continues or skips a rollback (NFR8.16).
  T-INF-U6-63  The doc states the retention each arm sets on its service-created log group,
               that it deletes older decision records, and that it is not yet observed live;
               each arm stack sets exactly that value through one Custom::LogRetention on the
               confirmed log-group pattern, and owns no AWS::Logs::LogGroup (NFR8.19).
  T-INF-U6-64  No error-rate query appears while no ENVELOPE-CONFIRM result is committed; no
               stack synthesizes a CloudWatch alarm or dashboard, in either mode (NFR8.20).
  T-INF-U6-70  P-DEPLOY item 1 captures pre-state read-only before the first diff: the
               caller-identity account check, AgentRuntimeArn into a shell variable never
               recorded, StackStatus CREATE_COMPLETE or UPDATE_COMPLETE, and the tag_before
               image's existence (describe-images, marked unverified) (D-CG-U6-8).
  T-INF-U6-72  P-DEPLOY's diff item runs the diff with `--method=template` and says why: the
               default change-set mode creates a change set and writes a template to the CDK
               bootstrap bucket, so it is not read-only. A defect found by deployment (S2's
               first diff, U6 code-summary deviation D-14), added at Step 21 with its failing
               run before the doc fix. Negative control: a diff item without the flag fails.
               The S2 record says the same through an errata note beside it,
               evidence/deploy/S2-Delphi-pinned-nova-lite.errata.md (the first S2 diff created a
               change set and wrote to the bootstrap bucket; later diffs used the flag), which is
               scrub-safe; the record's statement hash still matches its statement, so the
               receipt-bound files are unchanged (U6 review R-03).

U6b part (BR4.4, BR8.1, BR8.3, BR8.4, BR11.4; NFR8.5, NFR8.13-NFR8.15, NFR9.3, NFR9.9, NFR1.3,
FR11.4.1-FR11.4.2; D-CG-U6-6, -10). Expected diffs are compared with the delta of the COMMITTED
templates under infra/baseline/templates/ (synthesized in infra/.venv, the deploy venv), with no
synth, so the two venvs' aws-cdk-lib difference cannot enter the comparison.

  T-INF-U6-19  The IAM section's per-arm statement table (actions, resources with the account and
               region as placeholders) equals the synthesized statements of each arm, and its
               suppression table equals each policy's cdk-nag register (BR4.4, NFR9.9).
  T-INF-U6-36  S9's publish and verify-remote commands all name `--pool
               config/models_realpool.yaml`, the basename of the router's DELPHI_MODELS_FILE in
               the stack; S9 (verify-remote) comes before S10 (the serving_form edit) (BR8.1).
  T-INF-U6-38  S11's rollback is a `git revert` of U6c and states that the legacy prefix is
               retained, so the revert needs no data repair; its smoke test compares the served
               artifact_hash with verify-remote's (BR8.3, BR8.4).
  T-INF-U6-44  The model-access table's ids per arm equal the synthesized model-invoke ids per
               arm (inference profiles, or plain models in the deploy region), and its
               destination regions per arm equal the regions of the synthesized foundation-model
               grants (BR11.4, AC1.4.3).
  T-INF-U6-55  S7 and S8 list IMAGE-NEUTRAL as a precondition, and their derived expected diffs
               carry no ContainerUri line (NFR8.13).
  T-INF-U6-58  S7 and S8 list PROFILE-ROUTING-CONFIRM as a precondition; S12 stops on
               AccessDeniedException and names the revert; no routing entry has a read date
               later than the latest recorded confirmation (vacuous while every read date is
               pending). Control: a dated entry with no recorded confirmation is reported
               (NFR9.3, FR11.4.2).
  T-INF-U6-59  S9 carries `bucket_write` and its BUCKET-WRITE-CONFIRM receipt; its impact
               statement names the new router/<hash>/ prefix, the pointer router/current.json,
               that no existing key is overwritten and that versioning is unknown; in its command
               block the receipt precedes `--execute`; its stop rule is written in the
               publisher's real output terms (`creates`, `REPLACES`, `present, identical`, the
               scrub-safe statement block), each of which publish.py really prints (NFR8.5,
               D-CG-U6-6).
  T-INF-U6-60  The derived expected-diff block of S7, S8 and S11 equals the delta computed from
               the committed templates (u6a to u6b for S7/S8, u6b to u6c-deployed for S11, the
               set S11 deployed), resource lines and IAM statement lines; no block adds or
               removes a whole resource; the last-synthesized identity holds for every committed
               U6 template. u6c-predicted differs from u6c-deployed only in cdk_nag
               reason metadata. Needs the author's historical template sets (u6a, u6b,
               u6c-deployed), which the published sample does not ship: an author-record test.
  T-INF-U6-60b The committed u6c-predicted set holds the frozen identity of
               infra/baseline/live_identity.json. Control: a template that lost a resource is
               reported. Controls:
               a block with one line dropped differs, and a drifted property is caught (NFR8.15,
               BR6.4).
  T-INF-U6-74  Freshness: in both bucket modes the current synth of each arm equals the committed
               u6c-predicted template, except the AWS::CDK::Metadata analytics string (which
               encodes the synthesizing venv's library versions). Rebaselined 2026-10-20 by
               human decision ("Rebaseline, no deploy"): only the policy's cdk_nag reason text
               changed. Control: a drifted property is caught.
  T-INF-U6-67  (runbook half) S12 is preceded by SPEND-CONFIRM, states the 226 bound and the 201
               minimum, gives no cost estimate in advance, and reports cost afterwards from the
               records' cost_usd (NFR1.4, P-U6-14).
  T-INF-U6-68  The PROFILE-ROUTING-CONFIRM block names `get-inference-profile`, the four compared
               fields (modelArn region set, the model segment against underlying_model_id,
               status ACTIVE, type SYSTEM_DEFINED) and that inferenceProfileArn is never recorded
               (NFR9.3, DD-U6-3).
  T-INF-U6-69  S10 states the IMAGE-NEUTRAL branch that `.dockerignore` shows; the IMAGE-NEUTRAL
               block names the declined branch's expected_asset_hash_s10 and override
               comparison; in the declined branch S11 carries the override. Control: the other
               branch is reported (NFR8.14, R-03).
  guard        the autouse socket guard is active.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest

import u6_support as u6

ROOT = u6.REPO
LABELS = {"none", "read_only", "data_plane_invoke", "bucket_write", "deploy"}
CLASSES = {"none", "image", "iam_and_outputs", "artifact_uri_flip", "rollback", "roll_forward",
           "create_bucket_stack", "publish", "capture"}
SERVING = "git status --porcelain -- src config Dockerfile .dockerignore requirements-agent.txt " \
          "pyproject.toml infra"


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_deploy_docs.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _doc() -> str:
    return u6.doc_text()


def _card(cid: str) -> u6.Card:
    (c,) = [c for c in u6.cards() if c.id == cid]
    return c


def _deploy_cards() -> list[u6.Card]:
    return [c for c in u6.cards() if c.fields.get("Credentials") == "deploy"]


def _arms():
    return u6.infra_module("arms")


# --- T-INF-U6-2 --------------------------------------------------------------------------
def test_t_inf_u6_2_documented_arms_equal_arms():
    arms = _arms()
    body = u6.section(_doc(), "Arms")
    deployed = body.split("### Not yet available")[0]
    listed = re.findall(r"^- `([^`]+)` — stack `([^`]+)`", deployed, re.M)
    assert [a for a, _ in listed] == list(arms.ARMS)
    assert [s for _, s in listed] == [arms.stack_name(a) for a in arms.ARMS]
    assert f"**{len(arms.ARMS)}** arms" in deployed
    later = u6.section(_doc(), "Not yet available (U8)")
    names = re.findall(r"^- `([^`]+)`", later, re.M)
    assert names == list(arms.NOT_YET_AVAILABLE)
    assert "U8" in later and "D1" in later


# --- T-INF-U6-20 -------------------------------------------------------------------------
DESTRUCTIVE = re.compile(r"\bs3 rb\b|\bs3 rm\b|delete-bucket|put-bucket-|delete-object"
                         r"|delete-objects|cdk destroy|delete-stack")


def test_t_inf_u6_20_no_destructive_command_touches_the_live_account():
    runbook = u6.section(_doc(), "Migration runbook")
    existing = u6.section(_doc(), "Existing mode")
    for name, body in (("runbook", runbook), ("existing mode", existing)):
        hits = [b for b in u6.code_blocks(body) if DESTRUCTIVE.search(b)]
        assert hits == [], (name, hits)
        assert not DESTRUCTIVE.search(body), name
    s1 = _card("S1")
    assert "reported" in s1.fields["Pass"] and "never deleted" in s1.fields["Pass"]
    for item in u6.numbered(u6.section(_doc(), "Teardown sequence")):
        if re.search(r"\bs3 rb\b|\bs3 rm\b|delete-bucket|delete-object|Delphi-artifacts", item):
            assert "create mode only" in item, item[:80]
    assert "never touch" in u6.section(_doc(), "Teardown (a reader's dedicated account only)")


# --- T-INF-U6-21 -------------------------------------------------------------------------
def test_t_inf_u6_21_create_mode_checks_the_name_first():
    steps = u6.numbered(u6.section(_doc(), "Reader path (create mode)"))
    first = next(s for s in steps if "bucket" in s.lower())
    assert "head-bucket" in first and "read-only" in first
    assert "stop" in first.lower() and "existing" in first


# --- T-INF-U6-22 -------------------------------------------------------------------------
def test_t_inf_u6_22_existing_mode_names_the_undeclared_controls():
    body = u6.section(_doc(), "Existing mode")
    for word in ("public access", "encryption", "TLS", "ownership", "versioning"):
        assert word in body, word
    assert "not declared" in body and "read-only" in body


# --- T-INF-U6-26 -------------------------------------------------------------------------
def test_t_inf_u6_26_rollback_is_exercised_before_the_router_relies_on_it():
    ids = [c.id for c in u6.cards()]
    assert ids.index("S3") < ids.index("S5") and ids.index("S4") < ids.index("S5")
    assert "no exercised recovery path" in _card("S2").fields["Approval"]
    s5 = _card("S5")
    assert "RollbackExercise" in s5.fields["Preconditions"]
    assert "exercised at S3/S4" in s5.fields["Rollback"]
    assert "not yet exercised" in _card("S2").fields["Rollback"].lower()


# --- T-INF-U6-27 -------------------------------------------------------------------------
def test_t_inf_u6_27_no_garbage_collection_and_images_by_tag():
    assert "cdk gc" not in _doc()
    images = [s for s in u6.numbered(u6.section(_doc(), "Teardown sequence"))
              if "batch-delete-image" in s]
    assert images and all("imageTag=" in s and "recorded" in s for s in images)
    assert not any("imageDigest" in s for s in images)


# --- T-INF-U6-28 -------------------------------------------------------------------------
def test_t_inf_u6_28_every_step_has_one_credentials_label():
    found = u6.cards()
    assert found
    for c in found:
        assert c.fields.get("Credentials") in LABELS, (c.id, c.fields.get("Credentials"))


# --- T-INF-U6-29 -------------------------------------------------------------------------
def test_t_inf_u6_29_deploy_from_a_clean_tree_commit_not_baked():
    assert SERVING in u6.section(_doc(), "P-DEPLOY")
    for line in (ROOT / "Dockerfile").read_text().splitlines():
        if re.match(r"^\s*(LABEL|ARG|ENV)\b", line):
            assert not re.search(r"commit|git|revision|\bsha\b", line, re.I), line


# --- T-INF-U6-30 -------------------------------------------------------------------------
def test_t_inf_u6_30_one_change_class_an_expected_diff_and_a_rollback():
    for c in u6.cards():
        assert c.fields.get("Change class") in CLASSES, c.id
        assert c.fields.get("Expected diff"), c.id
    for c in _deploy_cards():
        assert re.search(r"override|revert|roll forward", c.fields.get("Rollback", ""), re.I), c.id


# --- T-INF-U6-31 -------------------------------------------------------------------------
def _forced(text: str) -> bool:
    return "--force" in text or "require-approval never" in text


def test_t_inf_u6_31_no_forced_deploy_or_disabled_prompt():
    assert _forced("npx cdk deploy Delphi-router --require-approval never"), "control"
    assert _forced("npx cdk deploy Delphi-router --force"), "control"
    assert not _forced(_doc())


# --- T-INF-U6-32 -------------------------------------------------------------------------
def test_t_inf_u6_32_pinned_before_router_per_change_class():
    found = u6.cards()
    by_class: dict[str, dict[str, int]] = {}
    for i, c in enumerate(found):
        stack = c.fields.get("Stack", "")
        by_class.setdefault(c.fields.get("Change class"), {}).setdefault(stack, i)
    for cls, stacks in by_class.items():
        if "Delphi-router" in stacks and "Delphi-pinned-nova-lite" in stacks:
            assert stacks["Delphi-pinned-nova-lite"] < stacks["Delphi-router"], cls
    assert "Delphi-router" in by_class["image"] and "Delphi-pinned-nova-lite" in by_class["image"]


# --- T-INF-U6-34 -------------------------------------------------------------------------
def test_t_inf_u6_34_every_step_states_its_continuity():
    for c in u6.cards():
        line = c.fields.get("Continuity", "")
        assert not ("keeps_serving" in line and "unobserved" in line), c.id
        if c.fields.get("Credentials") == "deploy":
            assert "outage_window" in line and "basis unobserved" in line, c.id
        else:
            assert "no runtime change" in line, c.id


# --- T-INF-U6-39 -------------------------------------------------------------------------
def test_t_inf_u6_39_handed_off_checks_sit_where_designed():
    for c in u6.cards():
        if c.fields.get("Change class") == "image":
            assert "pytest -m image" in c.fields["Preconditions"], c.id
            assert "T-IMG-U4-1" in c.fields["Preconditions"], c.id
    post = _card("S5").fields["Post-checks"]
    order = ["DS-U4-1", "git merge-base --is-ancestor 0fd4cf6", "DS-U2-1 (a)", "102401",
             "exits 1", "LOG-GROUP-CONFIRM", "ENVELOPE-CONFIRM", "DS-U2-1 (b)"]
    positions = [post.find(token) for token in order]
    assert all(p >= 0 for p in positions), dict(zip(order, positions))
    assert positions == sorted(positions), dict(zip(order, positions))
    for c in _deploy_cards():
        assert "C5-PER-ARM" in c.fields["Smoke"] and "delphi-verify" in c.fields["Smoke"], c.id


# --- T-INF-U6-41 -------------------------------------------------------------------------
def test_t_inf_u6_41_reader_path_is_one_ordered_sequence():
    body = u6.section(_doc(), "Reader path (create mode)")
    steps = u6.numbered(body)
    order = ["Prerequisites", "cdk bootstrap", "head-bucket", "deploy Delphi-artifacts",
             "delphi-artifact build", "publish", "--execute", "verify-remote",
             "deploy Delphi-router", "delphi-verify"]
    at, pos = 0, []
    for token in order:
        hit = next(i for i in range(at, len(steps)) if token in steps[i])
        pos.append(hit)
        at = hit
    assert pos == sorted(pos)
    create = u6.synth(context={"artifactBucket": "create"})
    for stack in create.stack_names:
        assert f"deploy {stack}" in body, stack
    assert "valid only" in body and "serving_form" in body and "pointer" in body


# --- T-INF-U6-42 -------------------------------------------------------------------------
def test_t_inf_u6_42_no_deploy_all_anywhere_a_reader_looks():
    files = [*sorted((ROOT / "docs").rglob("*.md")), ROOT / "README.md", ROOT / "infra" / "app.py"]
    assert [str(f.relative_to(ROOT)) for f in files if "deploy --all" in f.read_text()] == []


# --- T-INF-U6-43 -------------------------------------------------------------------------
def test_t_inf_u6_43_readme_describes_the_u6_state():
    arms = _arms()
    readme = (ROOT / "README.md").read_text()
    status = readme.split("## Status", 1)[1].split("\n## ", 1)[0]
    for a in arms.ARMS:
        assert f"`{arms.stack_name(a)}`" in status, a
    assert "by name" in status
    for later in arms.NOT_YET_AVAILABLE:
        assert f"`{arms.stack_name(later)}`" not in status, later
    cost = readme.split("## Cost basis", 1)[1].split("\n## ", 1)[0]
    s3 = [s for s in re.split(r"(?<=[.!?])\s+", cost) if re.search(r"\bS3\b", s)]
    assert any("create" in s and "provision" in s for s in s3), s3
    assert any("existing" in s and "reference" in s for s in s3), s3


# --- T-INF-U6-45 -------------------------------------------------------------------------
def test_t_inf_u6_45_the_manual_path_is_documented_and_ci_never_deploys():
    assert len(u6.numbered(u6.section(_doc(), "Reader path (create mode)"))) >= 10
    assert len(u6.cards()) >= 7
    ci = importlib.import_module("test_ci_gate")
    assert ci.FORBIDDEN_CI.search("npx cdk deploy Delphi-router -c artifactBucket=existing")
    assert not ci.FORBIDDEN_CI.search("python -m pytest --cov -rfEs")


# --- T-INF-U6-46 -------------------------------------------------------------------------
def test_t_inf_u6_46_prerequisites_state_the_facts_readers_trip_on():
    body = u6.section(_doc(), "Prerequisites")
    assert re.search(r"aws-cdk@2\.\d+\.\d+", body) and "observed working" in body
    assert re.search(r"\b20\d\d-\d\d-\d\d\b", body)
    assert "environment-agnostic" in body and "diff header" in body
    assert "infra/.venv" in body and ".venv/bin/python3 app.py" in body
    assert "region you deploy" in body and "snapshot region" in body
    assert "[Model access](#model-access)" in body
    rows = u6.table(u6.section(_doc(), "Model access"))
    arms = _arms()
    assert [r["Arm"].strip("`") for r in rows] == list(arms.ARMS)
    for r, spec in zip(rows, arms.arm_specs()):
        assert re.findall(r"`([^`]+)`", r["Model ids"]) == list(spec.candidate_model_ids)


# --- T-INF-U6-57 -------------------------------------------------------------------------
def test_t_inf_u6_57_continuity_approval_gates_every_deploy():
    found = u6.cards()
    assert found[0].id == "G0" and "OQ6-ENTRY:G0:all:a" in found[0].fields["Approval"]
    assert "G0" in _card("S1").fields["Preconditions"]
    assert _deploy_cards()
    for c in _deploy_cards():
        stack = c.fields["Stack"]
        assert re.search(rf"CONTINUITY-APPROVAL:{c.id}:{re.escape(stack)}:a<n>",
                         c.fields["Approval"]), c.id
        assert c.order.index("Approval") < c.order.index("Command"), c.id
    items = u6.numbered(u6.section(_doc(), "P-DEPLOY"))
    ask = next(i for i, s in enumerate(items) if "CONTINUITY-APPROVAL" in s)
    deploy = next(i for i, s in enumerate(items) if s.split(".", 1)[1].strip().startswith(
        "Deploy"))
    assert ask < deploy


# --- T-INF-U6-61 -------------------------------------------------------------------------
def test_t_inf_u6_61_a_failed_rollback_is_a_stop():
    doc = _doc()
    assert "continue-update-rollback" not in doc and "resources-to-skip" not in doc
    pdeploy = u6.section(doc, "P-DEPLOY")
    assert "UPDATE_ROLLBACK_FAILED" in pdeploy and "stop" in pdeploy.lower()


# --- T-INF-U6-63 -------------------------------------------------------------------------
def test_t_inf_u6_63_retention_is_stated_and_set_on_the_service_log_group():
    body = " ".join(u6.section(_doc(), "Log retention").split())
    for phrase in ("90-day retention", "LogRetention", "deletes every event older",
                   "decision records", "not yet observed"):
        assert phrase in body, phrase
    for mode in ("existing", "create"):
        synth = u6.synth(context={"artifactBucket": mode})
        for stack in synth.stack_names:
            assert not synth.resources(stack, "AWS::Logs::LogGroup"), (mode, stack)
            if stack == "Delphi-artifacts":
                continue
            (lr,) = synth.resources(stack, "Custom::LogRetention").values()
            assert lr["Properties"]["RetentionInDays"] == 90, stack
            name = u6.render(lr["Properties"]["LogGroupName"], allow_getatt=True)
            assert name == ("/aws/bedrock-agentcore/runtimes/<GetAtt:Runtime.AgentRuntimeId>"
                            "-DEFAULT"), name


# --- T-INF-U6-64 -------------------------------------------------------------------------
def test_t_inf_u6_64_no_alarm_and_no_query_before_the_envelope_is_known():
    seeded = "```\nfields message.error_kind as kind\n| stats count(*) by kind\n```"
    assert "stats count(" in seeded, "control: the query shape the check looks for"
    envelope_recorded = any("ENVELOPE-CONFIRM" in p.read_text()
                            for p in (ROOT / "evidence" / "deploy").glob("*.json")) \
        if (ROOT / "evidence" / "deploy").exists() else False
    if not envelope_recorded:
        assert "stats count(" not in _doc()
    for mode in ("existing", "create"):
        synth = u6.synth(context={"artifactBucket": mode})
        for stack in synth.stack_names:
            for rtype in ("AWS::CloudWatch::Alarm", "AWS::CloudWatch::Dashboard"):
                assert not synth.resources(stack, rtype), (mode, stack, rtype)


# --- T-INF-U6-70 -------------------------------------------------------------------------
def test_t_inf_u6_70_pre_state_is_captured_before_the_first_diff():
    items = u6.numbered(u6.section(_doc(), "P-DEPLOY"))
    first = items[0]
    for phrase in ("sts get-caller-identity", "AgentRuntimeArn", "shell variable",
                   "never recorded", "CREATE_COMPLETE", "UPDATE_COMPLETE", "describe-images",
                   "unverified"):
        assert phrase in first, phrase
    diff_at = next(i for i, s in enumerate(items) if "cdk" in s and " diff " in f" {s} ")
    assert diff_at > 0


# --- T-INF-U6-72 -------------------------------------------------------------------------
def template_diff_problems(item: str) -> list[str]:
    return [p for p in ("--method=template", "change set", "bootstrap bucket") if p not in item]


def test_t_inf_u6_72_the_diff_is_a_template_diff():
    assert template_diff_problems("2. Run the cdk diff (recipe above, `diff`) and read it.") != []
    items = u6.numbered(u6.section(_doc(), "P-DEPLOY"))
    diff_item = next(s for s in items if "cdk" in s and " diff " in f" {s} ")
    assert template_diff_problems(" ".join(diff_item.split())) == []


@pytest.mark.author_record("evidence/deploy")
def test_t_inf_u6_72_the_s2_errata_note_corrects_the_first_diff():
    # The S2 record under-describes its first diff, and its files are bound to a receipt, so
    # the correction is an errata note beside them; the record and statement stay as written.
    deploy = ROOT / "evidence" / "deploy"
    errata = deploy / "S2-Delphi-pinned-nova-lite.errata.md"
    assert errata.is_file(), "the S2 errata note is missing"
    text = " ".join(errata.read_text().split())
    assert template_diff_problems(text) == []
    for phrase in ("first S2 diff", "S2-Delphi-pinned-nova-lite.json", "not read-only",
                   "later diffs"):
        assert phrase in text, phrase
    record = json.loads((deploy / "S2-Delphi-pinned-nova-lite.json").read_text())
    statement = (deploy / "S2-Delphi-pinned-nova-lite-a1.statement.md").read_bytes()
    assert hashlib.sha256(statement).hexdigest() == record["statement_sha256"]
    from delphi.tools.scrub_check import scan_blob

    assert scan_blob(errata.name, errata.read_bytes()) == [], "the errata note is not scrub-safe"


# ===================================================================== U6b part
TEMPLATES = u6.INFRA / "baseline" / "templates"
ROUTING = u6.INFRA / "profile_routing.json"


def _ticks(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def _as_list(x) -> list:
    return x if isinstance(x, list) else [x]


def _statements(template: dict) -> set[tuple]:
    out = set()
    for res in template["Resources"].values():
        if res["Type"] == "AWS::IAM::ManagedPolicy":
            for s in res["Properties"]["PolicyDocument"]["Statement"]:
                out.add((frozenset(_as_list(s["Action"])),
                         frozenset(u6.render(r) for r in _as_list(s["Resource"]))))
    return out


def _register(template: dict) -> set[tuple]:
    out = set()
    for res in template["Resources"].values():
        for s in res.get("Metadata", {}).get("cdk_nag", {}).get("rules_to_suppress", []):
            if res["Type"] == "AWS::IAM::ManagedPolicy":
                out.add((s["id"], tuple(s.get("applies_to", []))))
    return out


def _committed(label: str) -> dict[str, dict]:
    return {p.name.removesuffix(".template.json"): json.loads(p.read_text())
            for p in sorted((TEMPLATES / label).glob("*.template.json"))}


def _block(card: u6.Card) -> list[str]:
    """The card's derived expected diff: the backticked list after `Derived expected diff`.
    A list, not a fenced block, because its foundation-model lines name destination regions
    and no fenced block in this document may carry a region literal (T-INF-U6-47)."""
    marker = card.body.find("Derived expected diff")
    assert marker >= 0, card.id
    lines = re.findall(r"^- `(\[[+~-]\] [^`]+)`$", card.body[marker:], re.M)
    assert lines, card.id
    return lines


# --- T-INF-U6-19 -------------------------------------------------------------------------
def test_t_inf_u6_19_the_iam_tables_equal_the_template():
    arms = _arms()
    synth = u6.existing()
    rows = u6.table(u6.section(_doc(), "Execution policy statements"))
    sups = u6.table(u6.section(_doc(), "cdk-nag suppressions"))
    assert rows and sups
    for spec in arms.arm_specs():
        documented = {(frozenset(_ticks(r["Actions"])), frozenset(_ticks(r["Resources"])))
                      for r in rows if _ticks(r["Arm"]) == [spec.arm]}
        assert documented == _statements(synth.templates[spec.stack_name]), spec.arm
        listed = {(r["Rule"].strip("`"), tuple(_ticks(r["Applies to"])))
                  for r in sups if _ticks(r["Arm"]) == [spec.arm]}
        assert listed == _register(synth.templates[spec.stack_name]), spec.arm
        assert all(len(r["Reason"]) >= 20 for r in sups if _ticks(r["Arm"]) == [spec.arm])
    iam = u6.section(_doc(), "IAM")
    assert "scoped to the region" not in iam and "U6b replaces" not in iam


# --- T-INF-U6-36 -------------------------------------------------------------------------
def test_t_inf_u6_36_the_flip_is_gated_on_verify_remote_with_the_router_pool():
    synth = u6.existing()
    models_file = synth.runtime("Delphi-router")["EnvironmentVariables"]["DELPHI_MODELS_FILE"]
    pool = f"--pool config/{pathlib.PurePosixPath(models_file).name}"
    assert pool == "--pool config/models_realpool.yaml"
    s9 = _card("S9")
    lines = [ln for b in u6.code_blocks(s9.body) for ln in b.splitlines()
             if "delphi-artifact publish" in ln or "delphi-artifact verify-remote" in ln]
    assert len([ln for ln in lines if "publish" in ln]) == 2, lines
    assert any("verify-remote" in ln for ln in lines), lines
    assert all(pool in ln for ln in lines), lines
    ids = [c.id for c in u6.cards()]
    assert ids.index("S9") < ids.index("S10") < ids.index("S11")
    assert "serving_form" in _card("S10").fields["Command"]
    assert "verify-remote" in _card("S11").fields["Preconditions"]


# --- T-INF-U6-38 -------------------------------------------------------------------------
def test_t_inf_u6_38_the_flip_has_a_revert_and_keeps_the_legacy_prefix():
    s11 = _card("S11")
    assert "git revert" in s11.fields["Rollback"] and "U6c" in s11.fields["Rollback"]
    assert "legacy" in s11.fields["Rollback"] and "retained" in s11.fields["Rollback"]
    assert "artifact_hash" in s11.fields["Smoke"] and "verify-remote" in s11.fields["Smoke"]


# --- T-INF-U6-44 -------------------------------------------------------------------------
def test_t_inf_u6_44_model_access_names_what_each_arm_invokes():
    arms = _arms()
    synth = u6.existing()
    rows = u6.table(u6.section(_doc(), "Model access"))
    for spec in arms.arm_specs():
        (row,) = [r for r in rows if r["Arm"].strip("`") == spec.arm]
        ids, regions = set(), set()
        for actions, resources in _statements(synth.templates[spec.stack_name]):
            if "bedrock:InvokeModel" not in actions:
                continue
            for r in resources:
                m = re.fullmatch(r"arn:aws:bedrock:(<AWS::Region>|[a-z0-9-]+):"
                                 r"(?:<AWS::AccountId>)?:(foundation-model|inference-profile)/(.+)",
                                 r)
                assert m, r
                region, kind, mid = m.groups()
                if kind == "inference-profile":
                    ids.add(mid)
                elif region == "<AWS::Region>":
                    ids.add(mid)
                else:
                    regions.add(region)
        assert set(_ticks(row["Model ids"])) == ids, spec.arm
        assert set(_ticks(row["Destination regions"])) == regions, spec.arm


# --- T-INF-U6-55 -------------------------------------------------------------------------
def test_t_inf_u6_55_grant_steps_are_image_neutral():
    for cid in ("S7", "S8"):
        c = _card(cid)
        assert "IMAGE-NEUTRAL" in c.fields["Preconditions"], cid
        assert not any("ContainerUri" in ln for ln in _block(c)), cid


# --- T-INF-U6-58 -------------------------------------------------------------------------
def routing_date_problems(profiles: dict, records: list[dict]) -> list[str]:
    """A routing read date later than every recorded PROFILE-ROUTING-CONFIRM; [] when none."""
    confirmed = sorted(str(r.get("started_at") or r.get("date") or "")[:10] for r in records
                       if "PROFILE-ROUTING-CONFIRM" in (r.get("checks") or {}))
    latest = confirmed[-1] if confirmed else None
    out = []
    for pid, entry in profiles.items():
        date = entry["provenance"].get("read_date")
        if date is not None and (latest is None or date > latest):
            out.append(f"{pid}: read_date {date} after the latest confirmation {latest}")
    return out


@pytest.mark.author_record("evidence/deploy")
def test_t_inf_u6_58_grant_steps_wait_for_profile_routing_confirm():
    for cid in ("S7", "S8"):
        assert "PROFILE-ROUTING-CONFIRM" in _card(cid).fields["Preconditions"], cid
    stop = _card("S12").fields["Stop"]
    assert "AccessDeniedException" in stop and "revert" in stop and "nothing" in stop
    profiles = json.loads(ROUTING.read_text())["profiles"]
    deploy = ROOT / "evidence" / "deploy"
    records = [json.loads(p.read_text()) for p in sorted(deploy.glob("*.json"))] \
        if deploy.exists() else []
    assert routing_date_problems(profiles, records) == []
    seeded = {"p": {"provenance": {"read_date": "2026-10-21"}}}
    assert routing_date_problems(seeded, []), "control: a dated entry with no confirmation"


# --- T-INF-U6-59 -------------------------------------------------------------------------
def test_t_inf_u6_59_the_bucket_write_is_confirmed_first_in_real_output_terms():
    s9 = _card("S9")
    assert s9.fields["Credentials"] == "bucket_write"
    assert "BUCKET-WRITE-CONFIRM:S9:bucket:a<n>" in s9.fields["Approval"]
    impact = s9.fields["Expected diff"]
    for phrase in ("router/<hash>/", "router/current.json", "no existing key", "versioning",
                   "unknown"):
        assert phrase in impact, phrase
    (block,) = [b for b in u6.code_blocks(s9.body) if "--execute" in b]
    assert block.index("BUCKET-WRITE-CONFIRM") < block.index("--execute")
    stop = s9.fields["Stop"]
    publish = (ROOT / "src" / "delphi" / "train" / "publish.py").read_text()
    for term in ("would write pointer", "creates", "REPLACES", "present, identical",
                 "statement (scrub-safe):"):
        assert term in stop, term
        assert term in publish, f"{term!r} is not what the publisher prints"
    assert "--execute" not in s9.fields["Preconditions"]


# --- T-INF-U6-60 -------------------------------------------------------------------------
@pytest.mark.author_record("infra/baseline/templates/u6a")
def test_t_inf_u6_60_expected_diffs_are_derived_from_committed_templates():
    expected_diff = u6.infra_module("expected_diff")
    u6a, u6b, u6c = _committed("u6a"), _committed("u6b"), _committed("u6c-predicted")
    # S11 deployed (and recorded DIFF-MATCH against) u6c-deployed; u6c-predicted was rebaselined
    # afterwards for the nag reason text only (human decision 2026-10-20, no deploy).
    deployed = _committed("u6c-deployed")
    steps = {"S7": (u6a, u6b), "S8": (u6a, u6b), "S11": (u6b, deployed)}
    for cid, (before, after) in steps.items():
        c = _card(cid)
        stack = c.fields["Stack"]
        derived = expected_diff.block_lines(before[stack], after[stack])
        assert derived, cid
        assert _block(c) == derived, cid
        assert not any(ln.startswith(("[+] resource", "[-] resource")) for ln in derived), cid
        assert _block(c)[:-1] != derived, "control: a dropped line differs"
    assert "`u6c-deployed`" in _card("S11").body, "S11 names the set it was derived from"
    assert set(deployed) == set(u6c)
    for stack in u6c:
        assert metadata_only_problems(deployed[stack], u6c[stack]) == [], stack
    seeded = json.loads(json.dumps(u6c["Delphi-router"]))
    seeded["Resources"]["Runtime"]["Properties"]["Description"] += " drift"
    assert metadata_only_problems(deployed["Delphi-router"], seeded) != [], "control"
    frozen = json.loads(u6.BASELINE.read_text())["stacks"]
    for label, templates in (("u6a", u6a), ("u6b", u6b), ("u6c-deployed", deployed),
                             ("u6c-predicted", u6c)):
        for stack, entry in frozen.items():
            assert u6.identity_problems(templates[stack], entry) == [], (label, stack)


def test_t_inf_u6_60b_the_current_baseline_keeps_the_frozen_identity():
    """The committed u6c-predicted templates still hold the last-synthesized identity (logical
    ids, runtime names) frozen in infra/baseline/live_identity.json; runs without the author's
    historical template sets."""
    u6c = _committed("u6c-predicted")
    frozen = json.loads(u6.BASELINE.read_text())["stacks"]
    assert set(u6c) == set(frozen)
    for stack, entry in frozen.items():
        assert u6.identity_problems(u6c[stack], entry) == [], stack
    lost = json.loads(json.dumps(u6c["Delphi-router"]))
    del lost["Resources"]["RuntimeExecPolicy5BBDFEE5"]
    assert u6.identity_problems(lost, frozen["Delphi-router"]) != [], "control: a lost resource"


def metadata_only_problems(before: dict, after: dict) -> list[str]:
    """[] when `after` differs from `before` only in policy `cdk_nag` reason text: equal with
    every cdk_nag block removed, and the same (rule, applies_to) set on every resource."""
    def strip(t: dict) -> dict:
        t = json.loads(json.dumps(t))
        for res in t["Resources"].values():
            res.get("Metadata", {}).pop("cdk_nag", None)
        return t

    def register(t: dict) -> dict[str, set]:
        out = {}
        for lid, r in t["Resources"].items():
            rules = r.get("Metadata", {}).get("cdk_nag", {}).get("rules_to_suppress", [])
            out[lid] = {(s["id"], tuple(s.get("applies_to", []))) for s in rules}
        return out

    out = [] if strip(before) == strip(after) else ["a non-metadata path changed"]
    if register(before) != register(after):
        out.append("the suppressed findings changed")
    return out


# --- T-INF-U6-74 -------------------------------------------------------------------------
def _without_analytics(template: dict) -> dict:
    """The template minus what encodes this venv's library versions (the committed set is
    synthesized in infra/.venv): the CDK analytics string, and the code asset key and runtime
    of aws-cdk-lib's own LogRetention singleton function, which ship with the library. The
    committed set omits that asset key (a 64-hex name secret scanners misread as a key)."""
    t = json.loads(json.dumps(template))
    for lid, res in t.get("Resources", {}).items():
        if res.get("Type") == "AWS::CDK::Metadata":
            res.get("Properties", {}).pop("Analytics", None)
        if res.get("Type") == "AWS::Lambda::Function" and lid.startswith("LogRetention"):
            res.get("Properties", {}).get("Code", {}).pop("S3Key", None)
            res.get("Properties", {}).pop("Runtime", None)
            res.get("Metadata", {}).pop("aws:asset:path", None)
    return t


def test_t_inf_u6_74_the_current_synth_equals_the_committed_u6c_templates():
    committed = _committed("u6c-predicted")
    assert set(committed) == {"Delphi-router", "Delphi-pinned-nova-lite"}
    for mode in ("existing", "create"):
        synth = u6.synth(context={"artifactBucket": mode})
        for stack, template in committed.items():
            assert _without_analytics(synth.templates[stack]) == _without_analytics(template), \
                (mode, stack, "re-synthesize infra/baseline/templates/u6c-predicted in infra/.venv")
    seeded = json.loads(json.dumps(committed["Delphi-router"]))
    seeded["Resources"]["Runtime"]["Properties"]["Description"] += " drift"
    assert _without_analytics(seeded) != _without_analytics(committed["Delphi-router"])


# --- T-INF-U6-67 (runbook half) ----------------------------------------------------------
def test_t_inf_u6_67_runbook_s12_is_behind_spend_confirm():
    s12 = _card("S12")
    pre = s12.fields["Preconditions"]
    assert "SPEND-CONFIRM:S12:Delphi-router:a<n>" in s12.fields["Approval"]
    for phrase in ("226", "201", "no cost estimate", "afterwards", "cost_usd",
                   "tokens by model"):
        assert phrase in pre + s12.fields["Approval"], phrase
    assert s12.order.index("Approval") < s12.order.index("Command")


# --- T-INF-U6-68 -------------------------------------------------------------------------
def test_t_inf_u6_68_profile_routing_confirm_names_its_call_and_fields():
    body = u6.section(_doc(), "PROFILE-ROUTING-CONFIRM")
    assert "get-inference-profile" in body and "read-only" in body
    for field in ("models[].modelArn", "underlying_model_id", "ACTIVE", "SYSTEM_DEFINED",
                  "destination_regions"):
        assert field in body, field
    assert "inferenceProfileArn" in body and "never recorded" in body
    assert "read_date" in body


# --- T-INF-U6-69 -------------------------------------------------------------------------
def branch_problems(doc: str, excluded: bool) -> list[str]:
    cards = {c.id: c for c in u6.cards(doc)}
    stated = cards["S10"].fields.get("IMAGE-NEUTRAL branch", "")
    want = "accepted" if excluded else "declined"
    out = [] if stated.startswith(want) else [f"S10 states {stated[:30]!r}, .dockerignore {want}"]
    block = u6.section(doc, "IMAGE-NEUTRAL")
    if ".dockerignore" not in block or "expected_asset_hash_s10" not in block \
            or "override" not in block:
        out.append("IMAGE-NEUTRAL block does not describe the declined branch")
    if not excluded and "imageOverride:Delphi-router" not in cards["S11"].fields["Command"]:
        out.append("declined branch: S11 carries no override")
    return out


def test_t_inf_u6_69_the_image_neutral_branch_follows_dockerignore():
    doc = _doc()
    assert branch_problems(doc, u6.location_excluded()) == []
    assert branch_problems(doc, not u6.location_excluded()), "control: the other branch"


def test_u6_deploy_docs_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
