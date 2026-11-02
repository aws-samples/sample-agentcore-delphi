"""The NFR1 sample checks — U2 LC-U2-12 (eval/latency_sample.py; ENT-210; BR9.1-BR9.4; P-5).

A published routing-overhead figure is only as good as the sample it is recomputed from.
These pure checks decide whether a sample implements NFR1's protocol: warm, router-arm,
versioned records only; 40 per DEMO prompt, 200 in all; nearest-rank P90; and a manifest
that states its method. Every number below is a FIXTURE value chosen by the test. None is a
measurement, and no routing-overhead figure exists until U6 commits the real sample.

  T-SMP-U2-1  counted: a mixed-arm sample fails; a legacy record (no schema_version) fails; a
              cold record is reported separately, not counted (BR9.1)
  T-SMP-U2-2  composition_ok: 39 records for one prompt fails; a sixth prompt fails; 5 x 40
              grouped by prompt_sha256, equal to the manifest's prompt_digests, passes (BR9.2,
              P-2)
  T-SMP-U2-3  nearest_rank_p90 picks rank 180 of 200 with no interpolation; a manifest whose
              published_p90_ms differs at the stated precision fails (BR9.3)
  T-SMP-U2-4  manifest_ok: each required field removed in turn fails (BR9.4)
  T-SMP-U2-5  the real committed sample at evidence/nfr1/ passes all four checks, and the
              recomputed figure equals the manifest's published figure at its stated
              precision; the same sample with one record removed is rejected (NFR1.3,
              SD-U2-3). Executing since U6 committed the sample: its strict xfail was
              removed then. The first body compared the unrounded recomputed value with the
              rounded published one, so it could not pass; it now compares at the precision
              the manifest states, as published_figure_ok does
  T-SMP-U2-6  the autouse socket guard is active (NFR4.2)

Partition: stdlib and the package only. No SDK call, no account.
"""
from __future__ import annotations

import hashlib
import importlib
import random
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)

import boto3  # noqa: F401 - loaded before the socket guard is installed (M-4, SD-U2-21)
import botocore.config  # noqa: F401 - as above
import pytest

DIGESTS = [hashlib.sha256(f"fixture prompt {i}".encode()).hexdigest() for i in range(5)]
REQUIRED = ("workload", "prompt_digests", "passes", "n", "percentile_method", "percentile",
            "applies_to", "arm", "record_schema_version", "captured_on", "region",
            "published_p90_ms", "published_p90_decimals")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_latency_sample.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _ls():
    return importlib.import_module("delphi.eval.latency_sample")


def _record(digest: str, overhead: float, *, arm="router", cold=False, error=None,
            versioned=True) -> dict:
    rec = {"request_id": "r", "arm": arm, "timestamp": "2026-10-19T08:15:30.123Z",
           "cold_start": cold, "routing_overhead_ms": overhead, "error_kind": error,
           "error_detail": None if error is None else "X: y", "prompt_sha256": digest}
    if versioned:
        rec["schema_version"] = 1
    return rec


def _sample() -> list[dict]:
    """5 prompts x 40 warm records, overhead fixture values 1..200 in a shuffled order."""
    values = [float(v) for v in range(1, 201)]
    random.Random(7).shuffle(values)  # noqa: S311 - a deterministic fixture order, not crypto
    return [_record(DIGESTS[i % 5], values[i]) for i in range(200)]


def _manifest(**over) -> dict:
    m = {"workload": "DEMO_PROMPTS (5 prompts)", "prompt_digests": list(DIGESTS),
         "passes": 40, "n": 200, "percentile_method": "nearest-rank", "percentile": 90,
         "applies_to": "warm", "arm": "router", "record_schema_version": 1,
         "captured_on": "2026-10-20", "region": "us-west-2", "published_p90_ms": 180.0,
         "published_p90_decimals": 1}
    m.update(over)
    return m


def test_t_smp_u2_1_only_warm_versioned_router_records_count():
    ls = _ls()
    sample = _sample()
    with pytest.raises(ls.SampleCheckError):
        ls.counted(sample + [_record(DIGESTS[0], 1.0, arm="pinned:nova-lite")])
    with pytest.raises(ls.SampleCheckError):
        ls.counted(sample + [_record(DIGESTS[0], 1.0, versioned=False)])
    result = ls.counted(sample + [_record(DIGESTS[0], 999.0, cold=True)])
    assert len(result.warm) == 200 and len(result.cold) == 1
    assert 999.0 not in [r["routing_overhead_ms"] for r in result.warm]


def test_t_smp_u2_2_composition_is_forty_per_demo_prompt():
    ls = _ls()
    warm = _sample()
    assert ls.composition_ok(warm, _manifest()) is True
    with pytest.raises(ls.SampleCheckError):
        ls.composition_ok(warm[1:], _manifest())  # 39 records for one prompt
    sixth = hashlib.sha256(b"a sixth prompt").hexdigest()
    with pytest.raises(ls.SampleCheckError):
        ls.composition_ok(warm[:-1] + [_record(sixth, 1.0)], _manifest())


def test_t_smp_u2_3_nearest_rank_p90_and_the_published_figure():
    ls = _ls()
    values = [float(v) for v in range(1, 201)]
    random.Random(3).shuffle(values)  # noqa: S311 - a deterministic fixture order, not crypto
    assert ls.nearest_rank_p90(values) == 180.0, "rank ceil(0.9 * 200) = 180, no interpolation"
    assert ls.published_figure_ok(_sample(), _manifest()) == 180.0
    with pytest.raises(ls.SampleCheckError):
        ls.published_figure_ok(_sample(), _manifest(published_p90_ms=180.1))
    assert ls.published_figure_ok(_sample(), _manifest(published_p90_ms=180.04)) == 180.0


@pytest.mark.parametrize("field", REQUIRED)
def test_t_smp_u2_4_the_manifest_states_the_method(field):
    ls = _ls()
    assert ls.manifest_ok(_manifest()) is True
    broken = _manifest()
    del broken[field]
    with pytest.raises(ls.SampleCheckError):
        ls.manifest_ok(broken)


def test_t_smp_u2_5_the_committed_sample_passes_every_check():
    ls = _ls()
    records, manifest = ls.load_sample()
    p90 = ls.check_sample(records, manifest)  # raises SampleCheckError on any protocol breach
    places = manifest["published_p90_decimals"]
    # check_sample returns the recomputed value; the manifest publishes it at its precision.
    assert f"{p90:.{places}f}" == f"{manifest['published_p90_ms']:.{places}f}"
    assert manifest["arm"] == "router" and manifest["applies_to"] == "warm"
    with pytest.raises(ls.SampleCheckError):  # the committed sample is not accepted blindly
        ls.check_sample(records[:-1], manifest)


def test_t_smp_u2_6_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
