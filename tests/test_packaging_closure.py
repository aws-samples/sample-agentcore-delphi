"""The request-path import closure — U3 Layer 6 (SD-2, SD-4; FR6.1, FR6.3; NFR2.3, NFR2.5,
NFR6.1; ADR-008, ADR-011; AC4.5.4 closure half, AC4.5.5, AC4.5.6; project.md § Forbidden,
module-boundary extension).

What the serving container imports is computed statically from `delphi.agent.entrypoint`
by ONE helper, `import_closure`, following SD-2's seven rules:
  1. it is rooted at `delphi.agent.entrypoint`;
  2. every Import / ImportFrom anywhere in a module counts (module scope, function bodies,
     TYPE_CHECKING, try/except) — over-approximation is fine for a "must not contain" check;
  3. parent packages are included (`a.b.c` adds `a` and `a.b`: their __init__ runs);
  4. `from x.y import z` resolves to the submodule x.y.z when one exists, else to x.y;
  5. relative imports are resolved against the importing module's package;
  6. any `importlib.import_module`, `__import__` or `importlib.util` loader call is a
     violation, because a static walk cannot follow it;
  7. names in `sys.stdlib_module_names` are excluded; first-party packages are walked;
     everything else is third-party.

  T-PKG-U3-3  (a) the closure's third-party set is a subset of {numpy, yaml, boto3, botocore,
              bedrock_agentcore} and disjoint from the denylist {torch, sklearn, tensorflow,
              jax, xgboost, lightgbm, transformers, scipy, pandas}. (b) Positive controls:
              delphi.train.artifact_store, delphi.train.harness, delphi.train.routers and
              delphi.eval.matrix ARE in the closure — proof the walk saw the function-local
              import in app.build_agent_from_env. (c) delphi.eval.scoring, delphi.train.publish
              and delphi.train.generate are NOT, nor (U4 BR4.15) delphi.eval.runner and
              delphi.eval.cell_harness. (d) Four negative-control fixture packages are
              each reported: a function-local `import sklearn`; a parent __init__.py importing
              torch; `from pkg import sub` where sub imports pandas; an
              `importlib.import_module("x")` call. Keep-fixed for (a)-(c) (F12); (d) is the proof
              that it can fail.
  T-PKG-U3-5  train/__init__.py's docstring carries the line
              `Request-path-safe modules: delphi.train.artifact_store, delphi.train.harness,
              delphi.train.routers` (one line), and its set EQUALS the closure's delphi.train.<x>
              modules (delphi.train itself excluded: it is the line's holder, not a member). The
              docstring does not contain "never AgentCore". artifact_store.py's module docstring
              names weights.npz, scalars.json and manifest.json and does not contain
              "swap the path" (plan D-CG-5).
  T-PKG-U3-6  Within the closure, (a) every Call whose callee attribute is `client` with first
              argument "bedrock-runtime" sits in delphi/agent/bedrock.py (and there is one); (b)
              none of requests, httpx, urllib3, http.client, urllib.request, aiohttp, openai,
              anthropic, google.generativeai, cohere or mistralai is imported, directly or
              function-locally. Stated limit: it does not police what boto3 calls internally.
              Keep-fixed; negative control: a fixture constructing client("bedrock-runtime")
              outside bedrock.py, which must be reported.

Partition A: stdlib `ast` only, no SDK, no account.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
from dataclasses import dataclass, field

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ALLOWED_THIRD_PARTY = {"numpy", "yaml", "boto3", "botocore", "bedrock_agentcore"}
DENYLIST = {"torch", "sklearn", "tensorflow", "jax", "xgboost", "lightgbm", "transformers",
            "scipy", "pandas"}
HTTP_AND_INFERENCE = ("requests", "httpx", "urllib3", "http.client", "urllib.request",
                      "aiohttp", "openai", "anthropic", "google.generativeai", "cohere",
                      "mistralai")


# --- the one closure helper (SD-2) ------------------------------------------------------
@dataclass
class Closure:
    first_party: set[str] = field(default_factory=set)       # walked module names
    third_party: set[str] = field(default_factory=set)       # top-level distribution names
    imported: set[str] = field(default_factory=set)          # every dotted name imported
    dynamic: list[str] = field(default_factory=list)         # rule-6 violations
    trees: dict[str, tuple[pathlib.Path, ast.Module]] = field(default_factory=dict)


def _module_file(src: pathlib.Path, name: str) -> pathlib.Path | None:
    base = src.joinpath(*name.split("."))
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    return None


def _is_dynamic_import(call: ast.Call, aliases: set[str]) -> str | None:
    f = call.func
    if isinstance(f, ast.Name) and (f.id == "__import__" or f.id in aliases):
        return f.id
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
        dotted = ".".join(reversed(parts))
        if dotted == "importlib.import_module" or dotted.startswith("importlib.util."):
            return dotted
    return None


def import_closure(root: str, src: pathlib.Path = SRC,
                   first_party: frozenset[str] = frozenset({"delphi"})) -> Closure:
    c = Closure()
    todo = [root]
    while todo:
        name = todo.pop()
        if name in c.first_party:
            continue
        path = _module_file(src, name)
        if path is None:
            raise AssertionError(f"first-party module {name!r} not found under {src}")
        c.first_party.add(name)
        tree = ast.parse(path.read_text(), filename=str(path))
        c.trees[name] = (path, tree)
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]

        found: list[str] = []
        dyn_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    parts = package.split(".")
                    base_parts = parts[: len(parts) - (node.level - 1)]
                    mod = ".".join(base_parts + ([node.module] if node.module else []))
                else:
                    mod = node.module or ""
                found.append(mod)
                for a in node.names:
                    full = f"{mod}.{a.name}"
                    top = mod.split(".")[0]
                    if top in first_party:
                        if _module_file(src, full) is not None:
                            found.append(full)
                    else:
                        c.imported.add(full)
                    if mod == "importlib" and a.name == "import_module":
                        dyn_aliases.add(a.asname or a.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                hit = _is_dynamic_import(node, dyn_aliases)
                if hit:
                    c.dynamic.append(f"{name}:{node.lineno} {hit}(...)")

        for mod in found:
            if not mod:
                continue
            c.imported.add(mod)
            top = mod.split(".")[0]
            if top in first_party:
                parts = mod.split(".")
                for i in range(1, len(parts) + 1):  # rule 3: parents run their __init__
                    todo.append(".".join(parts[:i]))
            elif top not in sys.stdlib_module_names:
                c.third_party.add(top)
    return c


def problems(c: Closure) -> list[str]:
    """Everything the request-path boundary forbids, as human-readable lines."""
    out = [f"denylisted import: {n}" for n in sorted(c.third_party & DENYLIST)]
    out += [f"third-party import outside the serving set: {n}"
            for n in sorted(c.third_party - ALLOWED_THIRD_PARTY - DENYLIST)]
    out += [f"dynamic import (cannot be followed statically): {d}" for d in c.dynamic]
    return out


def bedrock_client_sites(c: Closure) -> list[str]:
    """Module names that construct client("bedrock-runtime")."""
    sites = []
    for name, (_, tree) in sorted(c.trees.items()):
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "client" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "bedrock-runtime"):
                sites.append(name)
    return sites


@pytest.fixture(scope="module")
def closure() -> Closure:
    return import_closure("delphi.agent.entrypoint")


# --- T-PKG-U3-3 -------------------------------------------------------------------------
def test_t_pkg_u3_3a_third_party_set_is_the_serving_set(closure):
    assert closure.third_party <= ALLOWED_THIRD_PARTY, sorted(closure.third_party)
    assert not closure.third_party & DENYLIST
    assert problems(closure) == []


def test_t_pkg_u3_3b_positive_controls_are_in_the_closure(closure):
    for m in ("delphi.train.artifact_store", "delphi.train.harness", "delphi.train.routers",
              "delphi.eval.matrix", "delphi.agent.app", "delphi.router"):
        assert m in closure.first_party, m


def test_t_pkg_u3_3c_offline_modules_are_not_in_the_closure(closure):
    for m in ("delphi.eval.scoring", "delphi.train.publish", "delphi.train.generate",
              "delphi.train.artifact_contract", "delphi.train.artifact_location",
              # U4 BR4.15 (BRK-21): the exec-bearing runner and child harness stay off the
              # request path.
              "delphi.eval.runner", "delphi.eval.cell_harness",
              # U5 FR9.1.5 (BRK-U5-1): the repository tools never enter the container.
              "delphi.tools.scrub_check", "delphi.tools.ci_preflight"):
        assert m not in closure.first_party, m


def _pkg(tmp_path: pathlib.Path, files: dict[str, str]) -> pathlib.Path:
    src = tmp_path / "src"
    for rel, text in files.items():
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return src


NEGATIVE_CONTROLS = {
    "function-local import": (
        {"pkg/__init__.py": "", "pkg/entry.py": "def f():\n    import sklearn\n"},
        "denylisted import: sklearn"),
    "parent __init__ import": (
        {"pkg/__init__.py": "", "pkg/entry.py": "import pkg.sub.mod\n",
         "pkg/sub/__init__.py": "import torch\n", "pkg/sub/mod.py": "X = 1\n"},
        "denylisted import: torch"),
    "from-import of a submodule": (
        {"pkg/__init__.py": "", "pkg/entry.py": "from pkg import sub\n",
         "pkg/sub.py": "import pandas as pd\n"},
        "denylisted import: pandas"),
    "dynamic import": (
        {"pkg/__init__.py": "", "pkg/entry.py": "import importlib\nimportlib.import_module('x')\n"},
        "dynamic import (cannot be followed statically): pkg.entry:2 importlib.import_module(...)"),
}


@pytest.mark.parametrize("case", sorted(NEGATIVE_CONTROLS))
def test_t_pkg_u3_3d_negative_controls_are_reported(case, tmp_path):
    files, expected = NEGATIVE_CONTROLS[case]
    c = import_closure("pkg.entry", _pkg(tmp_path, files), frozenset({"pkg"}))
    assert expected in problems(c)


def test_t_pkg_u3_3d_relative_imports_are_resolved(tmp_path):
    src = _pkg(tmp_path, {"pkg/__init__.py": "", "pkg/a/__init__.py": "",
                          "pkg/a/entry.py": "from ..b import mod\n", "pkg/b/__init__.py": "",
                          "pkg/b/mod.py": "import xgboost\n"})
    c = import_closure("pkg.a.entry", src, frozenset({"pkg"}))
    assert "pkg.b.mod" in c.first_party
    assert "denylisted import: xgboost" in problems(c)


# --- T-PKG-U3-5 -------------------------------------------------------------------------
_LINE = re.compile(r"^Request-path-safe modules: (.+)$", re.M)


def test_t_pkg_u3_5_boundary_line_equals_the_closure(closure):
    doc = ast.get_docstring(ast.parse((SRC / "delphi" / "train" / "__init__.py").read_text()))
    assert doc, "train/__init__.py has no docstring"
    m = _LINE.search(doc)
    assert m, "no 'Request-path-safe modules:' line in train/__init__.py"
    declared = {s.strip() for s in m.group(1).split(",")}
    actual = {n for n in closure.first_party if n.startswith("delphi.train.")}
    assert declared == actual
    assert "never AgentCore" not in doc


def test_t_pkg_u3_5_artifact_store_docstring_is_true():
    doc = ast.get_docstring(ast.parse((SRC / "delphi" / "train" / "artifact_store.py")
                                      .read_text()))
    for name in ("weights.npz", "scalars.json", "manifest.json"):
        assert name in doc, name
    assert "swap the path" not in doc


# --- T-PKG-U3-6 -------------------------------------------------------------------------
def test_t_pkg_u3_6a_only_bedrock_py_builds_a_bedrock_runtime_client(closure):
    assert bedrock_client_sites(closure) == ["delphi.agent.bedrock"]


def test_t_pkg_u3_6b_no_http_or_inference_sdk_on_the_request_path(closure):
    hits = sorted(n for n in closure.imported for bad in HTTP_AND_INFERENCE
                  if n == bad or n.startswith(bad + "."))
    assert hits == []


def test_t_pkg_u3_6_negative_control_reports_a_second_client_site(tmp_path):
    src = _pkg(tmp_path, {"pkg/__init__.py": "", "pkg/entry.py": "import pkg.other\n",
                          "pkg/other.py": "def f(boto3):\n    return boto3.client("
                                          "'bedrock-runtime')\n"})
    c = import_closure("pkg.entry", src, frozenset({"pkg"}))
    assert bedrock_client_sites(c) == ["pkg.other"]


def test_t_pkg_u3_6_negative_control_reports_an_http_import(tmp_path):
    src = _pkg(tmp_path, {"pkg/__init__.py": "",
                          "pkg/entry.py": "def f():\n    from http import client\n"})
    c = import_closure("pkg.entry", src, frozenset({"pkg"}))
    assert "http.client" in c.imported
