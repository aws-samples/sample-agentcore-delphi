"""The built serving image — U3 Layer 7 (SD-3; NFR2.4; AC4.5.4 image half; G-4; infra
review R-05; plan D-CG-8).

Marked `image` and deselected from the default run (pyproject addopts). Run it with:

    .venv/bin/python -m pytest -m image tests/test_packaging_image.py

  T-PKG-U3-4  The context is staged by the shared in-process synth of the REAL router stack
              (the same context the deployment hashes), then `docker build --platform
              linux/arm64` runs on that CDK-staged directory. Inside the image, with
              `--network none`:
                * `pip list --format=json` holds no denylisted distribution;
                * `python -c "import bedrock_agentcore, delphi.agent.app,
                  delphi.train.artifact_store"` exits 0 (catches a dependency `--no-deps` left
                  out);
                * /app/config/models_realpool.yaml exists (catches an allowlist that dropped
                  config/), and /app/config/artifact-location.json does NOT (U6 BRK-U6-6,
                  P-U6-8: no request-path module reads it, so it stays out of the image and a
                  serving_form edit cannot move the image asset hash);
                * in-image construction: with DELPHI_ARM=router,
                  DELPHI_MODELS_FILE=/app/config/models_realpool.yaml, AWS_REGION set and
                  DELPHI_ARTIFACT_URI pointing at a READ-ONLY mounted local directory holding an
                  artifact produced on the host by the generator, `build_agent_from_env()` builds
                  a LearnedRouter and prints that artifact's hash.
              Stated limit: the legacy S3 branch is not exercised in the image offline; it is
              covered by T-PKG-U3-11(a) and first runs in the image at the deploy's
              `delphi-verify`. When selected, the test FAILS, not skips, without Docker.

Partition `image`: needs a running Docker daemon able to build linux/arm64. No AWS call.
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.image

ROOT = pathlib.Path(__file__).resolve().parents[1]
DENYLIST = {"torch", "scikit-learn", "tensorflow", "jax", "xgboost", "lightgbm",
            "transformers", "scipy", "pandas"}


def _docker(*args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)  # noqa: S603,S607 - fixed argv: the docker CLI


@pytest.fixture(scope="module")
def image_tag(cdk_synth):
    if shutil.which("docker") is None:
        pytest.fail("T-PKG-U3-4 needs Docker: the docker CLI is not on PATH")
    info = _docker("info", "--format", "{{.ServerVersion}}", timeout=60)
    if info.returncode != 0:
        pytest.fail(f"T-PKG-U3-4 needs a running Docker daemon: {info.stderr.strip()}")
    synth = cdk_synth()
    tag = f"delphi-u3-smoke:{synth.asset_hash[:12]}"
    build = _docker("build", "--platform", "linux/arm64", "-t", tag, str(synth.asset_dir))
    assert build.returncode == 0, (f"docker build failed:\n{build.stdout[-4000:]}\n"
                                   f"{build.stderr[-4000:]}")
    yield tag
    _docker("rmi", "-f", tag, timeout=120)  # local only (plan Step 23)


def _run_in(tag: str, *cmd: str, extra: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    return _docker("run", "--rm", "--network", "none", "--platform", "linux/arm64", *extra,
                   tag, *cmd, timeout=300)


def test_t_pkg_u3_4_no_denylisted_distribution_in_the_image(image_tag):
    res = _run_in(image_tag, "pip", "list", "--format=json")
    assert res.returncode == 0, res.stderr
    names = {re.sub(r"[-_.]+", "-", d["name"]).lower() for d in json.loads(res.stdout)}
    assert not names & DENYLIST, sorted(names & DENYLIST)
    assert {"bedrock-agentcore", "boto3", "numpy", "pyyaml", "delphi"} <= names


def test_t_pkg_u3_4_request_path_imports_resolve(image_tag):
    res = _run_in(image_tag, "python", "-c",
                  "import bedrock_agentcore, delphi.agent.app, delphi.train.artifact_store")
    assert res.returncode == 0, res.stderr


def test_t_pkg_u3_4_config_files_are_in_the_image(image_tag):
    res = _run_in(image_tag, "sh", "-c",
                  "test -f /app/config/models_realpool.yaml && "
                  "test ! -e /app/config/artifact-location.json")
    assert res.returncode == 0, res.stderr


def test_t_pkg_u3_4_agent_constructs_in_the_image_from_a_mounted_artifact(image_tag,
                                                                           tmp_path):
    from delphi.train.generate import build

    art_dir, art = build(ROOT / "config" / "models_realpool.yaml", tmp_path / "art")
    res = _run_in(
        image_tag, "python", "-c",
        "from delphi.agent.app import build_agent_from_env; a = build_agent_from_env(); "
        "print(type(a.router).__name__, a.router.artifact.artifact_hash)",
        extra=("-v", f"{art_dir}:/artifact:ro",
               "-e", "DELPHI_ARM=router",
               "-e", "DELPHI_MODELS_FILE=/app/config/models_realpool.yaml",
               "-e", "AWS_REGION=us-west-2",
               "-e", "DELPHI_ARTIFACT_URI=/artifact"),
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == ["LearnedRouter", art.artifact_hash], res.stdout
