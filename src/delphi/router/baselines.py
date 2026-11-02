"""Trivial routers — the sanity floor/ceiling reported in every table (T-CMP-2).

These are fully implemented (no ML). They double as fixtures for the router
behavioural tests and as the always-report comparator arms.
"""
from __future__ import annotations

import hashlib

from delphi.config import Pool
from delphi.router.base import Decision, Router, RouterError


class AlwaysModelRouter(Router):
    """Always routes to one fixed alias. Basis for pinned-model arms (D4) and for
    the always-cheapest / always-strongest comparators."""

    def __init__(self, pool: Pool, alias: str):
        self.pool = pool
        self.pool_aliases = pool.aliases
        if alias not in self.pool_aliases:
            raise RouterError(f"{alias!r} not in pool {self.pool_aliases}")
        self._alias = alias

    def predict(self, query: str) -> Decision:
        c = self.pool.by_alias(self._alias)
        scores = {a: (1.0 if a == self._alias else 0.0) for a in self.pool_aliases}
        return self._validate(
            Decision(
                model_id=c.id,
                chosen_alias=self._alias,
                scores=scores,
                latency_ms=0.0,
                metadata={"policy": "always", "target": self._alias},
            )
        )


class AlwaysCheapestRouter(AlwaysModelRouter):
    def __init__(self, pool: Pool):
        cheapest = min(
            pool.candidates, key=lambda c: c.price.input + c.price.output
        )
        super().__init__(pool, cheapest.alias)


class RandomRouter(Router):
    """Uniform-over-pool baseline. Seeded per query for determinism (T-RTR-1) so a
    fixed seed reproduces the same assignment across runs; averaged over 5 seeds
    when used as the H3 denominator (design §1.1 H3 capture ratio; T-STAT-4)."""

    def __init__(self, pool: Pool, seed: int = 0):
        self.pool = pool
        self.pool_aliases = pool.aliases
        self._seed = seed

    def _pick(self, query: str) -> int:
        h = hashlib.sha256(f"{self._seed}:{query}".encode()).hexdigest()
        return int(h, 16) % len(self.pool_aliases)

    def predict(self, query: str) -> Decision:
        idx = self._pick(query)
        alias = self.pool_aliases[idx]
        scores = {a: (1.0 if a == alias else 0.0) for a in self.pool_aliases}
        return self._validate(
            Decision(
                model_id=self.pool.by_alias(alias).id,
                chosen_alias=alias,
                scores=scores,
                latency_ms=0.0,
                metadata={"policy": "random", "seed": self._seed},
            )
        )


class OracleRouter(Router):
    """Upper bound: per-query argmax over the response matrix (design §1.1, the oracle).

    Requires the response matrix M[query][alias] = quality score, so it can only be
    evaluated offline over queries already in the matrix — it is NOT deployable on the
    request path. It exists to compute the oracle gap (T-STAT-1 gate) and the H3
    capture-ratio numerator ceiling.
    """

    def __init__(self, pool: Pool, matrix: dict[str, dict[str, float]]):
        self.pool = pool
        self.pool_aliases = pool.aliases
        self._matrix = matrix

    def predict(self, query: str) -> Decision:
        row = self._matrix.get(query)
        if row is None:
            raise RouterError(
                "oracle can only route queries present in the response matrix"
            )
        # argmax over candidates that are both scored and in the deployed pool
        scored = {a: row[a] for a in self.pool_aliases if a in row}
        if not scored:
            raise RouterError("no pool candidate scored for this query")
        alias = max(scored, key=scored.__getitem__)
        return self._validate(
            Decision(
                model_id=self.pool.by_alias(alias).id,
                chosen_alias=alias,
                scores=scored,
                latency_ms=0.0,
                metadata={"policy": "oracle"},
            )
        )
