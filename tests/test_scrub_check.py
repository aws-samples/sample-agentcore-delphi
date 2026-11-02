"""The identifier check — U5 LC-U5-10 (FR9.1.1-FR9.1.5, FR9.3.1; US4.4 AC4.4.1-AC4.4.2).

The register below was written before ``delphi.tools.scrub_check`` existed (team TDD
ordering). ``delphi-scrub-check`` reports every author identifier in the files git tracks, as
``path:line:class`` only, so its output can be pasted anywhere: it never prints the value it
found. Every hit-shaped string in this module is built at runtime (``"9" * 12``,
``"/Users/" + "alicex"``), so this module carries no identifier for the check to find. The
git repositories are temporary, under tmp_path, and every commit passes its identity
explicitly with git's global and system configuration switched off, so nothing here depends on
the author's own git setup (nfr-design R-02). No AWS call and no network.

Register:

- T-SCR-U5-1  a temporary repository seeded with an account-shaped id and a home path exits 1
  and reports both as ``path:line:class``, and neither value appears in stdout or stderr; a
  clean repository exits 0; a directory outside any git work tree exits 2. In process and as
  ``python -m delphi.tools.scrub_check``. Source: FR9.1.1, AC4.4.1, contract C5.
- T-SCR-U5-2  the AWS documentation placeholder ids and the test-fixture home paths are not
  reported, and scanning the check's own module and this test module gives zero hits.
  Source: FR9.1.2.
- T-SCR-U5-3  ``--history`` over a two-commit repository reports the commit that added the
  value, with its class only. Source: FR9.1.3.
- T-SCR-U5-4  one README sentence says the base image is pinned by digest, and one says the
  transitive dependencies are not locked. Source: FR9.3.1, AC4.4.2.
- T-SCR-U5-5  the two pattern texts equal SD-U5-13's literals; every ``ALLOW`` entry has a
  reason, and the account allowlist is the four documentation placeholders plus reviewed
  float-fraction entries; a hit beside
  ``# noqa``, ``# nosec``, ``scrub: ignore`` or ``gitleaks:allow``, and hits under ``aidlc/``,
  ``tests/``, a CI file and in a binary file, are all reported (no suppression token
  and no path escapes the scan). Source: FR9.1.2, nfr-requirements R-04, SD-U5-14.
- T-SCR-U5-6  a tracked file whose path matches a class is printed as ``<redacted-path>``,
  and the path text never appears. Source: FM-16.
- guard       the autouse socket guard is active.

Partition: needs ``git``. No SDK, no account.
"""
from __future__ import annotations

import importlib
import os
import pathlib
import re
import socket
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
ACCOUNT_VALUE = "9" * 12
HOME_VALUE = "/Users/" + "alicex"
PLACEHOLDERS = ("111111111111", "111122223333", "444455556666", "123456789012")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_scrub_check.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def scrub():
    """The module under test, reached at call time so a Red run fails per test."""
    return importlib.import_module("delphi.tools.scrub_check")


# ---- the one git helper -----------------------------------------------------------------

IDENTITY = ("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false")


def git_env(home: pathlib.Path) -> dict[str, str]:
    """git with no global or system configuration, so identity must be passed explicitly."""
    return {"PATH": os.pathsep.join([str(pathlib.Path(sys.executable).parent), "/usr/bin", "/bin"]),
            "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    res = subprocess.run(["git", "-C", str(repo), *IDENTITY, *args], capture_output=True,  # noqa: S603,S607 - fixed argv: the git CLI
                         text=True, check=False, env=git_env(repo.parent / "no-home"))
    assert res.returncode == 0, res.stderr
    return res


def seeded_repo(root: pathlib.Path, *commits: dict[str, bytes]) -> pathlib.Path:
    """A git repository with one commit per mapping of relative path -> file bytes."""
    root.mkdir(parents=True)
    git(root, "init", "-q")
    for n, files in enumerate(commits, 1):
        for rel, data in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", f"commit {n}")
    return root


def run_cli(path: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(git_env(path.parent / "no-home"), PYTHONPATH=str(REPO / "src"))
    return subprocess.run([sys.executable, "-m", "delphi.tools.scrub_check", *args, str(path)],  # noqa: S603 - fixed argv: this interpreter + the module  # nosemgrep
                          capture_output=True, text=True, check=False, env=env, timeout=120)


def run_main(capsys, *argv: str) -> tuple[int, str, str]:
    code = scrub().main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# ---- tests ------------------------------------------------------------------------------


def test_t_scr_u5_1_reports_classes_never_values(tmp_path, capsys):
    dirty = seeded_repo(tmp_path / "dirty", {
        "a.txt": f"first line\nid {ACCOUNT_VALUE} here\n".encode(),
        "notes/b.md": f"see {HOME_VALUE}/repo\n".encode(),
    })
    code, out, err = run_main(capsys, str(dirty))
    assert code == 1, out + err
    assert out.splitlines() == ["a.txt:2:account", "notes/b.md:1:home"]
    for value in (ACCOUNT_VALUE, HOME_VALUE, "alicex"):
        assert value not in out + err

    clean = seeded_repo(tmp_path / "clean", {"a.txt": b"nothing to see\n"})
    assert run_main(capsys, str(clean))[0] == 0
    outside = tmp_path / "outside"
    outside.mkdir()
    assert run_main(capsys, str(outside))[0] == 2

    for path, want in ((dirty, 1), (clean, 0), (outside, 2)):
        res = run_cli(path)
        assert res.returncode == want, res.stdout + res.stderr
        assert ACCOUNT_VALUE not in res.stdout + res.stderr


def test_t_scr_u5_2_placeholders_fixtures_and_itself_are_not_reported(tmp_path, capsys):
    fixtures = "\n".join([*PLACEHOLDERS, "/Users/someone/x", "/home/someone/y", "/Users/x/z"])
    repo = seeded_repo(tmp_path / "repo", {"docs.md": fixtures.encode() + b"\n"})
    code, out, err = run_main(capsys, str(repo))
    assert (code, out) == (0, ""), out + err
    for path in (REPO / "src" / "delphi" / "tools" / "scrub_check.py", pathlib.Path(__file__)):
        rel = str(path.relative_to(REPO))
        assert scrub().scan_blob(rel, path.read_bytes()) == [], rel


def test_t_scr_u5_3_history_names_the_commit_and_the_class(tmp_path, capsys):
    repo = seeded_repo(tmp_path / "repo", {"a.txt": b"clean\n"},
                       {"a.txt": f"clean\nnow {ACCOUNT_VALUE}\n".encode()})
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    code, out, err = run_main(capsys, "--history", str(repo))
    assert code == 1, out + err
    assert out.splitlines() == [f"{head}:account"]
    assert ACCOUNT_VALUE not in out + err


def readme_sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", " ".join(text.split()))


def test_t_scr_u5_4_the_dependency_posture_is_stated():
    assert not any("base image" in s and "digest" in s
                   for s in readme_sentences("Dependencies float. The base image too."))
    sents = readme_sentences((REPO / "README.md").read_text())
    assert sum("base image" in s and "by digest" in s for s in sents) == 1
    assert sum("transitive" in s and "not locked" in s for s in sents) == 1


def test_t_scr_u5_5_patterns_are_pinned_and_nothing_escapes(tmp_path, capsys):
    mod = scrub()
    assert mod.ACCOUNT.pattern == rb"(?<![A-Za-z0-9])[0-9]{12}(?![A-Za-z0-9])"
    assert mod.HOME.pattern == rb"/(?:Users|home)/([A-Za-z0-9][A-Za-z0-9._-]*)"
    assert set(mod.ALLOW) == {"account", "home"}
    placeholders = {p.encode() for p in PLACEHOLDERS}
    assert placeholders <= set(mod.ALLOW["account"])
    # Any other account entry is a reviewed non-identifier: a float's fraction digits.
    extra = {v: r for v, r in mod.ALLOW["account"].items() if v not in placeholders}
    assert all("float fraction digits" in r and "not an account id" in r for r in extra.values())
    assert all(reason.strip() for entries in mod.ALLOW.values() for reason in entries.values())
    tokens = ("# noqa", "# nosec", "# scrub: ignore", "# gitleaks:allow")
    value = ACCOUNT_VALUE.encode()
    repo = seeded_repo(tmp_path / "repo", {
        "suppressed.py": b"".join(b"x = '" + value + b"'  " + t.encode() + b"\n" for t in tokens),
        "aidlc/record.md": value + b"\n",
        "tests/fixture.txt": value + b"\n",
        "ci.yml": b"variables: {A: '" + value + b"'}\n",
        "blob.bin": b"\x00\x01" + value + b"\x00\n",
    })
    code, out, _ = run_main(capsys, str(repo))
    assert code == 1
    assert sorted(out.splitlines()) == sorted(
        [f"suppressed.py:{n}:account" for n in range(1, 5)]
        + ["aidlc/record.md:1:account", "tests/fixture.txt:1:account",
           "ci.yml:1:account", "blob.bin:1:account"])


def test_t_scr_u5_6_a_path_that_matches_is_redacted(tmp_path, capsys):
    repo = seeded_repo(tmp_path / "repo", {f"exports/{ACCOUNT_VALUE}.txt": b"clean\n"})
    code, out, err = run_main(capsys, str(repo))
    assert code == 1
    assert out.splitlines() == ["<redacted-path>:0:account"]
    assert ACCOUNT_VALUE not in out + err


def test_u5_scrub_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
