"""Old and new record shapes through the U1 consumer — U2 BR10.1-BR10.4 (AC4.2.1; P-3).

Integration tier across the agent -> delphi-verify boundary: versioned records are produced by
the REAL `handle_request`, not written by hand, and fed through U1's real `validate`,
`render_human` and `render_json`. The legacy record is the thirteen-field shape U1's tests use.
A record without `schema_version` is legacy, and only there is `embed_latency_ms` the
structural 0.0 the renderer must annotate.

  T-RC-U2-1  verify.validate passes on three responses: a legacy one (the thirteen fields), a
             versioned router one and a versioned pinned one with null latencies; the test
             first asserts the versioned ones carry schema_version == 1 (BR10.1, AC4.2.1).
             Keep-fixed at its Red run, because Step 7 already produces versioned records;
             negative control: the same precondition over the legacy record fails
  T-RC-U2-2  render_human over the three exits without exception; the embed annotation
             appears ONLY on the legacy record at 0.0, and not on a versioned record seeded
             with embed_latency_ms == 0.0; its text names the absent schema_version; null
             figure lines are skipped (BR10.1, D-CG-U2-3)
  T-RC-U2-3  render_json passes each record through unmodified (BR10.1). Keep-fixed at its
             Red run, with T-RC-U2-1's negative control
  T-RC-U2-4  telemetry.py's and render.py's module docstrings no longer claim the embed field
             is structurally zero on every record, and telemetry.py's states the legacy /
             versioned distinction (BR10.4 (d), P-3)
  T-RC-U2-5  the autouse socket guard is active (NFR4.2)

Partition: stdlib and the package only. No SDK call, no account.
"""
from __future__ import annotations

import json
import pathlib
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest
from u2_support import CountingConverse, learned_router

from delphi.agent.telemetry import InMemorySink
from delphi.router.baselines import AlwaysModelRouter

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "delphi"
LEGACY_RECORD = {
    "request_id": "legacy-1", "arm": "router", "chosen_alias": "nova-lite",
    "chosen_model_id": "us.amazon.nova-lite-v1:0", "scores": {"nova-lite": 7.5},
    "embed_latency_ms": 0.0, "decision_latency_ms": 1.25, "input_tokens": 12,
    "output_tokens": 7, "cost_usd": 0.000123, "is_fallback": False, "artifact_hash": None,
    "extra": {},
}
LEGACY = {
    "request_id": "legacy-1", "arm": "router", "chosen_model": "nova-lite",
    "scores": {"nova-lite": 7.5}, "decision_latency_ms": 1.25, "output": "a prophecy",
    "usage": {"input_tokens": 12, "output_tokens": 7}, "cost_usd": 0.000123,
    "is_fallback": False, "schema_version": 1, "decision_record": LEGACY_RECORD,
}


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_record_compat.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _response(pool, *, arm: str, request_id: str) -> dict:
    from delphi.agent.app import RouterAgent

    if arm == "router":
        agent = RouterAgent(pool, learned_router(pool), CountingConverse(), InMemorySink())
    else:
        alias = arm.split(":", 1)[1]
        agent = RouterAgent(pool, AlwaysModelRouter(pool, alias), CountingConverse(),
                            InMemorySink(), arm=arm, pinned_alias=alias)
    return agent.handle_request({"prompt": "hi", "request_id": request_id})


def _assert_versioned(response: dict) -> None:
    assert response["decision_record"].get("schema_version") == 1


@pytest.fixture
def three(pool) -> dict[str, dict]:
    return {
        "legacy": LEGACY,
        "router": _response(pool, arm="router", request_id="v-router"),
        "pinned": _response(pool, arm="pinned:mid", request_id="v-pinned"),
    }


def test_t_rc_u2_1_validate_accepts_both_shapes(three):
    from delphi.verify.validate import validate

    _assert_versioned(three["router"])
    _assert_versioned(three["pinned"])
    assert three["pinned"]["decision_record"]["decision_latency_ms"] is None
    for response in three.values():
        assert validate(response, correlation_id=response["request_id"]) is None
    with pytest.raises(AssertionError):  # negative control: the precondition can fail
        _assert_versioned(three["legacy"])


def test_t_rc_u2_2_the_annotation_marks_legacy_records_only(three):
    from delphi.verify.render import EMBED_ANNOTATION, render_human

    out = {name: render_human(resp, elapsed_ms=10.0) for name, resp in three.items()}
    assert EMBED_ANNOTATION in out["legacy"]
    assert EMBED_ANNOTATION not in out["router"] and EMBED_ANNOTATION not in out["pinned"]
    zeroed = dict(three["router"], decision_record=dict(three["router"]["decision_record"],
                                                         embed_latency_ms=0.0))
    assert EMBED_ANNOTATION not in render_human(zeroed, elapsed_ms=10.0)
    assert "schema_version" in EMBED_ANNOTATION
    assert "decision latency:" not in out["pinned"] and "embed latency:" not in out["pinned"]
    assert "embed latency:" in out["router"]


def test_t_rc_u2_3_json_mode_passes_every_record_through(three):
    from delphi.verify.render import render_json

    for response in three.values():
        assert json.loads(render_json(response)) == response["decision_record"]


def test_t_rc_u2_4_module_docstrings_tell_the_post_u2_truth():
    from delphi.agent import telemetry
    from delphi.verify import render

    for module in (telemetry, render):
        doc = (module.__doc__ or "").lower()
        assert "structurally 0.0 in every record" not in doc, module.__name__
        assert "no router writes" not in doc, module.__name__
    tel_doc = telemetry.__doc__ or ""
    assert "legacy" in tel_doc and "schema_version" in tel_doc


def test_t_rc_u2_5_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
