"""The U6 stack model — LC-U6-1 StackModel and LC-U6-3 ImageOverride (U6a; FR2.1, FR1.3,
FR8.2, FR11.1; NFR8.1-NFR8.4, NFR8.9, NFR8.13, NFR8.14; C4, C6).

Integration tier: every synth here builds the REAL `infra/app.py` app through `build_app` into a
temporary outdir (u6_support.synth). No test writes infra/cdk.out and none makes an AWS call.

  T-INF-U6-1   ARMS == ["router", "pinned:nova-lite"], in that order; an existing-mode synth
               yields exactly the stacks {Delphi-router, Delphi-pinned-nova-lite} (BR1.1, BR1.6).
  T-INF-U6-3   Both name transforms (stack 'Delphi-' + ':'/'_' -> '-'; runtime 'delphi_' +
               ':'/'-' -> '_') are injective over ARMS and over the nine-arm list (U8's, dropped);
               stack names match [A-Za-z0-9-]+, runtime names [a-zA-Z][a-zA-Z0-9_]{0,47}; the
               two live arms keep their names (BR1.3, BR1.5).
  T-INF-U6-4   Each pinned arm's alias is a candidate of the pool its container resolves
               (config/models.yaml); a pinned alias outside the pool raises ArmSpecError
               naming it (BR1.4).
  T-INF-U6-5   (keep-fixed) the synthesized (logical id, type) set and runtime name of each
               frozen stack contain the committed last-synthesized baseline
               (infra/baseline/live_identity.json). Negative control: the same template with
               the Runtime's logical id renamed is reported (BR1.5, NFR8.9).
  T-INF-U6-6   Synth with no `artifactBucket`, or with a value outside {existing, create},
               raises BucketModeError naming both values and when each applies; infra/cdk.json
               carries no artifactBucket key, and build_app refuses a cdk.json that does
               (BR2.1, NFR8.1).
  T-INF-U6-7   Existing mode: no AWS::S3::Bucket or AWS::S3::BucketPolicy in any template, no
               Delphi-artifacts stack, and the router grant's bucket renders equal to
               config/artifact-location.json's template (BR2.2, NFR8.2).
  T-INF-U6-8   Create mode: Delphi-artifacts holds one bucket with DeletionPolicy and
               UpdateReplacePolicy Retain (RemovalPolicy.RETAIN in code), public access fully
               blocked, SSE-S3, BucketOwnerEnforced, versioning on, and a bucket policy denying
               non-TLS access; each arm template is equal in both modes (BR2.4, NFR8.3).
  T-INF-U6-9   No 'delphi-artifacts' literal in infra/ code or config; the grant ARN, the
               DELPHI_ARTIFACT_URI bucket and the create-mode bucket name all render equal to
               the location file's template (BR2.5, NFR8.4).
  T-INF-U6-23  With no override, each stack's ContainerUri is the Fn::Sub over its single
               image asset's hash (BR5.1).
  T-INF-U6-24  imageOverride:<stack>=<64 hex> changes only that stack's ContainerUri, to that
               tag; the overridden stack builds NO image asset and its ECR resource and URI
               repository part are byte-equal to the non-override synth (INF-U6-12, probe c);
               a malformed tag, an unknown stack, or an override in cdk.json fails synth
               naming which (BR5.2, FR8.2.1).
  T-INF-U6-25  Each template outputs ContainerUri, equal to its runtime's container URI
               (BR5.3, FR8.2.2).
  T-INF-U6-37  The serving_form flip, by .dockerignore branch: with the location file outside
               the image context, a pointer synth differs from the legacy synth ONLY in the
               router's DELPHI_ARTIFACT_URI, with an equal asset hash; with it inside, the same
               comparison holds under an override to the router's tag (BR8.2, NFR8.14).
  T-INF-U6-56  The cdk-nag specifier is equal in the dev extra and infra/requirements.txt and
               bounded to one 2.x minor; cdk-nag is in neither requirements-agent.txt nor
               [project].dependencies; infra/requirements.txt's pyyaml equals [project]'s
               (NFR8.13, NFR2.1, TS-U6-1, TS-U6-3).
  T-INF-U6-62  (keep-fixed) each frozen runtime keeps NetworkMode PUBLIC and no
               AuthorizerConfiguration, as the baseline records. Negative control: an in-memory
               authorizer is reported (NFR8.9).
  guard        the autouse socket guard is active.

Partition: aws-cdk-lib + Node.js (synth), stdlib, yaml. Fails, never skips, without them.
"""
from __future__ import annotations

import json
import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import tomllib

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest
import yaml

import u6_support as u6

ROOT = u6.REPO
STACKS = {"Delphi-router", "Delphi-pinned-nova-lite"}
TAG = "a" * 64


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_infra_stack.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _baseline() -> dict:
    return json.loads(u6.BASELINE.read_text())["stacks"]


def _cdk_json_with(tmp_path: pathlib.Path, **extra) -> pathlib.Path:
    data = json.loads((u6.INFRA / "cdk.json").read_text())
    data["context"].update(extra)
    p = tmp_path / "cdk.json"
    p.write_text(json.dumps(data))
    return p


# --- T-INF-U6-1 --------------------------------------------------------------------------
def test_t_inf_u6_1_arms_is_the_two_known_good_arms():
    arms = u6.infra_module("arms")
    assert list(arms.ARMS) == ["router", "pinned:nova-lite"]
    assert u6.existing().stack_names == STACKS


# --- T-INF-U6-3 --------------------------------------------------------------------------
def test_t_inf_u6_3_name_transforms_are_injective_and_valid():
    arms = u6.infra_module("arms")
    for arm_list in (list(arms.ARMS), list(u6.NINE_ARMS)):
        stacks = [arms.stack_name(a) for a in arm_list]
        runtimes = [arms.runtime_name(a) for a in arm_list]
        assert len(set(stacks)) == len(arm_list) and len(set(runtimes)) == len(arm_list)
        assert all(re.fullmatch(r"[A-Za-z0-9-]+", s) for s in stacks), stacks
        assert all(re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", r) for r in runtimes), runtimes
    assert arms.stack_name("router") == "Delphi-router"
    assert arms.stack_name("pinned:nova-lite") == "Delphi-pinned-nova-lite"
    assert arms.runtime_name("router") == "delphi_router"
    assert arms.runtime_name("pinned:nova-lite") == "delphi_pinned_nova_lite"


# --- T-INF-U6-4 --------------------------------------------------------------------------
def test_t_inf_u6_4_pinned_alias_is_in_its_pool():
    arms = u6.infra_module("arms")
    pinned = [a for a in arms.ARMS if a.startswith("pinned:")]
    assert pinned
    for arm in pinned:
        spec = arms.arm_spec(arm)
        assert spec.pool_file == "config/models.yaml"
        pool = yaml.safe_load((ROOT / spec.pool_file).read_text())
        aliases = {c["alias"] for c in pool["candidates"]}
        assert spec.pinned_alias in aliases
    with pytest.raises(arms.ArmSpecError, match="not-a-model"):
        arms.arm_spec("pinned:not-a-model")


# --- T-INF-U6-5 (keep-fixed) -------------------------------------------------------------
def test_t_inf_u6_5_frozen_identity_contains_the_baseline():
    synth = u6.existing()
    for stack, frozen in _baseline().items():
        assert u6.identity_problems(synth.templates[stack], frozen) == [], stack
    moved = u6.renamed(synth.templates["Delphi-router"], "Runtime", "RuntimeV2")
    assert u6.identity_problems(moved, _baseline()["Delphi-router"]), "negative control"


# --- T-INF-U6-6 --------------------------------------------------------------------------
@pytest.mark.parametrize("context", [{}, {"artifactBucket": "nope"}])
def test_t_inf_u6_6_bucket_mode_is_required(context, tmp_path):
    stack_mod = u6.infra_module("runtime_stack")
    with pytest.raises(stack_mod.BucketModeError) as err:
        u6.synth(context=context, cdk_json=_cdk_json_with(tmp_path))
    msg = str(err.value)
    assert "existing" in msg and "create" in msg and "clean account" in msg
    assert "artifactBucket" not in json.loads((u6.INFRA / "cdk.json").read_text())["context"]
    with pytest.raises(stack_mod.BucketModeError, match="cdk.json"):
        u6.synth(context={"artifactBucket": "existing"},
                 cdk_json=_cdk_json_with(tmp_path, artifactBucket="existing"))


# --- T-INF-U6-7 --------------------------------------------------------------------------
def test_t_inf_u6_7_existing_mode_references_and_never_manages_the_bucket():
    synth = u6.existing()
    assert "Delphi-artifacts" not in synth.stack_names
    for stack in synth.stack_names:
        assert not synth.resources(stack, "AWS::S3::Bucket"), stack
        assert not synth.resources(stack, "AWS::S3::BucketPolicy"), stack
    s3 = [s for s in synth.statements("Delphi-router") if s["Action"] == "s3:GetObject"]
    # U6b narrows the grant to C6's router/* (T-INF-U6-12); the bucket is still the file's.
    assert [u6.render(s["Resource"]) for s in s3] == [
        f"arn:aws:s3:::{u6.location_bucket()}/router/*"]


# --- T-INF-U6-8 --------------------------------------------------------------------------
def test_t_inf_u6_8_create_mode_retains_a_controlled_bucket():
    create = u6.synth(context={"artifactBucket": "create"})
    assert create.stack_names == STACKS | {"Delphi-artifacts"}
    buckets = create.resources("Delphi-artifacts", "AWS::S3::Bucket")
    assert set(buckets) == {"ArtifactBucket7410C9EF", "AccessLogBucketDA470295"}, set(buckets)
    for bucket in buckets.values():  # the same controls on the artifact and access log buckets
        assert bucket["DeletionPolicy"] == "Retain" and bucket["UpdateReplacePolicy"] == "Retain"
        props = bucket["Properties"]
        assert props["PublicAccessBlockConfiguration"] == {
            "BlockPublicAcls": True, "BlockPublicPolicy": True, "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True}
        (rule,) = props["BucketEncryption"]["ServerSideEncryptionConfiguration"]
        assert rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"] == "AES256"
        assert props["OwnershipControls"]["Rules"] == [{"ObjectOwnership": "BucketOwnerEnforced"}]
        assert props["VersioningConfiguration"] == {"Status": "Enabled"}
    artifact = buckets["ArtifactBucket7410C9EF"]["Properties"]
    assert u6.render(artifact["BucketName"]) == u6.location_bucket()
    assert artifact["LoggingConfiguration"] == {
        "DestinationBucketName": {"Ref": "AccessLogBucketDA470295"},
        "LogFilePrefix": "artifact-bucket/"}
    (expiry,) = buckets["AccessLogBucketDA470295"]["Properties"]["LifecycleConfiguration"]["Rules"]
    assert expiry["Status"] == "Enabled" and expiry["ExpirationInDays"] > 0
    policies = create.resources("Delphi-artifacts", "AWS::S3::BucketPolicy").values()
    assert len(policies) == 2
    for policy in policies:
        denies = [s for s in policy["Properties"]["PolicyDocument"]["Statement"]
                  if s["Effect"] == "Deny"]
        assert any(s["Condition"] == {"Bool": {"aws:SecureTransport": "false"}} for s in denies)
    assert "RemovalPolicy.RETAIN" in (u6.INFRA / "delphi_infra" / "runtime_stack.py").read_text()
    existing = u6.existing()
    for stack in STACKS:
        assert create.templates[stack] == existing.templates[stack], f"{stack} differs by mode"


# --- T-INF-U6-9 --------------------------------------------------------------------------
def test_t_inf_u6_9_the_bucket_name_has_one_declaration():
    literal = []
    for path in sorted(u6.INFRA.rglob("*")):
        rel = path.relative_to(u6.INFRA)
        if (not path.is_file() or rel.parts[0] in {"cdk.out", ".venv", "baseline"}
                or "__pycache__" in rel.parts or path.suffix not in {".py", ".json", ".txt"}):
            continue
        if "delphi-artifacts" in path.read_text():
            literal.append(str(rel))
    assert literal == []
    bucket = u6.location_bucket()
    create = u6.synth(context={"artifactBucket": "create"})
    b = create.resources("Delphi-artifacts", "AWS::S3::Bucket")["ArtifactBucket7410C9EF"]
    uri = u6.render(create.runtime("Delphi-router")["EnvironmentVariables"][
        "DELPHI_ARTIFACT_URI"])
    grants = [u6.render(s["Resource"]) for s in create.statements("Delphi-router")
              if s["Action"] == "s3:GetObject"]
    assert u6.render(b["Properties"]["BucketName"]) == bucket
    assert uri.startswith(f"s3://{bucket}/")
    assert grants == [f"arn:aws:s3:::{bucket}/router/*"]  # U6b: C6 grant_prefix


# --- T-INF-U6-23 -------------------------------------------------------------------------
def test_t_inf_u6_23_default_image_is_the_built_asset():
    synth = u6.existing()
    for stack in STACKS:
        uri = synth.container_uri(stack)
        assert u6.render(uri).endswith(":" + synth.asset_hash(stack)), stack


# --- T-INF-U6-24 -------------------------------------------------------------------------
def _repo_part(uri) -> str:
    return u6.render(uri).rsplit(":", 1)[0]


def _ecr_layer_resources(synth, stack) -> list:
    return [s["Resource"] for s in synth.statements(stack)
            if "ecr:BatchGetImage" in (s["Action"] if isinstance(s["Action"], list)
                                       else [s["Action"]])]


def test_t_inf_u6_24_override_is_a_tag_for_one_named_stack(tmp_path):
    image = u6.infra_module("image")
    base = u6.existing()
    over = u6.existing(**{"imageOverride:Delphi-pinned-nova-lite": TAG})
    assert over.templates["Delphi-router"] == base.templates["Delphi-router"]
    assert u6.render(over.container_uri("Delphi-pinned-nova-lite")).endswith(":" + TAG)
    assert over.assets("Delphi-pinned-nova-lite") == {}, "an override must build no image"
    assert (_repo_part(over.container_uri("Delphi-pinned-nova-lite"))
            == _repo_part(base.container_uri("Delphi-pinned-nova-lite")))
    assert (_ecr_layer_resources(over, "Delphi-pinned-nova-lite")
            == _ecr_layer_resources(base, "Delphi-pinned-nova-lite"))
    for bad in ("A" * 64, "a" * 63, "g" * 64, ""):
        with pytest.raises(image.ImageOverrideError, match="64 lowercase hex"):
            u6.synth(context={"artifactBucket": "existing",
                              "imageOverride:Delphi-pinned-nova-lite": bad})
    with pytest.raises(image.ImageOverrideError, match="Delphi-nowhere"):
        u6.synth(context={"artifactBucket": "existing", "imageOverride:Delphi-nowhere": TAG})
    with pytest.raises(image.ImageOverrideError, match="cdk.json"):
        u6.synth(context={"artifactBucket": "existing"},
                 cdk_json=_cdk_json_with(tmp_path, **{"imageOverride:Delphi-router": TAG}))


# --- T-INF-U6-25 -------------------------------------------------------------------------
def test_t_inf_u6_25_container_uri_is_an_output():
    for synth in (u6.existing(), u6.existing(**{"imageOverride:Delphi-router": TAG})):
        for stack in STACKS:
            assert synth.outputs(stack)["ContainerUri"] == synth.container_uri(stack), stack


# --- T-INF-U6-37 -------------------------------------------------------------------------
_location_excluded = u6.location_excluded


def test_t_inf_u6_37_flip_changes_only_the_artifact_uri(tmp_path):
    expected_diff = u6.infra_module("expected_diff")
    tree = u6.scratch_tree(tmp_path / "tree")
    loc_path = tree / "config" / "artifact-location.json"
    loc = json.loads(loc_path.read_text())
    # Set both forms explicitly, so the check holds before and after U6c's flip (BRK-U6-3).
    loc["serving_form"] = "legacy_prefix"
    loc_path.write_text(json.dumps(loc, indent=2) + "\n")
    ctx = {"artifactBucket": "existing"}
    legacy = u6.synth(context=ctx, build_root=tree)
    if not _location_excluded():  # declined branch: hold the image with an override
        ctx["imageOverride:Delphi-router"] = legacy.asset_hash("Delphi-router")
        legacy = u6.synth(context=ctx, build_root=tree)
    loc["serving_form"] = "pointer"
    loc_path.write_text(json.dumps(loc, indent=2) + "\n")
    pointer = u6.synth(context=ctx, build_root=tree)
    delta = expected_diff.template_delta(legacy.templates, pointer.templates)
    assert [(d.stack, d.resource, d.path) for d in delta] == [
        ("Delphi-router", "Runtime", "Properties.EnvironmentVariables.DELPHI_ARTIFACT_URI")]
    assert u6.render(delta[0].after).endswith("/router/current.json")
    if _location_excluded():
        for stack in STACKS:
            assert pointer.asset_hash(stack) == legacy.asset_hash(stack), stack


# --- T-INF-U6-56 -------------------------------------------------------------------------
def _spec(lines: list[str], name: str) -> str | None:
    for line in lines:
        m = re.match(rf"^\s*{re.escape(name)}\s*([<>=!~].*)$", line.split("#")[0].strip(), re.I)
        if m:
            return m.group(1).replace(" ", "")
    return None


def test_t_inf_u6_56_cdk_nag_declarations_are_equal_and_not_serving():
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dev = [s.strip('"') for s in py["project"]["optional-dependencies"]["dev"]]
    infra = (u6.INFRA / "requirements.txt").read_text().splitlines()
    dev_spec, infra_spec = _spec(dev, "cdk-nag"), _spec(infra, "cdk-nag")
    assert dev_spec is not None and dev_spec == infra_spec, (dev_spec, infra_spec)
    m = re.fullmatch(r">=2\.(\d+),<2\.(\d+)", dev_spec)
    assert m and int(m.group(2)) == int(m.group(1)) + 1, dev_spec
    serving = (ROOT / "requirements-agent.txt").read_text().splitlines()
    assert _spec(serving, "cdk-nag") is None
    assert _spec(py["project"]["dependencies"], "cdk-nag") is None
    project_yaml = _spec(py["project"]["dependencies"], "pyyaml")
    assert project_yaml is not None and _spec(infra, "pyyaml") == project_yaml


# --- T-INF-U6-62 (keep-fixed) ------------------------------------------------------------
def test_t_inf_u6_62_inbound_posture_is_frozen():
    synth = u6.existing()
    for stack, frozen in _baseline().items():
        assert frozen["network_mode"] == "PUBLIC" and frozen["authorizer"] is False
        assert u6.inbound_problems(synth.templates[stack], frozen) == [], stack
    t = json.loads(json.dumps(synth.templates["Delphi-router"]))
    t["Resources"]["Runtime"]["Properties"]["AuthorizerConfiguration"] = {"x": 1}
    assert u6.inbound_problems(t, _baseline()["Delphi-router"]), "negative control"


# --- guard -------------------------------------------------------------------------------
def test_u6_infra_stack_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
