"""Timing, timestamp and cold start — U2 LC-U2-1, LC-U2-2, LC-U2-5 (BR4.1-BR4.6, BR5.1-BR5.3).

All clocks are injected fakes (BR4.6). The agent and the router share one fake monotonic
clock, so the nesting embed <= decision <= routing_overhead is checked on one clock, as it
holds in production on time.perf_counter. No figure here is a measurement: every number is
a fake-clock advance chosen by the test, and none says anything about the NFR1 budget.

  T-LAT-U2-1   a fake clock advancing 5 ms in routing and 1000 ms inside the fake Converse
               gives routing_overhead_ms == 5 (not 1005, not 0) (BR4.1, NFR1.1)
  T-LAT-U2-2   50 ms inside the prompt-digest helper and 50 ms inside query_cost leave the
               figure at 5; the digest is the prompt's SHA-256 (NFR1.4, SD-U2-1, P-2)
  T-LAT-U2-3   a LearnedRouter whose fake embedder advances 2 ms gives
               Decision.metadata["embed_ms"] == 2; scores keys equal the pool aliases; no
               "__" key (BR4.3, T-RTR-2)
  T-LAT-U2-4   parametrised by EMBED_FIGURE_FIELD: exactly one record field equals 2; the
               other is absent or 0.0 (NFR1.5, SD-U2-4, P-1)
  T-LAT-U2-5   over 50 seeded fake-clock runs: embed <= decision <= routing_overhead wherever
               non-null (BR4.4)
  T-LAT-U2-6   the fake wall clock 2026-10-19T08:15:30.123456Z gives exactly
               2026-10-19T08:15:30.123Z on a success record and on a rejection record (BR5.1)
  T-LAT-U2-7   sequential calls give cold_start true, false, false (BR5.2)
  T-LAT-U2-8   eight threads released together by a threading.Barrier: exactly one record
               carries cold_start true (BR5.2, NFR4.3)
  T-LAT-U2-9   the first call rejected (missing prompt), the second succeeds: true, then false
               (BR5.3)
  T-LAT-U2-10  the agent's fallback record (U4 BRK-04) and a router with no embedder carry no
               embed figure; the record's embed figure is null (ENT-209, BR4.3)
  T-LAT-U2-11  the autouse socket guard is active (NFR4.2)
  T-LAT-U2-12  with a fake embedder advancing 2 ms and a fake scorer advancing 7 ms, the embed
               figure is exactly 2 (it excludes the scoring step) and the decision figure is
               exactly 9 (embed + score), so embed < decision strictly - on the Decision and
               on the emitted record (BR4.3, BR4.4, review R-01)

Partition: stdlib and the package only. No SDK call, no account.
"""
from __future__ import annotations

import hashlib
import importlib
import random
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import threading

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest
from u2_support import (
    CountingConverse,
    FakeClock,
    TimedRouter,
    fake_wall,
    learned_router,
    record_dict,
)

from delphi.agent.telemetry import InMemorySink
from delphi.router.baselines import AlwaysModelRouter

STAMP = "2026-10-19T08:15:30.123Z"


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_routing_timing.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _agent(pool, router, *, clock, converse=None, sink=None, arm="router"):
    from delphi.agent.app import RouterAgent

    kwargs = dict(arm=arm, mono=clock, wall=fake_wall)
    if arm.startswith("pinned"):
        kwargs["pinned_alias"] = arm.split(":", 1)[1]
    return RouterAgent(pool, router, converse or CountingConverse(), sink or InMemorySink(),
                       **kwargs)


def test_t_lat_u2_1_the_window_closes_before_generation(pool):
    clock, sink = FakeClock(), InMemorySink()
    agent = _agent(pool, TimedRouter(pool, clock, 5.0), clock=clock, sink=sink,
                   converse=CountingConverse(clock=clock, advance_ms=1000.0))
    agent.handle_request({"prompt": "hi"})
    (rec,) = sink.records
    assert rec.routing_overhead_ms == pytest.approx(5.0)


def test_t_lat_u2_2_digest_and_cost_sit_outside_the_window(pool, monkeypatch):
    app = importlib.import_module("delphi.agent.app")
    clock, sink = FakeClock(), InMemorySink()
    real_digest, real_cost = app._prompt_digest, app.query_cost

    def slow_digest(encoded):
        clock.advance_ms(50.0)
        return real_digest(encoded)

    def slow_cost(pool_, usage):
        clock.advance_ms(50.0)
        return real_cost(pool_, usage)

    monkeypatch.setattr(app, "_prompt_digest", slow_digest)
    monkeypatch.setattr(app, "query_cost", slow_cost)
    _agent(pool, TimedRouter(pool, clock, 5.0), clock=clock, sink=sink).handle_request(
        {"prompt": "hello oracle"})
    (rec,) = sink.records
    assert rec.routing_overhead_ms == pytest.approx(5.0)
    assert rec.prompt_sha256 == hashlib.sha256(b"hello oracle").hexdigest()


def test_t_lat_u2_3_the_embed_figure_is_metadata_not_a_score(pool):
    clock = FakeClock()
    d = learned_router(pool, clock=clock, embed_ms=2.0).predict("hi")
    assert d.metadata["embed_ms"] == pytest.approx(2.0)
    assert set(d.scores) == set(pool.aliases)
    assert not [k for k in d.scores if k.startswith("__")]


def test_t_lat_u2_4_exactly_one_field_carries_the_embed_figure(pool):
    tel = importlib.import_module("delphi.agent.telemetry")
    field = tel.EMBED_FIGURE_FIELD
    clock, sink = FakeClock(), InMemorySink()
    _agent(pool, learned_router(pool, clock=clock, embed_ms=2.0), clock=clock,
           sink=sink).handle_request({"prompt": "hi"})
    rec = record_dict(sink.records[0])
    candidates = {
        "embed_latency_ms": rec.get("embed_latency_ms"),
        "latency_breakdown.embed_ms": (rec.get("latency_breakdown") or {}).get("embed_ms"),
    }
    carrying = [k for k, v in candidates.items() if v is not None and v == pytest.approx(2.0)]
    assert carrying == [field]
    for other, value in candidates.items():
        if other != field:
            assert value in (None, 0.0), (other, value)


def test_t_lat_u2_5_the_three_figures_nest(pool):
    for seed in range(50):
        rng = random.Random(seed)  # noqa: S311 - deterministic fixture values, not crypto
        clock, sink = FakeClock(), InMemorySink()
        router = learned_router(pool, clock=clock, embed_ms=rng.uniform(0, 20),
                                score_ms=rng.uniform(0, 20))
        converse = CountingConverse(clock=clock, advance_ms=rng.uniform(0, 500))
        _agent(pool, router, clock=clock, converse=converse, sink=sink).handle_request(
            {"prompt": f"q{seed}"})
        (rec,) = sink.records
        assert rec.embed_latency_ms <= rec.decision_latency_ms + 1e-9, seed
        assert rec.decision_latency_ms <= rec.routing_overhead_ms + 1e-9, seed


def test_t_lat_u2_6_timestamp_is_rfc3339_utc_milliseconds(pool):
    from delphi.agent.app import RequestRejected

    sink = InMemorySink()
    agent = _agent(pool, AlwaysModelRouter(pool, "mid"), clock=FakeClock(), sink=sink)
    agent.handle_request({"prompt": "hi"})
    with pytest.raises(RequestRejected):
        agent.handle_request({"prompt": ""})
    assert [r.timestamp for r in sink.records] == [STAMP, STAMP]


def test_t_lat_u2_7_cold_start_is_true_exactly_once_in_sequence(pool):
    sink = InMemorySink()
    agent = _agent(pool, AlwaysModelRouter(pool, "mid"), clock=FakeClock(), sink=sink)
    for _ in range(3):
        agent.handle_request({"prompt": "hi"})
    assert [r.cold_start for r in sink.records] == [True, False, False]


def test_t_lat_u2_8_concurrent_first_calls_give_exactly_one_cold_start(pool):
    sink = InMemorySink()
    agent = _agent(pool, AlwaysModelRouter(pool, "mid"), clock=FakeClock(), sink=sink)
    barrier = threading.Barrier(8)
    errors: list[BaseException] = []

    def call():
        try:
            barrier.wait()
            agent.handle_request({"prompt": "hi"})
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(r.cold_start for r in sink.records) == [False] * 7 + [True]


def test_t_lat_u2_9_a_failed_first_call_still_consumes_the_latch(pool):
    from delphi.agent.app import RequestRejected

    sink = InMemorySink()
    agent = _agent(pool, AlwaysModelRouter(pool, "mid"), clock=FakeClock(), sink=sink)
    with pytest.raises(RequestRejected):
        agent.handle_request({"request_id": "no-prompt"})
    agent.handle_request({"prompt": "hi"})
    assert [r.cold_start for r in sink.records] == [True, False]


def test_t_lat_u2_10_no_encoding_step_means_no_embed_figure(pool):
    def boom(_features, _aliases):
        raise RuntimeError("scorer exploded")

    clock = FakeClock()
    # U4 (BRK-04): the fallback is the agent's, so it is observed on the emitted record.
    fell = InMemorySink()
    _agent(pool, learned_router(pool, clock=clock, scorer=boom), clock=clock,
           sink=fell).handle_request({"prompt": "hi"})
    assert fell.records[0].is_fallback and fell.records[0].embed_latency_ms is None
    bare = learned_router(pool, clock=clock, embed_ms=None)
    assert "embed_ms" not in bare.predict("hi").metadata
    sink = InMemorySink()
    _agent(pool, bare, clock=clock, sink=sink).handle_request({"prompt": "hi"})
    assert sink.records[0].embed_latency_ms is None
    assert sink.records[0].decision_latency_ms is not None


def test_t_lat_u2_11_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()


def test_t_lat_u2_12_the_embed_figure_excludes_scoring(pool):
    clock = FakeClock()
    d = learned_router(pool, clock=clock, embed_ms=2.0, score_ms=7.0).predict("hi")
    assert d.metadata["embed_ms"] == pytest.approx(2.0)
    assert d.latency_ms == pytest.approx(9.0)
    assert d.metadata["embed_ms"] < d.latency_ms

    clock, sink = FakeClock(), InMemorySink()
    _agent(pool, learned_router(pool, clock=clock, embed_ms=2.0, score_ms=7.0), clock=clock,
           sink=sink).handle_request({"prompt": "hi"})
    (rec,) = sink.records
    assert rec.embed_latency_ms == pytest.approx(2.0)
    assert rec.decision_latency_ms == pytest.approx(9.0)
    assert rec.embed_latency_ms < rec.decision_latency_ms
