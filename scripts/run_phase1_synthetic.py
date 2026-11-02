#!/usr/bin/env python3
"""Phase 1 offline dry-run over the synthetic "Delphi Trials" — end to end, no AWS.

Pipeline:  generate dataset -> split (+ contamination check) -> build response matrix
           over the synthetic council -> oracle gate report -> write artifacts.

This exercises every offline Phase 1 component with real ground-truth scoring, so when
the real dataset + Bedrock creds land, only the answer source changes (SyntheticCouncil
-> Converse) and the rest of the pipeline is already proven.

Usage:
  python scripts/run_phase1_synthetic.py                 # complementary pool -> gate PASSES
  python scripts/run_phase1_synthetic.py --mode nested   # nested pool -> PASSES at these defaults
  python scripts/run_phase1_synthetic.py --mode nested --n 40 --seed 8  # gate FAILS (pivot demo)
  python scripts/run_phase1_synthetic.py --n 120 --cap 5.0

Nested mode is unstable across (n, seed): at the defaults it passes the gate narrowly, and the
failing case the design gates on is the command above (T-EVD-U5-2 runs both).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, "src")

from delphi.config import load_pool  # noqa: E402 - after the src/ path shim
from delphi.data import synthetic  # noqa: E402 - after the src/ path shim
from delphi.data.split import split_dataset  # noqa: E402 - after the src/ path shim
from delphi.data.synthetic_provider import SyntheticCouncil  # noqa: E402 - after the src/ path shim
from delphi.eval.build_matrix import build_response_matrix  # noqa: E402 - after the src/ path shim
from delphi.eval.contamination import check_contamination  # noqa: E402 - after the src/ path shim
from delphi.eval.cost import arm_cost_per_query, tokens_by_model  # noqa: E402 - after the src/ path shim
from delphi.eval.gate import evaluate_gate, render  # noqa: E402 - after the src/ path shim

_CONFIG = pathlib.Path("config/models_synthetic.yaml")
_OUT = pathlib.Path("data/phase1_synthetic")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["complementary", "nested"], default="complementary")
    ap.add_argument("--n", type=int, default=80, help="queries per domain (5 domains)")
    ap.add_argument("--cap", type=float, default=None, help="cost cap USD (synthetic $)")
    ap.add_argument("--seed", type=int, default=20260714)
    args = ap.parse_args()

    load_pool.cache_clear()  # allow the synthetic config to be picked up
    pool = load_pool(_CONFIG)
    print(f"Council: {', '.join(pool.aliases)}  (judge: {pool.judge_alias})")
    print(f"Price spread: {pool.price_spread():.1f}x   mode: {args.mode}\n")

    # 1. generate + split
    ds = synthetic.generate(n_per_domain=args.n, seed=args.seed)
    split = split_dataset(ds, seed=args.seed)
    print(f"Dataset: {len(ds)} trials  ->  train {len(split.train)} / "
          f"val {len(split.validation)} / test {len(split.test)}")

    # 2. contamination guard (T-STAT-6) — hash-partition is disjoint, verify it
    contam = check_contamination(split.train.prompts(), split.test.prompts())
    print(f"Contamination (train vs test): exact={contam.exact_overlap} "
          f"near_dup={contam.near_dup_pairs} -> {'CLEAN' if contam.clean else 'DIRTY'}")
    if not contam.clean:
        raise SystemExit("contamination detected — aborting (T-STAT-6)")

    # 3. build the response matrix over the TEST split (the gate is evaluated on held-out)
    from delphi.eval.scoring import SyntheticJudge

    council = SyntheticCouncil(mode=args.mode)
    result = build_response_matrix(
        split.test, pool, council, SyntheticJudge(), cost_cap_usd=args.cap
    )
    print(f"\nMatrix: {len(split.test)} queries x {len(pool.aliases)} advisors  "
          f"({result.native_pairs} native-scored, {result.judged_pairs} judged)")
    print(f"Synthetic spend: ${result.total_cost:.4f}"
          + (f"  (cap ${args.cap:.2f})" if args.cap else ""))

    # 4. oracle gate
    report = evaluate_gate(result.matrix)
    print("\n" + render(report))

    # 5. per-arm cost + write artifacts
    by_arm = {a: arm_cost_per_query(pool, [u for u in result.usages if u.alias == a])
              for a in pool.aliases}

    _OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "mode": args.mode,
        "seed": args.seed,
        "dataset": {"name": ds.name, "n": len(ds), "hash": ds.content_hash()},
        "split_manifest": split.manifest(),
        "gate": {
            "gap": report.gap, "passes": report.passes,
            "oracle_mean": report.oracle_mean,
            "best_single": report.best_single_alias,
            "best_single_mean": report.best_single_mean,
            "per_model_mean": report.per_model_mean,
        },
        "cost": {"total_synthetic_usd": result.total_cost, "per_arm_per_query": by_arm,
                 "tokens_by_model": tokens_by_model(result.usages)},
    }
    (_OUT / f"gate_report_{args.mode}.json").write_text(json.dumps(manifest, indent=2))
    (_OUT / f"gate_report_{args.mode}.txt").write_text(render(report))
    print(f"\nArtifacts written to {_OUT}/gate_report_{args.mode}.{{json,txt}}")


if __name__ == "__main__":
    main()
