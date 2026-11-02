"""The request path's one failure path and one success path — U2 LC-U2-8 (agent/app.py).

Every path through `handle_request` ends in exactly one DecisionRecord handed to the sink,
then either the C1 envelope (success) or a raise (failure). On failure the order is fixed:
build the record, emit it, write one `delphi.agent` line, raise (BR2.4). Rejections raise
RequestRejected; every other failure re-raises the ORIGINAL exception.

  T-REQ-U2-1   for every FR4.6.1 case: RequestRejected is raised carrying the record's
               error_kind; an ordered sink sees the emit before the raise; one
               request_rejected WARNING line; zero spy-router predict calls and zero Converse
               calls; no raw KeyError / TypeError escapes (BR3.5, BR2.4, FR4.6.1, FR4.6.3)
  T-REQ-U2-2   a raising Converse: record generation_failed, chosen_* set, tokens and cost
               null, the ORIGINAL exception re-raised, one request_failed ERROR line, emit
               before raise (W5, BR2.3, BR2.4)
  T-REQ-U2-3   a router with no fallback method that raises RouterError gets the AGENT's
               fallback (U4 BRK-15, inverted): no raise, is_fallback true, error_kind null,
               chosen_alias the agent's fallback alias. routing_failed now needs a failing
               fallback builder (W4, T-BND-U4-5)
  T-REQ-U2-4   the cost computation raises after a successful Converse: internal_error, tokens
               populated, cost_usd null (W6, BR2.3, FM-13)
  T-REQ-U2-5   is_fallback x generation failure: a non-fallback route then a raise gives
               false; a fallback route then a raise gives true; error_kind generation_failed in
               both (BR1.6)
  T-REQ-U2-6   success per arm (router, pinned:mid): the BR1.3 presence table; the pinned
               record has scores {}, the three latency figures null and artifact_hash null;
               the pinned ENVELOPE still carries decision_latency_ms 0.0 (BR1.3, BR4.2, BR10.3)
  T-REQ-U2-7   exactly one DecisionRecord construction per call on every workflow W1-W6 (and
               U4's W4f, BRK-16),
               counted by patching delphi.agent.app.DecisionRecord as T-AR-4 does (BR1.5,
               D-CG-U2-1)
  T-REQ-U2-8   the record of every workflow W1-W7 (W7 through record_json) passes the
               hand-written C2 validator (BR1.4, TS-U2-9, LC-U2-16)
  T-REQ-U2-9   the sink fails on the success path: one record_emit_failed ERROR line whose
               top-level keys are exactly the FR4.5.1 set plus record_json; no top-level
               scores or schema_version; json.loads(record_json) equals the record's JSON; the
               sink's exception is raised and no response returned (BR2.6, FR4.5.2, NR-1)
  T-REQ-U2-10  the sink fails on a failure path: exactly ONE line, record_emit_failed (not
               request_failed), carrying the failure's error_kind; the sink's exception is
               raised with the original as __context__ (FM-12)
  T-REQ-U2-11  a sentinel prompt echoed in a fake's exception message appears in no serialised
               record of W1-W7 and in no captured delphi.agent line (FR4.3.1, SD-U2-9);
               escaped-echo case: a multi-line, quote-carrying prompt echoed through
               KeyError(prompt) - whose str() is the repr-escaped prompt - leaves neither line
               of it in format_detail's output, the record's error_detail, its JSON, or the
               delphi.agent line; the JSON-escaped form is removed too (review R-02)
  T-REQ-U2-12  per error_kind: the line is on delphi.agent (never delphi.decision), at the
               ENT-206 level, with exactly the allowed key set; delphi.decision carries the
               record and nothing else (FR4.5.1, BR6.1, SD-U2-11)
  T-REQ-U2-13  a LearnedRouter whose scorer raises, and (U4 BRK-19) a RaisingRouter: one
               router_fallback WARNING line carrying the request_id; the record has
               is_fallback true and error_kind null; the invocation returns (BR6.2, FM-6)
  T-REQ-U2-14  a fake raising botocore ReadTimeoutError: error_detail starts with
               ReadTimeoutError; tokens and cost null (NFR7.4, SD-U2-8)
  T-REQ-U2-15  the autouse socket guard is active (NFR4.2)

Partition: stdlib, botocore exception classes, and the package. No SDK call, no account.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest
from botocore.exceptions import ReadTimeoutError
from u2_support import (  # noqa: F401 - failure_lines is a fixture
    CountingConverse,
    OrderedSink,
    RaisingConverse,
    RaisingRouter,
    RaisingSink,
    SpyRouter,
    c2_problems,
    failure_lines,
    learned_router,
    record_dict,
)

from delphi.agent.telemetry import InMemorySink, OTelSink
from delphi.router.baselines import AlwaysModelRouter

LINE_KEYS = {"event", "request_id", "arm", "error_kind", "detail"}
SENTINEL = "SENTINEL-7f3a9c the confidential prompt text"

REJECTIONS = {
    "not a mapping": ["prompt", "hi"],
    "prompt missing": {"request_id": "r1"},
    "prompt not a string": {"prompt": 42},
    "prompt whitespace": {"prompt": "   "},
    "lone surrogate": {"prompt": "x\ud800"},
    "102401 bytes": {"prompt": "a" * 102_401},
    "multi-byte over": {"prompt": "é" * 51_201},
    "request_id int": {"prompt": "hi", "request_id": 7},
    "request_id 257": {"prompt": "hi", "request_id": "x" * 257},
}


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_request_path.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _agent(pool, *, router=None, converse=None, sink=None, arm="router", alias="mid"):
    from delphi.agent.app import RouterAgent

    router = router or AlwaysModelRouter(pool, alias)
    converse = converse or CountingConverse()
    sink = sink if sink is not None else InMemorySink()
    if arm.startswith("pinned"):
        return RouterAgent(pool, router, converse, sink, arm=arm, pinned_alias=alias)
    return RouterAgent(pool, router, converse, sink, arm=arm)


def _rejected_type():
    from delphi.agent import app

    return app.RequestRejected


def _run(agent, payload, trace):
    """Call handle_request, appending "raise" to the shared trace when it raises."""
    try:
        return agent.handle_request(payload)
    except BaseException:
        trace.append("raise")
        raise


@pytest.mark.parametrize("case", sorted(REJECTIONS))
def test_t_req_u2_1_every_rejection_is_recorded_logged_and_free(pool, case, failure_lines):
    trace: list[str] = []
    sink, spy, fake = OrderedSink(trace), SpyRouter(pool), CountingConverse()
    agent = _agent(pool, router=spy, converse=fake, sink=sink)
    rejected = _rejected_type()
    with pytest.raises(rejected) as info:
        _run(agent, REJECTIONS[case], trace)
    assert type(info.value) is rejected and not isinstance(info.value, (KeyError, TypeError))
    (record,) = sink.records
    assert info.value.error_kind == record.error_kind
    assert record.error_kind in ("request_invalid", "request_too_large")
    assert trace == ["emit", "raise"]
    (line,) = failure_lines.records()
    assert line.levelno == logging.WARNING
    assert json.loads(line.getMessage())["event"] == "request_rejected"
    assert (spy.calls, len(fake.calls)) == (0, 0), "a rejected request spent nothing"


def test_t_req_u2_2_generation_failure_is_recorded_then_re_raised(pool, failure_lines):
    trace: list[str] = []
    sink, fake = OrderedSink(trace), RaisingConverse()
    with pytest.raises(RuntimeError) as info:
        _run(_agent(pool, converse=fake, sink=sink), {"prompt": "hi", "request_id": "r2"},
             trace)
    assert info.value is fake.raised[0], "the ORIGINAL exception, not a wrapper"
    (rec,) = sink.records
    assert rec.error_kind == "generation_failed"
    assert (rec.chosen_alias, rec.chosen_model_id) == ("mid", pool.by_alias("mid").id)
    assert (rec.input_tokens, rec.output_tokens, rec.cost_usd) == (None, None, None)
    assert rec.error_detail.startswith("RuntimeError: ThrottlingException")
    assert trace == ["emit", "raise"]
    (line,) = failure_lines.records()
    assert line.levelno == logging.ERROR
    assert json.loads(line.getMessage())["event"] == "request_failed"


def test_t_req_u2_3_routing_failure_without_a_fallback(pool):
    sink = InMemorySink()
    agent = _agent(pool, router=RaisingRouter(pool), sink=sink)
    out = agent.handle_request({"prompt": "hi"})
    (rec,) = sink.records
    assert out["is_fallback"] is True
    assert (rec.is_fallback, rec.error_kind, rec.error_detail) == (True, None, None)
    assert rec.chosen_alias == agent.fallback_alias == "strong"
    assert rec.chosen_model_id == pool.by_alias("strong").id


def test_t_req_u2_4_failure_after_generation_keeps_the_tokens(pool, monkeypatch):
    from delphi.agent import app

    def broken_cost(_pool, _usage):
        raise KeyError("no price for this alias")

    monkeypatch.setattr(app, "query_cost", broken_cost)
    sink = InMemorySink()
    with pytest.raises(KeyError):
        _agent(pool, sink=sink).handle_request({"prompt": "hi"})
    (rec,) = sink.records
    assert rec.error_kind == "internal_error"
    assert (rec.input_tokens, rec.output_tokens, rec.cost_usd) == (3, 2, None)
    assert rec.chosen_alias == "mid"


def test_t_req_u2_5_is_fallback_keeps_the_routing_steps_value(pool):
    def boom(_features, _aliases):
        raise RuntimeError("scorer exploded")

    for router, expected in ((AlwaysModelRouter(pool, "mid"), False),
                             (learned_router(pool, scorer=boom), True)):
        sink = InMemorySink()
        with pytest.raises(RuntimeError):
            _agent(pool, router=router, converse=RaisingConverse(),
                   sink=sink).handle_request({"prompt": "hi"})
        (rec,) = sink.records
        assert rec.error_kind == "generation_failed"
        assert rec.is_fallback is expected


def test_t_req_u2_6_success_presence_table_per_arm(pool):
    sink = InMemorySink()
    out = _agent(pool, router=learned_router(pool), sink=sink).handle_request({"prompt": "hi"})
    (rec,) = sink.records
    for name in ("chosen_alias", "chosen_model_id", "decision_latency_ms",
                 "routing_overhead_ms", "input_tokens", "output_tokens", "cost_usd",
                 "artifact_hash", "embed_latency_ms", "timestamp", "cold_start",
                 "prompt_sha256"):
        assert getattr(rec, name) is not None, f"router arm: {name} is null"
    assert set(rec.scores) == set(pool.aliases)
    assert (rec.error_kind, rec.error_detail, rec.schema_version) == (None, None, 1)
    assert out["decision_record"] == dataclasses.asdict(rec)

    sink = InMemorySink()
    out = _agent(pool, sink=sink, arm="pinned:mid").handle_request({"prompt": "hi"})
    (pinned,) = sink.records
    assert pinned.scores == {}
    assert (pinned.embed_latency_ms, pinned.decision_latency_ms,
            pinned.routing_overhead_ms, pinned.artifact_hash) == (None,) * 4
    assert pinned.is_fallback is False
    assert pinned.chosen_alias == "mid" and pinned.cost_usd is not None
    assert out["decision_latency_ms"] == 0.0, "the envelope is U1's and keeps 0.0 (BR10.3)"


def _broken_cost(_pool, _usage):
    """Stands in for delphi.agent.app.query_cost: the cost step fails after generation."""
    raise KeyError("no price for this alias")


def _broken_fallback(_agent, _req, _exc):
    """Stands in for delphi.agent.app._build_fallback: the fallback itself fails (U4 BR1.6)."""
    raise RuntimeError("fallback builder defect")


def _workflows(pool):
    """W1-W6 plus W4f as (name, make_agent, payload, expected exception or None, breaks_cost,
    breaks_fallback). After U4 a RaisingRouter alone falls back (W4f); W4's routing_failed is
    re-forced by patching the fallback builder to fail (BRK-16)."""
    return [
        ("W1 router success", lambda: _agent(pool, router=learned_router(pool)),
         {"prompt": "hi"}, None, False, False),
        ("W2 pinned success", lambda: _agent(pool, arm="pinned:mid"), {"prompt": "hi"},
         None, False, False),
        ("W3 rejected", lambda: _agent(pool), {"prompt": ""}, _rejected_type(), False, False),
        ("W4 routing failed", lambda: _agent(pool, router=RaisingRouter(pool)),
         {"prompt": "hi"}, Exception, False, True),
        ("W4f router fallback", lambda: _agent(pool, router=RaisingRouter(pool)),
         {"prompt": "hi"}, None, False, False),
        ("W5 generation failed", lambda: _agent(pool, converse=RaisingConverse()),
         {"prompt": "hi"}, RuntimeError, False, False),
        ("W6 internal error", lambda: _agent(pool), {"prompt": "hi"}, KeyError, True, False),
    ]


def _drive(agent, payload, raises):
    if raises is None:
        return agent.handle_request(payload)
    with pytest.raises(raises):
        agent.handle_request(payload)
    return None


def test_t_req_u2_7_exactly_one_construction_per_workflow(pool, monkeypatch):
    from delphi.agent import app

    real = app.DecisionRecord
    for name, make, payload, raises, breaks_cost, breaks_fallback in _workflows(pool):
        built: list = []

        def counting(*args, _built=built, **kwargs):
            rec = real(*args, **kwargs)
            _built.append(rec)
            return rec

        with monkeypatch.context() as m:
            if breaks_cost:
                m.setattr(app, "query_cost", _broken_cost)
            if breaks_fallback:
                m.setattr(app, "_build_fallback", _broken_fallback)
            m.setattr(app, "DecisionRecord", counting)
            _drive(make(), payload, raises)
        assert len(built) == 1, f"{name}: record constructed {len(built)} times"


def test_t_req_u2_8_every_workflow_record_is_c2_valid(pool, monkeypatch, failure_lines):
    from delphi.agent import app

    for name, make, payload, raises, breaks_cost, breaks_fallback in _workflows(pool):
        with monkeypatch.context() as m:
            if breaks_cost:
                m.setattr(app, "query_cost", _broken_cost)
            if breaks_fallback:
                m.setattr(app, "_build_fallback", _broken_fallback)
            agent = make()
            agent.sink = sink = InMemorySink()
            _drive(agent, payload, raises)
        (rec,) = sink.records
        assert c2_problems(record_dict(rec)) == [], (name, c2_problems(record_dict(rec)))
    # W7: the sink is down, so the record survives only inside record_json
    with pytest.raises(RuntimeError):
        _agent(pool, sink=RaisingSink()).handle_request({"prompt": "hi"})
    (line,) = [d for d in failure_lines.json() if d["event"] == "record_emit_failed"]
    assert c2_problems(json.loads(line["record_json"])) == []


def test_t_req_u2_9_sink_failure_on_success_logs_the_record(pool, failure_lines):
    sink = RaisingSink()
    with pytest.raises(RuntimeError) as info:
        _agent(pool, sink=sink).handle_request({"prompt": "hi", "request_id": "r9"})
    assert info.value is sink.raised[0]
    (line,) = failure_lines.records()
    assert line.levelno == logging.ERROR
    data = json.loads(line.getMessage())
    assert set(data) == LINE_KEYS | {"record_json"}
    assert data["event"] == "record_emit_failed" and data["error_kind"] is None
    assert data["request_id"] == "r9"
    assert "scores" not in data and "schema_version" not in data
    assert json.loads(data["record_json"]) == record_dict(sink.attempted[0])


def test_t_req_u2_10_sink_failure_on_a_failure_path_writes_one_line(pool, failure_lines):
    sink, fake = RaisingSink(), RaisingConverse()
    with pytest.raises(RuntimeError) as info:
        _agent(pool, sink=sink, converse=fake).handle_request({"prompt": "hi"})
    assert info.value is sink.raised[0], "the sink's exception is what propagates"
    assert info.value.__context__ is fake.raised[0], "the original stays reachable"
    lines = failure_lines.json()
    assert [d["event"] for d in lines] == ["record_emit_failed"]
    assert lines[0]["error_kind"] == "generation_failed"


def test_t_req_u2_11_no_prompt_text_in_any_record_or_line(pool, monkeypatch, failure_lines):
    from delphi.agent import app
    from delphi.router.base import RouterError

    def echo_scorer(features, _aliases):
        raise RuntimeError(f"scorer refused: {SENTINEL}")

    def echo_cost(_pool, _usage):
        raise KeyError(f"no price for {SENTINEL}")

    seen: list[str] = []
    runs = [
        (_agent(pool, router=learned_router(pool, embed_ms=None, scorer=echo_scorer)), None),
        (_agent(pool, arm="pinned:mid"), None),
        # U4 BRK-17: the agent's fallback absorbs it; the sentinel in the RouterError must
        # reach neither the fallback record nor the router_fallback line.
        (_agent(pool, router=RaisingRouter(pool, lambda q: RouterError(f"no route: {q}"))),
         None),
        (_agent(pool, converse=RaisingConverse(lambda p: RuntimeError(f"model saw {p}"))),
         RuntimeError),
        (_agent(pool, sink=RaisingSink()), RuntimeError),
    ]
    for agent, raises in runs:
        agent_sink = agent.sink if isinstance(agent.sink, InMemorySink) else None
        if raises:
            with pytest.raises(raises):
                agent.handle_request({"prompt": SENTINEL})
        else:
            agent.handle_request({"prompt": SENTINEL})
        if agent_sink is not None:
            seen += [r.to_json() for r in agent_sink.records]
    sink = InMemorySink()
    with pytest.raises(_rejected_type()):
        _agent(pool, sink=sink).handle_request({"prompt": SENTINEL + "x" * 102_400})
    monkeypatch.setattr(app, "query_cost", echo_cost)
    with pytest.raises(KeyError):
        _agent(pool, sink=sink).handle_request({"prompt": SENTINEL})
    seen += [r.to_json() for r in sink.records]
    seen += [r.getMessage() for r in failure_lines.records()]
    assert len(seen) >= 10
    assert not [s for s in seen if SENTINEL in s]



ESCAPED_PROMPT = 'first secret line\nsecond "secret" line\tend'


def test_t_req_u2_11_escaped_echo_of_a_multiline_prompt_is_redacted(pool, failure_lines):
    import json as _json

    from delphi.agent.telemetry import bound_detail, format_detail

    fragments = ("first secret line", 'second "secret" line', "second \\\"secret\\\" line")
    redact = (ESCAPED_PROMPT,)
    direct = format_detail(KeyError(ESCAPED_PROMPT), redact=redact)
    assert direct.startswith("KeyError: ")
    json_echo = bound_detail(f"echo {_json.dumps(ESCAPED_PROMPT)}", redact=redact)
    assert "[redacted]" in direct and "[redacted]" in json_echo

    sink = InMemorySink()
    agent = _agent(pool, sink=sink, converse=RaisingConverse(lambda p: KeyError(p)))
    with pytest.raises(KeyError):
        agent.handle_request({"prompt": ESCAPED_PROMPT})
    (rec,) = sink.records
    lines = [r.getMessage() for r in failure_lines.records()]
    assert lines
    for text in (direct, json_echo, rec.error_detail or "", rec.to_json(), *lines):
        for fragment in fragments:
            assert fragment not in text, (fragment, text)


LEVELS = {
    "request_invalid": (logging.WARNING, "request_rejected"),
    "request_too_large": (logging.WARNING, "request_rejected"),
    "routing_failed": (logging.ERROR, "request_failed"),
    "generation_failed": (logging.ERROR, "request_failed"),
    "internal_error": (logging.ERROR, "request_failed"),
}


@pytest.mark.parametrize("kind", sorted(LEVELS))
def test_t_req_u2_12_one_agent_line_per_kind_at_its_level(pool, kind, monkeypatch,
                                                          failure_lines):
    from delphi.agent import app

    payload, router, converse, raises = {"prompt": "hi"}, None, None, Exception
    if kind == "request_invalid":
        payload = {"prompt": 7}
    elif kind == "request_too_large":
        payload = {"prompt": "a" * 102_401}
    elif kind == "routing_failed":
        # U4 BRK-18: a RaisingRouter alone now falls back; the kind needs a failing builder.
        router = RaisingRouter(pool)
        monkeypatch.setattr(app, "_build_fallback", _broken_fallback)
    elif kind == "generation_failed":
        converse = RaisingConverse()
    else:
        monkeypatch.setattr(app, "query_cost", _broken_cost)
    # OTelSink installs its handler and INFO level only when `delphi.decision` has NO
    # handler, and the fixture has just attached caplog's. That install policy is OTelSink's
    # and outside U2's scope (`u2-decision-telemetry/nfr-design/logical-components.md` § 8),
    # so the level is set here instead.
    decision_log = logging.getLogger("delphi.decision")
    previous = decision_log.level
    decision_log.setLevel(logging.INFO)
    try:
        agent = _agent(pool, router=router, converse=converse, sink=OTelSink())
        with pytest.raises(raises):
            agent.handle_request(payload)
    finally:
        decision_log.setLevel(previous)
    (line,) = failure_lines.records("delphi.agent")
    level, event = LEVELS[kind]
    data = json.loads(line.getMessage())
    assert (line.levelno, data["event"], data["error_kind"]) == (level, event, kind)
    assert set(data) == LINE_KEYS
    decision = failure_lines.json("delphi.decision")
    assert len(decision) == 1 and decision[0]["error_kind"] == kind
    assert "event" not in decision[0]


def _scorer_boom(_features, _aliases):
    raise RuntimeError("scorer exploded")


# U4 BRK-19: kept, plus one RaisingRouter case - a non-learned router writing
# router_fallback is new behaviour.
@pytest.mark.parametrize("make", [
    lambda pool: learned_router(pool, scorer=_scorer_boom),
    lambda pool: RaisingRouter(pool, lambda _q: RuntimeError("scorer exploded")),
], ids=["learned", "raising"])
def test_t_req_u2_13_router_fallback_is_logged_by_the_agent(pool, make, failure_lines):
    sink = InMemorySink()
    out = _agent(pool, router=make(pool), sink=sink).handle_request(
        {"prompt": "hi", "request_id": "r13"})
    assert out["is_fallback"] is True
    (rec,) = sink.records
    assert rec.is_fallback is True and rec.error_kind is None
    (line,) = failure_lines.records()
    data = json.loads(line.getMessage())
    assert line.levelno == logging.WARNING
    assert (data["event"], data["request_id"]) == ("router_fallback", "r13")
    assert "scorer exploded" in data["detail"]
    assert set(data) == LINE_KEYS


def test_t_req_u2_14_a_read_timeout_is_countable(pool):
    sink = InMemorySink()
    fake = RaisingConverse(lambda _p: ReadTimeoutError(endpoint_url="https://bedrock.test"))
    with pytest.raises(ReadTimeoutError):
        _agent(pool, converse=fake, sink=sink).handle_request({"prompt": "hi"})
    (rec,) = sink.records
    assert rec.error_detail.startswith("ReadTimeoutError")
    assert (rec.input_tokens, rec.output_tokens, rec.cost_usd) == (None, None, None)


def test_t_req_u2_15_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
