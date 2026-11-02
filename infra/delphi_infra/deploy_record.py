"""DeployRecord and its receipts — LC-U6-6 (ENT-610 with delta Δ-2; BR6.6; NFR8.5, NFR8.11;
FR11.3.2, FR8.2.3; cicd-pipeline.md CD-U6-2; plan D-CG-U6-7, D-CG-U6-9).

A DeployRecord is what the HUMAN commits after a runbook step, at
`evidence/deploy/<step>-<stack>.json`. It holds stack names, 64-hex tags, commits, dates, check
outcomes and receipt REFERENCES only. It never holds approval text: a field an agent can write
is not consent (P-U6-15). Each human-only approval is a **receipt**, a pointer into the
committed audit trail, where the engine and its human-turn hook recorded, in order, the
question shown (DECISION_RECORDED, whose Decision begins with the checkpoint token and the
statement digest), the person's turn (HUMAN_TURN), the hook's copy of the reply
(QUESTION_REPLIED, a picker) or the kept typed reply (QUESTION_ANSWERED's Person Reply), and
the answer (QUESTION_ANSWERED).

`validate_evidence` checks every committed record against the field set and receipt rules 1-9
of CD-U6-2 (9 is D-CG-U6-7's G0 binding). What it proves, and what it does not, is stated in
CD-U6-2 (what a receipt proves): it proves an engine-observed human reply named this
checkpoint and this statement before the guarded action started on AWS's clock; it does not
prove who was at the keyboard, and a hand-edited, well-formed shard is detectable only in git
history. It runs after the fact; the runbook's order is what prevents a deploy without
approval.

Lives under infra/, outside the image build context: serving code must never depend on the
AI-DLC audit format.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from dataclasses import dataclass

#: The intent record this repository's receipts point into (repo-relative).
RECORD = "aidlc/spaces/default/intents/261008-delphi-blog-pattern"

CHECKPOINT = re.compile(r"^(OQ6-ENTRY|CONTINUITY-APPROVAL|BUCKET-WRITE-CONFIRM|SPEND-CONFIRM)"
                        r":(G0|S\d{1,2}(?:-rollback)?):([A-Za-z0-9-]+):a(\d+)$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
ISO_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CHANGE_CLASSES = {"image", "iam_and_outputs", "artifact_uri_flip", "rollback", "roll_forward",
                  "create_bucket_stack", "publish", "capture"}
CREDENTIALS = {"none", "read_only", "data_plane_invoke", "bucket_write", "deploy"}
STARTED_AT_SOURCES = {"stack_events", "object_last_modified", "first_record_timestamp"}
WINDOW_END_KINDS = {"verify_exit0", "rollback_complete"}
OUTCOMES = {"verified", "rolled_back", "aborted", "stopped"}
CHECK_IDS = {"DIFF-MATCH", "IMAGE-NEUTRAL", "PROFILE-ROUTING-CONFIRM", "T-IMG-U4-1",
             "C5-PER-ARM", "DS-U4-1", "DS-U2-1a", "DS-U2-1b", "LOG-GROUP-CONFIRM",
             "ENVELOPE-CONFIRM", "VERIFY-REMOTE", "POST-FLIP-HASH", "ROLLBACK-EXERCISE"}
CHECK_VALUES = {"pass", "fail", "not_run"}

REQUIRED = {"step", "stack", "commit", "change_class", "credentials", "aws_cdk_lib_version",
            "tag_before", "tag_after", "checks", "statement_sha256", "started_at",
            "started_at_source", "window_end_at", "window_end_kind", "outcome",
            "g0_receipt_key", "date"}
OPTIONAL = {"expected_asset_hash_s10", "continuity_approval_receipt", "bucket_write_receipt",
            "spend_receipt", "tree_clean_for_serving_paths", "diff_matched_expected",
            "profile_routing_regions", "log_group_pattern", "envelope"}
#: Receipt field -> the checkpoint kind it must carry.
RECEIPT_FIELDS = {"continuity_approval_receipt": "CONTINUITY-APPROVAL",
                  "bucket_write_receipt": "BUCKET-WRITE-CONFIRM",
                  "spend_receipt": "SPEND-CONFIRM"}
RECEIPT_KEYS = {"audit_shard", "checkpoint", "statement_sha256", "decision_recorded_at",
                "question_answered_at", "reply_evidence_rule"}
#: Substrings that would carry an account-bearing identifier into a committed record.
FORBIDDEN = ("arn:", ".dkr.ecr.", "amazonaws.com")


# --- the audit shard ---------------------------------------------------------------------
@dataclass(frozen=True)
class Block:
    ordinal: int
    event: str
    ts: str
    fields: dict

    def get(self, name: str) -> str | None:
        return self.fields.get(name)


_FIELD = re.compile(r"^\*\*([^*]+)\*\*: ?(.*)$")


def parse_shard(text: str) -> list[Block]:
    """Blocks split on lines that are exactly `---`; first occurrence of each field wins.

    A block's ORDINAL is the ordering authority: the shard is append-only, and its timestamps
    have one-second resolution, so they are a consistency check, never the order.
    """
    blocks, current = [], []
    for line in text.splitlines() + ["---"]:
        if line == "---":
            fields: dict[str, str] = {}
            for row in current:
                m = _FIELD.match(row)
                if m and m.group(1) not in fields:
                    fields[m.group(1)] = m.group(2).strip()
            if "Event" in fields:
                blocks.append(Block(len(blocks), fields["Event"], fields.get("Timestamp", ""),
                                    fields))
            current = []
        else:
            current.append(line)
    return blocks


# --- receipts ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Receipt:
    audit_shard: str
    checkpoint: str
    statement_sha256: str
    decision_recorded_at: str
    question_answered_at: str
    reply_evidence_rule: str

    @property
    def key(self) -> str:
        """Uniqueness key. Timestamps are NOT in it: one-second resolution makes them collide."""
        return f"{self.audit_shard}#{self.checkpoint}"

    @classmethod
    def from_dict(cls, data: object) -> Receipt:
        if not isinstance(data, dict) or set(data) != RECEIPT_KEYS:
            raise ValueError(f"a receipt has exactly the fields {sorted(RECEIPT_KEYS)}")
        return cls(**{k: str(data[k]) for k in RECEIPT_KEYS})


def _first_after(blocks, start, event, *, where=lambda b: True):
    for b in blocks[start + 1:]:
        if b.event == event and where(b):
            return b
    return None


def _between(blocks, a: Block, b: Block, events: set[str]) -> list[Block]:
    return [x for x in blocks[a.ordinal + 1:b.ordinal] if x.event in events]


def _reply_evidence(blocks, d: Block, h: Block, a: Block, checkpoint: str) -> tuple[str, list]:
    """Rule 5: which hook-recorded human reply backs the answer, (a) then (b) then (c)."""
    approved = (a.get("Details") or "").startswith("Approve")
    for r in _between(blocks, d, a, {"QUESTION_REPLIED"}):
        if r.get("Session") == h.get("Session") and checkpoint in (r.get("Question") or ""):
            if r.get("Reply") != "Approve":
                return "a", [f"QUESTION_REPLIED Reply is {r.get('Reply')!r}, not 'Approve'"]
            return "a", [] if approved else ["Details does not begin with 'Approve'"]
    if h.get("Picked") is not None:
        try:
            picked = json.loads(h.get("Picked"))
        except ValueError:
            picked = None
        if picked != ["Approve"]:
            return "b", [f"HUMAN_TURN Picked is {h.get('Picked')}, not [\"Approve\"]"]
        return "b", [] if approved else ["Details does not begin with 'Approve'"]
    if (a.get("Person Reply") or "").strip():
        return "c", [] if approved else ["Details does not begin with 'Approve'"]
    return "d", ["no hook-recorded human reply"]


def receipt_problems(r: Receipt, *, repo_root: pathlib.Path, record_root: pathlib.Path,
                     step: str, stack: str, started_at: str, statement_sha256: str) -> list[str]:
    """Rules 1-7 of CD-U6-2 for one receipt. [] when it holds."""
    out: list[str] = []
    m = CHECKPOINT.match(r.checkpoint)
    if not m:
        return [f"{r.checkpoint}: not a checkpoint token"]
    if (m.group(2), m.group(3)) != (step, stack):
        out.append(f"{r.checkpoint}: names step/stack {m.group(2)}/{m.group(3)}, the record is "
                   f"{step}/{stack}")
    audit = (record_root / "audit").resolve()
    shard_path = (repo_root / r.audit_shard).resolve()
    if audit not in shard_path.parents or not shard_path.is_file():           # rule 1
        return out + [f"{r.checkpoint}: audit shard {r.audit_shard} is not in the record"]
    blocks = parse_shard(shard_path.read_text())
    prefix = f"{r.checkpoint} sha256:{r.statement_sha256}"
    ds = [b for b in blocks if b.event == "DECISION_RECORDED"
          and (b.get("Decision") or "").startswith(prefix)]
    if len(ds) != 1:                                                          # rule 2
        return out + [f"{r.checkpoint}: {len(ds)} DECISION_RECORDED rows name it, not 1"]
    d = ds[0]
    for other in sorted(audit.glob("*.md")):
        if other != shard_path and any(
                b.event == "DECISION_RECORDED"
                and (b.get("Decision") or "").startswith(r.checkpoint + " ")
                for b in parse_shard(other.read_text())):
            out.append(f"{r.checkpoint}: also asked in another shard ({other.name})")
    if d.ts != r.decision_recorded_at:
        out.append(f"{r.checkpoint}: decision_recorded_at {r.decision_recorded_at} != {d.ts}")
    if "Approve" not in (d.get("Options") or "").split(","):
        out.append(f"{r.checkpoint}: the question did not offer 'Approve'")
    h = _first_after(blocks, d.ordinal, "HUMAN_TURN", where=lambda b: b.get("Reply") is None)
    a = _first_after(blocks, h.ordinal, "QUESTION_ANSWERED") if h else None
    if h is None or a is None or _between(blocks, d, h, {"DECISION_RECORDED",
                                                         "QUESTION_ANSWERED"}):  # rule 3
        return out + [f"{r.checkpoint}: no qualifying HUMAN_TURN answered this question"]
    if a.get("Stage") != d.get("Stage") or _between(blocks, d, a, {"DECISION_RECORDED"}):
        out.append(f"{r.checkpoint}: another DECISION_RECORDED or stage lies between the "
                   "question and its answer")                                 # rule 4
    if a.ts != r.question_answered_at:
        out.append(f"{r.checkpoint}: question_answered_at {r.question_answered_at} != {a.ts}")
    rule, problems = _reply_evidence(blocks, d, h, a, r.checkpoint)           # rule 5
    out += [f"{r.checkpoint}: {p}" for p in problems]
    if not problems and rule != r.reply_evidence_rule:
        out.append(f"{r.checkpoint}: reply_evidence_rule is {r.reply_evidence_rule!r}, the "
                   f"evidence is 5({rule})")
    if not (d.ts <= h.ts <= a.ts):                                             # rule 6
        out.append(f"{r.checkpoint}: rows are out of time order")
    if not a.ts < started_at:
        out.append(f"{r.checkpoint}: answered at {a.ts}, not strictly before started_at "
                   f"{started_at}; re-ask with a new attempt suffix")
    stem = f"{m.group(2)}-{m.group(3)}-a{m.group(4)}"                          # rule 7
    statement = repo_root / "evidence" / "deploy" / f"{stem}.statement.md"
    actual = hashlib.sha256(statement.read_bytes()).hexdigest() if statement.is_file() else None
    if not (actual == r.statement_sha256 == statement_sha256):
        out.append(f"{r.checkpoint}: statement {statement.name} digest {actual} does not equal "
                   "the receipt's and the record's")
    return out


# --- records -----------------------------------------------------------------------------
def _scrub_problems(record: dict) -> list[str]:
    # The ONE identifier definition is U5's scrub check; the CDK venv has no `delphi`
    # installed, so its source tree is put on the path rather than the patterns copied.
    src = str(pathlib.Path(__file__).resolve().parents[2] / "src")
    if src not in sys.path:
        sys.path.append(src)
    from delphi.tools.scrub_check import scan_blob

    data = json.dumps(record, sort_keys=True).encode()
    out = [f"identifier ({h.cls}) in the record" for h in scan_blob("record", data)]
    out += [f"forbidden substring {s!r} in the record" for s in FORBIDDEN if s.encode() in data]
    return out


def record_problems(record: dict) -> list[str]:
    """T-INF-U6-33: the ENT-610 field set (Δ-2), its value shapes, and scrub safety."""
    if not isinstance(record, dict):
        return ["a record is a JSON object"]
    out = [f"missing field {k}" for k in sorted(REQUIRED - set(record))]
    out += [f"unknown field {k}" for k in sorted(set(record) - REQUIRED - OPTIONAL)]
    get = record.get
    enums = (("change_class", CHANGE_CLASSES), ("credentials", CREDENTIALS),
             ("started_at_source", STARTED_AT_SOURCES), ("outcome", OUTCOMES))
    out += [f"{k} {get(k)!r} not in {sorted(v)}" for k, v in enums if k in record
            and get(k) not in v]
    if get("window_end_kind") is not None and get("window_end_kind") not in WINDOW_END_KINDS:
        out.append(f"window_end_kind {get('window_end_kind')!r} is not allowed")
    if "commit" in record and not COMMIT.match(str(get("commit"))):
        out.append("commit is not a git sha")
    for k in ("tag_before", "tag_after", "expected_asset_hash_s10", "statement_sha256"):
        v = get(k)
        needed = k == "statement_sha256" or (k != "expected_asset_hash_s10"
                                             and get("credentials") == "deploy")
        if (v is not None or needed) and not HEX64.match(str(v)):
            out.append(f"{k} is not 64 lowercase hex")
    for k in ("started_at", "window_end_at"):
        if get(k) is not None and not ISO_SECONDS.match(str(get(k))):
            out.append(f"{k} is not ISO-8601 UTC seconds")
    if "date" in record and not DATE.match(str(get("date"))):
        out.append("date is not YYYY-MM-DD")
    checks = get("checks", {})
    if not isinstance(checks, dict) or any(c not in CHECK_IDS or v not in CHECK_VALUES
                                           for c, v in checks.items()):
        out.append(f"checks must map ids in {sorted(CHECK_IDS)} to pass|fail|not_run")
    needs = {"continuity_approval_receipt": get("credentials") == "deploy",
             "bucket_write_receipt": get("credentials") == "bucket_write",
             "spend_receipt": get("change_class") == "capture"}
    out += [f"missing {k}" for k, need in needs.items() if need and k not in record]
    for k in RECEIPT_FIELDS:
        if k in record:
            try:
                Receipt.from_dict(get(k))
            except ValueError as exc:
                out.append(f"{k}: {exc}")
    return out + _scrub_problems(record)


def validate_evidence(evidence_dir: pathlib.Path, *, repo_root: pathlib.Path,
                      record_root: pathlib.Path) -> list[str]:
    """Every committed record and receipt under `evidence_dir`, plus rules 8 and 9."""
    evidence_dir, out, used = pathlib.Path(evidence_dir), [], {}
    loaded = []
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            loaded.append((path.name, json.loads(path.read_text())))
        except ValueError as exc:
            out.append(f"{path.name}: not JSON ({exc})")
    g0s = [(n, r) for n, r in loaded if isinstance(r, dict) and r.get("step") == "G0"]
    records = [(n, r) for n, r in loaded if (n, r) not in g0s]
    starts = sorted(r["started_at"] for _, r in records
                    if isinstance(r, dict) and isinstance(r.get("started_at"), str))
    earliest = starts[0] if starts else "9999-12-31T23:59:59Z"

    valid_g0 = []
    for name, g in g0s:
        try:
            rec = Receipt.from_dict(g.get("receipt"))
        except ValueError as exc:
            out.append(f"{name}: G0 {exc}")
            continue
        if not rec.checkpoint.startswith("OQ6-ENTRY:G0:all:"):
            out.append(f"{name}: G0 receipt is not an OQ6-ENTRY:G0:all checkpoint")
            continue
        probs = receipt_problems(rec, repo_root=repo_root, record_root=record_root, step="G0",
                                 stack="all", started_at=earliest,
                                 statement_sha256=str(g.get("statement_sha256")))
        out += [f"{name}: G0 {p}" for p in probs]
        if not probs:
            valid_g0.append(rec)
    if records and len(valid_g0) != 1:                                        # rule 9
        out.append(f"G0: {len(valid_g0)} valid OQ6-ENTRY receipts answered before the earliest "
                   "started_at; exactly 1 is required")
    for rec in valid_g0:
        used.setdefault(rec.key, []).append("G0")

    for name, r in records:
        probs = record_problems(r)
        if not probs and valid_g0 and r["g0_receipt_key"] != valid_g0[0].key:
            probs.append("g0_receipt_key does not name the G0 receipt")
        for field in RECEIPT_FIELDS:
            if isinstance(r, dict) and field in r and not probs:
                rec = Receipt.from_dict(r[field])
                kind = rec.checkpoint.split(":", 1)[0]
                if kind != RECEIPT_FIELDS[field]:
                    probs.append(f"{field} carries a {kind} checkpoint")
                probs += receipt_problems(rec, repo_root=repo_root, record_root=record_root,
                                          step=r["step"], stack=r["stack"],
                                          started_at=r["started_at"],
                                          statement_sha256=r["statement_sha256"])
                used.setdefault(rec.key, []).append(name)
        out += [f"{name}: {p}" for p in probs]
    out += [f"receipt {k} reused by {v}" for k, v in sorted(used.items()) if len(v) > 1]  # 8
    return out
