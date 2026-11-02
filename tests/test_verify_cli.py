"""CLI tests — U1 LC-7 (verify/cli.py), end to end over hand-written fakes.

Walks the VerificationOutcome state machine from functional-spec.md: every terminal state
maps to exactly one exit code, exit 1 carries two clauses, and stdout stays empty on
failure so a reader piping --json never gets half a record (C5, BR1.10, BR1.15).

  T-VY-1  exit 0 on the happy path, with the decision and the record on stdout
  T-VY-2  exit 2 on a usage error, stdout empty
  T-VY-3  exit 1 per failure kind, stdout empty, stderr carrying BOTH clauses
  T-VY-4  an unknown flag routes through the catalogue, not argparse's wording
  T-VY-5  the entry point is importable after the documented dev install
  T-VY-6  --json is C2-conformant, over a REAL versioned record produced by the agent's
          handle_request (its U2 gate is open, so the xfail is gone)
"""
from __future__ import annotations

import io
import json

import pytest

from delphi.verify.cli import main

ARN = "arn:aws:bedrock-agentcore:eu-west-1:111122223333:runtime/delphi_router-AbC123"

RECORD = {
    "request_id": "fixed-id",
    "arm": "router",
    "chosen_alias": "nova-lite",
    "chosen_model_id": "us.amazon.nova-lite-v1:0",
    "scores": {"nova-lite": 7.5},
    "embed_latency_ms": 0.0,
    "decision_latency_ms": 1.25,
    "input_tokens": 12,
    "output_tokens": 7,
    "cost_usd": 0.000123,
    "is_fallback": False,
    "artifact_hash": None,
    "extra": {},
}
GOOD = {
    "request_id": "fixed-id",
    "arm": "router",
    "chosen_model": "nova-lite",
    "scores": {"nova-lite": 7.5},
    "decision_latency_ms": 1.25,
    "output": "a cryptic prophecy",
    "usage": {"input_tokens": 12, "output_tokens": 7},
    "cost_usd": 0.000123,
    "is_fallback": False,
    "schema_version": 1,
    "decision_record": RECORD,
}


class FakeClient:
    def __init__(self, *, payload=None, status=200, body=None, raises=None):
        self.calls = []
        self._payload = payload
        self._status = status
        self._body = body
        self._raises = raises

    def invoke_agent_runtime(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        body = self._body
        if body is None:
            body = json.dumps(self._payload if self._payload is not None else {}).encode()
        return {
            "response": io.BytesIO(body),
            "statusCode": self._status,
            "runtimeSessionId": kwargs["runtimeSessionId"],
        }


def _run(argv, client, capsys):
    code = main(argv, env={}, client_factory=lambda **_k: client, id_factory=lambda: "fixed-id")
    out, err = capsys.readouterr()
    return code, out, err


def test_happy_path_exits_zero_and_prints_the_record(capsys):
    """T-VY-1."""
    code, out, err = _run(["--arn", ARN], FakeClient(payload=GOOD), capsys)
    assert code == 0
    assert "nova-lite" in out
    assert "fixed-id" in out
    assert err == ""


def test_usage_error_exits_two_with_empty_stdout(capsys):
    """T-VY-2."""
    code, out, err = _run([], FakeClient(payload=GOOD), capsys)
    assert code == 2
    assert out == ""
    assert err.strip()


@pytest.mark.parametrize(
    "client,needle",
    [
        (FakeClient(payload=GOOD, status=403), "403"),
        (FakeClient(body=b""), "empty"),
        (FakeClient(body=b"{not json"), "json"),
        (FakeClient(payload={k: v for k, v in GOOD.items() if k != "decision_record"}),
         "redeploy"),
        (FakeClient(payload=dict(GOOD, decision_record={"request_id": "other"})),
         "correlat"),
    ],
)
def test_every_failure_exits_one_with_two_clauses(client, needle, capsys):
    """T-VY-3. BR1.10: a single-clause message is a bug."""
    code, out, err = _run(["--arn", ARN], client, capsys)
    assert code == 1
    assert out == ""
    assert needle.lower() in err.lower()
    # Two clauses: a cause line and a "Next:" action line.
    assert "next:" in err.lower(), f"no next action in: {err!r}"


def test_unknown_flag_routes_through_the_catalogue(capsys):
    """T-VY-4. argparse's own SystemExit(2) must not escape and bypass the catalogue."""
    code, out, err = _run(["--arn", ARN, "--nonsense"], FakeClient(payload=GOOD), capsys)
    assert code == 2
    assert out == ""
    assert "next:" in err.lower()


def test_entry_point_is_importable():
    """T-VY-5. AC1.5.4 - and boto3 stays out of module scope, so this holds without the SDK."""
    from delphi.verify.cli import main as imported

    assert callable(imported)


def test_json_mode_emits_one_object(capsys):
    """T-VY-6 (executing half). The full C2 assertion is deferred below."""
    code, out, _ = _run(["--arn", ARN, "--json"], FakeClient(payload=GOOD), capsys)
    assert code == 0
    parsed = json.loads(out)
    assert len(parsed) == len(RECORD)


def test_json_mode_is_c2_conformant(capsys, pool):
    """T-VY-6. Its gate - U2's versioned DecisionRecord - is open, so the fixture is a real
    response from the agent's handle_request rather than a hand-written record."""
    from delphi.agent.app import RouterAgent
    from delphi.agent.bedrock import ConverseClient, ConverseResult
    from delphi.agent.telemetry import InMemorySink
    from delphi.router.baselines import AlwaysModelRouter

    class _Answers(ConverseClient):
        def converse(self, model_id, prompt, params):
            return ConverseResult(text="ok", input_tokens=3, output_tokens=2,
                                  model_id=model_id)

    agent = RouterAgent(pool, AlwaysModelRouter(pool, "mid"), _Answers(), InMemorySink())
    response = agent.handle_request({"prompt": "hi", "request_id": "fixed-id"})
    code, out, _ = _run(["--arn", ARN, "--json"], FakeClient(payload=response), capsys)
    assert code == 0
    parsed = json.loads(out)
    for required in ("schema_version", "request_id", "arm", "timestamp", "cold_start"):
        assert required in parsed, f"C2 requires {required}"
    assert parsed == response["decision_record"]
