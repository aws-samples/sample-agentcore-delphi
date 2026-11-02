"""Artifact persistence + round-trip into the request path (contract C3; design D1).

Saves the selected router as a triple of three files:
  * <dir>/weights.npz    — the trained router's numpy arrays (loaded with allow_pickle=False)
  * <dir>/scalars.json   — its non-array state (family, aliases, and e.g. knn's k)
  * <dir>/manifest.json  — Artifact fields (family, trained_aliases, encoder, artifact_hash,
                           weights_uri relative to the manifest) plus the selection summary

load_learned_router() reconstructs a ready-to-serve LearnedRouter from a local directory or
from S3, which is what the router arm does at cold start (DELPHI_ARTIFACT_URI). The S3 URI's
form selects the layout, and the loader never probes or falls back between them (SD-8):
  * legacy flat prefix, e.g. s3://<bucket>/router/current — the triple read from that prefix
    exactly as before (what the live router arm is configured with today);
  * pointer object, e.g. s3://<bucket>/router/current.json — a small JSON naming an immutable
    router/<artifact_hash>/ prefix; the manifest found there must carry the same hash.

Request-path-safe: imported by the serving container (see delphi/train/__init__.py).
"""
from __future__ import annotations

import json
import pathlib
import posixpath
import tempfile

import numpy as np

from delphi.config import Pool
from delphi.router.features import HashingEncoder
from delphi.router.learned import Artifact, LearnedRouter
from delphi.train.harness import SelectionResult, artifact_hash
from delphi.train.routers import TrainedRouter, router_from_state


def save_artifact(result: SelectionResult, out_dir: str, *,
                  weights_uri: str | None = None) -> Artifact:
    d = pathlib.Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    router = result.winner.router
    state = router.to_state()
    ahash = artifact_hash(state, result.encoder_dim)

    # numpy arrays -> npz; non-array scalars -> a small json sidecar inside the npz dir
    arrays = {k: np.asarray(v) for k, v in state.items() if isinstance(v, np.ndarray)}
    scalars = {k: v for k, v in state.items() if not isinstance(v, np.ndarray)}
    np.savez(d / "weights.npz", **arrays)
    (d / "scalars.json").write_text(json.dumps(scalars))

    manifest = {
        "family": router.family,
        "trained_aliases": list(router.aliases),
        "encoder_name": f"hashing-{result.encoder_dim}",
        "encoder_dim": result.encoder_dim,
        # Relative to the manifest (plan D-CG-1): an absolute path would publish the
        # author's filesystem layout, and the publication URI would publish the account id.
        "weights_uri": weights_uri or "weights.npz",
        "artifact_hash": ahash,
        "selection": {
            "capture": result.winner.capture,
            "p90_latency_ms": result.winner.p90_latency_ms,
        },
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return Artifact(
        family=router.family,
        trained_aliases=tuple(router.aliases),
        encoder_name=manifest["encoder_name"],
        weights_uri=manifest["weights_uri"],
        artifact_hash=ahash,
    )


def _load_state(art_dir: pathlib.Path) -> dict:
    npz = np.load(art_dir / "weights.npz", allow_pickle=False)
    state = {k: npz[k] for k in npz.files}
    scalars = json.loads((art_dir / "scalars.json").read_text())
    state.update(scalars)
    return state


def load_trained_router(art_dir: str) -> tuple[TrainedRouter, HashingEncoder, Artifact]:
    d = pathlib.Path(art_dir)
    manifest = json.loads((d / "manifest.json").read_text())
    state = _load_state(d)
    state["family"] = manifest["family"]  # ensure family present for the factory
    router = router_from_state(state)
    encoder = HashingEncoder(dim=int(manifest["encoder_dim"]))
    art = Artifact(
        family=manifest["family"],
        trained_aliases=tuple(manifest["trained_aliases"]),
        encoder_name=manifest["encoder_name"],
        weights_uri=manifest["weights_uri"],
        artifact_hash=manifest["artifact_hash"],
    )
    return router, encoder, art


_TRIPLE = ("weights.npz", "scalars.json", "manifest.json")


class ArtifactLocationError(RuntimeError):
    """The configured artifact location cannot be served as configured.

    Raised for a pointer that is absent, malformed, names a non-hashed prefix, or disagrees
    with the manifest it points at. Never answered by reading the other layout: the loader
    fails fast at construction instead (C6: no silent fallback).
    """


def _download_legacy(s3, bucket: str, key: str, out: pathlib.Path) -> None:
    """LEGACY flat-prefix form — today's behaviour, key arithmetic unchanged (R-03)."""
    prefix = key.rstrip("/")
    for name in _TRIPLE:
        obj_key = f"{prefix}/{name}" if prefix else name
        s3.download_file(bucket, obj_key, str(out / name))


def _download_pointer(s3, bucket: str, key: str, out: pathlib.Path) -> None:
    """POINTER form (C6 target): `key` names the pointer object itself.

    The pointer must name the hashed prefix beside it (`<dir>/<artifact_hash>/`), and the
    manifest downloaded from there must carry the same hash. Any other outcome raises
    ArtifactLocationError; nothing here ever reads the legacy flat prefix.
    """
    where = f"s3://{bucket}/{key}"
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:  # noqa: BLE001 - only service errors are renamed; others re-raise
        if getattr(exc, "response", None) is None:
            raise
        raise ArtifactLocationError(
            f"artifact pointer {where} could not be read ({exc}); the pointer form does not "
            f"fall back to the legacy layout"
        ) from exc
    try:
        pointer = json.loads(body)
        ahash, prefix = pointer["artifact_hash"], pointer["prefix"]
    except (ValueError, TypeError, KeyError) as exc:
        raise ArtifactLocationError(f"artifact pointer {where} is malformed ({exc!r})") from exc

    parent = posixpath.dirname(key)
    expected = f"{parent}/{ahash}/" if parent else f"{ahash}/"
    if not isinstance(ahash, str) or prefix != expected:
        raise ArtifactLocationError(
            f"artifact pointer {where} names prefix {prefix!r}; it must name the hashed "
            f"prefix {expected!r}"
        )
    for name in _TRIPLE:
        s3.download_file(bucket, f"{prefix}{name}", str(out / name))
    got = json.loads((out / "manifest.json").read_text()).get("artifact_hash")
    if got != ahash:
        raise ArtifactLocationError(
            f"artifact pointer {where} names artifact_hash {ahash} but the manifest under "
            f"{prefix} carries {got}"
        )


def resolve_artifact_dir(uri: str, dest: str | None = None, *, s3_client=None) -> str:
    """Return a local directory containing the artifact files.

    A local path is returned unchanged. An s3:// URI is downloaded, and its FORM — never
    what S3 returns — selects the layout (SD-8):
      * a key ending `.json` names the pointer object (`router/current.json`);
      * anything else is the legacy flat prefix (`router/current`), read exactly as before.
    `dest` defaults to a fresh private directory (mkdtemp, mode 0700, SD-11). boto3 is
    imported lazily, and only when no `s3_client` is injected, so local use needs no SDK.
    """
    if not uri.startswith("s3://"):
        return uri
    bucket, _, key = uri[len("s3://"):].partition("/")
    if dest is None:
        out = pathlib.Path(tempfile.mkdtemp(prefix="delphi-artifact-"))
    else:
        out = pathlib.Path(dest)
        out.mkdir(parents=True, exist_ok=True)
    if s3_client is None:
        import boto3  # lazy: only needed on the S3 path

        s3_client = boto3.client("s3")
    if key.endswith(".json"):
        _download_pointer(s3_client, bucket, key, out)
    else:
        _download_legacy(s3_client, bucket, key, out)
    return str(out)


def load_learned_router(art_dir: str, pool: Pool, *, s3_client=None) -> LearnedRouter:
    """Reconstruct the deployable LearnedRouter (design D1 cold-start path).

    `art_dir` may be a local directory or an s3:// URI; S3 is downloaded first. No fallback
    alias is taken: the request-path fallback is RouterAgent's (U4 BR1.5).
    """
    art_dir = resolve_artifact_dir(art_dir, s3_client=s3_client)
    router, encoder, art = load_trained_router(art_dir)

    def scorer(embedding, trained_aliases):
        return router.score(embedding, trained_aliases)

    return LearnedRouter(
        pool=pool,
        artifact=art,
        embedder=encoder,
        scorer=scorer,
    )
