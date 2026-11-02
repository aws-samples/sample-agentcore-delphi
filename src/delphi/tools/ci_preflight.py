"""CI credential preflight (LC-U5-2, SD-U5-2; NFR8.1). Step one of `scripts/ci.sh`.

CI must not be able to reach an AWS account: this repository has exactly one, and it is also
production. The CI definition declares no credential and disables IMDS. This module is the
check that nothing resolved anyway, from a project CI variable, a runner role, a container
credential endpoint or a web-identity token file.

    python -m delphi.tools.ci_preflight     exit 0: no credential resolves
                                            exit 3: one resolves (the provider is named)

It asks the standard SDK chain, `boto3.Session().get_credentials()`, and prints only the
provider's method name (`env`, `shared-credentials-file`, `iam-role`, ...). It never prints a
key, a token, a profile name or an ARN, and it never unsets a credential: a leaked credential
is a misconfiguration the human needs to see, so the build goes red. `ci.sh` runs it only
under `DELPHI_REQUIRE_NO_AWS_CREDS=1` or `CI=true`, because the author's machine holds a real
chain. Stated limit: this is a point-in-time check of the job's own environment.
"""
from __future__ import annotations

import sys

EXIT_CLEAN = 0
EXIT_CREDENTIALS_RESOLVE = 3


def main(argv: list[str] | None = None) -> int:
    """Return EXIT_CLEAN when no AWS credential resolves, else EXIT_CREDENTIALS_RESOLVE."""
    del argv  # no options; the signature matches the repository's other entry points
    import boto3  # lazily, as everywhere in delphi: importing this module needs no SDK

    credentials = boto3.Session().get_credentials()
    if credentials is None:
        print("ci_preflight: no AWS credentials resolve")
        return EXIT_CLEAN
    print(f"ci_preflight: AWS credentials resolved (provider: {credentials.method})",
          file=sys.stderr)
    return EXIT_CREDENTIALS_RESOLVE


if __name__ == "__main__":  # pragma: no cover - module execution shim
    sys.exit(main(sys.argv[1:]))
