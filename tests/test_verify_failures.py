"""Failure catalogue tests — U1 LC-6 (verify/failures.py).

The catalogue turns a failure kind into the two things AC1.5.3 requires: what failed, and
the next action the reader can take. BR1.10's check is per-cause, so the table must be
exhaustive over a SEPARATE enum — iterating the table against itself cannot fail.

  T-VF-1  exhaustiveness over FailureKind (the enum, not the FAILURES dict)
  T-VF-2  every entry's summary and next_action are non-empty AND differ
  T-VF-3  no next_action is a restatement of its summary
  T-VF-4  exit codes are 1 or 2, and only the usage kinds are 2
  T-VF-5  render_failure emits both clauses in one message
  T-VF-6  the enum's members equal ENT-005's failure_kind in entities.md, in order
  T-VF-7  agent_error's next action matches the U2 request path: it points at the
          `delphi.agent` failure line and the decision record's error_kind, and no longer
          claims the request path has no error handling (U2 review R-04)
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

from delphi.verify.failures import FAILURES, FailureKind, exit_code_for, render_failure

#: The design record this enum mirrors. Kept as a path, not copied, so the test reads the
#: same document a reviewer does.
_ENTITIES = (
    pathlib.Path(__file__).resolve().parents[1]
    / "aidlc/spaces/default/intents/261008-delphi-blog-pattern/construction"
    / "u1-verifiable-invocation/functional-design/entities.md"
)


def test_catalogue_is_exhaustive_over_the_enum():
    """T-VF-1. Iterating FailureKind, not FAILURES, is what lets this fail."""
    missing = [kind.value for kind in FailureKind if kind not in FAILURES]
    assert missing == [], f"no catalogue row for: {missing}"


def test_every_entry_has_two_distinct_clauses():
    """T-VF-2. AC1.5.3 wants a cause AND a next action, not one sentence twice."""
    for kind, text in FAILURES.items():
        assert text.summary.strip(), f"{kind.value}: empty summary"
        assert text.next_action.strip(), f"{kind.value}: empty next_action"
        assert text.summary.strip() != text.next_action.strip(), f"{kind.value}: identical"


def test_next_action_is_not_a_restatement_of_the_summary():
    """T-VF-3. Structural, not prose review: the action must add words of its own."""
    for kind, text in FAILURES.items():
        novel = set(text.next_action.lower().split()) - set(text.summary.lower().split())
        assert len(novel) >= 3, f"{kind.value}: next_action adds almost nothing new"


def test_exit_codes_are_one_or_two_and_only_usage_errors_are_two():
    """T-VF-4. C5 defines exactly three exit codes; 0 is never a failure."""
    usage = {
        FailureKind.MISSING_ARN,
        FailureKind.MALFORMED_ARN,
        # Added at R-50: a flag typo is a usage error, not an unparseable ARN.
        FailureKind.USAGE_ERROR,
    }
    for kind in FailureKind:
        code = exit_code_for(kind)
        assert code in (1, 2), f"{kind.value}: exit {code}"
        assert code == (2 if kind in usage else 1), f"{kind.value}: wrong exit code"


@pytest.mark.author_record("aidlc")
def test_enum_matches_the_design_record():
    """T-VF-6. Specified in the first plan as T-VF-4 and never written — the id was reused
    for the exit-code test. It is the check that would have caught R-29: the code and the
    design drifted to different failure vocabularies and nothing failed. Order is asserted
    too, because ENT-005 lists the members in reporting order.
    """
    text = _ENTITIES.read_text()
    block = re.search(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    assert block, "entities.md has no yaml block"
    doc = yaml.safe_load(block.group(1))
    ent5 = next(e for e in doc["entities"] if e["id"] == "ENT-005")
    attr = next(a for a in ent5["attributes"] if a["name"] == "failure_kind")
    assert [k.value for k in FailureKind] == attr["allowed_values"]


def test_rendered_failure_carries_both_clauses():
    """T-VF-5. BR1.10: a single-clause message is a bug."""
    msg = render_failure(FailureKind.RECORD_ABSENT, detail="statusCode 200, no record")
    assert FAILURES[FailureKind.RECORD_ABSENT].summary in msg
    assert FAILURES[FailureKind.RECORD_ABSENT].next_action in msg
    assert "statusCode 200, no record" in msg


def test_agent_error_next_action_matches_the_request_path():
    """T-VF-7. Since U2 every failure on the request path emits a decision record carrying
    error_kind, writes one `delphi.agent` line, then re-raises (agent/app.py). The advice
    must send the reader to those, not describe the pre-U2 path that had no handling.
    """
    text = FAILURES[FailureKind.AGENT_ERROR].next_action
    assert "no error handling" not in text
    assert "delphi.agent" in text
    assert "error_kind" in text
