# Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## Reporting a Vulnerability

If you discover a potential security issue in this project we ask that you notify AWS/Amazon Security via our [vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/). Please do **not** create a public GitHub issue.

## Security considerations for users of this sample

This is sample code, not production-ready software. Review it against your own security
requirements before you deploy it.

- **Generated-code execution is not sandboxed.** The `pass_at_1` scorer is off by default and
  executes model output only after an explicit opt-in (`allow_exec=True` or
  `DELPHI_ALLOW_UNSANDBOXED_EXEC=1`). When enabled, the code runs in a child process with an
  empty environment, restricted builtins and a timeout. That child process is **not a
  sandbox**: it can be escaped, and it has your user's file system and network access. Enable it
  only inside a disposable, isolated environment (for example a microVM, or a seccomp-confined
  container with no credentials and no network).
- **Deployed runtimes use AgentCore's public network mode with IAM (SigV4) authorization.**
  Callers need `bedrock-agentcore:InvokeAgentRuntime` on the runtime; there is no inbound
  authorizer beyond IAM. Grant that permission narrowly.
- **Least privilege.** Each runtime's execution role can invoke only its own candidate models,
  and only the router can read the artifact bucket, under `router/*`. The role's trust policy
  carries `aws:SourceAccount` and `aws:SourceArn` conditions. CloudWatch Logs actions are
  scoped to the AgentCore runtime log groups (`/aws/bedrock-agentcore/runtimes/*`). Only X-Ray,
  `PutMetricData` and `ecr:GetAuthorizationToken`, which support no resource-level permissions,
  are granted on `*`; the cdk-nag suppressions in `infra/delphi_infra/nag.py` give the reason for
  each.
- **Costs and data.** Deploying and invoking the stacks calls Amazon Bedrock models, which
  incurs charges. The runtime log groups keep decision records, including prompt hashes and
  token counts but not prompt text, for 90 days.
