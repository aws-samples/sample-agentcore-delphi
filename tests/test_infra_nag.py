"""cdk-nag at every synth — LC-U6-4 NagAspect (U6b; BR4.1, BR4.2 as amended by NFR Design
delta 6; NFR9.4-NFR9.6; DD-U6-1; INF-U6-14).

Integration tier: the REAL app through `build_app`, in both bucket modes, with the AwsSolutions
pack applied by the app itself. Findings are read from the cloud assembly's manifest (error
metadata) and from the nag report the pack writes into the assembly; suppressions are read from
each resource's `cdk_nag.rules_to_suppress` template metadata (probe (d)). No AWS call.

  T-INF-U6-16  In both modes no stack carries an unsuppressed AwsSolutions error, and the pack
               really ran on every stack: each has a nag report, and the report lists the IAM5
               rows as suppressed. Control: a scratch app with an unsuppressed `Resource: *`
               statement under the same Aspect is reported (BR4.1).
  T-INF-U6-17  Every suppression on every resource names one rule id, has a reason of at least
               20 characters, and, for IAM5, a non-empty literal `applies_to`; none covers the
               model-invoke statement (no entry names a Bedrock model ARN or a rendered model-invoke
               resource); the router policy's artifact-read suppression set is exactly DD-U6-1's
               one entry, `Resource::arn:aws:s3:::<bucket>/router/*` with the bucket from
               config/artifact-location.json (BR4.2, NFR Design delta 6c).
  T-INF-U6-65  Over both modes the register rules hold: no IAM5 suppression without
               `applies_to`, none with a regular expression, none naming a Bedrock model ARN
               (`arn:aws:bedrock:`) or `bedrock:Invoke`, no stack-level suppression; the router
               policy's IAM5 set is exactly {the artifact finding, `Resource::*`, the two logs
               findings}; the pinned policy's is exactly {`Resource::*`, the two logs findings}.
               Controls: a bare IAM5 entry, a regex entry and a stack-level entry are each
               reported (DD-U6-1, NFR9.5, NFR9.6).
  T-INF-U6-73  After minimizePolicies merges them, each arm's policy has exactly one statement
               on '*', in both modes and in the committed u6c-predicted templates, and its
               action set is exactly nag.STAR_ACTIONS (written out again here); every one of
               those actions is named by the policy's `Resource::*` reason, which is ONE entry
               per policy (nag.STAR_ENTRIES has one), and docs/deployment.md lists one
               `Resource::*` row per arm. cdk-nag matches the finding string, not the action, so
               this test is what fails when an action joins that statement. Controls: an added
               action, and a second '*' statement, are each reported (U6 review R-02).
  guard        the autouse socket guard is active.

Partition: aws-cdk-lib + cdk-nag + Node.js (synth), stdlib. Fails, never skips, without them.
"""
from __future__ import annotations

import copy
import json
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest

import u6_support as u6

MODES = ("existing", "create")
IAM5 = "AwsSolutions-IAM5"


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_infra_nag.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _artifact_finding() -> str:
    return f"Resource::arn:aws:s3:::{u6.location_bucket()}/router/*"


#: The two findings of the scoped logs grant, written here independently of nag.py.
_LOGS = ("Resource::arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:"
         "/aws/bedrock-agentcore/runtimes/*")
LOGS_FINDINGS = frozenset({_LOGS, _LOGS + ":log-stream:*"})


def _suppressions(template: dict):
    """(logical id, type, suppression) for every resource-level suppression."""
    for lid, res in template["Resources"].items():
        for s in res.get("Metadata", {}).get("cdk_nag", {}).get("rules_to_suppress", []):
            yield lid, res["Type"], s


def _iam5_set(template: dict) -> set[str]:
    return {a for _, rtype, s in _suppressions(template)
            if rtype == "AWS::IAM::ManagedPolicy" and s["id"] == IAM5
            for a in s.get("applies_to", []) if isinstance(a, str)}


def register_problems(stack: str, template: dict) -> list[str]:
    """The T-INF-U6-65 register rules over one template; [] when every rule holds."""
    out = []
    if "cdk_nag" in template.get("Metadata", {}):
        out.append("stack-level suppression")
    for lid, _rtype, s in _suppressions(template):
        if s["id"] == IAM5:
            applies = s.get("applies_to")
            if not applies:
                out.append(f"{lid}: IAM5 without applies_to")
                continue
            if any(not isinstance(a, str) for a in applies):
                out.append(f"{lid}: IAM5 applies_to holds a regular expression")
            # A Bedrock model ARN or action; the bedrock-agentcore log-group path is not one.
            if any("arn:aws:bedrock:" in str(a) or "bedrock:Invoke" in str(a) for a in applies):
                out.append(f"{lid}: applies_to names bedrock")
    iam5 = _iam5_set(template)
    if stack == "Delphi-router" and iam5 != {_artifact_finding(), "Resource::*", *LOGS_FINDINGS}:
        out.append(f"router IAM5 set {sorted(iam5)}")
    if stack == "Delphi-pinned-nova-lite" and iam5 != {"Resource::*", *LOGS_FINDINGS}:
        out.append(f"pinned IAM5 set {sorted(iam5)}")
    if stack == "Delphi-pinned-nova-lite" and _artifact_finding() in iam5:
        out.append("pinned policy suppresses the artifact finding")
    return out


# --- T-INF-U6-16 -------------------------------------------------------------------------
def _report_rows(outdir, stack: str) -> list[dict]:
    reports = [p for p in outdir.glob("*NagReport*.json") if stack in p.name]
    assert len(reports) == 1, (stack, sorted(p.name for p in outdir.iterdir()))
    data = json.loads(reports[0].read_text())
    return data["lines"] if isinstance(data, dict) else data


def test_t_inf_u6_16_no_unsuppressed_error_in_either_mode(tmp_path):
    nag = u6.infra_module("nag")
    for mode in MODES:
        synth = u6.synth(context={"artifactBucket": mode})
        assert nag.unsuppressed_errors(synth.outdir) == [], mode
        for stack in synth.stack_names:
            rows = _report_rows(synth.outdir, stack)
            assert rows, (mode, stack)
            if stack != "Delphi-artifacts":
                iam5 = [r["compliance"] for r in rows if r["ruleId"] == IAM5]
                assert "Suppressed" in iam5 and "Non-Compliant" not in iam5, (mode, stack, iam5)

    import aws_cdk as cdk
    from aws_cdk import aws_iam as iam

    app = cdk.App(outdir=str(tmp_path / "control"))
    stack = cdk.Stack(app, "Control")
    iam.ManagedPolicy(stack, "Wide", statements=[
        iam.PolicyStatement(actions=["s3:GetObject"], resources=["*"])])
    nag.apply(app)
    app.synth()
    errors = nag.unsuppressed_errors(tmp_path / "control")
    assert any(e.startswith(IAM5) for e in errors), errors


# --- T-INF-U6-17 -------------------------------------------------------------------------
def _model_invoke_findings(template: dict) -> set[str]:
    out = set()
    for res in template["Resources"].values():
        if res["Type"] != "AWS::IAM::ManagedPolicy":
            continue
        for s in res["Properties"]["PolicyDocument"]["Statement"]:
            actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
            if any(a.startswith("bedrock:Invoke") for a in actions):
                rs = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
                out |= {f"Resource::{u6.render(r)}" for r in rs}
    return out


def test_t_inf_u6_17_every_suppression_is_specific_and_reasoned():
    for mode in MODES:
        synth = u6.synth(context={"artifactBucket": mode})
        for stack, template in synth.templates.items():
            invoke = _model_invoke_findings(template)
            for lid, _rtype, s in _suppressions(template):
                assert s["id"].startswith("AwsSolutions-") and "[" not in s["id"], (stack, s)
                assert len(s["reason"]) >= 20, (stack, lid, s)
                if s["id"] == IAM5:
                    assert s.get("applies_to"), (stack, lid)
                    assert not set(s["applies_to"]) & invoke, (stack, lid)
            if stack == "Delphi-router":
                s3 = [s for _, _, s in _suppressions(template)
                      if any("s3:::" in str(a) for a in s.get("applies_to", []))]
                assert len(s3) == 1 and s3[0]["id"] == IAM5, (mode, s3)
                assert s3[0]["applies_to"] == [_artifact_finding()], (mode, s3)
                assert "router/*" in s3[0]["reason"] and "router arm only" in s3[0]["reason"]


# --- T-INF-U6-65 -------------------------------------------------------------------------
def test_t_inf_u6_65_the_register_rules_hold_in_both_modes():
    for mode in MODES:
        synth = u6.synth(context={"artifactBucket": mode})
        for stack, template in synth.templates.items():
            assert register_problems(stack, template) == [], (mode, stack)
    router = u6.existing().templates["Delphi-router"]
    policy = next(lid for lid, r in router["Resources"].items()
                  if r["Type"] == "AWS::IAM::ManagedPolicy")

    def seeded(entry=None, stack_level=False):
        t = copy.deepcopy(router)
        if entry is not None:
            t["Resources"][policy]["Metadata"]["cdk_nag"]["rules_to_suppress"].append(entry)
        if stack_level:
            t.setdefault("Metadata", {})["cdk_nag"] = {"rules_to_suppress": [
                {"id": IAM5, "reason": "x" * 30}]}
        return t

    assert register_problems("Delphi-router", seeded({"id": IAM5, "reason": "x" * 30}))
    assert register_problems("Delphi-router", seeded(
        {"id": IAM5, "reason": "x" * 30, "applies_to": [{"regex": "/^Resource::.*$/"}]}))
    assert register_problems("Delphi-router", seeded(stack_level=True))


# --- T-INF-U6-73 -------------------------------------------------------------------------
#: The merged `Resource: "*"` statement's actions, written here independently of nag.py.
STAR_ACTIONS = frozenset({
    "cloudwatch:PutMetricData", "ecr:GetAuthorizationToken", "xray:PutTelemetryRecords",
    "xray:PutTraceSegments"})


def star_problems(stack: str, template: dict) -> list[str]:
    """[] when the template's '*' resources sit in exactly one statement holding exactly
    STAR_ACTIONS, and every one of those actions is named by the policy's `Resource::*`
    suppression reasons."""
    out, stars = [], []
    for res in template["Resources"].values():
        if res["Type"] != "AWS::IAM::ManagedPolicy":
            continue
        for s in res["Properties"]["PolicyDocument"]["Statement"]:
            rs = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
            if any(u6.render(r) == "*" for r in rs):
                stars.append(s)
    if len(stars) != 1:
        return [f"{stack}: {len(stars)} statements on '*'"]
    (star,) = stars
    actions = star["Action"] if isinstance(star["Action"], list) else [star["Action"]]
    if set(actions) != STAR_ACTIONS:
        out.append(f"{stack}: '*' actions {sorted(set(actions) ^ STAR_ACTIONS)} differ")
    reasons = " ".join(s["reason"] for _, rtype, s in _suppressions(template)
                       if rtype == "AWS::IAM::ManagedPolicy" and s["id"] == IAM5
                       and s.get("applies_to") == ["Resource::*"])
    out += [f"{stack}: no '*' reason names {a}" for a in sorted(STAR_ACTIONS)
            if a.split(":", 1)[1] not in reasons]
    return out


def test_t_inf_u6_73_the_star_statement_holds_exactly_its_named_actions():
    nag = u6.infra_module("nag")
    assert nag.STAR_ACTIONS == STAR_ACTIONS, "the register's action set and the test's differ"
    committed = u6.INFRA / "baseline" / "templates" / "u6c-predicted"
    assert len(nag.STAR_ENTRIES) == 1, "one reason for the one merged '*' statement"
    for mode in MODES:
        synth = u6.synth(context={"artifactBucket": mode})
        for stack in ("Delphi-router", "Delphi-pinned-nova-lite"):
            assert star_problems(stack, synth.templates[stack]) == [], (mode, stack)
            star = [s for _, rtype, s in _suppressions(synth.templates[stack])
                    if rtype == "AWS::IAM::ManagedPolicy"
                    and s.get("applies_to") == ["Resource::*"]]
            assert len(star) == 1, (mode, stack, len(star))
    doc = (u6.REPO / "docs" / "deployment.md").read_text()
    rows = u6.table(u6.section(doc, "cdk-nag suppressions"))
    for arm in ("router", "pinned:nova-lite"):
        star_rows = [r for r in rows if r["Arm"].strip("`") == arm
                     and r["Applies to"].strip("`") == "Resource::*"]
        assert len(star_rows) == 1, (arm, len(star_rows))
    for path in sorted(committed.glob("*.template.json")):
        assert star_problems(path.name, json.loads(path.read_text())) == [], path.name
    # Controls: an action added to the '*' statement, and a second '*' statement.
    router = u6.existing().templates["Delphi-router"]
    policy = next(lid for lid, r in router["Resources"].items()
                  if r["Type"] == "AWS::IAM::ManagedPolicy")
    added = copy.deepcopy(router)
    for s in added["Resources"][policy]["Properties"]["PolicyDocument"]["Statement"]:
        if s["Resource"] == "*":
            s["Action"] = [*s["Action"], "s3:GetObject"]
    assert star_problems("seed", added) == ["seed: '*' actions ['s3:GetObject'] differ"]
    second = copy.deepcopy(router)
    second["Resources"][policy]["Properties"]["PolicyDocument"]["Statement"].append(
        {"Action": "s3:GetObject", "Effect": "Allow", "Resource": "*"})
    assert star_problems("seed", second) == ["seed: 2 statements on '*'"]


# --- guard -------------------------------------------------------------------------------
def test_u6_infra_nag_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
