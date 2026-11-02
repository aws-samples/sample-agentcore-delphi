"""The reader's teardown — LC-U6-9 TeardownInventory (U6a; FR13.1 under P-U6-5 A; BR12.1-
BR12.6; NFR10.3, NFR10.4; AC1.7.1-AC1.7.3, AC4.7.2).

The teardown is a documented sequence, not a tool (D-U6-13), held complete by a mechanical
inventory: every resource type the stacks synthesize, in either bucket mode, plus every class
created outside them, has a row saying how it is removed and what is lost.

  T-INF-U6-48  The "Teardown sequence" steps run in this order: read-only inventory; record each
               stack's ContainerUri tag; cdk destroy each arm stack by name; delete the runtime
               log groups; delete the recorded image tags; (create mode only) empty and delete
               the bucket, then destroy Delphi-artifacts; (optional, dedicated account only)
               remove the bootstrap stack; read-only inventory again (BR12.1).
  T-INF-U6-49  The synthesized resource types of both modes, plus the out-of-stack classes
               (service log groups, asset images, the CDK bootstrap stack, the uploaded
               artifact objects), are all inventory rows (BR12.2).
  T-INF-U6-50  The service_created and cdk_bootstrap rows state their bound and whether they
               accrue cost if left (BR12.3).
  T-INF-U6-51  Every destructive step states what is lost before its command, and no teardown
               command forces past a confirmation (BR12.4).
  T-INF-U6-52  The last step lists each read-only inventory command with its expected empty
               result (BR12.5, AC4.7.2).
  T-INF-U6-53  The bucket rows are create_only, and the section states its audience: a reader's
               dedicated account, never the author's (BR12.6, NFR10.4).
  guard        the autouse socket guard is active.
"""
from __future__ import annotations

import socket
import ssl  # noqa: F401 - loaded before the socket guard is installed (U2 M-4)

import boto3  # noqa: F401 - as above
import botocore.config  # noqa: F401 - as above
import pytest

import u6_support as u6

SECTION = "Teardown (a reader's dedicated account only)"
OUT_OF_STACK = ("service log groups", "asset images", "CDK bootstrap stack",
                "uploaded artifact objects")


class NetworkBlocked(RuntimeError):
    """Raised by the autouse guard: these tests must never open a socket."""


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _refuse(*_args, **_kwargs):
        raise NetworkBlocked("tests/test_teardown_inventory.py is offline by construction")

    monkeypatch.setattr(socket, "socket", _refuse)


def _steps() -> list[str]:
    return u6.numbered(u6.section(u6.doc_text(), "Teardown sequence"))


def _rows() -> list[dict[str, str]]:
    return u6.table(u6.section(u6.doc_text(), "Teardown inventory"))


def test_t_inf_u6_48_teardown_runs_in_order():
    steps = _steps()
    order = ["Inventory", "ContainerUri", "cdk destroy Delphi-pinned-nova-lite", "log group",
             "batch-delete-image", "Delphi-artifacts", "bootstrap", "Inventory again"]
    at, pos = 0, []
    for token in order:
        pos.append(next(i for i in range(at, len(steps)) if token in steps[i]))
        at = pos[-1]
    assert pos == sorted(pos) and len(set(pos)) == len(pos), pos
    assert any("cdk destroy Delphi-router" in s for s in steps)


def test_t_inf_u6_49_inventory_covers_every_class():
    classes = " ".join(r["Resource class"] for r in _rows())
    for mode in ("existing", "create"):
        synth = u6.synth(context={"artifactBucket": mode})
        for stack, template in synth.templates.items():
            for res in template["Resources"].values():
                assert f"`{res['Type']}`" in classes, (mode, stack, res["Type"])
    for name in OUT_OF_STACK:
        assert name in classes, name


def test_t_inf_u6_50_service_and_bootstrap_rows_state_bounds():
    rows = {r["Origin"]: r for r in _rows()}
    for origin in ("service_created", "cdk_bootstrap"):
        row = rows[origin]
        assert row["Bound"] and row["Bound"] != "—", origin
        assert row["Accrues cost if left"] in {"yes", "no"}, origin


def test_t_inf_u6_51_loss_is_stated_first_and_nothing_is_forced():
    destructive = [s for s in _steps() if "cdk destroy" in s or " delete" in s or "s3 rm" in s
                   or "s3 rb" in s or "batch-delete-image" in s]
    assert destructive
    for s in destructive:
        assert "**Lost:**" in s and s.index("**Lost:**") < s.index("```"), s[:80]
    body = u6.section(u6.doc_text(), SECTION)
    assert "--force" not in body and "--yes" not in body


def test_t_inf_u6_52_the_final_inventory_states_its_expected_result():
    last = _steps()[-1]
    assert "Inventory again" in last
    assert last.count("expected: empty") >= 4, last


def test_t_inf_u6_53_bucket_rows_are_create_only_and_the_audience_is_stated():
    for r in _rows():
        if "S3" in r["Resource class"] or "artifact objects" in r["Resource class"]:
            assert r["Mode"] == "create_only", r["Resource class"]
    body = u6.section(u6.doc_text(), SECTION)
    assert "dedicated" in body and "never against the author's account" in body


def test_u6_teardown_socket_guard_is_active():
    with pytest.raises(NetworkBlocked):
        socket.socket()
