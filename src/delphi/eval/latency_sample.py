"""NFR1 sample checks (U2 LC-U2-12; ENT-210; BR9.1-BR9.4; proposal P-5, accepted at plan gate).

A routing-overhead figure may be published only when it is recomputed from a COMMITTED
on-architecture sample that implements NFR1's protocol (BR4.5; project.md § Forbidden).
These are that protocol's checks, written once and called by whoever captures or publishes
the figure (U6's capture, U5's CI):

  manifest_ok          the manifest states the method: workload, 40 passes, N = 200,
                       nearest-rank, the 90th percentile, warm only, router arm, record
                       schema version, capture date, region, the figure and its precision
                       (BR9.4)
  counted              only warm, router-arm, versioned, successful records count; another
                       arm or a legacy record in the sample is an ERROR, not a filter; cold
                       records are reported separately (BR9.1)
  composition_ok       exactly 40 warm records for each of the five DEMO prompts, grouped by
                       prompt_sha256 against the manifest's digests (BR9.2)
  nearest_rank_p90     the value at 1-based rank ceil(0.9 * N), no interpolation (BR9.3)
  published_figure_ok  that value, at the stated precision, equals the published figure

The checks are pure functions over decoded record and manifest dicts. Only `load_sample`
touches the filesystem. No figure is produced or stated here: until the sample exists there
is nothing to recompute, and no routing-overhead figure may be quoted.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field

#: Where the committed sample lives (plan D-CG-U2-6): not git-ignored, and outside the image
#: build context's allowlist. U6 commits manifest.json and records.jsonl here.
SAMPLE_DIR = pathlib.Path(__file__).resolve().parents[3] / "evidence" / "nfr1"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Fields whose value the protocol fixes exactly.
PROTOCOL = {
    "workload": "DEMO_PROMPTS (5 prompts)",
    "passes": 40,
    "n": 200,
    "percentile_method": "nearest-rank",
    "percentile": 90,
    "applies_to": "warm",
    "arm": "router",
}
#: Fields the manifest must state, with the check each value must pass.
STATED = {
    "prompt_digests": lambda v: (isinstance(v, list) and len(v) == 5 and len(set(v)) == 5
                                 and all(isinstance(d, str) and _HEX64.match(d) for d in v)),
    "record_schema_version": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1,
    "captured_on": lambda v: isinstance(v, str) and bool(v.strip()),
    "region": lambda v: isinstance(v, str) and bool(v.strip()),
    "published_p90_ms": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "published_p90_decimals": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
}


class SampleCheckError(ValueError):
    """The sample or its manifest does not implement NFR1's protocol."""


@dataclass(frozen=True)
class CountedSample:
    warm: list[dict]    # the records the figure is computed from
    cold: list[dict]    # counted and reported separately; no budget applies to them
    failed: list[dict]  # records carrying an error_kind; never counted
    # U4 (NR-2, SD-U4-8): records where the agent's fallback answered. Bucketed FIRST, so a
    # cold or failed fallback cannot hide; a sample holding any is rejected by check_sample.
    fallback: list[dict] = field(default_factory=list)


def manifest_ok(manifest: dict) -> bool:
    for name, expected in PROTOCOL.items():
        if name not in manifest:
            raise SampleCheckError(f"manifest does not state {name}")
        if manifest[name] != expected:
            raise SampleCheckError(f"manifest {name} is {manifest[name]!r}, the protocol "
                                   f"requires {expected!r}")
    for name, ok in STATED.items():
        if name not in manifest:
            raise SampleCheckError(f"manifest does not state {name}")
        if not ok(manifest[name]):
            raise SampleCheckError(f"manifest {name} is malformed: {manifest[name]!r}")
    return True


def counted(records: list[dict]) -> CountedSample:
    warm, cold, failed, fallback = [], [], [], []
    for i, rec in enumerate(records):
        if rec.get("arm") != "router":
            raise SampleCheckError(f"record {i} is from arm {rec.get('arm')!r}; the sample "
                                   "must be drawn from the router arm alone")
        if "schema_version" not in rec:
            raise SampleCheckError(f"record {i} is a legacy record (no schema_version)")
        if rec.get("is_fallback") is True:
            # FIRST (ND-5): a router that fell back is not the router being measured,
            # whatever happened after routing.
            fallback.append(rec)
        elif rec.get("error_kind") is not None:
            failed.append(rec)
        elif rec.get("cold_start") is True:
            cold.append(rec)
        else:
            warm.append(rec)
    return CountedSample(warm=warm, cold=cold, failed=failed, fallback=fallback)


def composition_ok(warm: list[dict], manifest: dict) -> bool:
    groups: dict[str, int] = {}
    for rec in warm:
        digest = rec.get("prompt_sha256")
        groups[digest] = groups.get(digest, 0) + 1
    expected = set(manifest["prompt_digests"])
    if set(groups) != expected:
        raise SampleCheckError(f"the sample's prompts {sorted(map(str, groups))} are not the "
                               f"manifest's five DEMO prompts")
    short = {d: n for d, n in groups.items() if n != manifest["passes"]}
    if short:
        raise SampleCheckError(f"each prompt needs exactly {manifest['passes']} warm records; "
                               f"got {short}")
    return True


def nearest_rank_p90(values: list[float]) -> float:
    if not values:
        raise SampleCheckError("no values to take a percentile of")
    ordered = sorted(values)
    rank = -(-90 * len(ordered) // 100)  # ceil(0.9 * N) in integer arithmetic
    return ordered[rank - 1]


def published_figure_ok(warm: list[dict], manifest: dict) -> float:
    """The recomputed P90 when it matches the published figure at the stated precision."""
    values = [rec["routing_overhead_ms"] for rec in warm]
    if any(v is None for v in values):
        raise SampleCheckError("a counted record has no routing_overhead_ms")
    p90 = nearest_rank_p90(values)
    places = manifest["published_p90_decimals"]
    if f"{p90:.{places}f}" != f"{manifest['published_p90_ms']:.{places}f}":
        raise SampleCheckError(f"recomputed P90 {p90:.{places}f} does not equal the published "
                               f"{manifest['published_p90_ms']:.{places}f}")
    return p90


def check_sample(records: list[dict], manifest: dict) -> float:
    """All four checks, in order. Returns the recomputed figure."""
    manifest_ok(manifest)
    sample = counted(records)
    if sample.fallback:
        raise SampleCheckError(f"{len(sample.fallback)} fallback record(s) in the sample; a "
                               "fallback is not the router being measured (NFR1.2)")
    if len(sample.warm) != manifest["n"]:
        raise SampleCheckError(f"{len(sample.warm)} warm records; the protocol needs "
                               f"{manifest['n']}")
    composition_ok(sample.warm, manifest)
    return published_figure_ok(sample.warm, manifest)


def load_sample(directory: pathlib.Path = SAMPLE_DIR) -> tuple[list[dict], dict]:
    """Read records.jsonl (one record per line, as emitted) and manifest.json."""
    manifest = json.loads((directory / "manifest.json").read_text())
    lines = (directory / "records.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()], manifest
