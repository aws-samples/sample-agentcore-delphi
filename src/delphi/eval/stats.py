"""Statistical tests — the falsifiable core (design §3).

Paired bootstrap, non-inferiority testing, Holm-Bonferroni correction, and a power
analysis for sample sizing. Pure numpy so it runs anywhere the eval harness runs.

Every function returns a plain dataclass with the numbers a verdict is read off of;
the T-STAT tests assert on these fields. Seeds are explicit for determinism.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BootstrapCI:
    mean_delta: float
    lower: float
    upper: float
    level: float  # e.g. 0.95


def paired_bootstrap_ci(
    delta: np.ndarray,
    *,
    level: float = 0.95,
    resamples: int = 10_000,
    seed: int = 0,
    one_sided: bool = False,
) -> BootstrapCI:
    """Bootstrap CI on the mean of a paired per-query difference vector.

    delta[i] = router(q_i) - model_m(q_i). For two-sided at level L we take the
    (alpha/2, 1-alpha/2) percentiles; for one-sided we report the (alpha) percentile
    as `lower` and +inf as `upper` (used by the non-inferiority test).
    """
    delta = np.asarray(delta, dtype=float)
    n = len(delta)
    rng = np.random.default_rng(seed)
    # resample query indices with replacement, recompute the mean delta each time
    idx = rng.integers(0, n, size=(resamples, n))
    boot_means = delta[idx].mean(axis=1)
    alpha = 1.0 - level
    if one_sided:
        lower = float(np.percentile(boot_means, 100 * alpha))
        upper = float("inf")
    else:
        lower = float(np.percentile(boot_means, 100 * (alpha / 2)))
        upper = float(np.percentile(boot_means, 100 * (1 - alpha / 2)))
    return BootstrapCI(float(delta.mean()), lower, upper, level)


@dataclass(frozen=True)
class SuperiorityResult:
    """T-STAT-2: router superior to every single model, Holm-Bonferroni corrected."""

    per_model: dict[str, BootstrapCI]
    holm_rejected: dict[str, bool]  # alias -> reject H0 (router not better) at family level
    superior_to_all: bool


def superiority_vs_all(
    router: np.ndarray,
    per_model: dict[str, np.ndarray],
    *,
    family_alpha: float = 0.05,
    resamples: int = 10_000,
    seed: int = 0,
) -> SuperiorityResult:
    """Paired bootstrap of router - model for every model, with Holm-Bonferroni across
    the family of |pool| comparisons. superior_to_all is True iff every comparison's
    corrected CI lower bound is > 0 (router strictly beats each single model).

    We compute a p-value per comparison as the bootstrap mass at or below 0, then apply
    Holm's step-down procedure at family_alpha.
    """
    aliases = sorted(per_model)
    cis: dict[str, BootstrapCI] = {}
    pvals: dict[str, float] = {}
    rng = np.random.default_rng(seed)
    n = len(router)
    for a in aliases:
        delta = np.asarray(router, float) - np.asarray(per_model[a], float)
        idx = rng.integers(0, n, size=(resamples, n))
        boot = delta[idx].mean(axis=1)
        cis[a] = BootstrapCI(
            float(delta.mean()),
            float(np.percentile(boot, 2.5)),
            float(np.percentile(boot, 97.5)),
            0.95,
        )
        # one-sided p: P(mean delta <= 0) under bootstrap
        pvals[a] = float((boot <= 0).mean())

    rejected = holm_bonferroni(pvals, alpha=family_alpha)
    return SuperiorityResult(
        per_model=cis,
        holm_rejected=rejected,
        superior_to_all=all(rejected.values()),
    )


def holm_bonferroni(pvals: dict[str, float], *, alpha: float = 0.05) -> dict[str, bool]:
    """Holm step-down. Returns alias -> whether H0 is rejected at family-wise alpha."""
    ordered = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(ordered)
    rejected: dict[str, bool] = {}
    still_rejecting = True
    for rank, (alias, p) in enumerate(ordered):
        threshold = alpha / (m - rank)
        if still_rejecting and p <= threshold:
            rejected[alias] = True
        else:
            still_rejecting = False
            rejected[alias] = False
    return rejected


@dataclass(frozen=True)
class NonInferiorityResult:
    """T-STAT-3: router non-inferior to best single within margin delta, plus cost gate."""

    ci: BootstrapCI
    margin: float
    quality_non_inferior: bool
    cost_ratio: float
    cost_ok: bool
    passes: bool


def non_inferiority(
    router: np.ndarray,
    best_single: np.ndarray,
    *,
    margin: float = 2.0,
    router_cost_per_query: float,
    best_single_cost_per_query: float,
    cost_ceiling_ratio: float = 0.70,
    level: float = 0.975,
    resamples: int = 10_000,
    seed: int = 0,
) -> NonInferiorityResult:
    """Non-inferiority on quality (one-sided lower bound > -margin) AND cost <= ratio.

    delta = router - best_single. Non-inferior if the one-sided lower CI bound exceeds
    -margin (router is not worse than best-single by more than the margin).
    """
    delta = np.asarray(router, float) - np.asarray(best_single, float)
    ci = paired_bootstrap_ci(
        delta, level=level, resamples=resamples, seed=seed, one_sided=True
    )
    quality_ok = ci.lower > -margin
    cost_ratio = router_cost_per_query / best_single_cost_per_query
    cost_ok = cost_ratio <= cost_ceiling_ratio
    return NonInferiorityResult(
        ci=ci,
        margin=margin,
        quality_non_inferior=quality_ok,
        cost_ratio=cost_ratio,
        cost_ok=cost_ok,
        passes=quality_ok and cost_ok,
    )


@dataclass(frozen=True)
class PowerResult:
    n_required: int
    effect_size: float
    achieved_power: float


def required_n_for_paired_diff(
    *,
    effect: float,
    sd_of_diff: float,
    power: float = 0.80,
    alpha: float = 0.05,
    two_sided: bool = True,
) -> PowerResult:
    """Sample size for a paired difference test (the design's open question for T-STAT-2).

    Uses the normal approximation n = ((z_alpha + z_beta) * sd / effect)^2. Adequate for
    planning; the bootstrap is what actually reports the verdict. z-values are hard-coded
    for the common (alpha=0.05, power=0.80/0.90) cases to avoid a scipy dependency.
    """
    z_alpha = _z(1 - (alpha / 2 if two_sided else alpha))
    z_beta = _z(power)
    n = ((z_alpha + z_beta) * sd_of_diff / effect) ** 2
    import math

    n_req = int(math.ceil(n))
    return PowerResult(n_required=n_req, effect_size=effect, achieved_power=power)


_Z_TABLE = {
    0.80: 0.8416,
    0.90: 1.2816,
    0.95: 1.6449,
    0.975: 1.9600,
    0.99: 2.3263,
}


def _z(p: float) -> float:
    """Inverse normal CDF for the handful of quantiles the power calc needs."""
    key = round(p, 3)
    if key in _Z_TABLE:
        return _Z_TABLE[key]
    # nearest available quantile — planning-grade precision only
    nearest = min(_Z_TABLE, key=lambda k: abs(k - p))
    return _Z_TABLE[nearest]
