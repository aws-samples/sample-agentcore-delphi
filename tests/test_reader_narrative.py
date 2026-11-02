"""U7 reader narrative — offline docs tests over README.md and docs/*.md (T-DOC-U7-*).

The register below was written before any README or docs prose (team TDD ordering). Each id
names the assertion it makes and its source requirement. Every test reads the real repository
files; negative controls run the same helpers over strings seeded inside the test and never
modify a repository file. No AWS call, no network (an autouse guard makes ``socket.socket``
raise), no credentials.

Register:

- T-DOC-U7-1  The README opening block (text before the first ``## ``) is non-empty, and the
  headings ``## Cost basis`` and ``## Verify a deployment`` each exist exactly once.
  Source: SD-U7-1, D-CG-U7-1.
- T-DOC-U7-2  README.md and docs/*.md hold no 12-digit run, no bedrock-agentcore ARN carrying
  an account id, and no home-directory path; failures report file:line. This module holds no
  literal account id. Source: FR9.1.1, FR9.2.1, SD-U7-2.
- T-DOC-U7-3  Every README section naming a synthetic pipeline script contains ``synthetic``
  and ``illustrat``, and one sentence with both ``not evidence`` and ``model quality``.
  Source: BR7.1, FR5.5.1.
- T-DOC-U7-4  The section naming H1, H2 and H3 contains ``pending``, ``Phase 4``, ``real domain
  dataset`` and ``out of scope``; every sentence in it with a percentage or score names
  H1/H2/H3; the sentence containing ``pending`` has no figure. Source: BR7.2, FR5.5.2,
  D-CG-U7-3.
- T-DOC-U7-5  Every README section matching ``delphi-verify``, ``deployed`` or ``live``
  (word-bounded) contains ``mechanism`` and ``synthetic``, and one sentence with ``not`` and
  ``quality``. Source: BR7.3, FR5.5.3, SD-U7-3.
- T-DOC-U7-6  Across README.md and docs/*.md every sentence carrying a comparative-quality term
  also carries a hypothesis mark or an in-sentence ``claim-ok`` marker with a non-empty reason,
  and the marker count equals EXPECTED_CLAIM_OK. Source: BR7.4, FR5.5.4, SD-U7-4,
  D-CG-U7-5, D-CG-U7-6.
- T-DOC-U7-7  The opening block contrasts Bedrock Intelligent Prompt Routing (same-family) with
  Delphi (cross-family), cites a source in the same paragraph, and does so before any result.
  Source: FR14.1.1, FR14.1.2, FR5.2.3, SD-U7-12.
- T-DOC-U7-8  ``## Cost basis`` names chosen_model_id, input_tokens, output_tokens and
  tokens_by_model, and each resolves in code. Source: BR7.5, NFR7.1, SD-U7-9.
- T-DOC-U7-9  Every config/*.yaml with a pricing_snapshot has a BINDINGS row whose anchors hold,
  and the cost section names its path, region and pulled_date; a non-deployed pool is named in
  a sentence with ``no deployed arm``. Source: BR7.6, NFR7.2, SD-U7-6, D-CG-U7-9.
- T-DOC-U7-10 README.md still names AUTHOR_DEPLOYMENT_REGION; a deployed-arm region that differs
  from it is named only in sentences with ``differ``/``discrepan`` (a); one sentence has
  ``nothing in``, ``compar`` and ``region`` (b); the section has ``region you deploy`` (c).
  Source: NFR7.3, SD-U7-5, D-CG-U7-7, D-CG-U7-8.
- T-DOC-U7-11 No cost-section sentence pairs ``price``, a snapshot region and a parity word.
  Source: NFR7.3, SD-U7-8.
- T-DOC-U7-12 Each COST_CATEGORIES row is named in the cost section and anchored in the stack;
  one sentence has ``only model inference`` and ``pinned``; while no file under infra/ creates
  a bucket, every ``S3`` sentence says the reader provisions it separately, and once one does
  (U6 create mode) the ``S3`` sentences state both bucket modes (U6 BRK-U6-2); the section
  precedes deploy guidance. Source: NFR7.4, SD-U7-7, D-CG-U7-10, U6 BR11.3.
- T-DOC-U7-13 Each U7_SECTIONS block exists and holds no currency, latency, percentage or score
  figure. Source: FR5.2.1, NFR7.5, BR7.7, SD-U7-10.
- T-DOC-U7-14 Every backticked path-shaped token in U7_SECTIONS resolves to an existing
  repo-relative path, and at least one is present. Source: FR5.2.2, SD-U7-11, D-CG-U7-4.
- T-DOC-U7-15 This module imports only the standard library, ``delphi``, ``yaml`` and
  ``pytest``; never boto3 or botocore. Source: NFR4.1, SD-U7-13, D-CG-U7-11.
- T-DOC-U7-16 The autouse socket guard is active. Source: NFR4.1, D-CG-U7-11.
- T-DOC-U7-17 This docstring lists every T-DOC-U7 id and every U7 security requirement id.
  Source: TS-U7-2.

Requirement ids covered (security-requirements.md): FR9.1.1, FR9.2.1, FR5.5.1, FR5.5.2, FR5.5.3,
FR5.5.4, FR5.2.1, FR5.2.2, FR5.2.3, FR14.1.1, FR14.1.2, NFR7.1, NFR7.2, NFR7.3, NFR7.4, NFR7.5,
NFR4.1.

Splitting (TS-U7-3): sections split on ``^## ``, index 0 being the opening block. Prose is split
into blocks at blank lines, headings, list items, table rows and quotes, and each block into
sentences on ``(?<=[.!?])\\s+``; each non-empty line inside a fenced block is its own sentence.
"""
from __future__ import annotations

import ast
import dataclasses
import pathlib
import re
import socket
import sys

import pytest
import yaml

import delphi.eval.cost
from delphi.agent.telemetry import DecisionRecord

REPO = pathlib.Path(__file__).resolve().parents[1]
README = REPO / "README.md"
STACK = REPO / "infra" / "delphi_infra" / "runtime_stack.py"

# Sections U7 owns: "" is the opening block. The figure and path checks apply only to these.
U7_SECTIONS = ("", "Cost basis", "Verify a deployment")

# The author's deployment region, not verified live. T-DOC-U7-10 asserts README.md still names
# it; the CDK stack has no region constant (AWS_REGION is set at deploy time).
AUTHOR_DEPLOYMENT_REGION = "us-west-2"

# Exactly one in-sentence claim-ok marker is sanctioned (D-CG-U7-6: docs/phase1_pipeline.md).
EXPECTED_CLAIM_OK = 1

# Every pool file with a pricing_snapshot, what it prices, and where that binding is visible.
BINDINGS = {
    "config/models_realpool.yaml": {
        "prices": "router arm",
        "deployed": True,
        "anchors": {"infra/delphi_infra/runtime_stack.py": ("models_realpool.yaml",)},
    },
    "config/models.yaml": {
        "prices": "pinned arms; scripts/estimate_phase1_budget.py",
        "deployed": True,
        "anchors": {"src/delphi/config.py": ('"models.yaml"',)},
    },
    "config/models_synthetic.yaml": {
        "prices": "no deployed arm; the two synthetic scripts",
        "deployed": False,
        "anchors": {
            "scripts/run_phase1_synthetic.py": ("models_synthetic.yaml",),
            "scripts/run_phase2_synthetic.py": ("models_synthetic.yaml",),
        },
    },
}

# (category, README words, anchors in runtime_stack.py)
COST_CATEGORIES = (
    ("model inference", ("inference",), ("bedrock:InvokeModel",)),
    ("AgentCore Runtime", ("AgentCore Runtime",), ("CfnRuntime(",)),
    ("container image storage", ("ECR",), ("DockerImageAsset(",)),
    ("router artifact storage", ("S3",), ("s3:GetObject",)),
    (
        "logs, metrics, traces",
        ("CloudWatch", "X-Ray"),
        ("logs:PutLogEvents", "xray:PutTraceSegments"),
    ),
)

REQUIREMENT_IDS = (
    "FR9.1.1", "FR9.2.1", "FR5.5.1", "FR5.5.2", "FR5.5.3", "FR5.5.4", "FR5.2.1", "FR5.2.2",
    "FR5.2.3", "FR14.1.1", "FR14.1.2", "NFR7.1", "NFR7.2", "NFR7.3", "NFR7.4", "NFR7.5", "NFR4.1",
)
TEST_IDS = tuple(f"T-DOC-U7-{n}" for n in range(1, 18))

TERMS = re.compile(
    r"\b(outperform\w*|beats|superior|better quality|higher quality|dominance|dominates)\b", re.I
)
MARKS = re.compile(r"\bH[123]\b|hypothes|\bwhether\b|\bpending\b|falsif", re.I)
ALLOW = re.compile(r"<!--\s*claim-ok:(.*?)-->")
FIGURES = (
    re.compile(r"\$\s?\d"),
    re.compile(r"\bUSD\s?\d"),
    re.compile(r"\d+(\.\d+)?\s?ms\b"),
    re.compile(r"\d+(\.\d+)?\s?%"),
    re.compile(r"\b0\.\d+\b"),
)
SCORE_OR_PERCENT = (FIGURES[3], FIGURES[4])
# Built by concatenation so this module carries no home-directory path literal (SD-U7-2).
IDENTIFIERS = (
    re.compile(r"(?<!\d)\d{12}(?!\d)"),
    re.compile(r"arn:aws:bedrock-agentcore:[^:\s]+:\d{12}:"),
    re.compile(re.escape("/" + "Users" + "/")),
)
PATH_SUFFIXES = (".py", ".yaml", ".yml", ".md", ".json", ".toml", ".txt", ".drawio")
PATH_PREFIXES = ("src/", "config/", "scripts/", "docs/", "infra/", "tests/")
ALLOWED_THIRD_PARTY = {"delphi", "yaml", "pytest"}
FORBIDDEN_IMPORTS = {"boto3", "botocore"}

_SPLIT = re.compile(r"(?<=[.!?])\s+")
_BLOCK_START = re.compile(r"^\s*(#|[-*+] |\d+\. |\||>)")
_FENCE = re.compile(r"^\s*```")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_reader_narrative.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


# ---- helpers ---------------------------------------------------------------------------


def sections(text: str) -> dict[str, str]:
    """Split on ``^## `` headings. Key "" is the opening block; others are heading text."""
    parts = re.split(r"(?m)^## ", text)
    out = {"": parts[0]}
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        out.setdefault(heading.strip(), "")
        out[heading.strip()] += body
    return out


def heading_count(text: str, heading: str) -> int:
    return len(re.findall(rf"(?m)^## {re.escape(heading)}\s*$", text))


def sentences(text: str) -> list[str]:
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


def paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def strip_fences(text: str) -> str:
    return re.sub(r"(?ms)^\s*```.*?^\s*```\s*$", "", text)


def figure_hits(text: str, patterns=FIGURES) -> list[str]:
    return [m.group(0) for p in patterns for m in p.finditer(text)]


def identifier_hits(name: str, text: str) -> list[str]:
    return [
        f"{name}:{i}"
        for i, line in enumerate(text.splitlines(), start=1)
        if any(p.search(line) for p in IDENTIFIERS)
    ]


def unmarked_claims(text: str) -> list[str]:
    """Sentences carrying a quality term with neither a mark nor a reasoned claim-ok marker."""
    bad = []
    for s in sentences(text):
        if not TERMS.search(s) or MARKS.search(s):
            continue
        reasons = [m.group(1).strip() for m in ALLOW.finditer(s)]
        if not reasons or not all(reasons):
            bad.append(s)
    return bad


def is_path_shaped(token: str) -> bool:
    head = token.split(":", 1)[0]
    return head.endswith(PATH_SUFFIXES) or head.startswith(PATH_PREFIXES)


def path_tokens(text: str) -> list[str]:
    tokens = re.findall(r"`([^`\n]+)`", strip_fences(text))
    return [t for t in tokens if " " not in t and is_path_shaped(t)]


def missing_paths(tokens: list[str]) -> list[str]:
    return [t for t in tokens if not (REPO / t.split(":", 1)[0]).exists()]


def import_problems(source: str) -> list[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return sorted(
        n
        for n in names
        if n in FORBIDDEN_IMPORTS
        or not (n in sys.stdlib_module_names or n in ALLOWED_THIRD_PARTY)
    )


def missing_register_ids(doc: str) -> list[str]:
    ids = (*TEST_IDS, *REQUIREMENT_IDS)
    return [i for i in ids if not re.search(rf"{re.escape(i)}(?!\d)", doc)]


def pool_snapshots() -> dict[str, dict]:
    out = {}
    for path in sorted((REPO / "config").glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        if "pricing_snapshot" in data:
            out[path.relative_to(REPO).as_posix()] = data["pricing_snapshot"]
    return out


def unbound_pools(pool_paths) -> list[str]:
    return [p for p in pool_paths if p not in BINDINGS]


def parity_violations(text: str, regions) -> list[str]:
    parity = re.compile(r"\b(identical|equal|match\w*|same|cheaper|higher|lower)\b", re.I)
    return [
        s
        for s in sentences(text)
        if "price" in s.lower() and any(r in s for r in regions) and parity.search(s)
    ]


def readme() -> str:
    return README.read_text()


def doc_files() -> list[pathlib.Path]:
    return [README, *sorted((REPO / "docs").glob("*.md"))]


def section(name: str) -> str:
    secs = sections(readme())
    assert name in secs, f"README section {name!r} is missing"
    return secs[name]


# ---- component 1: README entry point and claim labels ----------------------------------


def test_doc_u7_1_owned_sections_exist():
    text = readme()
    assert sections(text)[""].strip(), "README opening block is empty"
    for heading in ("Cost basis", "Verify a deployment"):
        assert heading_count(text, heading) == 1, f"## {heading} must appear exactly once"


def test_doc_u7_2_no_account_identifiers_or_home_paths():
    seeded = "line one\nsee " + "/" + "Users" + "/x/repo\nid " + "0" * 12 + " here\n"
    assert identifier_hits("seed", seeded) == ["seed:2", "seed:3"]
    hits = []
    for path in doc_files():
        hits += identifier_hits(path.relative_to(REPO).as_posix(), path.read_text())
    assert hits == [], f"identifier or home path found: {hits}"


def test_doc_u7_3_synthetic_output_is_labelled_as_illustration():
    triggered = {
        h: b
        for h, b in sections(readme()).items()
        if "run_phase1_synthetic.py" in b or "run_phase2_synthetic.py" in b
    }
    assert triggered, "no README section names a synthetic pipeline script"
    for heading, body in triggered.items():
        low = body.lower()
        assert "synthetic" in low and "illustrat" in low, f"## {heading}: missing label words"
        assert any(
            "not evidence" in s.lower() and "model quality" in s.lower() for s in sentences(body)
        ), f"## {heading}: no sentence says 'not evidence' about 'model quality'"


def test_doc_u7_4_hypotheses_verdict_is_pending():
    secs = [
        (h, b)
        for h, b in sections(readme()).items()
        if all(re.search(rf"\bH{n}\b", b) for n in (1, 2, 3))
    ]
    assert secs, "no README section names H1, H2 and H3"
    for heading, body in secs:
        for word in ("pending", "Phase 4", "real domain dataset", "out of scope"):
            assert word in body, f"## {heading}: missing {word!r}"
        for s in sentences(body):
            if figure_hits(s, SCORE_OR_PERCENT):
                assert re.search(r"\bH[123]\b", s), f"bare figure in hypotheses section: {s!r}"
            if "pending" in s:
                assert not figure_hits(s, SCORE_OR_PERCENT), f"pending sentence has a figure: {s!r}"


_DEPLOY_TRIGGER = re.compile(r"\bdelphi-verify\b|\bdeployed\b|\blive\b", re.I)


def test_doc_u7_5_deployment_sections_state_mechanism_not_quality():
    assert not _DEPLOY_TRIGGER.search("we deliver the delivery plan")
    triggered = {h: b for h, b in sections(readme()).items() if _DEPLOY_TRIGGER.search(b)}
    assert triggered
    for heading, body in triggered.items():
        low = body.lower()
        assert "mechanism" in low and "synthetic" in low, f"## {heading!r}: no mechanism label"
        assert any(
            re.search(r"\bnot\b", s, re.I) and "quality" in s.lower() for s in sentences(body)
        ), f"## {heading!r}: no sentence says the deployment is not a quality result"


def test_doc_u7_6_no_unmarked_quality_claims():
    assert unmarked_claims("Our result: the router outperforms every single model.")
    assert unmarked_claims(
        "The router dominates here <!-- claim-ok: fixture -->. It also outperforms everything."
    ) == ["It also outperforms everything."]
    assert unmarked_claims("No router beats it <!-- claim-ok:   -->.")
    assert not unmarked_claims("We test whether the router outperforms a single model.")
    bad, markers = [], 0
    for path in doc_files():
        text = path.read_text()
        markers += len(ALLOW.findall(text))
        bad += [f"{path.relative_to(REPO)}: {s}" for s in unmarked_claims(text)]
    assert bad == [], "unmarked quality claims:\n" + "\n".join(bad)
    assert markers == EXPECTED_CLAIM_OK, f"{markers} claim-ok markers, expected {EXPECTED_CLAIM_OK}"


def test_doc_u7_7_opening_contrasts_cross_family_with_ipr():
    text = readme()
    opening = sections(text)[""]
    assert "Intelligent Prompt Routing" in opening
    assert re.search(r"\bsame[- ]family\b", opening, re.I)
    assert re.search(r"\bcross-family\b|\bacross (model )?families\b", opening, re.I)
    para = next(p for p in paragraphs(opening) if "Intelligent Prompt Routing" in p)
    cited = re.search(r"\[[^\]]+\]\([^)]+\)", para) or "docs/design.md" in para
    assert cited, "contrast cites no source"
    first_result = min(
        (i for i in (text.find(k) for k in (
            "run_phase1_synthetic", "run_phase2_synthetic", "capture", "## The three hypotheses"
        )) if i >= 0),
        default=len(text),
    )
    assert text.find("Intelligent Prompt Routing") < first_result, "contrast comes after a result"


# ---- component 2: cost basis ------------------------------------------------------------


def test_doc_u7_8_repricing_identifiers_resolve():
    body = section("Cost basis")
    fields = {f.name for f in dataclasses.fields(DecisionRecord)}
    for name in ("chosen_model_id", "input_tokens", "output_tokens"):
        assert name in body, f"cost section does not name {name}"
        assert name in fields, f"DecisionRecord has no field {name}"
    assert "tokens_by_model" in body
    assert hasattr(delphi.eval.cost, "tokens_by_model")


def test_doc_u7_9_every_pool_snapshot_is_disclosed():
    assert unbound_pools(["config/models_eu.yaml"]) == ["config/models_eu.yaml"]
    snaps = pool_snapshots()
    assert snaps, "no config/*.yaml carries a pricing_snapshot"
    assert unbound_pools(snaps) == [], f"pool files with no BINDINGS row: {unbound_pools(snaps)}"
    body = section("Cost basis")
    for pool, snap in snaps.items():
        for anchor_file, needles in BINDINGS[pool]["anchors"].items():
            src = (REPO / anchor_file).read_text()
            for needle in needles:
                assert needle in src, f"{pool}: anchor {needle!r} gone from {anchor_file}"
        for value in (pool, str(snap["region"]), str(snap["pulled_date"])):
            assert value in body, f"cost section does not disclose {value!r} for {pool}"
        if not BINDINGS[pool]["deployed"]:
            assert any(pool in s and "no deployed arm" in s for s in sentences(body)), (
                f"{pool}: no sentence says it prices no deployed arm"
            )


def test_doc_u7_10_region_discrepancy_is_disclosed():
    assert AUTHOR_DEPLOYMENT_REGION in (REPO / "README.md").read_text(), (
        "README.md no longer names the deployment region; revisit AUTHOR_DEPLOYMENT_REGION"
    )
    body = section("Cost basis")
    sents = sentences(body)
    snaps = pool_snapshots()
    for pool, row in BINDINGS.items():
        region = str(snaps[pool]["region"])
        if row["deployed"] and region != AUTHOR_DEPLOYMENT_REGION:
            naming = [s for s in sents if region in s]
            assert naming, f"{region} ({pool}) is not named"
            for s in naming:
                assert re.search(r"differ|discrepan", s, re.I), f"(a) no 'differ' in: {s!r}"
    assert any(
        "nothing in" in s.lower() and "compar" in s.lower() and "region" in s.lower() for s in sents
    ), "(b) no sentence says nothing in the code compares the regions"
    assert "region you deploy" in body, "(c) the reader's own region is not stated"


def test_doc_u7_11_no_regional_price_parity_claim():
    regions = [str(s["region"]) for s in pool_snapshots().values()]
    assert parity_violations("The us-east-1 prices are identical to us-west-2.", regions)
    body = section("Cost basis")
    assert parity_violations(body, regions) == []


def test_doc_u7_12_cost_categories_are_grounded_in_the_stack():
    text = readme()
    body = section("Cost basis")
    stack = STACK.read_text()
    for category, words, anchors in COST_CATEGORIES:
        for word in words:
            assert word in body, f"cost section omits {category} ({word})"
        for anchor in anchors:
            assert anchor in stack, f"{category}: anchor {anchor!r} gone from runtime_stack.py"
    sents = sentences(body)
    assert any("only model inference" in s.lower() and "pinned" in s.lower() for s in sents)
    # U6 BRK-U6-2 (P-U6-7): the create-mode bucket can live anywhere in infra/, not only in
    # runtime_stack.py, so the condition reads the whole tree. With no bucket construct every
    # S3 sentence says the reader provisions it separately; with one, the S3 sentences state
    # both modes (BR11.3): the stack provisions it in create mode, references it in existing.
    s3 = [s for s in sents if re.search(r"\bS3\b", s)]
    assert s3
    infra_src = "".join(p.read_text() for p in sorted((REPO / "infra").rglob("*.py"))
                        if not {".venv", "cdk.out"} & set(p.parts))
    if "s3.Bucket(" not in infra_src:
        for s in s3:
            assert "provision" in s and "separately" in s, f"S3 sentence: {s!r}"
    else:
        assert any("create" in s and "provision" in s for s in s3), s3
        assert any("existing" in s and "reference" in s for s in s3), s3
    marks = (text.find("## Deployment tooling"), text.find("cdk deploy"))
    deploy_at = min(i for i in marks if i >= 0)
    assert text.find("## Cost basis") < deploy_at, "cost basis comes after deploy guidance"


# ---- component 3: docs-wide hygiene and suite integrity --------------------------------


def test_doc_u7_13_no_figures_in_u7_sections():
    assert figure_hits("about $0.03 per query")
    assert figure_hits("a P90 of 85 ms")
    assert not figure_hits("pulled 2026-07-15 in us-west-2 for Phase 4 and H1")
    secs = sections(readme())
    for name in U7_SECTIONS:
        assert name in secs, f"README section {name!r} is missing"
        assert figure_hits(secs[name]) == [], f"figure in {name!r}: {figure_hits(secs[name])}"


def test_doc_u7_14_named_paths_resolve():
    assert not is_path_shaped("H1/H2/H3")
    assert is_path_shaped("STATUS.md:13")
    assert missing_paths(["nope/missing.py"]) == ["nope/missing.py"]
    secs = sections(readme())
    tokens = []
    for name in U7_SECTIONS:
        assert name in secs, f"README section {name!r} is missing"
        tokens += path_tokens(secs[name])
    assert tokens, "no path-shaped token in the U7 sections"
    assert missing_paths(tokens) == [], f"paths that do not exist: {missing_paths(tokens)}"


def test_doc_u7_15_module_imports_are_offline_safe():
    assert import_problems("import boto3\nimport requests\nimport re\n") == ["boto3", "requests"]
    assert import_problems(pathlib.Path(__file__).read_text()) == []


def test_doc_u7_16_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()


def test_doc_u7_17_register_is_complete():
    assert missing_register_ids(__doc__.replace("T-DOC-U7-9 ", "")) == ["T-DOC-U7-9"]
    assert missing_register_ids(__doc__) == []
