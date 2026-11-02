"""The C3 artifact contract as one checker — U3 Layer 2 (SD-7; NFR8.5, NFR10.8, AC1.3.3).

`delphi.train.artifact_contract.check_artifact(art_dir, pool)` returns every violated C3
invariant as a named `Violation`, and raises nothing on a bad artifact. It is the check the
publisher runs before any S3 call, so a mis-paired artifact becomes a one-line local error
instead of a cold-start crash (T-RTR-6's guard, wired at the cheap end of the pipeline).

  T-PKG-U3-10  For each family (linear, prototype, knn) a well-formed artifact returns NO
               violation, and one deliberately broken artifact per invariant returns a violation
               with that invariant's code:
                 alias_subset     trained aliases not a subset of the EXPLICITLY named pool,
                                  with the offending aliases in the message
                 scalars_aliases  scalars.aliases != manifest.trained_aliases
                 scalars_family   scalars.family != manifest.family
                 family_arity     a per-family array missing or of the wrong rank/shape
                 encoder_dim      manifest.encoder_dim disagrees with the arrays
                 artifact_hash    recomputed hash != manifest hash, or not 32 hex characters
                 weights_uri      missing, or an absolute path (/Users/, /home/, /private/)
               `save_artifact` called WITHOUT weights_uri writes a non-absolute value, the
               manifest-relative "weights.npz" (plan D-CG-1). Identity: the alias rule is ONE
               function, `artifact_contract.unknown_aliases is learned.unknown_aliases`.
               (The publish case of T-PKG-U3-10 lives in test_artifact_publish.py.)

Partition A: numpy + stdlib, no SDK, no account.
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest

from delphi.config import Pool

FAMILIES = ("linear", "prototype", "knn")
# one array per family whose removal must break arity
_ARRAY_OF = {"linear": "b", "prototype": "scale", "knn": "Ytr"}


def _codes(violations) -> set[str]:
    return {v.code for v in violations}


def _edit_json(path: pathlib.Path, **changes) -> None:
    data = json.loads(path.read_text())
    for k, v in changes.items():
        if v is _DROP:
            data.pop(k, None)
        else:
            data[k] = v
    path.write_text(json.dumps(data))


_DROP = object()


def _rewrite_npz(path: pathlib.Path, *, drop: str | None = None, replace=None) -> None:
    npz = np.load(path, allow_pickle=False)
    arrays = {k: npz[k] for k in npz.files}
    npz.close()
    if drop:
        arrays.pop(drop)
    if replace:
        arrays.update(replace)
    np.savez(path, **arrays)


@pytest.mark.parametrize("family", FAMILIES)
def test_t_pkg_u3_10_well_formed_artifact_has_no_violation(family, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    assert check_artifact(make_artifact(family), pool) == []


@pytest.mark.parametrize("family", FAMILIES)
def test_t_pkg_u3_10_alias_not_in_named_pool_names_the_offenders(family, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    smaller = Pool(tuple(c for c in pool.candidates if c.alias != "alt"),
                   pool.judge_id, pool.judge_alias, pool.snapshot_date)
    vs = [v for v in check_artifact(make_artifact(family), smaller) if v.code == "alias_subset"]
    assert len(vs) == 1
    assert "alt" in vs[0].message


@pytest.mark.parametrize("family", FAMILIES)
def test_t_pkg_u3_10_scalars_aliases_must_equal_trained_aliases(family, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact(family)
    _edit_json(d / "scalars.json", aliases=list(reversed(pool.aliases)))
    assert "scalars_aliases" in _codes(check_artifact(d, pool))


@pytest.mark.parametrize("family", FAMILIES)
def test_t_pkg_u3_10_scalars_family_must_equal_manifest_family(family, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact(family)
    other = next(f for f in FAMILIES if f != family)
    _edit_json(d / "scalars.json", family=other)
    assert "scalars_family" in _codes(check_artifact(d, pool))


@pytest.mark.parametrize("family", FAMILIES)
def test_t_pkg_u3_10_family_arity(family, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact(family)
    _rewrite_npz(d / "weights.npz", drop=_ARRAY_OF[family])
    assert "family_arity" in _codes(check_artifact(d, pool))


@pytest.mark.parametrize("family", FAMILIES)
def test_t_pkg_u3_10_family_arity_wrong_alias_width(family, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact(family)
    name = _ARRAY_OF[family]
    npz = np.load(d / "weights.npz", allow_pickle=False)
    arr = npz[name]
    npz.close()
    # drop the last alias column: the array no longer has one entry per trained alias
    _rewrite_npz(d / "weights.npz", replace={name: arr[..., :-1]})
    assert "family_arity" in _codes(check_artifact(d, pool))


@pytest.mark.parametrize("family", FAMILIES)
def test_t_pkg_u3_10_encoder_dim_against_the_arrays(family, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact(family)
    _edit_json(d / "manifest.json", encoder_dim=33)
    assert "encoder_dim" in _codes(check_artifact(d, pool))


@pytest.mark.parametrize("family", FAMILIES)
def test_t_pkg_u3_10_tampered_artifact_hash(family, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact(family)
    h = json.loads((d / "manifest.json").read_text())["artifact_hash"]
    flipped = ("0" if h[0] != "0" else "1") + h[1:]
    _edit_json(d / "manifest.json", artifact_hash=flipped)
    vs = [v for v in check_artifact(d, pool) if v.code == "artifact_hash"]
    assert len(vs) == 1 and flipped in vs[0].message


def test_t_pkg_u3_10_artifact_hash_must_be_32_hex(make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact()
    _edit_json(d / "manifest.json", artifact_hash="not-a-hash")
    assert "artifact_hash" in _codes(check_artifact(d, pool))


def test_t_pkg_u3_10_weights_uri_missing(make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact()
    _edit_json(d / "manifest.json", weights_uri=_DROP)
    assert "weights_uri" in _codes(check_artifact(d, pool))


@pytest.mark.parametrize("uri", [
    "/Users/someone/artifacts/router_realpool/weights.npz",
    "/home/someone/artifacts/weights.npz",
    "/private/var/folders/x/weights.npz",
])
def test_t_pkg_u3_10_absolute_weights_uri_is_refused(uri, make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact()
    _edit_json(d / "manifest.json", weights_uri=uri)
    assert "weights_uri" in _codes(check_artifact(d, pool))


def test_t_pkg_u3_10_missing_file_is_a_violation_not_an_exception(make_artifact, pool):
    from delphi.train.artifact_contract import check_artifact

    d = make_artifact()
    (d / "scalars.json").unlink()
    assert "missing_file" in _codes(check_artifact(d, pool))


def test_t_pkg_u3_10_save_artifact_default_weights_uri_is_not_absolute(make_artifact):
    d = make_artifact()
    uri = json.loads((d / "manifest.json").read_text())["weights_uri"]
    assert not uri.startswith("/"), uri
    assert uri == "weights.npz"


def test_t_pkg_u3_10_explicit_weights_uri_still_wins(make_artifact):
    d = make_artifact(weights_uri="weights-explicit.npz")
    assert json.loads((d / "manifest.json").read_text())["weights_uri"] == "weights-explicit.npz"


def test_t_pkg_u3_10_alias_rule_is_one_function():
    from delphi.router import learned
    from delphi.train import artifact_contract

    assert artifact_contract.unknown_aliases is learned.unknown_aliases
    assert learned.unknown_aliases(("a", "b", "c"), ("a", "b")) == {"c"}
    assert learned.unknown_aliases(("a",), ("a", "b")) == set()
