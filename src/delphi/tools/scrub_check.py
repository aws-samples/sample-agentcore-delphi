"""`delphi-scrub-check`: author identifiers in what would be published (LC-U5-9; SD-U5-13,
SD-U5-14; FR9.1.1-FR9.1.5, contract C5). Run it locally, last, on the tree you publish.

    delphi-scrub-check [PATH]             every file git tracks, scanned as bytes
    delphi-scrub-check --history [PATH]   every commit's added and removed lines

Output is `path:line:class` (or `commit:class` with --history) on stdout and a count line on
stderr. The matched value and the text around it are never printed, so the output can go into
a post, a log or a screenshot. A tracked path that itself matches a class is printed as
`<redacted-path>`, with line 0 for the path's own hit. Exit codes: 0 no hit, 1 any hit, 2 when
PATH is not inside a git work tree or git is not on PATH.

Two classes, each a generic pattern, so this module carries no identifier and cannot find one
in itself:

    account  a run of exactly 12 digits bounded by non-alphanumerics (an AWS account id shape)
    home     `/Users/<segment>` or `/home/<segment>` (a home-directory path)

The rules, by design (SD-U5-14): every tracked file is scanned, binaries included; there is no
path filter, no exclusion list and no inline suppression token. A hit that is not an
identifier is resolved only by adding that exact value, with its reason, to `ALLOW` below, so
every exception is a reviewed diff in this published module. A pattern is never loosened.
Stated limit: the allowlist is reviewed by its own author and is visible, not tamper-proof.

Local only: CI never runs it (the tree stays red until publication), and it is outside the
serving image's import closure (T-PKG-U3-3c). Standard library only.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass

EXIT_CLEAN = 0
EXIT_HITS = 1
EXIT_UNUSABLE = 2
REDACTED = "<redacted-path>"

ACCOUNT = re.compile(rb"(?<![A-Za-z0-9])[0-9]{12}(?![A-Za-z0-9])")
HOME = re.compile(rb"/(?:Users|home)/([A-Za-z0-9][A-Za-z0-9._-]*)")
CLASSES: dict[str, re.Pattern[bytes]] = {"account": ACCOUNT, "home": HOME}

#: class -> exact value -> reason. Closed and in code: extend it only by an exact value with a
#: reason. For `account` the value is the 12 digits; for `home` it is the path segment.
ALLOW: dict[str, dict[bytes, str]] = {
    "account": {
        b"111111111111": "AWS documentation placeholder account id",
        b"111122223333": "AWS documentation placeholder account id",
        b"444455556666": "AWS documentation placeholder account id",
        b"123456789012": "AWS documentation placeholder account id",
        b"289376278409": "float fraction digits in results/v2 results, not an account id",
        b"838210037044": "float fraction digits in results/v3 results, not an account id",
    },
    "home": {
        b"someone": "test-fixture home directory name",
        b"x": "test-fixture home directory name",
    },
}


class ScrubError(RuntimeError):
    """git could not be run, or did not answer, for the directory given."""


@dataclass(frozen=True, order=True)
class Hit:
    where: str   # a repo-relative path, REDACTED, or a commit id (history mode)
    line: int    # 1-based line of the hit; 0 for a path's own hit and for history hits
    cls: str


def _value(cls: str, match: re.Match[bytes]) -> bytes:
    return match.group(1) if cls == "home" else match.group(0)


def classes_in(data: bytes) -> list[str]:
    """The classes with at least one non-allowlisted hit in `data`."""
    return [cls for cls, pattern in CLASSES.items()
            if any(_value(cls, m) not in ALLOW[cls] for m in pattern.finditer(data))]


def scan_blob(path: str, data: bytes) -> list[Hit]:
    """Every hit in one tracked file, its path included; the path is redacted if it hits."""
    raw_path = os.fsencode(path)
    path_classes = classes_in(raw_path)
    shown = REDACTED if path_classes else path
    hits = {Hit(REDACTED, 0, cls) for cls in path_classes}
    for cls, pattern in CLASSES.items():
        for m in pattern.finditer(data):
            if _value(cls, m) not in ALLOW[cls]:
                hits.add(Hit(shown, data.count(b"\n", 0, m.start()) + 1, cls))
    return sorted(hits)


def _git(git: str, root: pathlib.Path, *args: str) -> bytes:
    res = subprocess.run([git, "-C", str(root), *args], capture_output=True, check=False)  # noqa: S603 - fixed argv: the resolved git binary  # nosemgrep
    if res.returncode != 0:
        # git's own message is not echoed: it can quote a path, and a path can be an identifier.
        raise ScrubError(f"git {args[0]} failed (exit {res.returncode})")
    return res.stdout


def _read_tracked(path: pathlib.Path) -> bytes | None:
    """A tracked file's bytes (a symlink's target text, as git stores it); None if absent."""
    if path.is_symlink():
        return os.fsencode(os.readlink(path))
    if path.is_file():
        return path.read_bytes()
    return None


def scan_tree(root: pathlib.Path, git: str = "git") -> list[Hit]:
    """Hits in every file `git ls-files` lists under `root` (the work-tree top)."""
    hits: list[Hit] = []
    for raw in _git(git, root, "ls-files", "-z").split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        data = _read_tracked(root / rel)
        if data is None:
            shown = REDACTED if classes_in(raw) else rel
            print(f"delphi-scrub-check: tracked but absent from the work tree: {shown}",
                  file=sys.stderr)
            continue
        hits += scan_blob(rel, data)
    return sorted(hits)


def scan_history(root: pathlib.Path, git: str = "git") -> list[Hit]:
    """One Hit per (commit, class) over the added and removed lines of every commit."""
    log = _git(git, root, "log", "-p", "--all", "--text", "--no-color", "--no-ext-diff",
               "--format=%x00%H")
    hits: list[Hit] = []
    for chunk in log.split(b"\0")[1:]:
        sha, _, diff = chunk.partition(b"\n")
        changed = b"\n".join(ln for ln in diff.splitlines() if ln[:1] in (b"+", b"-"))
        hits += [Hit(sha.decode(), 0, cls) for cls in classes_in(changed)]
    return hits


def _work_tree_top(git: str, path: pathlib.Path) -> pathlib.Path | None:
    res = subprocess.run([git, "-C", str(path), "rev-parse", "--show-toplevel"],  # noqa: S603 - fixed argv: the resolved git binary  # nosemgrep
                         capture_output=True, check=False)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    return pathlib.Path(os.fsdecode(res.stdout.strip()))


def _report(hits: list[Hit], history: bool) -> None:
    for hit in hits:
        print(f"{hit.where}:{hit.cls}" if history else f"{hit.where}:{hit.line}:{hit.cls}")
    by_class = Counter(hit.cls for hit in hits)
    places = len({hit.where for hit in hits})
    unit = "commit(s)" if history else "file(s)"
    detail = ", ".join(f"{cls}={by_class[cls]}" for cls in CLASSES if by_class[cls]) or "none"
    print(f"delphi-scrub-check: {len(hits)} hit(s) in {places} {unit}; {detail}",
          file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="delphi-scrub-check", description=__doc__.split("\n")[0])
    parser.add_argument("path", nargs="?", default=".", help="a directory inside the work tree")
    parser.add_argument("--history", action="store_true",
                        help="scan every commit's added and removed lines instead of the tree")
    args = parser.parse_args(argv)
    git = shutil.which("git")
    if git is None:
        print("delphi-scrub-check: git is not on PATH", file=sys.stderr)
        return EXIT_UNUSABLE
    top = _work_tree_top(git, pathlib.Path(args.path))
    if top is None:
        print("delphi-scrub-check: not inside a git work tree", file=sys.stderr)
        return EXIT_UNUSABLE
    try:
        hits = scan_history(top, git) if args.history else scan_tree(top, git)
    except ScrubError as exc:
        print(f"delphi-scrub-check: {exc}", file=sys.stderr)
        return EXIT_UNUSABLE
    _report(hits, args.history)
    return EXIT_HITS if hits else EXIT_CLEAN


def _entry() -> None:  # pragma: no cover - console-script shim
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover - module execution shim
    sys.exit(main(sys.argv[1:]))
