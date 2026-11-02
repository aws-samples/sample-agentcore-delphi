"""One artifact-location definition — U3 Layer 3 (SD-9; NFR10.7; C6, as amended).

`config/artifact-location.json` is the single definition of where the router artifact lives.
Both sides read it with the standard library's `json`: the publisher through
`delphi.train.artifact_location`, the CDK stack directly (infra/.venv has no PyYAML, M5).

  T-PKG-U3-20  For the same placeholder account and region, the publisher's destinations from
               `delphi.train.artifact_location` (bucket, pointer key, triple prefix, legacy
               prefix) EQUAL what runtime_stack.py composes: the stack's DELPHI_ARTIFACT_URI is
               the publisher's serving URI, its bucket is the publisher's bucket, and the stack's
               S3 read grant names the same bucket, under `router/*` from U6b (BRK-U6-7).
               runtime_stack.py contains no `router/current`
               literal. config/artifact-location.json contains no 12-digit number.
  T-PKG-U3-23  SERVING-FORM PIN. With the placeholder environment, the router
               DELPHI_ARTIFACT_URI equals EXACTLY
               s3://delphi-artifacts-111111111111-us-west-2/router/current.json, and the location
               file's serving_form == "pointer". Template expression too (plan D-CG-7, G-5): the
               environment-agnostic synthesized router EnvironmentVariables, serialized with
               sorted keys, equals BYTE FOR BYTE the baseline below: the one in
               `u3-packaging-integrity/infrastructure-design/infrastructure-specification.md` § 4,
               with only the URI suffix changed.
               U6c flipped the form after S9's verify-remote passed (BRK-U6-3).

Partition B: needs aws-cdk-lib + Node.js for the in-process synth, no credentials, no account.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCOUNT, REGION = "111111111111", "us-west-2"

# `u3-packaging-integrity/infrastructure-design/infrastructure-specification.md` § 4,
# measured at f331520 (environment-agnostic synth).
BASELINE_ENV = {
    "AWS_REGION": {"Ref": "AWS::Region"},
    "DELPHI_ARM": "router",
    "DELPHI_ARTIFACT_URI": {"Fn::Join": ["", [
        "s3://delphi-artifacts-", {"Ref": "AWS::AccountId"}, "-", {"Ref": "AWS::Region"},
        "/router/current.json"]]},
    "DELPHI_CONFIG_DIR": "/app/config",
    "DELPHI_MODELS_FILE": "/app/config/models_realpool.yaml",
}


def _location_file() -> dict:
    return json.loads((ROOT / "config" / "artifact-location.json").read_text())


# --- T-PKG-U3-20 ---------------------------------------------------------------------
def test_t_pkg_u3_20_publisher_and_stack_resolve_the_same_destinations(cdk_synth):
    from delphi.train.artifact_location import load_location

    loc = load_location()
    synth = cdk_synth(account=ACCOUNT, region=REGION)
    uri = synth.runtime_properties()["EnvironmentVariables"]["DELPHI_ARTIFACT_URI"]

    bucket = loc.bucket(ACCOUNT, REGION)
    assert bucket == f"delphi-artifacts-{ACCOUNT}-{REGION}"
    assert uri == loc.serving_uri(ACCOUNT, REGION)
    assert uri.startswith(f"s3://{bucket}/")
    assert loc.pointer_key == "router/current.json"
    assert loc.legacy_prefix == "router/current"
    assert loc.triple_prefix("ab" * 16) == f"router/{'ab' * 16}/"
    assert loc.pointer_uri(ACCOUNT, REGION) == f"s3://{bucket}/router/current.json"

    grants = [res for r in synth.template["Resources"].values()
              if r["Type"] == "AWS::IAM::ManagedPolicy"
              for st in r["Properties"]["PolicyDocument"]["Statement"]
              if "s3:GetObject" in (st["Action"] if isinstance(st["Action"], list)
                                    else [st["Action"]])
              for res in (st["Resource"] if isinstance(st["Resource"], list)
                          else [st["Resource"]])]
    # U6 BRK-U6-7 (U6b, BR3.3): the read grant names the same bucket, narrowed to C6's
    # grant_prefix router/*, which covers the pointer, the triples and the legacy prefix.
    assert grants == [f"arn:aws:s3:::{bucket}/router/*"]


def test_t_pkg_u3_20_stack_holds_no_location_literal():
    src = (ROOT / "infra" / "delphi_infra" / "runtime_stack.py").read_text()
    assert "router/current" not in src


def test_t_pkg_u3_20_location_file_holds_no_account_id():
    text = (ROOT / "config" / "artifact-location.json").read_text()
    assert not re.search(r"\d{12}", text)
    assert set(_location_file()) == {"bucket", "pointer_key", "triple_prefix",
                                     "legacy_prefix", "serving_form"}


def test_t_pkg_u3_20_publisher_prefix_and_loader_prefix_cannot_diverge(tmp_path):
    # The loader expects the pointer to name `<pointer dir>/<hash>/`; the publisher writes to
    # triple_prefix. A location file where those differ must be refused, not half-served.
    import pytest

    from delphi.train.artifact_location import load_location

    bad = {**_location_file(), "triple_prefix": "artifacts/{artifact_hash}/"}
    p = tmp_path / "artifact-location.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="triple_prefix"):
        load_location(p)


# --- T-PKG-U3-23 ---------------------------------------------------------------------
def test_t_pkg_u3_23_router_uri_is_the_pointer(cdk_synth):
    env = cdk_synth(account=ACCOUNT, region=REGION).runtime_properties()["EnvironmentVariables"]
    assert env["DELPHI_ARTIFACT_URI"] == (
        "s3://delphi-artifacts-111111111111-us-west-2/router/current.json")


def test_t_pkg_u3_23_serving_form_is_pointer():
    assert _location_file()["serving_form"] == "pointer"


def test_t_pkg_u3_23_template_expression_is_byte_identical_to_the_baseline(cdk_synth):
    env = cdk_synth().runtime_properties()["EnvironmentVariables"]
    assert json.dumps(env, sort_keys=True) == json.dumps(BASELINE_ENV, sort_keys=True)
