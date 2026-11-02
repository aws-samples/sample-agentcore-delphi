#!/usr/bin/env python3
"""Phase 1 on real Bedrock models: the Delphi Trials (minus craft) through Converse.

Pipeline:  generate (synthetic seed) -> drop craft -> split (+ contamination check) ->
           real answers for train/val/test -> oracle gate on test (as the synthetic run
           does) and on the full set -> per-domain quality and per-model cost.

Every paid cell is cached in data/real_run/cells.jsonl; Phase 2 reads the same cache, and
the dollar cap bounds the cache's total spend, judge included.

Usage (needs Bedrock access in us-west-2; AWS_PROFILE selects the account):
  python scripts/run_phase1_real.py --dry-run 5      # 5 queries per domain, show raw answers
  python scripts/run_phase1_real.py                  # full set, gate report
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, "src")

from delphi.agent.bedrock import BedrockConverseClient  # noqa: E402 - after the src/ path shim
from delphi.config import load_pool  # noqa: E402 - after the src/ path shim
from delphi.data.schema import Dataset  # noqa: E402 - after the src/ path shim
from delphi.eval.contamination import check_contamination  # noqa: E402 - after the src/ path shim
from delphi.eval.cost import arm_cost_per_query, tokens_by_model  # noqa: E402 - after the src/ path shim
from delphi.eval.gate import evaluate_gate, render  # noqa: E402 - after the src/ path shim
from delphi.eval.matrix import ResponseMatrix  # noqa: E402 - after the src/ path shim
from real_source import (  # noqa: E402 - after the src/ path shim; scripts/ is on the path
    REAL_DOMAINS,
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

REGION = "us-west-2"


def _per_domain(m: ResponseMatrix, ds) -> dict[str, dict[str, float]]:
    acc: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for q, row in zip(ds.queries, m.scores):
        for a, sc in zip(m.aliases, row):
            acc[q.domain][a].append(float(sc))
        acc[q.domain]["ORACLE"].append(float(row.max()))
    return {d: {a: sum(v) / len(v) for a, v in acc[d].items()} for d in REAL_DOMAINS if d in acc}


def _domain_table(table: dict[str, dict[str, float]], aliases) -> str:
    cols = list(aliases) + ["ORACLE"]
    lines = [f"{'domain':10s}" + "".join(f"{c:>15s}" for c in cols)]
    for d, row in table.items():
        lines.append(f"{d:10s}" + "".join(f"{row[c]:15.1f}" for c in cols))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80, help="queries per domain before filtering")
    ap.add_argument("--seed", type=int, default=20260714)
    ap.add_argument("--cap", type=float, default=10.0, help="hard USD cap for the whole run")
    ap.add_argument("--dry-run", type=int, default=0, metavar="K",
                    help="only K queries per domain, and print raw answers with scores")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--trials", choices=["v1", "v2", "v3"], default="v1")
    args = ap.parse_args()

    load_pool.cache_clear()
    pool = load_pool(pool_config(args.trials))
    judge_spec = load_judge(pool_config(args.trials))
    client = BedrockConverseClient(REGION)
    council = RealCouncil(client, pool,
                          provider_default=provider_default_aliases(pool_config(args.trials)))
    ds, split, out, judge_prompt = load_trials(args.trials)
    judge = ConverseJudge(client, judge_spec, prompt=judge_prompt)
    cache = AnswerCache(out / "cells.jsonl")
    prior = prior_spend(args.trials)
    args.cap -= prior  # the cap bounds the whole run, earlier datasets included
    print(f"Pool: {', '.join(pool.ids)}\nJudge: {judge_spec.id}   trials {args.trials}   "
          f"cap left ${args.cap:.4f} (after ${prior:.4f} earlier)   this set's spend so far "
          f"${cache.spend:.4f}\n")

    print(f"Dataset: {len(ds)} trials ({', '.join(REAL_DOMAINS)}) -> train {len(split.train)} "
          f"/ val {len(split.validation)} / test {len(split.test)}")
    contam = check_contamination(split.train.prompts(), split.test.prompts())
    print(f"Contamination (train vs test): exact={contam.exact_overlap} "
          f"near_dup={contam.near_dup_pairs} -> {'CLEAN' if contam.clean else 'DIRTY'}")
    if not contam.clean:
        raise SystemExit("contamination detected - aborting (T-STAT-6)")

    if args.dry_run:
        picked, seen = [], defaultdict(int)
        for q in split.test.queries:
            if seen[q.domain] < args.dry_run:
                picked.append(q)
                seen[q.domain] += 1
        sub = Dataset(f"{ds.name}:dry{args.dry_run}", tuple(picked))
        build = build_real_matrix(sub, pool, council, judge, cache, cost_cap_usd=args.cap,
                                  workers=args.workers)
        lenient = build_real_matrix(sub, pool, council, judge, cache, cost_cap_usd=args.cap,
                                    scoring="lenient").matrix
        for q in sub.queries:
            print("\n" + "-" * 78 + f"\n[{q.domain}] {q.prompt}\n  expected: {q.answer}")
            for a in pool.aliases:
                c = build.cells[(q.id, a)]
                text = c["answer"].strip().replace("\n", " / ")
                print(f"  {a:14s} score {c['score']:5.1f}  {text[:160]}")
        for name, m in (("strict", build.matrix), ("lenient", lenient)):
            print(f"\n{name}:\n" + _domain_table(_per_domain(m, sub), pool.aliases))
        print(f"\nNew calls {build.new_calls}; run spend ${build.run_spend:.4f}")
        return

    for name, part in (("train", split.train), ("validation", split.validation),
                       ("test", split.test)):
        b = build_real_matrix(part, pool, council, judge, cache, cost_cap_usd=args.cap,
                              workers=args.workers)
        print(f"  {name:10s} {len(part):4d} queries  new calls {b.new_calls:4d}  "
              f"run spend ${cache.spend:.4f}")

    def gate_json(r):
        return {"gap": r.gap, "passes": r.passes, "oracle_mean": r.oracle_mean,
                "best_single": r.best_single_alias, "best_single_mean": r.best_single_mean,
                "random_mean": r.random_mean, "per_model_mean": r.per_model_mean}

    manifest = {
        "trials": args.trials, "prior_spend_usd": prior, "domains": list(REAL_DOMAINS),
        "dataset": {"name": ds.name, "n": len(ds), "hash": ds.content_hash()},
        "split_manifest": split.manifest(),
        "pool": list(pool.ids), "judge": judge_spec.id, "scoring": {},
    }
    for scoring in ("strict", "lenient"):
        test = build_real_matrix(split.test, pool, council, judge, cache, cost_cap_usd=args.cap,
                                 scoring=scoring).matrix
        full = build_real_matrix(ds, pool, council, judge, cache, cost_cap_usd=args.cap,
                                 scoring=scoring).matrix
        rep_test, rep_full = evaluate_gate(test), evaluate_gate(full)
        print(f"\n##### SCORING: {scoring.upper()} #####")
        print("\nTEST split (the gate the synthetic run reports):\n" + render(rep_test))
        print("\nTest quality by domain:\n"
              + _domain_table(_per_domain(test, split.test), pool.aliases))
        print("\nFULL set (train+val+test):\n" + render(rep_full))
        print("\nFull-set quality by domain:\n"
              + _domain_table(_per_domain(full, ds), pool.aliases))
        manifest["scoring"][scoring] = {
            "gate_test": gate_json(rep_test), "gate_full": gate_json(rep_full),
            "by_domain_test": _per_domain(test, split.test),
            "by_domain_full": _per_domain(full, ds),
        }

    full_build = build_real_matrix(ds, pool, council, judge, cache, cost_cap_usd=args.cap)
    by_arm = {a: arm_cost_per_query(pool, full_build.usages(a)) for a in pool.aliases}
    judge_usd = sum(c.get("judge_cost_usd", 0.0) for c in cache.cells.values())
    print("\nCost per query (full set, Bedrock-reported tokens):")
    for a, v in by_arm.items():
        print(f"  {a:14s} ${v:.6f}")
    print(f"Judge spend ${judge_usd:.4f} (at the assumed upper-bound price); "
          f"total run spend ${cache.spend:.4f} of ${args.cap:.2f}")
    manifest["cost"] = {"per_arm_per_query": by_arm,
                        "tokens_by_model": tokens_by_model(full_build.usages()),
                        "judge_usd": judge_usd, "run_spend_usd": cache.spend}
    (out / "phase1_real.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nArtifacts written to {out}/phase1_real.json")


if __name__ == "__main__":
    main()
