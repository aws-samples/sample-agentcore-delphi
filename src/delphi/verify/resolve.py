"""Argument resolution — LC-1, serving BR1.8 and AC1.5.2.

Resolution is a separate, pure step so every exit-2 case is decided BEFORE any network
call: a reader who typos a flag finds out instantly, with no spend and no latency.

Three author-specific defaults die here, not one. `scripts/drive_router.py` defaulted the
ARN to the author's account (`:26-29`), `--profile` to the literal "bedrock" (`:95`) and
`--region` to the author's region (`:96`). AC1.5.2 says "no author-specific
configuration", which is wider than "no hard-coded ARN": a reader whose AWS config has no
profile of that name failed before any invocation, for a reason that looked like a
credential problem.

The region is DERIVED from the ARN's own region field, so there is no literal region
default in this module at all.
"""
from __future__ import annotations

import argparse
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from delphi.verify.failures import FailureKind

#: A demo set spanning the router's domains, so a reader can watch the choice change.
#: The default prompt is the FIRST element - deterministic, never a random pick (BR1.9).
DEMO_PROMPTS: tuple[str, ...] = (
    "Deliver a cryptic prophecy about a coming war.",
    "Which titan holds up the sky? Answer with the single letter."
    " A) Atlas B) Cronus C) Helios D) Oceanus",
    "A merchant stacks 12 amphorae then 8 more. How many in all? Answer with a number.",
    "Write a Python function laurels(n) returning the sum of 1 to n.",
    "What is the Oracle of Delphi also called?",
)

#: arn:<partition>:bedrock-agentcore:<region>:<account>:runtime/<name>
_ARN = re.compile(
    r"^arn:[a-z0-9-]+:bedrock-agentcore:(?P<region>[a-z0-9-]+):\d{12}:runtime/(?P<name>.+)$"
)


class UsageError(Exception):
    """A resolution failure. Carries its kind so the CLI maps it to exit 2 (C5)."""

    def __init__(self, kind: FailureKind, detail: str = ""):
        super().__init__(detail or kind.value)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class VerificationRequest:
    """ENT-001: the fully resolved inputs of one run, before any invocation."""

    runtime_arn: str
    region: str
    runtime_name: str
    prompt: str
    correlation_id: str
    profile: str | None = None
    json_mode: bool = False


class _CataloguedParser(argparse.ArgumentParser):
    """argparse raises SystemExit(2) of its own accord on an unknown flag, which would
    bypass the failure catalogue entirely and print argparse's wording instead of a
    cause-plus-next-action pair. The exit code happens to be right, which is exactly why
    it would go unnoticed.

    The kind is USAGE_ERROR, not MALFORMED_ARN (R-50). An earlier draft mapped every
    argparse error to the ARN kind, so a flag typo told the reader their ARN could not be
    parsed and to go and re-read it — the wrong-place misdirection BR1.10 exists to
    prevent. argparse's own message is carried through as the detail, so the specific
    complaint is not lost.
    """

    def error(self, message: str):  # noqa: D102 - argparse's own contract
        raise UsageError(FailureKind.USAGE_ERROR, message)


def _build_parser() -> _CataloguedParser:
    p = _CataloguedParser(
        prog="delphi-verify",
        description=(
            "Invoke one deployed Delphi runtime and show the routing decision plus the "
            "telemetry record for that invocation. Inbound auth is IAM SigV4, so your "
            "credential chain must hold bedrock-agentcore:InvokeAgentRuntime on the ARN."
        ),
        add_help=True,
    )
    # No default on any of these three. See the module docstring.
    p.add_argument("--arn", default=None, help="runtime ARN (or set DELPHI_RUNTIME_ARN)")
    p.add_argument("--region", default=None, help="override the region read from the ARN")
    p.add_argument("--profile", default=None, help="AWS profile; omit to use the standard chain")
    p.add_argument("--prompt", default=None, help="prompt to send (default: the first demo prompt)")
    p.add_argument("--json", dest="json_mode", action="store_true",
                   help="emit the decision record alone, for piping")
    return p


def resolve(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    id_factory: Callable[[], str] | None = None,
) -> VerificationRequest:
    """Build ENT-001 from argv and the environment. Raises UsageError for every exit-2 case.

    `id_factory` is injected so the correlation id is deterministic under test, which is
    what makes LC-4's correlation check assertable.
    """
    args = _build_parser().parse_args(list(argv))

    arn = args.arn or env.get("DELPHI_RUNTIME_ARN")
    if not arn:
        raise UsageError(FailureKind.MISSING_ARN)

    match = _ARN.match(arn)
    if match is None:
        raise UsageError(FailureKind.MALFORMED_ARN, f"not a runtime ARN: {arn}")

    # An explicit --prompt must say something (ENT-001: non-empty after stripping). Only an
    # ABSENT flag falls back to the demo prompt; a blank one would otherwise be silently
    # replaced, or sent as a real, billable invocation.
    if args.prompt is not None and not args.prompt.strip():
        raise UsageError(FailureKind.USAGE_ERROR, "--prompt was given but is empty")

    mint = id_factory or (lambda: uuid.uuid4().hex)
    return VerificationRequest(
        runtime_arn=arn,
        region=args.region or match.group("region"),
        runtime_name=match.group("name"),
        prompt=args.prompt if args.prompt is not None else DEMO_PROMPTS[0],
        correlation_id=mint(),
        profile=args.profile,
        json_mode=args.json_mode,
    )
