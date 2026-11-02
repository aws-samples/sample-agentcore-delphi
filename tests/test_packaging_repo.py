"""Repository completeness — U3 Layer 1 (SD-17, SD-18; NFR10.9, NFR10.10, AC1.1.1-AC1.1.3).

The defect class these guard against is real and already happened once: an unanchored
`data/` in .gitignore also matched `src/delphi/data/`, so that package was missing from every
clone (fixed in bb18f3e). A clean clone must collect the same suite the author runs.

  T-PKG-U3-21  `git archive HEAD` extracted into tmp_path collects with ZERO collection errors,
               and every `tests/test_*.py` tracked at HEAD collects at least one item there.
               Also `git ls-files --others --ignored --exclude-standard src/` lists no `.py`
               (plan D-CG-4: "ignored", not merely "untracked", which is the normal state of a
               new module before its commit). Keep-fixed (F16). Negative control: a scratch repo
               whose unanchored `data/` pattern ignores `src/pkg/data/x.py`, which the helper
               must report.
  T-PKG-U3-22  `git check-ignore --no-index src/delphi/<name>/x.py` reports NOT ignored for each
               of data, artifacts, build, dist, htmlcov and logs (the intended-top-level set is
               root-anchored). Positive control: `infra/node_modules/x` IS ignored, because
               node_modules stays unanchored (SD-18). The framework block markers
               `# BEGIN AI-DLC:gitignore` / `# END AI-DLC:gitignore` are still present and none
               of the six patterns sits between them. Negative control: a scratch repo with an
               unanchored `build/`, which the helper must report as ignoring a nested path.

Partition A: needs `git`, no SDK, no account.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tarfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ANCHORED = ("data", "artifacts", "build", "dist", "htmlcov", "logs")
BEGIN, END = "# BEGIN AI-DLC:gitignore", "# END AI-DLC:gitignore"


def _git(repo: pathlib.Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,  # noqa: S603,S607 - fixed argv: the git CLI
                          check=check)


def ignored_py_under_src(repo: pathlib.Path) -> list[str]:
    """`.py` files under src/ that git ignores (and so would be missing from a clone)."""
    out = _git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "src/").stdout
    return sorted(p for p in out.splitlines() if p.endswith(".py"))


def is_ignored(repo: pathlib.Path, path: str) -> bool:
    """True when git's ignore rules match `path` (check-ignore exits 0 on a match)."""
    res = _git(repo, "check-ignore", "--no-index", "-q", path, check=False)
    if res.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore failed: {res.stderr}")
    return res.returncode == 0


def _scratch_repo(tmp_path: pathlib.Path, gitignore: str, *, name: str = "scratch") -> pathlib.Path:
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(gitignore)
    return repo


# --- T-PKG-U3-21 ---------------------------------------------------------------
def _collect_counts(output: str) -> dict[str, int]:
    """Per-file item counts from `pytest --collect-only` output in either -q or -qq form."""
    counts: dict[str, int] = {}
    for line in output.splitlines():
        m = re.match(r"^(tests/test_[\w]+\.py)::", line)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            continue
        m = re.match(r"^(tests/test_[\w]+\.py): (\d+)$", line)
        if m:
            counts[m.group(1)] = int(m.group(2))
    return counts


def test_t_pkg_u3_21_fresh_archive_collects_every_tracked_test_file(tmp_path):
    archive = tmp_path / "head.tar"
    _git(ROOT, "archive", "--format=tar", "-o", str(archive), "HEAD")
    tree = tmp_path / "tree"
    tree.mkdir()
    with tarfile.open(archive) as tf:
        tf.extractall(tree, filter="data")

    tracked = sorted(
        p for p in _git(ROOT, "ls-files", "tests/").stdout.splitlines()
        if re.fullmatch(r"tests/test_[\w]+\.py", p)
    )
    assert tracked, "no tracked test files at HEAD"

    env = {k: v for k, v in os.environ.items() if not k.startswith("DELPHI_")}
    # `-m ""` lifts the default `not image` deselection so a file holding only the image
    # test still lists its item; --collect-only runs nothing.
    res = subprocess.run(  # fixed argv list, never a shell  # nosemgrep
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-m", "", "-p",
         "no:cacheprovider"],
        cwd=tree, capture_output=True, text=True, env=env,
    )
    assert res.returncode == 0, (f"collection failed in the fresh archive:\n{res.stdout}\n"
                                 f"{res.stderr}")
    assert not re.search(r"\b\d+ errors?\b", res.stdout), res.stdout
    counts = _collect_counts(res.stdout)
    empty = [p for p in tracked if counts.get(p, 0) < 1]
    assert empty == [], f"tracked test files collecting nothing from a clean clone: {empty}"


def test_t_pkg_u3_21_nothing_under_src_is_ignored():
    assert ignored_py_under_src(ROOT) == []


def test_t_pkg_u3_21_negative_control_reports_an_ignored_package(tmp_path):
    repo = _scratch_repo(tmp_path, "data/\n")
    target = repo / "src" / "pkg" / "data" / "x.py"
    target.parent.mkdir(parents=True)
    target.write_text("X = 1\n")
    assert ignored_py_under_src(repo) == ["src/pkg/data/x.py"]


# --- T-PKG-U3-22 ---------------------------------------------------------------
@pytest.mark.parametrize("name", ANCHORED)
def test_t_pkg_u3_22_nested_package_named_like_a_top_level_dir_is_not_ignored(name):
    assert not is_ignored(ROOT, f"src/delphi/{name}/x.py"), (
        f".gitignore pattern for {name!r} is unanchored and matches a nested package"
    )


def test_t_pkg_u3_22_node_modules_stays_ignored_at_any_depth():
    assert is_ignored(ROOT, "infra/node_modules/x")


def test_t_pkg_u3_22_anchored_patterns_sit_outside_the_framework_block():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    if BEGIN not in lines:  # the published sample ships no framework-managed block
        assert END not in lines, "an END marker without its BEGIN"
        return
    assert END in lines
    block = lines[lines.index(BEGIN) + 1: lines.index(END)]
    inside = [ln for ln in block if ln.strip().strip("/") in ANCHORED]
    assert inside == [], f"anchored patterns inside the framework-managed block: {inside}"


def test_t_pkg_u3_22_negative_control_reports_an_unanchored_pattern(tmp_path):
    unanchored = _scratch_repo(tmp_path, "build/\n", name="unanchored")
    assert is_ignored(unanchored, "src/pkg/build/x.py")
    anchored = _scratch_repo(tmp_path, "/build/\n", name="anchored")
    assert not is_ignored(anchored, "src/pkg/build/x.py")
