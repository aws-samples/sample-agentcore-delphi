"""Per-arm execution grants — LC-U6-2 GrantBuilder (U6b; BR3.1-BR3.6; NFR9.1-NFR9.4, NFR9.7,
NFR9.8, NFR9.10; DD-U6-4, DD-U6-7).

The grant set is a pure function of the arm, so nothing about it is written by hand:

    ArmSpec.candidate_model_ids (the pool's `candidates`, never `judge`)
      -> a cross-region profile id: inference-profile/<id> in the deploy region, plus
         foundation-model/<underlying id> in each destination region infra/profile_routing.json
         lists for it
      -> a plain model id: foundation-model/<id> in the deploy region
    a profile with no routing entry -> GrantError naming it, so synth fails (NFR9.2)

Only `bedrock:InvokeModel` is granted: agent/bedrock.py calls `converse` only, so the stream
action is not needed (NFR9.1). Only the router arm reads the artifact bucket, on C6's
grant_prefix `router/*`, which covers the legacy triple, the pointer and every content-addressed
prefix, so the pointer flip needs no IAM change (BR3.3).

The logs resource (BR3.5) is scoped to the AgentCore runtime log groups,
`/aws/bedrock-agentcore/runtimes/*` and their streams, as the AgentCore documentation's
execution-role policy does (LOGS_PROVENANCE). The prefix, not the one runtime's group, because
the policy must exist before the runtime whose id names the group. LOG-GROUP-CONFIRM recorded
the group pattern at S5; the scoping was then verified live on 2026-10-28: decision records
still reached CloudWatch.

The trust policy carries the confused-deputy conditions (OQ5, BR3.6) the AgentCore
documentation gives for the runtime execution role: `aws:SourceAccount` (StringEquals, this
account) and `aws:SourceArn` (ArnLike, any AgentCore resource in this account and region),
recorded with their provenance in TRUST_CONDITION_PROVENANCE.

This module returns plain data. runtime_stack.py turns it into IAM statements, so the action
names a reader looks for stay in that file.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass

#: infra/profile_routing.json, beside this package (outside the image context, TS-U6-4).
ROUTING_FILE = pathlib.Path(__file__).resolve().parents[1] / "profile_routing.json"

#: C6 grant_prefix: every key the router reads lives under it (pointer, triples, legacy).
GRANT_PREFIX = "router/"

#: Geography prefixes of cross-region inference profile ids (`us.amazon.nova-lite-v1:0`).
_PROFILE = re.compile(r"^(us|eu|apac|ca|jp|au|us-gov|global)\.(.+)$")

#: LOG-GROUP-CONFIRM (BR3.5) closed at S5: the runtime log-group name pattern the read-only
#: check recorded (evidence/deploy/S5-Delphi-router.json `log_group_pattern`). `<runtime-id>`
#: is a placeholder: applying it would need the runtime's id from a `Ref`.
CONFIRMED_LOG_GROUP_PATTERN: str | None = "/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT"

def runtime_log_group(runtime_id: str) -> str:
    """The runtime's application log group: CONFIRMED_LOG_GROUP_PATTERN with its id."""
    return CONFIRMED_LOG_GROUP_PATTERN.replace("<runtime-id>", runtime_id)


#: Every AgentCore runtime log group sits under this prefix (the pattern above).
RUNTIME_LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/runtimes/"

#: The logs actions are scoped to RUNTIME_LOG_GROUP_PREFIX rather than '*' (decision
#: 2026-10-28, reversing the 2026-10-20 one after a live check). Setting this False puts them
#: back on '*'; either way changes both arms' templates and needs a deploy.
NARROW_LOGS = True
LOGS_PROVENANCE = {
    "page": "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/"
            "runtime-permissions.html",
    "section": "AgentCore Runtime execution role",
    "read_date": "2026-10-28",
    "verified": "2026-10-28 live deploy: decision records delivered to the runtime log groups",
}

#: OQ5 (BR3.6): the confused-deputy condition keys the AgentCore documentation gives for the
#: runtime execution role's trust policy. A key is added only together with its provenance.
TRUST_CONDITION_KEYS: tuple[str, ...] = ("aws:SourceAccount", "aws:SourceArn")
TRUST_CONDITION_PROVENANCE: dict | None = {
    "page": "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/"
            "runtime-permissions.html",
    "section": "AgentCore Runtime trust policy",
    "read_date": "2026-10-28",
    "read_by": "documentation only, no account call",
}


class GrantError(ValueError):
    """The arm's grant cannot be derived (fails synth, naming what is missing)."""


@dataclass(frozen=True)
class GrantPlan:
    """What one arm's execution policy grants, as resource strings (ENT-605)."""

    arm: str
    model_invoke: tuple[str, ...]      # bedrock:InvokeModel
    logs: tuple[str, ...]              # logs:CreateLogGroup/CreateLogStream/PutLogEvents
    artifact_read: str | None          # s3:GetObject, router only


def load_routing(path: pathlib.Path = ROUTING_FILE) -> dict[str, dict]:
    """The routing file's `profiles`, each entry checked for the fields the grant needs."""
    profiles = json.loads(pathlib.Path(path).read_text())["profiles"]
    for pid, entry in profiles.items():
        m = _PROFILE.match(pid)
        missing = {"underlying_model_id", "destination_regions", "provenance"} - set(entry)
        if not m or missing or not entry["destination_regions"]:
            raise GrantError(f"{path.name}: entry {pid!r} is not a profile id with an "
                             f"underlying model, destination regions and provenance")
        if entry["underlying_model_id"] != m.group(2):
            raise GrantError(f"{path.name}: {pid!r} names underlying model "
                             f"{entry['underlying_model_id']!r}; expected {m.group(2)!r}")
        if not {"source", "read_date", "read_by"} <= set(entry["provenance"]):
            raise GrantError(f"{path.name}: {pid!r} provenance needs source, read_date, read_by")
    return profiles


def model_invoke_resources(arm: str, model_ids, routing: dict, *, region: str,
                           account: str) -> tuple[str, ...]:
    """The exact model ARNs `arm` may invoke, in a stable order, with no '*' anywhere."""
    if not model_ids:
        raise GrantError(f"arm {arm!r} has no candidate models, so no model grant can be "
                         "derived; its grant is designed when the arm returns (U8)")
    out: list[str] = []
    for mid in model_ids:
        m = _PROFILE.match(mid)
        if m is None:
            out.append(f"arn:aws:bedrock:{region}::foundation-model/{mid}")
            continue
        if mid not in routing:
            raise GrantError(f"arm {arm!r}: inference profile {mid!r} has no entry in "
                             f"{ROUTING_FILE.name}; add its destination regions with "
                             "provenance (PROFILE-ROUTING-CONFIRM) before synthesizing")
        entry = routing[mid]
        out.append(f"arn:aws:bedrock:{region}:{account}:inference-profile/{mid}")
        out += [f"arn:aws:bedrock:{r}::foundation-model/{entry['underlying_model_id']}"
                for r in entry["destination_regions"]]
    return tuple(dict.fromkeys(out))


def log_group_arn(pattern: str, *, region: str, account: str) -> str:
    return f"arn:aws:logs:{region}:{account}:log-group:{pattern}"


def log_retention_groups(stack_name: str, *, region: str, account: str) -> list[str]:
    """What aws-cdk-lib's LogRetention function sets retention on: the AgentCore runtime log
    groups, and its own Lambda log group (it caps that one too), named after the stack."""
    return [log_group_arn(RUNTIME_LOG_GROUP_PREFIX + "*", region=region, account=account),
            log_group_arn(f"/aws/lambda/{stack_name}-*", region=region, account=account)]


def logs_resources(pattern: str | None, *, region: str, account: str) -> tuple[str, ...]:
    """'*' when no pattern is given; the given group(s) and their log streams otherwise."""
    if pattern is None:
        return ("*",)
    group = log_group_arn(pattern, region=region, account=account)
    return (group, group + ":log-stream:*")


#: What grant_plan scopes the logs resource to by default: every runtime log group.
DEFAULT_LOG_GROUP_PATTERN: str | None = RUNTIME_LOG_GROUP_PREFIX + "*" if NARROW_LOGS else None


def grant_plan(spec, *, routing: dict, region: str, account: str, bucket: str,
               log_group_pattern: str | None = DEFAULT_LOG_GROUP_PATTERN) -> GrantPlan:
    """The whole grant set of one ArmSpec (DD-U6-4). `bucket` is the rendered bucket name."""
    return GrantPlan(
        arm=spec.arm,
        model_invoke=model_invoke_resources(spec.arm, spec.candidate_model_ids, routing,
                                            region=region, account=account),
        logs=logs_resources(log_group_pattern, region=region, account=account),
        artifact_read=f"arn:aws:s3:::{bucket}/{GRANT_PREFIX}*" if spec.reads_artifact else None,
    )


def check_location_prefix(location: dict) -> None:
    """Every key config/artifact-location.json names sits under GRANT_PREFIX, or the router's
    grant would deny a key it is told to read (C6 invariant 4)."""
    keys = (location["pointer_key"], location["triple_prefix"], location["legacy_prefix"])
    outside = [k for k in keys if not k.startswith(GRANT_PREFIX)]
    if outside:
        raise GrantError(f"artifact-location.json keys {outside} are outside the router's "
                         f"grant prefix {GRANT_PREFIX}*")


def trust_conditions(keys, provenance: dict | None, *, region: str,
                     account: str) -> dict[str, dict[str, str]]:
    """The role trust policy's Condition for the confirmed keys; {} when none is given."""
    if not keys:
        return {}
    if not provenance:
        raise GrantError(f"trust condition keys {list(keys)} need provenance (the AgentCore "
                         "documentation page and read date) before they are added (OQ5)")
    known = {
        "aws:SourceAccount": ("StringEquals", account),
        # The documented form: any AgentCore resource of this account in this region.
        "aws:SourceArn": ("ArnLike", f"arn:aws:bedrock-agentcore:{region}:{account}:*"),
    }
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise GrantError(f"unknown trust condition keys {unknown}")
    out: dict[str, dict[str, str]] = {}
    for k in keys:
        op, value = known[k]
        out.setdefault(op, {})[k] = value
    return out
