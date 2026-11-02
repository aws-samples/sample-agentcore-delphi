"""Bedrock Converse client wrapper.

Thin adapter over bedrock-runtime `converse`. Isolated behind an interface so the agent
and the eval harness can run against a fake in tests (no live Bedrock calls in unit tests)
and so arm parity (T-INF-1) is enforced: the same generation params flow through here for
every arm; the only thing that varies upstream is which model_id the router chose.

One converse() is one model call (ENT-207; C1 § Retry, corrected). The client is built with
botocore `total_max_attempts: 1`, which COUNTS the first request; `max_attempts: 1` would
allow two calls. An explicit Config also wins over AWS_MAX_ATTEMPTS / AWS_RETRY_MODE in the
environment. Without this, botocore's default retries could make several billed model calls
inside one invocation while the record reported one call's tokens.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

#: The retry posture of every bedrock-runtime client this module builds or accepts.
SINGLE_ATTEMPT_RETRIES = {"total_max_attempts": 1, "mode": "standard"}


class RegionNotSetError(RuntimeError):
    """No region was given. There is no default region (U6 BR11.7, NFR6.1).

    A literal default once sent every reader, and every local run, to one region whatever the
    deployment's own region was. The region now comes from an argument, from AWS_REGION (the
    stack always sets it on each arm), or from a runtime ARN, and its absence fails loudly.
    """


def build_runtime_client(region: str):
    """The single-attempt bedrock-runtime client (BR7.1). The SDK is imported here, not at
    module scope, so every module imports without credentials or the SDK."""
    import boto3
    from botocore.config import Config

    return boto3.client("bedrock-runtime", region_name=region,
                        config=Config(retries=dict(SINGLE_ATTEMPT_RETRIES)))


def require_single_attempt(client):
    """Refuse an injected client that could retry (BR7.2, TS-U2-4).

    botocore normalises a correct `max_attempts: 0` to `total_max_attempts: 1`, so one
    predicate accepts both correct spellings. A client with no explicit retries config
    reports no such key and is refused: botocore's defaults would apply. Stated limit: this
    fixes the attempt count, not the retry mode.
    """
    meta = getattr(client, "meta", None)
    retries = getattr(getattr(meta, "config", None), "retries", None) or {}
    if retries.get("total_max_attempts") != 1:
        raise ValueError(
            "an injected bedrock-runtime client must make exactly one attempt per call: build "
            "it with botocore Config(retries={'total_max_attempts': 1, 'mode': 'standard'}) "
            f"(its retries config is {retries!r})"
        )
    return client


@dataclass(frozen=True)
class GenerationParams:
    """Identical across all arms (T-INF-1). Any per-arm divergence confounds the study."""

    max_tokens: int = 1024
    temperature: float = 0.0
    top_p: float = 1.0


@dataclass(frozen=True)
class ConverseResult:
    text: str
    input_tokens: int
    output_tokens: int
    model_id: str


class ConverseClient(abc.ABC):
    @abc.abstractmethod
    def converse(
        self, model_id: str, prompt: str, params: GenerationParams
    ) -> ConverseResult:
        raise NotImplementedError


class BedrockConverseClient(ConverseClient):
    """Real client. Uses token counts from the response usage block for cost ground truth."""

    def __init__(self, region: str, client=None):
        if not region:
            raise RegionNotSetError("BedrockConverseClient needs a region; pass one (the agent "
                                    "passes AWS_REGION). There is no default region")
        self._region = region
        # boto3 bedrock-runtime, built lazily; an injected one must be single-attempt.
        self._client = require_single_attempt(client) if client is not None else None

    def _ensure_client(self):
        """The one place the client is resolved, so a test can reach the lazy client."""
        if self._client is None:
            self._client = build_runtime_client(self._region)
        return self._client

    def converse(
        self, model_id: str, prompt: str, params: GenerationParams
    ) -> ConverseResult:
        resp = self._ensure_client().converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={
                "maxTokens": params.max_tokens,
                "temperature": params.temperature,
                "topP": params.top_p,
            },
        )
        usage = resp["usage"]
        text = resp["output"]["message"]["content"][0]["text"]
        return ConverseResult(
            text=text,
            input_tokens=usage["inputTokens"],
            output_tokens=usage["outputTokens"],
            model_id=model_id,
        )
