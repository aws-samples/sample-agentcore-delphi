"""The request-path fallback lives in the agent — U4 LC-U4-1, LC-U4-2, LC-U4-3, LC-U4-8.

Before U4 the fallback was a method on one router class, reached by an optional-method probe,
so any other router's failure escaped `handle_request` as a 5xx. After U4 `RouterAgent._route`
calls `router.predict` and the agent's own decision check inside one guarded region, and any
failure becomes the agent's fallback decision, for every router type (ADR-005, BR1.1-BR1.8).

  T-BND-U4-1   per router type - AlwaysModelRouter built over a wider pool (an alias the agent's
               pool does not hold), RaisingRouter, a LearnedRouter whose scorer raises, and a
               reader-style subclass: the envelope is returned, is_fallback true, error_kind
               null, the record passes U2's C2 validator, exactly one router_fallback WARNING
               line, chosen_model_id is the fallback alias's id, and cost_usd equals
               query_cost for that alias (NFR7.1). A bare RaisingRouter never yields
               routing_failed (BR1.1, BR1.4, BR1.7, AC2.5.3)
  T-BND-U4-2   a malformed result - (a) an out-of-pool alias, (b) a model id that does not match
               its alias, (c) a non-Decision, plus a negative and a NaN embed_ms - each falls
               back, and the fake Converse sees only the fallback model id; (d) a patched
               `_check_decision` that raises is reported as a fallback, not routing_failed
               (BR1.2, P-2, D-CG-U4-11)
  T-BND-U4-3   fallback alias: the default is the strongest by input + output price, the first
               in pool order on a tie; an explicit alias is honoured; an unknown alias raises
               ValueError at construction; an AST scan of agent/app.py finds the
               `price.input + price.output` derivation in exactly one function (BR1.3)
  T-BND-U4-4   the names `strongest_alias` and `predict_with_fallback` appear nowhere in src/,
               scripts/, tests/ (this file excluded), docs/ or README.md; neither
               LearnedRouter.__init__ nor load_learned_router takes strongest_alias (BR1.5,
               NFR4.1)
  T-BND-U4-5   (a) RaisingRouter plus a patched `_build_fallback` that raises: routing_failed,
               chosen_* null, the PATCHED exception re-raised with the router's exception as
               its __context__, one request_failed ERROR line; (b) a patched `_routing_facts`
               that raises after a valid decision: routing_failed (BR1.6, SD-U4-2, nfr-design
               R-01)
  T-BND-U4-6   RouterError's docstring names RouterAgent and no longer says "The agent catches
               this" (BR2.1)
  T-BND-U4-9   `_load_router_arm` over an artifact generated into tmp_path from
               config/models_realpool.yaml: a LearnedRouter; the agent's fallback alias is the
               pool's strongest; the router's artifact_hash equals the manifest's; a raising
               scorer gives a C2-valid fallback record; the `def _load_router_arm` line carries
               no coverage pragma (FR6.2.1, functional-design R-02)
  T-BND-U4-10  a fake clock that advances only inside `router.predict` gives a
               routing_overhead_ms equal to that advance, for a valid decision and for a
               raising router; a valid decision writes zero delphi.agent lines. The advance is
               a fixture value, not a measurement (NFR1.1, SD-U4-7)
  T-BND-U4-12  a valid 200-warm NFR1 sample plus one fallback record, in three shapes
               (warm-eligible, cold_start true, error_kind generation_failed): each lands in
               CountedSample.fallback only, and check_sample raises naming "fallback" (NFR1.2,
               ND-5, nfr-requirements R-03)
  T-BND-U4-13  a pinned agent whose pinned_alias is not in the pool raises ValueError at
               construction, and no record is emitted (ND-1, SD-U4-2)
  guard        the autouse socket guard is active (NFR4.3). Keep-fixed by construction

New symbols are reached through the module object inside each test (D-CG-U4-5), so a red
run reports one failure per test rather than one collection error.

Partition: stdlib, numpy and the package. No SDK call, no account.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib
import inspect
import json
import logging
import pathlib
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)
import botocore.config  # noqa: F401 - as above
import pytest
from u2_support import (  # noqa: F401 - failure_lines is a fixture
    CountingConverse,
    FakeClock,
    RaisingRouter,
    TimedRouter,
    c2_problems,
    failure_lines,
    learned_router,
    record_dict,
)
from u4_support import MalformedRouter, RaisingTimedRouter, ReaderError, ReaderRouter, wider_pool

from delphi.agent.telemetry import InMemorySink
from delphi.eval.cost import Usage, query_cost
from delphi.router.baselines import AlwaysModelRouter

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "src" / "delphi" / "agent" / "app.py"
REALPOOL = ROOT / "config" / "models_realpool.yaml"
LINE_KEYS = {"event", "request_id", "arm", "error_kind", "detail"}
#: Assembled at runtime, so the strings occur only in this file's T-BND-U4-4 search terms.
REMOVED_NAMES = ("strongest" + "_alias", "predict" + "_with_fallback")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_agent_fallback.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _app():
    return importlib.import_module("delphi.agent.app")


def _agent(pool, router, *, converse=None, sink=None, **kwargs):
    return _app().RouterAgent(pool, router, converse or CountingConverse(),
                              sink if sink is not None else InMemorySink(), **kwargs)


def _boom(_features, _aliases):
    raise RuntimeError("scorer exploded")


ROUTER_TYPES = {
    "always-model over a wider pool": lambda pool: AlwaysModelRouter(wider_pool(pool), "ghost"),
    "raising router": lambda pool: RaisingRouter(pool),
    "learned, raising scorer": lambda pool: learned_router(pool, scorer=_boom),
    "reader subclass": lambda pool: ReaderRouter(pool),
}


# --------------------------------------------------------------------- T-BND-U4-1
@pytest.mark.parametrize("name", sorted(ROUTER_TYPES))
def test_t_bnd_u4_1_every_router_type_falls_back(pool, name, failure_lines):
    router = ROUTER_TYPES[name](pool)
    sink, fake = InMemorySink(), CountingConverse(input_tokens=11, output_tokens=7)
    agent = _agent(pool, router, converse=fake, sink=sink)
    out = agent.handle_request({"prompt": "hello there", "request_id": "r-bnd-1"})
    (rec,) = sink.records
    assert rec.chosen_alias == agent.fallback_alias, "the fallback is the agent's"
    assert out["is_fallback"] is True and out["decision_record"] == dataclasses.asdict(rec)
    assert rec.is_fallback is True and rec.error_kind is None
    assert c2_problems(record_dict(rec)) == [], c2_problems(record_dict(rec))
    strong = pool.by_alias("strong")
    assert (rec.chosen_alias, rec.chosen_model_id) == ("strong", strong.id)
    assert [model for model, _, _ in fake.calls] == [strong.id]
    assert rec.cost_usd == query_cost(pool, Usage("strong", 11, 7))
    assert set(rec.scores) == set(pool.aliases) and set(rec.scores.values()) == {0.0}
    (line,) = failure_lines.records()
    data = json.loads(line.getMessage())
    assert line.levelno == logging.WARNING
    assert (data["event"], data["request_id"], data["error_kind"]) == (
        "router_fallback", "r-bnd-1", None)
    assert set(data) == LINE_KEYS
    assert "hello there" not in line.getMessage()


def test_t_bnd_u4_1_a_bare_raising_router_never_yields_routing_failed(pool):
    sink = InMemorySink()
    for router in (RaisingRouter(pool), ReaderRouter(pool)):
        _agent(pool, router, sink=sink).handle_request({"prompt": "hi"})
    assert [r.error_kind for r in sink.records] == [None, None]
    assert all(r.is_fallback for r in sink.records)


# --------------------------------------------------------------------- T-BND-U4-2
@pytest.mark.parametrize("kind", MalformedRouter.KINDS)
def test_t_bnd_u4_2_a_malformed_decision_falls_back(pool, kind, failure_lines):
    sink, fake = InMemorySink(), CountingConverse()
    _agent(pool, MalformedRouter(pool, kind), converse=fake, sink=sink).handle_request(
        {"prompt": "hi"})
    (rec,) = sink.records
    strong = pool.by_alias("strong")
    assert [model for model, _, _ in fake.calls] == [strong.id], "only the fallback id"
    assert (rec.is_fallback, rec.error_kind, rec.chosen_model_id) == (True, None, strong.id)
    assert c2_problems(record_dict(rec)) == []
    assert [d["event"] for d in failure_lines.json()] == ["router_fallback"]


def test_t_bnd_u4_2_d_a_defect_in_the_decision_check_is_a_fallback(pool, monkeypatch):
    app = _app()

    def broken_check(_decision, _pool):
        raise RuntimeError("check defect")

    monkeypatch.setattr(app, "_check_decision", broken_check)
    sink = InMemorySink()
    _agent(pool, AlwaysModelRouter(pool, "mid"), sink=sink).handle_request({"prompt": "hi"})
    (rec,) = sink.records
    assert (rec.is_fallback, rec.error_kind, rec.chosen_alias) == (True, None, "strong")


# --------------------------------------------------------------------- T-BND-U4-3
def test_t_bnd_u4_3_one_fallback_alias_resolved_at_construction(pool):
    app = _app()
    assert _agent(pool, RaisingRouter(pool)).fallback_alias == "strong"
    sink = InMemorySink()
    agent = _agent(pool, RaisingRouter(pool), sink=sink, fallback_alias="mid")
    assert agent.fallback_alias == "mid"
    agent.handle_request({"prompt": "hi"})
    assert sink.records[0].chosen_alias == "mid"
    with pytest.raises(ValueError):
        _agent(pool, RaisingRouter(pool), fallback_alias="ghost")
    # a tie resolves to the first in pool order
    tie = dataclasses.replace(pool, candidates=tuple(
        dataclasses.replace(c, price=pool.by_alias("strong").price) for c in pool.candidates))
    assert app._resolve_fallback_alias(tie, None) == tie.aliases[0]
    assert derivation_functions(APP.read_text()) == ["_resolve_fallback_alias"]


def derivation_functions(source: str) -> list[str]:
    """Functions holding a `<x>.price.input + <y>.price.output` sum (BR1.3's one site)."""
    def is_price(node, attr):
        return (isinstance(node, ast.Attribute) and node.attr == attr
                and isinstance(node.value, ast.Attribute) and node.value.attr == "price")

    out = []
    for fn in [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)]:
        if any(isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add)
               and is_price(n.left, "input") and is_price(n.right, "output")
               for n in ast.walk(fn)):
            out.append(fn.name)
    return out


# --------------------------------------------------------------------- T-BND-U4-4
def removed_name_sites(root: pathlib.Path, exclude: set[pathlib.Path]) -> list[str]:
    hits = []
    paths = [root / "README.md"]
    for d in ("src", "scripts", "tests", "docs"):
        paths += sorted(p for p in (root / d).rglob("*")
                        if p.is_file() and "__pycache__" not in p.parts)
    for p in paths:
        if p.resolve() in exclude:
            continue
        try:
            text = p.read_text()
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(name in line for name in REMOVED_NAMES):
                hits.append(f"{p.relative_to(root)}:{n}")
    return hits


def test_t_bnd_u4_4_one_fallback_implementation_no_dead_parameter():
    from delphi.router.learned import LearnedRouter
    from delphi.train.artifact_store import load_learned_router

    assert removed_name_sites(ROOT, {pathlib.Path(__file__).resolve()}) == []
    assert REMOVED_NAMES[0] not in inspect.signature(LearnedRouter.__init__).parameters
    assert REMOVED_NAMES[0] not in inspect.signature(load_learned_router).parameters
    assert not hasattr(LearnedRouter, REMOVED_NAMES[1])


# --------------------------------------------------------------------- T-BND-U4-5
def test_t_bnd_u4_5_a_failing_fallback_builder_is_routing_failed(pool, monkeypatch,
                                                                 failure_lines):
    app = _app()
    raised: list[BaseException] = []

    def broken_builder(_agent, _req, exc):
        err = RuntimeError("fallback builder defect")
        raised.append(err)
        raise err

    monkeypatch.setattr(app, "_build_fallback", broken_builder)
    router_exc = ReaderError("router said no")
    router = RaisingRouter(pool, lambda _q: router_exc)
    sink = InMemorySink()
    with pytest.raises(RuntimeError) as info:
        _agent(pool, router, sink=sink).handle_request({"prompt": "hi"})
    assert info.value is raised[0], "the patched exception propagates"
    assert info.value.__context__ is router_exc, "the router's error stays reachable"
    (rec,) = sink.records
    assert rec.error_kind == "routing_failed"
    assert (rec.chosen_alias, rec.chosen_model_id) == (None, None)
    (line,) = failure_lines.records()
    data = json.loads(line.getMessage())
    assert (line.levelno, data["event"], data["error_kind"]) == (
        logging.ERROR, "request_failed", "routing_failed")


def test_t_bnd_u4_5_b_a_failing_routing_facts_step_is_routing_failed(pool, monkeypatch):
    app = _app()

    def broken_facts(_decision):
        raise RuntimeError("facts defect")

    monkeypatch.setattr(app, "_routing_facts", broken_facts)
    sink = InMemorySink()
    with pytest.raises(RuntimeError):
        _agent(pool, AlwaysModelRouter(pool, "mid"), sink=sink).handle_request(
            {"prompt": "hi"})
    assert sink.records[0].error_kind == "routing_failed"


# --------------------------------------------------------------------- T-BND-U4-6
def test_t_bnd_u4_6_router_error_names_the_layer_that_falls_back():
    from delphi.router.base import RouterError

    doc = RouterError.__doc__ or ""
    assert "RouterAgent" in doc
    assert "The agent catches this" not in doc


# --------------------------------------------------------------------- T-BND-U4-9
@pytest.fixture
def realpool():
    from delphi.config import load_pool

    load_pool.cache_clear()
    p = load_pool(REALPOOL)
    load_pool.cache_clear()
    return p


def test_t_bnd_u4_9_the_serving_wiring_runs_offline(tmp_path, realpool, monkeypatch):
    from delphi.router.learned import LearnedRouter
    from delphi.train.generate import build

    app = _app()
    out, _art = build(REALPOOL, out_dir=tmp_path / "art")
    manifest = json.loads((out / "manifest.json").read_text())
    sink = InMemorySink()
    agent = app._load_router_arm(realpool, str(out), CountingConverse(), sink)
    assert isinstance(agent.router, LearnedRouter)
    strongest = max(realpool.candidates,
                    key=lambda c: (c.price.input + c.price.output)).alias  # oracle for the test
    assert agent.fallback_alias == strongest
    assert agent.router.artifact.artifact_hash == manifest["artifact_hash"]
    monkeypatch.setattr(agent.router, "_scorer", _boom)
    agent.handle_request({"prompt": "hi"})
    (rec,) = sink.records
    assert rec.is_fallback is True and rec.chosen_alias == strongest
    assert c2_problems(record_dict(rec)) == []
    (line,) = [ln for ln in APP.read_text().splitlines() if ln.startswith("def _load_router_arm")]
    assert "pragma: no cover" not in line


# --------------------------------------------------------------------- T-BND-U4-10
def test_t_bnd_u4_10_the_routing_window_gains_no_io(pool, failure_lines):
    from delphi.agent.app import RouterAgent

    for router_of in (lambda c: TimedRouter(pool, c, 5.0),
                      lambda c: RaisingTimedRouter(pool, c, 5.0)):
        clock, sink = FakeClock(), InMemorySink()
        agent = RouterAgent(pool, router_of(clock), CountingConverse(clock=clock, advance_ms=900),
                            sink, arm="router", mono=clock)
        agent.handle_request({"prompt": "hi"})
        assert sink.records[0].routing_overhead_ms == pytest.approx(5.0)
    events = [d["event"] for d in failure_lines.json()]
    assert events == ["router_fallback"], "the valid decision wrote no delphi.agent line"
    assert sink.records[0].is_fallback is True


# --------------------------------------------------------------------- T-BND-U4-12
DIGESTS = [hashlib.sha256(f"fixture prompt {i}".encode()).hexdigest() for i in range(5)]


def _record(digest, overhead, **over) -> dict:
    rec = {"schema_version": 1, "request_id": "r", "arm": "router",
           "timestamp": "2026-10-19T08:15:30.123Z", "cold_start": False,
           "routing_overhead_ms": overhead, "error_kind": None, "error_detail": None,
           "prompt_sha256": digest, "is_fallback": False}
    rec.update(over)
    return rec


def _manifest() -> dict:
    return {"workload": "DEMO_PROMPTS (5 prompts)", "prompt_digests": list(DIGESTS),
            "passes": 40, "n": 200, "percentile_method": "nearest-rank", "percentile": 90,
            "applies_to": "warm", "arm": "router", "record_schema_version": 1,
            "captured_on": "2026-10-20", "region": "us-west-2", "published_p90_ms": 180.0,
            "published_p90_decimals": 1}


FALLBACK_SHAPES = {
    "warm-eligible": {},
    "cold start": {"cold_start": True},
    "generation failed": {"error_kind": "generation_failed", "error_detail": "X: y"},
}


@pytest.mark.parametrize("shape", sorted(FALLBACK_SHAPES))
def test_t_bnd_u4_12_a_fallback_record_cannot_enter_the_sample(shape):
    ls = importlib.import_module("delphi.eval.latency_sample")
    warm = [_record(DIGESTS[i % 5], float(i + 1)) for i in range(200)]
    assert ls.check_sample(warm, _manifest()) == 180.0, "the base sample is valid"
    bad = _record(DIGESTS[0], 3.0, is_fallback=True, **FALLBACK_SHAPES[shape])
    sample = ls.counted(warm + [bad])
    assert sample.fallback == [bad]
    assert bad not in sample.warm + sample.cold + sample.failed
    with pytest.raises(ls.SampleCheckError, match="fallback"):
        ls.check_sample(warm + [bad], _manifest())


# --------------------------------------------------------------------- T-BND-U4-13
def test_t_bnd_u4_13_a_pinned_alias_outside_the_pool_fails_at_construction(pool):
    sink = InMemorySink()
    with pytest.raises(ValueError):
        _agent(pool, AlwaysModelRouter(pool, "mid"), sink=sink, arm="pinned:nope",
               pinned_alias="nope")
    assert sink.records == []


# --------------------------------------------------------------------- guard
def test_u4_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
