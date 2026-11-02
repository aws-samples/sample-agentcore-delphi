"""Harness/infrastructure + comparator tests — design §3 (T-INF-*, T-CMP-*).

  T-INF-3b  telemetry completeness over invocations that CAN fail: ten calls with calls 3 and
            7 raising give ten records, two of them generation_failed (BR2.5, AC4.2.3). The
            same parametrised test as T-INF-3, whose non-raising case is kept unchanged.
"""
from __future__ import annotations

import pytest

from delphi.agent.app import RouterAgent
from delphi.agent.bedrock import ConverseClient, ConverseResult, GenerationParams
from delphi.agent.telemetry import InMemorySink
from delphi.eval.cost import (Usage, arm_cost_per_query, reconciles_with_cost_explorer,
                              tokens_by_model)
from delphi.router.baselines import AlwaysModelRouter


class FakeConverse(ConverseClient):
    """Records the exact model_id + params each call used, so arm parity is checkable."""

    def __init__(self):
        self.calls = []

    def converse(self, model_id, prompt, params):
        self.calls.append((model_id, prompt, params))
        return ConverseResult(text=f"answer from {model_id}", input_tokens=100, output_tokens=50,
                              model_id=model_id)


class FailingOnSomeCallsConverse(FakeConverse):
    """FakeConverse that raises on the given 1-based call numbers, as the real client can."""

    def __init__(self, failing: set[int]):
        super().__init__()
        self._failing = failing

    def converse(self, model_id, prompt, params):
        result = super().converse(model_id, prompt, params)
        if len(self.calls) in self._failing:
            raise RuntimeError("ThrottlingException: rate exceeded")
        return result


# ---------------------------------------------------------------------------
# T-INF-1  Arm parity (routing is the only delta)
# ---------------------------------------------------------------------------
def test_arm_parity_same_params_across_arms(pool):
    params = GenerationParams(max_tokens=512, temperature=0.0)
    fake = FakeConverse()
    sink = InMemorySink()

    router_arm = RouterAgent(pool, AlwaysModelRouter(pool, "mid"), fake, sink, arm="router",
                             params=params)
    pinned_arm = RouterAgent(pool, AlwaysModelRouter(pool, "mid"), fake, sink,
                             arm="pinned:strong", pinned_alias="strong", params=params)

    router_arm.handle_request({"prompt": "hello"})
    pinned_arm.handle_request({"prompt": "hello"})

    # identical prompt + generation params; only the chosen model differs
    assert fake.calls[0][1] == fake.calls[1][1]  # same prompt
    assert fake.calls[0][2] == fake.calls[1][2]  # same params object value


# ---------------------------------------------------------------------------
# T-INF-2  Cost ground truth
# ---------------------------------------------------------------------------
def test_cost_computed_from_tokens(pool):
    usages = [Usage("mid", 1000, 500)]
    # mid price: 0.0008 in, 0.0032 out -> 1*0.0008 + 0.5*0.0032 = 0.0024
    assert arm_cost_per_query(pool, usages) == pytest.approx(0.0024)


def test_cost_reconciliation_within_tolerance():
    assert reconciles_with_cost_explorer(100.0, 101.5, tolerance=0.02)
    assert not reconciles_with_cost_explorer(100.0, 105.0, tolerance=0.02)


def test_tokens_by_model_is_repriceable():
    usages = [Usage("mid", 100, 50), Usage("mid", 200, 25), Usage("cheap", 10, 10)]
    agg = tokens_by_model(usages)
    assert agg["mid"] == (300, 75)
    assert agg["cheap"] == (10, 10)


# ---------------------------------------------------------------------------
# T-INF-3  Telemetry completeness (100% of invocations logged)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["complete", "raising_3_and_7"])
def test_every_invocation_emits_a_record(pool, mode):
    failing = {3, 7} if mode == "raising_3_and_7" else set()
    fake = FailingOnSomeCallsConverse(failing)
    sink = InMemorySink()
    agent = RouterAgent(pool, AlwaysModelRouter(pool, "mid"), fake, sink, arm="router")
    for i in range(10):
        if i + 1 in failing:
            with pytest.raises(RuntimeError):
                agent.handle_request({"prompt": f"q{i}", "request_id": f"r{i}"})
        else:
            agent.handle_request({"prompt": f"q{i}", "request_id": f"r{i}"})
    assert len(sink.records) == 10
    assert {r.request_id for r in sink.records} == {f"r{i}" for i in range(10)}
    failed = [r for r in sink.records if getattr(r, "error_kind", None) is not None]
    assert [r.request_id for r in failed] == [f"r{n - 1}" for n in sorted(failing)]
    assert {r.error_kind for r in failed} <= {"generation_failed"}
    # every successful record carries the fields the statistical eval needs
    for rec in sink.records:
        if rec in failed:
            continue
        assert rec.cost_usd > 0
        assert rec.chosen_model_id
        assert rec.input_tokens > 0


# ---------------------------------------------------------------------------
# T-INF-4  Replayability (idempotent from a manifest)
# ---------------------------------------------------------------------------
@pytest.mark.xfail(reason="Phase 3/4: needs the manifest-driven eval driver")
def test_eval_run_reproducible_from_manifest():
    raise AssertionError("re-run from (dataset hash, artifact hash, model config hash) -> "
                         "identical headline numbers")


# ---------------------------------------------------------------------------
# T-INF-5  Throughput (no throttling skew)
# ---------------------------------------------------------------------------
@pytest.mark.xfail(reason="Phase 4: needs the eval run at target QPS; live Bedrock is reachable "
                          "since Phase 3, but the NFR1 capture is not that run")
def test_no_throttling_during_eval():
    raise AssertionError("assert zero ThrottlingException across the eval run, or exclude+report")


# ---------------------------------------------------------------------------
# T-CMP-1  Bedrock Intelligent Prompt Routing comparator arm
# ---------------------------------------------------------------------------
@pytest.mark.xfail(reason="the IPR arm is not implemented: unit U8 was dropped through its "
                          "designed fallback (2026-10-20), so no ipr arm is deployed or wired")
def test_ipr_arm_reports_same_metric_table():
    raise AssertionError("run IPR (Anthropic-family) arm over the identical test set and "
                         "emit the same columns")
