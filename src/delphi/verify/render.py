"""Renderer — LC-5, serving BR1.13, BR1.14 and BR1.16.

Two jobs, and the second is the delicate one: show a reader the routing decision, and
report `embed_latency_ms` honestly.

Two record shapes reach this renderer, and `schema_version` tells them apart (C2, ADR-004).
A record that carries it is versioned: its `embed_latency_ms` is the router's own timing of
the encoding step, or null where there is no encoding step, and a null figure is not
printed. A record WITHOUT it is legacy - emitted before its arm was redeployed with the
embed-figure producer - and there `embed_latency_ms` is the structural 0.0 the old request
path wrote. Only that case is annotated, and the value is still printed as received. It is
not suppressed - that would hide a defect the published post exists to teach - and it is not
replaced by a computed value, which would be the unevidenced figure the publication gate
forbids. `team.md` records that false confidence is worse than a named gap.

The annotation names the ABSENCE. It deliberately does not redirect the reader to
`decision_latency_ms`: C2 deprecates that field as a strict subset of
`routing_overhead_ms`, and NFR1.5 forbids presenting it as the routing figure.

The three latency figures nest (embed <= decision <= routing overhead) and are never
summed, so nothing here computes a total. No threshold literal appears either: the 150 ms
budget has no executing test behind it yet, so printing it beside a number would imply a
comparison this command cannot make.
"""
from __future__ import annotations

import json

from delphi.agent.telemetry import EMBED_FIGURE_FIELD

#: Names the absence: a legacy record has no schema_version, and no producer stood behind
#: its 0.0. Must not mention decision_latency_ms (see the module docstring).
EMBED_ANNOTATION = (
    "not a measurement - this record has no schema_version, so it predates the "
    "embed-figure producer"
)

#: Says what decision_latency_ms is NOT, in the line a reader sees. C2 records it as a
#: strict subset of routing_overhead_ms, and NFR1.5 forbids presenting it as the budget.
DECISION_LATENCY_CAVEAT = "a subset, not the routing-overhead budget figure"


def _bar(value: float, vmax: float = 100.0, width: int = 24) -> str:
    filled = int(round(width * max(0.0, min(value, vmax)) / vmax))
    return "█" * filled + "·" * (width - filled)


def render_json(response: dict) -> str:
    """The decision record alone, so a reader can pipe it into jq (C5's --json mode).

    Passed through unmodified - including `embed_latency_ms` as the 0.0 it genuinely is.
    """
    return json.dumps(response.get("decision_record", {}), indent=2, sort_keys=True)


def render_human(response: dict, *, elapsed_ms: float) -> str:
    """The rendered view: routing decision, record, and the client-side round trip."""
    record = response.get("decision_record", {}) or {}
    scores = response.get("scores") or {}
    chosen = response.get("chosen_model")
    lines: list[str] = []

    lines.append("=" * 78)
    lines.append(f"arm: {response.get('arm')}    chosen model: {chosen}")
    lines.append("-" * 78)

    if scores:
        lines.append("Router decision (per-candidate score):")
        for alias, val in sorted(scores.items(), key=lambda kv: -kv[1]):
            mark = "  <- CHOSEN" if alias == chosen else ""
            lines.append(f"  {alias:16s} {_bar(val)} {val:6.2f}{mark}")
    else:
        lines.append(f"Chosen model: {chosen} (no router scores on this arm)")

    lines.append("-" * 78)
    lines.append("Answer:")
    for line in (response.get("output") or "").strip().splitlines() or [""]:
        lines.append("  " + line)

    usage = response.get("usage") or {}
    lines.append("-" * 78)
    lines.append(
        f"tokens: in {usage.get('input_tokens', '?')} / out {usage.get('output_tokens', '?')}"
        f"    cost: ${response.get('cost_usd', 0):.6f} per-invocation"
    )

    decision = record.get("decision_latency_ms", response.get("decision_latency_ms"))
    if decision is not None:
        # NOT labelled "routing overhead" (R-49). C2 records decision_latency_ms as a
        # strict SUBSET of routing_overhead_ms - it omits pool lookup and score-map
        # assembly - and NFR1.5 forbids presenting it as the budget figure. The label says
        # what it is and what it is not, in the line a reader actually sees.
        lines.append(
            f"decision latency: {decision:.2f} ms"
            f"   [{DECISION_LATENCY_CAVEAT}]"
        )

    embed = record.get(EMBED_FIGURE_FIELD)
    if embed is not None:
        # Keyed on legacy-ness AND the structural 0.0 (D-CG-U2-3): a legacy record with a
        # non-zero value, and any versioned record, carry no annotation.
        legacy_zero = "schema_version" not in record and embed == 0.0
        note = f"   [{EMBED_ANNOTATION}]" if legacy_zero else ""
        lines.append(f"embed latency:    {embed:.2f} ms{note}")

    lines.append(f"client round trip: {elapsed_ms:.2f} ms per-invocation (includes generation)")
    lines.append(f"fallback: {response.get('is_fallback')}")

    lines.append("-" * 78)
    lines.append("Decision record:")
    for key in sorted(record):
        lines.append(f"  {key}: {record[key]!r}")

    return "\n".join(lines)
