"""DeployRecord and the human-only approval receipts — LC-U6-6 (U6a; ENT-610 with delta Δ-2;
BR6.6; NFR8.11, NFR8.5; FR11.3.2, FR8.2.3; cicd-pipeline.md CD-U6-2 rules 1-8; D-CG-U6-7 rule 9;
D-CG-U6-9 reply_evidence_rule).

A receipt is a reference into the committed audit trail, never approval text: the validator
proves that the main session showed a question naming the checkpoint and the statement digest,
that the human-turn hook saw a real reply in that session, and that the answer preceded the
guarded action. It does not prove who was at the keyboard (CD-U6-2, what a receipt proves).

  T-INF-U6-33  A record holds the ENT-610 field set and nothing else (no free-text approval
               field); enums, tags, dates and the per-credential receipts are checked; a record
               carrying an account id, an ARN or a repository URI fails; a missing field fails.
               Committed records under evidence/deploy/ are scanned too (vacuous while none
               exists, D-CG-U6-16; the failability lives in the fixtures).
  T-INF-U6-66  Receipt rules 1-9 over fixture shards built from real block shapes
               (u6_support.ShardBuilder), one case per row of CD-U6-2's fixture table plus
               the plan's additions: token absent from the shown question is recorded as
               rule 5(b); G0 missing; G0 answered after the earliest started_at; two G0
               receipts.
  guard        the autouse socket guard is active.

Fixture shards live in tmp_path; the real audit shard is never read here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest

import u6_support as u6

PIN = "Delphi-pinned-nova-lite"
G0_CP, S2_CP = "OQ6-ENTRY:G0:all:a1", f"CONTINUITY-APPROVAL:S2:{PIN}:a1"
TAG_A, TAG_B = "a" * 64, "b" * 64


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_deploy_record.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _dr():
    return u6.infra_module("deploy_record")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class World:
    """A repo root in tmp_path: a record with one audit shard, and evidence/deploy/."""

    def __init__(self, root: pathlib.Path):
        self.root = root
        self.record = root / "rec"
        self.shard_rel = "rec/audit/a.md"
        self.statements = {"G0-all-a1": "Every U6 deploy states an outage window, basis "
                           "unobserved, approved per deploy.\n",
                           f"S2-{PIN}-a1": "Window: from cdk deploy to the first verify exit "
                           "0 or rollback complete. Basis unobserved. No exercised recovery "
                           "path exists yet.\n"}
        self.edit_statement_after = False
        g0, s2 = _sha(self.statements["G0-all-a1"]), _sha(self.statements[f"S2-{PIN}-a1"])
        self.shard = (u6.ShardBuilder()
                      .decision("2026-10-18T07:37:07Z", f"{G0_CP} sha256:{g0} — Accept OQ6?")
                      .human("2026-10-18T07:56:11Z", picked=["Approve"])
                      .replied("2026-10-18T07:56:12Z", f"{G0_CP} sha256:{g0} — Accept OQ6?",
                               "Approve")
                      .answered("2026-10-18T07:56:33Z", "Approve")
                      .decision("2026-10-18T08:10:00Z", f"{S2_CP} sha256:{s2} — Deploy S2?")
                      .human("2026-10-18T08:11:00Z", picked=["Approve"])
                      .replied("2026-10-18T08:11:00Z", f"{S2_CP} sha256:{s2} — Deploy S2?",
                               "Approve")
                      .answered("2026-10-18T08:11:05Z", "Approve"))
        self.extra_shards: dict[str, str] = {}
        self.g0 = {"step": "G0", "stack": "all", "statement_sha256": g0,
                   "receipt": self._receipt(G0_CP, g0, "2026-10-18T07:37:07Z",
                                            "2026-10-18T07:56:33Z")}
        self.extra_g0: list[dict] = []
        self.s2 = {
            "step": "S2", "stack": PIN, "commit": "0123abc", "change_class": "image",
            "credentials": "deploy", "aws_cdk_lib_version": "2.261.0",
            "tag_before": TAG_A, "tag_after": TAG_B,
            "checks": {"DIFF-MATCH": "pass", "T-IMG-U4-1": "pass", "C5-PER-ARM": "pass"},
            "statement_sha256": s2, "started_at": "2026-10-18T09:00:00Z",
            "started_at_source": "stack_events", "window_end_at": "2026-10-18T09:20:00Z",
            "window_end_kind": "verify_exit0", "outcome": "verified",
            "g0_receipt_key": f"{self.shard_rel}#{G0_CP}", "date": "2026-10-18",
            "continuity_approval_receipt": self._receipt(S2_CP, s2, "2026-10-18T08:10:00Z",
                                                         "2026-10-18T08:11:05Z"),
        }
        self.extra_records: list[dict] = []

    def _receipt(self, cp, sha, d, a, rule="a"):
        return {"audit_shard": self.shard_rel, "checkpoint": cp, "statement_sha256": sha,
                "decision_recorded_at": d, "question_answered_at": a,
                "reply_evidence_rule": rule}

    def write(self) -> list[str]:
        audit = self.record / "audit"
        audit.mkdir(parents=True)
        (audit / "a.md").write_text(self.shard.text())
        for name, text in self.extra_shards.items():
            (audit / name).write_text(text)
        ev = self.root / "evidence" / "deploy"
        ev.mkdir(parents=True)
        for stem, text in self.statements.items():
            (ev / f"{stem}.statement.md").write_text(text)
        if self.g0 is not None:
            (ev / "G0-all.json").write_text(json.dumps(self.g0))
        for i, g in enumerate(self.extra_g0):
            (ev / f"G0-all-{i}.json").write_text(json.dumps(g))
        (ev / f"S2-{PIN}.json").write_text(json.dumps(self.s2))
        for i, r in enumerate(self.extra_records):
            (ev / f"{r['step']}-{r['stack']}-{i}.json").write_text(json.dumps(r))
        if self.edit_statement_after:
            with (ev / f"S2-{PIN}-a1.statement.md").open("a") as f:
                f.write("edited after asking\n")
        return _dr().validate_evidence(ev, repo_root=self.root, record_root=self.record)


def _s2_shard(w: World, *, between=None, reply="Approve", picked=("Approve",),
              human_reply=None, replied=True, question_has_token=True, person_reply=None,
              same_second=False, answer_ts="2026-10-18T08:11:05Z", details="Approve"):
    """Rebuild the S2 half of the shard with one variation."""
    s2 = w.s2["statement_sha256"]
    b = u6.ShardBuilder()
    b.blocks = w.shard.blocks[:4]
    b.decision("2026-10-18T08:10:00Z", f"{S2_CP} sha256:{s2} — Deploy S2?")
    b.human("2026-10-18T08:11:00Z", picked=list(picked) if picked else None, reply=human_reply)
    if between:
        b.decision("2026-10-18T08:11:01Z", "Another question?")
    if replied:
        q = f"{S2_CP} sha256:{s2} — Deploy S2?" if question_has_token else "Deploy S2?"
        b.replied("2026-10-18T08:11:00Z" if same_second else "2026-10-18T08:11:02Z", q, reply)
    b.answered(answer_ts, details, person_reply=person_reply)
    w.shard = b


def _case_real_shape(w):
    pass


def _case_same_second(w):
    _s2_shard(w, same_second=True)


def _case_reply_stop(w):
    _s2_shard(w, reply="Stop", picked=("Stop",))


def _case_no_reply_evidence(w):
    _s2_shard(w, replied=False, picked=None)


def _case_typed_answer(w):
    _s2_shard(w, replied=False, picked=None, person_reply="Yes, go ahead with S2.")
    w.s2["continuity_approval_receipt"]["reply_evidence_rule"] = "c"


def _case_decision_between(w):
    _s2_shard(w, between=True)


def _case_reply_command_only(w):
    _s2_shard(w, human_reply="command", picked=None)


def _case_answer_at_started(w):
    _s2_shard(w, answer_ts="2026-10-18T09:00:00Z")
    w.s2["continuity_approval_receipt"]["question_answered_at"] = "2026-10-18T09:00:00Z"


def _case_checkpoint_in_two_shards(w):
    other = u6.ShardBuilder().decision("2026-10-18T08:00:00Z",
                                       f"{S2_CP} sha256:{w.s2['statement_sha256']} — again")
    w.extra_shards["b.md"] = other.text()


def _case_key_in_two_records(w):
    dup = copy.deepcopy(w.s2)
    dup["step"], dup["outcome"] = "S2", "aborted"
    w.extra_records.append(dup)


def _case_statement_edited(w):
    w.edit_statement_after = True


def _case_token_absent_is_rule_b(w):
    _s2_shard(w, question_has_token=False)
    w.s2["continuity_approval_receipt"]["reply_evidence_rule"] = "b"


def _case_rule_letter_wrong(w):
    _s2_shard(w, question_has_token=False)  # evidence is 5(b) but the receipt says (a)


def _case_g0_missing(w):
    w.g0 = None


def _case_g0_after_started(w):
    w.g0["receipt"]["question_answered_at"] = "2026-10-18T09:30:00Z"


def _case_two_g0(w):
    w.extra_g0.append(copy.deepcopy(w.g0))


CASES = [
    ("real shape, Reply Approve", _case_real_shape, True, None),
    ("HUMAN_TURN and QUESTION_REPLIED in one second", _case_same_second, True, None),
    ("Reply Stop", _case_reply_stop, False, "Approve"),
    ("no reply evidence", _case_no_reply_evidence, False, "no hook-recorded human reply"),
    ("typed answer with Person Reply", _case_typed_answer, True, None),
    ("a decision between D and A", _case_decision_between, False, "DECISION_RECORDED"),
    ("HUMAN_TURN Reply command only", _case_reply_command_only, False, "HUMAN_TURN"),
    ("answer at started_at", _case_answer_at_started, False, "started_at"),
    ("checkpoint in two shards", _case_checkpoint_in_two_shards, False, "another shard"),
    ("key in two records", _case_key_in_two_records, False, "reused"),
    ("statement edited after asking", _case_statement_edited, False, "statement"),
    ("token absent: recorded as 5(b)", _case_token_absent_is_rule_b, True, None),
    ("rule letter disagrees", _case_rule_letter_wrong, False, "reply_evidence_rule"),
    ("G0 missing", _case_g0_missing, False, "G0"),
    ("G0 answered after started_at", _case_g0_after_started, False, "G0"),
    ("two G0 receipts", _case_two_g0, False, "G0"),
]


# --- T-INF-U6-33 -------------------------------------------------------------------------
def test_t_inf_u6_33_record_field_set_and_scrub(tmp_path):
    dr = _dr()
    w = World(tmp_path)
    assert dr.record_problems(w.s2) == []
    bad = {
        "account id": {**w.s2, "commit": "9" * 12},
        "an ARN": {**w.s2, "tag_before": "arn:aws:ecr:x"},
        "a repository URI": {**w.s2, "checks": {"DIFF-MATCH": "pass"},
                             "notes_uri": "x.dkr.ecr.y.amazonaws.com/r:t"},
        "approval text": {**w.s2, "continuity_approval_text": "I approve"},
        "missing field": {k: v for k, v in w.s2.items() if k != "started_at"},
        "no receipt on a deploy": {k: v for k, v in w.s2.items()
                                   if k != "continuity_approval_receipt"},
        "bad tag": {**w.s2, "tag_after": "B" * 64},
        "bad outcome": {**w.s2, "outcome": "fine"},
    }
    for what, rec in bad.items():
        assert dr.record_problems(rec), what
    committed = u6.REPO / "evidence" / "deploy"
    if committed.exists():  # vacuous until the human commits a record (D-CG-U6-16)
        assert dr.validate_evidence(committed, repo_root=u6.REPO,
                                    record_root=u6.REPO / dr.RECORD) == []


# --- T-INF-U6-66 -------------------------------------------------------------------------
@pytest.mark.parametrize("name,mutate,ok,needle", CASES, ids=[c[0] for c in CASES])
def test_t_inf_u6_66_receipts_bind_a_human_answer_before_the_action(tmp_path, name, mutate,
                                                                   ok, needle):
    w = World(tmp_path)
    mutate(w)
    problems = w.write()
    if ok:
        assert problems == [], problems
    else:
        assert problems, name
        assert any(needle in p for p in problems), problems


def test_u6_deploy_record_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
