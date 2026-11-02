"""Serving-dependency declarations — U3 Layer 1 (SD-1, SD-14; NFR2.1, NFR2.2, NFR2.6, NFR10.4).

The serving image's third-party set must have exactly ONE declaration, and it must be the
one the Dockerfile installs. Before U3 there were two: `requirements-agent.txt` (live) and the
`[agent]` extra (installed by no path, with a comment claiming the opposite).

  T-PKG-U3-1   pyproject.toml has no `agent` extra; requirements-agent.txt's name set EQUALS
               {bedrock-agentcore, boto3, numpy, pyyaml}; `dev` CONTAINS boto3, aws-cdk-lib and
               constructs and is disjoint from the heavy-ML denylist; the Dockerfile has exactly
               one `pip install -r`, it names requirements-agent.txt, and the `pip install .`
               line carries `--no-deps`; the false comments are gone (requirements-agent.txt has
               neither "Mirrors the [agent]" nor "reproducible image tag", pyproject.toml has no
               "agent =", the Dockerfile has no "COPYing artifacts/"). Also the AC1.5.4
               no-regression check: boto3 stays in `dev`.
  T-PKG-U3-2   every `[project] dependencies` NAME also appears in requirements-agent.txt (with
               `--no-deps`, a name the package declares but the text file omits would be missing
               from the image). Keep-fixed: the lists agree today (F2). Negative control: a
               seeded drifted pair, which the helper must report.
  T-PKG-U3-17  the specifiers of aws-cdk-lib and constructs in `dev` EQUAL those in
               infra/requirements.txt, so the test venv and the CDK venv cannot drift.

Partition A: stdlib only, no SDK, no account.
"""
from __future__ import annotations

import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]

SERVING_SET = {"bedrock-agentcore", "boto3", "numpy", "pyyaml"}
HEAVY_ML = {
    "torch", "scikit-learn", "tensorflow", "jax", "xgboost",
    "lightgbm", "transformers", "scipy", "pandas",
}

_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def req_name(spec: str) -> str:
    """The distribution name of one requirement specifier (`boto3>=1.34` -> `boto3`)."""
    m = _NAME.match(spec)
    if not m:
        raise ValueError(f"not a requirement specifier: {spec!r}")
    return _normalise(m.group(1))


def req_specifier(spec: str) -> str:
    """The version part of one specifier, whitespace removed (`a >= 1` -> `>=1`)."""
    return re.sub(r"\s+", "", spec[_NAME.match(spec).end():])


def requirement_lines(path: pathlib.Path) -> list[str]:
    """Non-blank, non-comment lines of a requirements file, inline comments stripped."""
    out = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def missing_from(declared: list[str], installed: list[str]) -> set[str]:
    """Names in `declared` that `installed` does not provide."""
    return {req_name(s) for s in declared} - {req_name(s) for s in installed}


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _dev() -> list[str]:
    return _pyproject()["project"]["optional-dependencies"]["dev"]


# --- T-PKG-U3-1 ----------------------------------------------------------------
def test_t_pkg_u3_1_no_agent_extra():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "agent" not in extras, "the [agent] extra is a second, dead serving declaration"


def test_t_pkg_u3_1_requirements_agent_is_exactly_the_serving_set():
    names = {req_name(s) for s in requirement_lines(ROOT / "requirements-agent.txt")}
    assert names == SERVING_SET


def test_t_pkg_u3_1_dev_carries_boto3_and_cdk_and_no_heavy_ml():
    names = {req_name(s) for s in _dev()}
    # boto3 in dev is the AC1.5.4 no-regression check: delphi-verify imports under test.
    assert {"boto3", "aws-cdk-lib", "constructs"} <= names
    assert not (names & HEAVY_ML), f"heavy ML dependency in dev: {sorted(names & HEAVY_ML)}"


def test_t_pkg_u3_1_dockerfile_installs_the_one_declaration_with_no_deps():
    lines = [ln.strip() for ln in (ROOT / "Dockerfile").read_text().splitlines()]
    installs_r = [ln for ln in lines if re.search(r"pip install\b.*\s-r\s", ln)]
    assert len(installs_r) == 1, installs_r
    assert "requirements-agent.txt" in installs_r[0]
    installs_pkg = [ln for ln in lines if re.search(r"pip install\b.*\s\.\s*$", ln)]
    assert len(installs_pkg) == 1, installs_pkg
    assert "--no-deps" in installs_pkg[0], (
        "without --no-deps, `pip install .` resolves a second dependency set into the image"
    )


def test_t_pkg_u3_1_false_comments_are_gone():
    req = (ROOT / "requirements-agent.txt").read_text()
    assert "Mirrors the [agent]" not in req
    assert "reproducible image tag" not in req
    assert "agent =" not in (ROOT / "pyproject.toml").read_text()
    assert "COPYing artifacts/" not in (ROOT / "Dockerfile").read_text()


# --- T-PKG-U3-2 ----------------------------------------------------------------
def test_t_pkg_u3_2_project_dependencies_are_installed_by_the_image():
    declared = _pyproject()["project"]["dependencies"]
    installed = requirement_lines(ROOT / "requirements-agent.txt")
    assert missing_from(declared, installed) == set()


def test_t_pkg_u3_2_negative_control_reports_a_drifted_pair():
    # The helper must be able to fail: a name the package declares but the image omits.
    assert missing_from(["numpy>=1.26", "pyyaml>=6.0", "Requests>=2"], ["numpy", "PyYAML"]) == {
        "requests"
    }


# --- T-PKG-U3-17 ---------------------------------------------------------------
def test_t_pkg_u3_17_cdk_specifiers_equal_infra_requirements():
    dev = {req_name(s): req_specifier(s) for s in _dev()}
    infra = {req_name(s): req_specifier(s)
             for s in requirement_lines(ROOT / "infra" / "requirements.txt")}
    for name in ("aws-cdk-lib", "constructs"):
        assert name in dev, f"{name} absent from the dev extra"
        assert dev[name] == infra[name], (name, dev[name], infra[name])
