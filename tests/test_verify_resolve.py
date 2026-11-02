"""Argument resolution tests — U1 LC-1 (verify/resolve.py).

Resolution is a separate step from invocation so every exit-2 case is decided before any
network call, with no spend and no latency (BR1.8, functional-spec Workflow 1 steps 1-5).

  T-VR-1  no --arn and no DELPHI_RUNTIME_ARN -> UsageError(MISSING_ARN)
  T-VR-2  --arn wins over DELPHI_RUNTIME_ARN
  T-VR-3  DELPHI_RUNTIME_ARN is used when --arn is absent
  T-VR-4  a malformed ARN -> UsageError(MALFORMED_ARN), not a transport failure
  T-VR-5  region is derived from the ARN, with no literal region default in the module
  T-VR-6  --region overrides the ARN-derived value
  T-VR-7  profile is None when --profile is absent, and no profile literal exists
  T-VR-8  the default prompt is the FIRST demonstration prompt, deterministically
  T-VR-9  the correlation id comes from an injected factory
  T-VR-10 resolution reaches no network and imports no boto3
  T-VR-11 an explicitly blank --prompt is a usage error; an absent one uses the default
"""
from __future__ import annotations

import pytest

from delphi.verify import resolve as resolve_mod
from delphi.verify.failures import FailureKind
from delphi.verify.resolve import DEMO_PROMPTS, UsageError, resolve

ARN = "arn:aws:bedrock-agentcore:eu-west-1:111122223333:runtime/delphi_router-AbC123"


def test_missing_arn_is_a_usage_error():
    """T-VR-1."""
    with pytest.raises(UsageError) as exc:
        resolve([], env={})
    assert exc.value.kind is FailureKind.MISSING_ARN


def test_flag_wins_over_environment():
    """T-VR-2."""
    other = "arn:aws:bedrock-agentcore:us-east-1:444455556666:runtime/other-XyZ"
    req = resolve(["--arn", ARN], env={"DELPHI_RUNTIME_ARN": other})
    assert req.runtime_arn == ARN


def test_environment_is_used_when_the_flag_is_absent():
    """T-VR-3."""
    req = resolve([], env={"DELPHI_RUNTIME_ARN": ARN})
    assert req.runtime_arn == ARN


@pytest.mark.parametrize(
    "bad",
    [
        "not-an-arn",
        "arn:aws:bedrock-agentcore:eu-west-1:111122223333:agent/abc",
        "arn:aws:bedrock-agentcore:eu-west-1:runtime/abc",
    ],
)
def test_malformed_arn_is_a_usage_error(bad):
    """T-VR-4. Exit 2, decided before any network call."""
    with pytest.raises(UsageError) as exc:
        resolve(["--arn", bad], env={})
    assert exc.value.kind is FailureKind.MALFORMED_ARN


def test_region_is_derived_from_the_arn():
    """T-VR-5. runtime_client.py:38's us-east-1 default is never exercised on this path."""
    req = resolve(["--arn", ARN], env={})
    assert req.region == "eu-west-1"


def test_no_argument_carries_a_string_default():
    """T-VR-5 / T-VR-7. Asserted on the PARSER, not by grepping the source.

    An earlier form of this test grepped the module text, which failed on the docstring
    explaining why those defaults were removed — it could not tell a live default from
    prose about a dead one. Reading the parser's own defaults cannot be fooled that way,
    and it is the thing that actually matters: no author-specific value may be the
    fallback for --arn, --region or --profile (AC1.5.2).
    """
    parser = resolve_mod._build_parser()
    # argparse's own --help carries the SUPPRESS sentinel; it is not ours and not a default.
    string_defaults = {
        action.dest: action.default
        for action in parser._actions
        if action.dest != "help" and isinstance(action.default, str)
    }
    assert string_defaults == {}, f"author-specific defaults survive: {string_defaults}"
    # And the three that previously carried one are explicitly None.
    by_dest = {a.dest: a.default for a in parser._actions}
    assert by_dest["arn"] is None
    assert by_dest["region"] is None
    assert by_dest["profile"] is None


def test_region_flag_overrides_the_arn():
    """T-VR-6."""
    req = resolve(["--arn", ARN, "--region", "ap-south-1"], env={})
    assert req.region == "ap-south-1"


def test_profile_is_unset_by_default():
    """T-VR-7. The replaced driver defaulted to the author's own profile name, so a reader
    without one failed before any invocation — and it looked like a credential problem."""
    assert resolve(["--arn", ARN], env={}).profile is None


def test_default_prompt_is_the_first_demonstration_prompt():
    """T-VR-8. Deterministic: not a random pick, and not the five-prompt loop."""
    req = resolve(["--arn", ARN], env={})
    assert req.prompt == DEMO_PROMPTS[0]
    assert len(DEMO_PROMPTS) == 5


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_an_explicitly_blank_prompt_is_a_usage_error(blank):
    """T-VR-11. Someone who typed --prompt meant to send something. Today `""` silently
    becomes the demo prompt and whitespace is sent as a real, billable invocation. ENT-001
    already requires the prompt be non-empty after stripping; this makes the code agree."""
    with pytest.raises(UsageError) as exc:
        resolve(["--arn", ARN, "--prompt", blank], env={})
    assert exc.value.kind is FailureKind.USAGE_ERROR


def test_an_absent_prompt_still_uses_the_default():
    """T-VR-11, the other half: only an EXPLICIT blank is refused."""
    assert resolve(["--arn", ARN], env={}).prompt == DEMO_PROMPTS[0]


def test_correlation_id_comes_from_an_injected_factory():
    """T-VR-9. Makes LC-1 testable and LC-4's correlation check assertable."""
    req = resolve(["--arn", ARN], env={}, id_factory=lambda: "fixed-id")
    assert req.correlation_id == "fixed-id"


def test_resolution_touches_no_network_and_no_sdk(monkeypatch):
    """T-VR-10. Structural: resolve must not reach the client factory at all."""
    import delphi.verify.client as client_mod

    def explode(*_a, **_k):  # pragma: no cover - the assertion is that it is never called
        raise AssertionError("resolution must not build a client")

    monkeypatch.setattr(client_mod, "build_client", explode)
    req = resolve(["--arn", ARN], env={})
    assert req.runtime_arn == ARN
