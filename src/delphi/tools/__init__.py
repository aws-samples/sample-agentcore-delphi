"""Offline repository tools (unit u5-evidence-and-ci). Not on the request path.

Two standard-library modules the author and the CI gate run against the repository itself,
never against a deployed runtime:

    ci_preflight  LC-U5-2  `scripts/ci.sh` step one: fail when any AWS credential resolves
    scrub_check   LC-U5-9  `delphi-scrub-check`: report author identifiers in tracked files

Neither module is imported by `delphi.agent.entrypoint`, so neither enters the serving image's
import closure (T-PKG-U3-3c). `boto3` is imported lazily, inside `ci_preflight.main` only.
"""
