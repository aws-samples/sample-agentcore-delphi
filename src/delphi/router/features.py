"""Feature encoder for the request path (design D2, strategy (a)/(c)).

A dependency-free hashing encoder: word 1- and 2-gram feature hashing into a fixed-width
vector with signed buckets. No model weights, no network hop, microsecond-scale on CPU —
so it comfortably fits the <150ms P90 routing budget (T-RTR-4) and needs nothing baked
into the image beyond this code. It implements the Embedder interface so a trained router
can use it interchangeably with a future ONNX/Bedrock encoder.

Deterministic: buckets come from SHA-1, so the same query always yields the same vector
(underpins T-RTR-1 determinism).
"""
from __future__ import annotations

import hashlib
import re

import numpy as np

from delphi.router.embedding import Embedder

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _features(text: str) -> list[str]:
    toks = _tokens(text)
    grams = list(toks)
    grams += [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    return grams


class HashingEncoder(Embedder):
    """Signed feature hashing to a dense L2-normalised vector."""

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _hash(self, feature: str) -> tuple[int, float]:
        h = hashlib.sha1(feature.encode(), usedforsecurity=False).digest()
        idx = int.from_bytes(h[:4], "big") % self.dim
        sign = 1.0 if (h[4] & 1) else -1.0
        return idx, sign

    def embed_vec(self, query: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float64)
        for f in _features(query):
            idx, sign = self._hash(f)
            v[idx] += sign
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    def embed(self, query: str) -> list[float]:
        return self.embed_vec(query).tolist()

    def matrix(self, queries: list[str]) -> np.ndarray:
        """Stack embeddings for a list of queries -> (n, dim)."""
        return (np.vstack([self.embed_vec(q) for q in queries]) if queries
                else np.zeros((0, self.dim)))
