"""Build a router artifact over a named pool — the producer behind `delphi-artifact build`
(FR1.1, FR1.5, NFR10.5, NFR10.6; plan D-CG-2).

Moved, recipe unchanged, from the deleted `scripts/build_realpool_artifact.py`; the only
generalisation is that the pool file is an argument. The routing labels are SYNTHETIC: the
Delphi-Trials domains are remapped round-robin onto the pool's aliases, so the trained router
genuinely routes across every alias. This proves the router MECHANISM on real Bedrock; it is
NOT a routing-quality claim.

Output: `<artifacts_root>/router_<pool>_<hash8>/` by default, named after what it was
trained on (the pool file's stem without `models_`) and its content hash — never after a
deployment role (SD-16). The manifest's `weights_uri` is the manifest-relative
`"weights.npz"` (plan D-CG-1).

Determinism: the artifact is stable within one environment (same numpy/BLAS) and identified
by its `artifact_hash`. It is not claimed to be byte-identical across machines (SD-15).

Offline-only: never imported by the request path (T-PKG-U3-3).
"""
from __future__ import annotations

import hashlib
import pathlib
import warnings

import numpy as np

from delphi.config import Pool, load_pool
from delphi.data import synthetic
from delphi.data.split import split_dataset
from delphi.eval.matrix import ResponseMatrix
from delphi.router.learned import Artifact
from delphi.train.artifact_store import save_artifact
from delphi.train.harness import SelectionResult, artifact_hash, train_and_select

SEED = 20260714
N_PER_DOMAIN = 60
ENCODER_DIM = 512
DOMAINS = ("prophecy", "facts", "reckoning", "craft", "lore")


def default_dir_name(pool_file: str | pathlib.Path, ahash: str) -> str:
    """`router_<pool>_<hash8>`, where <pool> is the pool file stem minus `models_`."""
    stem = pathlib.Path(pool_file).stem
    return f"router_{stem.removeprefix('models_')}_{ahash[:8]}"


def _unit(alias: str, query_id: str) -> float:
    return int(hashlib.sha256(f"{alias}:{query_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _matrix_for(split_ds, aliases: tuple[str, ...], winner_of: dict[str, str]) -> ResponseMatrix:
    qs = split_ds.queries
    scores = np.zeros((len(qs), len(aliases)))
    for i, q in enumerate(qs):
        win = winner_of[q.domain]
        for j, a in enumerate(aliases):
            scores[i, j] = 60 + 8 * _unit(a, q.id) + (28 if a == win else 0)
    return ResponseMatrix(tuple(x.id for x in qs), aliases, scores)


def train_for_pool(pool: Pool) -> SelectionResult:
    """The recipe: synthetic Delphi-Trials data, domains round-robin onto the pool."""
    aliases = pool.aliases
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=N_PER_DOMAIN, seed=SEED)
    winner_of = {d: aliases[i % len(aliases)] for i, d in enumerate(DOMAINS)}
    sp = split_dataset(ds, seed=SEED)
    tr = _matrix_for(sp.train, aliases, winner_of)
    va = _matrix_for(sp.validation, aliases, winner_of)
    return train_and_select(tr, sp.train.prompts(), va, sp.validation.prompts(),
                            encoder_dim=ENCODER_DIM)


def build(pool_file: str | pathlib.Path, out_dir: str | pathlib.Path | None = None, *,
          artifacts_root: str | pathlib.Path = "artifacts") -> tuple[pathlib.Path, Artifact]:
    """Train over the pool in `pool_file` and save the artifact triple.

    `out_dir` wins when given; otherwise the content-derived name under `artifacts_root`.
    Returns the directory and the saved Artifact.
    """
    load_pool.cache_clear()
    try:
        pool = load_pool(pathlib.Path(pool_file))
    finally:
        load_pool.cache_clear()  # a pool loaded here must not leak into later callers
    result = train_for_pool(pool)
    if out_dir is None:
        ahash = artifact_hash(result.winner.router.to_state(), result.encoder_dim)
        out_dir = pathlib.Path(artifacts_root) / default_dir_name(pool_file, ahash)
    out = pathlib.Path(out_dir)
    art = save_artifact(result, str(out))
    return out, art


__all__ = ["build", "default_dir_name", "train_for_pool"]
