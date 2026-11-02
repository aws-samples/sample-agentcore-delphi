"""The deployment surface: build context and stack — U3 Layer 5 (SD-12, SD-13; FR10.2;
NFR10.1-NFR10.3; AC4.5.2, AC4.5.3). Named for what it tests, unlike test_infra.py, which
tests the eval harness.

The image build context is the repo root, filtered by ONE allowlist (.dockerignore). Before U3
it was two disagreeing denylists (.dockerignore and the CDK `exclude=` list), so a STATUS.md
edit or a new AI-DLC record moved the image asset hash and redeployed every arm.

  T-PKG-U3-14  The real `router` stack, staged with its DEFAULT build root into a tmp_path
               outdir: the staged asset top level EQUALS {.dockerignore, Dockerfile, config,
               pyproject.toml, requirements-agent.txt, src}, with no __pycache__, no .pyc and no
               *.egg-info anywhere under it. runtime_stack.py contains no `exclude=` and no
               "baked artifact". The default build root holds Dockerfile and pyproject.toml
               (pins the parents[2] invariant).
  T-PKG-U3-15  The REAL DelphiRuntimeStack built with build_root=<tmp_path copy of the serving
               paths plus synthetic STATUS.md and aidlc/x.md — never the real record tree>:
               (a) appending to STATUS.md and aidlc/x.md leaves asset_hash UNCHANGED;
               (b) creating src/**/__pycache__/*.pyc leaves it UNCHANGED, and so does a
                   src/*.egg-info directory (what `pip install -e` writes);
               (c) editing src/delphi/agent/app.py CHANGES it — the guard against a vacuous
                   allowlist.
  T-PKG-U3-16  infra/cdk.json's context has NO `@aws-cdk/aws-ecr-assets:dockerIgnoreSupport`,
               and an App with every cdk.json context key synthesizes the router stack.
               Negative control: the same App with that flag added RAISES on synth, which is why
               it must never be added (it expired in aws-cdk-lib 2.261.0; X1).

All three FAIL, never skip, when aws_cdk or Node.js is missing (the cdk_synth fixture).
Partition B: aws-cdk-lib + Node.js, no credentials, no account. No test writes infra/cdk.out.
"""
from __future__ import annotations

import json
import pathlib
import shutil

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
STAGED = {".dockerignore", "Dockerfile", "config", "pyproject.toml",
          "requirements-agent.txt", "src"}
FLAG = "@aws-cdk/aws-ecr-assets:dockerIgnoreSupport"


def _junk(tree: pathlib.Path) -> list[str]:
    return sorted(str(p.relative_to(tree)) for p in tree.rglob("*")
                  if p.name == "__pycache__" or p.suffix == ".pyc"
                  or p.name.endswith(".egg-info"))


# --- T-PKG-U3-14 ---------------------------------------------------------------------
def test_t_pkg_u3_14_staged_context_is_exactly_the_serving_paths(cdk_synth):
    synth = cdk_synth()
    staged = {p.name for p in synth.asset_dir.iterdir()}
    assert staged == STAGED, sorted(staged ^ STAGED)
    assert _junk(synth.asset_dir) == []


def test_t_pkg_u3_14_stack_source_has_no_second_filter_and_no_false_claim():
    src = (ROOT / "infra" / "delphi_infra" / "runtime_stack.py").read_text()
    assert "exclude=" not in src
    assert "baked artifact" not in src


def test_t_pkg_u3_14_default_build_root_is_the_repository(cdk_synth):
    cdk_synth()  # puts infra/ on sys.path
    from delphi_infra import runtime_stack

    root = runtime_stack._REPO_ROOT
    assert root == ROOT
    assert (root / "Dockerfile").is_file() and (root / "pyproject.toml").is_file()


# --- T-PKG-U3-15 ---------------------------------------------------------------------
@pytest.fixture
def scratch_tree(tmp_path):
    tree = tmp_path / "tree"
    shutil.copytree(ROOT / "src", tree / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
    shutil.copytree(ROOT / "config", tree / "config")
    for name in ("pyproject.toml", "requirements-agent.txt", "Dockerfile", ".dockerignore"):
        shutil.copy2(ROOT / name, tree / name)
    (tree / "STATUS.md").write_text("# synthetic status\n")
    (tree / "aidlc").mkdir()
    (tree / "aidlc" / "x.md").write_text("synthetic record\n")
    return tree


def test_t_pkg_u3_15_asset_hash_tracks_serving_content_only(cdk_synth, scratch_tree):
    base = cdk_synth(build_root=scratch_tree).asset_hash

    # (a) non-serving edits
    with (scratch_tree / "STATUS.md").open("a") as f:
        f.write("edited\n")
    with (scratch_tree / "aidlc" / "x.md").open("a") as f:
        f.write("a new decision\n")
    (scratch_tree / "aidlc" / "y.md").write_text("another record\n")
    assert cdk_synth(build_root=scratch_tree).asset_hash == base, "(a) non-serving edit moved it"

    # (b) interpreter and installer droppings under src/
    pyc = scratch_tree / "src" / "delphi" / "agent" / "__pycache__" / "app.cpython-312.pyc"
    pyc.parent.mkdir()
    pyc.write_bytes(b"\x00compiled")
    egg = scratch_tree / "src" / "delphi.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Name: delphi\n")
    assert cdk_synth(build_root=scratch_tree).asset_hash == base, (
        "(b) __pycache__/egg-info moved it")

    # (c) a serving edit must move it, or the allowlist is vacuous
    with (scratch_tree / "src" / "delphi" / "agent" / "app.py").open("a") as f:
        f.write("\n# serving edit\n")
    assert cdk_synth(build_root=scratch_tree).asset_hash != base, "(c) serving edit did not"


# --- T-PKG-U3-16 ---------------------------------------------------------------------
def _cdk_context() -> dict:
    return json.loads((ROOT / "infra" / "cdk.json").read_text())["context"]


def test_t_pkg_u3_16_expired_flag_is_absent_and_the_app_synthesizes(cdk_synth):
    ctx = _cdk_context()
    assert FLAG not in ctx
    synth = cdk_synth(context=ctx)
    assert synth.runtime_properties()["EnvironmentVariables"]["DELPHI_ARM"] == "router"


def test_t_pkg_u3_16_negative_control_the_flag_makes_synth_raise(cdk_synth):
    with pytest.raises(Exception, match="dockerIgnoreSupport"):
        cdk_synth(context={**_cdk_context(), FLAG: True})
