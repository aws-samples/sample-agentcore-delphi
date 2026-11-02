"""Embedding strategies (design D2) — the per-request latency bottleneck.

Three interchangeable strategies, resolved empirically in Phase 2:
  (a) LocalEncoder    — small ONNX-quantised encoder in-container, no network hop
  (b) BedrockEncoder  — Titan/Cohere embeddings API, +1 network call
  (c) none            — feature/MF routers (mfrouter) need no dense embedding

Only the interface is fixed here; concrete encoders are wired in Phase 2 once the
target-CPU latency numbers (T-RTR-4) decide the winner.
"""
from __future__ import annotations

import abc


class Embedder(abc.ABC):
    #: dimensionality of the returned vector; None for feature-based routers
    dim: int | None

    @abc.abstractmethod
    def embed(self, query: str) -> list[float]:
        raise NotImplementedError


class LocalEncoder(Embedder):
    """Strategy (a): in-container ONNX encoder. Placeholder until Phase 2 picks a model."""

    def __init__(self, model_path: str, dim: int = 384):
        self.model_path = model_path
        self.dim = dim
        self._session = None  # lazily loaded ONNX session

    def embed(self, query: str) -> list[float]:  # pragma: no cover - Phase 2
        raise NotImplementedError("wire ONNX session in Phase 2 (D2 resolution)")


class BedrockEncoder(Embedder):
    """Strategy (b): Bedrock embeddings API. +40-80ms network cost, zero container weight."""

    def __init__(self, model_id: str = "amazon.titan-embed-text-v2:0", dim: int = 1024):
        self.model_id = model_id
        self.dim = dim

    def embed(self, query: str) -> list[float]:  # pragma: no cover - Phase 2
        raise NotImplementedError("wire bedrock invoke_model in Phase 2 (D2 resolution)")
