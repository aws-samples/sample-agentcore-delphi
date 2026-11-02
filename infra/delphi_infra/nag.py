"""cdk-nag at every synth — LC-U6-4 NagAspect (U6b; BR4.1, BR4.2 as amended by NFR Design
delta 6; NFR9.4-NFR9.6; DD-U6-1; INF-U6-14).

`apply(app)` adds the AwsSolutions rule pack as an Aspect to the whole app, in both bucket
modes. Any error the pack reports and no suppression covers is an error annotation, and
`unsuppressed_errors` reads those back from the cloud assembly, so `infra/app.py` exits non-zero
on them (the CDK CLI refuses them too).

The suppression register, one entry per row, each attached to ONE resource (never a stack):

  * every arm's execution policy: AwsSolutions-IAM5 on `Resource::*`, ONCE, for the one merged
    `Resource: "*"` statement that `minimizePolicies` makes of the classes with no
    resource-level permissions: X-Ray, PutMetricData and ecr:GetAuthorizationToken
    (account-scoped). cdk-nag matches the finding string, not the action, so the entry would
    also silence an action added to that statement. STAR_ACTIONS is that statement's action
    set, and T-INF-U6-73 fails when the template's set differs;
  * every arm's execution policy: AwsSolutions-IAM5 on exactly the two logs findings, the
    AgentCore runtime log-group prefix and its log streams (grants.NARROW_LOGS);
  * the router's execution policy only: AwsSolutions-IAM5 on exactly the `router/*` artifact
    finding (DD-U6-1). Measured at Code Generation (probe (b)): the pack reports
    `Resource::arn:aws:s3:::<bucket>/router/*`, where <bucket> is the location file's template
    with the stack tokens printed as `<AWS::AccountId>` and `<AWS::Region>`;
  * each arm stack's LogRetention singleton (the CDK-provided Lambda that sets retention on the
    AgentCore-created log group): AwsSolutions-IAM4 on the AWSLambdaBasicExecutionRole managed
    policy and AwsSolutions-IAM5 on its retention statement, which runtime_stack.py scopes to
    the runtime log-group prefix and the function's own `/aws/lambda/<stack>-*` group. Both
    are attached to the singleton's own role and policy.

Rules the register keeps (T-INF-U6-65): `applies_to` is mandatory on every IAM5 entry, because
an IAM5 suppression without it silences every IAM5 finding on the resource, including a future
model-invoke regression to '*'; entries are literal strings, never regular expressions; none
names `bedrock`, so the model-invoke statement is never covered. Because a model-invoke `*`
would render the same `Resource::*` the observability entries suppress, the template test
T-INF-U6-10 is the second, independent defence.
"""
from __future__ import annotations

import pathlib

IAM5 = "AwsSolutions-IAM5"

#: The actions of the one merged `Resource: "*"` statement in every arm's policy (T-INF-U6-73).
STAR_ACTIONS = frozenset({
    "cloudwatch:PutMetricData", "ecr:GetAuthorizationToken", "xray:PutTelemetryRecords",
    "xray:PutTraceSegments"})

#: The `Resource::*` entry on every arm's policy: (gate, reason). ONE entry, because after
#: minimizePolicies there is one merged '*' statement, whose actions the reason names. The
#: reason is rendered into each policy's template Metadata (`cdk_nag.rules_to_suppress`).
STAR_ENTRIES: tuple[tuple[str, str], ...] = (
    ("X-Ray, PutMetricData, GetAuthorizationToken: no resource-level permissions",
     "One merged Resource:* statement (minimizePolicies) holding exactly "
     "cloudwatch:PutMetricData, ecr:GetAuthorizationToken, xray:PutTelemetryRecords, "
     "xray:PutTraceSegments (T-INF-U6-73). None of these actions supports resource-level "
     "permissions, so * is the only resource IAM accepts for them."),
)

LOGS_REASON = (
    "logs:CreateLogGroup, logs:CreateLogStream and logs:PutLogEvents scoped to the AgentCore "
    "runtime log groups (/aws/bedrock-agentcore/runtimes/*) and their streams, as the AgentCore "
    "execution-role documentation does: the one runtime's group is named by its runtime id, "
    "which exists only after this policy, so the prefix is granted")

ARTIFACT_REASON = (
    "s3:GetObject on C6 grant_prefix router/* only, one prefix of one bucket, router arm only: "
    "the content-addressed router/<artifact_hash>/ prefixes are produced at publish time and "
    "cannot be enumerated at synth. Gate: none, structural while C6 serves from hashed "
    "prefixes (DD-U6-1, proposal P-U6-13)")

IAM4 = "AwsSolutions-IAM4"

#: The managed policy aws-cdk-lib's LogRetention singleton attaches to its own Lambda role.
LOG_RETENTION_MANAGED_POLICY = (
    "Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")

LOG_RETENTION_IAM4_REASON = (
    "aws-cdk-lib's LogRetention singleton Lambda uses AWSLambdaBasicExecutionRole for its own "
    "CloudWatch Logs output; the construct does not expose the role's policies")

LOG_RETENTION_IAM5_REASON = (
    "aws-cdk-lib's LogRetention singleton gets logs:PutRetentionPolicy and "
    "logs:DeleteRetentionPolicy, scoped here to the AgentCore runtime log groups "
    "(/aws/bedrock-agentcore/runtimes/*), where it sets retention on a group the service "
    "creates under a runtime id known only after deploy, and to its own Lambda log group "
    "(/aws/lambda/<stack>-*), which it also caps; that function's name is generated at deploy")


def _token_text(value: str, placeholder: str) -> str:
    """How cdk-nag renders an unresolved stack token in a finding: `<AWS::AccountId>`."""
    from aws_cdk import Token

    return f"<{placeholder}>" if Token.is_unresolved(value) else value


def artifact_finding(location: dict, stack) -> str:
    """The exact IAM5 finding the router's `router/*` grant raises, as cdk-nag prints it."""
    from delphi_infra.grants import GRANT_PREFIX

    bucket = location["bucket"].format(
        account=_token_text(stack.account, "AWS::AccountId"),
        region=_token_text(stack.region, "AWS::Region"))
    return f"Resource::arn:aws:s3:::{bucket}/{GRANT_PREFIX}*"


def log_retention_findings(stack) -> list[str]:
    """The IAM5 findings the scoped LogRetention grant raises, as cdk-nag prints them."""
    from delphi_infra.grants import log_retention_groups

    rendered = log_retention_groups(stack.stack_name,
                                    region=_token_text(stack.region, "AWS::Region"),
                                    account=_token_text(stack.account, "AWS::AccountId"))
    return [f"Resource::{r}" for r in rendered]


def logs_findings(stack) -> list[str]:
    """The IAM5 findings the scoped logs grant raises, as cdk-nag prints them."""
    from delphi_infra.grants import DEFAULT_LOG_GROUP_PATTERN, logs_resources

    rendered = logs_resources(DEFAULT_LOG_GROUP_PATTERN,
                              region=_token_text(stack.region, "AWS::Region"),
                              account=_token_text(stack.account, "AWS::AccountId"))
    return [f"Resource::{r}" for r in rendered]


def policy_suppressions(spec, location: dict, stack) -> list:
    """The register entries for one arm's execution policy (NagPackSuppression objects)."""
    from cdk_nag import NagPackSuppression

    out = [NagPackSuppression(id=IAM5, reason=reason, applies_to=["Resource::*"])
           for _gate, reason in STAR_ENTRIES]
    out.append(NagPackSuppression(id=IAM5, reason=LOGS_REASON, applies_to=logs_findings(stack)))
    if spec.reads_artifact:
        out.append(NagPackSuppression(id=IAM5, reason=ARTIFACT_REASON,
                                      applies_to=[artifact_finding(location, stack)]))
    return out


#: cfn-nag's W13 (managed policy with a '*' resource) is the same merged statement STAR_ENTRIES
#: covers; the reason is rendered into the policy's template Metadata for template scanners.
CFN_NAG_W13_REASON = (
    "The one Resource:* statement holds only cloudwatch:PutMetricData, "
    "ecr:GetAuthorizationToken, xray:PutTelemetryRecords and xray:PutTraceSegments, none of "
    "which supports resource-level permissions")


def suppress_policy(policy, spec, location: dict, stack) -> None:
    """Attach the register to the policy's own CfnManagedPolicy, never to the stack."""
    from cdk_nag import NagSuppressions

    NagSuppressions.add_resource_suppressions(policy, policy_suppressions(spec, location, stack))
    policy.node.default_child.add_metadata("cfn_nag", {"rules_to_suppress": [
        {"id": "W13", "reason": CFN_NAG_W13_REASON}]})


def suppress_log_retention(stack, retention) -> None:
    """Attach the LogRetention register to the stack's singleton role (and its policy), found
    from the construct's own tree, never to the stack."""
    from cdk_nag import NagPackSuppression, NagSuppressions

    singleton = next(c for c in stack.node.children if c.node.id.startswith("LogRetention")
                     and c is not retention)
    NagSuppressions.add_resource_suppressions(singleton, [
        NagPackSuppression(id=IAM4, reason=LOG_RETENTION_IAM4_REASON,
                           applies_to=[LOG_RETENTION_MANAGED_POLICY]),
        NagPackSuppression(id=IAM5, reason=LOG_RETENTION_IAM5_REASON,
                           applies_to=log_retention_findings(stack)),
    ], apply_to_children=True)


def apply(app) -> None:
    """The AwsSolutions pack on every stack of `app`, writing a JSON report per stack."""
    from aws_cdk import Aspects
    from cdk_nag import AwsSolutionsChecks, NagReportFormat

    Aspects.of(app).add(AwsSolutionsChecks(reports=True,
                                           report_formats=[NagReportFormat.JSON]))


def unsuppressed_errors(outdir) -> list[str]:
    """Every AwsSolutions error annotation in a synthesized cloud assembly, as `<rule>: <path>`
    lines; [] when the pack found nothing unsuppressed.

    Read through the assembly reader, which knows where this aws-cdk-lib version keeps stack
    metadata (in the manifest, or in a `<Stack>.metadata.json` beside it).
    """
    from aws_cdk import cx_api

    assembly = cx_api.CloudAssembly(str(pathlib.Path(outdir)))
    out = []
    for stack in assembly.stacks:
        for message in stack.messages:
            data = str(message.entry.data)
            if message.level == cx_api.SynthesisMessageLevel.ERROR \
                    and data.startswith("AwsSolutions-"):
                out.append(f"{data.split(':', 1)[0]}: {message.id}")
    return sorted(out)
