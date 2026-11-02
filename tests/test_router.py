"""Router unit/behavioural tests — design §3 (T-RTR-1 .. T-RTR-6)."""
from __future__ import annotations

import pytest

from delphi.router.base import Decision, Router, RouterError
from delphi.router.baselines import (
    AlwaysCheapestRouter,
    AlwaysModelRouter,
    OracleRouter,
    RandomRouter,
)


# ---------------------------------------------------------------------------
# T-RTR-1  Determinism
# ---------------------------------------------------------------------------
def test_determinism_same_query_same_choice(pool):
    r = RandomRouter(pool, seed=123)
    a = r.predict("some query").chosen_alias
    b = r.predict("some query").chosen_alias
    assert a == b


def test_different_seed_can_differ(pool):
    q = "another query"
    choices = {RandomRouter(pool, seed=s).predict(q).chosen_alias for s in range(20)}
    assert len(choices) > 1  # seed actually varies the assignment


# ---------------------------------------------------------------------------
# T-RTR-2  Schema
# ---------------------------------------------------------------------------
def test_decision_schema(pool):
    d = AlwaysModelRouter(pool, "mid").predict("q")
    assert isinstance(d, Decision)
    assert d.model_id == pool.by_alias("mid").id
    assert set(d.scores.keys()) == set(pool.aliases)  # score for EVERY candidate
    assert isinstance(d.latency_ms, float)


# ---------------------------------------------------------------------------
# T-RTR-3  Fallback (never raise on the request path)
# ---------------------------------------------------------------------------
def test_learned_router_falls_back_on_scorer_error(pool):
    # U4 (D-CG-U4-10, BRK-02): the fallback is the agent's, so T-RTR-3 drives RouterAgent
    # over a raising LearnedRouter. The three assertions are carried onto what the agent's
    # fallback produces: flagged, the strongest alias, and the fallback's all-zero scores.
    from delphi.agent.app import RouterAgent
    from delphi.agent.bedrock import ConverseClient, ConverseResult
    from delphi.agent.telemetry import InMemorySink
    from delphi.router.learned import Artifact, LearnedRouter

    class OkConverse(ConverseClient):
        def converse(self, model_id, prompt, params):
            return ConverseResult(text="ok", input_tokens=1, output_tokens=1, model_id=model_id)

    def boom(_features, _aliases):
        raise RuntimeError("classifier exploded")

    art = Artifact("mlprouter", pool.aliases, "enc", "s3://w", "hash123")
    lr = LearnedRouter(pool, art, embedder=None, scorer=boom)
    sink = InMemorySink()
    out = RouterAgent(pool, lr, OkConverse(), sink).handle_request({"prompt": "q"})
    (d,) = sink.records
    assert d.is_fallback and out["is_fallback"]
    assert d.chosen_alias == "strong"
    assert d.scores == {a: 0.0 for a in pool.aliases}


# ---------------------------------------------------------------------------
# T-RTR-4  Latency budget (< 150 ms P90) — on the target architecture, from the committed
# NFR1 sample (evidence/nfr1/, U6 S12): the warm routing window (receipt in handle_request to
# just before the Converse call) of the deployed router arm, recomputed by U2's sample checks
# and compared with the budget the router's pool file declares. It says nothing about
# end-to-end latency, Converse time or cold start (T-RTR-5).
# ---------------------------------------------------------------------------
def _router_pool_budget_ms() -> float:
    import pathlib

    import yaml

    pool_file = pathlib.Path(__file__).resolve().parents[1] / "config" / "models_realpool.yaml"
    return float(yaml.safe_load(pool_file.read_text())["latency_budget_ms"]
                 ["routing_overhead_p90"])


def test_routing_latency_p90_under_150ms():
    from delphi.eval import latency_sample

    budget = _router_pool_budget_ms()
    assert budget == 150.0, "the D2 budget the router arm's pool file declares"
    records, manifest = latency_sample.load_sample()  # absent sample: FileNotFoundError, a fail
    p90 = latency_sample.check_sample(records, manifest)
    assert manifest["arm"] == "router" and manifest["applies_to"] == "warm"
    assert p90 < budget
    # Negative controls: the comparison is strict, and an over-budget sample fails it.
    assert not p90 < p90
    over = [dict(r, routing_overhead_ms=budget + 1.0) for r in records]
    assert not latency_sample.nearest_rank_p90(
        [r["routing_overhead_ms"] for r in latency_sample.counted(over).warm]) < budget


# ---------------------------------------------------------------------------
# T-RTR-5  Cold start (artifact load < 3s, cold invoke < 10s). The deployed container exists
# since Phase 3, so that gate is open; what is still missing is a committed measurement. The
# NFR1 sample is warm-only by protocol, and no record measures artifact load or the cold
# invoke's wall time.
# ---------------------------------------------------------------------------
@pytest.mark.xfail(reason="needs a committed cold-start capture (artifact load and cold "
                          "invoke wall time); the deployed container exists, no record "
                          "measures either")
def test_cold_start_budget():
    raise AssertionError("measure S3 artifact load < 3s and total cold invoke < 10s")


# ---------------------------------------------------------------------------
# T-RTR-6  Unknown-model guard (drift between trained and live pool)
# ---------------------------------------------------------------------------
def test_router_cannot_emit_alias_outside_pool(pool):
    class RogueRouter(Router):
        pool_aliases = pool.aliases

        def predict(self, query: str) -> Decision:
            return self._validate(
                Decision("x.ghost", "ghost", {"ghost": 1.0}, 1.0)
            )

    with pytest.raises(RouterError):
        RogueRouter().predict("q")


def test_learned_router_rejects_artifact_trained_on_unknown_alias(pool):
    from delphi.router.learned import Artifact, LearnedRouter

    art = Artifact("mlprouter", pool.aliases + ("ghost",), "enc", "s3://w", "h")
    with pytest.raises(RouterError):
        LearnedRouter(pool, art, embedder=None, scorer=lambda f, a: {})


# ---------------------------------------------------------------------------
# T-CMP-2  Trivial routers as floor/ceiling
# ---------------------------------------------------------------------------
def test_always_cheapest_picks_cheapest(pool):
    d = AlwaysCheapestRouter(pool).predict("q")
    assert d.chosen_alias == "cheap"


def test_oracle_picks_per_query_argmax(pool):
    matrix = {
        "q1": {"cheap": 10, "mid": 20, "strong": 90, "alt": 15},
        "q2": {"cheap": 80, "mid": 20, "strong": 30, "alt": 15},
    }
    oracle = OracleRouter(pool, matrix)
    assert oracle.predict("q1").chosen_alias == "strong"
    assert oracle.predict("q2").chosen_alias == "cheap"


def test_oracle_refuses_unknown_query(pool):
    oracle = OracleRouter(pool, {})
    with pytest.raises(RouterError):
        oracle.predict("never seen")
