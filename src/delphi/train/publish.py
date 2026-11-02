"""`delphi-artifact` — build, check and publish the router artifact (SD-5, SD-6, SD-10;
NFR8.1-NFR8.5, NFR8.8; contracts C3 and C6 as amended).

Subcommands:
  build --pool <file> [--out <dir>]                 train an artifact locally (no AWS)
  check <dir> --pool <file>                         every C3 invariant, locally (no AWS)
  publish <dir> --pool <file> [--execute] [--region R]
                                                    plan the publication; write only with
                                                    --execute
  verify-remote --pool <file> [--region R]          read the live pointer form and check it

Exit codes mirror C5 (delphi-verify), so a reader learns one convention: 0 success,
1 a check, conflict or service failure, 2 a usage error (no --pool, no region, no account).

Safety properties, each pinned by a test:
  * `--pool` has no default — publishing against an unspecified pool is a usage error.
  * The C3 checks run before ANY client is built (T-PKG-U3-10's publish case).
  * Without `--execute` nothing is written (T-PKG-U3-7).
  * The region comes from --region or the standard AWS chain, never a literal; the account
    comes from sts:GetCallerIdentity; no profile is named, so the standard credential chain
    applies (T-PKG-U3-9). boto3 is imported only inside the real client factory.
  * Publication is content-addressed and conditional (SD-6): the triple goes to the immutable
    `router/<artifact_hash>/` with IfNoneMatch="*"; every remote object is read and compared
    BEFORE the first write, so a conflict writes nothing; present-and-equal objects are
    skipped, so re-running the same command resumes an interrupted publish; the pointer is
    written LAST, with IfMatch on the previous pointer's ETag (or IfNoneMatch="*" when none),
    and the previous pointer is printed first so it can be restored.
  * Nothing is ever written, copied or deleted under the legacy `router/current/` prefix the
    live router reads today (T-PKG-U3-8(a)); U6 owns the flip to the pointer form, gated on
    `verify-remote` exiting 0.

Offline/publication-side module: never imported by the request path (T-PKG-U3-3).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import pathlib
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from delphi.config import Pool, load_pool
from delphi.train.artifact_contract import ARTIFACT_FILES, check_artifact
from delphi.train.artifact_location import ArtifactLocation, load_location

#: manifest fields that identify the router; `selection` (wall-clock) and `weights_uri` are
#: deliberately NOT compared (SD-6 conflict rule).
MANIFEST_IDENTITY = ("family", "trained_aliases", "encoder_name", "encoder_dim",
                     "artifact_hash")
_NOT_FOUND = {"NoSuchKey", "404", "NotFound"}


class UsageError(Exception):
    """Exit 2: the command cannot run as invoked."""


class PublishError(Exception):
    """Exit 1: a check, a conflict or a service refusal, with the key named."""


# --- AWS clients ---------------------------------------------------------------------
class Boto3Clients:  # pragma: no cover - needs live AWS
    """The single real-client factory. Standard credential chain; no profile is named."""

    def default_region(self) -> str | None:
        import boto3

        return boto3.session.Session().region_name

    def s3(self, region: str):
        import boto3

        return boto3.client("s3", region_name=region)

    def sts(self, region: str):
        import boto3

        return boto3.client("sts", region_name=region)


def _error_code(exc: BaseException) -> str | None:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code"))
    return None


def _get_or_none(s3, bucket: str, key: str) -> tuple[bytes, str] | None:
    """(body, etag) of an object, or None when it does not exist."""
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 - not-found is an answer; others become PublishError
        code = _error_code(exc)
        if code in _NOT_FOUND:
            return None
        raise PublishError(f"reading s3://{bucket}/{key} failed ({code or exc!r}); check "
                           f"the credentials' access to the bucket") from exc
    return obj["Body"].read(), obj.get("ETag", "")


def _put(s3, bucket: str, key: str, body: bytes, *, if_none_match: bool = False,
         if_match: str | None = None) -> None:
    kwargs: dict[str, str] = {}
    if if_none_match:
        kwargs["IfNoneMatch"] = "*"
    if if_match is not None:
        kwargs["IfMatch"] = if_match
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body, **kwargs)
    except Exception as exc:  # noqa: BLE001 - every refusal is surfaced with the key named
        code = _error_code(exc)
        if code == "PreconditionFailed":
            raise PublishError(f"conflict writing s3://{bucket}/{key}: it changed since it was "
                               f"read (412 PreconditionFailed); re-run to re-plan") from exc
        raise PublishError(f"writing s3://{bucket}/{key} failed ({code or exc!r}); nothing "
                           f"after it was written — re-run the same command to resume") from exc


# --- content comparison (SD-6 conflict rule) -----------------------------------------
def _npz_arrays(data: bytes) -> dict[str, np.ndarray]:
    with np.load(io.BytesIO(data), allow_pickle=False) as npz:
        return {k: npz[k] for k in npz.files}


def _differences(name: str, remote: bytes, local: bytes) -> list[str]:
    """What differs between a remote and a local artifact file, by identifying content."""
    try:
        if name == "weights.npz":
            r, loc = _npz_arrays(remote), _npz_arrays(local)
            if set(r) != set(loc):
                return [f"array keys {sorted(r)} != {sorted(loc)}"]
            return [f"array {k}" for k in sorted(r)
                    if r[k].dtype != loc[k].dtype or r[k].shape != loc[k].shape
                    or r[k].tobytes() != loc[k].tobytes()]
        rj, lj = json.loads(remote), json.loads(local)
    except (ValueError, OSError, EOFError) as exc:
        return [f"remote object is unreadable ({exc!r})"]
    if name == "scalars.json":
        return [] if rj == lj else ["scalars"]
    return [f for f in MANIFEST_IDENTITY if rj.get(f) != lj.get(f)]


# --- resolution ----------------------------------------------------------------------
def _load_pool(path: str) -> Pool:
    p = pathlib.Path(path)
    if not p.is_file():
        raise UsageError(f"pool file {path} does not exist; pass --pool <models file>")
    load_pool.cache_clear()
    try:
        return load_pool(p)
    finally:
        load_pool.cache_clear()


@dataclass
class _Target:
    s3: object
    bucket: str
    location: ArtifactLocation


def _resolve_target(args, env: Mapping[str, str], clients) -> _Target:
    region = (args.region or env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
              or clients.default_region())
    if not region:
        raise UsageError("no AWS region resolved; pass --region or set AWS_REGION")
    try:
        account = clients.sts(region).get_caller_identity()["Account"]
    except Exception as exc:  # noqa: BLE001 - any failure here means no usable account
        raise UsageError(f"no AWS account resolved from the credential chain ({exc}); "
                         f"configure credentials, e.g. AWS_PROFILE") from exc
    location = load_location()
    return _Target(s3=clients.s3(region), bucket=location.bucket(account, region),
                   location=location)


def _check_or_raise(art_dir: pathlib.Path, pool: Pool) -> None:
    violations = check_artifact(art_dir, pool)
    if violations:
        lines = "\n".join(f"  {v}" for v in violations)
        raise PublishError(f"artifact {art_dir} fails the C3 contract against the named "
                           f"pool:\n{lines}\nfix or rebuild it with `delphi-artifact build`")


# --- subcommands ---------------------------------------------------------------------
def _cmd_build(args, env, clients) -> int:
    from delphi.train.generate import build

    pool = _load_pool(args.pool)
    out, art = build(args.pool, args.out)
    _check_or_raise(out, pool)
    print(f"artifact: {out}")
    print(f"artifact_hash: {art.artifact_hash}")
    print(f"family: {art.family}")
    print(f"trained_aliases: {', '.join(art.trained_aliases)}")
    print("note: labels are synthetic; this proves the routing mechanism, not routing quality")
    return 0


def _cmd_check(args, env, clients) -> int:
    pool = _load_pool(args.pool)
    _check_or_raise(pathlib.Path(args.dir), pool)
    print(f"ok: {args.dir} satisfies the C3 contract against {args.pool}")
    return 0


@dataclass
class _Plan:
    prefix: str
    to_write: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    previous: tuple[bytes, str] | None = None


def _plan(target: _Target, art_dir: pathlib.Path, ahash: str) -> _Plan:
    """Read every remote object first; any conflict raises before anything is written."""
    plan = _Plan(prefix=target.location.triple_prefix(ahash))
    for name in ARTIFACT_FILES:
        key = plan.prefix + name
        remote = _get_or_none(target.s3, target.bucket, key)
        if remote is None:
            plan.to_write.append(name)
            continue
        diff = _differences(name, remote[0], (art_dir / name).read_bytes())
        if diff:
            raise PublishError(f"conflict at s3://{target.bucket}/{key}: the remote object "
                               f"differs in {', '.join(diff)}; an immutable prefix is never "
                               f"overwritten — investigate before publishing")
        plan.present.append(name)
    plan.previous = _get_or_none(target.s3, target.bucket, target.location.pointer_key)
    return plan


def _pointer_outcome(plan: _Plan, ahash: str, already: bool) -> str:
    """What the pointer write does, in the words the S9 stop rule reads (U6 D-CG-U6-6).

    A replacement says so in capitals and states that the bucket's versioning is unknown, so
    the old pointer is recoverable only from the body printed above it, not from the bucket.
    """
    if already:
        return f"unchanged (already names {ahash})"
    if plan.previous is None:
        return "creates (no previous pointer)"
    return ("REPLACES the previous pointer above; bucket versioning unknown, the old body is "
            "printed above")


def _print_statement(target: _Target, plan: _Plan, outcome: str, previous: str | None) -> None:
    """The plan as a statement the BUCKET-WRITE-CONFIRM question can show verbatim (U6 S9).

    The bucket is the location file's TEMPLATE, never the resolved name, which carries the
    account id; everything else is a key, an object name or a hash, so the block is
    scrub-safe and can be committed as the statement file as printed.
    """
    print("statement (scrub-safe):")
    print(f"  bucket: {target.location.bucket_template}")
    print(f"  prefix: {plan.prefix}")
    print(f"  objects to write: {', '.join(plan.to_write) or 'none'}")
    print(f"  objects present, identical: {', '.join(plan.present) or 'none'}")
    print(f"  pointer {target.location.pointer_key}: {outcome}")
    if previous is not None:
        print(f"  previous pointer body: {previous}")
    print("  bucket versioning: unknown")


def _cmd_publish(args, env, clients) -> int:
    pool = _load_pool(args.pool)
    art_dir = pathlib.Path(args.dir)
    _check_or_raise(art_dir, pool)                       # before ANY client is built
    ahash = json.loads((art_dir / "manifest.json").read_text())["artifact_hash"]
    target = _resolve_target(args, env, clients)
    plan = _plan(target, art_dir, ahash)
    pointer_key = target.location.pointer_key

    print(f"bucket: {target.bucket}")
    print(f"artifact_hash: {ahash}")
    for name in plan.present:
        print(f"  present, identical: {plan.prefix}{name}")
    for name in plan.to_write:
        print(f"  {'write' if args.execute else 'would write'}: {plan.prefix}{name}")
    previous = None
    if plan.previous is not None:
        previous = plan.previous[0].decode("utf-8", "replace")
        print(f"previous pointer {pointer_key} (ETag {plan.previous[1]}): {previous}")
    else:
        print(f"previous pointer {pointer_key}: none")

    already = False
    if plan.previous is not None:
        try:
            prev = json.loads(plan.previous[0])
            already = prev.get("artifact_hash") == ahash and prev.get("prefix") == plan.prefix
        except ValueError:
            already = False

    outcome = _pointer_outcome(plan, ahash, already)
    if already:
        print(f"pointer {pointer_key}: {outcome}")
    else:
        verb = "write pointer" if args.execute else "would write pointer"
        print(f"{verb} {pointer_key}: {outcome}")
    _print_statement(target, plan, outcome, previous)

    if not args.execute:
        print("dry run: nothing written; pass --execute to publish")
        return 0

    for name in plan.to_write:
        _put(target.s3, target.bucket, plan.prefix + name, (art_dir / name).read_bytes(),
             if_none_match=True)
    if already:
        print(f"pointer {pointer_key} already names {ahash}; unchanged")
        return 0
    body = json.dumps({
        "artifact_hash": ahash,
        "prefix": plan.prefix,
        "published_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }).encode()
    if plan.previous is not None:
        _put(target.s3, target.bucket, pointer_key, body, if_match=plan.previous[1])
    else:
        _put(target.s3, target.bucket, pointer_key, body, if_none_match=True)
    print(f"published: pointer {pointer_key} -> {plan.prefix} ({ahash})")
    return 0


def _cmd_verify_remote(args, env, clients) -> int:
    from delphi.train.artifact_store import ArtifactLocationError, resolve_artifact_dir

    pool = _load_pool(args.pool)
    target = _resolve_target(args, env, clients)
    uri = f"s3://{target.bucket}/{target.location.pointer_key}"
    with tempfile.TemporaryDirectory(prefix="delphi-verify-remote-") as tmp:
        try:
            # The exact loader the router arm will use after U6's flip to the pointer form.
            local = resolve_artifact_dir(uri, tmp, s3_client=target.s3)
        except ArtifactLocationError as exc:
            raise PublishError(f"{exc}; do not flip serving_form to 'pointer'") from exc
        except Exception as exc:  # noqa: BLE001 - surfaced with the location named
            raise PublishError(f"reading the triple behind {uri} failed "
                               f"({_error_code(exc) or exc!r})") from exc
        _check_or_raise(pathlib.Path(local), pool)
        ahash = json.loads((pathlib.Path(local) / "manifest.json").read_text())["artifact_hash"]
    print(f"ok: {uri} -> {target.location.triple_prefix(ahash)} is consistent and satisfies "
          f"the C3 contract against {args.pool}")
    return 0


# --- CLI ------------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="delphi-artifact", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="train an artifact over a named pool (no AWS)")
    b.add_argument("--pool", required=True, help="models file, e.g. config/models_realpool.yaml")
    b.add_argument("--out", help="output directory (default artifacts/router_<pool>_<hash8>)")
    b.set_defaults(func=_cmd_build)

    c = sub.add_parser("check", help="check an artifact against a named pool (no AWS)")
    c.add_argument("dir")
    c.add_argument("--pool", required=True)
    c.set_defaults(func=_cmd_check)

    u = sub.add_parser("publish", help="publish an artifact (dry run unless --execute)")
    u.add_argument("dir")
    u.add_argument("--pool", required=True)
    u.add_argument("--execute", action="store_true", help="actually write to S3")
    u.add_argument("--region", help="default: the standard AWS region chain")
    u.set_defaults(func=_cmd_publish)

    v = sub.add_parser("verify-remote", help="check the published pointer form (read only)")
    v.add_argument("--pool", required=True)
    v.add_argument("--region", help="default: the standard AWS region chain")
    v.set_defaults(func=_cmd_verify_remote)
    return p


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None,
         clients=None) -> int:
    """Run one subcommand and return its exit code (testable without subprocesses)."""
    import os

    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:  # argparse: --help is 0, a usage error is 2
        return int(exc.code or 0)
    try:
        return args.func(args, env, clients or Boto3Clients())
    except UsageError as exc:
        print(f"delphi-artifact: {exc}", file=sys.stderr)
        return 2
    except PublishError as exc:
        print(f"delphi-artifact: {exc}", file=sys.stderr)
        return 1


def _entry() -> None:  # pragma: no cover - console-script shim
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover - module execution shim
    _entry()
