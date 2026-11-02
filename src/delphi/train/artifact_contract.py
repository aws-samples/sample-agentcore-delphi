"""The C3 artifact contract as one checker (contract C3, ADR-009; NFR8.5, NFR10.8).

`check_artifact(art_dir, pool)` inspects a local artifact triple — `weights.npz`,
`scalars.json`, `manifest.json` — against the EXPLICITLY named pool and returns every
violated invariant as a named `Violation`. It raises nothing on a bad artifact: a caller
(the `delphi-artifact` publisher, a test) decides what a violation means.

This is the T-RTR-6 guard moved to the cheap end of the pipeline. `LearnedRouter` still
refuses a mis-paired artifact at container import; this check refuses it before upload,
where the failure is a one-line local error rather than a dead production container. The
alias rule itself is ONE function, `delphi.router.learned.unknown_aliases`, shared by both.

Invariants (each a small function below, codes stable for tests and messages):
  missing_file     all three files are present and parse
  alias_subset     trained_aliases is a subset of the named pool's aliases
  scalars_aliases  scalars.aliases == manifest.trained_aliases
  scalars_family   scalars.family == manifest.family
  family_arity     the family's arrays exist with one entry per trained alias
  encoder_dim      manifest.encoder_dim matches the arrays' feature dimension
  artifact_hash    the recomputed hash equals manifest.artifact_hash (32 hex characters)
  weights_uri      present and NOT an absolute filesystem path (checked at publication
                   only — the cold-start loader still accepts the live manifest as it is)

Offline/publication-side module: it is not imported by the request path (T-PKG-U3-3).
Imports only the standard library, numpy and delphi.*.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from delphi.config import Pool
from delphi.router.learned import unknown_aliases
from delphi.train.harness import artifact_hash

ARTIFACT_FILES = ("weights.npz", "scalars.json", "manifest.json")
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class Violation:
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class _Loaded:
    manifest: dict
    scalars: dict
    arrays: dict[str, np.ndarray]


def _load(d: pathlib.Path) -> tuple[_Loaded | None, list[Violation]]:
    missing = [n for n in ARTIFACT_FILES if not (d / n).is_file()]
    if missing:
        return None, [Violation("missing_file", f"{d}: missing {', '.join(missing)}")]
    try:
        manifest = json.loads((d / "manifest.json").read_text())
        scalars = json.loads((d / "scalars.json").read_text())
        with np.load(d / "weights.npz", allow_pickle=False) as npz:
            arrays = {k: npz[k] for k in npz.files}
    except (ValueError, OSError) as exc:
        return None, [Violation("missing_file", f"{d}: unreadable artifact file ({exc})")]
    if not isinstance(manifest, dict) or not isinstance(scalars, dict):
        return None, [Violation("missing_file", f"{d}: manifest/scalars must be JSON objects")]
    return _Loaded(manifest, scalars, arrays), []


# --- one function per invariant --------------------------------------------------
def _alias_subset(a: _Loaded, pool: Pool) -> list[Violation]:
    unknown = unknown_aliases(a.manifest.get("trained_aliases", ()), pool.aliases)
    if unknown:
        return [Violation("alias_subset",
                          f"trained aliases {sorted(unknown)} absent from the named pool "
                          f"{list(pool.aliases)}")]
    return []


def _scalars_aliases(a: _Loaded, pool: Pool) -> list[Violation]:
    if list(a.scalars.get("aliases", [])) != list(a.manifest.get("trained_aliases", [])):
        return [Violation("scalars_aliases",
                          f"scalars.aliases {a.scalars.get('aliases')} != "
                          f"manifest.trained_aliases {a.manifest.get('trained_aliases')}")]
    return []


def _scalars_family(a: _Loaded, pool: Pool) -> list[Violation]:
    if a.scalars.get("family") != a.manifest.get("family"):
        return [Violation("scalars_family",
                          f"scalars.family {a.scalars.get('family')!r} != "
                          f"manifest.family {a.manifest.get('family')!r}")]
    return []


def _feature_dim(a: _Loaded) -> tuple[int | None, list[str]]:
    """The arrays' feature dimension, plus any arity problems, for the manifest's family."""
    fam = a.manifest.get("family")
    n = len(a.manifest.get("trained_aliases", []))
    arr = a.arrays
    problems: list[str] = []

    def need(name: str, ndim: int) -> np.ndarray | None:
        if name not in arr:
            problems.append(f"{fam}: array {name!r} missing")
            return None
        if arr[name].ndim != ndim:
            problems.append(f"{fam}: {name} has rank {arr[name].ndim}, expected {ndim}")
            return None
        return arr[name]

    def per_alias(name: str, size: int) -> None:
        if size != n:
            problems.append(f"{fam}: {name} has {size} alias entries, expected {n}")

    if fam == "linear":
        W, b = need("W", 2), need("b", 1)
        if W is not None:
            per_alias("W", W.shape[1])
        if b is not None:
            per_alias("b", b.shape[0])
        return (W.shape[0] if W is not None else None), problems
    if fam == "prototype":
        P, scale = need("P", 2), need("scale", 1)
        if P is not None:
            per_alias("P", P.shape[0])
        if scale is not None:
            per_alias("scale", scale.shape[0])
        return (P.shape[1] if P is not None else None), problems
    if fam == "knn":
        Xtr, Ytr = need("Xtr", 2), need("Ytr", 2)
        if Ytr is not None:
            per_alias("Ytr", Ytr.shape[1])
        if Xtr is not None and Ytr is not None and Xtr.shape[0] != Ytr.shape[0]:
            problems.append(f"knn: Xtr has {Xtr.shape[0]} rows, Ytr {Ytr.shape[0]}")
        k = a.scalars.get("k")
        if not isinstance(k, int) or isinstance(k, bool) or k < 1:
            problems.append(f"knn: scalar k must be a positive integer, got {k!r}")
        return (Xtr.shape[1] if Xtr is not None else None), problems
    problems.append(f"unknown family {fam!r}")
    return None, problems


def _family_arity(a: _Loaded, pool: Pool) -> list[Violation]:
    _, problems = _feature_dim(a)
    return [Violation("family_arity", p) for p in problems]


def _encoder_dim(a: _Loaded, pool: Pool) -> list[Violation]:
    dim, _ = _feature_dim(a)
    declared = a.manifest.get("encoder_dim")
    if dim is not None and declared != dim:
        return [Violation("encoder_dim",
                          f"manifest.encoder_dim {declared!r} != array feature dimension {dim}")]
    return []


def _artifact_hash(a: _Loaded, pool: Pool) -> list[Violation]:
    declared = a.manifest.get("artifact_hash")
    if not isinstance(declared, str) or not _HEX32.match(declared):
        return [Violation("artifact_hash",
                          f"manifest.artifact_hash {declared!r} is not 32 hex characters")]
    state = {**a.arrays, **a.scalars}
    try:
        actual = artifact_hash(state, int(a.manifest.get("encoder_dim")))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return [Violation("artifact_hash", f"cannot recompute the hash ({exc!r})")]
    if actual != declared:
        return [Violation("artifact_hash",
                          f"manifest.artifact_hash {declared} != recomputed {actual}")]
    return []


def _is_absolute(uri: str) -> bool:
    return (uri.startswith("/") or uri.startswith("\\") or uri.startswith("file:")
            or bool(re.match(r"^[A-Za-z]:[\\/]", uri)))


def _weights_uri(a: _Loaded, pool: Pool) -> list[Violation]:
    uri = a.manifest.get("weights_uri")
    if not isinstance(uri, str) or not uri:
        return [Violation("weights_uri", "manifest.weights_uri is missing")]
    if _is_absolute(uri):
        return [Violation("weights_uri",
                          f"manifest.weights_uri {uri!r} is an absolute filesystem path; it "
                          f"must be relative to the manifest (e.g. 'weights.npz')")]
    return []


_INVARIANTS: tuple[Callable[[_Loaded, Pool], list[Violation]], ...] = (
    _alias_subset, _scalars_aliases, _scalars_family, _family_arity,
    _encoder_dim, _artifact_hash, _weights_uri,
)


def check_artifact(art_dir: str | pathlib.Path, pool: Pool) -> list[Violation]:
    """Every violated C3 invariant for the artifact in `art_dir` against `pool`."""
    loaded, violations = _load(pathlib.Path(art_dir))
    if loaded is None:
        return violations
    for invariant in _INVARIANTS:
        violations.extend(invariant(loaded, pool))
    return violations


__all__ = ["ARTIFACT_FILES", "Violation", "check_artifact", "unknown_aliases"]
