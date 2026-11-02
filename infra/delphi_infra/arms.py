"""The arm set and its one derivation — LC-U6-1 StackModel (contract C4; U6 BR1.1-BR1.4).

`ARMS` is the single source for which stacks the app synthesizes, for the arm count the
documentation states, and (from U6b) for each arm's grants. Every name an arm needs is DERIVED
from its arm string here, in one place, so the stack name, the runtime name, the pool file and
the pinned alias cannot drift apart (ENT-602).

U6 narrows ARMS to the two arms known to serve (relayed READY in the author's account). The
other seven are removed, not hidden behind a flag. U8, which was to restore each one as its
model access (D1) is confirmed, was dropped on 2026-10-20 through its designed fallback, so
ARMS stays at two in this release. Removing an arm from this tuple destroys nothing: no
command in this repository deletes a stack that is not in ARMS (BR1.7).
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass

#: Invariant: this file sits exactly two levels below the repository root (as runtime_stack).
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The arms this app deploys, in order (C4 invariant 1; BR1.1).
ARMS: tuple[str, ...] = ("router", "pinned:nova-lite")

#: Arms the app used to define. U8 was to restore each once its model access was confirmed (D1);
#: U8 was dropped (2026-10-20), so none returns in this release.
#: Listed so the documentation can name them; never synthesized while they are here.
NOT_YET_AVAILABLE: tuple[str, ...] = (
    "pinned:nova-pro", "pinned:llama-3-3-70b", "pinned:mistral-large", "pinned:deepseek-v3-1",
    "pinned:claude-haiku-4-5", "pinned:claude-sonnet-4-6", "ipr",
)

#: The pool file the router container is pointed at (DELPHI_MODELS_FILE), and the default
#: every other arm's container resolves when that variable is unset (config.load_pool).
ROUTER_POOL = "config/models_realpool.yaml"
DEFAULT_POOL = "config/models.yaml"


class ArmSpecError(ValueError):
    """An arm string the stack cannot derive a valid ArmSpec from (fails synth, BR1.4)."""


def stack_name(arm: str) -> str:
    """CloudFormation stack names allow [A-Za-z0-9-]: ':' and '_' map to '-'."""
    return "Delphi-" + arm.replace(":", "-").replace("_", "-")


def runtime_name(arm: str) -> str:
    """CfnRuntime names match [a-zA-Z][a-zA-Z0-9_]{0,47}: ':' and '-' map to '_'."""
    return "delphi_" + arm.replace(":", "_").replace("-", "_")


@dataclass(frozen=True)
class ArmSpec:
    """One arm as the stack sees it (ENT-602). Derived, never declared separately."""

    arm: str
    stack_name: str
    runtime_name: str
    pool_file: str           # repo-relative
    pinned_alias: str | None
    candidate_model_ids: tuple[str, ...]
    reads_artifact: bool     # only the router reads the artifact bucket


def _candidates(root: pathlib.Path, pool_file: str) -> list[dict]:
    import yaml  # infra/requirements.txt declares it (TS-U6-3); imported where it is used

    data = yaml.safe_load((root / pool_file).read_text())
    return list(data.get("candidates") or [])


def arm_spec(arm: str, root: pathlib.Path | None = None) -> ArmSpec:
    """The ArmSpec for `arm`, reading its pool from `root` (default: this repository)."""
    root = pathlib.Path(root) if root is not None else _REPO_ROOT
    if arm == "router":
        pool, alias = ROUTER_POOL, None
        ids = tuple(c["id"] for c in _candidates(root, pool))
    elif arm.startswith("pinned:"):
        pool, alias = DEFAULT_POOL, arm.split(":", 1)[1]
        by_alias = {c["alias"]: c["id"] for c in _candidates(root, pool)}
        if alias not in by_alias:
            raise ArmSpecError(f"arm {arm!r}: pinned alias {alias!r} is not a candidate in "
                               f"{pool}; the container would fail at construction (BR1.4)")
        ids = (by_alias[alias],)
    elif arm == "ipr":
        pool, alias, ids = DEFAULT_POOL, None, ()
    else:
        raise ArmSpecError(f"arm {arm!r} is not 'router', 'pinned:<alias>' or 'ipr'")
    return ArmSpec(arm=arm, stack_name=stack_name(arm), runtime_name=runtime_name(arm),
                   pool_file=pool, pinned_alias=alias, candidate_model_ids=ids,
                   reads_artifact=arm == "router")


def arm_specs(root: pathlib.Path | None = None) -> tuple[ArmSpec, ...]:
    """One ArmSpec per arm in ARMS, in order: what the app, the docs and teardown read."""
    return tuple(arm_spec(a, root) for a in ARMS)
