"""The `delphi-artifact` publisher — U3 Layer 4 (SD-5, SD-6, SD-10; NFR8.1-NFR8.5, NFR8.8;
AC1.3.3; C6 ledger "consistent triple at any instant").

Every case drives `delphi.train.publish.main` over the hand-written FakeS3 / FakeSTS from
conftest.py, injected through the publisher's client-factory argument. boto3 is never
imported by a test, and no AWS call is made.

  T-PKG-U3-7   `publish <dir> --pool <file>` WITHOUT --execute records ZERO write calls and
               still runs every C3 check: exit 0 on a good artifact, 1 on a bad one.
  T-PKG-U3-8   With --execute: (a) no delete_object(s), no copy_object, and no write whose key
               STARTS WITH router/current/ (trailing slash, so router/current.json is allowed);
               (b) the pointer put is the FINAL write; (c) a fake failing the second triple write
               leaves NO pointer; (d) after EVERY recorded write, following the pointer (if
               present) in that snapshot yields a complete triple whose manifest artifact_hash
               equals the pointer's; (e) every triple put carries IfNoneMatch="*", and the
               pointer put carries IfMatch=<previous etag> when a pointer exists, or
               IfNoneMatch="*" when none does; the previous pointer is printed before it is
               replaced.
  T-PKG-U3-9   Source check on src/delphi/train/publish.py: no 12-digit literal, no
               `profile_name=`, no us-west-2/us-east-1 literal, no module-scope `import boto3`
               (checked with ast). --pool absent: exit 2. No resolvable region: exit 2. FakeSTS
               raising: exit 2.
  T-PKG-U3-13  `verify-remote --pool <file>`: absent pointer exit 1; a pointer naming a
               non-hashed prefix exit 1; a pointer whose triple fails a C3 invariant exit 1,
               naming it; a consistent pointer and triple exit 0. ZERO writes in every case.
  T-PKG-U3-24  Resume and conflict: (1) a fake already holding weights.npz only — the run writes
               the other two objects then the pointer, and does NOT rewrite weights.npz; (2)
               re-publishing from a REGENERATED directory whose manifest differs only in
               `selection` and `weights_uri` writes nothing to the triple and exits 0; (3) a
               remote manifest.json whose trained_aliases differ — exit 1, naming trained_aliases
               and the key, with no write recorded.
  T-PKG-U3-10  (publish case) `publish` on an artifact trained for the wrong pool: exit 1, the
               offending aliases on stderr, and FakeS3 records ZERO calls of any kind — the check
               runs before any S3 call (AC1.3.3).

Partition A: no SDK, no account.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re

import pytest
from conftest import FakeSTS  # the hand-written fake, shared with the other U3 tests

from delphi.config import load_pool

ROOT = pathlib.Path(__file__).resolve().parents[1]
POOL_FILE = "config/models_realpool.yaml"
ACCOUNT, REGION = "111111111111", "us-west-2"
BUCKET = f"delphi-artifacts-{ACCOUNT}-{REGION}"
TRIPLE = ("weights.npz", "scalars.json", "manifest.json")
POINTER = "router/current.json"


class FakeClients:
    """Hand-written stand-in for the publisher's real boto3 client factory."""

    def __init__(self, s3, sts, *, region: str | None = REGION):
        self._s3, self._sts, self._region = s3, sts, region
        self.built: list[tuple[str, str]] = []

    def default_region(self) -> str | None:
        return self._region

    def s3(self, region: str):
        self.built.append(("s3", region))
        return self._s3

    def sts(self, region: str):
        self.built.append(("sts", region))
        return self._sts


@pytest.fixture
def realpool():
    load_pool.cache_clear()
    p = load_pool(ROOT / POOL_FILE)
    load_pool.cache_clear()
    return p


@pytest.fixture
def art(make_artifact, realpool):
    return make_artifact("linear", over=realpool, name="good")


def _hash(d: pathlib.Path) -> str:
    return json.loads((d / "manifest.json").read_text())["artifact_hash"]


def _run(argv, s3, sts=None, *, region=REGION, capsys=None):
    from delphi.train.publish import main

    clients = FakeClients(s3, sts or FakeSTS(), region=region)
    code = main(argv, env={}, clients=clients)
    return code, clients


def _publish(d, *extra):
    return ["publish", str(d), "--pool", POOL_FILE, *extra]


def _old_artifact(make_artifact, realpool):
    """A different, complete, previously-published artifact for consistency checks."""
    return make_artifact("prototype", over=realpool, name="old")


# --- T-PKG-U3-7 ----------------------------------------------------------------------
def test_t_pkg_u3_7_dry_run_writes_nothing_and_exits_0(art, fake_s3_cls):
    s3 = fake_s3_cls()
    code, _ = _run(_publish(art), s3)
    assert code == 0
    assert s3.writes == []


def test_t_pkg_u3_7_dry_run_still_runs_every_check(art, fake_s3_cls, capsys):
    (art / "scalars.json").write_text(json.dumps({"family": "knn", "aliases": []}))
    s3 = fake_s3_cls()
    code, _ = _run(_publish(art), s3)
    err = capsys.readouterr().err
    assert code == 1
    assert "scalars_family" in err and "scalars_aliases" in err
    assert s3.calls == []


# --- T-PKG-U3-8 ----------------------------------------------------------------------
def _seeded_with_previous(make_artifact, realpool, fake_s3_cls, **kw):
    old = _old_artifact(make_artifact, realpool)
    h = _hash(old)
    objects = {f"router/{h}/{n}": (old / n).read_bytes() for n in TRIPLE}
    objects[POINTER] = json.dumps({"artifact_hash": h, "prefix": f"router/{h}/",
                                   "published_at": "2026-10-01T00:00:00Z"}).encode()
    # the legacy flat triple too, which publication must never touch
    objects.update({f"router/current/{n}": (old / n).read_bytes() for n in TRIPLE})
    return fake_s3_cls(objects, **kw), h


def test_t_pkg_u3_8a_never_deletes_copies_or_writes_under_the_legacy_prefix(
        art, make_artifact, realpool, fake_s3_cls):
    s3, _ = _seeded_with_previous(make_artifact, realpool, fake_s3_cls)
    before = {k: v for k, v in s3.store.items() if k.startswith("router/current/")}
    code, _ = _run(_publish(art, "--execute"), s3)
    assert code == 0
    ops = {op for op, *_ in s3.calls}
    assert ops <= {"get_object", "head_object", "put_object"}
    assert [k for k, _ in s3.writes if k.startswith("router/current/")] == []
    assert {k: v for k, v in s3.store.items() if k.startswith("router/current/")} == before


def test_t_pkg_u3_8b_pointer_is_the_final_write(art, fake_s3_cls):
    s3 = fake_s3_cls()
    code, _ = _run(_publish(art, "--execute"), s3)
    h = _hash(art)
    assert code == 0
    assert [k for k, _ in s3.writes] == [f"router/{h}/{n}" for n in TRIPLE] + [POINTER]
    pointer = json.loads(s3.store[POINTER])
    assert pointer["artifact_hash"] == h and pointer["prefix"] == f"router/{h}/"
    assert pointer["published_at"]


def test_t_pkg_u3_8c_failed_triple_write_leaves_no_pointer(art, fake_s3_cls, capsys):
    s3 = fake_s3_cls(fail_write=2)
    code, _ = _run(_publish(art, "--execute"), s3)
    err = capsys.readouterr().err
    assert code == 1
    assert POINTER not in s3.store
    assert f"router/{_hash(art)}/scalars.json" in err


def _consistent(snapshot: dict[str, bytes]) -> bool:
    if POINTER not in snapshot:
        return True
    ptr = json.loads(snapshot[POINTER])
    keys = [ptr["prefix"] + n for n in TRIPLE]
    if not all(k in snapshot for k in keys):
        return False
    return json.loads(snapshot[ptr["prefix"] + "manifest.json"])["artifact_hash"] == (
        ptr["artifact_hash"])


def test_t_pkg_u3_8d_consistent_triple_at_every_instant(art, make_artifact, realpool,
                                                        fake_s3_cls):
    s3, _ = _seeded_with_previous(make_artifact, realpool, fake_s3_cls)
    assert _consistent(s3.store)  # the seeded state is itself consistent (non-vacuous)
    code, _ = _run(_publish(art, "--execute"), s3)
    assert code == 0
    assert len(s3.snapshots) == 4
    assert all(_consistent(snap) for snap in s3.snapshots)


def test_t_pkg_u3_8e_conditional_writes_with_no_previous_pointer(art, fake_s3_cls):
    s3 = fake_s3_cls()
    code, _ = _run(_publish(art, "--execute"), s3)
    assert code == 0
    for key, kw in s3.writes[:-1]:
        assert kw["IfNoneMatch"] == "*" and kw["IfMatch"] is None, key
    key, kw = s3.writes[-1]
    assert key == POINTER and kw["IfNoneMatch"] == "*" and kw["IfMatch"] is None


def test_t_pkg_u3_8e_pointer_replacement_uses_if_match_and_prints_previous(
        art, make_artifact, realpool, fake_s3_cls, capsys):
    s3, old_hash = _seeded_with_previous(make_artifact, realpool, fake_s3_cls)
    prev_etag = s3.get_object(Bucket=BUCKET, Key=POINTER)["ETag"]
    s3.calls.clear()
    code, _ = _run(_publish(art, "--execute"), s3)
    out = capsys.readouterr().out
    assert code == 0
    key, kw = s3.writes[-1]
    assert key == POINTER and kw["IfMatch"] == prev_etag and kw["IfNoneMatch"] is None
    assert old_hash in out
    assert out.index(old_hash) < out.rindex(_hash(art))


def test_t_pkg_u3_8e_concurrent_pointer_change_is_a_conflict(art, make_artifact, realpool,
                                                             fake_s3_cls, capsys):
    s3, _ = _seeded_with_previous(make_artifact, realpool, fake_s3_cls)
    real_put = s3.put_object

    def racing_put(Bucket, Key, Body, **kw):
        if Key == POINTER:  # another publisher moved the pointer between our read and write
            s3.store[POINTER] = b'{"artifact_hash": "x", "prefix": "router/x/"}'
        return real_put(Bucket=Bucket, Key=Key, Body=Body, **kw)

    s3.put_object = racing_put
    code, _ = _run(_publish(art, "--execute"), s3)
    assert code == 1
    assert POINTER in capsys.readouterr().err


# --- T-PKG-U3-9 ----------------------------------------------------------------------
def test_t_pkg_u3_9_publisher_source_carries_no_account_region_or_profile():
    src = (ROOT / "src" / "delphi" / "train" / "publish.py").read_text()
    assert not re.search(r"\d{12}", src)
    assert "profile_name=" not in src
    assert "us-west-2" not in src and "us-east-1" not in src
    tree = ast.parse(src)
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name.split(".")[0] for n in top for a in n.names} | {
        (n.module or "").split(".")[0] for n in top if isinstance(n, ast.ImportFrom)}
    assert not names & {"boto3", "botocore"}, "boto3 must be imported lazily"


def test_t_pkg_u3_9_pool_is_required(art, fake_s3_cls):
    s3 = fake_s3_cls()
    code, _ = _run(["publish", str(art), "--execute"], s3)
    assert code == 2 and s3.calls == []


def test_t_pkg_u3_9_no_resolvable_region_is_a_usage_error(art, fake_s3_cls, capsys):
    s3 = fake_s3_cls()
    code, clients = _run(_publish(art, "--execute"), s3, region=None)
    assert code == 2 and s3.calls == [] and clients.built == []
    assert "region" in capsys.readouterr().err


def test_t_pkg_u3_9_region_flag_wins(art, fake_s3_cls):
    s3 = fake_s3_cls()
    code, clients = _run(_publish(art, "--region", "eu-west-1"), s3, region=None)
    assert code == 0
    assert ("s3", "eu-west-1") in clients.built
    assert {b for _, b, _, _ in s3.calls} == {f"delphi-artifacts-{ACCOUNT}-eu-west-1"}


def test_t_pkg_u3_9_unresolvable_account_is_a_usage_error(art, fake_s3_cls, fake_sts_cls,
                                                          capsys):
    s3 = fake_s3_cls()
    code, _ = _run(_publish(art, "--execute"), s3, fake_sts_cls(fail=True))
    assert code == 2 and s3.calls == []
    assert "account" in capsys.readouterr().err


# --- T-PKG-U3-13 ---------------------------------------------------------------------
def _verify():
    return ["verify-remote", "--pool", POOL_FILE]


def test_t_pkg_u3_13_absent_pointer_exits_1(art, fake_s3_cls):
    s3 = fake_s3_cls({f"router/current/{n}": (art / n).read_bytes() for n in TRIPLE})
    code, _ = _run(_verify(), s3)
    assert code == 1 and s3.writes == []


def test_t_pkg_u3_13_non_hashed_prefix_exits_1(art, fake_s3_cls):
    h = _hash(art)
    s3 = fake_s3_cls({**{f"router/current/{n}": (art / n).read_bytes() for n in TRIPLE},
                      POINTER: json.dumps({"artifact_hash": h,
                                           "prefix": "router/current/"}).encode()})
    code, _ = _run(_verify(), s3)
    assert code == 1 and s3.writes == []


def test_t_pkg_u3_13_c3_failure_exits_1_and_names_it(make_artifact, fake_s3_cls, capsys):
    wrong = make_artifact("linear", name="wrongpool")  # trained on the conftest pool
    h = _hash(wrong)
    s3 = fake_s3_cls({**{f"router/{h}/{n}": (wrong / n).read_bytes() for n in TRIPLE},
                      POINTER: json.dumps({"artifact_hash": h,
                                           "prefix": f"router/{h}/"}).encode()})
    code, _ = _run(_verify(), s3)
    assert code == 1 and s3.writes == []
    assert "alias_subset" in capsys.readouterr().err


def test_t_pkg_u3_13_consistent_pointer_and_triple_exits_0(art, fake_s3_cls):
    s3 = fake_s3_cls()
    assert _run(_publish(art, "--execute"), s3)[0] == 0
    s3.calls.clear()
    code, _ = _run(_verify(), s3)
    assert code == 0 and s3.writes == []


# --- T-PKG-U3-24 ---------------------------------------------------------------------
def test_t_pkg_u3_24_resume_writes_only_what_is_missing(art, fake_s3_cls):
    h = _hash(art)
    s3 = fake_s3_cls({f"router/{h}/weights.npz": (art / "weights.npz").read_bytes()})
    code, _ = _run(_publish(art, "--execute"), s3)
    assert code == 0
    assert [k for k, _ in s3.writes] == [f"router/{h}/scalars.json", f"router/{h}/manifest.json",
                                         POINTER]


def test_t_pkg_u3_24_regenerated_directory_writes_nothing_to_the_triple(art, fake_s3_cls,
                                                                        tmp_path):
    import shutil

    s3 = fake_s3_cls()
    assert _run(_publish(art, "--execute"), s3)[0] == 0
    regen = tmp_path / "regen"
    shutil.copytree(art, regen)
    m = json.loads((regen / "manifest.json").read_text())
    m["selection"] = {"capture": 0.5, "p90_latency_ms": 99.0}
    m["weights_uri"] = "weights-elsewhere.npz"
    (regen / "manifest.json").write_text(json.dumps(m))
    s3.calls.clear()

    code, _ = _run(_publish(regen, "--execute"), s3)
    h = _hash(art)
    assert code == 0
    assert [k for k, _ in s3.writes if k.startswith(f"router/{h}/")] == []


def test_t_pkg_u3_24_remote_manifest_conflict_exits_1_with_no_write(art, fake_s3_cls, capsys):
    h = _hash(art)
    remote = json.loads((art / "manifest.json").read_text())
    remote["trained_aliases"] = list(reversed(remote["trained_aliases"]))
    s3 = fake_s3_cls({f"router/{h}/manifest.json": json.dumps(remote).encode()})
    code, _ = _run(_publish(art, "--execute"), s3)
    err = capsys.readouterr().err
    assert code == 1
    assert s3.writes == []
    assert "trained_aliases" in err and f"router/{h}/manifest.json" in err


def test_t_pkg_u3_24_remote_weights_conflict_exits_1(art, fake_s3_cls, capsys):
    h = _hash(art)
    s3 = fake_s3_cls({f"router/{h}/weights.npz": b"not an npz"})
    code, _ = _run(_publish(art, "--execute"), s3)
    assert code == 1 and s3.writes == []
    assert f"router/{h}/weights.npz" in capsys.readouterr().err


# --- T-PKG-U3-10 (publish case) ------------------------------------------------------
def test_t_pkg_u3_10_publish_wrong_pool_fails_before_any_s3_call(make_artifact, fake_s3_cls,
                                                                 capsys):
    wrong = make_artifact("linear", name="wrong")  # cheap/mid/strong/alt, not the realpool
    s3 = fake_s3_cls()
    code, clients = _run(_publish(wrong, "--execute"), s3)
    err = capsys.readouterr().err
    assert code == 1
    assert s3.calls == [] and clients.built == []
    for alias in ("cheap", "mid", "strong", "alt"):
        assert alias in err


# --- the other subcommands -----------------------------------------------------------
def test_check_subcommand_exit_codes(art, make_artifact):
    from delphi.train.publish import main

    assert main(["check", str(art), "--pool", POOL_FILE], env={}) == 0
    assert main(["check", str(make_artifact("knn", name="w")), "--pool", POOL_FILE],
                env={}) == 1
    assert main(["check", str(art)], env={}) == 2


def test_help_exits_0():
    from delphi.train.publish import main

    assert main(["--help"], env={}) == 0
