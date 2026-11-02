"""Delphi AgentCore Runtime stack (one per experimental arm).

Builds the agent into an ARM64 container image and registers it as an
AWS::BedrockAgentCore::Runtime via the L1 CfnRuntime construct in aws-cdk-lib. The image
carries code and config only: the router arm pulls its trained artifact from S3 at cold
start (DELPHI_ARTIFACT_URI, design D1), so an artifact change needs no image rebuild.
Also creates the least-privilege execution role the runtime
assumes to call Bedrock and emit telemetry.

This is the current supported IaC path. It intentionally does NOT use:
  * bedrock-agentcore-starter-toolkit (the older Python deploy helper), or
  * AWS::Bedrock::Agent / bedrock-agent-runtime (classic Bedrock Agents).

U6a (LC-U6-1, LC-U6-3): the artifact bucket's binding mode is a REQUIRED synth context value,
`artifactBucket` in {existing, create}, with no default (NFR8.1). In `existing` mode the bucket is
referenced by name and never managed: no bucket resource is synthesized, so no deploy, rollback
or destroy of an arm stack can modify or delete it (NFR8.2). In `create` mode a separate
`Delphi-artifacts` stack provisions it (DelphiArtifactsStack below, NFR8.3). The arm stacks are
identical in both modes: they name the bucket from config/artifact-location.json, the one
declaration of its name (NFR8.4), and take no cross-stack reference. An image override
(`delphi_infra.image`) replaces the built asset with a tag for one stack, and every arm outputs
its ContainerUri, so a rollback target is read from the stack, never predicted (FR8.2.2).

U6b (LC-U6-2, LC-U6-4): each arm's execution policy is derived by `delphi_infra.grants` from the
arm's own pool and infra/profile_routing.json, so an arm can invoke only its candidates, and only
the router reads the artifact bucket, under `router/*`. The cdk-nag register
(`delphi_infra.nag`) is attached to each policy and to the create-mode bucket; the Aspect itself
is applied to the whole app in infra/app.py. Every construct id is unchanged, so no logical id
moves (T-INF-U6-5).
"""
from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_bedrockagentcore as agentcore,
    aws_ecr_assets as ecr_assets,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct

from delphi_infra import arms, grants, image as image_override, nag

# Invariant: this file sits exactly two levels below the repository root (T-PKG-U3-14).
_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The bucket binding modes and when each applies (NFR8.1). Named in the error a reader sees.
BUCKET_MODES = {
    "existing": "the artifact bucket already exists in this account (the author's account): "
                "the stacks reference it by name and never manage it",
    "create": "a clean account: a separate Delphi-artifacts stack provisions the bucket, "
              "retained on stack deletion",
}


#: How long the create-mode access log bucket keeps server access logs.
ACCESS_LOG_DAYS = 365

#: How long the runtime's application log group keeps decision records.
LOG_RETENTION = logs.RetentionDays.THREE_MONTHS


class BucketModeError(ValueError):
    """`artifactBucket` is missing, unknown, or committed to cdk.json (fails synth)."""


def bucket_mode_message(problem: str) -> str:
    modes = "; ".join(f"'{k}' when {v}" for k, v in BUCKET_MODES.items())
    return (f"{problem}. Pass -c artifactBucket=existing or -c artifactBucket=create: {modes}. "
            "It has no default, because each value is wrong for the other audience")


def bucket_mode(scope: Construct) -> str:
    mode = scope.node.try_get_context("artifactBucket")
    if mode not in BUCKET_MODES:
        problem = ("synth context artifactBucket is not set" if mode is None
                   else f"synth context artifactBucket is {mode!r}")
        raise BucketModeError(bucket_mode_message(problem))
    return mode


def _location(build_root: Path) -> dict:
    """config/artifact-location.json, read with stdlib json (C6; T-PKG-U3-20)."""
    return json.loads((build_root / "config" / "artifact-location.json").read_text())


def _bucket_name(loc: dict, stack: Stack) -> str:
    """The bucket name from its one declaration. The plain format over the account/region
    tokens synthesizes the same Fn::Join the stack has always rendered (T-PKG-U3-23)."""
    return loc["bucket"].format(account=stack.account, region=stack.region)


def _artifact_uri(loc: dict, stack: Stack) -> str:
    """The router arm's DELPHI_ARTIFACT_URI, composed from config/artifact-location.json.

    That file is the ONE location definition, shared with the `delphi-artifact` publisher
    (C6; T-PKG-U3-20). The plain f-string over the account/region tokens synthesizes the same
    Fn::Join as before (T-PKG-U3-23).
    """
    form = loc["serving_form"]
    if form == "legacy_prefix":
        key = loc["legacy_prefix"]
    elif form == "pointer":
        key = loc["pointer_key"]
    else:
        raise ValueError(f"artifact-location.json: unknown serving_form {form!r}")
    return f"s3://{_bucket_name(loc, stack)}/{key}"


def _scope_log_retention(stack: Stack, retention, log_groups: list[str]) -> None:
    """Scope the LogRetention singleton's retention grant from '*' to `log_groups`.

    aws-cdk-lib renders that grant as the only statement of the singleton role's DefaultPolicy
    and does not expose it. The policy document is resolved, its one statement re-scoped, and
    assigned back, so cdk-nag and the template both see the scoped resource.
    """
    singleton = next(c for c in stack.node.children if c.node.id.startswith("LogRetention")
                     and c is not retention)
    policy = singleton.node.find_child("ServiceRole").node.find_child("DefaultPolicy")
    cfn = policy.node.default_child
    document = stack.resolve(cfn.policy_document)
    (statement,) = document["Statement"]
    if sorted(statement["Action"]) != ["logs:DeleteRetentionPolicy", "logs:PutRetentionPolicy"]:
        raise ValueError(f"unexpected LogRetention grant: {statement['Action']}")
    cfn.policy_document = {**document, "Statement": [{**statement, "Resource": log_groups}]}
    # Checkov's and cfn-nag's Lambda hardening rules do not fit this construct-owned helper;
    # each skip says why.
    fn = singleton.node.default_child
    fn.add_metadata("cfn_nag", {"rules_to_suppress": [
        {"id": "W58", "reason": "The role carries AWSLambdaBasicExecutionRole, a managed policy "
                                "that grants CloudWatch Logs; cfn-nag cannot resolve it"},
        {"id": "W89", "reason": "Calls only the CloudWatch Logs API and handles no customer "
                                "data; no VPC resources to reach"},
        {"id": "W92", "reason": "Construct-owned helper, invoked once per deploy by "
                                "CloudFormation; reserved concurrency adds nothing"},
    ]})
    fn.add_metadata("checkov", {"skip": [
        {"id": "CKV_AWS_115", "comment": "Construct-owned helper, invoked once per deploy by "
                                         "CloudFormation; reserved concurrency adds nothing"},
        {"id": "CKV_AWS_116", "comment": "Invoked synchronously by CloudFormation, which "
                                         "receives and reports every failure; no async DLQ"},
        {"id": "CKV_AWS_117", "comment": "Calls only the CloudWatch Logs API and handles no "
                                         "customer data; no VPC resources to reach"},
    ]})


class DelphiArtifactsStack(Stack):
    """`create` mode only: the artifact bucket for a reader's clean account (NFR8.3, BR2.4).

    RETAIN is stated in code for both DeletionPolicy and UpdateReplacePolicy, never left to a
    default, so `cdk destroy Delphi-artifacts` keeps the bucket and its data (AC1.2.3). Six
    controls are declared: public access fully blocked, SSE-S3, TLS enforced by bucket policy,
    BucketOwnerEnforced, versioning on, and server access logging to a second bucket in this
    stack that carries the same controls and expires its logs. No auto-delete custom resource
    is added.
    """

    def __init__(self, scope: Construct, construct_id: str, *, build_root: Path | None = None,
                 **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        build_root = Path(build_root) if build_root is not None else _REPO_ROOT
        # Deliver server access logs under a bucket policy (no ACL), which BucketOwnerEnforced
        # requires. Set on this stack only, so the arm stacks' templates are untouched.
        self.node.set_context("@aws-cdk/aws-s3:serverAccessLogsUseBucketPolicy", True)
        access_logs = s3.Bucket(
            self,
            "AccessLogBucket",
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            versioned=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(ACCESS_LOG_DAYS),
                                              noncurrent_version_expiration=Duration.days(1))],
        )
        s3.Bucket(
            self,
            "ArtifactBucket",
            bucket_name=_bucket_name(_location(build_root), self),
            removal_policy=RemovalPolicy.RETAIN,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            versioned=True,
            server_access_logs_bucket=access_logs,
            server_access_logs_prefix="artifact-bucket/",
        )


class DelphiRuntimeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, arm: str,
                 build_root: Path | None = None, image_tag: str | None = None, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        # Required, no default (NFR8.1). Read here too, so a stack built outside build_app
        # cannot skip the decision; the arm template is the same in both modes.
        bucket_mode(self)
        # The image build context. Injectable so tests stage the REAL construct over a
        # scratch tree (T-PKG-U3-15); infra/app.py passes nothing, so deploys use the repo.
        build_root = Path(build_root) if build_root is not None else _REPO_ROOT
        location = _location(build_root)  # read once: the grant and the URI name one bucket
        grants.check_location_prefix(location)

        # One derivation (arms.py): names, pool and candidates. The pool is read from the same
        # build root the image is built from, so the grant and the container read one file.
        # CfnRuntime name pattern: [a-zA-Z][a-zA-Z0-9_]{0,47}
        spec = arms.arm_spec(arm, build_root)
        runtime_name = spec.runtime_name

        # --- the image: built from the repo root, or a rollback tag (delphi_infra.image) ---
        if image_tag is None:
            image = ecr_assets.DockerImageAsset(
                self,
                "AgentImage",
                directory=str(build_root),
                platform=ecr_assets.Platform.LINUX_ARM64,
                # The context is filtered by ONE allowlist, the repo's .dockerignore. No second
                # filter is passed here: two disagreeing lists made the asset hash depend on
                # non-serving files (T-PKG-U3-14/-15).
            )
            container_uri, repository_arn = image.image_uri, image.repository.repository_arn
        else:
            # No asset under an override: the deploy builds and pushes nothing (INF-U6-12).
            container_uri, repository_arn = image_override.override_image(self, image_tag)

        # --- Least-privilege execution role (production-safety: no admin/*FullAccess) ---
        # Confused-deputy conditions from the AgentCore documentation (grants.TRUST_CONDITION_KEYS):
        # only AgentCore acting for a resource in this account and region may assume the role.
        trust = grants.trust_conditions(grants.TRUST_CONDITION_KEYS,
                                        grants.TRUST_CONDITION_PROVENANCE,
                                        region=self.region, account=self.account)
        role = iam.Role(
            self,
            "RuntimeExecutionRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com",
                                            conditions=trust or None),
            description=f"Delphi AgentCore runtime execution role ({arm})",
        )

        plan = grants.grant_plan(spec, routing=grants.load_routing(), region=self.region,
                                 account=self.account, bucket=_bucket_name(location, self))
        statements = [
            # Converse through bedrock:InvokeModel only (no stream API is called), on this
            # arm's candidates alone: each inference profile in the deploy region and its
            # underlying foundation model in every destination region infra/profile_routing.json
            # lists (BR3.1, BR3.2). No '*' in any resource.
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=list(plan.model_invoke),
            ),
            # Observability: OTEL decision records -> CloudWatch/X-Ray (design D6). The logs
            # actions are scoped to the AgentCore runtime log groups and their streams
            # (grants.NARROW_LOGS, BR3.5). X-Ray and PutMetricData have no resource-level
            # permissions. Each wildcard carries a nag suppression.
            iam.PolicyStatement(
                actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                resources=list(plan.logs),
            ),
            iam.PolicyStatement(
                actions=["xray:PutTraceSegments", "xray:PutTelemetryRecords",
                         "cloudwatch:PutMetricData"],
                resources=["*"],
            ),
            # ECR pull: AgentCore validates AT CREATE TIME that the execution role can pull
            # the image. GetAuthorizationToken is account-scoped by the service (must be "*");
            # the image-layer reads are on the shared CDK asset repository (container assets),
            # which every CDK app in this account and region pushes to (BR3.4).
            iam.PolicyStatement(
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            ),
            iam.PolicyStatement(
                actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                         "ecr:BatchCheckLayerAvailability"],
                resources=[repository_arn],
            ),
        ]
        if plan.artifact_read is not None:
            # The router alone pulls its artifact at cold start (design D1), under C6's
            # grant_prefix router/*: the legacy triple, the pointer and every hashed prefix,
            # so the pointer flip needs no IAM change (BR3.3). The pinned arm has no S3 grant.
            statements.append(iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[plan.artifact_read],
            ))

        # Attach as an explicit managed policy so we can force the Runtime to wait for it.
        # (The role's auto-generated DefaultPolicy is created in PARALLEL with the Runtime,
        # so AgentCore's create-time ECR validation can race ahead of the grant — the cause
        # of the earlier "Access denied while validating ECR URI" failure.)
        exec_policy = iam.ManagedPolicy(self, "RuntimeExecPolicy", statements=statements)
        role.add_managed_policy(exec_policy)
        nag.suppress_policy(exec_policy, spec, location, self)

        # DELPHI_CONFIG_DIR is also baked into the image (Dockerfile ENV), but set it at
        # the runtime config level too so config resolution never depends on image-layer
        # state — this is what makes the config path deterministic across redeploys.
        env = {"DELPHI_ARM": arm, "AWS_REGION": self.region, "DELPHI_CONFIG_DIR": "/app/config"}
        # The router arm loads its trained artifact from S3 at cold start (design D1).
        # It also uses the real-pool config so the deployed pool matches the artifact's
        # trained pool (T-RTR-6) and every candidate is an invokable Bedrock model.
        if arm == "router":
            env["DELPHI_ARTIFACT_URI"] = _artifact_uri(location, self)
            env["DELPHI_MODELS_FILE"] = "/app/config/models_realpool.yaml"

        runtime = agentcore.CfnRuntime(
            self,
            "Runtime",
            agent_runtime_name=runtime_name,
            agent_runtime_artifact=agentcore.CfnRuntime.AgentRuntimeArtifactProperty(
                container_configuration=agentcore.CfnRuntime.ContainerConfigurationProperty(
                    container_uri=container_uri,
                ),
            ),
            network_configuration=agentcore.CfnRuntime.NetworkConfigurationProperty(
                network_mode="PUBLIC",
            ),
            protocol_configuration="HTTP",
            role_arn=role.role_arn,
            environment_variables=env,
            description=f"Delphi router experiment arm: {arm}",
        )
        # Force the Runtime to wait until the exec policy is attached, so the create-time
        # ECR-pull validation cannot race the grant.
        runtime.node.add_dependency(exec_policy)
        runtime.node.add_dependency(role)

        # Retention on the runtime's application log group. The AgentCore service creates that
        # group, so it is not a CDK-owned LogGroup: LogRetention sets the policy whether or not
        # the group exists yet, and leaves the group in place when the stack is deleted.
        retention = logs.LogRetention(
            self,
            "RuntimeLogRetention",
            log_group_name=grants.runtime_log_group(runtime.attr_agent_runtime_id),
            retention=LOG_RETENTION,
        )
        _scope_log_retention(self, retention, grants.log_retention_groups(
            self.stack_name, region=self.region, account=self.account))
        nag.suppress_log_retention(self, retention)

        CfnOutput(self, "AgentRuntimeArn", value=runtime.attr_agent_runtime_arn)
        CfnOutput(self, "Arm", value=arm)
        # The rollback target, read from the stack before each deploy (FR8.2.2). Only the
        # 64-hex tag after the final ':' is ever recorded: the repository part carries the
        # account id (NFR8.11).
        CfnOutput(self, "ContainerUri", value=container_uri)
