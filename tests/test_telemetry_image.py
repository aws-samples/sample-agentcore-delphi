"""U2's two checks inside the built serving image (P-INF-U2-1; CD-U2-3; FM-19).

Marked `image` and deselected from the default run (pyproject addopts). Run it with:

    .venv/bin/python -m pytest -m image tests/test_telemetry_image.py -v

The image is built exactly as U3's T-PKG-U3-4 builds it: the context is staged by the
shared in-process synth of the REAL router stack, then `docker build --platform
linux/arm64` runs on that CDK-staged directory. Every container runs with `--network none`.

  T-IMG-U2-1  DELPHI_ARM=router, DELPHI_MODELS_FILE=/app/config/models_realpool.yaml,
              AWS_REGION set and NO DELPHI_ARTIFACT_URI: `build_agent_from_env()` exits
              non-zero; stderr carries exactly one JSON line, with event
              agent_construction_failed, arm router and request_id null; stdout is empty.
              This is the construction-failure line running in the shipped interpreter,
              before any deploy (FM-19, BR6.3)
  T-IMG-U2-2  the same image with DELPHI_ARM=pinned:nova-lite: construction exits 0 and
              writes nothing to stderr (CD-U2-3 (b))

Partition `image`: needs a running Docker daemon able to build linux/arm64. No AWS call.
When selected, the test FAILS, not skips, without Docker.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.image

CONSTRUCT = "from delphi.agent.app import build_agent_from_env; build_agent_from_env()"
BASE_ENV = ("-e", "DELPHI_MODELS_FILE=/app/config/models_realpool.yaml",
            "-e", "AWS_REGION=us-west-2")


def _docker(*args: str, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)  # noqa: S603,S607 - fixed argv: the docker CLI


@pytest.fixture(scope="module")
def image_tag(cdk_synth):
    if shutil.which("docker") is None:
        pytest.fail("T-IMG-U2 needs Docker: the docker CLI is not on PATH")
    info = _docker("info", "--format", "{{.ServerVersion}}", timeout=60)
    if info.returncode != 0:
        pytest.fail(f"T-IMG-U2 needs a running Docker daemon: {info.stderr.strip()}")
    synth = cdk_synth()
    tag = f"delphi-u2-telemetry:{synth.asset_hash[:12]}"
    build = _docker("build", "--platform", "linux/arm64", "-t", tag, str(synth.asset_dir))
    assert build.returncode == 0, (f"docker build failed:\n{build.stdout[-4000:]}\n"
                                   f"{build.stderr[-4000:]}")
    yield tag
    _docker("rmi", "-f", tag, timeout=120)  # local only


def _construct(tag: str, arm: str) -> subprocess.CompletedProcess:
    return _docker("run", "--rm", "--network", "none", "--platform", "linux/arm64",
                   "-e", f"DELPHI_ARM={arm}", *BASE_ENV, tag, "python", "-c", CONSTRUCT,
                   timeout=300)


def _json_lines(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def test_t_img_u2_1_construction_failure_is_one_json_line_in_the_image(image_tag):
    res = _construct(image_tag, "router")
    assert res.returncode != 0
    assert res.stdout == ""
    (line,) = _json_lines(res.stderr)
    assert (line["event"], line["arm"], line["request_id"]) == (
        "agent_construction_failed", "router", None), res.stderr


def test_t_img_u2_2_a_pinned_arm_constructs_silently_in_the_image(image_tag):
    res = _construct(image_tag, "pinned:nova-lite")
    assert res.returncode == 0, res.stderr
    assert res.stderr == ""
