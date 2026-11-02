#!/usr/bin/env python3
"""Phase 2 dry-run: train router families on the synthetic matrix, select, emit artifact,
and prove the artifact round-trips into a request-path LearnedRouter. No AWS.

Pipeline:
  generate -> split -> build train+val+test matrices -> train_and_select (on val) ->
  save artifact -> reload as LearnedRouter -> route the TEST split and report capture vs
  the oracle/best-single/random baselines.

Selection uses validation only (Threat #6). Test is scored once, at the end, with the
already-selected router — the honest Phase 4-style number.

Usage:
  python scripts/run_phase2_synthetic.py
  python scripts/run_phase2_synthetic.py --mode nested --n 80
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, "src")

import numpy as np  # noqa: E402 - after the src/ path shim

from delphi.config import load_pool  # noqa: E402 - after the src/ path shim
from delphi.data import synthetic  # noqa: E402 - after the src/ path shim
from delphi.data.split import split_dataset  # noqa: E402 - after the src/ path shim
from delphi.data.synthetic_provider import SyntheticCouncil  # noqa: E402 - after the src/ path shim
from delphi.eval.build_matrix import build_response_matrix  # noqa: E402 - after the src/ path shim
from delphi.eval.matrix import (  # noqa: E402 - after the src/ path shim
    H3_CAPTURE_THRESHOLD,
    ResponseMatrix,
    capture_ratio,
    oracle_gap,
)
from delphi.eval.scoring import SyntheticJudge  # noqa: E402 - after the src/ path shim
from delphi.train.artifact_store import load_learned_router, save_artifact  # noqa: E402 - after the src/ path shim
from delphi.train.harness import train_and_select  # noqa: E402 - after the src/ path shim

_CONFIG = pathlib.Path("config/models_synthetic.yaml")
_ART = "artifacts/phase2_synthetic"


def _matrix_for(ds, pool, council, judge):
    res = build_response_matrix(ds, pool, council, judge)
    return res.matrix


def _routed_capture(learned_router, test_matrix: ResponseMatrix, test_prompts, query_ids):
    """Route each test query through the reconstructed LearnedRouter, return capture."""
    col = {a: j for j, a in enumerate(test_matrix.aliases)}
    id_to_row = {qid: i for i, qid in enumerate(test_matrix.queries)}
    chosen_idx = np.empty(len(test_prompts), dtype=int)
    for k, (qid, prompt) in enumerate(zip(query_ids, test_prompts)):
        decision = learned_router.predict(prompt)
        chosen_idx[k] = col[decision.chosen_alias]
    # reorder chosen to matrix row order (they align since we iterate matrix order)
    routed = test_matrix.policy_per_query(chosen_idx)
    return capture_ratio(routed, test_matrix), float(routed.mean())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["complementary", "nested"], default="complementary")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260714)
    ap.add_argument("--dim", type=int, default=512)
    args = ap.parse_args()

    load_pool.cache_clear()
    pool = load_pool(_CONFIG)

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=args.n, seed=args.seed)
    split = split_dataset(ds, seed=args.seed)
    council = SyntheticCouncil(mode=args.mode)
    judge = SyntheticJudge()

    print(f"mode={args.mode}  dataset={len(ds)}  "
          f"train/val/test={len(split.train)}/{len(split.validation)}/{len(split.test)}")

    train_m = _matrix_for(split.train, pool, council, judge)
    val_m = _matrix_for(split.validation, pool, council, judge)
    test_m = _matrix_for(split.test, pool, council, judge)
    print(f"oracle gap (train): {oracle_gap(train_m):.2f}   (test): {oracle_gap(test_m):.2f}\n")

    # --- train + select on validation ---
    result = train_and_select(
        train_m, split.train.prompts(), val_m, split.validation.prompts(),
        encoder_dim=args.dim,
    )
    print("Router family selection (validation):")
    print(result.table())
    print(f"\nWinner: {result.winner.family}  "
          f"(val capture {result.winner.capture:.3f}, P90 {result.winner.p90_latency_ms:.2f}ms)")

    # --- persist + reload into the request path ---
    art = save_artifact(result, _ART)
    print(f"\nArtifact saved: family={art.family} hash={art.artifact_hash} -> {_ART}/")
    learned = load_learned_router(_ART, pool)
    print(f"Reloaded LearnedRouter OK (encoder={art.encoder_name})")

    # --- honest test-set evaluation with the selected router ---
    test_cap, test_q = _routed_capture(
        learned, test_m, split.test.prompts(), list(test_m.queries)
    )
    bs_idx = test_m.best_single_index()
    print("\n" + "=" * 60)
    print("HELD-OUT TEST (selected router, scored once):")
    print(f"  router mean quality : {test_q:6.2f}")
    print(f"  best single ({test_m.aliases[bs_idx]:9s}): "
          f"{test_m.best_single_per_query().mean():6.2f}")
    print(f"  oracle (ceiling)    : {test_m.oracle_per_query().mean():6.2f}")
    print(f"  random (floor)      : {test_m.random_per_query().mean():6.2f}")
    print(f"  CAPTURE RATIO       : {test_cap:.3f}   (H3 target >= {H3_CAPTURE_THRESHOLD:.2f})")
    verdict = "PASS" if test_cap >= H3_CAPTURE_THRESHOLD else "MISS"
    print(f"  H3 (learnability)   : {verdict}")
    print("=" * 60)


if __name__ == "__main__":
    main()
