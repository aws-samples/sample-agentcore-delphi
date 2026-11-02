"""Shared fixtures for the Delphi test suite.

Provides a small synthetic pool and response matrices with known structure so the
statistical tests have deterministic, hand-checkable inputs.
"""
from __future__ import annotations

import os
import pathlib

import numpy as np
import pytest

from delphi.config import Candidate, Pool, Price, load_pool


# --- U5: suite guards (SD-U5-7; NFR4.1, NFR4.2) -----------------------------------------
@pytest.fixture(autouse=True)
def _isolate_pool_cache():
    """Clear load_pool's lru_cache around every test, so a pool cached by one test (for
    example from a DELPHI_MODELS_FILE it pointed at a temporary file) never reaches the next.
    The manual cache_clear() calls elsewhere stay; this makes forgetting one harmless."""
    load_pool.cache_clear()
    yield
    load_pool.cache_clear()


#: Reason prefix of the one skip CI accepts: a test that checks the author's own working
#: record (deploy evidence, the design record) against files this sample does not ship.
AUTHOR_RECORD_SKIP = "author record not shipped: "
_REPO = pathlib.Path(__file__).resolve().parents[1]


def pytest_runtest_setup(item):
    """`@pytest.mark.author_record(path, ...)`: skip, with AUTHOR_RECORD_SKIP, when any named
    repo-relative path is absent. Where the record is present the test runs as before."""
    for mark in item.iter_markers("author_record"):
        missing = [p for p in mark.args if not (_REPO / p).exists()]
        if missing:
            pytest.skip(AUTHOR_RECORD_SKIP + ", ".join(missing))


def _skip_reason(report) -> str:
    longrepr = report.longrepr
    return str(longrepr[2]) if isinstance(longrepr, tuple) and len(longrepr) == 3 else ""


def pytest_sessionfinish(session, exitstatus):
    """Under DELPHI_FAIL_ON_SKIP=1 (exported by scripts/ci.sh) any skipped test fails the
    run: a skip is a criterion that cannot fail. The one exception is an author_record skip,
    which is counted and named but accepted. A plain local `pytest` is unaffected, and
    deselected tests (the `image` partition) are not skips."""
    if os.environ.get("DELPHI_FAIL_ON_SKIP") != "1":
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    reports = reporter.stats.get("skipped", []) if reporter is not None else []
    accepted = [r for r in reports if AUTHOR_RECORD_SKIP in _skip_reason(r)]
    if accepted:
        reporter.write_line(f"ci: {len(accepted)} author-record test(s) skipped (record not "
                            "shipped); accepted")
    other = len(reports) - len(accepted)
    if other:
        reporter.write_line(f"ci: {other} skipped test(s); CI fails on any other skip")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


@pytest.fixture
def pool() -> Pool:
    """A 4-model synthetic pool with a >10x price spread (mirrors design §2.3)."""
    candidates = (
        Candidate("cheap", "vendor.cheap-v1", "famA", Price(0.00006, 0.00024), True),
        Candidate("mid", "vendor.mid-v1", "famB", Price(0.0008, 0.0032), True),
        Candidate("strong", "vendor.strong-v1", "famC", Price(0.003, 0.015), True),
        Candidate("alt", "vendor.alt-v1", "famD", Price(0.00058, 0.00168), True),
    )
    return Pool(candidates, judge_id="ext.judge", judge_alias="judge", snapshot_date="2026-07-07")


@pytest.fixture
def complementary_matrix():
    """Response matrix where each model wins on a distinct query subset -> large oracle
    gap. Should PASS the T-STAT-1 complementarity gate."""
    from delphi.eval.matrix import ResponseMatrix

    aliases = ("cheap", "mid", "strong", "alt")
    n = 400
    rng = np.random.default_rng(42)
    scores = rng.uniform(60, 70, size=(n, 4))
    # give each model a block of queries where it is clearly best (+25 pts)
    for j in range(4):
        block = slice(j * (n // 4), (j + 1) * (n // 4))
        scores[block, j] += 25
    queries = tuple(f"q{i}" for i in range(n))
    return ResponseMatrix(queries, aliases, scores)


@pytest.fixture
def nested_matrix():
    """Response matrix where 'strong' dominates everywhere -> tiny oracle gap.
    Should FAIL the T-STAT-1 gate (nested error sets, H2 unreachable)."""
    from delphi.eval.matrix import ResponseMatrix

    aliases = ("cheap", "mid", "strong", "alt")
    n = 400
    rng = np.random.default_rng(7)
    base = rng.uniform(50, 60, size=(n, 4))
    base[:, 2] = rng.uniform(85, 90, size=n)  # strong always best by a wide margin
    queries = tuple(f"q{i}" for i in range(n))
    return ResponseMatrix(queries, aliases, base)


# --- U3: small real artifacts ----------------------------------------------------
@pytest.fixture
def make_artifact(pool, tmp_path):
    """Factory: train one router family on a tiny matrix over `pool` and save its triple.

    Returns the artifact directory. Real `train_and_select` + `save_artifact`, so every
    artifact a U3 test inspects is produced by the production code path, not hand-built.
    """
    from delphi.eval.matrix import ResponseMatrix
    from delphi.train.artifact_store import save_artifact
    from delphi.train.harness import train_and_select

    def _make(family: str = "linear", *, name: str | None = None, encoder_dim: int = 32,
              weights_uri: str | None = None, over: Pool | None = None) -> pathlib.Path:
        aliases = (over or pool).aliases
        n = 24
        prompts = [f"query {i} about topic {i % len(aliases)}" for i in range(n)]
        rng = np.random.default_rng(3)
        scores = rng.uniform(60, 70, size=(n, len(aliases)))
        for i in range(n):
            scores[i, i % len(aliases)] += 20
        m = ResponseMatrix(tuple(f"q{i}" for i in range(n)), aliases, scores)
        result = train_and_select(m, prompts, m, prompts, encoder_dim=encoder_dim,
                                  families=[family])
        out = tmp_path / (name or f"art_{family}")
        save_artifact(result, str(out), weights_uri=weights_uri)
        return out

    return _make


# --- U3: hand-written S3 / STS fakes (never a mocking library) -------------------
class FakeClientError(Exception):
    """Shaped like botocore's ClientError: callers read `.response["Error"]["Code"]`."""

    def __init__(self, code: str, op: str, key: str, status: int):
        super().__init__(f"{op} {key}: {code}")
        self.response = {"Error": {"Code": code, "Message": code},
                         "ResponseMetadata": {"HTTPStatusCode": status}}


class _Body:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3:
    """In-memory S3 for one bucket that records every call, in order.

    * `calls` is a list of (op, bucket, key, kwargs).
    * Conditional writes are honoured: IfNoneMatch="*" on an existing key, or IfMatch with a
      stale/absent ETag, raise a 412 PreconditionFailed.
    * `fail_write` = n makes the n-th put_object (1-based) raise without storing anything.
    * After EVERY recorded write, a snapshot of the whole store is appended to `snapshots`,
      so a test can check an invariant at every instant (T-PKG-U3-8(d)).
    * delete_object / delete_objects / copy_object RAISE: U3's publisher must never call them.
    """

    def __init__(self, objects: dict[str, bytes] | None = None, *, fail_write: int | None = None):
        import hashlib

        # An S3 ETag's shape, not a security use.
        self._md5 = lambda b: '"' + hashlib.md5(b, usedforsecurity=False).hexdigest() + '"'
        self.store: dict[str, bytes] = dict(objects or {})
        self.calls: list[tuple[str, str, str, dict]] = []
        self.snapshots: list[dict[str, bytes]] = []
        self.fail_write = fail_write
        self._writes = 0

    # --- reads -----------------------------------------------------------------
    def get_object(self, Bucket: str, Key: str, **kw):
        self.calls.append(("get_object", Bucket, Key, kw))
        if Key not in self.store:
            raise FakeClientError("NoSuchKey", "GetObject", Key, 404)
        data = self.store[Key]
        return {"Body": _Body(data), "ETag": self._md5(data), "ContentLength": len(data)}

    def head_object(self, Bucket: str, Key: str, **kw):
        self.calls.append(("head_object", Bucket, Key, kw))
        if Key not in self.store:
            raise FakeClientError("404", "HeadObject", Key, 404)
        return {"ETag": self._md5(self.store[Key]), "ContentLength": len(self.store[Key])}

    def download_file(self, Bucket: str, Key: str, Filename: str, **kw):
        self.calls.append(("download_file", Bucket, Key, kw))
        if Key not in self.store:
            raise FakeClientError("404", "HeadObject", Key, 404)
        pathlib.Path(Filename).write_bytes(self.store[Key])

    # --- writes ----------------------------------------------------------------
    def put_object(self, Bucket: str, Key: str, Body, IfNoneMatch: str | None = None,
                   IfMatch: str | None = None, **kw):
        kwargs = dict(kw, IfNoneMatch=IfNoneMatch, IfMatch=IfMatch)
        self.calls.append(("put_object", Bucket, Key, kwargs))
        self._writes += 1
        if self.fail_write is not None and self._writes == self.fail_write:
            raise FakeClientError("InternalError", "PutObject", Key, 500)
        data = Body if isinstance(Body, bytes) else Body.read()
        if IfNoneMatch == "*" and Key in self.store:
            raise FakeClientError("PreconditionFailed", "PutObject", Key, 412)
        if IfMatch is not None and (Key not in self.store
                                    or self._md5(self.store[Key]) != IfMatch):
            raise FakeClientError("PreconditionFailed", "PutObject", Key, 412)
        self.store[Key] = data
        self.snapshots.append(dict(self.store))
        return {"ETag": self._md5(data)}

    def delete_object(self, *a, **kw):
        raise AssertionError("FakeS3: delete_object must never be called")

    def delete_objects(self, *a, **kw):
        raise AssertionError("FakeS3: delete_objects must never be called")

    def copy_object(self, *a, **kw):
        raise AssertionError("FakeS3: copy_object must never be called")

    # --- helpers for assertions --------------------------------------------------
    @property
    def keys_requested(self) -> list[str]:
        return [key for _, _, key, _ in self.calls]

    @property
    def writes(self) -> list[tuple[str, dict]]:
        return [(key, kw) for op, _, key, kw in self.calls if op == "put_object"]


class FakeSTS:
    """`get_caller_identity` returns a fixed placeholder account, or raises."""

    def __init__(self, account: str = "111111111111", *, fail: bool = False):
        self.account = account
        self.fail = fail
        self.calls = 0

    def get_caller_identity(self):
        self.calls += 1
        if self.fail:
            raise FakeClientError("ExpiredToken", "GetCallerIdentity", "-", 403)
        return {"Account": self.account, "Arn": "arn:aws:iam::111111111111:user/test"}


@pytest.fixture
def fake_s3_cls():
    return FakeS3


@pytest.fixture
def fake_sts_cls():
    return FakeSTS


# --- U3: in-process CDK synthesis (plan D-CG-3) --------------------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class SynthResult:
    def __init__(self, outdir: pathlib.Path, template: dict, stack_name: str):
        import json

        self.outdir = outdir
        self.template = template
        assets = json.loads((outdir / f"{stack_name}.assets.json").read_text())
        images = assets.get("dockerImages", {})
        if len(images) != 1:
            raise AssertionError(f"expected one docker image asset, got {sorted(images)}")
        (self.asset_hash, entry), = images.items()
        self.asset_dir = outdir / entry["source"]["directory"]

    def runtime_properties(self) -> dict:
        rs = [r for r in self.template["Resources"].values()
              if r["Type"] == "AWS::BedrockAgentCore::Runtime"]
        assert len(rs) == 1, rs
        return rs[0]["Properties"]


@pytest.fixture(scope="session")
def cdk_synth(tmp_path_factory):
    """Lazy, session-scoped factory that synthesizes the REAL DelphiRuntimeStack.

    Inserts infra/ on sys.path only when first called and imports
    `delphi_infra.runtime_stack` — never the module `app` (infra/app.py runs app.synth() at
    import and writes infra/cdk.out). Every synth goes to an outdir under tmp_path, so no
    test writes infra/cdk.out. Fails — never skips — without aws_cdk or Node.js, because a
    skip is a criterion that cannot fail. Identical requests (no build_root) are cached.
    """
    import shutil
    import sys

    cache: dict[tuple, SynthResult] = {}

    def _synth(*, build_root: pathlib.Path | None = None, account: str | None = None,
               region: str | None = None, context: dict | None = None,
               arm: str = "router") -> SynthResult:
        # U6 BRK-U6-1 (P-U6-7): the stack requires the artifactBucket mode (NFR8.1). These
        # tests are about the author's deployment, so `existing` is merged UNDER the caller's
        # context; a caller that names a mode keeps it, and the cache key includes it.
        context = {"artifactBucket": "existing", **(context or {})}
        key = (account, region, tuple(sorted(context.items())), arm)
        if build_root is None and key in cache:
            return cache[key]
        try:
            import aws_cdk as cdk
        except ImportError as exc:  # pragma: no cover - environment guard
            pytest.fail(f"aws-cdk-lib is not installed in this venv ({exc}); "
                        f"run `.venv/bin/python -m pip install -e '.[dev]'`")
        if shutil.which("node") is None:  # pragma: no cover - environment guard
            pytest.fail("Node.js is not on PATH; the aws-cdk-lib jsii bridge needs it")
        infra = str(REPO_ROOT / "infra")
        if infra not in sys.path:
            sys.path.insert(0, infra)
        from delphi_infra.runtime_stack import DelphiRuntimeStack

        outdir = tmp_path_factory.mktemp("cdk-out")
        app = cdk.App(outdir=str(outdir), context=dict(context or {}))
        env = cdk.Environment(account=account, region=region) if account else None
        safe = arm.replace(":", "-").replace("_", "-")
        stack_name = f"Delphi-{safe}"
        kwargs = {"build_root": build_root} if build_root is not None else {}
        DelphiRuntimeStack(app, stack_name, arm=arm, env=env, **kwargs)
        assembly = app.synth()
        result = SynthResult(outdir, assembly.get_stack_by_name(stack_name).template,
                             stack_name)
        if build_root is None:
            cache[key] = result
        return result

    return _synth
