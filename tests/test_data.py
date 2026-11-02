"""Synthetic dataset, split, scoring, and matrix-build tests (Phase 1 offline pipeline)."""
from __future__ import annotations

import warnings

import pytest

from delphi.config import load_pool
from delphi.data import synthetic
from delphi.data.schema import Query, TaskType
from delphi.data.split import split_dataset
from delphi.data.synthetic_provider import SyntheticCouncil
from delphi.eval import scoring
from delphi.eval.build_matrix import BudgetExceeded, build_response_matrix
from delphi.eval.contamination import NearDupFilter, check_contamination
from delphi.eval.gate import evaluate_gate
from delphi.eval.scoring import SyntheticJudge

_SYNTH_CONFIG = "config/models_synthetic.yaml"


def _pool():
    load_pool.cache_clear()
    import pathlib

    return load_pool(pathlib.Path(_SYNTH_CONFIG))


# --- dataset -----------------------------------------------------------------
def test_generate_is_deterministic():
    a = synthetic.generate(n_per_domain=20, seed=1)
    b = synthetic.generate(n_per_domain=20, seed=1)
    assert a.content_hash() == b.content_hash()


def test_generate_has_no_internal_near_dups():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=30, seed=2)
    # a fresh NearDupFilter should accept every prompt (none are near-dups of another)
    ndf = NearDupFilter()
    assert all(ndf.accept(q.prompt) for q in ds.queries)


def test_all_domains_present():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=15, seed=3)
    domains = {q.domain for q in ds.queries}
    assert domains == {"prophecy", "facts", "reckoning", "craft", "lore"}


# --- split + contamination ---------------------------------------------------
def test_split_is_disjoint_and_clean():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=40, seed=4)
    split = split_dataset(ds, seed=4)
    ids = lambda d: {q.id for q in d.queries}  # noqa: E731 - a one-line local helper
    assert ids(split.train) & ids(split.test) == set()
    assert ids(split.train) & ids(split.validation) == set()
    rep = check_contamination(split.train.prompts(), split.test.prompts())
    assert rep.clean


def test_split_fractions_roughly_hold():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=60, seed=5)
    split = split_dataset(ds, train=0.6, validation=0.2, seed=5)
    total = len(ds)
    assert 0.5 < len(split.train) / total < 0.7
    assert 0.1 < len(split.validation) / total < 0.3


# --- scoring -----------------------------------------------------------------
def test_native_scorers():
    mc = Query("1", "p", TaskType.KNOWLEDGE_MC, "facts", answer="C")
    assert scoring.score_native("The answer is C.", mc) == 100.0
    assert scoring.score_native("A", mc) == 0.0

    math_q = Query("2", "p", TaskType.MATH, "reckoning", answer="42")
    assert scoring.score_native("I believe it is 42 amphorae", math_q) == 100.0
    assert scoring.score_native("41", math_q) == 0.0

    qa = Query("3", "p", TaskType.OPEN_QA, "lore", answer="Mount Parnassus")
    assert scoring.score_native("Mount Parnassus", qa) == 100.0
    assert 0.0 < scoring.score_native("the mount Parnassus range", qa) < 100.0


def test_pass_at_1_executes_code():
    q = Query("4", "p", TaskType.CODE, "craft", answer="laurels",
              metadata={"entry_point": "laurels", "tests": [((5,), 15), ((1,), 1)]})
    good = "def laurels(n):\n    return sum(range(1, n+1))"
    bad = "def laurels(n):\n    return 0"
    # U4 BRK-13: the code is ours, so it is stated as synthetic; it now runs in the child.
    assert scoring.score_native(good, q, provenance="synthetic") == 100.0
    assert scoring.score_native(bad, q, provenance="synthetic") == 0.0


def test_judge_scores_open_ended():
    judge = SyntheticJudge()
    q = Query("5", "p", TaskType.OPEN_ENDED, "prophecy")
    good = "When the bronze gates groan, seek the path you fear; there lies your fortune."
    bad = "The smoke is silent; no omen comes."
    assert judge.score(good, q) > judge.score(bad, q)


# --- matrix build + gate -----------------------------------------------------
def test_build_matrix_and_gate_complementary():
    pool = _pool()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=40, seed=7)
    split = split_dataset(ds, seed=7)
    council = SyntheticCouncil(mode="complementary")
    result = build_response_matrix(split.test, pool, council, SyntheticJudge())
    report = evaluate_gate(result.matrix)
    assert report.passes
    assert report.gap >= 3.0
    assert result.total_cost > 0


def test_nested_mode_fails_gate():
    pool = _pool()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=40, seed=8)
    split = split_dataset(ds, seed=8)
    council = SyntheticCouncil(mode="nested")
    result = build_response_matrix(split.test, pool, council, SyntheticJudge())
    report = evaluate_gate(result.matrix)
    assert not report.passes  # apollo dominates -> gap collapses below threshold


def test_budget_cap_enforced():
    pool = _pool()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=20, seed=9)
    split = split_dataset(ds, seed=9)
    council = SyntheticCouncil(mode="complementary")
    with pytest.raises(BudgetExceeded):
        build_response_matrix(split.test, pool, council, SyntheticJudge(), cost_cap_usd=0.001)
