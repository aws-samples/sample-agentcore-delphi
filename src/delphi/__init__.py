"""Delphi — a learned cross-model query router on Amazon Bedrock AgentCore.

See docs/ for the goal/TDD/delivery plan. The package is organised by lifecycle position,
and this docstring states the request path's packages as a machine-read line that
tests/test_boundary_statements.py compares with the import closure of
delphi.agent.entrypoint (T-BND-U4-7):

1. The request path - what the AgentCore container imports to serve an invocation.
   Request-path packages: delphi.agent, delphi.config, delphi.eval, delphi.failure_log,
   delphi.router, delphi.train.
   delphi.eval and delphi.train enter only in part: the named modules the closure reaches
   (delphi.eval.matrix and delphi.eval.cost; for delphi.train, the line in
   train/__init__.py). Their offline modules stay out of the container.
2. The reader's client - delphi.verify, the `delphi-verify` command that invokes a deployed
   runtime from the reader's machine. It is not imported by the container.
3. Offline - delphi.data, the rest of delphi.eval and the rest of delphi.train: dataset
   generation, response-matrix builds, scoring and router training, driven by scripts/ and
   the `delphi-artifact` command.
"""

__version__ = "0.1.0"
