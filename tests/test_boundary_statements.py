"""Boundary statements are checked against the code — U4 LC-U4-9, LC-U4-11 (BR2.2, BR2.3).

A docstring or document that says which code runs where is a boundary DEFINITION, so it is
compared with the code rather than trusted (team.md, docstring-as-contract). The request path's
package set comes from U3's one closure helper, `import_closure("delphi.agent.entrypoint")`,
imported from tests/test_packaging_closure.py and not copied (WF-5).

  T-BND-U4-7   src/delphi/__init__.py's `Request-path packages:` list equals the top-level
               delphi.<x> names of the request-path closure; the docstring holds neither
               "inference-time routing only" nor "live in scripts/"; a seeded wrong list is
               reported (BR2.2, AC2.5.2)
  T-BND-U4-8   README's "What runs where" section: the AgentCore bullet's backticked src/delphi/<x>
               paths name the same set, and the bullet says the eval and train members are
               partial; the banned phrases are absent from README.md and
               docs/agent_scoping.md (BR2.3, P-7)
  T-BND-U4-11  the fallback is visible in a single inspected invocation: U1's real renderer
               shows "fallback: True" for the envelope of a real fallback invocation, and not
               for a normal one (FR6.2.3, functional-design R-04). Keep-fixed; the normal
               render is the negative control
  T-BND-U4-14  every `# pragma: no cover` in the src files U4 creates or edits carries a
               ` - <why>` reason (NFR3.2, D-CG-U4-8). Keep-fixed; negative control: a seeded
               bare pragma is reported
  T-BND-U4-15  no line of README.md or docs/*.md pairs "live" or
               "deployed" with "agent-level fallback" or "RouterAgent" unless the same line
               says "repository-level" or "not yet" (G-U4-4, D-CG-U4-8). Stated limit: a
               phrase check, so a rewording can evade it; a human read of the documents is
               the other control. Keep-fixed; negative control: a seeded claim line is reported
  guard        the autouse socket guard is active (NFR4.3)

Partition: stdlib and the package. No SDK call, no account.
"""
from __future__ import annotations

import ast
import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import botocore.config  # noqa: F401 - as above
import pytest
from test_packaging_closure import import_closure
from u2_support import CountingConverse, RaisingRouter

from delphi.agent.telemetry import InMemorySink

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "delphi"
BANNED = ("inference-time routing only", "live in scripts/", "live in `scripts/`",
          "inference-time request path")
#: The src files U4 creates or edits (T-BND-U4-14).
U4_SRC_FILES = (
    "__init__.py", "agent/app.py", "agent/runtime_client.py", "router/base.py",
    "router/learned.py", "router/baselines.py", "train/artifact_store.py", "eval/scoring.py",
    "eval/build_matrix.py", "eval/latency_sample.py", "eval/runner.py", "eval/cell_harness.py",
    "eval/gate.py", "eval/stats.py", "failure_log.py",
)
BARE_PRAGMA = re.compile(r"#\s*pragma:\s*no cover(?!\s+-\s+\S)")
_LIVE = re.compile(r"\b(live|deployed)\b", re.I)
_FALLBACK = re.compile(r"agent-level fallback|RouterAgent", re.I)
_HEDGE = re.compile(r"repository-level|not yet", re.I)


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_boundary_statements.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


@pytest.fixture(scope="module")
def request_path_set() -> set[str]:
    closure = import_closure("delphi.agent.entrypoint")
    return {".".join(m.split(".")[:2]) for m in closure.first_party if m.count(".") >= 1}


# --------------------------------------------------------------------- T-BND-U4-7
def declared_packages(doc: str) -> set[str] | None:
    """The comma list after `Request-path packages:`, possibly wrapped over lines."""
    m = re.search(r"Request-path packages:((?:\s*delphi\.[a-z_]+,?)+)", doc)
    if m is None:
        return None
    return set(re.findall(r"delphi\.[a-z_]+", m.group(1)))


def test_t_bnd_u4_7_the_package_docstring_names_the_request_path(request_path_set):
    doc = ast.get_docstring(ast.parse((PKG / "__init__.py").read_text())) or ""
    assert declared_packages(doc) == request_path_set, (declared_packages(doc),
                                                        request_path_set)
    for phrase in BANNED[:3]:
        assert phrase not in doc, phrase
    seeded = "Request-path packages: delphi.agent, delphi.router.\n"
    assert declared_packages(seeded) != request_path_set


# --------------------------------------------------------------------- T-BND-U4-8
def readme_section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1) if m else ""


def agentcore_bullet(section: str) -> str:
    bullets = re.split(r"\n(?=- )", section.strip())
    (bullet,) = [b for b in bullets if "AgentCore" in b.split(":**")[0]]
    return bullet


def bullet_packages(bullet: str) -> set[str]:
    paths = re.findall(r"`src/delphi/([a-z_]+)(?:\.py|/)?`", bullet)
    return {f"delphi.{p}" for p in paths}


def test_t_bnd_u4_8_the_readme_names_the_same_set(request_path_set):
    readme = (ROOT / "README.md").read_text()
    bullet = agentcore_bullet(readme_section(readme, "What runs where"))
    assert bullet_packages(bullet) == request_path_set, (bullet_packages(bullet),
                                                         request_path_set)
    assert "partial" in bullet.lower(), "the bullet must say eval and train enter in part"
    scoping = (ROOT / "docs" / "agent_scoping.md").read_text()
    for phrase in BANNED:
        assert phrase not in readme and phrase not in scoping, phrase
    assert bullet_packages("- **On AgentCore Runtime:** `src/delphi/agent`") != request_path_set


# --------------------------------------------------------------------- T-BND-U4-11
def test_t_bnd_u4_11_a_fallback_is_visible_in_one_inspected_invocation(pool):
    from delphi.agent.app import RouterAgent
    from delphi.router.baselines import AlwaysModelRouter
    from delphi.verify.render import render_human

    fell = RouterAgent(pool, RaisingRouter(pool), CountingConverse(), InMemorySink()
                       ).handle_request({"prompt": "hi"})
    normal = RouterAgent(pool, AlwaysModelRouter(pool, "mid"), CountingConverse(),
                         InMemorySink()).handle_request({"prompt": "hi"})
    assert "fallback: True" in render_human(fell, elapsed_ms=1.0)
    assert "fallback: True" not in render_human(normal, elapsed_ms=1.0)


# --------------------------------------------------------------------- T-BND-U4-14
def test_t_bnd_u4_14_every_pragma_in_u4_files_carries_a_reason():
    for rel in U4_SRC_FILES:
        bare = [ln for ln in (PKG / rel).read_text().splitlines() if BARE_PRAGMA.search(ln)]
        assert bare == [], (rel, bare)
    assert BARE_PRAGMA.search("x = 1  # pragma: no cover")
    assert not BARE_PRAGMA.search("x = 1  # pragma: no cover - runs in the child")


# --------------------------------------------------------------------- T-BND-U4-15
def live_fallback_claims(text: str) -> list[str]:
    return [ln for ln in text.splitlines()
            if _LIVE.search(ln) and _FALLBACK.search(ln) and not _HEDGE.search(ln)]


def test_t_bnd_u4_15_no_document_says_the_live_arm_has_the_agent_fallback():
    files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    claims = {str(p.relative_to(ROOT)): live_fallback_claims(p.read_text()) for p in files}
    assert {k: v for k, v in claims.items() if v} == {}
    assert live_fallback_claims("The live router arm now applies the agent-level fallback.")
    assert not live_fallback_claims(
        "The agent-level fallback is repository-level until a deployed image carries it.")


# --------------------------------------------------------------------- guard
def test_u4_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
