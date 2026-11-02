#!/usr/bin/env bash
# The one quality gate (LC-U5-1). CI definitions set the platform up and then run
# `bash scripts/ci.sh`; a developer runs the same command locally, in .venv.
#
# Steps run in order and the first failure stops the gate, naming its step:
#   preflight  no AWS credential may resolve. Runs only when DELPHI_REQUIRE_NO_AWS_CREDS=1
#              or CI=true, because the author's own machine holds a credential chain.
#              It detects a credential and fails; it never unsets one.
#   ruff       lint, configured in pyproject.toml.
#   pytest     the whole suite, with the coverage floor read from pyproject.toml. Any
#              skipped test or strict XPASS fails it (tests/conftest.py, xfail_strict).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# CI never opts in to executing generated code, whatever the caller's environment says.
unset DELPHI_ALLOW_UNSANDBOXED_EXEC
export DELPHI_FAIL_ON_SKIP=1

# step NAME COMMAND...: print a banner, run the command, and on failure name the step and
# exit with the command's own exit code.
step() {
  local name="$1"
  shift
  echo "==> ${name}"
  local rc=0
  "$@" || rc=$?
  if [[ "${rc}" -ne 0 ]]; then
    echo "ci.sh: step '${name}' failed (exit ${rc})" >&2
    exit "${rc}"
  fi
}

if [[ "${DELPHI_REQUIRE_NO_AWS_CREDS:-}" == 1 || "${CI:-}" == true ]]; then
  step preflight env PYTHONPATH="${ROOT}/src" python -m delphi.tools.ci_preflight
else
  echo "ci.sh: credential preflight not required (not CI)"
fi
step ruff python -m ruff check .
# -rfEs: the short summary names failures, errors and skips (-rs alone would drop FAILED lines).
step pytest python -m pytest --cov -rfEs
