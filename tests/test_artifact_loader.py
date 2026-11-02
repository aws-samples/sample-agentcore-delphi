"""The dual-form cold-start loader — U3 Layer 3 (SD-8, SD-11; NFR8.6-NFR8.8; C6 ledger).

`resolve_artifact_dir(uri, dest=None, *, s3_client=None)` picks the layout from the FORM of
the configured URI and never from what S3 returns, so there is no silent fallback:
  * a key ending `.json` is the POINTER form (C6 target): read the pointer, download the triple
    from `router/<artifact_hash>/`, and require the manifest to agree with the pointer;
  * anything else is the LEGACY flat-prefix form, read with today's key arithmetic, byte for
    byte (`prefix.rstrip("/")`, then `f"{prefix}/{name}" if prefix else name`).

  T-PKG-U3-11(a)  STACK-COMPOSED URI — a hard pre-deploy gate. The URI is taken from the
                  router stack SYNTHESIZED IN-PROCESS (placeholder account 111111111111, region
                  us-west-2), not from a literal. Since U6c's flip (BRK-U6-8) it is the pointer
                  form: FakeS3 holds router/current.json and the router/<hash>/ triple, whose
                  manifest carries an ABSOLUTE /Users/... weights_uri. The requested keys are
                  EXACTLY, in order, router/current.json then the triple's weights.npz,
                  scalars.json, manifest.json; `load_learned_router` builds a router against
                  config/models_realpool.yaml's pool; the absolute weights_uri is NOT rejected at
                  load. LEGACY COMPATIBILITY stays pinned by an explicit router/current URI, which
                  reads EXACTLY router/current/weights.npz, router/current/scalars.json,
                  router/current/manifest.json. Second case: a bucket-root URI requests the bare
                  keys weights.npz, scalars.json, manifest.json (pins today's empty-prefix
                  arithmetic, R-03).
  T-PKG-U3-11(b)  Pointer happy path: a URI ending router/current.json reads the pointer,
                  downloads the triple from router/<artifact_hash>/, and returns a loadable dir.
  T-PKG-U3-11(c)  NO FALLBACK: pointer-form URI, pointer absent, flat triple present ->
                  ArtifactLocationError, and ZERO requested keys start with router/current/.
  T-PKG-U3-11(d)  pointer artifact_hash != downloaded manifest's -> ArtifactLocationError.
  T-PKG-U3-11(e)  pointer prefix is not router/<artifact_hash>/ (e.g. router/current/) ->
                  ArtifactLocationError, with nothing downloaded from that prefix.
  T-PKG-U3-12     two calls with no `dest` return DISTINCT directories, neither is
                  /tmp/delphi-artifact, and each has mode 0700 (SD-11).

Partition: (a) is B (needs aws-cdk-lib + Node.js for the in-process synth); the rest are A.
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
import tempfile

import pytest

from delphi.config import load_pool

ROOT = pathlib.Path(__file__).resolve().parents[1]
ACCOUNT, REGION = "111111111111", "us-west-2"
BUCKET = f"delphi-artifacts-{ACCOUNT}-{REGION}"
TRIPLE = ("weights.npz", "scalars.json", "manifest.json")


@pytest.fixture
def realpool():
    load_pool.cache_clear()
    p = load_pool(ROOT / "config" / "models_realpool.yaml")
    load_pool.cache_clear()
    return p


def _objects(art_dir: pathlib.Path, prefix: str) -> dict[str, bytes]:
    return {f"{prefix}{n}": (art_dir / n).read_bytes() for n in TRIPLE}


def _hash(art_dir: pathlib.Path) -> str:
    return json.loads((art_dir / "manifest.json").read_text())["artifact_hash"]


def _pointer(h: str, prefix: str | None = None) -> bytes:
    return json.dumps({"artifact_hash": h, "prefix": prefix or f"router/{h}/",
                       "published_at": "2026-10-18T00:00:00Z"}).encode()


def _stack_uri(cdk_synth) -> str:
    env = cdk_synth(account=ACCOUNT, region=REGION).runtime_properties()["EnvironmentVariables"]
    return env["DELPHI_ARTIFACT_URI"]


# --- T-PKG-U3-11(a) ----------------------------------------------------------------
def test_t_pkg_u3_11a_stack_composed_uri_reads_the_pointer_then_its_triple(
        cdk_synth, make_artifact, realpool, fake_s3_cls, tmp_path):
    from delphi.train.artifact_store import load_learned_router, resolve_artifact_dir

    uri = _stack_uri(cdk_synth)
    assert uri == f"s3://{BUCKET}/router/current.json", uri
    art = make_artifact("linear", over=realpool,
                        weights_uri="/Users/someone/agentcore-delphi/artifacts/router_realpool/"
                                    "weights.npz")
    h = _hash(art)
    s3 = fake_s3_cls({**_objects(art, f"router/{h}/"), "router/current.json": _pointer(h)})

    local = resolve_artifact_dir(uri, str(tmp_path / "dl"), s3_client=s3)

    assert s3.keys_requested == ["router/current.json"] + [f"router/{h}/{n}" for n in TRIPLE]
    assert {b for _, b, _, _ in s3.calls} == {BUCKET}
    router = load_learned_router(local, realpool)
    assert router.artifact.weights_uri.startswith("/Users/")  # accepted at load, by design
    assert router.predict("route me").chosen_alias in realpool.aliases


def test_t_pkg_u3_11a_load_learned_router_takes_the_same_seam(
        make_artifact, realpool, fake_s3_cls):
    from delphi.train.artifact_store import load_learned_router

    art = make_artifact("linear", over=realpool)
    s3 = fake_s3_cls(_objects(art, "router/current/"))
    router = load_learned_router(f"s3://{BUCKET}/router/current", realpool, s3_client=s3)
    assert router.artifact.artifact_hash == _hash(art)
    assert s3.keys_requested == [f"router/current/{n}" for n in TRIPLE]


@pytest.mark.parametrize("uri", [f"s3://{BUCKET}", f"s3://{BUCKET}/"])
def test_t_pkg_u3_11a_bucket_root_requests_bare_keys(uri, make_artifact, fake_s3_cls, tmp_path):
    from delphi.train.artifact_store import resolve_artifact_dir

    s3 = fake_s3_cls(_objects(make_artifact(), ""))
    resolve_artifact_dir(uri, str(tmp_path / "dl"), s3_client=s3)
    assert s3.keys_requested == list(TRIPLE)


def test_t_pkg_u3_11a_local_path_is_returned_unchanged(fake_s3_cls):
    from delphi.train.artifact_store import resolve_artifact_dir

    s3 = fake_s3_cls()
    assert resolve_artifact_dir("/some/local/dir", s3_client=s3) == "/some/local/dir"
    assert s3.calls == []


# --- T-PKG-U3-11(b) ----------------------------------------------------------------
def test_t_pkg_u3_11b_pointer_form_happy_path(make_artifact, realpool, fake_s3_cls, tmp_path):
    from delphi.train.artifact_store import load_learned_router, resolve_artifact_dir

    art = make_artifact("prototype", over=realpool)
    h = _hash(art)
    s3 = fake_s3_cls({**_objects(art, f"router/{h}/"), "router/current.json": _pointer(h)})

    local = resolve_artifact_dir(f"s3://{BUCKET}/router/current.json", str(tmp_path / "dl"),
                                 s3_client=s3)

    assert s3.keys_requested[0] == "router/current.json"
    assert s3.keys_requested[1:] == [f"router/{h}/{n}" for n in TRIPLE]
    router = load_learned_router(local, realpool)
    assert router.artifact.artifact_hash == h


# --- T-PKG-U3-11(c) ----------------------------------------------------------------
def test_t_pkg_u3_11c_absent_pointer_never_falls_back(make_artifact, fake_s3_cls, tmp_path):
    from delphi.train.artifact_store import ArtifactLocationError, resolve_artifact_dir

    s3 = fake_s3_cls(_objects(make_artifact(), "router/current/"))  # flat triple, no pointer
    with pytest.raises(ArtifactLocationError):
        resolve_artifact_dir(f"s3://{BUCKET}/router/current.json", str(tmp_path / "dl"),
                             s3_client=s3)
    assert [k for k in s3.keys_requested if k.startswith("router/current/")] == []


def test_t_pkg_u3_11c_malformed_pointer_is_a_named_error(fake_s3_cls, tmp_path):
    from delphi.train.artifact_store import ArtifactLocationError, resolve_artifact_dir

    s3 = fake_s3_cls({"router/current.json": b"{not json"})
    with pytest.raises(ArtifactLocationError):
        resolve_artifact_dir(f"s3://{BUCKET}/router/current.json", str(tmp_path / "dl"),
                             s3_client=s3)


# --- T-PKG-U3-11(d) ----------------------------------------------------------------
def test_t_pkg_u3_11d_pointer_and_manifest_must_agree(make_artifact, fake_s3_cls, tmp_path):
    from delphi.train.artifact_store import ArtifactLocationError, resolve_artifact_dir

    art = make_artifact()
    other = "f" * 32
    s3 = fake_s3_cls({**_objects(art, f"router/{other}/"),
                      "router/current.json": _pointer(other)})
    with pytest.raises(ArtifactLocationError, match=other):
        resolve_artifact_dir(f"s3://{BUCKET}/router/current.json", str(tmp_path / "dl"),
                             s3_client=s3)


# --- T-PKG-U3-11(e) ----------------------------------------------------------------
@pytest.mark.parametrize("bad_prefix", ["router/current/", "router/", "elsewhere/abc/"])
def test_t_pkg_u3_11e_pointer_must_name_its_hashed_prefix(bad_prefix, make_artifact,
                                                         fake_s3_cls, tmp_path):
    from delphi.train.artifact_store import ArtifactLocationError, resolve_artifact_dir

    art = make_artifact()
    h = _hash(art)
    s3 = fake_s3_cls({**_objects(art, bad_prefix), "router/current.json": _pointer(h, bad_prefix)})
    with pytest.raises(ArtifactLocationError):
        resolve_artifact_dir(f"s3://{BUCKET}/router/current.json", str(tmp_path / "dl"),
                             s3_client=s3)
    assert s3.keys_requested == ["router/current.json"]


# --- T-PKG-U3-12 ---------------------------------------------------------------------
def test_t_pkg_u3_12_default_dest_is_private_and_distinct(make_artifact, fake_s3_cls,
                                                          tmp_path, monkeypatch):
    from delphi.train.artifact_store import resolve_artifact_dir

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    objects = _objects(make_artifact(), "router/current/")
    a = resolve_artifact_dir(f"s3://{BUCKET}/router/current", s3_client=fake_s3_cls(objects))
    b = resolve_artifact_dir(f"s3://{BUCKET}/router/current", s3_client=fake_s3_cls(objects))
    assert a != b
    for d in (a, b):
        assert pathlib.Path(d) != pathlib.Path("/tmp/delphi-artifact")  # noqa: S108 - asserts it is NOT used  # nosec B108
        assert pathlib.Path(d).name.startswith("delphi-artifact-")
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
        assert sorted(p.name for p in pathlib.Path(d).iterdir()) == sorted(TRIPLE)
