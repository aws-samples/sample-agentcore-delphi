# Phase 1 Offline Pipeline

Builds the response matrix and runs the oracle gate (T-STAT-1) — the go/no-go on H2. Everything here is offline batch compute (design D5); it never touches AgentCore. The synthetic dry-run proves the pipeline end to end with **no AWS**, so the real run is a one-line swap of the answer source.

## Stages

```
generate ─▶ split (+contamination) ─▶ build response matrix ─▶ oracle gate ─▶ artifacts
 data/synthetic  data/split           eval/build_matrix        eval/gate       data/phase1_*
```

1. **Generate** (`data/synthetic.py`) — the "Delphi Trials": petitioners pose trials across five domains (prophecy, facts, reckoning, craft, lore) with **real ground truth**. A near-dup filter guarantees no internal duplicates, so any split is contamination-clean by construction.
2. **Split** (`data/split.py`) — deterministic hash-partition into train/val/test (60/20/20). Stable across runs, independent of order (T-INF-4).
3. **Contamination check** (`eval/contamination.py`) — exact + MinHash near-dup guard across splits (T-STAT-6). Belt-and-suspenders on the disjoint partition.
4. **Build matrix** (`eval/build_matrix.py`) — for every (query, candidate): get answer, score it, record token cost. Enforces a dollar cap (`BudgetExceeded` if crossed). Scored natively where ground truth exists (exact-match / numeric / pass@1 / F1), by the judge for open-ended (T-STAT-5).
5. **Oracle gate** (`eval/gate.py`) — compute oracle gap = mean(oracle) − mean(best single). ≥ 3.0 → PASS (pursue H2); < 3.0 → FAIL (pivot to H1 or enlarge pool).

## The synthetic council

Six fictional advisors (`config/models_synthetic.yaml`), each with a competence profile over domains:

| Advisor | Strong domain | Role |
|---|---|---|
| pythia | prophecy | the priestess |
| apollo | facts | god of knowledge |
| hermes | craft (code) | trickster/messenger |
| athena | reckoning (math) | strategy & reason |
| calliope | lore | muse of epic |
| sibyl | — (generalist) | decent everywhere, best nowhere |

Zeus is the out-of-pool judge (T-STAT-5). Prices span 60× so cost accounting is meaningfully exercised.

Two modes (`--mode`):
- **complementary** (default) — each advisor best in one domain → non-nested error sets → **gate PASSES** (gap 27.55 at the defaults). The scenario where a router can win on quality.
- **nested** — apollo near-perfect everywhere. At the defaults the gate still **PASSES** (gap 3.53): nested mode is unstable across `(n, seed)`, and that instability is itself a finding worth publishing, because a gate verdict on a small synthetic pool can turn on the sample drawn. At `--mode nested --n 40 --seed 8` the gate **FAILS** (gap 0.00), which demonstrates the exact kill/pivot the design gates on: no router can beat a single model that dominates <!-- claim-ok: method statement about the synthetic nested fixture -->.

## Running

```bash
python scripts/run_phase1_synthetic.py                 # complementary -> PASS
python scripts/run_phase1_synthetic.py --mode nested   # nested -> PASS at the defaults (gap 3.53)
python scripts/run_phase1_synthetic.py --mode nested --n 40 --seed 8   # nested -> FAIL (gap 0.00)
python scripts/run_phase1_synthetic.py --n 100 --cap 5.0 --seed 123
```

Writes `data/phase1_synthetic/gate_report_<mode>.{json,txt}`: dataset + split hashes (replayable manifest), gate verdict, per-model means, and per-arm cost.

## Swapping in the real run (when creds + dataset land)

Only step 4's **answer source** changes:
- `SyntheticCouncil.answer(advisor, query)` → a real `agent.bedrock.BedrockConverseClient.converse(model_id, prompt, params)` call, using Bedrock's reported token usage for exact cost (T-INF-2).
- `config/models_synthetic.yaml` → `config/models.yaml` (the real pinned-price pool).
- The dataset generator → the real benchmark loaders + the frozen domain set (`workload.yaml`).

Everything downstream — split, contamination, scoring, matrix, gate, cost, artifacts — is unchanged and already proven by the synthetic run. The `pass_at_1` scorer refuses to execute model output by default: candidate code runs only in a child process (`eval/runner.py`, `eval/cell_harness.py`) with an empty environment, allowlisted builtins and a per-cell timeout, and model output needs an explicit opt-in (`allow_exec=True` or `DELPHI_ALLOW_UNSANDBOXED_EXEC=1`). That child is **not a sandbox**; a real sandbox (seccomp or a microVM) is still required before real model output is scored (see `eval/scoring.py`). The pass@1 score's integrity holds against incorrect code, not adversarial code: the child that runs a candidate also reports that candidate's result, so code written to subvert the harness could report a pass it did not earn.
