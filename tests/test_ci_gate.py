"""The CI gate — U5 LC-U5-8 (FR7.1.1, FR7.1.2, FR7.2.1, FR7.3.1, FR7.3.3, FR7.5.1, NFR3.1,
NFR3.2, NFR4.1, NFR4.2, NFR8.1-NFR8.3; US4.6 AC4.6.1-AC4.6.3).

The register below was written before the gate existed (team TDD ordering). One entry command,
`bash scripts/ci.sh`, holds the whole gate; CI files only set the platform up and call it. The
seeded rows run that real command over a minimal copy of the tree, each seeded with one reason
to fail, so every step is shown able to stop the gate for its own reason. No AWS call and no
network: an autouse guard makes ``socket.socket`` raise, and every child process gets a BUILT
environment with no ``AWS_*``, ``CI`` or ``DELPHI_*`` variable inherited, ``HOME`` pointed at a
missing directory under tmp_path and IMDS disabled, so no credential chain can resolve.

Register:

- T-CI-U5-1   ``.github/workflows/ci.yml`` (yaml.safe_load): triggers are exactly
  ``pull_request`` and a push to ``main``; the job matrix is exactly python 3.11 and 3.13;
  nothing in the file is ``workflow_dispatch``, a schedule, an ``environment`` or a deploy; the
  job has a ``timeout-minutes`` whose comment says it is tuned from the first recorded run; a
  step runs ``node --version``. Source: FR7.1.1, AC4.6.1, D-CG-U5-6, D-CG-U5-7.
- T-CI-U5-2   every CI file calls ``bash scripts/ci.sh`` and has no ``pytest`` or ``ruff`` token
  of its own on a non-comment line; ``scripts/ci.sh`` starts with ``set -euo pipefail`` and has
  the line ``unset DELPHI_ALLOW_UNSANDBOXED_EXEC``. Source: FR7.1.2, FM-19.
- T-CI-U5-2g  control: the clean minimal copy (``fail_under = 0``) exits 0 and prints all three
  step banners and ``no AWS credentials resolve``, and no ``failed (exit``. Every seeded row
  asserts this control first. Source: SD-U5-3, FM-3.
- T-CI-U5-2a  a failing seeded test: ``step 'pytest' failed`` and
  ``FAILED tests/test_seeded.py::test_seeded_failure``; no ``not reached``. Source: FR7.1.2.
- T-CI-U5-2b  ``eval("1")`` in a seeded ``src/`` module: ``step 'ruff' failed``, ``S307`` and
  ``_seeded.py``; no ``==> pytest``. Source: FR7.1.2.
- T-CI-U5-2c  ``fail_under = 100``: ``step 'pytest' failed``, ``Required test coverage of 100``
  and ``not reached``; no ``is invalid`` and no ``FAILED tests/``. Source: NFR3.1, R-02.
- T-CI-U5-2d  a runtime-built fake key and secret in the environment: exit 3,
  ``step 'preflight' failed`` and ``provider: env``; no ``==> ruff``; neither fake value appears
  in the output. Source: NFR8.1, R-01.
- T-CI-U5-2e  a bare ``xfail`` on a passing test: ``step 'pytest' failed`` and ``XPASS(strict)``;
  no ``not reached``. Source: NFR4.1.
- T-CI-U5-2f  ``pytest.skip("seeded")``: ``step 'pytest' failed`` and ``ci: 1 skipped test``; no
  ``not reached``. Source: NFR4.1, NR-U5-13.
- T-CI-U5-2h  ``@pytest.mark.author_record`` naming an absent path: exit 0 and
  ``author-record test(s) skipped``; no ``failed (exit``. The one skip CI accepts, so a
  published sample without the author's record stays green. Source: NFR4.1.
- T-CI-U5-3   ``.github/workflows/ci.yml``, ``scripts/ci.sh`` and ``.pre-commit-config.yaml``
  are listed by ``git ls-files --cached --others --exclude-standard`` (present and not ignored) and
  ``git check-ignore`` exits 1 for each. Source: FR7.2.1, AC4.6.3.
- T-CI-U5-4   tomllib: ``[tool.coverage.run] source = ["src"]`` and
  ``[tool.coverage.report] fail_under = 80``; ``--cov`` is not in ``addopts``; ``ci.sh`` carries
  no ``--cov-fail-under``. Source: NFR3.1, AC4.6.2.
- T-CI-U5-5   ``[tool.ruff]`` keys are exactly line-length 100 and the three extend-excludes,
  ``[tool.ruff.lint]`` keys are exactly select E/BLE/S and per-file-ignores ``tests/**`` = S101;
  the ``dev`` extra holds ``ruff>=0.15,<0.16``. Source: FR7.3.1, NR-U5-9.
- T-CI-U5-6   every ``# noqa`` COMMENT token (read with tokenize) under src/, tests/, scripts/
  and infra/ (cdk.out and .venv excluded) has the form ``# noqa: CODE[,CODE] - <why>``. Seeded
  controls: a bare noqa and a codes-only noqa are found; a noqa inside a string is not.
  Source: FR7.3.3, D-CG-U5-10.
- T-CI-U5-7   ``.pre-commit-config.yaml`` is tracked-or-addable; its ``gitleaks`` hook ``rev`` is a
  release tag or a 40-hex SHA; no CI file mentions gitleaks. Source: FR7.5.1.
- T-CI-U5-8   (a) ``xfail_strict`` is true; ``ci.sh``'s pytest line asks for the skip summary
  (``-r`` with ``s``) and ``ci.sh`` exports ``DELPHI_FAIL_ON_SKIP=1``. (b) no ``xfail`` call under
  tests/ passes ``strict=False`` (AST). Seeded control: a ``strict=False`` source is found.
  Source: NFR4.1, D-CG-U5-1.
- T-CI-U5-9a  ``load_pool()`` reads a temporary models file named by ``DELPHI_MODELS_FILE`` and
  returns its two unique aliases (the setup half of the pair). Source: NFR4.2.
- T-CI-U5-9b  run right after 9a, with the variable unset: ``load_pool()`` returns
  ``config/models.yaml``'s aliases. Without the autouse cache clear it returns 9a's pool.
  Source: NFR4.2.
- T-CI-U5-10  the workflow's ``env`` sets ``AWS_EC2_METADATA_DISABLED: "true"`` and
  ``DELPHI_REQUIRE_NO_AWS_CREDS: "1"``, and the file names no credential variable, no
  ``id_tokens`` and no ``secrets:``. Source: NFR8.1.
- T-CI-U5-11  no non-comment line of a CI file or of ``ci.sh`` matches ``cdk deploy``,
  ``cdk bootstrap``, ``\\baws\\s``, ``delphi-verify``, ``delphi-artifact``,
  ``delphi-scrub-check`` or ``scrub_check``. Seeded control: such a line is found, and the same
  text in a comment is not. Source: NFR8.2, FR9.1.4.
- T-CI-U5-12  no CI file sets ``DELPHI_ALLOW_UNSANDBOXED_EXEC``; any ``.github/workflows/*.yml``
  declares ``contents: read`` and pins every ``uses:`` to a 40-hex SHA. Seeded control: a
  tag-pinned workflow is reported. Source: NFR8.3.
- T-CI-U5-13  ``delphi.tools.ci_preflight.main``, in process with a cleaned environment: a
  runtime-built fake key gives exit 3 and ``provider: env`` with the value absent from stdout
  and stderr; no key gives exit 0. One subprocess run shows the exit code through ``-m``.
  Source: NFR8.1, SD-U5-2.
- T-CI-U5-14  every ``# pragma: no cover`` COMMENT token under src/ carries `` - <why>``.
  Keep-fixed; seeded control: a bare pragma is found, a pragma named in a docstring is not.
  Source: NFR3.2, D-CG-U5-4.
- T-CI-U5-15  ``git check-ignore .cache/pip/x`` exits 0 (the CI pip cache is never tracked).
  Source: infrastructure-design R-01, D-CG-U5-5.
- guard       the autouse socket guard is active.

Partition: needs ``git``, ``bash`` and the dev extra (``ruff``, ``pytest-cov``). No account.
"""
from __future__ import annotations

import ast
import io
import os
import pathlib
import re
import shutil
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import subprocess
import sys
import tokenize
import tomllib

import boto3  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import botocore.credentials  # noqa: F401 - as above
import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
GITLAB_CI = REPO / ".gitlab-ci.yml"
GITHUB_CI = REPO / ".github" / "workflows" / "ci.yml"
CI_SH = REPO / "scripts" / "ci.sh"
PRE_COMMIT = REPO / ".pre-commit-config.yaml"
PYPROJECT = REPO / "pyproject.toml"
SCANNED_TOPS = ("src", "tests", "scripts", "infra")
EXCLUDED_PARTS = {"cdk.out", ".venv", "node_modules", "__pycache__", ".pytest_cache"}

NOQA_ANY = re.compile(r"#\s*noqa\b", re.I)
NOQA_FORM = re.compile(r"# noqa: [A-Z]+[0-9]+(,[A-Z]+[0-9]+)* - \S")
PRAGMA_ANY = re.compile(r"#\s*pragma:\s*no cover")
PRAGMA_FORM = re.compile(r"# pragma: no cover - \S")
FORBIDDEN_CI = re.compile(
    r"cdk deploy|cdk bootstrap|\baws\s|delphi-verify|delphi-artifact|delphi-scrub-check"
    r"|scrub_check"
)
CREDENTIAL_NAMES = (
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
    "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE", "id_tokens", "secrets:",
)
TRACKED = (".github/workflows/ci.yml", "scripts/ci.sh", ".pre-commit-config.yaml")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_ci_gate.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


# ---- helpers ---------------------------------------------------------------------------


def _run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """The one subprocess seam in this module; every argv is fixed by its caller."""
    kwargs.setdefault("capture_output", "stdout" not in kwargs)
    return subprocess.run(argv, text=True, check=False, **kwargs)  # noqa: S603 - fixed argv  # nosemgrep


def _git(*args: str, cwd: pathlib.Path = REPO) -> subprocess.CompletedProcess:
    return _run(["git", "-C", str(cwd), *args])


def ci_files() -> list[pathlib.Path]:
    """Every CI definition in the repository: any GitHub workflow, plus GitLab's if present."""
    found = [GITLAB_CI] if GITLAB_CI.exists() else []
    return found + sorted((REPO / ".github" / "workflows").glob("*.yml"))


def code_lines(text: str) -> list[str]:
    """Non-blank lines that are not whole-line comments (YAML and shell share ``#``)."""
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def forbidden_ci_lines(text: str) -> list[str]:
    return [ln for ln in code_lines(text) if FORBIDDEN_CI.search(ln)]


def comment_tokens(source: str) -> list[tuple[int, str]]:
    toks = tokenize.generate_tokens(io.StringIO(source).readline)
    return [(t.start[0], t.string) for t in toks if t.type == tokenize.COMMENT]


def bad_comments(source: str, any_re: re.Pattern, form_re: re.Pattern) -> list[int]:
    return [n for n, c in comment_tokens(source) if any_re.search(c) and not form_re.search(c)]


def python_files(*tops: str) -> list[pathlib.Path]:
    """`.py` files under each top, pruning excluded directories (infra/.venv is large)."""
    out = []
    for top in tops:
        for dirpath, dirnames, filenames in os.walk(REPO / top):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_PARTS)
            out += [pathlib.Path(dirpath) / f for f in sorted(filenames) if f.endswith(".py")]
    return out


def strict_false_xfails(source: str) -> list[int]:
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name != "xfail":
            continue
        for kw in node.keywords:
            if kw.arg == "strict" and isinstance(kw.value, ast.Constant) and not kw.value.value:
                out.append(node.lineno)
    return out


def workflow_problems(text: str) -> list[str]:
    """NFR8.3 for a GitHub workflow: read-only token, every action pinned by full SHA."""
    problems = []
    data = yaml.safe_load(text) or {}
    if (data.get("permissions") or {}).get("contents") != "read":
        problems.append("permissions: contents: read is missing")
    for m in re.finditer(r"uses:\s*(\S+)", text):
        if not re.search(r"@[0-9a-f]{40}$", m.group(1)):
            problems.append(f"unpinned action: {m.group(1)}")
    return problems


def pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def built_env(home: pathlib.Path, **extra: str) -> dict[str, str]:
    """The environment every child in this module gets: built, never inherited.

    No AWS_*, CI or DELPHI_* variable from the parent. HOME is a missing directory, so the
    shared AWS files resolve to nothing; IMDS is off. The two AWS file variables are not set
    at all (they are launcher-owned in the author's session), so they cannot point anywhere.
    """
    env = {
        "PATH": os.pathsep.join([str(pathlib.Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "HOME": str(home),
        "AWS_EC2_METADATA_DISABLED": "true",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(extra)
    return env


# ---- the seeded-copy harness (SD-U5-3) --------------------------------------------------

TRIVIAL_TEST = "def test_trivial():\n    assert 1 + 1 == 2\n"


def make_copy(root: pathlib.Path, *, fail_under: int = 0,
              files: dict[str, str] | None = None) -> pathlib.Path:
    """A minimal copy of the tree: ci.sh, pyproject.toml, src/, conftest.py, one test."""
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(CI_SH, root / "scripts" / "ci.sh")
    text, n = re.subn(r"(?m)^fail_under = \d+$", f"fail_under = {fail_under}",
                      PYPROJECT.read_text())
    assert n == 1, "pyproject.toml declares no single fail_under to seed"
    (root / "pyproject.toml").write_text(text)
    shutil.copytree(REPO / "src", root / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    (root / "tests").mkdir()
    shutil.copy2(REPO / "tests" / "conftest.py", root / "tests" / "conftest.py")
    (root / "tests" / "test_seeded.py").write_text(TRIVIAL_TEST)
    for rel, body in (files or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    return root


def run_gate(copy: pathlib.Path, **extra_env: str) -> subprocess.CompletedProcess:
    env = built_env(copy.parent / "no-home", DELPHI_REQUIRE_NO_AWS_CREDS="1", **extra_env)
    return _run(["bash", str(copy / "scripts" / "ci.sh")], cwd=copy, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)


def assert_row(res: subprocess.CompletedProcess, *, exit_code: int | None,
               contains: tuple[str, ...], excludes: tuple[str, ...]) -> None:
    out = res.stdout
    if exit_code is None:
        assert res.returncode != 0, out
    else:
        assert res.returncode == exit_code, out
    for needle in contains:
        assert needle in out, f"missing {needle!r} in:\n{out}"
    for needle in excludes:
        assert needle not in out, f"unexpected {needle!r} in:\n{out}"


CONTROL = dict(exit_code=0,
               contains=("==> preflight", "no AWS credentials resolve", "==> ruff",
                         "==> pytest"),
               excludes=("failed (exit",))


@pytest.fixture(scope="module")
def control_run(tmp_path_factory) -> subprocess.CompletedProcess:
    """Row (g), run once; every seeded row asserts it before its own seed means anything."""
    return run_gate(make_copy(tmp_path_factory.mktemp("ci-g") / "repo"))


def seeded(tmp_path: pathlib.Path, control: subprocess.CompletedProcess, **kw):
    assert_row(control, **CONTROL)
    extra_env = kw.pop("env", {})
    return run_gate(make_copy(tmp_path / "repo", **kw), **extra_env)


# ---- T-CI-U5-1 ... -2g ------------------------------------------------------------------


def test_t_ci_u5_1_github_ci_triggers_matrix_and_bounds():
    assert GITHUB_CI.exists(), ".github/workflows/ci.yml is missing"
    text = GITHUB_CI.read_text()
    data = yaml.safe_load(text)
    triggers = data[True]  # YAML 1.1 reads the bare key `on` as True
    assert triggers == {"pull_request": None, "push": {"branches": ["main"]}}, triggers
    (job,) = data["jobs"].values()
    assert job["strategy"]["matrix"] == {"python": ["3.11", "3.13"]}

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k != "environment", "a job declares an environment"
                assert k != "workflow_dispatch", "a manual trigger"
                assert "deploy" not in str(k).lower(), f"deploy key {k!r}"
                yield from walk(v, k)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v, key)
        else:
            yield key, node

    for _key, value in walk(data):
        assert "schedule" not in str(value), f"a schedule rule: {value!r}"
        assert "deploy" not in str(value).lower(), f"deploy step: {value!r}"
    assert job["timeout-minutes"] == 30
    lines = text.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.strip().startswith("timeout-minutes:"))
    assert "first recorded run" in lines[at - 1], "timeout carries no tuning comment"
    assert any(step.get("run") == "node --version" for step in job["steps"])


def test_t_ci_u5_2_every_ci_file_only_calls_the_entry_command():
    files = ci_files()
    assert files, "no CI file"
    for path in files:
        text = path.read_text()
        assert "bash scripts/ci.sh" in text, path
        own = [ln for ln in code_lines(text) if re.search(r"\b(pytest|ruff)\b", ln)]
        assert own == [], f"{path.name} runs a gate tool itself: {own}"
    assert CI_SH.exists(), "scripts/ci.sh is missing"
    body = code_lines(CI_SH.read_text())
    assert body[0] == "set -euo pipefail", body[:1]
    assert "unset DELPHI_ALLOW_UNSANDBOXED_EXEC" in body


def test_t_ci_u5_2g_control_copy_passes_every_step(control_run):
    assert_row(control_run, **CONTROL)


def test_t_ci_u5_2a_a_failing_test_stops_the_gate_at_pytest(tmp_path, control_run):
    bad = "def test_seeded_failure():\n    assert 1 == 2\n"
    res = seeded(tmp_path, control_run, files={"tests/test_seeded.py": bad})
    assert_row(res, exit_code=None,
               contains=("step 'pytest' failed",
                         "FAILED tests/test_seeded.py::test_seeded_failure"),
               excludes=("not reached",))


def test_t_ci_u5_2b_a_lint_finding_stops_the_gate_at_ruff(tmp_path, control_run):
    res = seeded(tmp_path, control_run, files={"src/delphi/_seeded.py": 'X = eval("1")\n'})
    assert_row(res, exit_code=None, contains=("step 'ruff' failed", "S307", "_seeded.py"),
               excludes=("==> pytest",))


def test_t_ci_u5_2c_the_floor_is_read_from_configuration(tmp_path, control_run):
    res = seeded(tmp_path, control_run, fail_under=100)
    assert_row(res, exit_code=None,
               contains=("step 'pytest' failed", "Required test coverage of 100", "not reached"),
               excludes=("is invalid", "FAILED tests/"))


def test_t_ci_u5_2d_a_resolvable_credential_stops_the_gate_first(tmp_path, control_run):
    fake_key = "AKIA" + "Z" * 16
    fake_secret = "z" * 40
    res = seeded(tmp_path, control_run,
                 env={"AWS_ACCESS_KEY_ID": fake_key, "AWS_SECRET_ACCESS_KEY": fake_secret})
    assert_row(res, exit_code=3, contains=("step 'preflight' failed", "provider: env"),
               excludes=("==> ruff", fake_key, fake_secret))


def test_t_ci_u5_2e_a_strict_xpass_fails_the_gate(tmp_path, control_run):
    xpass = ("import pytest\n\n\n@pytest.mark.xfail(reason=\"seeded\")\n"
             "def test_seeded_xpass():\n    assert True\n")
    res = seeded(tmp_path, control_run, files={"tests/test_seeded.py": xpass})
    assert_row(res, exit_code=None, contains=("step 'pytest' failed", "XPASS(strict)"),
               excludes=("not reached",))


def test_t_ci_u5_2f_a_skipped_test_fails_the_gate(tmp_path, control_run):
    skip = "import pytest\n\n\ndef test_seeded_skip():\n    pytest.skip(\"seeded\")\n"
    res = seeded(tmp_path, control_run, files={"tests/test_seeded.py": skip})
    assert_row(res, exit_code=None, contains=("step 'pytest' failed", "ci: 1 skipped test"),
               excludes=("not reached",))


def test_t_ci_u5_2h_an_author_record_skip_is_accepted(tmp_path, control_run):
    skip = ("import pytest\n\n\n@pytest.mark.author_record(\"no-such-record\")\n"
            "def test_seeded_record():\n    assert False\n")
    res = seeded(tmp_path, control_run, files={"tests/test_seeded.py": skip})
    assert_row(res, exit_code=0, contains=("ci: 1 author-record test(s) skipped",),
               excludes=("failed (exit",))


# ---- T-CI-U5-3 ... -8 -------------------------------------------------------------------


def test_t_ci_u5_3_ci_definition_and_entry_command_ship():
    listed = set(_git("ls-files", "--cached", "--others", "--exclude-standard").stdout.split())
    for rel in TRACKED:
        assert rel in listed, f"{rel} is absent or ignored"
        assert _git("check-ignore", "-q", rel).returncode == 1, f"{rel} is ignored"


def test_t_ci_u5_4_coverage_floor_is_configuration():
    data = pyproject()
    cov = data["tool"].get("coverage", {})
    assert cov.get("run", {}).get("source") == ["src"]
    assert cov.get("report", {}).get("fail_under") == 80
    assert "--cov" not in data["tool"]["pytest"]["ini_options"]["addopts"]
    assert CI_SH.exists(), "scripts/ci.sh is missing"
    assert "--cov-fail-under" not in CI_SH.read_text()


def test_t_ci_u5_5_ruff_configuration_and_bound():
    data = pyproject()
    ruff = data["tool"].get("ruff", {})
    assert set(ruff) == {"line-length", "extend-exclude", "lint"}, sorted(ruff)
    assert ruff["line-length"] == 100
    assert ruff["extend-exclude"] == ["infra/cdk.out", ".venv", "infra/.venv"]
    assert ruff["lint"] == {"select": ["E", "BLE", "S"],
                            "per-file-ignores": {"tests/**": ["S101"]}}
    assert "ruff>=0.15,<0.16" in data["project"]["optional-dependencies"]["dev"]


def test_t_ci_u5_6_every_noqa_names_its_rule_and_reason():
    seeded_src = ('import x  # noqa\nimport y  # noqa: E402\nS = "# noqa"\n'
                  'import z  # noqa: E402 - after the shim\n')
    assert bad_comments(seeded_src, NOQA_ANY, NOQA_FORM) == [1, 2]
    bad = []
    for path in python_files(*SCANNED_TOPS):
        for n in bad_comments(path.read_text(), NOQA_ANY, NOQA_FORM):
            bad.append(f"{path.relative_to(REPO)}:{n}")
    assert bad == [], f"noqa without a rule and a reason: {bad}"


def test_t_ci_u5_7_gitleaks_is_a_pinned_local_hook():
    assert PRE_COMMIT.exists(), ".pre-commit-config.yaml is missing"
    listed = set(_git("ls-files", "--cached", "--others", "--exclude-standard").stdout.split())
    assert ".pre-commit-config.yaml" in listed
    repos = yaml.safe_load(PRE_COMMIT.read_text())["repos"]
    (repo,) = [r for r in repos if any(h["id"] == "gitleaks" for h in r["hooks"])]
    assert re.fullmatch(r"v\d+\.\d+\.\d+|[0-9a-f]{40}", repo["rev"]), repo["rev"]
    for path in ci_files():
        assert "gitleaks" not in path.read_text().lower(), path


def test_t_ci_u5_8_xfail_is_strict_and_skips_fail_ci():
    opts = pyproject()["tool"]["pytest"]["ini_options"]
    assert opts.get("xfail_strict") is True
    assert CI_SH.exists(), "scripts/ci.sh is missing"
    sh = code_lines(CI_SH.read_text())
    assert "export DELPHI_FAIL_ON_SKIP=1" in sh
    (pytest_line,) = [ln for ln in sh if "-m pytest" in ln]
    assert re.search(r"\s-r[a-zA-Z]*s", pytest_line), pytest_line
    seeded_src = '@pytest.mark.xfail(reason="r", strict=False)\ndef t(): pass\n'
    assert strict_false_xfails(seeded_src) == [1]
    offenders = [f"{p.relative_to(REPO)}:{n}"
                 for p in python_files("tests") for n in strict_false_xfails(p.read_text())]
    assert offenders == [], f"strict=False overrides xfail_strict: {offenders}"


# ---- T-CI-U5-9a / -9b: an ordered pair; keep them adjacent -----------------------------

U5_ONLY_ALIASES = ("u5-only-alpha", "u5-only-beta")
U5_POOL_YAML = """\
pricing_snapshot: {publication_date: "2026-10-19"}
judge: {id: fixture.judge, alias: judge}
candidates:
  - {alias: u5-only-alpha, id: fixture.alpha, family: a, price_per_1k: {input: 0.1, output: 0.2}}
  - {alias: u5-only-beta, id: fixture.beta, family: b, price_per_1k: {input: 0.3, output: 0.4}}
"""


def test_t_ci_u5_9a_load_pool_reads_the_named_models_file(tmp_path, monkeypatch):
    from delphi.config import load_pool

    models = tmp_path / "models.yaml"
    models.write_text(U5_POOL_YAML)
    monkeypatch.setenv("DELPHI_MODELS_FILE", str(models))
    assert load_pool().aliases == U5_ONLY_ALIASES


def test_t_ci_u5_9b_the_pool_cache_does_not_leak_between_tests(monkeypatch):
    from delphi.config import load_pool

    monkeypatch.delenv("DELPHI_MODELS_FILE", raising=False)
    expected = tuple(c["alias"] for c in
                     yaml.safe_load((REPO / "config" / "models.yaml").read_text())["candidates"])
    assert load_pool().aliases == expected


# ---- T-CI-U5-10 ... -15 -----------------------------------------------------------------


def test_t_ci_u5_10_ci_holds_no_credential_and_disables_imds():
    assert GITHUB_CI.exists(), ".github/workflows/ci.yml is missing"
    variables = yaml.safe_load(GITHUB_CI.read_text())["env"]
    assert variables["AWS_EC2_METADATA_DISABLED"] == "true"
    assert variables["DELPHI_REQUIRE_NO_AWS_CREDS"] == "1"
    for path in ci_files():
        text = path.read_text()
        named = [n for n in CREDENTIAL_NAMES if n in text]
        assert named == [], f"{path.name} names {named}"


def test_t_ci_u5_11_ci_runs_nothing_that_writes_to_aws():
    assert forbidden_ci_lines("script:\n  - npx cdk deploy --all\n") == ["  - npx cdk deploy --all"]
    assert forbidden_ci_lines("# never run cdk deploy here\n") == []
    assert CI_SH.exists(), "scripts/ci.sh is missing"
    files = [*ci_files(), CI_SH]
    assert GITHUB_CI in files, ".github/workflows/ci.yml is missing"
    for path in files:
        assert forbidden_ci_lines(path.read_text()) == [], path


def test_t_ci_u5_12_ci_never_opts_in_to_execution_and_pins_actions():
    tag_pinned = "permissions: {contents: read}\nsteps:\n  - uses: actions/checkout@v4\n"
    assert workflow_problems(tag_pinned) == ["unpinned action: actions/checkout@v4"]
    files = ci_files()
    assert files, "no CI file"
    for path in files:
        assert "DELPHI_ALLOW_UNSANDBOXED_EXEC" not in path.read_text(), path
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        assert workflow_problems(path.read_text()) == [], path


def _clean_aws_env(monkeypatch, tmp_path: pathlib.Path) -> None:
    for name in [k for k in os.environ if k.startswith("AWS_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(tmp_path / "no-home"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def test_t_ci_u5_13_preflight_fails_when_a_credential_resolves(tmp_path, monkeypatch, capsys):
    import delphi.tools as tools_pkg
    import importlib

    preflight = importlib.import_module(f"{tools_pkg.__name__}.ci_preflight")
    _clean_aws_env(monkeypatch, tmp_path)
    assert preflight.main([]) == 0
    assert "no AWS credentials resolve" in capsys.readouterr().out

    fake_key, fake_secret = "AKIA" + "Q" * 16, "q" * 40  # pragma: allowlist secret - a fake
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", fake_key)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", fake_secret)
    assert preflight.main([]) == 3
    out = capsys.readouterr()
    assert "provider: env" in out.out + out.err
    for value in (fake_key, fake_secret):
        assert value not in out.out + out.err

    res = _run([sys.executable, "-m", "delphi.tools.ci_preflight"],
               env=built_env(tmp_path / "no-home", PYTHONPATH=str(REPO / "src")))
    assert res.returncode == 0, res.stdout + res.stderr


def test_t_ci_u5_14_every_coverage_pragma_carries_a_reason():
    seeded_src = ('def f():  # pragma: no cover\n    pass\n"""a `# pragma: no cover` note"""\n'
                  'def g():  # pragma: no cover - needs live AWS\n    pass\n')
    assert bad_comments(seeded_src, PRAGMA_ANY, PRAGMA_FORM) == [1]
    bad = [f"{p.relative_to(REPO)}:{n}" for p in python_files("src")
           for n in bad_comments(p.read_text(), PRAGMA_ANY, PRAGMA_FORM)]
    assert bad == [], f"pragma without a reason: {bad}"


def test_t_ci_u5_15_the_ci_pip_cache_is_ignored():
    assert _git("check-ignore", "-q", ".cache/pip/x").returncode == 0


def test_u5_ci_gate_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
