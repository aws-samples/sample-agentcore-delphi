#!/usr/bin/env python3
"""Cost-aware routing on the real matrix (H1): same quality as the best single model, for less.

Offline only: every number comes from the Phase 1 answer cache, and a missing cell is an
error, never a call. The three router families are the Phase 2 ones, trained the same way
to predict per-candidate quality. What changes is the decision: instead of argmax predicted
quality, each query goes to

    argmax_j  predicted_quality_j - lam * mean_cost_j / 1e-4

with mean_cost_j each model's mean cost per query on TRAIN (a router cannot see an answer's
length before the call), so lam is quality points traded per $0.0001 of expected cost.

Selection rule, fixed before test is touched: over every (family, lam), keep those whose
VALIDATION quality is at least the best single model's validation quality (stricter than
H1's margin, on purpose) (the best single
model is chosen on train), and take the cheapest on validation; ties go to the higher
quality. If none qualifies, the run says so. Test is scored once, with the selected pair, and
H1 is the design's own test: delphi.eval.stats.non_inferiority (margin 2, cost <= 0.70x).

Usage:
  python scripts/run_phase2_costaware_real.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, "src")

import numpy as np  # noqa: E402 - after the src/ path shim

from delphi.config import load_pool  # noqa: E402 - after the src/ path shim
from delphi.eval.stats import non_inferiority  # noqa: E402 - after the src/ path shim
from delphi.router.features import HashingEncoder  # noqa: E402 - after the src/ path shim
from delphi.train.routers import FAMILIES  # noqa: E402 - after the src/ path shim
from real_source import AnswerCache, cell_score, load_trials, pool_config  # noqa: E402 - scripts/ is on the path

_CACHE = pathlib.Path("data/real_run/cells.jsonl")  # written by run_phase1_real.py
_OUT = pathlib.Path("results/phase2_costaware_real.json")
LAMBDAS = (0.0, 0.25, 0.5, 1, 2, 3, 5, 7.5, 10, 15, 20, 30, 50, 100)
BOOT = 10_000


def _tables(part, aliases, cache, scoring):
    missing = [(q.id, a) for q in part.queries for a in aliases if (q.id, a) not in cache.cells]
    if missing:
        raise SystemExit(f"{len(missing)} cells missing from the cache; run Phase 1 first")
    S = np.array([[cell_score(cache.cells[(q.id, a)], q, scoring) for a in aliases]
                  for q in part.queries])
    C = np.array([[cache.cells[(q.id, a)]["cost_usd"] for a in aliases] for q in part.queries])
    return S, C


def _pick(pred, mean_cost, lam):
    return (pred - lam * mean_cost[None, :] / 1e-4).argmax(axis=1)


def _take(M, idx):
    return M[np.arange(len(M)), idx]


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", choices=["v1", "v2", "v3"], default="v1")
    args = ap.parse_args()
    ds, split, out_dir, _ = load_trials(args.trials)
    cache_path = _CACHE if args.trials == "v1" else out_dir / "cells.jsonl"
    out_path = _OUT if args.trials == "v1" else out_dir / "phase2_costaware_real.json"
    load_pool.cache_clear()
    pool = load_pool(pool_config(args.trials))
    aliases = pool.aliases
    cache = AnswerCache(cache_path)
    enc = HashingEncoder(dim=512)
    Xtr, Xva, Xte = (enc.matrix(p.prompts()) for p in (split.train, split.validation, split.test))
    domains_te = [q.domain for q in split.test.queries]
    out = {"rule": "cheapest on validation with val quality >= best-single val quality",
           "lambdas": list(LAMBDAS), "scoring": {}}

    for scoring in ("strict", "lenient"):
        Str, Ctr = _tables(split.train, aliases, cache, scoring)
        Sva, Cva = _tables(split.validation, aliases, cache, scoring)
        Ste, Cte = _tables(split.test, aliases, cache, scoring)
        mean_cost = Ctr.mean(axis=0)
        bs = int(Str.mean(axis=0).argmax())          # best single, chosen on train
        bar_q, bar_c = Sva[:, bs].mean(), Cva[:, bs].mean()

        print(f"\n##### SCORING: {scoring.upper()} #####")
        print(f"best single (train): {aliases[bs]}   val quality {bar_q:.2f}   "
              f"val cost ${bar_c:.6f}/query")
        print("expected cost per query (train): "
              + ", ".join(f"{a} ${c:.6f}" for a, c in zip(aliases, mean_cost)))

        rows, routers = [], {}
        for fam, cls in FAMILIES.items():
            routers[fam] = cls.fit(Xtr, Str, aliases)
            pva = routers[fam].predict_quality(Xva)
            for lam in LAMBDAS:
                ch = _pick(pva, mean_cost, lam)
                rows.append({"family": fam, "lam": lam, "val_q": float(_take(Sva, ch).mean()),
                             "val_c": float(_take(Cva, ch).mean())})
        ok = [r for r in rows if r["val_q"] >= bar_q - 1e-9]
        print(f"\nvalidation sweep: {len(rows)} (family, lam) pairs, {len(ok)} meet the bar")
        print("  family     lam    val_q    val_cost   vs best single")
        for r in sorted(rows, key=lambda r: (r["family"], r["lam"])):
            mark = "ok" if r in ok else "  "
            print(f"  {r['family']:9s} {r['lam']:5g}  {r['val_q']:6.2f}  ${r['val_c']:.6f}  "
                  f"{100 * (r['val_c'] / bar_c - 1):+6.1f}%  {mark}")
        if not ok:
            print("NO (family, lam) matches the best single model's validation quality.")
            out["scoring"][scoring] = {"selected": None}
            continue
        sel = min(ok, key=lambda r: (r["val_c"], -r["val_q"]))

        ch = _pick(routers[sel["family"]].predict_quality(Xte), mean_cost, sel["lam"])
        rq, rc = _take(Ste, ch), _take(Cte, ch)
        bq, bc = Ste[:, bs], Cte[:, bs]
        tie = Ste == Ste.max(axis=1, keepdims=True)
        cheap = np.where(tie, Cte, np.inf).argmin(axis=1)   # cheapest-correct oracle
        rng = np.random.default_rng(0)
        idx = rng.integers(0, len(rq), size=(BOOT, len(rq)))
        dq = (rq[idx] - bq[idx]).mean(axis=1)
        save = 1 - rc[idx].mean(axis=1) / bc[idx].mean(axis=1)
        mix = {a: int((ch == j).sum()) for j, a in enumerate(aliases)}
        by_dom = {}
        for d, j in zip(domains_te, ch):
            by_dom.setdefault(d, {a: 0 for a in aliases})[aliases[j]] += 1

        print("\n" + "=" * 66)
        print(f"SELECTED on validation: {sel['family']}, lam={sel['lam']:g}")
        print("HELD-OUT TEST (scored once):")
        for j, a in enumerate(aliases):
            tag = "  <- best single" if j == bs else ""
            print(f"  {a:22s} {Ste[:, j].mean():6.2f}   ${Cte[:, j].mean():.6f}/query{tag}")
        print(f"  {'cost-aware router':22s} {rq.mean():6.2f}   ${rc.mean():.6f}/query")
        print(f"  {'cheapest-correct oracle':22s} {_take(Ste, cheap).mean():6.2f}   "
              f"${_take(Cte, cheap).mean():.6f}/query  (ceiling)")
        print(f"  quality vs best single: {rq.mean() - bq.mean():+.2f}  "
              f"(95% CI {np.percentile(dq, 2.5):+.2f} to {np.percentile(dq, 97.5):+.2f})")
        print(f"  cost saving vs best single: {100 * (1 - rc.mean() / bc.mean()):.1f}%  "
              f"(95% CI {100 * np.percentile(save, 2.5):.1f}% to "
              f"{100 * np.percentile(save, 97.5):.1f}%)")
        ni = non_inferiority(rq, bq, router_cost_per_query=float(rc.mean()),
                             best_single_cost_per_query=float(bc.mean()))
        h1 = ni.passes
        print(f"  H1 (design: non-inferior within {ni.margin:g} pts, one-sided lower bound "
              f"{ni.ci.lower:+.2f}; cost ratio {ni.cost_ratio:.3f} <= 0.70): "
              f"{'PASS' if h1 else 'FAIL'}")
        print(f"  mix: {mix}")
        print(f"  mix by domain: {by_dom}")
        print("=" * 66)

        # descriptive only, never used for selection: the test frontier of the same family
        pte = routers[sel["family"]].predict_quality(Xte)
        frontier = []
        for lam in LAMBDAS:
            c2 = _pick(pte, mean_cost, lam)
            frontier.append({"lam": lam, "test_q": float(_take(Ste, c2).mean()),
                             "test_c": float(_take(Cte, c2).mean())})
        print(f"  test frontier for {sel['family']} (descriptive, not used to select):")
        for f in frontier:
            print(f"    lam {f['lam']:5g}: q {f['test_q']:6.2f}  ${f['test_c']:.6f}  "
                  f"{100 * (1 - f['test_c'] / bc.mean()):+6.1f}% saving")

        out["scoring"][scoring] = {
            "best_single": aliases[bs], "selected": sel, "validation_sweep": rows,
            "test": {"router_q": float(rq.mean()), "router_c": float(rc.mean()),
                     "best_single_q": float(bq.mean()), "best_single_c": float(bc.mean()),
                     "single": {a: {"q": float(Ste[:, j].mean()), "c": float(Cte[:, j].mean())}
                                for j, a in enumerate(aliases)},
                     "oracle_cheapest_q": float(_take(Ste, cheap).mean()),
                     "oracle_cheapest_c": float(_take(Cte, cheap).mean()),
                     "dq_ci95": [float(np.percentile(dq, 2.5)), float(np.percentile(dq, 97.5))],
                     "saving": float(1 - rc.mean() / bc.mean()),
                     "saving_ci95": [float(np.percentile(save, 2.5)),
                                     float(np.percentile(save, 97.5))],
                     "h1_pass": bool(h1), "h1_lower_bound": ni.ci.lower,
                     "h1_margin": ni.margin, "cost_ratio": ni.cost_ratio, "mix": mix,
                     "mix_by_domain": by_dom},
            "test_frontier_descriptive": frontier,
        }

    out_path.write_text(json.dumps(out, indent=2))
    print(f"\nwritten {out_path}  (no Bedrock calls made)")


if __name__ == "__main__":
    main()
