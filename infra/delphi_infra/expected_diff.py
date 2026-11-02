"""The template delta between two synthesized app states (U6 NFR8.15; D-CG-U6-10).

A deploy step's expected diff is DERIVED, not hand-written: it is the set of changes between
the committed templates of the previous and the current U6 commit, including resource
`Metadata` (cdk-nag suppressions) and any `AWS::CDK::Metadata` change. This module is that
computation, over plain JSON: no CDK import, no synth, no AWS call.

A change is reported at the deepest path where the two templates differ. A CloudFormation
intrinsic (`Ref`, `Fn::*`) and a list are compared whole, so an `Fn::Join` that changed one
part is one change, the way `cdk diff` shows it.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

SECTIONS = ("Resources", "Outputs", "Parameters", "Conditions")
_MISSING = object()


@dataclass(frozen=True)
class Change:
    stack: str
    resource: str    # a logical id for Resources; "<Section>.<name>" otherwise; "*" a stack
    path: str        # dotted path inside it; "" when the whole entry was added or removed
    before: object
    after: object

    @property
    def kind(self) -> str:
        if self.before is _MISSING:
            return "added"
        if self.after is _MISSING:
            return "removed"
        return "modified"


def _is_leaf(value) -> bool:
    if not isinstance(value, dict):
        return True
    return len(value) == 1 and next(iter(value)).startswith(("Fn::", "Ref"))


def _walk(stack: str, resource: str, path: str, a, b, out: list[Change]) -> None:
    if a == b:
        return
    if a is _MISSING or b is _MISSING or _is_leaf(a) or _is_leaf(b):
        out.append(Change(stack, resource, path, a, b))
        return
    for key in sorted(set(a) | set(b)):
        _walk(stack, resource, f"{path}.{key}" if path else key,
              a.get(key, _MISSING), b.get(key, _MISSING), out)


_SYMBOL = {"added": "[+]", "removed": "[-]", "modified": "[~]"}


class RenderError(ValueError):
    """An intrinsic this module cannot print as text. Never read as 'no change'."""


def render(node, path: str = "$") -> str:
    """A resource expression as text: `Ref X` and `Fn::Sub ${X}` become `<X>`, `Fn::Join` is
    concatenated, a string is itself. Anything else raises (fail closed)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict) and len(node) == 1:
        (fn, arg), = node.items()
        if fn == "Ref" and isinstance(arg, str):
            return f"<{arg}>"
        if fn == "Fn::Sub" and isinstance(arg, str):
            return re.sub(r"\$\{([^}]+)\}", r"<\1>", arg)
        if fn == "Fn::Join" and isinstance(arg, list) and len(arg) == 2:
            sep, parts = arg
            return sep.join(render(p, f"{path}/{k}") for k, p in enumerate(parts))
    raise RenderError(f"unrenderable intrinsic at {path}")


def _policy_pairs(template: dict) -> dict[str, set[tuple[str, str]]]:
    """{policy logical id: {(actions, rendered resource)}} for every managed policy."""
    out: dict[str, set[tuple[str, str]]] = {}
    for lid, res in template.get("Resources", {}).items():
        if res.get("Type") != "AWS::IAM::ManagedPolicy":
            continue
        pairs = out.setdefault(lid, set())
        for i, s in enumerate(res["Properties"]["PolicyDocument"]["Statement"]):
            actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
            resources = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
            for r in resources:
                pairs.add((",".join(sorted(actions)), render(r, f"{lid}/Statement/{i}")))
    return out


def block_lines(before: dict, after: dict) -> list[str]:
    """One stack's expected-diff block, as docs/deployment.md prints it (NFR8.15).

    First the resource lines, `[~] <logical id> <path>` (`[+]`/`[-]` for an added or removed
    entry; `[+] resource <id>` for a whole resource, which is a DIFF-MATCH stop), then the IAM
    statement lines, `[-]`/`[+] <policy id> <actions> on <resource>`, the shape `cdk diff`
    prints in its IAM table.
    """
    lines = []
    for c in template_delta({"stack": before}, {"stack": after}):
        if c.path == "" and not c.resource.startswith(tuple(f"{s}." for s in SECTIONS)):
            lines.append(f"{_SYMBOL[c.kind]} resource {c.resource}")
        else:
            lines.append(f"{_SYMBOL[c.kind]} {c.resource} {c.path}".rstrip())
    a, b = _policy_pairs(before), _policy_pairs(after)
    for lid in sorted(set(a) | set(b)):
        old, new = a.get(lid, set()), b.get(lid, set())
        lines += [f"[-] {lid} {act} on {res}" for act, res in sorted(old - new)]
        lines += [f"[+] {lid} {act} on {res}" for act, res in sorted(new - old)]
    return lines


def template_delta(before: Mapping[str, dict], after: Mapping[str, dict]) -> list[Change]:
    """Every change from `before` to `after`, both {stack name: template}, in a stable order."""
    out: list[Change] = []
    for stack in sorted(set(before) | set(after)):
        if stack not in before or stack not in after:
            out.append(Change(stack, "*", "", before.get(stack, _MISSING),
                              after.get(stack, _MISSING)))
            continue
        for section in SECTIONS:
            a, b = before[stack].get(section, {}), after[stack].get(section, {})
            for name in sorted(set(a) | set(b)):
                resource = name if section == "Resources" else f"{section}.{name}"
                _walk(stack, resource, "", a.get(name, _MISSING), b.get(name, _MISSING), out)
    return out
