"""The artifact generator — U3 Layer 4 (SD-15, SD-16; FR1.1, FR1.5, NFR10.5, NFR10.6;
AC1.3.1, AC1.3.2).

`delphi-artifact build --pool <file> [--out <dir>]` replaces scripts/build_realpool_artifact.py
(plan D-CG-2): same training recipe, the pool as an argument, a content-derived default name.

  T-PKG-U3-18  `python -m delphi.train.publish build --pool config/models_realpool.yaml --out
               <tmp>` run TWICE as subprocesses, with PYTHONHASHSEED=0 and PYTHONHASHSEED=1, into
               two tmp_path directories. Both exit 0. They give equal artifact_hash,
               byte-identical weights.npz and equal parsed scalars.json. harness.artifact_hash
               recomputed from the loaded triple equals manifest.artifact_hash. trained_aliases
               equals the pool's aliases (AC1.3.2). check_artifact passes against that pool, and
               load_learned_router constructs a router. NO literal hash is asserted and
               manifest.json is NOT byte-compared (SD-15: its `selection` block is wall-clock).
               pyproject.toml declares `delphi-artifact = "delphi.train.publish:_entry"`.
  T-PKG-U3-19  The generator called IN-PROCESS (so generate.py is inside --cov=src) with no
               output directory produces a default name matching router_<pool>_<hash8>
               (router_realpool_<8 hex> for the realpool file, the hash being the artifact's).
               No .py under src/ or scripts/ contains the literal `router_current`.

Provenance only, NOT an assertion: on Darwin 25.6.0 arm64, Python 3.12.10, numpy 2.5.1 with
Apple Accelerate BLAS/LAPACK, encoder_dim 512, seed 20260714, the realpool artifact_hash was
55e220c46421c320bb2dd30eead9f9e4 (NFR design M1). A different hash on another environment is
an environment note, not a defect and not a pass: the claim is "deterministic within one
environment; identified by its hash", never cross-machine reproducibility.

Partition A: numpy + stdlib, no SDK, no account.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib

import numpy as np
import pytest

from delphi.config import load_pool

ROOT = pathlib.Path(__file__).resolve().parents[1]
REALPOOL = ROOT / "config" / "models_realpool.yaml"


@pytest.fixture
def realpool():
    load_pool.cache_clear()
    p = load_pool(REALPOOL)
    load_pool.cache_clear()
    return p


def _build_subprocess(out: pathlib.Path, seed: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DELPHI_")}
    env["PYTHONHASHSEED"] = seed
    return subprocess.run(  # noqa: S603 - fixed argv: this interpreter + delphi.train.publish  # nosemgrep
        [sys.executable, "-m", "delphi.train.publish", "build",
         "--pool", "config/models_realpool.yaml", "--out", str(out)],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )


def _arrays(path: pathlib.Path) -> dict[str, bytes]:
    with np.load(path, allow_pickle=False) as npz:
        return {k: npz[k].tobytes() for k in npz.files}


# --- T-PKG-U3-18 ---------------------------------------------------------------------
def test_t_pkg_u3_18_generator_is_stable_within_one_environment(tmp_path, realpool):
    from delphi.train.artifact_contract import check_artifact
    from delphi.train.artifact_store import _load_state, load_learned_router
    from delphi.train.harness import artifact_hash

    a, b = tmp_path / "a", tmp_path / "b"
    ra, rb = _build_subprocess(a, "0"), _build_subprocess(b, "1")
    assert ra.returncode == 0, ra.stderr
    assert rb.returncode == 0, rb.stderr

    ma = json.loads((a / "manifest.json").read_text())
    mb = json.loads((b / "manifest.json").read_text())
    assert ma["artifact_hash"] == mb["artifact_hash"]
    assert (a / "weights.npz").read_bytes() == (b / "weights.npz").read_bytes()
    assert _arrays(a / "weights.npz") == _arrays(b / "weights.npz")
    assert json.loads((a / "scalars.json").read_text()) == json.loads(
        (b / "scalars.json").read_text())

    assert artifact_hash(_load_state(a), ma["encoder_dim"]) == ma["artifact_hash"]
    assert tuple(ma["trained_aliases"]) == realpool.aliases
    assert ma["weights_uri"] == "weights.npz"
    assert check_artifact(a, realpool) == []
    router = load_learned_router(str(a), realpool)
    assert router.artifact.artifact_hash == ma["artifact_hash"]
    assert ma["artifact_hash"] in ra.stdout


def test_t_pkg_u3_18_console_script_is_declared():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["delphi-artifact"] == "delphi.train.publish:_entry"


def test_t_pkg_u3_18_build_requires_a_pool(tmp_path):
    from delphi.train.publish import main

    assert main(["build", "--out", str(tmp_path / "x")], env={}) == 2


# --- T-PKG-U3-19 ---------------------------------------------------------------------
def test_t_pkg_u3_19_default_name_is_content_derived(tmp_path, realpool):
    from delphi.train.generate import build

    out, art = build(REALPOOL, artifacts_root=tmp_path)
    assert out.parent == tmp_path
    assert re.fullmatch(r"router_realpool_[0-9a-f]{8}", out.name), out.name
    assert out.name == f"router_realpool_{art.artifact_hash[:8]}"
    assert json.loads((out / "manifest.json").read_text())["artifact_hash"] == art.artifact_hash
    assert art.trained_aliases == realpool.aliases


def test_t_pkg_u3_19_default_name_strips_only_the_models_prefix():
    from delphi.train.generate import default_dir_name

    assert default_dir_name("config/models_realpool.yaml", "0123456789abcdef" * 2) == (
        "router_realpool_01234567")
    assert default_dir_name("pools/custom.yaml", "f" * 32) == "router_custom_ffffffff"


def test_t_pkg_u3_19_no_source_names_router_current():
    offenders = [str(p.relative_to(ROOT))
                 for d in ("src", "scripts") for p in (ROOT / d).rglob("*.py")
                 if "router_current" in p.read_text()]
    assert offenders == []
