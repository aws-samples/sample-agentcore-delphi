"""LearnedRouter — wraps a trained artifact (RouteLLM/LLMRouter lineage, design D1).

The artifact (classifier weights + encoder choice + pool manifest) is produced offline
in Phase 2 and pulled from S3 at cold start, or baked into the image for immutability.
This class is the thin request-path adapter: embed -> predict -> Decision.

The scoring model itself is not implemented here — Phase 2 selects among RouteLLM
BERT-classifier, LLMRouter mlprouter/graphrouter/mfrouter. This wires the loaded
artifact into the Router contract and enforces the pool-drift guard (T-RTR-6).

It does not recover from its own failures: an exception from predict propagates, and the
request-path fallback is RouterAgent's (agent/app.py), applied to every router alike
(ADR-005, BR1.1).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from delphi.config import Pool
from delphi.router.base import Decision, Router, RouterError
from delphi.router.embedding import Embedder


@dataclass(frozen=True)
class Artifact:
    """Everything needed to reconstruct a trained router deterministically.

    trained_aliases is the pool the router was trained on; if it differs from the
    live deployed pool, predict() must refuse to emit an unknown alias (T-RTR-6).
    artifact_hash feeds the replayability manifest (T-INF-4).
    """

    family: str  # "bert-classifier" | "mlprouter" | "graphrouter" | "mfrouter"
    trained_aliases: tuple[str, ...]
    encoder_name: str
    weights_uri: str
    artifact_hash: str

    @classmethod
    def from_manifest(cls, manifest_path: str) -> "Artifact":
        d = json.loads(open(manifest_path).read())
        return cls(
            family=d["family"],
            trained_aliases=tuple(d["trained_aliases"]),
            encoder_name=d["encoder_name"],
            weights_uri=d["weights_uri"],
            artifact_hash=d["artifact_hash"],
        )


def unknown_aliases(trained, pool_aliases) -> set[str]:
    """Aliases an artifact was trained on that the pool does not offer (T-RTR-6).

    The ONE definition of the alias rule: LearnedRouter applies it at cold start and the
    C3 checker (delphi.train.artifact_contract) applies it before publication.
    """
    return set(trained) - set(pool_aliases)


class LearnedRouter(Router):
    def __init__(
        self,
        pool: Pool,
        artifact: Artifact,
        embedder: Embedder | None,
        scorer,  # callable: (embedding | query, trained_aliases) -> dict[alias, score]
        *,
        mono=time.perf_counter,  # injectable monotonic clock, seconds (BR4.6)
    ):
        self.pool = pool
        self.pool_aliases = pool.aliases
        self.artifact = artifact
        self.embedder = embedder
        self._scorer = scorer
        self._mono = mono

        # T-RTR-6: the router must never be able to emit an alias absent from the
        # live pool. Refuse to construct if the trained pool isn't a subset.
        unknown = unknown_aliases(artifact.trained_aliases, self.pool_aliases)
        if unknown:
            raise RouterError(
                f"artifact trained on aliases {sorted(unknown)} absent from deployed "
                f"pool {self.pool_aliases} (T-RTR-6)"
            )

    def predict(self, query: str) -> Decision:
        """Route one query. latency_ms is embed plus score on the injected clock.

        When an embedder exists, its own timing goes into metadata["embed_ms"] - the producer
        of the record's embed figure (BR4.3). It is metadata, never a score key: T-RTR-2
        pins the scores' key set to the pool aliases. No I/O and no logging here.
        """
        start = self._mono()
        metadata: dict[str, object] = {
            "policy": "learned",
            "family": self.artifact.family,
            "artifact_hash": self.artifact.artifact_hash,
        }
        if self.embedder is not None:
            features = self.embedder.embed(query)
            metadata["embed_ms"] = (self._mono() - start) * 1000.0
        else:
            features = query
        scores = self._scorer(features, self.artifact.trained_aliases)
        chosen = max(scores, key=scores.__getitem__)
        latency_ms = (self._mono() - start) * 1000.0
        return self._validate(
            Decision(
                model_id=self.pool.by_alias(chosen).id,
                chosen_alias=chosen,
                scores=scores,
                latency_ms=latency_ms,
                metadata=metadata,
            )
        )
