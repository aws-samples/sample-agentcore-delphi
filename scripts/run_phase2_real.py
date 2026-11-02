#!/usr/bin/env python3
"""Phase 2 on real Bedrock models: train router families on the real matrix, select on
validation, and score once on test - for both scorings Phase 1 reports.

Reads the Phase 1 answer cache (data/real_run/cells.jsonl); a cell missing from it is paid
for under the same cap. The selected router is saved, reloaded as the request-path
LearnedRouter, and routes the test split, exactly as scripts/run_phase2_synthetic.py does.
Router cost is the Bedrock-reported cost of the call the router's chosen model made on each
test query, so H1 can be read against each single model's cost on the same queries.

Usage:
  python scripts/run_phase2_real.py
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, "src")

import numpy as np  # noqa: E402 - after the src/ path shim

from delphi.agent.bedrock import BedrockConverseClient  # noqa: E402 - after the src/ path shim
from delphi.config import load_pool  # noqa: E402 - after the src/ path shim
from delphi.eval.matrix import H3_CAPTURE_THRESHOLD, capture_ratio, oracle_gap  # noqa: E402 - after the src/ path shim
from real_source import (  # noqa: E402 - after the src/ path shim; scripts/ is on the path
    AnswerCache,
    ConverseJudge,
    RealCouncil,
    build_real_matrix,
    load_judge,
    load_trials,
    pool_config,
    provider_default_aliases,
    prior_spend,
)
from delphi.train.artifact_store import load_learned_router, save_artifact  # noqa: E402 - after the src/ path shim
from delphi.train.harness import train_and_select  # noqa: E402 - after the src/ path shim

REGION = "us-west-2"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--seed", type=int, default=20260714)
    ap.add_argument("--dim", type=int, default=512)
    ap.add_argument("--cap", type=float, default=10.0)
    ap.add_argument("--trials", choices=["v1", "v2", "v3"], default="v1")
    args = ap.parse_args()

    load_pool.cache_clear()
    pool = load_pool(pool_config(args.trials))
    client = BedrockConverseClient(REGION)
    council = RealCouncil(client, pool,
                          provider_default=provider_default_aliases(pool_config(args.trials)))
    ds, split, out_dir, judge_prompt = load_trials(args.trials)
    judge = ConverseJudge(client, load_judge(pool_config(args.trials)), prompt=judge_prompt)
    cache = AnswerCache(out_dir / "cells.jsonl")
    args.cap -= prior_spend(args.trials)
    print(f"dataset={len(ds)}  train/val/test={len(split.train)}/{len(split.validation)}/"
          f"{len(split.test)}   run spend so far ${cache.spend:.4f}")

    out = {}
    for scoring in ("strict", "lenient"):
        def matrix(part, scoring=scoring):
            return build_real_matrix(part, pool, council, judge, cache, cost_cap_usd=args.cap,
                                     scoring=scoring).matrix

        train_m, val_m, test_m = matrix(split.train), matrix(split.validation), matrix(split.test)
        print(f"\n##### SCORING: {scoring.upper()} #####")
        print(f"oracle gap (train): {oracle_gap(train_m):.2f}   (test): {oracle_gap(test_m):.2f}")

        result = train_and_select(train_m, split.train.prompts(), val_m,
                                  split.validation.prompts(), encoder_dim=args.dim)
        print("Router family selection (validation):\n" + result.table())
        suffix = "" if args.trials == "v1" else "_" + args.trials
        art_dir = f"artifacts/phase2_real{suffix}_{scoring}"
        art = save_artifact(result, art_dir)
        learned = load_learned_router(art_dir, pool)
        print(f"Winner: {result.winner.family}  artifact {art.artifact_hash} -> {art_dir}/")

        col = {a: j for j, a in enumerate(test_m.aliases)}
        chosen = [learned.predict(q.prompt).chosen_alias for q in split.test.queries]
        routed = test_m.policy_per_query(np.array([col[a] for a in chosen]))
        cap = capture_ratio(routed, test_m)

        def cost_of(alias, q):
            return cache.cells[(q.id, alias)]["cost_usd"]

        n = len(split.test)
        single_cost = {a: sum(cost_of(a, q) for q in split.test.queries) / n
                       for a in pool.aliases}
        router_cost = sum(cost_of(a, q) for a, q in zip(chosen, split.test.queries)) / n
        oracle_idx = test_m.scores.argmax(axis=1)
        oracle_cost = sum(cost_of(test_m.aliases[j], q)
                          for j, q in zip(oracle_idx, split.test.queries)) / n
        bs = test_m.aliases[test_m.best_single_index()]
        single_q = {a: float(test_m.scores[:, j].mean()) for j, a in enumerate(test_m.aliases)}
        mix = Counter(chosen)
        by_domain = Counter((q.domain, a) for q, a in zip(split.test.queries, chosen))

        print("=" * 64)
        print("HELD-OUT TEST (selected router, scored once):")
        print(f"  router              : {routed.mean():6.2f}   ${router_cost:.6f}/query")
        for a in pool.aliases:
            tag = "  <- best single" if a == bs else ""
            print(f"  {a:20s}: {single_q[a]:6.2f}   ${single_cost[a]:.6f}/query{tag}")
        print(f"  oracle (ceiling)    : {test_m.oracle_per_query().mean():6.2f}   "
              f"${oracle_cost:.6f}/query (argmax, first on ties)")
        print(f"  random (floor)      : {test_m.random_per_query().mean():6.2f}")
        print(f"  CAPTURE RATIO       : {cap:.3f}   (H3 target >= {H3_CAPTURE_THRESHOLD:.2f}) "
              f"-> {'PASS' if cap >= H3_CAPTURE_THRESHOLD else 'MISS'}")
        print(f"  router vs best single: {routed.mean() - single_q[bs]:+.2f} quality, cost "
              f"{100 * (router_cost / single_cost[bs] - 1):+.1f}%")
        print(f"  router mix: {dict(mix)}")
        print(f"  router mix by domain: {dict(sorted(by_domain.items()))}")
        print("=" * 64)
        out[scoring] = {
            "winner": result.winner.family, "artifact_hash": art.artifact_hash,
            "families": [{"family": r.family, "val_capture": r.capture,
                          "val_quality": r.val_mean_quality, "p90_ms": r.p90_latency_ms}
                         for r in result.all_results],
            "test": {"router_quality": float(routed.mean()), "router_cost": router_cost,
                     "single_quality": single_q, "single_cost": single_cost,
                     "best_single": bs, "oracle_quality": float(test_m.oracle_per_query().mean()),
                     "oracle_cost": oracle_cost,
                     "random_quality": float(test_m.random_per_query().mean()),
                     "capture": cap, "gap": oracle_gap(test_m), "mix": dict(mix),
                     "mix_by_domain": {f"{d}/{a}": k for (d, a), k in by_domain.items()}},
        }

    out["run_spend_usd"] = cache.spend
    (out_dir / "phase2_real.json").write_text(json.dumps(out, indent=2))
    print(f"\nrun spend ${cache.spend:.4f} of ${args.cap:.2f}; written {out_dir}/phase2_real.json")


if __name__ == "__main__":
    main()
