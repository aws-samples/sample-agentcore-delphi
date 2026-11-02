# Pre-registration: v3 real-model run, tiny to frontier pool (written 2026-10-28, before any v3 call)

**Why.** v2 showed that routing gains depend on the pool. With a 13× price spread and a cheap model that was also the best, there was nothing to save. v3 keeps the v2 questions fixed and widens the pool: six new models from 3–4B to frontier, a 110× price spread, and seven families.

## Decisions (the author, 2026-10-28)
- **Run-wide cap: $100.** It covers v1 + v2 + v3. Prior spend is $4.1311; v3 is estimated at $7–9.
- **Pool:** the v2 pool plus Gemma 3 4B, Nova Micro, Ministral 3B, Qwen3 32B, Gemma 3 27B, DeepSeek V3.2 and Claude Sonnet 5.5. That is ten arms (`results/models_v3pool.yaml`).
- **Claude Sonnet 5.5 runs at its provider-default sampling.** It rejects `temperature`. Every other arm keeps temperature 0 / topP 1 / maxTokens 1024. Claude gets maxTokens 1024 only. This is a disclosed exception to arm parity (T-INF-1): Claude's answers are sampled, not greedy.

## Data, scoring, judge (unchanged from v2)
- Same 1,000 v2 questions and the same split (491 / 202 / 307, seed 20261028).
- The v2 answers for Nova Lite, Nova Pro and Llama 3.3 70B are reused unchanged: same prompts and parameters, cached.
- Strict and lenient scoring as in v2. **Lenient is primary.**
- Judge `us.openai.gpt-5.4` with the v2 judge prompt. It sits outside every routed family.
- No other model was considered and dropped after seeing v3 answers. Models excluded *before* this run, from probes:
  - Llama 3.2 1B/3B: end of life.
  - Nemotron Nano 9B: runaway reasoning.
  - gpt-oss: OpenAI family, the same as the judge.

## Analyses (v3 alone; test scored once)
1. **Phase 1:** per-model and per-domain quality, cost per query, and the oracle gate (threshold 3.0) on test and on the full set.
2. **Phase 2 quality router:** three families selected on validation by capture. **H3** is capture ≥ 0.60. **H2** is `delphi.eval.stats.superiority_vs_all` (paired bootstrap, Holm-Bonferroni, α 0.05) on test. *H2 is pre-registered this time.*
3. **Phase 2 cost-aware router:** the same λ grid, expected-cost term and selection rule as v2. The rule is: cheapest on validation among pairs whose validation quality is ≥ the best single model's (best single chosen on train). **H1** is `non_inferiority(margin=2, level=0.975, cost ≤ 0.70×)` on test.
4. **Audit:**
   - List questions every model misses (lenient).
   - Report the primary results with the ambiguous "temple columns" template included, as in v2.
   - Give a sensitivity analysis without it.

## Success criteria (lenient primary)
- **H1 PASS:** lower bound > −2 and cost ≤ 0.70× the best single model.
- **H2 PASS:** superior to every single model after Holm correction.
- **H3 PASS:** capture ≥ 0.60.

Every outcome is reported as it falls.
