"""Per-arm execution grants — LC-U6-2 GrantBuilder (U6b; BR3.1-BR3.6, BR4.3; NFR9.1-NFR9.4,
NFR9.7, NFR9.8, NFR9.10; DD-U6-4, DD-U6-7; INF-U6-16).

Integration tier: every template here comes from the REAL `infra/app.py` app built through
`build_app` (u6_support.synth). Statements are selected by ACTION, never by Sid or position, and
every resource is RENDERED (u6_support.render) before it is matched, failing closed on an
unknown intrinsic. The expected grant set is derived here, independently of grants.py, from the
pool YAML's `candidates` and infra/profile_routing.json. No test makes an AWS call.

  T-INF-U6-10  Per arm, the model-invoke statements allow exactly `bedrock:InvokeModel` (no
               stream action) and their rendered resources equal the set derived from the arm's
               pool and the routing file: the inference profile in the deploy region per
               candidate, plus the foundation model in each destination region. No resource
               contains '*'; the pool's `judge` id never appears. Regression cases, each of which
               must be caught: (a) an Fn::Join with a literal part `foundation-model/*`, (b) an
               Fn::Sub ending `inference-profile/*`, (c) a plain "*", (d) an unknown intrinsic,
               which raises RenderError (BR3.1, NFR9.1).
  T-INF-U6-11  Every profile id in every arm's pool has a routing entry whose underlying model is
               the id without its geography prefix, with non-empty destination regions and
               provenance {source, read_date, read_by: "operator"}; the grant covers exactly
               those regions; a pool fixture with an unlisted profile fails synth with GrantError
               naming it; over the nine-arm list every arm either gets a grant or fails loudly
               naming itself (BR3.2, NFR9.2, NFR9.10).
  T-INF-U6-12  In both bucket modes the router template has exactly one s3:GetObject statement,
               on `arn:aws:s3:::<bucket>/router/*` (C6 grant_prefix); the pinned template has no
               S3 statement at all (BR3.3, NFR9.4).
  T-INF-U6-13  runtime_stack.py says neither "scoped to the region" nor an unqualified "scoped to
               the CDK asset repository"; it names the shared asset repository. Control: the U6a
               comment text is caught (BR3.4, NFR9.9).
  T-INF-U6-14  The logs actions are scoped to the AgentCore runtime log groups:
               grants.NARROW_LOGS is True with its provenance (documentation page, live check);
               each arm's logs statement grants exactly the runtime log-group prefix and its log
               streams, no logs action is on '*', and the LogRetention grant has that prefix plus
               the function's own `/aws/lambda/<stack>-*` log group;
               the policy's logs suppression names exactly those two findings. grants.py, nag.py,
               runtime_stack.py and the docs' suppression section no longer state the old
               logs-on-'*' decision; grant_plan still returns '*' for no pattern (BR3.5, NFR9.7;
               decision 2026-10-28, reversing 2026-10-20).
  T-INF-U6-15  Each runtime role's AgentCore trust statement carries exactly the documented
               confused-deputy conditions, aws:SourceAccount (StringEquals) and aws:SourceArn
               (ArnLike, this account and region), named in grants.TRUST_CONDITION_KEYS with
               provenance; a key set without provenance is refused, and with provenance it
               yields exactly those keys (BR3.6, NFR9.8).
  T-INF-U6-18  In create mode every resource of every policy statement falls into exactly one
               class: (a) provisioned by this app, (b) AWS-owned model ARN, (c) the bootstrap
               asset repository, (d) '*' with an IAM5 suppression, (e) the existing-mode bucket,
               (f) the AgentCore runtime log groups and their streams, by documented prefix;
               class (e) is empty in create mode and holds the router's grant in existing mode.
               Control: a foreign bucket is unclassified (BR4.3, AC1.2.1).
  guard        the autouse socket guard is active.

Partition: aws-cdk-lib + Node.js (synth), stdlib, yaml. Fails, never skips, without them.
"""
from __future__ import annotations

import copy
import json
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest
import yaml

import u6_support as u6

ROOT = u6.REPO
ROUTING = u6.INFRA / "profile_routing.json"
REGION, ACCOUNT = "<AWS::Region>", "<AWS::AccountId>"
GEO = re.compile(r"^(us|eu|apac|ca|jp|au|us-gov|global)\.")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_infra_grants.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _arms():
    return u6.infra_module("arms")


def _routing() -> dict:
    return json.loads(ROUTING.read_text())["profiles"]


def _actions(stmt: dict) -> list[str]:
    a = stmt["Action"]
    return a if isinstance(a, list) else [a]


def _resources(stmt: dict) -> list:
    r = stmt["Resource"]
    return r if isinstance(r, list) else [r]


def _policy_statements(template: dict) -> list[dict]:
    out = []
    for res in template["Resources"].values():
        if res["Type"] == "AWS::IAM::ManagedPolicy":
            out += res["Properties"]["PolicyDocument"]["Statement"]
    return out


def _policy_suppressions(template: dict) -> list[dict]:
    out = []
    for res in template["Resources"].values():
        if res["Type"] == "AWS::IAM::ManagedPolicy":
            out += res.get("Metadata", {}).get("cdk_nag", {}).get("rules_to_suppress", [])
    return out


def _expected_model_resources(spec) -> set[str]:
    """Derived here from the pool YAML and the routing file, not from grants.py."""
    pool = yaml.safe_load((ROOT / spec.pool_file).read_text())
    ids = [c["id"] for c in pool["candidates"]]
    if spec.pinned_alias is not None:
        ids = [c["id"] for c in pool["candidates"] if c["alias"] == spec.pinned_alias]
    assert tuple(ids) == spec.candidate_model_ids
    routing, out = _routing(), set()
    for mid in ids:
        if GEO.match(mid):
            entry = routing[mid]
            out.add(f"arn:aws:bedrock:{REGION}:{ACCOUNT}:inference-profile/{mid}")
            out |= {f"arn:aws:bedrock:{r}::foundation-model/{entry['underlying_model_id']}"
                    for r in entry["destination_regions"]}
        else:
            out.add(f"arn:aws:bedrock:{REGION}::foundation-model/{mid}")
    return out


def model_invoke_problems(template: dict, expected: set[str]) -> list[str]:
    """What is wrong with a template's model-invoke grant; [] when it is exactly `expected`.
    Raises u6.RenderError on an unknown intrinsic (fail closed)."""
    stmts = [s for s in _policy_statements(template)
             if any(a.startswith("bedrock:Invoke") for a in _actions(s))]
    problems, rendered = [], set()
    for s in stmts:
        if set(_actions(s)) != {"bedrock:InvokeModel"}:
            problems.append(f"actions {sorted(_actions(s))}")
        for r in _resources(s):
            text = u6.render(r)
            if "*" in text:
                problems.append(f"wildcard resource {text}")
            rendered.add(text)
    if rendered != expected:
        problems.append(f"resources {sorted(rendered ^ expected)} differ from the derivation")
    return problems


# --- T-INF-U6-10 -------------------------------------------------------------------------
def test_t_inf_u6_10_each_arm_invokes_only_its_candidates():
    arms = _arms()
    synth = u6.existing()
    judge = yaml.safe_load((ROOT / arms.ROUTER_POOL).read_text())["judge"]["id"]
    for spec in arms.arm_specs():
        template = synth.templates[spec.stack_name]
        expected = _expected_model_resources(spec)
        assert model_invoke_problems(template, expected) == [], spec.arm
        assert judge not in json.dumps(template), f"{spec.arm}: the judge reached a grant"
    router = synth.templates["Delphi-router"]
    expected = _expected_model_resources(arms.arm_spec("router"))

    def with_extra(resource):
        t = copy.deepcopy(router)
        for s in _policy_statements(t):
            if "bedrock:InvokeModel" in _actions(s):
                s["Resource"] = [*_resources(s), resource]
        return t

    cases = {
        "a": {"Fn::Join": ["", ["arn:aws:bedrock:", {"Ref": "AWS::Region"},
                                "::foundation-model/*"]]},
        "b": {"Fn::Sub": "arn:aws:bedrock:${AWS::Region}:${AWS::AccountId}:inference-profile/*"},
        "c": "*",
    }
    for name, resource in cases.items():
        assert model_invoke_problems(with_extra(resource), expected), f"regression case {name}"
    with pytest.raises(u6.RenderError):
        model_invoke_problems(with_extra({"Fn::ImportValue": "x"}), expected)  # case d


# --- T-INF-U6-11 -------------------------------------------------------------------------
def test_t_inf_u6_11_profiles_are_routed_with_provenance(tmp_path):
    arms, grants = _arms(), u6.infra_module("grants")
    routing = _routing()
    synth = u6.existing()
    for spec in arms.arm_specs():
        rendered = set()
        for s in _policy_statements(synth.templates[spec.stack_name]):
            if "bedrock:InvokeModel" in _actions(s):
                rendered |= {u6.render(r) for r in _resources(s)}
        for mid in spec.candidate_model_ids:
            if not GEO.match(mid):
                continue
            entry = routing[mid]
            assert entry["underlying_model_id"] == mid.split(".", 1)[1], mid
            assert entry["destination_regions"], mid
            prov = entry["provenance"]
            assert {"source", "read_date", "read_by"} <= set(prov), mid
            assert prov["source"] and prov["read_by"] == "operator", mid
            granted = {r.split(":")[3] for r in rendered
                       if r.endswith(f":foundation-model/{entry['underlying_model_id']}")}
            assert granted == set(entry["destination_regions"]), mid
    tree = u6.scratch_tree(tmp_path / "tree")
    pool_path = tree / arms.ROUTER_POOL
    pool = yaml.safe_load(pool_path.read_text())
    pool["candidates"].append({"alias": "unlisted", "id": "us.example.unlisted-v1:0",
                               "family": "x", "price_per_1k": {"input": 0.0, "output": 0.0}})
    pool_path.write_text(yaml.safe_dump(pool))
    with pytest.raises(grants.GrantError, match=re.escape("us.example.unlisted-v1:0")):
        u6.synth(context={"artifactBucket": "existing"}, build_root=tree)
    failed = []
    for arm in u6.NINE_ARMS:
        try:
            grants.grant_plan(arms.arm_spec(arm), routing=grants.load_routing(),
                              region=REGION, account=ACCOUNT,
                              bucket=u6.location_bucket())
        except grants.GrantError as exc:
            assert arm in str(exc), (arm, str(exc))
            failed.append(arm)
    assert failed == ["ipr"], failed


# --- T-INF-U6-12 -------------------------------------------------------------------------
def test_t_inf_u6_12_only_the_router_reads_and_only_router_prefix():
    for mode in ("existing", "create"):
        synth = u6.synth(context={"artifactBucket": mode})
        s3 = [s for s in _policy_statements(synth.templates["Delphi-router"])
              if any(a.startswith("s3:") for a in _actions(s))]
        assert len(s3) == 1 and _actions(s3[0]) == ["s3:GetObject"], (mode, s3)
        assert [u6.render(r) for r in _resources(s3[0])] == [
            f"arn:aws:s3:::{u6.location_bucket()}/router/*"], mode
        pinned = [s for s in _policy_statements(synth.templates["Delphi-pinned-nova-lite"])
                  if any(a.startswith("s3:") for a in _actions(s))]
        assert pinned == [], mode


# --- T-INF-U6-13 -------------------------------------------------------------------------
def comment_problems(text: str) -> list[str]:
    out = []
    if "scoped to the region" in text:
        out.append("'scoped to the region'")
    for m in re.finditer(r"scoped to the CDK asset repository", text):
        if "shared" not in text[max(0, m.start() - 40):m.end() + 40]:
            out.append("unqualified 'scoped to the CDK asset repository'")
    return out


def test_t_inf_u6_13_the_comments_tell_the_truth():
    text = (u6.INFRA / "delphi_infra" / "runtime_stack.py").read_text()
    assert comment_problems(text) == []
    assert "shared" in text and "asset repository" in text
    old = ("# Invoke foundation models (Converse) + embeddings, scoped to the region.\n"
           "# image-layer reads are scoped to the CDK asset repository.")
    assert len(comment_problems(old)) == 2, "control: the U6a comments are caught"


# --- T-INF-U6-14 -------------------------------------------------------------------------
LOGS = {"logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"}


#: Wording of the superseded logs-on-'*' decision; no site may still state it.
OLD_DECISION_PHRASES = ("logs stay on *", "logs stay on `*`", "stays on `*`, by decision",
                        "narrowing was deliberately not applied", "until LOG-GROUP-CONFIRM")
_PREFIX = "/aws/bedrock-agentcore/runtimes/*"


def old_decision_problems(where: str, text: str) -> list[str]:
    flat = " ".join(text.split())
    return [f"{where}: still says {p!r}" for p in OLD_DECISION_PHRASES if p in flat]


def test_t_inf_u6_14_logs_are_scoped_to_the_runtime_log_groups():
    arms, grants = _arms(), u6.infra_module("grants")
    assert grants.NARROW_LOGS is True
    assert "runtime-permissions" in grants.LOGS_PROVENANCE["page"]
    assert "live" in grants.LOGS_PROVENANCE["verified"]
    assert grants.CONFIRMED_LOG_GROUP_PATTERN.startswith(grants.RUNTIME_LOG_GROUP_PREFIX)
    group = f"arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:{_PREFIX}"
    synth = u6.existing()
    for spec in arms.arm_specs():
        t = synth.templates[spec.stack_name]
        logs = [s for s in _policy_statements(t) if LOGS & set(_actions(s))]
        assert len(logs) == 1 and set(_actions(logs[0])) == LOGS, spec.arm
        assert sorted(u6.render(r) for r in _resources(logs[0])) == \
            [group, group + ":log-stream:*"], spec.arm
        assert not any("*" == u6.render(r) for s in _policy_statements(t)
                       if set(_actions(s)) & LOGS for r in _resources(s)), spec.arm
        retention = [r for r in t["Resources"].values() if r["Type"] == "AWS::IAM::Policy"]
        (statement,) = [s for r in retention
                        for s in r["Properties"]["PolicyDocument"]["Statement"]]
        own = (f"arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:"
               f"/aws/lambda/{spec.stack_name}-*")
        assert [u6.render(r) for r in statement["Resource"]] == [group, own], spec.arm
        scoped = [x for x in _policy_suppressions(t) if x["id"] == "AwsSolutions-IAM5"
                  and any("log-group" in a for a in x.get("applies_to", []))]
        assert len(scoped) == 1 and sorted(scoped[0]["applies_to"]) == \
            [f"Resource::{group}", f"Resource::{group}:log-stream:*"], spec.arm
    sites = {"infra/delphi_infra/grants.py": (u6.INFRA / "delphi_infra" / "grants.py").read_text(),
             "infra/delphi_infra/nag.py": (u6.INFRA / "delphi_infra" / "nag.py").read_text(),
             "infra/delphi_infra/runtime_stack.py":
                 (u6.INFRA / "delphi_infra" / "runtime_stack.py").read_text(),
             "docs/deployment.md cdk-nag suppressions":
                 u6.section((ROOT / "docs" / "deployment.md").read_text(), "cdk-nag suppressions")}
    assert [p for where, text in sites.items() for p in old_decision_problems(where, text)] == []
    assert old_decision_problems("seed", "logs stay on * by decision") != [], "control"
    plan = grants.grant_plan(arms.arm_spec("pinned:nova-lite"), routing=grants.load_routing(),
                             region=REGION, account=ACCOUNT, bucket=u6.location_bucket(),
                             log_group_pattern=None)
    assert plan.logs == ("*",), "no pattern still means '*'"


# --- T-INF-U6-15 -------------------------------------------------------------------------
def _agentcore_trust(role: dict) -> list[dict]:
    return [s for s in role["Properties"]["AssumeRolePolicyDocument"]["Statement"]
            if s["Principal"].get("Service") == "bedrock-agentcore.amazonaws.com"]


def test_t_inf_u6_15_trust_conditions_carry_the_documented_keys():
    grants = u6.infra_module("grants")
    assert grants.TRUST_CONDITION_KEYS == ("aws:SourceAccount", "aws:SourceArn")
    assert "runtime-permissions" in grants.TRUST_CONDITION_PROVENANCE["page"]
    synth = u6.existing()
    for stack in synth.stack_names:
        (statement,) = [s for role in synth.resources(stack, "AWS::IAM::Role").values()
                        for s in _agentcore_trust(role)]
        cond = statement["Condition"]
        assert set(cond) == {"StringEquals", "ArnLike"}, stack
        assert u6.render(cond["StringEquals"]["aws:SourceAccount"]) == "<AWS::AccountId>"
        assert u6.render(cond["ArnLike"]["aws:SourceArn"]) == \
            "arn:aws:bedrock-agentcore:<AWS::Region>:<AWS::AccountId>:*", stack
    with pytest.raises(grants.GrantError, match="provenance"):
        grants.trust_conditions(("aws:SourceAccount",), None, region=REGION, account=ACCOUNT)
    got = grants.trust_conditions(("aws:SourceAccount",), {"page": "p", "read_date": "d"},
                                  region=REGION, account=ACCOUNT)
    assert {k for cond in got.values() for k in cond} == {"aws:SourceAccount"}
    assert grants.trust_conditions((), None, region=REGION, account=ACCOUNT) == {}


# --- T-INF-U6-18 -------------------------------------------------------------------------
def classify(resource: str, template: dict, all_templates: dict, mode: str) -> str | None:
    """The BR4.3 class of one rendered resource, or None (unclassified)."""
    image = u6.infra_module("image")
    repo = image.asset_repository_name(_FakeScope()).replace("${AWS::AccountId}", ACCOUNT) \
        .replace("${AWS::Region}", REGION)
    m = re.fullmatch(r"<GetAtt:([^.>]+)\.[^>]+>(/.*)?", resource)
    if m and m.group(1) in template["Resources"]:
        return "a"
    if re.fullmatch(r"arn:aws:bedrock:(<AWS::Region>|[a-z0-9-]+):(<AWS::AccountId>)?:"
                    r"(foundation-model|inference-profile)/[^*]+", resource):
        return "b"
    if resource == f"arn:<AWS::Partition>:ecr:{REGION}:{ACCOUNT}:repository/{repo}":
        return "c"
    if re.fullmatch(r"arn:aws:logs:<AWS::Region>:<AWS::AccountId>:log-group:"
                    r"/aws/bedrock-agentcore/runtimes/\*(:log-stream:\*)?", resource):
        return "f"
    if resource == "*":
        sup = [x for x in _policy_suppressions(template) if x["id"] == "AwsSolutions-IAM5"
               and "Resource::*" in x.get("applies_to", [])]
        return "d" if sup else None
    s3 = re.fullmatch(r"arn:aws:s3:::([^/]+)(/.*)?", resource)
    if s3:
        created = {u6.render(b["Properties"]["BucketName"])
                   for t in all_templates.values() for b in t["Resources"].values()
                   if b["Type"] == "AWS::S3::Bucket" and "BucketName" in b["Properties"]}
        if s3.group(1) in created:
            return "a"
        if mode == "existing" and s3.group(1) == u6.location_bucket():
            return "e"
    return None


class _FakeScope:
    class node:  # noqa: N801 - mimics a construct's `node`
        @staticmethod
        def try_get_context(_key):
            return None


def _all_policy_resources(templates: dict):
    for stack, t in templates.items():
        for res in t["Resources"].values():
            if res["Type"] in {"AWS::IAM::ManagedPolicy", "AWS::S3::BucketPolicy"}:
                for s in res["Properties"]["PolicyDocument"]["Statement"]:
                    for r in _resources(s):
                        yield stack, t, u6.render(r, allow_getatt=True)


def test_t_inf_u6_18_every_granted_resource_is_accounted_for():
    for mode in ("create", "existing"):
        synth = u6.synth(context={"artifactBucket": mode})
        classes = []
        for stack, t, r in _all_policy_resources(synth.templates):
            c = classify(r, t, synth.templates, mode)
            assert c is not None, (mode, stack, r)
            classes.append((stack, c))
        if mode == "create":
            assert all(c != "e" for _, c in classes)
            assert ("Delphi-router", "a") in classes
        else:
            assert ("Delphi-router", "e") in classes
    t = u6.existing().templates["Delphi-router"]
    assert classify("arn:aws:s3:::someone-elses-bucket/*", t, {"x": t}, "create") is None


# --- guard -------------------------------------------------------------------------------
def test_u6_infra_grants_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
