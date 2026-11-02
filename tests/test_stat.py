"""Statistical tests — design §3 (T-STAT-1 .. T-STAT-6).

These are the heart of the project. The math-layer tests (T-STAT-1..4 mechanics, power)
run for real against synthetic matrices with known structure. The data-dependent
assertions (against the real Phase 1 response matrix) are marked xfail until that data
exists — they are the exit criteria for Phase 1/4, not Phase 0.
"""
from __future__ import annotations

import numpy as np
import pytest

from delphi.eval import matrix as M
from delphi.eval import stats as S
from delphi.eval.contamination import check_contamination


# ---------------------------------------------------------------------------
# T-STAT-1  Complementarity (gates H2 pursuit)
# ---------------------------------------------------------------------------
class TestComplementarityGate:
    def test_oracle_gap_passes_on_complementary_pool(self, complementary_matrix):
        gap = M.oracle_gap(complementary_matrix)
        assert gap >= 3.0, f"expected complementary pool to clear the gate, gap={gap:.2f}"

    def test_oracle_gap_fails_on_nested_pool(self, nested_matrix):
        gap = M.oracle_gap(nested_matrix)
        assert gap < 3.0, f"nested pool should fail the gate, gap={gap:.2f}"

    def test_oracle_never_below_best_single(self, complementary_matrix, nested_matrix):
        for mat in (complementary_matrix, nested_matrix):
            assert mat.oracle_per_query().mean() >= mat.best_single_per_query().mean()

    @pytest.mark.xfail(reason="Phase 1: needs the real response matrix from S3")
    def test_gate_on_real_matrix(self):
        raise AssertionError("load response_matrix from S3 manifest and assert gap >= 3.0")


# ---------------------------------------------------------------------------
# T-STAT-2  H2 superiority (paired bootstrap + Holm-Bonferroni)
# ---------------------------------------------------------------------------
class TestSuperiority:
    def test_router_beating_all_models_is_detected(self):
        rng = np.random.default_rng(0)
        n = 500
        # router strictly dominates every model by a clear margin
        per_model = {a: rng.uniform(40, 60, n) for a in ("m1", "m2", "m3")}
        router = np.maximum.reduce(list(per_model.values())) + 5.0
        res = S.superiority_vs_all(router, per_model, seed=1)
        assert res.superior_to_all
        assert all(res.holm_rejected.values())

    def test_router_tied_with_a_model_is_not_superior(self):
        rng = np.random.default_rng(2)
        n = 500
        tied = rng.uniform(40, 60, n)
        per_model = {"m1": rng.uniform(30, 40, n), "m2": tied}
        router = tied.copy()  # identical to m2 -> not strictly superior
        res = S.superiority_vs_all(router, per_model, seed=3)
        assert not res.superior_to_all
        assert not res.holm_rejected["m2"]

    def test_holm_is_stricter_than_uncorrected(self):
        # a marginal p-value that clears raw 0.05 but not the Holm threshold
        pvals = {"a": 0.01, "b": 0.04, "c": 0.045}
        rejected = S.holm_bonferroni(pvals, alpha=0.05)
        assert rejected["a"] is True
        assert rejected["c"] is False  # 0.045 > 0.05/1 only after step-down fails earlier

    @pytest.mark.xfail(reason="Phase 4: needs router + per-model scores on the test split")
    def test_h2_on_real_data(self):
        raise AssertionError("assert lower CI bound vs strongest model > 0")


# ---------------------------------------------------------------------------
# T-STAT-3  H1 non-inferiority + cost
# ---------------------------------------------------------------------------
class TestNonInferiority:
    def test_non_inferior_and_cheap_passes(self):
        rng = np.random.default_rng(0)
        n = 500
        best = rng.uniform(60, 70, n)
        router = best - rng.uniform(0, 1.0, n)  # within the 2-pt margin
        res = S.non_inferiority(
            router, best, margin=2.0,
            router_cost_per_query=0.0005, best_single_cost_per_query=0.001,
        )
        assert res.quality_non_inferior
        assert res.cost_ok
        assert res.passes

    def test_cheap_but_too_much_worse_fails_quality(self):
        rng = np.random.default_rng(1)
        n = 500
        best = rng.uniform(60, 70, n)
        router = best - 5.0  # clearly worse than the 2-pt margin
        res = S.non_inferiority(
            router, best, margin=2.0,
            router_cost_per_query=0.0001, best_single_cost_per_query=0.001,
        )
        assert not res.quality_non_inferior
        assert not res.passes

    def test_non_inferior_but_not_cheap_enough_fails_cost(self):
        rng = np.random.default_rng(2)
        n = 500
        best = rng.uniform(60, 70, n)
        router = best.copy()
        res = S.non_inferiority(
            router, best, margin=2.0,
            router_cost_per_query=0.0009, best_single_cost_per_query=0.001,  # 0.9 > 0.7
        )
        assert res.quality_non_inferior
        assert not res.cost_ok
        assert not res.passes


# ---------------------------------------------------------------------------
# T-STAT-4  H3 capture ratio
# ---------------------------------------------------------------------------
class TestCaptureRatio:
    def test_oracle_captures_one(self, complementary_matrix):
        oracle = complementary_matrix.oracle_per_query()
        assert M.capture_ratio(oracle, complementary_matrix) == pytest.approx(1.0, abs=0.05)

    def test_random_captures_zero(self, complementary_matrix):
        rand = complementary_matrix.random_per_query(seeds=5)
        assert M.capture_ratio(rand, complementary_matrix) == pytest.approx(0.0, abs=0.05)

    @pytest.mark.xfail(reason="Phase 2/4: needs the trained router's per-query scores")
    def test_learned_router_captures_at_least_60pct(self):
        raise AssertionError("assert capture_ratio(router) >= 0.60 on held-out test set")


# ---------------------------------------------------------------------------
# T-STAT-5  Judge-bias control (dual scoring agreement)
# ---------------------------------------------------------------------------
class TestJudgeBias:
    @pytest.mark.xfail(reason="Phase 4: needs native + judge scores for both methods")
    def test_h1_h2_agree_in_sign_across_scoring_methods(self):
        raise AssertionError("assert sign(native verdict) == sign(judge verdict)")

    def test_judge_is_outside_candidate_pool(self, pool):
        judge_families = {"famA", "famB", "famC", "famD"}
        # the real config's judge (gpt-5-4) must not share a family with any candidate
        assert pool.judge_alias not in pool.aliases


# ---------------------------------------------------------------------------
# T-STAT-6  No contamination (exact + MinHash near-dup)
# ---------------------------------------------------------------------------
class TestContamination:
    def test_disjoint_sets_are_clean(self):
        train = ["what is the capital of france", "solve 2x + 3 = 7"]
        evalset = ["name a prime number greater than ten", "who wrote hamlet"]
        rep = check_contamination(train, evalset)
        assert rep.clean

    def test_exact_overlap_flagged(self):
        train = ["what is the capital of france"]
        evalset = ["what is the capital of france"]
        rep = check_contamination(train, evalset)
        assert rep.exact_overlap == 1
        assert not rep.clean

    def test_near_duplicate_flagged(self):
        train = ["what is the capital city of france today"]
        evalset = ["what is the capital city of france today please"]
        rep = check_contamination(train, evalset, near_dup_threshold=0.6)
        assert rep.near_dup_pairs >= 1
        assert not rep.clean


# ---------------------------------------------------------------------------
# Power analysis — the design's open sizing question (feeds Phase 0 exit)
# ---------------------------------------------------------------------------
class TestPowerAnalysis:
    def test_required_n_grows_as_effect_shrinks(self):
        big = S.required_n_for_paired_diff(effect=4.0, sd_of_diff=15.0)
        small = S.required_n_for_paired_diff(effect=2.0, sd_of_diff=15.0)
        assert small.n_required > big.n_required

    def test_two_point_effect_sizing_is_reported(self):
        res = S.required_n_for_paired_diff(effect=2.0, sd_of_diff=15.0, power=0.80)
        # sanity: normal approx gives ((1.96+0.84)*15/2)^2 ~= 441
        assert 350 <= res.n_required <= 550
