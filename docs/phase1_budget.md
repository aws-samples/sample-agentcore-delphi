# Phase 1 Token Budget & Power Analysis

**Status:** grounded in real Bedrock pricing (Price List API, publication 2026-07-07, pulled 2026-07-14, us-east-1, on-demand).
Regenerate with `python scripts/estimate_phase1_budget.py`.

This addresses two open items from the design review: the Phase 1 cost cap (review point #1) and the missing power analysis for sample sizing (review point #5).

## 1. What Phase 1 spends money on

The response matrix is `M[query][candidate] = quality score`. Building it requires **one full generation per (query, candidate) pair**, plus **one judge call per candidate answer** on the open-ended subset (T-STAT-5b). This is the single most expensive step in the whole project and the reason the oracle gate (T-STAT-1) is worth running before any of it on the full set.

## 2. Per-generation cost (real prices)

Assumes 600 input / 350 output tokens averaged across the benchmark mix (chain-of-thought answers pull output up vs. plain multiple-choice).

| Candidate | $/generation | Verified |
|---|---|---|
| nova-lite | $0.000120 | ✅ |
| llama-3.3-70b | $0.000684 | ✅ |
| deepseek-v3.1 | $0.000936 | ✅ |
| nova-pro | $0.001600 | ✅ |
| mistral-large | $0.006600 | ✅ |
| claude-haiku-4.5 | $0.001880 | ⚠️ unverified |
| claude-sonnet-4.6 | $0.007050 | ⚠️ unverified |

**One matrix row (7 candidates on one query): $0.01887.**

Price spread across the pool is **60×** (nova-lite vs. claude-sonnet blended). That comfortably clears the >10× bar the design sets for H1 cost headroom (§2.3) — cost dominance has room to be real, not trivial.

## 3. Budget by scenario

| Scenario | Matrix $ | +Judge $ | **Total $** |
|---|---|---|---|
| smoke (100/bench ×5 + domain) | 11.32 | 22.01 | **33.33** |
| **planned (1000/bench ×5 + 1000 domain)** | 113.22 | 220.08 | **333.30** |
| thorough (2000/bench ×5 + 2000 domain) | 226.44 | 440.16 | **666.60** |

The judge line is the larger half because the judge reads prompt + candidate answer and is invoked once per candidate answer. If native metrics (exact-match / pass@1 / F1) cover most benchmarks, the judge is only needed on the domain set + open-ended benchmarks, which would **cut the judge column by about 60%**: an estimate, not measured, since no script in this repository produces it. Budget the full amount; expect to spend less.

## 4. Recommended hard cap

**$499.95** for the planned scenario (planned total ×1.5, as `scripts/estimate_phase1_budget.py` prints it). The 1.5× headroom covers:
- generation retries on throttling/timeouts,
- top-2 re-sampling on a subset to bound oracle optimism (Threat #3),
- output-length overrun on code/math benchmarks.

**Excludes** SageMaker/EC2 compute for the LLMRouter data-generation and training pipeline — that is a separate compute line (a few GPU-hours for training the router families in Phase 2, negligible next to the API spend).

## 5. Power analysis (sample sizing for T-STAT-2)

The design's open question: what N does the held-out **test split** need so the paired bootstrap can detect a 2-point quality difference at 80% power? Using the normal approximation `N = ((z_α + z_β)·SD/effect)²`:

| Effect to detect | SD of per-query paired diff | **N required** (per comparison, 80% power, two-sided) |
|---|---|---|
| 3.0 pt | 10 | 88 |
| 2.0 pt | 10 | 197 |
| 3.0 pt | 15 | 197 |
| **2.0 pt** | **15** | **442** |
| 2.0 pt | 20 | 785 |

**Implication for the split.** To detect a 2-point difference at a realistic SD of ~15 quality points, you need **~442 test queries per comparison**. With the design's 20% test split, **1,000 queries per benchmark → 200 test queries** — *underpowered* for a 2-point effect at SD=15. Two ways out:

1. Raise `target_queries_per_benchmark` to ~2,200 so the 20% test split lands ~440 (pushes Phase 1 toward the "thorough" row; run `scripts/estimate_phase1_budget.py` for its printed cost).
2. Keep 1,000/benchmark but pool the test set **across benchmarks** for the H2 comparison (2,500 test queries total across 5 benchmarks), accepting that per-benchmark verdicts are underpowered while the aggregate verdict is not.

Recommendation: **option 2** for the headline verdict, and report per-benchmark results as directional. The design already commits to a sample-size calculation when a result is "underpowered" (§4 Phase 4 exit) — this is that calculation, done up front. SD should be re-estimated from the actual matrix once Phase 1 completes and this table re-read.

## 6. Snapshot hygiene

All figures pin to the 2026-07-07 price snapshot in `config/models.yaml`. Cost conclusions are re-priceable from `tokens_by_model()` output if a provider changes prices (Threat #4). Re-pull the snapshot and re-hash `config/models.yaml` at Phase 0 exit, and **confirm the two unverified Anthropic prices** — they were absent from the Price List API `model` enum on 2026-07-14 and are currently placeholders.
