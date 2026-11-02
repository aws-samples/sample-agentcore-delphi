"""Phase 2 training harness tests: featurizer, router families, selection, artifact
round-trip, and the request-path guarantees (T-RTR-1/2/6) via the reconstructed router.
"""
from __future__ import annotations

import pathlib
import warnings

import numpy as np
import pytest

from delphi.config import load_pool
from delphi.data import synthetic
from delphi.data.split import split_dataset
from delphi.data.synthetic_provider import SyntheticCouncil
from delphi.eval.build_matrix import build_response_matrix
from delphi.eval.scoring import SyntheticJudge
from delphi.router.features import HashingEncoder
from delphi.train.artifact_store import load_learned_router, save_artifact
from delphi.train.harness import train_and_select
from delphi.train.routers import KNNRouter, LinearRouter, PrototypeRouter, router_from_state

_CFG = "config/models_synthetic.yaml"


def _pool():
    load_pool.cache_clear()
    return load_pool(pathlib.Path(_CFG))


def _matrices(mode="complementary", n=40, seed=11):
    pool = _pool()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ds = synthetic.generate(n_per_domain=n, seed=seed)
    split = split_dataset(ds, seed=seed)
    council = SyntheticCouncil(mode=mode)
    judge = SyntheticJudge()
    tr = build_response_matrix(split.train, pool, council, judge).matrix
    va = build_response_matrix(split.validation, pool, council, judge).matrix
    te = build_response_matrix(split.test, pool, council, judge).matrix
    return pool, split, tr, va, te


# --- featurizer --------------------------------------------------------------
def test_encoder_is_deterministic_and_normalised():
    enc = HashingEncoder(dim=256)
    a = enc.embed_vec("who slew the serpent Python")
    b = enc.embed_vec("who slew the serpent Python")
    assert np.allclose(a, b)                       # determinism (underpins T-RTR-1)
    assert abs(np.linalg.norm(a) - 1.0) < 1e-9     # unit norm


def test_encoder_distinguishes_queries():
    enc = HashingEncoder(dim=512)
    assert not np.allclose(enc.embed_vec("compute 2+2"), enc.embed_vec("write a function"))


# --- router families ---------------------------------------------------------
@pytest.mark.parametrize("family_cls", [LinearRouter, PrototypeRouter, KNNRouter])
def test_family_fits_and_scores_all_aliases(family_cls):
    pool, split, tr, va, te = _matrices()
    enc = HashingEncoder(dim=256)
    X = enc.matrix(split.train.prompts())
    router = family_cls.fit(X, tr.scores, tr.aliases)
    scores = router.score(enc.embed_vec("who holds up the sky"), tr.aliases)
    assert set(scores) == set(tr.aliases)          # score for EVERY candidate (T-RTR-2 shape)


@pytest.mark.parametrize("family_cls", [LinearRouter, PrototypeRouter, KNNRouter])
def test_family_state_round_trips(family_cls):
    pool, split, tr, va, te = _matrices()
    enc = HashingEncoder(dim=128)
    X = enc.matrix(split.train.prompts())
    router = family_cls.fit(X, tr.scores, tr.aliases)
    clone = router_from_state(router.to_state())
    e = enc.embed_vec("charm Hades with music")
    assert router.score(e, tr.aliases) == clone.score(e, tr.aliases)


# --- selection ---------------------------------------------------------------
def test_selection_picks_a_latency_passing_family():
    pool, split, tr, va, te = _matrices()
    result = train_and_select(tr, split.train.prompts(), va, split.validation.prompts(),
                              encoder_dim=256)
    assert result.winner.meets_latency
    # winner has the max capture among the passing families
    passing = [r for r in result.all_results if r.meets_latency]
    assert result.winner.capture == max(r.capture for r in passing)


def test_learned_router_beats_random_on_complementary_pool():
    pool, split, tr, va, te = _matrices(mode="complementary")
    result = train_and_select(tr, split.train.prompts(), va, split.validation.prompts(),
                              encoder_dim=512)
    # on a complementary pool the selected router should capture real signal
    assert result.winner.capture > 0.3


# --- artifact round-trip into the request path -------------------------------
def test_artifact_save_and_reload_into_learned_router(tmp_path):
    pool, split, tr, va, te = _matrices()
    result = train_and_select(tr, split.train.prompts(), va, split.validation.prompts(),
                              encoder_dim=256)
    art = save_artifact(result, str(tmp_path / "art"))
    assert (tmp_path / "art" / "manifest.json").exists()
    assert (tmp_path / "art" / "weights.npz").exists()

    learned = load_learned_router(str(tmp_path / "art"), pool)

    # T-RTR-1 determinism: same query -> same choice
    q = "how many labours did Heracles perform"
    d1 = learned.predict(q)
    d2 = learned.predict(q)
    assert d1.chosen_alias == d2.chosen_alias
    # T-RTR-2 schema: scores for every candidate, model_id set
    assert set(d1.scores) == set(pool.aliases)
    assert d1.model_id == pool.by_alias(d1.chosen_alias).id
    # metadata carries the artifact hash (replayability)
    assert d1.metadata["artifact_hash"] == art.artifact_hash


def test_reloaded_router_never_emits_unknown_alias(tmp_path):
    # T-RTR-6: a router trained on the synthetic pool must not emit aliases outside it.
    pool, split, tr, va, te = _matrices()
    result = train_and_select(tr, split.train.prompts(), va, split.validation.prompts(),
                              encoder_dim=128)
    art = save_artifact(result, str(tmp_path / "art"))
    learned = load_learned_router(str(tmp_path / "art"), pool)
    for prompt in split.test.prompts()[:20]:
        assert learned.predict(prompt).chosen_alias in pool.aliases


def test_resolve_artifact_dir_passthrough_for_local_path():
    # A non-s3 path is returned unchanged (no boto3 import, no download).
    from delphi.train.artifact_store import resolve_artifact_dir
    assert resolve_artifact_dir("/some/local/dir") == "/some/local/dir"


def test_build_agent_router_arm_requires_artifact_uri(monkeypatch):
    # The router arm must fail fast with a clear error when the artifact URI is absent.
    from delphi.agent.app import build_agent_from_env
    monkeypatch.setenv("DELPHI_ARM", "router")
    monkeypatch.setenv("DELPHI_CONFIG_DIR", "config")
    # U6 BRK-U6-4: the agent has no default region now (BR11.7), so set one; this test is
    # about the artifact URI, and must not depend on the developer's shell environment.
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.delenv("DELPHI_ARTIFACT_URI", raising=False)
    load_pool.cache_clear()
    with pytest.raises(RuntimeError, match="DELPHI_ARTIFACT_URI"):
        build_agent_from_env()
