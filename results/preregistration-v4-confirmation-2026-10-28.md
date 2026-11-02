# Pre-registration: v4 confirmation run (written 2026-10-28, before any v4 call)

**Purpose.** On v3's test split, the learned router scored 97.95 against Claude Sonnet 5.5's 95.77 at 18% of Claude's cost. That comparison was chosen *after* seeing v3, so it is descriptive only. v4 tests it as a pre-registered claim on fresh questions, with the router frozen.

## Router: frozen, not retrained
- **Artifact:** a read-only copy of `artifacts/phase2_real_v3_lenient/`, frozen in `data/frozen_router_v4/`. Not published with the sample; `scripts/run_confirm_v4_real.py` records its SHA-256 on first use.
- **Router:** knn family, hashing-512 encoder, artifact_hash `7de415bebe18f5d4e54a656098789e5f`. It was trained on the v3 train split under lenient scores and selected on the v3 validation split.
- **File SHA-256:**
  - manifest `2eceb5f9…a99f`
  - scalars `a6dcdc47…d637`
  - weights `895e3797…bac4`
- No retraining, family change, λ or threshold is applied. The router is the plain argmax of predicted quality, exactly as reported in v3.

## Data
- **Source:** `load_trials("v4")`, which calls `generate_v2(n_per_domain=130, seed=20261029, exclude=<all v1 and v2 prompts>, max_per_base=3)`.
- **Size:** 520 questions, 130 per domain. Content hash prefix `a477d2f7f8270380`.
- **All 520 are test.** None is used for training or selection.
- **Disjoint from v2:** zero exact and zero near-duplicate overlap.
- **Why 130 per domain:** it is lore's remaining capacity, and it keeps the v3 domain balance.
- **Same distribution, new items:** the questions come from the same templates and fact bank as v2. Lore and facts questions are new phrasings of facts v2 also asked. This tests generalisation to new questions of the same kind, not to a new workload.

## Arms, scoring, judge (unchanged from v3)
- All ten v3 arms answer every question, with the same parameters. Claude runs at provider-default sampling, as in v3.
- Lenient scoring is primary; strict is reported.
- The judge is `us.openai.gpt-5.4` with the v2 judge prompt.
- **Router cost per query** is the Bedrock-reported cost of the answer from the model the router chose for that question. The judge is excluded, as in all earlier runs.

## Primary hypothesis (H1-vs-frontier), lenient
The router is **non-inferior to Claude Sonnet 5.5 within δ = 2 quality points, at ≤ 0.70× Claude's cost per query**.

Test: `delphi.eval.stats.non_inferiority(router, claude, margin=2.0, level=0.975, cost_ceiling_ratio=0.70)`. That is a one-sided 97.5% paired-bootstrap lower bound > −2, with 10,000 resamples and seed 0.

**PASS** if both parts hold. Anything else is a FAIL and is reported as such.

## Secondary (reported; no claim depends on them)
1. Router vs Claude superiority: paired bootstrap 95% CI lower bound > 0.
2. Router vs Gemma 3 27B and vs DeepSeek V3.2: mean difference, CI and cost ratio.
3. H2: `superiority_vs_all` over all ten arms (Holm, α 0.05).
4. All of the above under strict scoring.
5. Per-domain quality and routing mix.
6. Audit:
   - questions every model misses;
   - primary test repeated without the ambiguous "temple columns" template, as sensitivity only.

## Budget
- **Run-wide cap: $100.** Prior spend is $13.6427.
- **Projected v4 spend: about $6.** Most of it is the judge: 130 prophecy questions × 10 arms.
- A dry run of 3 questions per domain comes first, to check parsing only. Its answers are part of the 520 and are kept.
