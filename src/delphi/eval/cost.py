"""Per-query cost from token counts (T-INF-2 ground truth).

Cost is computed from Bedrock invocation-log token counts using the pinned price
snapshot in config/models.yaml — never from estimates. This module also reports cost
in tokens-by-model so results can be re-priced after a provider price change (Threat #4).
"""
from __future__ import annotations

from dataclasses import dataclass

from delphi.config import Pool


@dataclass(frozen=True)
class Usage:
    """Token counts for one model invocation, as read from the invocation log."""

    alias: str
    input_tokens: int
    output_tokens: int


def query_cost(pool: Pool, usage: Usage) -> float:
    """Dollar cost of a single invocation."""
    return pool.by_alias(usage.alias).price.cost(
        usage.input_tokens, usage.output_tokens
    )


def arm_cost_per_query(pool: Pool, usages: list[Usage]) -> float:
    """Mean dollar cost per query for an arm, given its per-query usages."""
    if not usages:
        return 0.0
    return sum(query_cost(pool, u) for u in usages) / len(usages)


def tokens_by_model(usages: list[Usage]) -> dict[str, tuple[int, int]]:
    """Aggregate (input, output) tokens per alias — the re-priceable artifact (Threat #4)."""
    agg: dict[str, list[int]] = {}
    for u in usages:
        cur = agg.setdefault(u.alias, [0, 0])
        cur[0] += u.input_tokens
        cur[1] += u.output_tokens
    return {a: (v[0], v[1]) for a, v in agg.items()}


def reconciles_with_cost_explorer(
    computed_total: float, cost_explorer_total: float, tolerance: float = 0.02
) -> bool:
    """T-INF-2: token-derived cost must match Cost Explorer within tolerance (2%)."""
    if cost_explorer_total == 0:
        return computed_total == 0
    return abs(computed_total - cost_explorer_total) / cost_explorer_total <= tolerance
