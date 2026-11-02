"""Image selection and the rollback override — LC-U6-3 ImageOverride (U6 BR5.1-BR5.3; FR8.2;
INF-U6-12).

By default an arm runs the image built from the working tree (a `DockerImageAsset`). A rollback
names a previously deployed image by TAG only, for ONE stack, through synth context:

    cdk deploy Delphi-router -c artifactBucket=existing -c imageOverride:Delphi-router=<64 hex>

The tag is composed into the same shared CDK container-assets repository the execution role may
pull from, so an override cannot point anywhere else, and no account id ever appears in the
command or in a deploy record (NFR8.11).

Under an override the stack creates NO image asset (INF-U6-12): the deploy then builds nothing
and pushes nothing, so a rollback does not depend on Docker or on the working tree. Measured at
Code Generation (probe c): the composed `ContainerUri` and the ECR repository ARN render
byte-identical to the asset's, except the tag.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

OVERRIDE_PREFIX = "imageOverride:"
_TAG = re.compile(r"^[0-9a-f]{64}$")


class ImageOverrideError(ValueError):
    """A malformed tag, an unknown stack, or an override committed to cdk.json (fails synth)."""


def reject_committed(cdk_context: Mapping[str, object]) -> None:
    """An override is a per-deploy decision, never configuration (BR5.2)."""
    committed = sorted(k for k in cdk_context if k.startswith(OVERRIDE_PREFIX))
    if committed:
        raise ImageOverrideError(f"infra/cdk.json carries {committed}; an image override is "
                                 "passed per deploy with -c, never committed")


def parse_overrides(context: Mapping[str, object], stacks: Iterable[str]) -> dict[str, str]:
    """{stack name: tag} for every `imageOverride:<stack>` key; refuses anything malformed."""
    known = set(stacks)
    out: dict[str, str] = {}
    for key, value in context.items():
        if not key.startswith(OVERRIDE_PREFIX):
            continue
        stack = key[len(OVERRIDE_PREFIX):]
        if stack not in known:
            raise ImageOverrideError(f"{key}: stack {stack!r} is not in ARMS "
                                     f"({sorted(known)})")
        tag = value if isinstance(value, str) else ""
        if not _TAG.match(tag):
            raise ImageOverrideError(f"{key}: the value must be 64 lowercase hex characters "
                                     f"(an image tag), got {str(value)[:80]!r}")
        out[stack] = tag
    return out


def asset_repository_name(scope) -> str:
    """The synthesizer's container-assets repository name, with the bootstrap qualifier."""
    from aws_cdk import DefaultStackSynthesizer

    qualifier = (scope.node.try_get_context("@aws-cdk/core:bootstrapQualifier")
                 or DefaultStackSynthesizer.DEFAULT_QUALIFIER)
    return DefaultStackSynthesizer.DEFAULT_IMAGE_ASSETS_REPOSITORY_NAME.replace(
        "${Qualifier}", qualifier)


def override_image(stack, tag: str) -> tuple[str, str]:
    """(container URI, repository ARN) for `tag`, composed exactly as the asset composes them."""
    from aws_cdk import Fn, aws_ecr as ecr

    name = asset_repository_name(stack)
    uri = Fn.sub("${AWS::AccountId}.dkr.ecr.${AWS::Region}.${AWS::URLSuffix}/"
                 + name + ":" + tag)
    repo = ecr.Repository.from_repository_name(stack, "AssetRepository", Fn.sub(name))
    return uri, repo.repository_arn
