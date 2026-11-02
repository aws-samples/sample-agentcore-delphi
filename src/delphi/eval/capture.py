"""`delphi-nfr1-capture` — the on-architecture NFR1 sample (U6 LC-U6-7; NFR1.4-NFR1.7; BR10.1-
BR10.3; FR11.2.1, FR11.4.2; DD-U6-10).

    delphi-nfr1-capture --arn <router runtime ARN> [--profile <p>] [--out <dir>]

It invokes the deployed ROUTER arm in ONE runtime session, sequentially, and keeps the decision
record each invoke response carries (ADR-010), so it never reads CloudWatch. It is a separate
command from `delphi-verify`, which stays one invocation per session (C5).

Protocol (NFR1, U2's `latency_sample`):
  * one warmup invocation opens the session; its record is kept as cold and never counted;
  * each pass invokes, once, every DEMO prompt that still has fewer than 40 warm records; a
    `cold_start: true` after the first record increments `cold_after_first` and does not
    count (U2's A-1 is RECORDED by this number, not assumed);
  * the run fails at 45 passes, naming `cold_after_first`;
  * a hard counter refuses invocation 227 (1 + 45 x 5), so a defect in the pass logic cannot
    overspend: every invocation calls a real, billed Bedrock model;
  * the first `generation_failed` whose detail code is `AccessDeniedException` stops the run at
    that record and names the alias; a record from another arm, or a legacy record (no
    `schema_version`), raises a named error. In every one of these cases nothing is written.

Only when the run completes are `manifest.json` and `records.jsonl` written (U2's SAMPLE_DIR
names), after U2's `check_sample` and the U5 scrub check pass over the exact bytes. The
manifest's region comes from the ARN; the account id is never written. The files are then the
HUMAN's to commit. No figure is quoted from them anywhere until they are committed and pass
(BR10.4). The guard against spending without consent is the runbook's SPEND-CONFIRM receipt,
which a person gives; there is deliberately no `--yes` flag an agent could type.

Imports: stdlib and delphi only; boto3 is reached lazily, through the verification client
factory, and only when no invoker is injected.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import pathlib
import re
import sys
import uuid
from dataclasses import dataclass

from delphi.agent.runtime_client import RuntimeInvoker
from delphi.eval.latency_sample import SAMPLE_DIR, check_sample, nearest_rank_p90
from delphi.verify.resolve import DEMO_PROMPTS

WARM_PER_PROMPT = 40
MAX_PASSES = 45
#: 1 warmup + MAX_PASSES x the five prompts. Arithmetic from the protocol, not a measurement.
MAX_INVOCATIONS = 1 + MAX_PASSES * len(DEMO_PROMPTS)
PUBLISHED_P90_DECIMALS = 1
_ARN = re.compile(r"^arn:[a-z0-9-]+:bedrock-agentcore:(?P<region>[a-z0-9-]+):\d{12}:runtime/.+$")
_DENIED = "[AccessDeniedException]"


class CaptureError(RuntimeError):
    """The capture stopped; nothing was written."""


class LegacyRecordError(CaptureError):
    """A record without schema_version: the sample takes versioned records only (BR7.3)."""


class WrongArmError(CaptureError):
    """A record from an arm other than the router."""


class AccessDeniedStop(CaptureError):
    """A routed model was denied: revert the model grant on that stack (FR11.4.2)."""


class PassCapExceeded(CaptureError):
    """45 passes ran without 40 warm records for every prompt."""


class InvocationBudgetExceeded(CaptureError):
    """The hard counter refused one more invocation (DD-U6-10)."""


class SampleWriteError(CaptureError):
    """The finished sample failed U2's checks or the scrub check, or the outdir is not empty."""


@dataclass
class CaptureResult:
    records: list[dict]
    manifest: dict


class _Counter:
    """Wraps the invoker; refuses to issue invocation MAX_INVOCATIONS + 1."""

    def __init__(self, invoker: RuntimeInvoker, limit: int):
        self.invoker, self.limit, self.calls = invoker, limit, 0

    def __call__(self, prompt: str, session_id: str) -> dict:
        if self.calls >= self.limit:
            raise InvocationBudgetExceeded(f"refused invocation {self.calls + 1}: the bound is "
                                           f"{self.limit} invocations")
        self.calls += 1
        resp = self.invoker.invoke(prompt, session_id=session_id)
        record = resp.payload.get("decision_record") if isinstance(resp.payload, dict) else None
        if not isinstance(record, dict):
            raise CaptureError(f"invocation {self.calls}: the response carries no "
                               "decision_record object")
        return record


def _check(record: dict, n: int) -> None:
    if record.get("arm") != "router":
        raise WrongArmError(f"invocation {n}: record from arm {record.get('arm')!r}; the "
                            "sample is drawn from the router arm alone")
    if "schema_version" not in record:
        raise LegacyRecordError(f"invocation {n}: a legacy record (no schema_version); the "
                                "router image must carry U2 before the capture")
    detail = record.get("error_detail") or ""
    if record.get("error_kind") == "generation_failed" and _DENIED in detail:
        raise AccessDeniedStop(f"invocation {n}: alias {record.get('chosen_alias')!r} was "
                               "denied (AccessDeniedException); stop, revert the model grant "
                               "on this stack, correct the routing file, rerun from the start")


def capture(invoker: RuntimeInvoker, *, region: str, captured_on: str,
            max_passes: int = MAX_PASSES, max_invocations: int = MAX_INVOCATIONS) -> CaptureResult:
    """Run the protocol against `invoker`. Raises a CaptureError subclass on any stop."""
    session = f"delphi-nfr1-{uuid.uuid4()}"  # >= 33 characters, one per run (NFR1.5)
    call = _Counter(invoker, max_invocations)
    digests = {p: hashlib.sha256(p.encode("utf-8")).hexdigest() for p in DEMO_PROMPTS}
    records: list[dict] = []

    warmup = call(DEMO_PROMPTS[0], session)
    _check(warmup, call.calls)
    records.append(warmup)
    warm = {p: 0 for p in DEMO_PROMPTS}
    cold_after_first = 0
    for _ in range(max_passes):
        for prompt in [p for p in DEMO_PROMPTS if warm[p] < WARM_PER_PROMPT]:
            rec = call(prompt, session)
            _check(rec, call.calls)
            records.append(rec)
            if rec.get("cold_start") is True:
                cold_after_first += 1
            elif rec.get("error_kind") is None and not rec.get("is_fallback"):
                warm[prompt] += 1
        if all(n >= WARM_PER_PROMPT for n in warm.values()):
            break
    else:
        raise PassCapExceeded(f"{max_passes} passes without {WARM_PER_PROMPT} warm records per "
                              f"prompt; cold_after_first={cold_after_first} (U2 A-1)")

    warm_records = [r for r in records[1:] if r.get("cold_start") is False
                    and r.get("error_kind") is None and not r.get("is_fallback")]
    p90 = nearest_rank_p90([r["routing_overhead_ms"] for r in warm_records])
    manifest = {
        "workload": "DEMO_PROMPTS (5 prompts)",
        "prompt_digests": [digests[p] for p in DEMO_PROMPTS],
        "passes": WARM_PER_PROMPT, "n": WARM_PER_PROMPT * len(DEMO_PROMPTS),
        "percentile_method": "nearest-rank", "percentile": 90, "applies_to": "warm",
        "arm": "router", "record_schema_version": int(warmup["schema_version"]),
        "captured_on": captured_on, "region": region,
        "published_p90_ms": round(p90, PUBLISHED_P90_DECIMALS),
        "published_p90_decimals": PUBLISHED_P90_DECIMALS,
        "warmup_records": 1 if warmup.get("cold_start") is True else 0,
        "cold_after_first": cold_after_first, "invocations": call.calls,
        "max_passes": max_passes,
    }
    if warmup.get("cold_start") is not True:  # the warmup is never counted, cold or not
        records = records[1:]
    return CaptureResult(records=records, manifest=manifest)


def _encode(result: CaptureResult) -> dict[str, bytes]:
    return {
        "manifest.json": (json.dumps(result.manifest, indent=2, sort_keys=True) + "\n").encode(),
        "records.jsonl": "".join(json.dumps(r, sort_keys=True) + "\n"
                                 for r in result.records).encode(),
    }


def write_sample(result: CaptureResult, out_dir: pathlib.Path) -> list[pathlib.Path]:
    """Validate the exact bytes, then write them. Nothing is written if any check fails."""
    from delphi.tools.scrub_check import scan_blob

    files = _encode(result)
    records = [json.loads(line) for line in files["records.jsonl"].decode().splitlines()]
    try:
        check_sample(records, json.loads(files["manifest.json"]))
    except ValueError as exc:
        raise SampleWriteError(f"the sample fails U2's checks: {exc}") from exc
    hits = [h for name, data in files.items() for h in scan_blob(name, data)]
    if hits:
        raise SampleWriteError(f"the sample carries identifiers: {sorted({h.cls for h in hits})}")
    out_dir = pathlib.Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise SampleWriteError(f"{out_dir} is not empty; a sample is never overwritten")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, data in files.items():
        (out_dir / name).write_bytes(data)
        written.append(out_dir / name)
    return written


def region_from_arn(arn: str) -> str:
    m = _ARN.match(arn)
    if not m:
        raise ValueError(f"not a bedrock-agentcore runtime ARN: {arn[:60]!r}")
    return m.group("region")


def run(invoker: RuntimeInvoker, *, arn: str, out_dir: pathlib.Path,
        captured_on: str | None = None, **limits) -> CaptureResult:
    """Capture, then write the files. The only public entry the CLI and the tests share."""
    region = region_from_arn(arn)
    day = captured_on or _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    result = capture(invoker, region=region, captured_on=day, **limits)
    write_sample(result, out_dir)
    return result


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - live runtime only
    parser = argparse.ArgumentParser(prog="delphi-nfr1-capture",
                                     description=__doc__.split("\n")[0])
    parser.add_argument("--arn", required=True, help="the deployed router runtime ARN")
    parser.add_argument("--profile", help="a named AWS profile (default: the standard chain)")
    parser.add_argument("--out", default=str(SAMPLE_DIR), help="output directory")
    args = parser.parse_args(argv)
    try:
        region = region_from_arn(args.arn)
    except ValueError as exc:
        print(f"delphi-nfr1-capture: {exc}", file=sys.stderr)
        return 2
    from delphi.agent.runtime_client import AgentCoreRuntimeInvoker
    from delphi.verify.client import build_client

    client = build_client(region=region, profile=args.profile)
    invoker = AgentCoreRuntimeInvoker(args.arn, region=region, client=client)
    try:
        result = run(invoker, arn=args.arn, out_dir=pathlib.Path(args.out))
    except CaptureError as exc:
        print(f"delphi-nfr1-capture: stopped, nothing written: {exc}", file=sys.stderr)
        return 1
    m = result.manifest
    print(f"wrote {args.out}/manifest.json and records.jsonl: {m['invocations']} invocations, "
          f"cold_after_first={m['cold_after_first']}; commit them only after review")
    return 0


def _entry() -> None:  # pragma: no cover - console-script shim
    sys.exit(main(sys.argv[1:]))
