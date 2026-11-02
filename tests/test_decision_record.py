"""DecisionRecord, the data model — U2 LC-U2-6 (agent/telemetry.py; ENT-201, ENT-203).

Written before the code under the team's tdd ordering. New symbols are reached through the
module object inside each test (plan D-CG-U2-4), so a Red run reports one failure per test
rather than one collection error for the module.

  T-REC-U2-1   records built from success_fields and from each failure_fields stage carry
               schema_version == DECISION_RECORD_SCHEMA_VERSION == 1; the constant is the
               record's own, defined in telemetry.py and not derived from the envelope's
               RESPONSE_SCHEMA_VERSION (BR1.1, ADR-004)
  T-REC-U2-2   the thirteen pre-U2 field names are a subset of the fields; each pre-U2 type is
               admissible under the new type; DecisionRecord still constructs from the
               thirteen alone, with schema_version defaulting to 1 (BR1.2, D-CG-U2-9)
  T-REC-U2-3   each of the five error_kind values constructs with a detail; an unknown kind
               raises; a kind with no detail raises; a detail with no kind raises (BR2.1)
  T-REC-U2-4   format_detail gives "<Type>: <msg>", "<Type>[<Code>]: <msg>" for an exception
               carrying response["Error"]["Code"], and at most 512 characters for a 10 KB
               message; a string passed in `redact` is removed before truncation (BR2.2)
  T-REC-U2-5   rejection_detail for request_too_large is exactly "prompt is <n> bytes; bound is
               102400" - the byte count and the bound, no content (BR2.2, FR4.6.2)
  T-REC-U2-6   failure_fields applies the null pattern per stage (received, selected,
               generated): null where unknown, never 0 / 0.0; tokens kept on generated;
               scores == {} before routing; a stage that disagrees with the kind raises
               (BR2.3, NFR7.1)
  T-REC-U2-7   no latency field name contains "total" or "sum" (BR4.4). Keep-fixed; negative
               control: the same predicate over a seeded name list fails
  T-REC-U2-8   set(ErrorKind values) & set(FailureKind values) == set() (BR2.7, R-29)
  T-REC-U2-9   the routing_overhead_ms definition block carries the not-evidence sentence;
               inside it, on every agent/ line naming routing_overhead and on every
               verify/render.py line naming routing_overhead, no "150" and no "budget" outside
               the allowed not-evidence statements (BR4.5, NFR1.2, SD-U2-2). Negative control:
               a seeded block saying "meets the 150 ms budget" fails
  T-REC-U2-10  the prompt_sha256 definition block contains "confirm" and "guess" (FR4.3.2, P-2)
  T-REC-U2-11  the autouse socket guard is active (NFR4.2)

Partition: stdlib and the package only. No SDK call, no account.
"""
from __future__ import annotations

import dataclasses
import importlib
import pathlib
import re
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import typing

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "delphi"
TS = "2026-10-19T08:15:30.123Z"
DIGEST = "a" * 64

PRE_U2_TYPES = {
    "request_id": str,
    "arm": str,
    "chosen_alias": str,
    "chosen_model_id": str,
    "scores": dict[str, float],
    "embed_latency_ms": float,
    "decision_latency_ms": float,
    "input_tokens": int,
    "output_tokens": int,
    "cost_usd": float,
    "is_fallback": bool,
    "artifact_hash": str | None,
    "extra": dict[str, object],
}

ERROR_KINDS = (
    "request_invalid", "request_too_large", "routing_failed", "generation_failed",
    "internal_error",
)
STAGE_OF = {
    "request_invalid": "received", "request_too_large": "received",
    "routing_failed": "received", "generation_failed": "selected",
    "internal_error": "generated",
}

#: The not-evidence sentence BR4.5 requires at the point of definition.
NOT_EVIDENCE = "A single value is not evidence about the NFR1 budget."
#: render.py lines that name routing_overhead and the budget only to say the field must NOT
#: be presented as it. Matched exactly, so a reworded line is re-examined, not waved through.
RENDER_ALLOWED = (
    "#: strict subset of routing_overhead_ms, and NFR1.5 forbids presenting it as the budget.",
)


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_decision_record.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _tel():
    return importlib.import_module("delphi.agent.telemetry")


def _facts(**over) -> dict:
    facts = dict(
        request_id="r1", arm="router", timestamp=TS, cold_start=False,
        chosen_alias="mid", chosen_model_id="vendor.mid-v1", scores={"mid": 1.0},
        embed_latency_ms=0.5, decision_latency_ms=1.0, routing_overhead_ms=2.0,
        input_tokens=3, output_tokens=2, cost_usd=0.001, is_fallback=False,
        artifact_hash="abc", prompt_sha256=DIGEST,
    )
    facts.update(over)
    return facts


def _failure(tel, kind: str, **over):
    fields = tel.failure_fields(stage=STAGE_OF[kind], error_kind=kind, detail="X: y",
                                **_facts(**over))
    return tel.DecisionRecord(**fields)


def definition_block(source: str, name: str) -> list[str]:
    """The attribute line for `name` plus every line up to the next attribute or blank line
    (SD-U2-2). The blank line keeps the last field's block from running into the methods."""
    lines = source.splitlines()
    attr = re.compile(r"^    [a-z_]+\s*:")
    start = next(i for i, ln in enumerate(lines) if re.match(rf"^    {name}\s*:", ln))
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if attr.match(ln) or not ln.strip():
            break
        block.append(ln)
    return block


def budget_claims(lines: list[str], allowed: tuple[str, ...]) -> list[str]:
    """Lines that carry a 150 literal or the word budget and are not an allowed statement."""
    return [ln for ln in lines
            if ("150" in ln or "budget" in ln.lower())
            and not any(a in ln for a in allowed)]


def test_t_rec_u2_1_every_record_carries_its_own_schema_version():
    tel = _tel()
    assert tel.DECISION_RECORD_SCHEMA_VERSION == 1
    records = [tel.DecisionRecord(**tel.success_fields(**_facts()))]
    records += [_failure(tel, kind) for kind in ERROR_KINDS]
    assert [r.schema_version for r in records] == [1] * 6
    source = (SRC / "agent" / "telemetry.py").read_text()
    assert re.search(r"^DECISION_RECORD_SCHEMA_VERSION\s*=\s*1\s*$", source, re.M)
    assert "RESPONSE_SCHEMA_VERSION" not in source, "the record's version is not derived"


def test_t_rec_u2_2_additive_only_over_the_pre_u2_thirteen():
    tel = _tel()
    names = {f.name for f in dataclasses.fields(tel.DecisionRecord)}
    assert set(PRE_U2_TYPES) <= names
    hints = typing.get_type_hints(tel.DecisionRecord)
    for name, old in PRE_U2_TYPES.items():
        new = hints[name]
        old_members = set(typing.get_args(old)) if typing.get_origin(old) in (
            typing.Union, type(str | None)) else {old}
        new_members = set(typing.get_args(new)) if typing.get_origin(new) in (
            typing.Union, type(str | None)) else {new}
        assert old_members <= new_members, f"{name}: {old} is not admissible under {new}"
    assert {"schema_version", "timestamp", "cold_start", "routing_overhead_ms",
            "error_kind", "error_detail", "prompt_sha256"} <= names
    legacy = tel.DecisionRecord(
        request_id="r", arm="router", chosen_alias="mid", chosen_model_id="m",
        scores={}, embed_latency_ms=0.0, decision_latency_ms=0.0, input_tokens=1,
        output_tokens=1, cost_usd=0.1, is_fallback=False,
    )
    assert legacy.schema_version == 1 and legacy.error_kind is None


def test_t_rec_u2_3_error_kind_is_closed_and_paired_with_detail():
    tel = _tel()
    assert {k.value for k in tel.ErrorKind} == set(ERROR_KINDS)
    base = dict(_facts(), error_kind=None, error_detail=None)
    for kind in ERROR_KINDS:
        rec = tel.DecisionRecord(**dict(base, error_kind=kind, error_detail="T: m"))
        assert rec.error_kind == kind
    with pytest.raises(ValueError):
        tel.DecisionRecord(**dict(base, error_kind="agent_error", error_detail="T: m"))
    with pytest.raises(ValueError):
        tel.DecisionRecord(**dict(base, error_kind="routing_failed", error_detail=None))
    with pytest.raises(ValueError):
        tel.DecisionRecord(**dict(base, error_kind=None, error_detail="T: m"))


class _CodedError(Exception):
    """Shaped like botocore's ClientError (and conftest's FakeClientError)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


def test_t_rec_u2_4_format_detail_shape_code_bound_and_redaction():
    tel = _tel()
    assert tel.format_detail(RuntimeError("boom")) == "RuntimeError: boom"
    assert (tel.format_detail(_CodedError("ThrottlingException", "rate exceeded"))
            == "_CodedError[ThrottlingException]: rate exceeded")
    long = tel.format_detail(RuntimeError("x" * 10_240))
    assert len(long) == 512 and long.startswith("RuntimeError: xxx")
    prompt_text = "the whole confidential prompt"
    out = tel.format_detail(RuntimeError(f"bad input {prompt_text}"), redact=(prompt_text,))
    assert prompt_text not in out and out.startswith("RuntimeError: bad input ")


def test_t_rec_u2_5_rejection_detail_names_size_and_bound_only():
    tel = _tel()
    assert (tel.rejection_detail("prompt", size=102_401, bound=102_400, unit="bytes")
            == "prompt is 102401 bytes; bound is 102400")


def test_t_rec_u2_6_failure_fields_null_pattern_per_stage():
    tel = _tel()
    received = _failure(tel, "request_invalid")
    for name in ("chosen_alias", "chosen_model_id", "embed_latency_ms", "decision_latency_ms",
                 "routing_overhead_ms", "input_tokens", "output_tokens", "cost_usd",
                 "prompt_sha256"):
        assert getattr(received, name) is None, name
    assert received.scores == {} and received.is_fallback is False
    assert received.artifact_hash == "abc", "present on the router arm, as the arm would be"

    routing = _failure(tel, "routing_failed")
    assert routing.chosen_alias is None and routing.scores == {}
    assert routing.prompt_sha256 == DIGEST, "the prompt validated, so its digest is known"

    selected = _failure(tel, "generation_failed")
    assert selected.chosen_alias == "mid" and selected.scores == {"mid": 1.0}
    assert selected.routing_overhead_ms == 2.0
    assert (selected.input_tokens, selected.output_tokens, selected.cost_usd) == (None,) * 3

    generated = _failure(tel, "internal_error", cost_usd=None)
    assert (generated.input_tokens, generated.output_tokens) == (3, 2)
    assert generated.cost_usd is None

    for rec in (received, routing, selected, generated):
        for value in dataclasses.asdict(rec).values():
            assert not (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and value == 0), f"a zero where the value is unknown: {rec}"
    with pytest.raises(ValueError):
        tel.failure_fields(stage="generated", error_kind="request_invalid", detail="X: y",
                           **_facts())


def _latency_names_with_total_or_sum(names) -> list[str]:
    return [n for n in names if n.endswith("_ms") and ("total" in n or "sum" in n)]


def test_t_rec_u2_7_no_latency_field_is_a_total_or_sum():
    tel = _tel()
    names = [f.name for f in dataclasses.fields(tel.DecisionRecord)]
    assert _latency_names_with_total_or_sum(names) == []
    # negative control: the predicate can fail
    assert _latency_names_with_total_or_sum(names + ["routing_total_ms"]) == ["routing_total_ms"]


def test_t_rec_u2_8_agent_and_client_vocabularies_are_disjoint():
    tel = _tel()
    from delphi.verify.failures import FailureKind

    assert {k.value for k in tel.ErrorKind} & {k.value for k in FailureKind} == set()


def test_t_rec_u2_9_routing_overhead_is_not_presented_as_the_budget():
    source = (SRC / "agent" / "telemetry.py").read_text()
    block = definition_block(source, "routing_overhead_ms")
    assert any(NOT_EVIDENCE in ln for ln in block), "the not-evidence sentence is missing"
    assert budget_claims(block, (NOT_EVIDENCE,)) == []
    agent_lines = [ln for p in sorted((SRC / "agent").glob("*.py"))
                   for ln in p.read_text().splitlines() if "routing_overhead" in ln]
    assert budget_claims([ln for ln in agent_lines if ln not in block], (NOT_EVIDENCE,)) == []
    render_lines = [ln for ln in (SRC / "verify" / "render.py").read_text().splitlines()
                    if "routing_overhead" in ln]
    assert budget_claims(render_lines, RENDER_ALLOWED) == []
    # negative control: a block claiming the budget is met fails
    seeded = ["    routing_overhead_ms: float | None = None",
              f"    # {NOT_EVIDENCE}", "    # meets the 150 ms budget on the router arm"]
    assert budget_claims(seeded, (NOT_EVIDENCE,)) == [seeded[2]]


def test_t_rec_u2_10_prompt_digest_definition_states_the_confirmation_caveat():
    source = (SRC / "agent" / "telemetry.py").read_text()
    block = " ".join(definition_block(source, "prompt_sha256")).lower()
    assert "confirm" in block and "guess" in block


def test_t_rec_u2_11_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
