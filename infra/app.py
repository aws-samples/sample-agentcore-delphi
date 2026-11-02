#!/usr/bin/env python3
"""CDK app for Delphi AgentCore Runtime deployment.

Uses the current L1 constructs `aws_cdk.aws_bedrockagentcore.CfnRuntime` /
`CfnRuntimeEndpoint` (CloudFormation AWS::BedrockAgentCore::Runtime). This is the
supported IaC path — NOT the deprecated bedrock-agentcore-starter-toolkit and NOT
classic Bedrock Agents (bedrock-agent / AWS::Bedrock::Agent).

The AgentCore CLI (`@aws/agentcore deploy`) also drives CDK under the hood; this stack
is the explicit, reviewable equivalent for teams that want the infra in-repo.

One stack per arm in `delphi_infra.arms.ARMS`, deployed by NAME, one stack per deploy:

    cdk synth  -c artifactBucket=existing
    cdk deploy Delphi-pinned-nova-lite -c artifactBucket=existing

`artifactBucket` is required (`existing` in an account that already holds the bucket,
`create` in a clean account) and has no default. docs/deployment.md is the full sequence.

`build_app` is the whole app, so the suite builds exactly what a deploy synthesizes
(tests/u6_support.py) without writing infra/cdk.out; `synth()` runs only under `__main__`.

From U6b the cdk-nag AwsSolutions pack runs as an Aspect on every stack (delphi_infra.nag), and
`main()` exits non-zero when it reports an error no suppression covers (BR4.1).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import aws_cdk as cdk

from delphi_infra import arms, image, nag
from delphi_infra.runtime_stack import (
    BucketModeError,
    DelphiArtifactsStack,
    DelphiRuntimeStack,
    bucket_mode,
    bucket_mode_message,
)

CDK_JSON = Path(__file__).resolve().parent / "cdk.json"
#: What the CDK CLI adds to every synth's context by default (it is not in cdk.json). Applied
#: under everything else, so an offline synth renders the same CDKMetadata, path and asset
#: metadata a `cdk diff` reads; under the CLI the CLI's own values arrive first and win.
CLI_DEFAULT_CONTEXT = {
    "aws:cdk:version-reporting": True,
    "aws:cdk:enable-path-metadata": True,
    "aws:cdk:enable-asset-metadata": True,
}


def committed_context(cdk_json: Path = CDK_JSON) -> dict:
    """infra/cdk.json's `context`, refusing the two keys that must never be committed."""
    context = json.loads(Path(cdk_json).read_text()).get("context", {})
    if "artifactBucket" in context:
        raise BucketModeError(bucket_mode_message(
            "infra/cdk.json carries artifactBucket; it must be passed per synth with -c"))
    image.reject_committed(context)
    return context


def build_app(app: cdk.App, *, build_root: Path | None = None,
              cdk_json: Path = CDK_JSON) -> cdk.App:
    """Add every Delphi stack to `app` and return it.

    cdk.json's context, then the CLI's defaults, are merged UNDER the app's own context (a key
    the caller set wins, as with `cdk -c`), so a synth that does not go through the CDK CLI
    renders the template the CLI would (U6 deviation D-1).
    """
    for key, value in {**CLI_DEFAULT_CONTEXT, **committed_context(cdk_json)}.items():
        if app.node.try_get_context(key) is None:
            app.node.set_context(key, value)
    mode = bucket_mode(app)
    specs = arms.arm_specs(root=build_root)
    overrides = image.parse_overrides(app.node.get_all_context(),
                                      [s.stack_name for s in specs])
    if mode == "create":
        DelphiArtifactsStack(app, "Delphi-artifacts", build_root=build_root)
    for spec in specs:
        # One stack instantiation per experimental arm. Each becomes a separate deployed
        # runtime; routing vs. pinned is set by the DELPHI_ARM env var (design D4).
        DelphiRuntimeStack(app, spec.stack_name, arm=spec.arm, build_root=build_root,
                           image_tag=overrides.get(spec.stack_name))
    nag.apply(app)  # every stack, both modes (LC-U6-4)
    return app


def main() -> int:
    assembly = build_app(cdk.App()).synth()
    errors = nag.unsuppressed_errors(assembly.directory)
    for line in errors:
        print(f"cdk-nag: unsuppressed {line}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
