#!/usr/bin/env python3
"""v4 confirmation: the frozen v3 router vs Claude Sonnet 5.5 on 520 fresh questions.

Pre-registered in results/preregistration-v4-confirmation-2026-10-28.md. Builds the full
ten-arm matrix on the v4 questions (all test), routes each question with the frozen artifact
in data/frozen_router_v4/ (a copy of artifacts/phase2_real_v3_lenient/), and runs the
primary test (non-inferiority vs Claude, lenient) and the secondary ones. The cap covers the
whole project, earlier runs included.

Usage:
  python scripts/run_confirm_v4_real.py --dry-run 3     # 3 per domain, parsing check only
  python scripts/run_confirm_v4_real.py                 # the confirmation
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, "src")

import numpy as np  # noqa: E402 - after the src/ path shim

from delphi.agent.bedrock import BedrockConverseClient  # noqa: E402 - after the src/ path shim
from delphi.config import load_pool  # noqa: E402 - after the src/ path shim
from delphi.data.schema import Dataset  # noqa: E402 - after the src/ path shim
from delphi.eval.stats import non_inferiority, paired_bootstrap_ci, superiority_vs_all  # noqa: E402 - after the src/ path shim
from delphi.train.artifact_store import load_learned_router  # noqa: E402 - after the src/ path shim
from real_source import (  # noqa: E402 - scripts/ is on the path
    REAL_DOMAINS,
    AnswerCache,
    ConverseJudge,
    RealCouncil,
    build_real_matrix,
    cell_score,
    load_judge,
    load_trials,
    pool_config,
    prior_spend,
    provider_default_aliases,
)

FROZEN = pathlib.Path("data/frozen_router_v4")
#: The SHA-256 of each file of the author's frozen router, kept for reference. That artifact is
#: not published (it was trained on third-party model outputs); a reader freezes their own.
AUTHOR_SHA256 = {
    "manifest.json": "2eceb5f9cd5e6a1689affc29c2241f55b2477f519050d83836a628c78338a99f",
    "scalars.json": "a6dcdc478b7d9245487185b66e1032929dd5d0a2a3fbec804d467739576fd637",
    "weights.npz": "895e3797ac22b722f6411971c353fc7d62bbbda429b4bd8e3df3f3987d5dbac4"}
REGION = "us-west-2"
FRONTIER = "claude-sonnet-5-5"


def _verify_frozen() -> None:
    """Freeze on first use: record each file's SHA-256 in SHA256SUMS, then refuse any change."""
    sums = FROZEN / "SHA256SUMS"
    got = {name: hashlib.sha256((FROZEN / name).read_bytes()).hexdigest()
           for name in AUTHOR_SHA256}
    if not sums.exists():
        sums.write_text("".join(f"{h}  {n}\n" for n, h in got.items()))
        print(f"froze {FROZEN}: recorded {sums.name}")
    want = dict(reversed(line.split("  ", 1)) for line in sums.read_text().splitlines())
    for name, digest in got.items():
        if want.get(name) != digest:
            raise SystemExit(f"frozen router file {name} changed since it was frozen")
    if got == AUTHOR_SHA256:
        print("frozen router matches the author's artifact")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=100.0)
    ap.add_argument("--dry-run", type=int, default=0, metavar="K")
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()
    _verify_frozen()

    cfg = pool_config("v4")
    load_pool.cache_clear()
    pool = load_pool(cfg)
    A = pool.aliases
    client = BedrockConverseClient(REGION)
    council = RealCouncil(client, pool, provider_default=provider_default_aliases(cfg))
    ds, _split, out, judge_prompt = load_trials("v4")
    judge = ConverseJudge(client, load_judge(cfg), prompt=judge_prompt)
    cache = AnswerCache(out / "cells.jsonl")
    prior = prior_spend("v4")
    cap = args.cap - prior
    print(f"v4: {len(ds)} questions, {len(A)} arms; frozen router verified; "
          f"cap left ${cap:.4f} (after ${prior:.4f} earlier); v4 spend so far ${cache.spend:.4f}")

    if args.dry_run:
        picked, seen = [], defaultdict(int)
        for q in ds.queries:
            if seen[q.domain] < args.dry_run:
                picked.append(q)
                seen[q.domain] += 1
        ds = Dataset(f"{ds.name}:dry{args.dry_run}", tuple(picked))

    build_real_matrix(ds, pool, council, judge, cache, cost_cap_usd=cap, workers=args.workers)
    print(f"answers built; v4 spend ${cache.spend:.4f}; project total ${prior + cache.spend:.4f}")

    router = load_learned_router(str(FROZEN), pool)
    col = {a: j for j, a in enumerate(A)}
    ch = np.array([col[router.predict(q.prompt).chosen_alias] for q in ds.queries])
    C = np.array([[cache.cells[(q.id, a)]["cost_usd"] for a in A] for q in ds.queries])
    rc = C[np.arange(len(ch)), ch]
    cl = col[FRONTIER]
    result = {"n": len(ds), "dataset_hash": ds.content_hash(), "dry_run": args.dry_run,
              "v4_spend_usd": cache.spend, "project_spend_usd": prior + cache.spend,
              "router_cost": float(rc.mean()), "single_cost": {a: float(C[:, j].mean())
                                                               for j, a in enumerate(A)},
              "scoring": {}}

    for scoring in ("lenient", "strict"):
        S = np.array([[cell_score(cache.cells[(q.id, a)], q, scoring) for a in A]
                      for q in ds.queries])
        r = S[np.arange(len(ch)), ch]
        ni = non_inferiority(r, S[:, cl], router_cost_per_query=float(rc.mean()),
                             best_single_cost_per_query=float(C[:, cl].mean()))
        sup_cl = paired_bootstrap_ci(r - S[:, cl])
        h2 = superiority_vs_all(r, {a: S[:, j] for j, a in enumerate(A)})
        tag = "PRIMARY" if scoring == "lenient" else "secondary"
        print(f"\n##### {scoring.upper()} ({tag}) #####")
        print(f"  {'model':20s} quality   $/query")
        for j, a in enumerate(A):
            print(f"  {a:20s} {S[:, j].mean():7.2f}   {C[:, j].mean():.6f}")
        print(f"  {'ROUTER (frozen)':20s} {r.mean():7.2f}   {rc.mean():.6f}")
        print(f"  {'oracle':20s} {S.max(axis=1).mean():7.2f}")
        verdict = "PASS" if ni.passes else "FAIL"
        print(f"\n  H1 vs Claude: delta {ni.ci.mean_delta:+.2f}, one-sided 97.5% lower bound "
              f"{ni.ci.lower:+.2f} (> -2?), cost ratio {ni.cost_ratio:.3f} (<= 0.70?) -> {verdict}")
        print(f"  superiority vs Claude: 95% CI {sup_cl.lower:+.2f} to {sup_cl.upper:+.2f}")
        for a in ("gemma-3-27b", "deepseek-v3-2"):
            c = paired_bootstrap_ci(r - S[:, col[a]])
            print(f"  vs {a:16s}: {c.mean_delta:+.2f} (95% CI {c.lower:+.2f} to {c.upper:+.2f}), "
                  f"cost ratio {rc.mean() / C[:, col[a]].mean():.2f}")
        print(f"  H2 superior to all ten: {h2.superior_to_all}")
        print(f"  {'domain':10s} router  Claude  routed-to")
        dom = {}
        for d in REAL_DOMAINS:
            i = [k for k, q in enumerate(ds.queries) if q.domain == d]
            mix = {A[j]: int((ch[i] == j).sum()) for j in range(len(A)) if (ch[i] == j).any()}
            dom[d] = {"router": float(r[i].mean()), "claude": float(S[i, cl].mean()), "mix": mix}
            print(f"  {d:10s} {r[i].mean():6.1f}  {S[i, cl].mean():6.1f}  {mix}")
        keep = [k for k, q in enumerate(ds.queries) if not q.prompt.startswith("A temple has")]
        ni_k = non_inferiority(r[keep], S[keep, cl],
                               router_cost_per_query=float(rc[keep].mean()),
                               best_single_cost_per_query=float(C[keep, cl].mean()))
        allmiss = int((S.max(axis=1) == 0).sum())
        print(f"  audit: all-miss questions {allmiss}; without temple template (n={len(keep)}): "
              f"lower bound {ni_k.ci.lower:+.2f}, cost ratio {ni_k.cost_ratio:.3f} -> "
              f"{'PASS' if ni_k.passes else 'FAIL'}")
        result["scoring"][scoring] = {
            "single_quality": {a: float(S[:, j].mean()) for j, a in enumerate(A)},
            "router_quality": float(r.mean()), "oracle": float(S.max(axis=1).mean()),
            "h1_vs_claude": {"mean_delta": ni.ci.mean_delta, "lower": ni.ci.lower,
                             "cost_ratio": ni.cost_ratio, "pass": ni.passes},
            "superiority_vs_claude_ci95": [sup_cl.lower, sup_cl.upper],
            "h2_superior_to_all": h2.superior_to_all,
            "h2_per_model": {a: [c.mean_delta, c.lower, c.upper] for a, c in h2.per_model.items()},
            "by_domain": dom, "allmiss": allmiss,
            "no_temple": {"n": len(keep), "lower": ni_k.ci.lower, "cost_ratio": ni_k.cost_ratio,
                          "pass": ni_k.passes},
        }
    name = "confirm_v4_dry.json" if args.dry_run else "confirm_v4.json"
    (out / name).write_text(json.dumps(result, indent=2))
    print(f"\nwritten {out / name}")


if __name__ == "__main__":
    main()
