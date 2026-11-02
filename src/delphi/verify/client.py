"""Client factory — LC-2, serving DD-1, DD-2 and BR1.8.

The command builds its own `bedrock-agentcore` client and passes it to
`AgentCoreRuntimeInvoker` through that class's existing `client=` parameter, so `--profile`
has somewhere to land and "no profile_name" becomes something a test can observe.

**A client built with no botocore config inherits boto3's `legacy` retry mode.** Verified
against botocore 1.43.50: with `retry_mode` unset the resolved mode is `legacy`, up to
five attempts. So "exactly one invocation" would hold against a test fake while, on the
wire, one printed invocation could be several runtime calls and several Bedrock charges.

Note `total_max_attempts`, NOT `max_attempts`: the latter counts retries AFTER the initial
request, so `max_attempts: 1` permits TWO attempts. botocore normalises the dict at client
construction, which is why the assertion in T-VC-1 reads the built client rather than the
Config object - reading the Config would pass on the wrong spelling.

`botocore.config` is imported inside the factory, never at module scope: it is the same
dependency as `boto3` by another name, and a module-scope import would break both the
repository's convention and AC1.5.4.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

#: One total attempt, including the initial request. Perturbing this raises at construction.
RETRIES: dict[str, Any] = {"total_max_attempts": 1, "mode": "standard"}

#: botocore's own default is already 60, so this PINS a value rather than raising one. It
#: exists so a future default change cannot silently lower the floor, and so the number is
#: visible beside its reason. It carries no claim about what a cold start costs.
READ_TIMEOUT_S = 60
CONNECT_TIMEOUT_S = 10


class VerifyConfigError(RuntimeError):
    """Raised when the retry configuration would permit more than one attempt.

    Fail fast at construction rather than quietly double-billing a reader - the convention
    `team.md` § Code Style records, with five existing raise sites in this repository.
    """


def effective_total_attempts(retries: Mapping[str, Any]) -> int:
    """Total attempts including the initial request, whichever spelling is used.

    `total_max_attempts` includes the initial request. `max_attempts` counts retries after
    it, so one more than its value. With neither, botocore's mode decides the default.
    """
    if "total_max_attempts" in retries:
        return int(retries["total_max_attempts"])
    if "max_attempts" in retries:
        return int(retries["max_attempts"]) + 1
    return 5 if retries.get("mode") == "legacy" else 3


def config_kwargs() -> dict[str, Any]:
    """The kwargs handed to botocore's Config. Exposed so a test can assert the pin exists.

    Asserting `resolved read_timeout >= 60` would pass even if the pin were dropped,
    because the default IS 60 - so the check has to look at what we pass, not what resolves.
    """
    return {
        "retries": dict(RETRIES),
        "read_timeout": READ_TIMEOUT_S,
        "connect_timeout": CONNECT_TIMEOUT_S,
    }


def build_client_config():
    """Build the botocore Config, refusing anything that would allow a second attempt."""
    from botocore.config import Config  # lazy: never at module scope

    kwargs = config_kwargs()
    attempts = effective_total_attempts(kwargs["retries"])
    if attempts != 1:
        raise VerifyConfigError(
            f"retry configuration permits {attempts} attempts, not 1: {kwargs['retries']!r}. "
            "Use total_max_attempts, not max_attempts - the latter counts retries after the "
            "initial request, so one invocation would become two model calls."
        )
    return Config(**kwargs)


def build_client(
    *,
    region: str,
    profile: str | None,
    session_factory: Callable[..., Any] | None = None,
):
    """Return a `bedrock-agentcore` client. The only AWS client this command ever builds.

    No `profile_name` is passed when `profile` is None, so the standard credential chain
    applies. ADR-010 puts the decision record in the invoke response, so no CloudWatch
    client is needed and no `logs:*` permission is required.
    """
    config = build_client_config()

    if session_factory is None:
        import boto3  # lazy: never at module scope

        session_factory = boto3.Session

    kwargs: dict[str, Any] = {"region_name": region}
    if profile is not None:
        kwargs["profile_name"] = profile

    session = session_factory(**kwargs)
    return session.client("bedrock-agentcore", config=config)
