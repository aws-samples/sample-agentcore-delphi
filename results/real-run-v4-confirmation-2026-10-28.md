# v4 confirmation: frozen router vs Claude Sonnet 5.5 — 2026-10-28

- **Pre-registration:** `results/preregistration-v4-confirmation-2026-10-28.md`, written before any v4 call.
- **Questions:** 520 fresh ones, 130 per domain, all test. They come from the same generator, with zero exact or near-duplicate overlap with v1 or v2.
- **Router:** the v3 lenient router, frozen. knn, hash `7de415be…`, with its SHA-256 checked before and after the run.
- **Spend:** v4 cost **$6.17**. The project total is **$19.81 of the $100 cap**.

## Primary result (pre-registered, lenient): **PASS**

| | quality | $ / query |
|---|---|---|
| Claude Sonnet 5.5 | 95.48 | $0.001754 |
| **frozen router** | **96.63** | **$0.000346** |
| difference | **+1.15** | **80.3% cheaper (ratio 0.197)** |

The non-inferiority test (δ = 2, one-sided 97.5%) gives a **lower bound of −0.54 > −2**, with a cost ratio of **0.197 ≤ 0.70**. That is a **PASS**.

The audit matches: no question was missed by every model. Without the ambiguous "temple" template (n = 513) the lower bound is −1.23 with cost ratio 0.202, still a PASS.

**What the pre-registered claim now supports:** *on held-out questions, the learned router matched Claude Sonnet 5.5's quality at one-fifth of its cost.*

## Secondary results (reported; no claim depends on them)

- **Better than Claude? Not shown.** The difference is +1.15, but the 95% CI runs from −0.54 to +2.90.
- **vs Gemma 3 27B:** −0.27 (CI −1.56 to +0.96), and the router costs **9.2×** more ($0.000346 vs $0.000038). On these questions the cheap open-weight model alone is as good as the router and much cheaper. The v3 finding replicates.
- **vs DeepSeek V3.2:** +0.44 (CI −1.13 to +2.00), at 2.8× DeepSeek's cost.
- **H2 (superior to all ten): FAIL.**
- **Strict scoring:** non-inferior to Claude, lower bound −0.98 with cost ratio 0.197. But strict again mostly measures answer format: the router scores 0 on arithmetic because Nova Lite shows its working.

**Per-model quality (lenient):**
- Gemma 27B 96.90
- DeepSeek 96.19
- Claude 95.48
- Qwen3 32B 94.60
- Nova Lite 93.00
- Llama 70B 92.21
- Nova Pro 91.12
- Nova Micro 88.94
- Ministral 3B 86.04
- Gemma 4B 83.73
- Oracle 99.65

## Where the frozen router sent the 520 questions

| domain | router | Claude | routed to |
|---|---|---|---|
| prophecy | **93.1** | 89.6 | DeepSeek 54, Gemma 27B 47, Claude 29 |
| facts | 99.2 | 99.2 | Nova Lite 117, Nova Pro 13 |
| reckoning | **100.0** | 95.4 | Nova Lite 130 |
| lore | 94.2 | **97.7** | mostly Nova Lite (66), Nova Pro (26), Nova Micro (20) |

This is the same behaviour as in v3, on new questions:
- Large models get the creative, constrained writing, where they help.
- The $0.00002 model gets arithmetic and facts.
- The router gives back a few points on lore by under-spending there.

## For the blog

- **Can now be stated as a pre-registered, held-out result:** the router matched the frontier model at 80% lower cost (non-inferiority, δ = 2).
- **Must be stated next to it:**
  - The router was not shown to be *better* than Claude.
  - A single cheap open-weight model (Gemma 3 27B) did as well as the router at a ninth of its cost.
  - The questions are a small synthetic workload.
  - Claude ran at its provider-default sampling while the other models ran at temperature 0.
  - The judge's price is an assumed upper bound.

## Files

- `results/v4/`: `confirm_v4.json`, `confirm_v4_dry.json`. The 5,200-answer cache is not published (third-party model outputs); the script regenerates it.
- `results/v4_confirm.log`, `results/v4_dry_run.log`.
- The frozen router is not published: it was trained on third-party model outputs. Freeze your own copy of `artifacts/phase2_real_v3_lenient/` in `data/frozen_router_v4/`.
- `scripts/run_confirm_v4_real.py`.

Nothing was committed.
