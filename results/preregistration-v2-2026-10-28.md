# Pre-registration: Delphi Trials v2 real-model run (written 2026-10-28, before any v2 call)

**Why.** On v1 (300 questions, 67 test) the cost-aware router was 74–89% cheaper than the best single model, with a quality loss under 1.5 points. But the design's H1 non-inferiority test could not be decided: the CI was too wide. v2 asks the same question on a larger, more varied set. Everything below is fixed before any v2 answer exists.

## Data
- **Generator:** `scripts/trials_v2.py`, `generate_v2(n_per_domain=250, seed=20261028, exclude=<all v1 prompts>, max_per_base=3)`. This gives 1,000 questions, 250 in each of four domains.
- **Domains:**
  - facts: 108 distinct multiple-choice facts
  - lore: 104 distinct short-answer questions
  - reckoning: arithmetic in three tiers
  - prophecy: 28 topics × 7 form constraints
- **No code questions.**
- **Disjoint from v1:** the near-duplicate filter is seeded with every v1 prompt.
- **Split:** `split_dataset(train=0.5, validation=0.2, seed=20261028)`, giving 491 / 202 / 307. The test share is larger than v1's (0.2) so that test can decide H1. The v1 power estimate was about 200 test questions.

## Models, generation, cost
- **Models:** same pool, generation parameters and prices as v1 (`results/models_realrun.yaml`).
- **Judge:** `us.openai.gpt-5.4`, charged at the assumed upper-bound price.
- **Judge prompt:** v1's, plus one clause: "and meets any form the request specifies (line count, length, rhyme, opening word, required image, riddle)." Prophecy is scored only by this judge.
- **Spend cap:** v1 ($1.1843) plus v2 together must stay within the original $10 cap. Projected v2 spend is about $4.

## Scoring
- **Strict:** the pipeline's own `score_native`.
- **Lenient:** the v1 lenient re-reading, unchanged.
- **Lenient is the primary scoring for H1**, decided after the v1 dry run showed strict measures answer format rather than correctness. Strict is reported alongside.

## Analyses (all on v2 alone; v1 is not pooled in)
1. **Phase 1:** per-model and per-domain quality, plus the oracle gate (T-STAT-1, threshold 3.0) on test and on the full set.
2. **Phase 2 quality router:** as `run_phase2_real.py`. Three families, selected on validation by capture ratio, scored once on test. H3 is capture ≥ 0.60.
3. **Phase 2 cost-aware router (the H1 test):**
   - Exactly as `run_phase2_costaware_real.py`: same families, same λ grid (0 to 100), same expected-cost term (mean train cost per model).
   - **Selection rule:** cheapest on validation among (family, λ) pairs with validation quality ≥ the best single model's validation quality. The best single model is chosen on train.
   - **H1:** `delphi.eval.stats.non_inferiority(margin=2.0, level=0.975, cost ≤ 0.70×)` on test, scored once.
4. **Audit, reported both ways:**
   - Questions every model misses under lenient scoring are listed as possible bad ground truth.
   - Results are given with and without them.
   - The primary H1 result includes them, so nothing is dropped post hoc.

## What would count as success
- **H1 PASS (lenient):** one-sided 97.5% lower bound of (router − best single) > −2 points, and router cost ≤ 0.70× best single, on the v2 test split.
- **Any other outcome is reported as it is.** That includes a fail, or a pass only under strict scoring.
