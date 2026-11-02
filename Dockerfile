# Delphi AgentCore Runtime container.
# AgentCore Runtime requires linux/arm64; the CDK DockerImageAsset builds for it.
# The image runs the bedrock-agentcore SDK entrypoint, which serves the AgentCore
# HTTP protocol contract (POST /invocations, GET /ping on :8080).
# The base image is pinned by digest (python:3.13-slim, OCI index, read 2026-10-28), so a
# rebuild is reproducible; bump the digest deliberately to take OS and Python patches.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim@sha256:b3575dcf816cdacc5292df77a6e3e59b8941631078373a045efa2ac1e9c5c3b2

WORKDIR /app

# Container-only deps (request path): SDK + boto3 + numpy/pyyaml for router+config.
# The base image's bundled pip is replaced first: older pip releases carry known advisories.
RUN pip install --no-cache-dir pip==26.2.2
COPY requirements-agent.txt .
RUN pip install --no-cache-dir -r requirements-agent.txt

# Install the delphi package.
COPY pyproject.toml .
COPY src/ ./src/
COPY config/ ./config/
# --no-deps: the third-party set is exactly requirements-agent.txt above (one declaration).
RUN pip install --no-cache-dir --no-deps .

# Config is not Python-importable package data; point the app at the copied dir so it
# never has to guess the path from the installed package depth (the first-deploy bug).
ENV DELPHI_CONFIG_DIR=/app/config

# Run as an unprivileged user. The app writes only to a private mkdtemp directory under /tmp
# (the router artifact download), and :8080 needs no privilege.
RUN useradd --create-home --uid 1000 --user-group bedrock_agentcore
USER bedrock_agentcore

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/ping', timeout=4)"]
CMD ["python", "-m", "delphi.agent.entrypoint"]
