"""Shared helpers for U6's test modules (plan Step 4, D-CG-U6-1). Not collected: no `test_` prefix.

  * `infra_module(name)` imports `infra/delphi_infra/<name>.py`, and `app_module()` loads
    `infra/app.py` under a private name, so a test builds the REAL app through `build_app`
    without running `app.synth()` at import and without writing `infra/cdk.out`.
  * `synth(...)` builds the real app into a temporary outdir and returns an `AppSynth`. It
    tolerates zero image assets in a stack (the image-override case), which U3's
    `SynthResult` deliberately does not. Identical requests are cached for the session.
  * `render(...)` turns a CloudFormation intrinsic into placeholder text (`Ref X` -> `<X>`,
    `Fn::Sub` `${X}` -> `<X>`, `Fn::Join` concatenated). Any other shape FAILS CLOSED with its
    path (INF-U6-16): an unknown shape is never read as "no wildcard".
  * `identity(...)` extracts a stack's frozen identity (INF-U6-15) from a template.

No helper here makes an AWS call. Synthesis needs Node.js (the jsii kernel talks over stdio,
not a socket) and no credential, because every stack is environment-agnostic.
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import pathlib
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass

REPO = pathlib.Path(__file__).resolve().parents[1]
INFRA = REPO / "infra"
BASELINE = INFRA / "baseline" / "live_identity.json"
LOCATION = REPO / "config" / "artifact-location.json"
#: Every arm the app has ever defined; the seven not in ARMS were U8's, and U8 was dropped
#: (2026-10-20). The transforms stay injective over all nine regardless (BR1.3).
NINE_ARMS = ("router", "pinned:nova-lite", "pinned:nova-pro", "pinned:llama-3-3-70b",
             "pinned:mistral-large", "pinned:deepseek-v3-1", "pinned:claude-haiku-4-5",
             "pinned:claude-sonnet-4-6", "ipr")
SERVING_PATHS = ("src", "config", "pyproject.toml", "requirements-agent.txt", "Dockerfile",
                 ".dockerignore")


class RenderError(AssertionError):
    """An intrinsic shape the renderer does not know. Fails the calling test, closed."""


def _infra_on_path() -> None:
    if str(INFRA) not in sys.path:
        sys.path.insert(0, str(INFRA))


def infra_module(name: str):
    """`delphi_infra.<name>`, imported fresh enough for a Red run to fail per test."""
    _infra_on_path()
    return importlib.import_module(f"delphi_infra.{name}")


def app_module():
    """`infra/app.py` loaded as a module. Its `__main__` guard keeps `synth()` from running."""
    _infra_on_path()
    name = "delphi_u6_infra_app"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, INFRA / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        del sys.modules[name]
        raise
    return mod


@dataclass
class AppSynth:
    outdir: pathlib.Path
    templates: dict[str, dict]

    @property
    def stack_names(self) -> set[str]:
        return set(self.templates)

    def assets(self, stack: str) -> dict:
        data = json.loads((self.outdir / f"{stack}.assets.json").read_text())
        return data.get("dockerImages", {})

    def asset_hash(self, stack: str) -> str:
        images = self.assets(stack)
        assert len(images) == 1, f"{stack}: expected one image asset, got {sorted(images)}"
        return next(iter(images))

    def resources(self, stack: str, rtype: str) -> dict[str, dict]:
        return {k: v for k, v in self.templates[stack]["Resources"].items()
                if v["Type"] == rtype}

    def runtime(self, stack: str) -> dict:
        (props,) = [r["Properties"] for r in
                    self.resources(stack, "AWS::BedrockAgentCore::Runtime").values()]
        return props

    def container_uri(self, stack: str):
        return self.runtime(stack)["AgentRuntimeArtifact"]["ContainerConfiguration"][
            "ContainerUri"]

    def statements(self, stack: str) -> list[dict]:
        out = []
        for res in self.resources(stack, "AWS::IAM::ManagedPolicy").values():
            out += res["Properties"]["PolicyDocument"]["Statement"]
        return out

    def outputs(self, stack: str) -> dict:
        return {k: v["Value"] for k, v in self.templates[stack].get("Outputs", {}).items()}


_CACHE: dict[tuple, AppSynth] = {}


def synth(*, context: dict | None = None, build_root: pathlib.Path | None = None,
          cdk_json: pathlib.Path | None = None) -> AppSynth:
    """Build the REAL app (`build_app`) into a fresh temporary outdir and synthesize it."""
    ctx = dict(context or {})
    key = tuple(sorted(ctx.items()))
    if build_root is None and cdk_json is None and key in _CACHE:
        return _CACHE[key]
    if shutil.which("node") is None:
        raise AssertionError("Node.js is not on PATH; the aws-cdk-lib jsii bridge needs it")
    import aws_cdk as cdk

    mod = app_module()
    outdir = pathlib.Path(tempfile.mkdtemp(prefix="u6-synth-"))
    app = cdk.App(outdir=str(outdir), context=ctx)
    kwargs = {}
    if build_root is not None:
        kwargs["build_root"] = build_root
    if cdk_json is not None:
        kwargs["cdk_json"] = cdk_json
    mod.build_app(app, **kwargs)
    asm = app.synth()
    result = AppSynth(outdir, {s.stack_name: s.template for s in asm.stacks})
    if build_root is None and cdk_json is None:
        _CACHE[key] = result
    return result


def existing(**extra) -> AppSynth:
    return synth(context={"artifactBucket": "existing", **extra})


def scratch_tree(dest: pathlib.Path) -> pathlib.Path:
    """A copy of the serving paths, so a test may edit config without touching the repo."""
    for name in SERVING_PATHS:
        src = REPO / name
        if src.is_dir():
            shutil.copytree(src, dest / name, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "*.egg-info", ".DS_Store"))
        else:
            shutil.copy2(src, dest / name)
    return dest


# --- rendering intrinsics (INF-U6-16) -----------------------------------------------------
def render(node, path: str = "$", *, allow_getatt: bool = False) -> str:
    """`allow_getatt` also renders `Fn::GetAtt [Lid, Attr]` as `<GetAtt:Lid.Attr>` (U6b
    T-INF-U6-18 classifies bucket-policy resources by the logical id they name)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict) and len(node) == 1:
        (fn, arg), = node.items()
        if fn == "Ref" and isinstance(arg, str):
            return f"<{arg}>"
        if (allow_getatt and fn == "Fn::GetAtt" and isinstance(arg, list) and len(arg) == 2
                and all(isinstance(a, str) for a in arg)):
            return f"<GetAtt:{arg[0]}.{arg[1]}>"
        if fn == "Fn::Sub" and isinstance(arg, str):
            out, i = [], 0
            while i < len(arg):
                if arg.startswith("${", i):
                    j = arg.index("}", i)
                    out.append(f"<{arg[i + 2:j]}>")
                    i = j + 1
                else:
                    out.append(arg[i])
                    i += 1
            return "".join(out)
        if fn == "Fn::Join" and isinstance(arg, list) and len(arg) == 2:
            sep, parts = arg
            return sep.join(render(p, f"{path}/{fn}/{k}", allow_getatt=allow_getatt)
                            for k, p in enumerate(parts))
    raise RenderError(f"unrenderable intrinsic at {path}: {json.dumps(node)[:160]}")


def location_excluded() -> bool:
    """P-U6-8's branch, read from `.dockerignore` as it stands: True when
    config/artifact-location.json is re-excluded after `!config/` (outside the image context)."""
    lines = [ln.strip() for ln in (REPO / ".dockerignore").read_text().splitlines()]
    return ("!config/" in lines and "config/artifact-location.json" in lines
            and lines.index("config/artifact-location.json") > lines.index("!config/"))


def location_bucket() -> str:
    """The bucket template with the stack tokens rendered as `render` renders them."""
    loc = json.loads(LOCATION.read_text())
    return loc["bucket"].format(account="<AWS::AccountId>", region="<AWS::Region>")


# --- frozen identity (INF-U6-15) ----------------------------------------------------------
def identity(template: dict) -> dict:
    res = template["Resources"]
    runtimes = [r for r in res.values() if r["Type"] == "AWS::BedrockAgentCore::Runtime"]
    assert len(runtimes) == 1, runtimes
    props = runtimes[0]["Properties"]
    return {
        "resources": sorted([k, v["Type"]] for k, v in res.items()
                            if v["Type"] != "AWS::CDK::Metadata"),
        "runtime_name": props["AgentRuntimeName"],
        "network_mode": props["NetworkConfiguration"]["NetworkMode"],
        "authorizer": "AuthorizerConfiguration" in props,
    }


def identity_problems(template: dict, frozen: dict) -> list[str]:
    """What a template lost against its frozen baseline entry; [] when it still holds."""
    now = identity(template)
    missing = [r for r in frozen["resources"] if r not in now["resources"]]
    out = [f"lost {lid} ({rtype})" for lid, rtype in missing]
    for key in ("runtime_name",):
        if now[key] != frozen[key]:
            out.append(f"{key} {frozen[key]!r} -> {now[key]!r}")
    return out


def inbound_problems(template: dict, frozen: dict) -> list[str]:
    now = identity(template)
    return [f"{k} {frozen[k]!r} -> {now[k]!r}" for k in ("network_mode", "authorizer")
            if now[k] != frozen[k]]


class ShardBuilder:
    """An audit shard built from BLOCK SHAPES only (CD-U6-2, the fixture table).

    The shapes are copied from the record's real DECISION_RECORDED -> HUMAN_TURN ->
    QUESTION_REPLIED -> QUESTION_ANSWERED rows (`## <Heading>`, then `**Name**: value` lines,
    blocks separated by a line `---`), with the session id replaced by `<session-uuid>`. The
    real shard is never read by a test.
    """

    SESSION = "<session-uuid>"

    def __init__(self):
        self.blocks: list[tuple[str, list[tuple[str, str]]]] = []

    def _add(self, heading: str, ts: str, event: str, fields: list[tuple[str, str]]):
        self.blocks.append((heading, [("Timestamp", ts), ("Event", event), *fields]))
        return self

    def decision(self, ts, decision, *, options="Approve,Stop", stage="code-generation"):
        return self._add("Decision Recorded", ts, "DECISION_RECORDED",
                         [("Stage", stage), ("Decision", decision), ("Options", options)])

    def human(self, ts, *, picked=None, reply=None, session=SESSION):
        fields = [("Session", session)]
        if reply is not None:
            fields.append(("Reply", reply))
        if picked is not None:
            fields.append(("Picked", json.dumps(picked)))
        return self._add("Human Turn", ts, "HUMAN_TURN", fields)

    def replied(self, ts, question, reply, *, session=SESSION):
        return self._add("Question Replied", ts, "QUESTION_REPLIED",
                         [("Session", session), ("Question", question), ("Reply", reply)])

    def answered(self, ts, details, *, stage="code-generation", person_reply=None):
        fields = [("Stage", stage), ("Details", details)]
        if person_reply is not None:
            fields.append(("Person Reply", person_reply))
        return self._add("Question Answered", ts, "QUESTION_ANSWERED", fields)

    def text(self) -> str:
        parts = []
        for heading, fields in self.blocks:
            body = "\n".join(f"**{k}**: {v}" for k, v in fields)
            parts.append(f"## {heading}\n{body}\n")
        return "# Audit\n\n---\n\n" + "\n---\n\n".join(parts) + "\n---\n"


# --- docs/deployment.md parser (LC-U6-5 DeployRunbook, LC-U6-8, LC-U6-9) -------------------
DEPLOY_DOC = REPO / "docs" / "deployment.md"
_HEADING = re.compile(r"^(#{1,6}) (.+?)\s*$", re.M)
_STEP = re.compile(r"^(G0|S\d{1,2}) — (.+)$")
_ROW = re.compile(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.*?)\s*\|\s*$")


def doc_text() -> str:
    return DEPLOY_DOC.read_text()


def section(text: str, title: str) -> str:
    """The body under the first heading equal to `title`, up to the next heading of the same
    or a higher level. Fails, never returns empty silently, when the heading is absent."""
    for m in _HEADING.finditer(text):
        if m.group(2) == title:
            level = len(m.group(1))
            rest = text[m.end():]
            for n in _HEADING.finditer(rest):
                if len(n.group(1)) <= level:
                    return rest[:n.start()]
            return rest
    raise AssertionError(f"docs/deployment.md has no heading {title!r}")


def code_blocks(text: str) -> list[str]:
    return re.findall(r"^```[^\n]*\n(.*?)^```", text, re.M | re.S)


def numbered(text: str) -> list[str]:
    """Top-level numbered list items (`1. ...`), each with its continuation lines."""
    items, current = [], None
    for line in text.splitlines():
        if re.match(r"^\d+\. ", line):
            if current is not None:
                items.append(current)
            current = line
        elif current is not None and (line.startswith("   ") or line.strip() == ""
                                      or line.startswith("```")):
            current += "\n" + line
        elif current is not None:
            items.append(current)
            current = None
    if current is not None:
        items.append(current)
    return [i.strip() for i in items]


@dataclass
class Card:
    """One runbook step: its id, title, table fields in order, and the raw body."""

    id: str
    title: str
    fields: dict[str, str]
    order: list[str]
    body: str


def cards(text: str | None = None) -> list[Card]:
    runbook = section(text if text is not None else doc_text(), "Migration runbook")
    out = []
    heads = [m for m in _HEADING.finditer(runbook) if len(m.group(1)) == 3]
    for i, m in enumerate(heads):
        step = _STEP.match(m.group(2))
        if not step:
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(runbook)
        body = runbook[m.end():end]
        fields, order = {}, []
        for line in body.splitlines():
            row = _ROW.match(line)
            if row:
                fields[row.group(1)] = row.group(2)
                order.append(row.group(1))
        out.append(Card(step.group(1), step.group(2), fields, order, body))
    return out


def table(text: str) -> list[dict[str, str]]:
    """A Markdown table's rows as dicts keyed by the header cells."""
    lines = [ln for ln in text.splitlines() if ln.startswith("|")]
    if len(lines) < 2:
        return []
    head = [c.strip() for c in lines[0].strip("|").split("|")]
    rows = []
    for ln in lines[2:]:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        rows.append(dict(zip(head, cells)))
    return rows


def renamed(template: dict, old: str, new: str) -> dict:
    """A copy of `template` with one resource's logical id changed (a negative control)."""
    t = copy.deepcopy(template)
    t["Resources"][new] = t["Resources"].pop(old)
    return t
