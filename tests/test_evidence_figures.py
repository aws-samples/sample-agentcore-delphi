"""Published figures are pinned to the code that produces them — U5 LC-U5-7 (FR5.1.1-FR5.5.2,
NFR1.1, NFR4.3-NFR4.5, FR7.6.1; US2.2, US3.1, US3.3).

The register below was written before any document was corrected (team TDD ordering). Each
reader-facing command runs here as a real subprocess, exactly as a reader types it:
``[sys.executable, "-I", tests/u5_smoke_runner.py, scripts/<script>.py, *args]`` with its cwd
a fresh tmp_path holding symlinks to ``src/`` and ``config/``, and a built environment with no
``AWS_*`` or ``DELPHI_*`` variable inherited. The runner refuses sockets, so no child can reach
the network, and nothing is written into the repository (T-EVD-U5-17). Each command runs once
per session. No AWS call; an autouse guard makes ``socket.socket`` raise in this process too.

Values are read from what the scripts emit: phase 1 from ``gate_report_<mode>.json``
(``gate.gap``, ``gate.passes``), phase 2 from its labelled lines and selection table, the
budget from the estimate script's stdout. Nothing is read from phase 1's non-ASCII verdict
sentence, and no wall-clock field is compared.

How a pin is checked (SD-U5-9). ``FIGURES`` maps each figure id to its sites. A site is a
path, a regex with one capture group, the number of times it must match, and the key of the
computed value it is compared with. Both ways: the computed value must be what the test
expects, and each site's captured number must equal the computed value rounded to the
decimals written at the site (``exact`` sites must equal the printed text, NR-U5-11). A site
whose regex matches other than its declared count fails, so an edit that stops a site
matching cannot pass vacuously. A site line carrying ``~`` before a number fails.

Figure register (closed, FR5.2.3; a figure found later is added here by id):

- FIG-1   phase-1 complementary gate gap (README, docs/phase1_pipeline.md).
  Check: T-EVD-U5-1.
- FIG-2   nested mode: PASS at the defaults, FAIL at ``--n 40 --seed 8`` (README,
  docs/phase1_pipeline.md twice, scripts/run_phase1_synthetic.py docstring).
  Checks: T-EVD-U5-2, T-EVD-U5-3, T-EVD-U5-10.
- FIG-3   phase-2 held-out capture and H3 verdict (README, docs/phase2_training.md).
  Check: T-EVD-U5-4.
- FIG-4   phase-2 validation capture and quality per family, held-out qualities
  (docs/phase2_training.md). Check: T-EVD-U5-4.
- FIG-5   phase-2 ``p90_ms`` wall-clock column (docs/phase2_training.md): elided, and stated
  not to be a latency claim. Check: T-EVD-U5-7.
- FIG-6   phase-2 nested capture, router quality equal to best single (docs/phase2_training.md).
  Check: T-EVD-U5-4.
- FIG-7   test counts and measured coverage (README, docs/): none
  published. Check: T-EVD-U5-6.
- FIG-8   the H3 bar (README, docs/design.md twice) equals ``H3_CAPTURE_THRESHOLD``.
  Check: T-EVD-U5-5.
- FIG-9   budget figures (docs/phase1_budget.md, docs/phase1_pipeline.md, docs/design.md,
  docs/phase0_checklist.md) equal the estimate script's printed values. Check: T-EVD-U5-8.
- FIG-10  the judge-column cut (docs/phase1_budget.md): labelled an estimate, not measured.
  Check: T-EVD-U5-8.
- FIG-12  "routing microsecond-scale" (docs/design.md): names its source, not measured.
  Check: T-EVD-U5-16.
- FIG-13  the measured NFR1 routing-overhead P90 (docs/deployment.md, docs/design.md),
  added by U6 once the on-architecture sample was committed. Check: T-EVD-U5-19.

Tests:

- T-EVD-U5-1   phase-1 complementary JSON: gap 27.55 at 2 places and passes true; every FIG-1
  site matches its count and equals it; no ``~`` on a site line. Source: FR5.1.1, AC2.2.2,
  AC2.2.3.
- T-EVD-U5-2   nested at the defaults: gap 3.53, passes true; ``--mode nested --n 40
  --seed 8``: gap 0.00, passes false; both exit 0. Keep-fixed; negative control: a wrong
  expected value is rejected by the same helper. Source: FR5.1.2, FR5.5.1, AC3.3.1.
- T-EVD-U5-3   no sentence in the reader corpus or the script docstring pairs ``nested`` with
  ``FAIL`` without naming ``--n 40`` and ``--seed 8``; the FIG-2 numeric sites equal the runs.
  Source: FR5.1.2, NR-U5-3.
- T-EVD-U5-4   phase 2: ``CAPTURE RATIO`` 0.779 and ``H3`` PASS; FIG-3, FIG-4 and FIG-6 sites
  equal their computed values; nested router quality equals best single. Source: FR5.1.3,
  AC3.1.2.
- T-EVD-U5-5   the phase-2 script holds no float constant 0.6 (AST) and imports
  ``H3_CAPTURE_THRESHOLD``; FIG-8 sites equal the constant. Source: FR5.1.4, NR-U5-4.
- T-EVD-U5-6   README.md and docs/**/*.md: nothing matches the count
  pattern (markup between number and word tolerated; the checklist line is a listed control
  for both halves); a percentage on a test-coverage line equals ``fail_under``.
  Source: FR5.1.5, R-03, nfr-design R-01.
- T-EVD-U5-7   no ``<number> ms`` inside a fenced block of docs/phase2_training.md, and one
  sentence says the ``p90_ms`` column is wall clock and not a latency claim. Source: FR5.2.1.
- T-EVD-U5-8   the budget script exits 0 and every FIG-9 site equals its printed value; the
  FIG-10 sentence carries ``estimate`` and ``not measured``, or is gone. Source: FR5.2.2,
  AC2.2.1, R-03, D-CG-U5-8.
- T-EVD-U5-9   this docstring's FIG ids equal ``FIGURES``' keys (FIG-1 to FIG-13; the
  eleventh is retired) and it names every T-EVD-U5 test defined here. Keep-fixed; negative
  control: a docstring missing one id is reported. Source: FR5.2.3.
- T-EVD-U5-10  the nested bullet of docs/phase1_pipeline.md calls the outcome a ``finding``.
  Source: FR5.5.1, AC3.3.2.
- T-EVD-U5-11  the pass_at_1 paragraph of docs/phase1_pipeline.md has a sentence with
  ``pass@1``, ``incorrect`` and ``adversarial``. Source: FR5.5.2.
- T-EVD-U5-12  NFR1 gate, two branches, never a skip. Absent ``evidence/nfr1/`` (today): every
  reader sentence, wrapped lines joined, with ``<number> ms`` and P90/p90/overhead contains
  ``budget`` or ``target`` (``<`` and the less-or-equal sign do not count). Present: the sample
  passes ``check_sample`` and every such unlabelled figure equals ``published_p90_ms``.
  Source: NFR1.1, AC2.2.4, nfr-design R-03.
- T-EVD-U5-13  ``git check-ignore evidence/nfr1/records.jsonl`` exits 1. Keep-fixed; negative
  control: a temporary repository that ignores ``evidence/`` reports it. Source: NFR1.1.
- T-EVD-U5-14  two phase-2 runs give the same verdict and capture; nothing else is compared.
  Keep-fixed; negative control: differing outputs are rejected by the comparison helper.
  Source: NFR4.4, AC3.1.3.
- T-EVD-U5-15  README has one ``## Run the method offline`` section, before
  ``## Verify a deployment``, naming both scripts, containing ``no AWS account`` and no
  figure. Source: NFR4.5, AC3.1.4.
- T-EVD-U5-16  the FIG-12 sentence names its source and contains ``not measured``,
  or the figure is gone. Source: SD-U5-9, D-CG-U5-8.
- T-EVD-U5-17  ``(relpath, mtime_ns, size)`` under the repository's ``data/`` and
  ``artifacts/`` is unchanged across every smoke run, and each run's outputs exist under its
  own tmp_path. Keep-fixed; negative control: the snapshot helper sees a seeded change.
  Source: NFR4.3, AC3.1.1.
- T-EVD-U5-18  the runner, given a script that calls ``socket.create_connection``, exits
  non-zero with ``network disabled by smoke runner``. Keep-fixed; negative control: the same
  script run without the runner, in its describe mode, reports the real ``socket`` function.
  Source: NFR4.3.
- T-EVD-U5-19  the committed NFR1 sample passes ``check_sample``; every FIG-13 site matches its
  count and equals the recomputed P90 at the decimals written; each site's paragraph or list
  item cites ``evidence/nfr1/manifest.json`` and states what the figure measures (warm,
  in-process, router arm, the region, 200 warm invocations, the demo workload) and what it
  does not (end-to-end latency, Converse time, cold start, prompts outside the warm records'
  input-token range, which is recomputed from the records and stated as "9 to 58 input
  tokens", and any headroom reading: the 150 ms comparison is a "window-only check").
  Negative controls: a site text missing one caveat, and a wrong computed value, are both
  reported. Source: NFR1.1, BR10.4 (U6, Step 21 of its plan; U6 review R-04).
- guard        the autouse socket guard is active.
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import tomllib
from dataclasses import dataclass

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNNER = REPO / "tests" / "u5_smoke_runner.py"
SCRIPTS = REPO / "scripts"
P1, P2, BUDGET = "run_phase1_synthetic.py", "run_phase2_synthetic.py", "estimate_phase1_budget.py"


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_evidence_figures.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


# ---- the register -----------------------------------------------------------------------

NUM = r"(?:~\s*)?(\d+(?:\.\d+)?)"   # one capture group; a leading ~ is caught by the line rule
APPROX = re.compile(r"~\s*\$?\d")


@dataclass(frozen=True)
class Site:
    path: str
    pattern: str
    count: int
    key: str | None          # computed value compared with; None for a rule-only site
    exact: bool = False      # compare the printed text, not a rounded number
    scale: float = 1.0       # 100 for a percentage of a ratio


BUDGET_DOC_ALIASES = ("nova-lite", "llama-3.3-70b", "deepseek-v3.1", "nova-pro",
                      "mistral-large", "claude-haiku-4.5", "claude-sonnet-4.6")
SCENARIOS = ("smoke", "planned", "thorough")
_ROW = r"^\| \**{w} \([^|]*"


def _budget_sites() -> tuple[Site, ...]:
    doc = "docs/phase1_budget.md"
    sites = [Site(doc, rf"^\| {re.escape(a)} \| \$(\d+\.\d+) \|", 1,
                  f"budget.gen.{a.replace('.', '-')}", exact=True) for a in BUDGET_DOC_ALIASES]
    for w in SCENARIOS:
        row = _ROW.format(w=w)
        sites += [Site(doc, row + r"\| (\d+\.\d+) \|", 1, f"budget.{w}.matrix", exact=True),
                  Site(doc, row + r"\|[^|]*\| (\d+\.\d+) \|", 1, f"budget.{w}.judge", exact=True),
                  Site(doc, row + r"\|[^|]*\|[^|]*\| \*\*(\d+\.\d+)\*\* \|", 1,
                       f"budget.{w}.total", exact=True)]
    sites += [
        Site(doc, r"One matrix row \(7 candidates on one query\): (?:~\s*)?\$(\d+(?:\.\d+)?)",
             1, "budget.row", exact=True),
        Site(doc, r"pool is \*\*(\d+)×\*\*", 1, "budget.spread"),
        Site(doc, r"^\*\*\$(\d+(?:\.\d+)?)\*\* for the planned scenario", 1, "budget.cap",
             exact=True),
        Site("docs/phase1_pipeline.md", r"Prices span (\d+)×", 1, "p1.spread"),
        Site("docs/design.md", r"pool spread is (\d+)×", 1, "budget.spread"),
        Site("docs/design.md", r"Budget: (?:~\s*)?\$(\d+(?:\.\d+)?) planned", 1,
             "budget.planned.total", exact=True),
        Site("docs/design.md", r"planned, \$(\d+(?:\.\d+)?) cap", 1, "budget.cap", exact=True),
        Site("docs/phase0_checklist.md", r"\*\*\$(\d+(?:\.\d+)?) hard cap\*\*", 1, "budget.cap",
             exact=True),
    ]
    return tuple(sites)


_P2DOC = "docs/phase2_training.md"
FIGURES: dict[str, tuple[Site, ...]] = {
    "FIG-1": (
        Site("README.md", rf"{P1}\s+# gate PASSES \(gap {NUM}\)", 1, "p1.gap"),
        Site("docs/phase1_pipeline.md", rf"\*\*gate PASSES\*\* \(gap {NUM}", 1, "p1.gap"),
    ),
    "FIG-2": (
        Site("README.md", rf"--mode nested\s+# gate also PASSES \(gap {NUM}\)", 1, "p1n.gap"),
        Site("README.md", rf"--n 40 --seed 8\s+# gate FAILS \(gap {NUM}\)", 1, "p1n40.gap"),
        Site("docs/phase1_pipeline.md", rf"still \*\*PASSES\*\* \(gap {NUM}\)", 1, "p1n.gap"),
        Site("docs/phase1_pipeline.md", rf"--seed 8` the gate \*\*FAILS\*\* \(gap {NUM}\)", 1,
             "p1n40.gap"),
        Site("docs/phase1_pipeline.md", rf"--mode nested\s+# nested -> PASS at the defaults "
             rf"\(gap {NUM}\)", 1, "p1n.gap"),
        Site("docs/phase1_pipeline.md", rf"--n 40 --seed 8\s+# nested -> FAIL \(gap {NUM}\)", 1,
             "p1n40.gap"),
        Site("scripts/run_phase1_synthetic.py", r"--mode nested --n 40 --seed 8\s+#.*(FAIL)", 1,
             None),
    ),
    "FIG-3": (
        Site("README.md", rf"{P2}\s+# H3 capture {NUM} on test", 1, "p2.capture"),
        Site(_P2DOC, r"-> CAPTURE (\d\.\d+) \(H3 \w+\)", 1, "p2.capture"),
        Site(_P2DOC, r"-> CAPTURE \d\.\d+ \(H3 (\w+)\)", 1, "p2.h3", exact=True),
    ),
    "FIG-4": tuple(
        Site(_P2DOC, rf"^{mark}{fam}\s+{skip}(\d+\.\d+)", 1, f"p2.val.{fam}.{what}")
        for mark, fam in (("\\*", "linear"), (" ", "knn"), (" ", "prototype"))
        for what, skip in (("capture", ""), ("q", r"\S+\s+"))
    ) + (
        Site(_P2DOC, r"HELD-OUT TEST: router (\d+\.\d+)", 1, "p2.router_q"),
        Site(_P2DOC, r"vs best-single (\d+\.\d+)", 1, "p2.best_q"),
        Site(_P2DOC, r", oracle (\d+\.\d+) ->", 1, "p2.oracle_q"),
    ),
    "FIG-5": (Site(_P2DOC, r"(p90_ms)", 2, None),),   # the elided column and its sentence
    "FIG-6": (
        Site(_P2DOC, r"capture (\d\.\d{3}) but router quality == best-single quality", 1,
             "p2n.capture"),
    ),
    "FIG-7": (Site("docs/phase0_checklist.md", r"All .3 tests (written)", 1, None),),
    "FIG-8": (
        Site("README.md", r"router captures ≥(\d+)% of the oracle gap", 1, "h3", scale=100),
        Site("docs/design.md", r"captures \*\*≥(\d+)% of the oracle gap\*\*", 1, "h3",
             scale=100),
        Site("docs/design.md", r"\(oracle.random\) ≥ (\d\.\d+)", 1, "h3"),
        Site("docs/design.md", r"Capture ratio < (\d\.\d+) on held-out", 1, "h3"),
        Site("docs/design.md", r"≥(\d+)% capture,", 1, "h3", scale=100),
    ),
    "FIG-9": _budget_sites(),
    "FIG-10": (Site("docs/phase1_budget.md", r"(judge column)", 1, None),),
    "FIG-12": (Site("docs/design.md", r"(microsecond-scale)", 1, None),),
    "FIG-13": tuple(Site(path, r"nearest-rank P90 of \*\*(\d+(?:\.\d+)?) ms\*\*", 1, "nfr1.p90")
                    for path in ("docs/deployment.md", "docs/design.md")),
}

#: The source the FIG-12 sentence must name, beside ``not measured``.
SOURCE_PHRASES = {"FIG-12": "design expectation"}


# ---- helpers ----------------------------------------------------------------------------

_SPLIT = re.compile(r"(?<=[.!?])\s+")
_BLOCK_START = re.compile(r"^\s*(#|[-*+] |\d+\. |\||>)")
_FENCE = re.compile(r"^\s*```")


def sentences(text: str) -> list[str]:
    """Sentences of a Markdown text: wrapped lines are joined inside a paragraph or list item
    before splitting (nfr-design R-03); each non-blank fenced line is its own sentence."""
    out: list[str] = []
    buf: list[str] = []
    fence = False

    def flush() -> None:
        if buf:
            out.extend(s for s in _SPLIT.split(" ".join(buf)) if s.strip())
            buf.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            flush()
            fence = not fence
            continue
        if fence:
            if line.strip():
                out.append(line.strip())
            continue
        if not line.strip():
            flush()
            continue
        if _BLOCK_START.match(line):
            flush()
        buf.append(line.strip())
    flush()
    return out


def fenced_lines(text: str) -> list[str]:
    out, fence = [], False
    for line in text.splitlines():
        if _FENCE.match(line):
            fence = not fence
        elif fence:
            out.append(line)
    return out


def reader_docs() -> list[pathlib.Path]:
    """The reader corpus: README.md and every Markdown file in docs/."""
    return [REPO / "README.md", *sorted((REPO / "docs").rglob("*.md"))]


def read(rel: str) -> str:
    return (REPO / rel).read_text()


def site_problems(site: Site, text: str, values: dict[str, float],
                  printed: dict[str, str]) -> list[str]:
    """Every way one site can disagree with its producer; [] when it pins correctly."""
    matches = list(re.finditer(site.pattern, text, re.M))
    where = f"{site.path} /{site.pattern}/"
    if len(matches) != site.count:
        return [f"{where}: {len(matches)} matches, expected {site.count}"]
    problems = []
    for m in matches:
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.end())
        line = text[start:end if end >= 0 else None]
        if APPROX.search(line):
            problems.append(f"{where}: approximation marker on a pinned line: {line.strip()!r}")
        if site.key is None:
            continue
        got = m.group(1)
        if site.exact:
            if got != printed[site.key]:
                problems.append(f"{where}: {got!r} != printed {printed[site.key]!r}")
            continue
        places = len(got.split(".")[1]) if "." in got else 0
        want = f"{values[site.key] * site.scale:.{places}f}"
        if f"{float(got):.{places}f}" != want:
            problems.append(f"{where}: {got} != computed {want}")
    return problems


def figure_problems(fig: str, values: dict[str, float], printed: dict[str, str]) -> list[str]:
    out = []
    for site in FIGURES[fig]:
        out += site_problems(site, read(site.path), values, printed)
    return out


def snapshot(*dirs: pathlib.Path) -> set[tuple[str, int, int]]:
    """(relpath, mtime_ns, size) of every file under the given directories."""
    out = set()
    for d in dirs:
        for p in sorted(d.rglob("*")) if d.exists() else ():
            if p.is_file():
                st = p.stat()
                out.add((str(p.relative_to(d.parent)), st.st_mtime_ns, st.st_size))
    return out


def gate_matches(report: dict, gap: float, passes: bool) -> bool:
    return f"{report['gate']['gap']:.2f}" == f"{gap:.2f}" and report["gate"]["passes"] is passes


def same_claims(a: dict[str, str], b: dict[str, str]) -> bool:
    """NFR4.4: two phase-2 runs agree on what is claimed, the verdict and the capture."""
    return (a["p2.h3"], a["p2.capture"]) == (b["p2.h3"], b["p2.capture"])


def missing_ids(doc: str, fig_ids, test_ids) -> list[str]:
    in_doc = set(re.findall(r"\bFIG-\d+\b", doc))
    missing = sorted(set(fig_ids) - in_doc) + sorted(i for i in test_ids
                                                     if not re.search(rf"{i}(?!\d)", doc))
    extra = sorted(in_doc - set(fig_ids))
    return missing + [f"unregistered {e}" for e in extra]


# ---- the smoke runs (SD-U5-8) -----------------------------------------------------------


def smoke_env(home: pathlib.Path) -> dict[str, str]:
    return {
        "PATH": os.pathsep.join([str(pathlib.Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "HOME": str(home),
        "AWS_EC2_METADATA_DISABLED": "true",
    }


def run_script(cwd: pathlib.Path, script: str, *args: str) -> subprocess.CompletedProcess:
    """One reader command, in its own directory, under the socket-refusing runner."""
    cwd.mkdir(parents=True)
    for name in ("src", "config"):
        (cwd / name).symlink_to(REPO / name)
    argv = [sys.executable, "-I", str(RUNNER), str(SCRIPTS / script), *args]
    return subprocess.run(argv, cwd=cwd, env=smoke_env(cwd / "no-home"), capture_output=True,  # noqa: S603 - fixed argv: this interpreter, the runner, a repo script  # nosemgrep
                          text=True, timeout=600, check=False)


@dataclass
class Run:
    cwd: pathlib.Path
    res: subprocess.CompletedProcess


def gate_report(run: Run, mode: str) -> dict:
    return json.loads((run.cwd / "data" / "phase1_synthetic" / f"gate_report_{mode}.json")
                      .read_text())


def phase2_printed(out: str) -> dict[str, str]:
    """The labelled lines and the selection table of a phase-2 run, as printed."""
    got = {}
    for label, key in (("router mean quality", "p2.router_q"), ("oracle (ceiling)", "p2.oracle_q"),
                       ("CAPTURE RATIO", "p2.capture"), ("H3 (learnability)", "p2.h3")):
        m = re.search(rf"^\s+{re.escape(label)}\s*:\s*(\S+)", out, re.M)
        if m:
            got[key] = m.group(1)
    m = re.search(r"^\s+best single \([^)]*\):\s*(\S+)", out, re.M)
    if m:
        got["p2.best_q"] = m.group(1)
    for m in re.finditer(r"^[* ](\w+)\s+(\d\.\d+)\s+(\d+\.\d+)\s+\S+\s+(?:yes|no)$", out, re.M):
        got[f"p2.val.{m.group(1)}.capture"] = m.group(2)
        got[f"p2.val.{m.group(1)}.q"] = m.group(3)
    return got


def budget_printed(out: str) -> dict[str, str]:
    got = {}
    for m in re.finditer(r"^  (\S+)\s+\$(\d+\.\d{6})", out, re.M):
        got[f"budget.gen.{m.group(1)}"] = m.group(2)
    for key, pattern in (("budget.row", r"One matrix row .*: \$(\d+\.\d+)"),
                         ("budget.spread", r"Candidate price spread .*: (\d+\.\d+)x"),
                         ("budget.cap", r"RECOMMENDED HARD CAP .*: \$(\d+\.\d+)")):
        m = re.search(pattern, out)
        if m:
            got[key] = m.group(1)
    for m in re.finditer(r"^(smoke|planned|thorough) .*?(\d+\.\d\d)\s+(\d+\.\d\d)\s+(\d+\.\d\d)$",
                         out, re.M):
        w = m.group(1)
        got[f"budget.{w}.matrix"], got[f"budget.{w}.judge"], got[f"budget.{w}.total"] = (
            m.group(2), m.group(3), m.group(4))
    return got


@pytest.fixture(scope="session")
def runs(tmp_path_factory) -> dict:
    """Every reader command, once, with the repository's data/ and artifacts/ snapshotted
    before the first and after the last (T-EVD-U5-17)."""
    guarded = (REPO / "data", REPO / "artifacts")
    before = snapshot(*guarded)
    base = tmp_path_factory.mktemp("u5-smoke")
    out = {
        "p1": Run(base / "p1", None), "p1n": Run(base / "p1n", None),
        "p1n40": Run(base / "p1n40", None), "p2a": Run(base / "p2a", None),
        "p2b": Run(base / "p2b", None), "p2n": Run(base / "p2n", None),
        "budget": Run(base / "budget", None),
    }
    argvs = {
        "p1": (P1,), "p1n": (P1, "--mode", "nested"),
        "p1n40": (P1, "--mode", "nested", "--n", "40", "--seed", "8"),
        "p2a": (P2,), "p2b": (P2,), "p2n": (P2, "--mode", "nested"), "budget": (BUDGET,),
    }
    for name, argv in argvs.items():
        out[name].res = run_script(out[name].cwd, *argv)
    out["before"], out["after"] = before, snapshot(*guarded)
    return out


@pytest.fixture(scope="session")
def computed(runs) -> tuple[dict[str, float], dict[str, str]]:
    """(values, printed) for every figure key the scripts produce."""
    printed: dict[str, str] = {}
    printed.update(phase2_printed(runs["p2a"].res.stdout))
    printed.update({f"p2n.{k[3:]}": v for k, v in phase2_printed(runs["p2n"].res.stdout).items()})
    printed.update(budget_printed(runs["budget"].res.stdout))
    m = re.search(r"Price spread: (\d+\.\d+)x", runs["p1"].res.stdout)
    if m:
        printed["p1.spread"] = m.group(1)
    values = {k: float(v) for k, v in printed.items() if re.fullmatch(r"\d+(\.\d+)?", v)}
    for name, mode in (("p1", "complementary"), ("p1n", "nested"), ("p1n40", "nested")):
        values[f"{name}.gap"] = float(gate_report(runs[name], mode)["gate"]["gap"])
    return values, printed


# ---- T-EVD-U5-1 ... -5 ------------------------------------------------------------------


def test_t_evd_u5_1_the_phase1_gate_gap_is_pinned_both_ways(runs, computed):
    assert runs["p1"].res.returncode == 0, runs["p1"].res.stderr
    report = gate_report(runs["p1"], "complementary")
    assert gate_matches(report, 27.55, True), report["gate"]
    assert figure_problems("FIG-1", *computed) == []


def test_t_evd_u5_2_nested_mode_is_unstable_and_its_failure_executes(runs):
    for name in ("p1n", "p1n40"):
        assert runs[name].res.returncode == 0, runs[name].res.stderr
    defaults, failing = gate_report(runs["p1n"], "nested"), gate_report(runs["p1n40"], "nested")
    assert gate_matches(defaults, 3.53, True), defaults["gate"]
    assert gate_matches(failing, 0.00, False), failing["gate"]
    assert not gate_matches(defaults, 3.54, True), "the helper accepts a wrong expected value"
    assert not gate_matches(failing, 0.00, True), "the helper accepts a wrong verdict"


def nested_fail_problems(name: str, text: str, *, per_line: bool = False) -> list[str]:
    units = text.splitlines() if per_line else sentences(text)
    return [f"{name}: {s.strip()[:90]!r}" for s in units
            if "nested" in s and "FAIL" in s and not ("--n 40" in s and "--seed 8" in s)]


def test_t_evd_u5_3_no_sentence_says_nested_fails_at_the_defaults(computed):
    assert nested_fail_problems("seed", "With `--mode nested` the gate FAILS.") != []
    assert nested_fail_problems("seed", "`--mode nested --n 40 --seed 8` FAILS.") == []
    bad = []
    for path in reader_docs():
        bad += nested_fail_problems(str(path.relative_to(REPO)), path.read_text())
    script = SCRIPTS / P1
    bad += nested_fail_problems(f"scripts/{P1}", ast.get_docstring(ast.parse(script.read_text())),
                                per_line=True)
    assert bad == [], "nested paired with FAIL without the failing command:\n" + "\n".join(bad)
    assert figure_problems("FIG-2", *computed) == []


def test_t_evd_u5_4_the_phase2_capture_and_tables_are_pinned(runs, computed):
    values, printed = computed
    for name in ("p2a", "p2n"):
        assert runs[name].res.returncode == 0, runs[name].res.stderr
    assert printed["p2.capture"] == "0.779"
    assert printed["p2.h3"] == "PASS"
    assert printed["p2n.router_q"] == printed["p2n.best_q"], "nested router != best single"
    problems = []
    for fig in ("FIG-3", "FIG-4", "FIG-6"):
        problems += figure_problems(fig, values, printed)
    assert problems == []


def float_constants(source: str, value: float) -> list[int]:
    return [n.lineno for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Constant) and isinstance(n.value, float) and n.value == value]


def test_t_evd_u5_5_the_h3_bar_is_one_named_constant(computed):
    import delphi.eval.matrix as matrix

    assert float_constants("x = 0.60\n", 0.6) == [1]
    source = (SCRIPTS / P2).read_text()
    assert float_constants(source, 0.6) == [], "the H3 bar is a literal in the phase-2 script"
    imported = {a.name for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.ImportFrom) and n.module == "delphi.eval.matrix"
                for a in n.names}
    assert "H3_CAPTURE_THRESHOLD" in imported
    values, printed = computed
    values = dict(values, h3=matrix.H3_CAPTURE_THRESHOLD)
    assert values["h3"] == 0.60
    assert figure_problems("FIG-8", values, printed) == []


# ---- T-EVD-U5-6 ... -9 ------------------------------------------------------------------

COUNT = re.compile(r"\b\d+[\s`*_]*(?:passed|pass|xfail(?:ed)?|deselected|skipped)\b")
#: A line that talks about test coverage (not, say, telemetry "100% coverage" of invocations).
COV_LINE = re.compile(r"--cov|cov=|fail_under|coverage floor|line coverage|test coverage", re.I)
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def count_problems(name: str, text: str, floor: int) -> list[str]:
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if COUNT.search(line):
            out.append(f"{name}:{n}: test count")
        if COV_LINE.search(line):
            out += [f"{name}:{n}: measured coverage {p}%" for p in PERCENT.findall(line)
                    if float(p) != floor]
    return out


def test_t_evd_u5_6_no_test_count_or_measured_coverage_is_published():
    floor = tomllib.loads(read("pyproject.toml"))["tool"]["coverage"]["report"]["fail_under"]
    # The checklist line as it read at 0fd4cf6 (the section sign built with chr(), so this
    # module carries none for T-CIT-U4-1 to read as a citation).
    checklist_then = (f"- [x] All {chr(0xA7)}3 tests written. 31 pass (math/behaviour layer "
                      "runs for real), 9 `xfail` are the later-phase data gates.")
    assert len(COUNT.findall(checklist_then)) == 2, "the pattern misses a marked-up count"
    assert count_problems("seed", "`--cov=src` 94% with the floor met", floor) != []
    assert count_problems("seed", "T-INF-3 asserts 100% coverage of invocations", floor) == []
    bad = []
    for path in reader_docs():
        bad += count_problems(str(path.relative_to(REPO)), path.read_text(), floor)
    assert bad == [], bad
    assert figure_problems("FIG-7", {}, {}) == []


def wall_clock_hits(text: str) -> list[str]:
    """Fenced lines carrying `<number> ms`, or a number in a `p90_ms` table column."""
    hits, column, width = [], None, 0
    for line in fenced_lines(text):
        cells = line.split()
        if "p90_ms" in cells:
            column, width = cells.index("p90_ms"), len(cells)
            continue
        if MS_FIGURE.search(line):
            hits.append(line.strip())
        elif column is not None and len(cells) == width:
            if re.fullmatch(r"\d+(\.\d+)?", cells[column].lstrip("*")):
                hits.append(line.strip())
    return hits


def test_t_evd_u5_7_no_wall_clock_latency_in_the_phase2_doc():
    seeded = "```\nfamily  capture  p90_ms  ok\n*linear  0.849  0.04  yes\n```\n"
    assert wall_clock_hits(seeded) == ["*linear  0.849  0.04  yes"]
    assert wall_clock_hits("```\nP90 0.04ms\n```\n") == ["P90 0.04ms"]
    text = read(_P2DOC)
    assert wall_clock_hits(text) == [], "wall-clock values in a fenced block"
    assert any("p90_ms" in s and "wall clock" in s and "not a latency claim" in s
               for s in sentences(text)), "no sentence says the p90_ms column is wall clock"
    assert figure_problems("FIG-5", {}, {}) == []


def fig10_problems(text: str) -> list[str]:
    return [s for s in sentences(text) if "judge column" in s and PERCENT.search(s)
            and not ("estimate" in s and "not measured" in s)]


def test_t_evd_u5_8_budget_figures_equal_the_estimate_script(runs, computed):
    res = runs["budget"].res
    assert res.returncode == 0, res.stderr
    values, printed = computed
    assert printed["budget.row"] == "0.01887" and printed["budget.cap"] == "499.95"
    assert figure_problems("FIG-9", values, printed) == []
    assert fig10_problems("This cuts the judge column by about 60%.") != []
    assert fig10_problems(read("docs/phase1_budget.md")) == []
    assert figure_problems("FIG-10", values, printed) == []


def test_t_evd_u5_9_the_register_is_closed():
    tests = sorted({f"T-EVD-U5-{m.group(1)}" for name in globals()
                    if (m := re.match(r"test_t_evd_u5_(\d+)_", name))}, key=lambda i: int(i[9:]))
    assert len(tests) == 19, tests
    # The eleventh figure cited only the author's status file, which this sample does not ship.
    assert sorted(FIGURES, key=lambda f: int(f[4:])) == [f"FIG-{n}" for n in range(1, 14)
                                                         if n != 11]
    without = re.sub(r"T-EVD-U5-7(?!\d)", "", __doc__)
    assert missing_ids(without, FIGURES, tests) == ["T-EVD-U5-7"]
    assert missing_ids(__doc__, FIGURES, tests) == []


# ---- T-EVD-U5-10 ... -18 ----------------------------------------------------------------


def test_t_evd_u5_10_the_nested_result_is_framed_as_a_finding():
    text = read("docs/phase1_pipeline.md")
    bullets = [ln for ln in text.splitlines() if ln.startswith("- **nested**")]
    assert len(bullets) == 1, bullets
    assert "finding" in bullets[0]


def test_t_evd_u5_11_the_pass_at_1_caveat_is_published():
    paras = [p for p in re.split(r"\n\s*\n", read("docs/phase1_pipeline.md")) if "pass_at_1" in p]
    assert len(paras) == 1, len(paras)
    assert any("pass@1" in s and "incorrect" in s and "adversarial" in s
               for s in sentences(paras[0]))


MS_FIGURE = re.compile(r"\d+(?:\.\d+)?\s*ms\b")
P90_WORD = re.compile(r"P90|p90|overhead")
LABEL_WORD = re.compile(r"\bbudget\b|\btarget\b")


def unlabelled_overhead(text: str) -> list[str]:
    return [s for s in sentences(text)
            if MS_FIGURE.search(s) and P90_WORD.search(s) and not LABEL_WORD.search(s)]


def test_t_evd_u5_12_no_measured_routing_overhead_without_the_sample():
    from delphi.eval import latency_sample

    assert unlabelled_overhead("Routing adds 85 ms of P90 overhead.") != []
    assert unlabelled_overhead("D2 (<150ms P90 overhead).") != [], "a symbol is not a label"
    assert unlabelled_overhead("Under the 150 ms P90\nbudget (D2).") == [], "wrapped line"
    bad = {}
    for path in reader_docs():
        hits = unlabelled_overhead(path.read_text())
        if hits:
            bad[str(path.relative_to(REPO))] = [h[:80] for h in hits]
    if latency_sample.SAMPLE_DIR.exists():
        records, manifest = latency_sample.load_sample()
        latency_sample.check_sample(records, manifest)
        places = manifest["published_p90_decimals"]
        want = f"{manifest['published_p90_ms']:.{places}f}"
        wrong = {k: [s for s in v if want not in s] for k, v in bad.items()}
        assert {k: v for k, v in wrong.items() if v} == {}, wrong
    else:
        assert bad == {}, f"routing-overhead figure without the committed sample: {bad}"


def _git(cwd: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,  # noqa: S603,S607 - fixed argv: the git CLI
                          check=False)


def test_t_evd_u5_13_the_nfr1_sample_home_is_not_ignored(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert _git(scratch, "init", "-q").returncode == 0
    (scratch / ".gitignore").write_text("evidence/\n")
    assert _git(scratch, "check-ignore", "-q", "evidence/nfr1/records.jsonl").returncode == 0
    assert _git(REPO, "check-ignore", "-q", "evidence/nfr1/records.jsonl").returncode == 1


def test_t_evd_u5_14_repeat_phase2_runs_agree_on_the_claim(runs):
    a, b = (phase2_printed(runs[n].res.stdout) for n in ("p2a", "p2b"))
    assert not same_claims(a, dict(a, **{"p2.capture": "0.780"}))
    assert not same_claims(a, dict(a, **{"p2.h3": "MISS"}))
    assert same_claims(a, b), (a, b)


OFFLINE = "Run the method offline"


def test_t_evd_u5_15_the_offline_path_is_the_readers_way_in():
    text = read("README.md")
    headings = re.findall(r"(?m)^## (.+?)\s*$", text)
    assert headings.count(OFFLINE) == 1, headings
    assert headings.index(OFFLINE) < headings.index("Verify a deployment")
    body = re.split(r"(?m)^## ", text)[headings.index(OFFLINE) + 1]
    assert P1 in body and P2 in body
    assert "no AWS account" in " ".join(body.split()), "wrapped lines are joined first"
    figures = re.findall(r"\d+\.\d+|\d+\s*%|\$\s?\d|\d+\s*ms\b", body)
    assert figures == [], f"a figure in the offline section: {figures}"


def sourced_problems(fig: str, text: str) -> list[str]:
    """Sentences stating an unproduced figure without its source and `not measured`."""
    (site,) = FIGURES[fig]
    return [s for s in sentences(text) if re.search(site.pattern, s)
            and not ("not measured" in s and SOURCE_PHRASES[fig] in s)]


def test_t_evd_u5_16_unproduced_figures_name_their_source():
    assert sourced_problems("FIG-12", "Routing is microsecond-scale.") != []
    for fig in ("FIG-12",):
        (site,) = FIGURES[fig]
        assert sourced_problems(fig, read(site.path)) == [], fig
        assert figure_problems(fig, {}, {}) == []


def test_t_evd_u5_17_smoke_runs_write_only_into_their_own_directories(runs, tmp_path):
    seeded = tmp_path / "data"
    seeded.mkdir()
    (seeded / "f").write_text("a")
    first = snapshot(seeded)
    (seeded / "f").write_text("ab")
    assert snapshot(seeded) != first, "the snapshot helper misses a changed file"
    assert runs["before"] == runs["after"], "a smoke run wrote into the repository"
    for name, mode in (("p1", "complementary"), ("p1n", "nested"), ("p1n40", "nested")):
        assert (runs[name].cwd / "data" / "phase1_synthetic" / f"gate_report_{mode}.json").exists()
    for name in ("p2a", "p2b", "p2n"):
        assert (runs[name].cwd / "artifacts" / "phase2_synthetic" / "manifest.json").exists()


PROBE = """\
import socket, sys
if sys.argv[1:] == ["--describe"]:
    print("create_connection is", getattr(socket.create_connection, "__module__", "?"))
    raise SystemExit(0)
socket.create_connection(("192.0.2.1", 9), timeout=0.01)
"""


def test_t_evd_u5_18_the_runner_refuses_the_network(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE)
    env = smoke_env(tmp_path / "no-home")
    plain = subprocess.run([sys.executable, "-I", str(probe), "--describe"], env=env,  # noqa: S603 - fixed argv: this interpreter + the probe  # nosemgrep
                           capture_output=True, text=True, timeout=60, check=False)
    assert plain.stdout.strip() == "create_connection is socket", plain.stdout + plain.stderr
    assert "network disabled by smoke runner" not in plain.stdout + plain.stderr
    guarded = subprocess.run([sys.executable, "-I", str(RUNNER), str(probe)], env=env,  # noqa: S603 - fixed argv: this interpreter, the runner, the probe  # nosemgrep
                             capture_output=True, text=True, timeout=60, check=False)
    assert guarded.returncode != 0
    assert "network disabled by smoke runner" in guarded.stderr


#: What every FIG-13 site must say around the figure: its source, what it measures and what it
#: does not. Matched in the site's paragraph or list item, wrapped lines joined. The last three
#: (U6 review R-04): the workload's prompt-length range, that longer prompts are unmeasured,
#: and that the 150 ms comparison is a window-only check, not a headroom claim.
NFR1_CAVEATS = ("evidence/nfr1/manifest.json", "warm", "in-process", "router arm", "us-west-2",
                "200 warm invocations", "demo workload", "does not measure", "end-to-end",
                "Converse", "cold start", "9 to 58 input tokens", "unmeasured",
                "window-only check")
#: The input-token range NFR1_CAVEATS states, recomputed from the warm records (never typed).
NFR1_TOKEN_RANGE = (9, 58)
_BULLET = re.compile(r"^(\s*)[-*+] ")


def site_unit(text: str, pos: int) -> str:
    """The paragraph holding `pos`, narrowed to its list item when it is a list."""
    start = text.rfind("\n\n", 0, pos)
    start = 0 if start < 0 else start + 2
    end = text.find("\n\n", pos)
    lines = text[start:None if end < 0 else end].splitlines(keepends=True)
    offset, at = start, 0
    for i, line in enumerate(lines):
        if offset + len(line) > pos:
            at = i
            break
        offset += len(line)
    first = next((i for i in range(at, -1, -1) if _BULLET.match(lines[i])), None)
    if first is None:
        return " ".join("".join(lines).split())
    indent = len(_BULLET.match(lines[first]).group(1))
    last = next((j for j in range(first + 1, len(lines))
                 if (m := _BULLET.match(lines[j])) and len(m.group(1)) <= indent), len(lines))
    return " ".join("".join(lines[first:last]).split())


def nfr1_caveat_problems(path: str, text: str) -> list[str]:
    (pattern,) = {s.pattern for s in FIGURES["FIG-13"]}
    out = []
    for m in re.finditer(pattern, text, re.M):
        unit = site_unit(text, m.start())
        out += [f"{path}: the FIG-13 site does not say {c!r}" for c in NFR1_CAVEATS
                if c not in unit]
    return out


def test_t_evd_u5_19_the_measured_nfr1_figure_is_pinned_and_bounded():
    from delphi.eval import latency_sample

    records, manifest = latency_sample.load_sample()  # absent sample: a failure, never a skip
    values = {"nfr1.p90": latency_sample.check_sample(records, manifest)}
    places = manifest["published_p90_decimals"]
    assert f"{values['nfr1.p90']:.{places}f}" == f"{manifest['published_p90_ms']:.{places}f}"
    assert figure_problems("FIG-13", values, {}) == []
    tokens = [r["input_tokens"] for r in latency_sample.counted(records).warm]
    assert (min(tokens), max(tokens)) == NFR1_TOKEN_RANGE, "the stated prompt range drifted"
    assert f"{NFR1_TOKEN_RANGE[0]} to {NFR1_TOKEN_RANGE[1]} input tokens" in NFR1_CAVEATS
    problems = []
    for site in FIGURES["FIG-13"]:
        problems += nfr1_caveat_problems(site.path, read(site.path))
    assert problems == []
    # Negative controls: a wrong computed value, and a site missing one caveat.
    assert figure_problems("FIG-13", {"nfr1.p90": values["nfr1.p90"] + 1.0}, {}) != []
    seeded = ("Intro.\n\n- other item\n- The warm, in-process routing window on the router arm in "
              "us-west-2 has a nearest-rank P90 of **0.3 ms** over 200 warm invocations of the\n"
              "  demo workload (`evidence/nfr1/manifest.json`, 9 to 58 input tokens; longer\n"
              "  prompts unmeasured; a window-only check). It does not measure end-to-end\n"
              "  latency or Converse time.\n- a later item about cold start\n")
    assert nfr1_caveat_problems("seed", seeded) == ["seed: the FIG-13 site does not say "
                                                    "'cold start'"]


def test_u5_evidence_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
