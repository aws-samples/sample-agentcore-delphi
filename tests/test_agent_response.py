"""Response envelope tests — U1 LC-8 (agent/app.py:92-105).

ADR-010 puts the DecisionRecord in the invoke response IN ADDITION to CloudWatch, so the
verification command needs no log access. The change is strictly additive: exactly two
keys, every existing key unmoved, and the SAME record instance both emitted and returned
(BR1.1 - BR1.6, NFR1.17).

  T-AR-1  the key set equals today's nine plus exactly two
  T-AR-2  key ORDER is preserved - a key-set test alone is order-blind
  T-AR-3  decision_record is a mapping, not to_json()'s string
  T-AR-4  the emitted record and the returned one are the SAME object
  T-AR-5  emission is observed BEFORE response assembly
  T-AR-6  two independent versions: the envelope carries RESPONSE_SCHEMA_VERSION (C1, U1's)
          and the nested record carries its own schema_version == 1 (C2, U2's)
  T-AR-7  scores is a mapping and never None, per arm kind

T-AR-8 (a raising Converse propagated with no record) was removed by U2 and replaced by
T-INF-3b in tests/test_infra.py, which asserts the opposite: a record IS emitted when the
Converse call raises (BR2.5).
"""
from __future__ import annotations

import pytest

from delphi.agent.app import RouterAgent
from delphi.agent.bedrock import ConverseClient, ConverseResult, GenerationParams
from delphi.agent.telemetry import DecisionRecord, TelemetrySink
from delphi.router.baselines import AlwaysModelRouter

TODAYS_KEYS = [
    "request_id",
    "arm",
    "chosen_model",
    "scores",
    "decision_latency_ms",
    "output",
    "usage",
    "cost_usd",
    "is_fallback",
]


class FakeConverse(ConverseClient):
    def converse(self, model_id: str, prompt: str, params: GenerationParams) -> ConverseResult:
        return ConverseResult(text="ok", input_tokens=3, output_tokens=2, model_id=model_id)


class OrderedSink(TelemetrySink):
    """Captures by identity and records when emission happened relative to assembly."""

    def __init__(self):
        self.records: list[DecisionRecord] = []
        self.trace: list[str] = []

    def emit(self, record: DecisionRecord) -> None:
        self.records.append(record)
        self.trace.append("emit")


def _agent(pool, sink, *, arm="router", alias="mid"):
    if arm.startswith("pinned"):
        return RouterAgent(
            pool, AlwaysModelRouter(pool, alias), FakeConverse(), sink,
            arm=arm, pinned_alias=alias,
        )
    return RouterAgent(pool, AlwaysModelRouter(pool, alias), FakeConverse(), sink, arm=arm)


def test_key_set_is_todays_nine_plus_exactly_two(pool):
    """T-AR-1."""
    sink = OrderedSink()
    out = _agent(pool, sink).handle_request({"prompt": "hi"})
    assert set(out) == set(TODAYS_KEYS) | {"schema_version", "decision_record"}


def test_key_order_is_preserved(pool):
    """T-AR-2. An earlier contract draft moved cost_usd and dropped two keys."""
    sink = OrderedSink()
    out = _agent(pool, sink).handle_request({"prompt": "hi"})
    assert list(out)[: len(TODAYS_KEYS)] == TODAYS_KEYS


def test_decision_record_is_a_mapping_not_a_string(pool):
    """T-AR-3. to_json() returns a STRING; double-encoding would break jq and C1's $ref."""
    sink = OrderedSink()
    out = _agent(pool, sink).handle_request({"prompt": "hi"})
    assert isinstance(out["decision_record"], dict)
    assert out["decision_record"]["request_id"] == out["request_id"]


def test_the_record_is_built_once_not_twice(pool, monkeypatch):
    """T-AR-4. Field equality cannot detect a second construction, so COUNT constructions.

    Two separate DecisionRecord instances built from the same inputs compare equal, so an
    equality assertion passes on the very defect BR1.1 forbids.
    """
    import dataclasses

    import delphi.agent.app as app_mod

    built: list[DecisionRecord] = []
    real = app_mod.DecisionRecord

    def counting(*args, **kwargs):
        rec = real(*args, **kwargs)
        built.append(rec)
        return rec

    monkeypatch.setattr(app_mod, "DecisionRecord", counting)

    sink = OrderedSink()
    out = _agent(pool, sink).handle_request({"prompt": "hi"})

    assert len(built) == 1, f"record constructed {len(built)} times"
    (emitted,) = sink.records
    assert emitted is built[0], "the sink got a different instance"
    assert out["decision_record"] == dataclasses.asdict(built[0])


def test_emission_precedes_response_assembly(pool):
    """T-AR-5. The sink must never be missing a record the caller already holds.

    Proven by ordering rather than by a flag on the agent: a sink that raises means the
    caller gets no response at all, which can only happen if emit runs first.
    """

    class RaisingSink(TelemetrySink):
        def __init__(self):
            self.called = False

        def emit(self, record: DecisionRecord) -> None:
            self.called = True
            raise RuntimeError("sink down")

    sink = RaisingSink()
    agent = RouterAgent(
        pool, AlwaysModelRouter(pool, "mid"), FakeConverse(), sink, arm="router"
    )
    with pytest.raises(RuntimeError, match="sink down"):
        agent.handle_request({"prompt": "hi"})
    assert sink.called is True


def test_schema_version_is_the_envelopes_not_the_records(pool):
    """T-AR-6. C1's addition is U1's; C2's field of the same name is U2's. Both exist, and
    neither is derived from the other."""
    from delphi.agent.app import RESPONSE_SCHEMA_VERSION

    sink = OrderedSink()
    out = _agent(pool, sink).handle_request({"prompt": "hi"})
    assert out["schema_version"] == RESPONSE_SCHEMA_VERSION
    assert out["decision_record"]["schema_version"] == 1


@pytest.mark.parametrize("arm", ["router", "pinned:mid"])
def test_scores_is_always_a_mapping(pool, arm):
    """T-AR-7. components.md says "null"; the code says {} and C1/C2 supersede the note."""
    sink = OrderedSink()
    out = _agent(pool, sink, arm=arm).handle_request({"prompt": "hi"})
    assert isinstance(out["scores"], dict)
    assert isinstance(out["decision_record"]["scores"], dict)
