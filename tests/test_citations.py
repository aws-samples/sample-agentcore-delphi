"""Every section reference resolves — U4 LC-U4-10 (BR3.1-BR3.4, FR6.4, AC2.5.1).

A citation is provenance, and a dead one sends a reader nowhere. This check walks the
code-and-docs corpus and resolves every section-sign reference through a CLOSED set of forms;
an occurrence that matches no form, names an unregistered file, or points at an absent
heading is a failure, never a skip (WF-4).

Forms (ENT-409):
  design_section      `design <sign>N[.M]` -> docs/design.md. A bare `<sign>N[.M]` means the
                      same, and is allowed only in Markdown files: in code every reference must
                      be qualified, so an abbreviation like `LC <sign>3` fails instead of
                      resolving silently to design section 3.
  named_file_section  `<file>.md <sign> <heading or N>` -> the REGISTRY entry for that exact
                      key, which is a unique path. A bare non-unique record file name is not a
                      key (BR3.4).
  contract_section    `C<n> <sign> <heading>` -> the contract summary.
A numeric token (N or N.M) matches a heading whose leading number is that token. Text matches
when some heading of the target, normalised (case-folded; the dashes, commas and colons
dropped; whitespace collapsed), is a prefix of the normalised text after the sign. Fenced code
in a target is not a heading. The corpus is src/, tests/, scripts/, infra/ (without cdk.out
and .venv), docs/, config/ and README.md (plus CLAUDE.md and STATUS.md where present); the
aidlc/ and .claude/ trees are outside it (P-1). Where the design record is not shipped (a
published sample), T-CIT-U4-1 and the rows that target it skip as author-record tests. This
file builds the sign with chr(), so it carries none itself.

  T-CIT-U4-1  the corpus has zero problems (BR3.1, BR3.4, AC2.5.1)
  T-CIT-U4-2  seeded controls are each reported: a dead `design <sign>2.1`, an unregistered
              `LC <sign>3` in code, and an unregistered `nowhere.md <sign> X` (BR3.1)
  T-CIT-U4-3  per register row CIT-01..CIT-18: the site carries the rewritten citation, and
              the target section contains the row's "target states" phrase; CIT-04's comment
              also says "defined here"; CIT-12's self-reference is gone (BR3.2, BR3.3)
  guard       the autouse socket guard is active (NFR4.3)

Partition: stdlib only. No SDK call, no account.
"""
from __future__ import annotations

import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import botocore.config  # noqa: F401 - as above
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
S = chr(0xA7)  # the section sign, built so this file holds no reference of its own
CORPUS_DIRS = ("src", "tests", "scripts", "infra", "docs", "config")
CORPUS_FILES = ("README.md", "CLAUDE.md", "STATUS.md")
EXCLUDED_PARTS = {"cdk.out", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
CODE_TOPS = {"src", "tests", "scripts", "infra", "config"}
RECORD = "aidlc/spaces/default/intents/261008-delphi-blog-pattern"
DESIGN = "docs/design.md"
CONTRACTS = f"{RECORD}/inception/contract-design/contract-summary.md"
U2_LC = "u2-decision-telemetry/nfr-design/logical-components.md"
U3_IS = "u3-packaging-integrity/infrastructure-design/infrastructure-specification.md"
#: Each key is a unique name or path; its value is the repo-relative target.
REGISTRY = {
    "team.md": "aidlc/spaces/default/memory/team.md",
    "project.md": "aidlc/spaces/default/memory/project.md",
    "phase1_budget.md": "docs/phase1_budget.md",
    U2_LC: f"{RECORD}/construction/{U2_LC}",
    U3_IS: f"{RECORD}/construction/{U3_IS}",
}


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_citations.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


# ------------------------------------------------------------------- the checker
def normalise(text: str) -> str:
    text = text.casefold()
    for ch in "—–-,:":
        text = text.replace(ch, "")
    return " ".join(text.split())


def headings(target: str) -> list[tuple[int, str, int]]:
    """(level, text, line index) for every Markdown heading outside fenced code."""
    out, fence = [], False
    for i, line in enumerate((ROOT / target).read_text().splitlines()):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m and not fence:
            out.append((len(m.group(1)), m.group(2).strip(), i))
    return out


def find_heading(target: str, rest: str) -> tuple[int, str, int] | None:
    hs = headings(target)
    num = re.match(r"(\d+(?:\.\d+)*)", rest)
    if num:
        token = re.escape(num.group(1))
        return next((h for h in hs if re.match(rf"{token}\.?(\s|$)", h[1])), None)
    text = normalise(rest)
    return next((h for h in hs if normalise(h[1]) and text.startswith(normalise(h[1]))), None)


def classify(before: str, is_code: bool) -> tuple[str | None, str | None]:
    """(target, problem) for the text preceding one section sign."""
    before = before.rstrip()
    if re.search(r"\bdesign$", before):
        return DESIGN, None
    if re.search(r"\bC\d+$", before):
        return CONTRACTS, None
    named = re.search(r"`?([\w./-]+\.md)`?$", before)
    if named:
        key = named.group(1)
        if key not in REGISTRY:
            return None, f"unregistered file {key!r}"
        return REGISTRY[key], None
    if is_code:
        return None, "unqualified reference in code"
    return DESIGN, None


def problems_in(text: str, is_code: bool, where: str = "<text>") -> list[str]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for i, ch in enumerate(line):
            if ch != S:
                continue
            target, problem = classify(line[:i], is_code)
            rest = line[i + 1:].lstrip()
            if problem is None and find_heading(target, rest) is None:
                problem = f"no heading in {target} for {rest[:40]!r}"
            if problem:
                out.append(f"{where}:{n}: {problem}")
    return out


def corpus() -> list[pathlib.Path]:
    paths = []
    for d in CORPUS_DIRS:
        paths += sorted(p for p in (ROOT / d).rglob("*")
                        if p.is_file() and not set(p.relative_to(ROOT).parts) & EXCLUDED_PARTS)
    return paths + [ROOT / f for f in CORPUS_FILES if (ROOT / f).exists()]


def corpus_problems() -> list[str]:
    out = []
    for p in corpus():
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        rel = p.relative_to(ROOT)
        is_code = rel.parts[0] in CODE_TOPS and p.suffix != ".md"
        out += problems_in(text, is_code, str(rel))
    return out


# --------------------------------------------------------------------- T-CIT-U4-1
@pytest.mark.author_record(RECORD)
def test_t_cit_u4_1_every_section_reference_resolves():
    assert corpus_problems() == []


# --------------------------------------------------------------------- T-CIT-U4-2
@pytest.mark.parametrize("seeded,is_code,expect", [
    (f"see design {S}2.1 here", True, "no heading"),
    (f"rule (LC {S}3 rule 1)", True, "unqualified"),
    (f"per nowhere.md {S} X", False, "unregistered file"),
])
def test_t_cit_u4_2_seeded_controls_are_reported(seeded, is_code, expect):
    (problem,) = problems_in(seeded, is_code)
    assert expect in problem, problem


# --------------------------------------------------------------------- T-CIT-U4-3
#: (id, site, rewritten citation as it appears at the site, target, section, phrase)
REGISTER = [
    ("CIT-01", "src/delphi/agent/app.py", f"(design {S}2)", DESIGN, "2",
     "embed → predict → converse → telemetry"),
    ("CIT-02", "src/delphi/agent/runtime_client.py", f"design {S}4 Phase 4", DESIGN, "4",
     "evaluation run"),
    ("CIT-03", "scripts/estimate_phase1_budget.py", f"phase1_budget.md` {S} 2",
     "docs/phase1_budget.md", "2", "600 input / 350 output"),
    ("CIT-04", "src/delphi/eval/gate.py", f"design {S}4 Phase 1", DESIGN, "4",
     "Kill/pivot on T-STAT-1"),
    ("CIT-05", "src/delphi/eval/stats.py", f"(design {S}3)", DESIGN, "3", "complementarity gate"),
    ("CIT-06", "src/delphi/router/baselines.py", f"design {S}1.1 H3", DESIGN, "1.1",
     "(router−random)/(oracle−random)"),
    ("CIT-07", "src/delphi/router/baselines.py", f"design {S}1.1, the oracle", DESIGN, "1.1",
     "per-query argmax"),
    ("CIT-08", "tests/test_stat.py", f"design {S}3 (T-STAT-1", DESIGN, "3", "T-STAT-1..6"),
    ("CIT-09", "tests/test_router.py", f"design {S}3 (T-RTR-1", DESIGN, "3", "T-RTR-1..6"),
    ("CIT-10", "tests/test_infra.py", f"design {S}3 (T-INF-", DESIGN, "3", "T-INF-1..5"),
    ("CIT-11", "tests/test_infra.py", f"design {S}3 (T-INF-", DESIGN, "3", "T-CMP-1..2"),
    ("CIT-13", "src/delphi/failure_log.py", f"{U2_LC}` {S} 3 rule 1", REGISTRY[U2_LC], "3",
     "Dependency direction"),
    ("CIT-14", "tests/test_failure_paths.py", f"{U2_LC}` {S} 3 rule 2", REGISTRY[U2_LC], "3",
     "Dependency direction"),
    ("CIT-15", "tests/test_failure_log.py", f"{U2_LC}` {S} 3 rule 1", REGISTRY[U2_LC], "3",
     "Dependency direction"),
    ("CIT-16", "tests/test_request_path.py", f"{U2_LC}` {S} 8", REGISTRY[U2_LC], "8",
     "What is deliberately absent"),
    ("CIT-17", "tests/test_artifact_location.py", f"{U3_IS}` {S} 4", REGISTRY[U3_IS], "4",
     "Synthesized baseline"),
    ("CIT-18", "tests/test_artifact_location.py", f"{U3_IS}` {S} 4", REGISTRY[U3_IS], "4",
     "Synthesized baseline"),
]


def section_text(target: str, number: str) -> str:
    lines = (ROOT / target).read_text().splitlines()
    head = find_heading(target, number)
    assert head is not None, (target, number)
    level, _, start = head
    end = next((i for lvl, _, i in headings(target) if i > start and lvl <= level), len(lines))
    return "\n".join(lines[start:end])


def _row_param(row):
    """A row whose target is in the design record skips where that record is not shipped."""
    marks = [pytest.mark.author_record(RECORD)] if row[3].startswith("aidlc/") else []
    return pytest.param(row, id=row[0], marks=marks)


@pytest.mark.parametrize("row", [_row_param(r) for r in REGISTER])
def test_t_cit_u4_3_each_rewrite_points_at_what_it_cites(row):
    cid, site, citation, target, number, phrase = row
    text = (ROOT / site).read_text()
    assert text.count(citation) >= (2 if cid in ("CIT-17", "CIT-18") else 1), (cid, citation)
    assert phrase in section_text(target, number), (cid, phrase)
    if cid == "CIT-04":
        (line,) = [ln for ln in text.splitlines() if ln.startswith("GATE_THRESHOLD")]
        assert f"design {S}4" in line and "defined here" in line


def test_t_cit_u4_3_cit_12_the_self_reference_is_deleted():
    section = section_text(DESIGN, "3")
    assert f"({S}3.1)" not in section and "T-STAT-1..6" in section


# --------------------------------------------------------------------- guard
def test_u4_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
