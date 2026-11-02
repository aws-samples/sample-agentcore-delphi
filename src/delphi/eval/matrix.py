"""The response matrix M[query][alias] = quality score, and the derived baselines
that the oracle gate (T-STAT-1) and capture ratio (T-STAT-4) are computed from.

Pure numpy/stdlib. This is the single source of the oracle, best-single, and random
reference numbers used everywhere downstream.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ResponseMatrix:
    """Dense quality matrix. rows=queries, cols=candidate aliases.

    scores[i, j] = quality of candidate `aliases[j]` on query `queries[i]`.
    Quality is on a fixed scale (design uses "quality points"); the tests treat it as
    an interval scale where differences are meaningful.
    """

    queries: tuple[str, ...]
    aliases: tuple[str, ...]
    scores: np.ndarray  # shape (n_queries, n_aliases)

    def __post_init__(self):
        if self.scores.shape != (len(self.queries), len(self.aliases)):
            raise ValueError(
                f"scores shape {self.scores.shape} != "
                f"({len(self.queries)}, {len(self.aliases)})"
            )

    # --- reference policies, per-query vectors (length n_queries) ---

    def oracle_per_query(self) -> np.ndarray:
        """Best achievable per query: max over candidates."""
        return self.scores.max(axis=1)

    def best_single_index(self) -> int:
        """Index of the single model with the highest mean quality."""
        return int(self.scores.mean(axis=0).argmax())

    def best_single_per_query(self) -> np.ndarray:
        """The per-query scores of the single best-on-average model (a real deployable
        arm, unlike the oracle)."""
        return self.scores[:, self.best_single_index()]

    def random_per_query(self, seeds: int = 5) -> np.ndarray:
        """Expected per-query score under uniform routing, averaged over `seeds`
        pseudo-random assignments. With the full matrix this equals the row mean, but
        we average explicit seeded draws to match the design's stated protocol
        (T-STAT-4, "5 seeds, averaged")."""
        rng_scores = np.empty((seeds, len(self.queries)))
        for s in range(seeds):
            rng = np.random.default_rng(s)
            choice = rng.integers(0, len(self.aliases), size=len(self.queries))
            rng_scores[s] = self.scores[np.arange(len(self.queries)), choice]
        return rng_scores.mean(axis=0)

    def policy_per_query(self, chosen_indices: np.ndarray) -> np.ndarray:
        """Per-query score of an arbitrary policy given its chosen column per row."""
        return self.scores[np.arange(len(self.queries)), chosen_indices]


def oracle_gap(matrix: ResponseMatrix) -> float:
    """mean(oracle) - mean(best_single). The T-STAT-1 gate quantity.

    If this is small, the candidate pool has largely nested error sets and H2 is
    unreachable regardless of router quality (design §1.1 critical structural fact).
    """
    return float(matrix.oracle_per_query().mean() - matrix.best_single_per_query().mean())


#: The H3 (learnability) bar: a router passes when it captures at least this share of the
#: oracle gap on the held-out test split (design 1.1). Defined once, so every published
#: statement of the bar is checked against this value (T-EVD-U5-5).
H3_CAPTURE_THRESHOLD: float = 0.60


def capture_ratio(router_per_query: np.ndarray, matrix: ResponseMatrix, seeds: int = 5) -> float:
    """(router - random) / (oracle - random) on mean quality. T-STAT-4.

    1.0 means the router matches the oracle; 0.0 means no better than random.
    """
    router = float(router_per_query.mean())
    random = float(matrix.random_per_query(seeds=seeds).mean())
    oracle = float(matrix.oracle_per_query().mean())
    denom = oracle - random
    if abs(denom) < 1e-12:
        raise ValueError("oracle == random; capture ratio undefined (dead pool)")
    return (router - random) / denom
