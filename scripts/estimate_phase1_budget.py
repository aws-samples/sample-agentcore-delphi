#!/usr/bin/env python3
"""Phase 1 token-budget estimator (design §4 Phase 1, my review point #1).

Phase 1 generates the response matrix: every query x every candidate, scored. This is
the expensive step (|queries| x |pool| full generations). This script computes the
dollar cost from the pinned price snapshot and prints a budget table with a hard cap
recommendation.

Run: python scripts/estimate_phase1_budget.py
Cost = sum over candidates of price.cost(avg_input, avg_output) * n_queries.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "src")

from delphi.config import load_pool  # noqa: E402 - after the src/ path shim

# Assumptions (`phase1_budget.md` § 2; Bedrock patterns doc: 1K tokens ~= 750 words).
# Averaged across the benchmark mix (GSM8K/MMLU/MBPP/NaturalQA/ARC + domain set).
AVG_INPUT_TOKENS = 600     # prompt + few-shot context, blended across benchmarks
AVG_OUTPUT_TOKENS = 350    # chain-of-thought answers pull this up vs. plain MC
N_QUERIES_PER_SCENARIO = {
    "smoke (100/bench x5 + domain)": 600,
    "planned (1000/bench x5 + 1000 domain)": 6000,
    "thorough (2000/bench x5 + 2000 domain)": 12000,
}
JUDGE_INPUT = AVG_INPUT_TOKENS + AVG_OUTPUT_TOKENS  # judge reads prompt + candidate answer
JUDGE_OUTPUT = 120                                  # short verdict + score


def main() -> None:
    pool = load_pool()

    print("=" * 74)
    print("DELPHI — Phase 1 Response-Matrix Token Budget")
    print(f"Price snapshot: {pool.snapshot_date} (us-east-1, on-demand)")
    print(f"Assumed avg tokens: {AVG_INPUT_TOKENS} in / {AVG_OUTPUT_TOKENS} out per generation")
    print("=" * 74)

    # per-candidate cost of one full generation
    print("\nPer-generation cost by candidate (one query -> one response):")
    per_gen = {}
    for c in pool.candidates:
        cost = c.price.cost(AVG_INPUT_TOKENS, AVG_OUTPUT_TOKENS)
        per_gen[c.alias] = cost
        flag = "" if c.verified else "  [PRICE UNVERIFIED — confirm Phase 0]"
        print(f"  {c.alias:18s} ${cost:.6f}{flag}")

    matrix_cost_per_query = sum(per_gen.values())  # one row = all candidates on one query
    print(f"\nOne matrix row (all {len(pool.candidates)} candidates on 1 query): "
          f"${matrix_cost_per_query:.5f}")
    print(f"Candidate price spread (dearest/cheapest, blended): {pool.price_spread():.1f}x "
          f"(need >10x for meaningful H1 headroom)")

    # judge cost per query (T-STAT-5b, open-ended only, but budget for the whole set)
    judge = pool.by_alias("nova-pro")  # placeholder unit cost until judge is a Candidate
    _ = judge  # judge is gpt-5.4, not in pool; use a representative mid price for planning
    judge_per_query = pool.by_alias("mistral-large").price.cost(JUDGE_INPUT, JUDGE_OUTPUT) \
        * len(pool.candidates)  # judge scores every candidate's answer

    print("\n" + "-" * 74)
    print(f"{'Scenario':<42s}{'Matrix $':>12s}{'+Judge $':>10s}{'Total $':>10s}")
    print("-" * 74)
    for name, n in N_QUERIES_PER_SCENARIO.items():
        matrix = matrix_cost_per_query * n
        judging = judge_per_query * n
        total = matrix + judging
        print(f"{name:<42s}{matrix:>12.2f}{judging:>10.2f}{total:>10.2f}")
    print("-" * 74)

    planned_n = N_QUERIES_PER_SCENARIO["planned (1000/bench x5 + 1000 domain)"]
    planned_total = (matrix_cost_per_query + judge_per_query) * planned_n
    cap = planned_total * 1.5  # 50% headroom for retries, re-sampling (Threat #3), longer outputs
    print(f"\nRECOMMENDED HARD CAP (planned x 1.5 for retries/re-sampling): ${cap:.2f}")
    print("Notes:")
    print("  * Excludes SageMaker/EC2 compute for the LLMRouter pipeline (separate line).")
    print("  * Anthropic prices are UNVERIFIED; if higher than assumed, matrix cost rises.")
    print("  * Re-sampling top-2 candidates on a subset (oracle-optimism, Threat #3) not")
    print("    included in the planned row — that is what the 1.5x headroom is for.")


if __name__ == "__main__":
    main()
