"""The NFR1 capture, `delphi-nfr1-capture` — LC-U6-7 (U6a; NFR1.4-NFR1.7; BR7.3, BR10.1-BR10.3;
FR11.2.1, FR11.4.2; DD-U6-10).

Every case drives `delphi.eval.capture` through a hand-written fake implementing U1's
`RuntimeInvoker` seam. No test builds an AWS client, invokes a runtime, or spends anything.

  T-INF-U6-35   The capture reads only the router arm and only versioned records: a legacy
                record (no schema_version) raises LegacyRecordError, never a silent filter
                (BR7.3, FR11.2.1).
  T-INF-U6-40a  One session: every call carries the same runtime session id of at least 33
                characters; records are taken from the invoke response's decision_record; no
                AWS client of any kind is constructed (BR10.1, NFR1.5).
  T-INF-U6-40b  A fake cold on calls 1 and 17 ends with exactly 40 warm records per DEMO prompt,
                cold_after_first == 1, and 202 calls (BR10.2, NFR1.6).
  T-INF-U6-40c  An always-cold fake fails at the 45-pass cap with PassCapExceeded naming
                cold_after_first (BR10.2).
  T-INF-U6-40d  A generation_failed record whose detail code is AccessDeniedException on call 9
                stops the run at call 9, names the alias, and writes no file (FR11.4.2).
  T-INF-U6-40e  A fixture run's manifest.json and records.jsonl pass U2's check_sample and the
                U5 scrub check, carry the region read from the ARN and no account id (BR10.3,
                NFR1.7).
  T-INF-U6-40f  A record from another arm raises WrongArmError before anything is written.
  T-INF-U6-67   With an always-cold fake the run makes EXACTLY 226 calls and fails; with the
                pass cap bypassed it still stops at 226 with InvocationBudgetExceeded, and call
                227 never reaches the invoker (NFR1.4, DD-U6-10).
  guard         the autouse socket guard is active.

No latency figure is stated here: the fake's routing_overhead_ms values are fixture inputs, and
the computed P90 is compared only with itself through U2's published_figure_ok.
"""
from __future__ import annotations

import hashlib
import json
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3
import botocore.config  # noqa: F401 - as above
import pytest

from delphi.agent.runtime_client import RuntimeInvoker, RuntimeResponse
from delphi.verify.resolve import DEMO_PROMPTS

ACCT = "1" * 12
ARN = f"arn:aws:bedrock-agentcore:eu-west-1:{ACCT}:runtime/delphi_router-abc"
DIGEST = {p: hashlib.sha256(p.encode()).hexdigest() for p in DEMO_PROMPTS}


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_nfr1_capture.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def _cap():
    from delphi.eval import capture

    return capture


class FakeInvoker(RuntimeInvoker):
    """Scripted decision records; counts calls and remembers every session id."""

    def __init__(self, *, cold_on=(1,), always_cold=False, deny_on=None, other_arm_on=None,
                 legacy_on=None):
        self.calls, self.sessions = 0, []
        self.cold_on, self.always_cold = set(cold_on), always_cold
        self.deny_on, self.other_arm_on, self.legacy_on = deny_on, other_arm_on, legacy_on

    def invoke(self, prompt, *, session_id=None, request_id=None):
        self.calls += 1
        self.sessions.append(session_id)
        n = self.calls
        rec = {"request_id": request_id or f"r{n}", "arm": "router", "schema_version": 1,
               "timestamp": "2026-10-20T08:00:00.000Z", "chosen_alias": "nova-lite",
               "cold_start": self.always_cold or n in self.cold_on,
               "routing_overhead_ms": float(n % 50) + 0.5, "error_kind": None,
               "error_detail": None, "prompt_sha256": DIGEST[prompt], "is_fallback": False,
               "cost_usd": 0.0, "input_tokens": 1, "output_tokens": 1}
        if n == self.deny_on:
            rec.update(error_kind="generation_failed", chosen_alias="nova-pro",
                       error_detail="ClientError[AccessDeniedException]: not authorized")
        if n == self.other_arm_on:
            rec["arm"] = "pinned:nova-lite"
        if n == self.legacy_on:
            del rec["schema_version"]
        return RuntimeResponse(payload={"decision_record": rec}, status_code=200,
                               session_id=session_id)


def _run(fake, tmp_path, **kw):
    return _cap().run(fake, arn=ARN, out_dir=tmp_path / "nfr1", captured_on="2026-10-20", **kw)


# --- T-INF-U6-35 -------------------------------------------------------------------------
def test_t_inf_u6_35_legacy_records_raise_never_filter(tmp_path):
    cap = _cap()
    with pytest.raises(cap.LegacyRecordError):
        _run(FakeInvoker(legacy_on=5), tmp_path)
    assert not (tmp_path / "nfr1").exists()


# --- T-INF-U6-40a ------------------------------------------------------------------------
def test_t_inf_u6_40a_one_session_records_from_the_response(tmp_path, monkeypatch):
    def _no_client(*_a, **_k):
        raise AssertionError("the capture must build no AWS client when given an invoker")

    monkeypatch.setattr(boto3, "client", _no_client)
    monkeypatch.setattr(boto3.session.Session, "client", _no_client)
    fake = FakeInvoker()
    result = _run(fake, tmp_path)
    assert len(set(fake.sessions)) == 1 and len(fake.sessions[0]) >= 33
    assert len(result.records) == fake.calls


# --- T-INF-U6-40b ------------------------------------------------------------------------
def test_t_inf_u6_40b_forty_warm_per_prompt_and_cold_after_first(tmp_path):
    fake = FakeInvoker(cold_on=(1, 17))
    result = _run(fake, tmp_path)
    warm = [r for r in result.records if r["cold_start"] is False]
    counts = {d: sum(r["prompt_sha256"] == d for r in warm) for d in DIGEST.values()}
    assert set(counts.values()) == {40}
    assert result.manifest["cold_after_first"] == 1 and result.manifest["warmup_records"] == 1
    assert fake.calls == 202 == result.manifest["invocations"]


# --- T-INF-U6-40c ------------------------------------------------------------------------
def test_t_inf_u6_40c_always_cold_fails_at_the_pass_cap(tmp_path):
    cap = _cap()
    with pytest.raises(cap.PassCapExceeded, match="cold_after_first"):
        _run(FakeInvoker(always_cold=True), tmp_path)
    assert not (tmp_path / "nfr1").exists()


# --- T-INF-U6-40d ------------------------------------------------------------------------
def test_t_inf_u6_40d_access_denied_stops_and_writes_nothing(tmp_path):
    cap = _cap()
    fake = FakeInvoker(deny_on=9)
    with pytest.raises(cap.AccessDeniedStop, match="nova-pro"):
        _run(fake, tmp_path)
    assert fake.calls == 9
    assert not (tmp_path / "nfr1").exists()


# --- T-INF-U6-40e ------------------------------------------------------------------------
def test_t_inf_u6_40e_files_pass_u2_checks_and_the_scrub_check(tmp_path):
    from delphi.eval import latency_sample
    from delphi.tools.scrub_check import scan_blob

    _run(FakeInvoker(cold_on=(1, 17)), tmp_path)
    out = tmp_path / "nfr1"
    assert sorted(p.name for p in out.iterdir()) == ["manifest.json", "records.jsonl"]
    records, manifest = latency_sample.load_sample(out)
    latency_sample.check_sample(records, manifest)
    assert manifest["region"] == "eu-west-1" and manifest["published_p90_decimals"] == 1
    for name in ("manifest.json", "records.jsonl"):
        data = (out / name).read_bytes()
        assert scan_blob(name, data) == [] and ACCT.encode() not in data and b"arn:" not in data


# --- T-INF-U6-40f ------------------------------------------------------------------------
def test_t_inf_u6_40f_another_arm_fails_before_writing(tmp_path):
    cap = _cap()
    with pytest.raises(cap.WrongArmError, match="pinned:nova-lite"):
        _run(FakeInvoker(other_arm_on=30), tmp_path)
    assert not (tmp_path / "nfr1").exists()


# --- T-INF-U6-67 -------------------------------------------------------------------------
def test_t_inf_u6_67_the_hard_counter_stops_at_226(tmp_path):
    cap = _cap()
    assert cap.MAX_INVOCATIONS == 1 + 45 * len(DEMO_PROMPTS) == 226
    fake = FakeInvoker(always_cold=True)
    with pytest.raises(cap.PassCapExceeded):
        _run(fake, tmp_path)
    assert fake.calls == 226
    fake = FakeInvoker(always_cold=True)
    with pytest.raises(cap.InvocationBudgetExceeded, match="226"):
        _run(fake, tmp_path, max_passes=10_000)
    assert fake.calls == 226


def test_u6_nfr1_capture_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
