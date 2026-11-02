"""Renderer tests — U1 LC-5 (verify/render.py).

embed_latency_ms is the structural 0.0 on every LEGACY record - one without schema_version,
emitted before its arm was redeployed with U2's embed-figure producer - and the fixtures
below are legacy records. U1 prints the value faithfully and annotates it, without
redirecting the reader to decision_latency_ms — C2 deprecates that field and NFR1.5
forbids presenting it as the routing figure (BR1.13, BR1.14).

  T-VD-1  the annotation is present when embed_latency_ms == 0.0
  T-VD-2  the annotation is ABSENT when the value is non-zero
  T-VD-3  the annotation does not mention decision_latency_ms
  T-VD-4  the module carries no "150" literal and renders no derived latency total
  T-VD-5  --json emits exactly one JSON object and nothing else
  T-VD-6  --json passes embed_latency_ms through unmodified
  T-VD-7  cost and latency labels say per-invocation, never per-call
"""
from __future__ import annotations

import json
import pathlib

from delphi.verify import render as render_mod
from delphi.verify.render import (
    DECISION_LATENCY_CAVEAT,
    EMBED_ANNOTATION,
    render_human,
    render_json,
)

RECORD = {
    "request_id": "r1",
    "arm": "router",
    "chosen_alias": "nova-lite",
    "chosen_model_id": "us.amazon.nova-lite-v1:0",
    "scores": {"nova-lite": 7.5, "nova-pro": 6.1},
    "embed_latency_ms": 0.0,
    "decision_latency_ms": 1.25,
    "input_tokens": 12,
    "output_tokens": 7,
    "cost_usd": 0.000123,
    "is_fallback": False,
    "artifact_hash": "abc123",
    "extra": {},
}
RESPONSE = {
    "request_id": "r1",
    "arm": "router",
    "chosen_model": "nova-lite",
    "scores": {"nova-lite": 7.5, "nova-pro": 6.1},
    "decision_latency_ms": 1.25,
    "output": "a cryptic prophecy",
    "usage": {"input_tokens": 12, "output_tokens": 7},
    "cost_usd": 0.000123,
    "is_fallback": False,
    "schema_version": 1,
    "decision_record": RECORD,
}


def test_annotation_present_when_the_field_is_zero():
    """T-VD-1."""
    out = render_human(RESPONSE, elapsed_ms=42.0)
    assert EMBED_ANNOTATION in out


def test_annotation_absent_when_the_field_is_non_zero():
    """T-VD-2. Otherwise the annotation becomes a decorative constant."""
    record = dict(RECORD, embed_latency_ms=3.5)
    out = render_human(dict(RESPONSE, decision_record=record), elapsed_ms=42.0)
    assert EMBED_ANNOTATION not in out


def test_annotation_does_not_redirect_to_decision_latency():
    """T-VD-3. C2 deprecates that field; NFR1.5 forbids presenting it as the budget figure."""
    assert "decision_latency_ms" not in EMBED_ANNOTATION


def test_rendered_line_does_not_present_decision_latency_as_the_budget_figure():
    """T-VD-8. NFR1.5's stated criterion is a test over the RENDERED OUTPUT, and an earlier
    draft had none - the line said "routing decision: N ms", which is exactly the
    presentation the requirement forbids (R-49)."""
    out = render_human(RESPONSE, elapsed_ms=42.0)
    assert DECISION_LATENCY_CAVEAT in out
    # The phrase "routing" must never appear as this field's own label.
    assert "routing decision:" not in out
    assert "routing overhead:" not in out


def test_no_threshold_literal_and_no_derived_total():
    """T-VD-4. The three latency figures nest and must never be summed."""
    source = pathlib.Path(render_mod.__file__).read_text()
    assert '"150"' not in source and "150.0" not in source
    out = render_human(RESPONSE, elapsed_ms=42.0)
    assert "total latency" not in out.lower()


def test_json_mode_emits_one_object_and_nothing_else():
    """T-VD-5. A reader pipes this into jq."""
    out = render_json(RESPONSE)
    parsed = json.loads(out)
    assert parsed["request_id"] == "r1"
    assert out.strip().startswith("{") and out.strip().endswith("}")


def test_json_mode_passes_the_zero_through_unmodified():
    """T-VD-6. Never suppressed, never substituted."""
    assert json.loads(render_json(RESPONSE))["embed_latency_ms"] == 0.0


def test_labels_say_per_invocation_not_per_call():
    """T-VD-7. bedrock.py:52 still allows several model calls inside one invocation."""
    out = render_human(RESPONSE, elapsed_ms=42.0).lower()
    assert "per-invocation" in out
    assert "per-call" not in out
