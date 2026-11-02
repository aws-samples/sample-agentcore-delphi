"""Oracle-gate report (T-STAT-1) — the go/no-go artifact for H2 (design §4 Phase 1).

Computes the oracle gap and per-model summary from a response matrix and renders the
kill/pivot decision the whole project hinges on: if the oracle barely beats the best
single model, H2 is unreachable and the project pivots to H1 (cost) only.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from delphi.eval.matrix import ResponseMatrix, capture_ratio, oracle_gap

GATE_THRESHOLD = 3.0  # design §4 Phase 1 kill/pivot on T-STAT-1; 3.0 is defined here, not there


@dataclass(frozen=True)
class GateReport:
    oracle_mean: float
    best_single_alias: str
    best_single_mean: float
    random_mean: float
    gap: float
    passes: bool
    per_model_mean: dict[str, float]
    oracle_capture: float  # sanity: oracle's own capture ratio (should be ~1.0)

    def verdict(self) -> str:
        if self.passes:
            return (f"PASS — oracle gap {self.gap:.2f} ≥ {GATE_THRESHOLD}. Pool has "
                    f"non-nested error sets; H2 (quality dominance) is worth pursuing.")
        return (f"FAIL — oracle gap {self.gap:.2f} < {GATE_THRESHOLD}. Error sets are largely "
                f"nested; abandon H2 for this pool, pivot to H1 (cost) or enlarge the pool.")


def evaluate_gate(matrix: ResponseMatrix) -> GateReport:
    gap = oracle_gap(matrix)
    bsi = matrix.best_single_index()
    per_model = {
        alias: float(matrix.scores[:, j].mean()) for j, alias in enumerate(matrix.aliases)
    }
    return GateReport(
        oracle_mean=float(matrix.oracle_per_query().mean()),
        best_single_alias=matrix.aliases[bsi],
        best_single_mean=float(matrix.best_single_per_query().mean()),
        random_mean=float(matrix.random_per_query().mean()),
        gap=gap,
        passes=gap >= GATE_THRESHOLD,
        per_model_mean=per_model,
        oracle_capture=capture_ratio(matrix.oracle_per_query(), matrix),
    )


def render(report: GateReport) -> str:
    lines = [
        "=" * 70,
        "ORACLE GATE REPORT (T-STAT-1)",
        "=" * 70,
        "",
        "Per-model mean quality:",
    ]
    for alias, m in sorted(report.per_model_mean.items(), key=lambda kv: -kv[1]):
        star = "  <- best single" if alias == report.best_single_alias else ""
        lines.append(f"  {alias:12s} {m:6.2f}{star}")
    lines += [
        "",
        f"  {'random':12s} {report.random_mean:6.2f}  (uniform routing baseline)",
        f"  {'ORACLE':12s} {report.oracle_mean:6.2f}  (per-query argmax, upper bound)",
        "",
        f"Oracle gap (oracle - best_single): {report.gap:.2f} quality points",
        f"Gate threshold: {GATE_THRESHOLD}",
        "",
        report.verdict(),
        "=" * 70,
    ]
    return "\n".join(lines)
