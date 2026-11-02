"""Training + selection harness (design §4 Phase 2).

Given per-split response matrices (train + validation), for each router family:
  1. featurize queries with the HashingEncoder,
  2. fit the family on the TRAIN matrix,
  3. score it on VALIDATION by capture ratio (T-STAT-4 criterion) and measure per-query
     routing latency,
  4. select the family with the highest validation capture ratio that also meets the
     latency budget (T-RTR-4). Selection happens on VALIDATION only, never test
     (Threat #6, selection-inference leakage).

Emits a versioned Artifact (delphi.router.learned.Artifact) + a numpy state blob, so the
winner drops straight into LearnedRouter on the request path.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import numpy as np

from delphi.eval.matrix import ResponseMatrix, capture_ratio
from delphi.router.features import HashingEncoder
from delphi.train.routers import FAMILIES, TrainedRouter


@dataclass(frozen=True)
class FamilyResult:
    family: str
    capture: float                 # validation capture ratio (selection metric)
    val_mean_quality: float
    p90_latency_ms: float
    meets_latency: bool
    router: TrainedRouter


@dataclass(frozen=True)
class SelectionResult:
    winner: FamilyResult
    all_results: tuple[FamilyResult, ...]
    encoder_dim: int

    def table(self) -> str:
        rows = ["family      capture  val_q   p90_ms  ok"]
        for r in sorted(self.all_results, key=lambda x: -x.capture):
            mark = "*" if r is self.winner else " "
            ok = "yes" if r.meets_latency else "NO"
            rows.append(f"{mark}{r.family:10s} {r.capture:6.3f} {r.val_mean_quality:6.2f} "
                        f"{r.p90_latency_ms:6.2f}  {ok}")
        return "\n".join(rows)


def _quality_of_choices(matrix: ResponseMatrix, chosen_idx: np.ndarray) -> np.ndarray:
    return matrix.policy_per_query(chosen_idx)


def _evaluate(
    router: TrainedRouter,
    encoder: HashingEncoder,
    val_matrix: ResponseMatrix,
    val_prompts: list[str],
    latency_budget_ms: float,
) -> FamilyResult:
    X = encoder.matrix(val_prompts)
    pred = router.predict_quality(X)                 # (n, n_aliases) predicted quality
    # align predicted-alias columns to the matrix's alias order
    col = {a: j for j, a in enumerate(router.aliases)}
    order = [col[a] for a in val_matrix.aliases]
    chosen = pred[:, order].argmax(axis=1)
    routed = _quality_of_choices(val_matrix, chosen)
    cap = capture_ratio(routed, val_matrix)

    # per-query routing latency (embed + score), P90 over the validation prompts
    lat = []
    for q in val_prompts:
        t = time.perf_counter()
        e = encoder.embed_vec(q)
        router.score(e, val_matrix.aliases)
        lat.append((time.perf_counter() - t) * 1000.0)
    p90 = float(np.percentile(lat, 90)) if lat else 0.0

    return FamilyResult(
        family=router.family,
        capture=cap,
        val_mean_quality=float(routed.mean()),
        p90_latency_ms=p90,
        meets_latency=p90 <= latency_budget_ms,
        router=router,
    )


def train_and_select(
    train_matrix: ResponseMatrix,
    train_prompts: list[str],
    val_matrix: ResponseMatrix,
    val_prompts: list[str],
    *,
    encoder_dim: int = 512,
    latency_budget_ms: float = 150.0,
    families: list[str] | None = None,
) -> SelectionResult:
    if train_matrix.aliases != val_matrix.aliases:
        raise ValueError("train/val matrices must share the same alias order")
    encoder = HashingEncoder(dim=encoder_dim)
    Xtr = encoder.matrix(train_prompts)
    Ytr = train_matrix.scores
    aliases = train_matrix.aliases

    chosen_families = families or list(FAMILIES)
    results: list[FamilyResult] = []
    for fam in chosen_families:
        router = FAMILIES[fam].fit(Xtr, Ytr, aliases)
        results.append(_evaluate(router, encoder, val_matrix, val_prompts, latency_budget_ms))

    # select: highest capture among latency-passing families; if none pass, highest capture
    passing = [r for r in results if r.meets_latency]
    pool = passing or results
    winner = max(pool, key=lambda r: r.capture)
    return SelectionResult(winner=winner, all_results=tuple(results), encoder_dim=encoder_dim)


def artifact_hash(state: dict, encoder_dim: int) -> str:
    h = hashlib.sha256()
    h.update(str(encoder_dim).encode())
    h.update(state["family"].encode())
    for key in sorted(state):
        val = state[key]
        if isinstance(val, np.ndarray):
            h.update(key.encode())
            h.update(np.ascontiguousarray(val).tobytes())
    return h.hexdigest()[:32]
