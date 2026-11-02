"""Router contract. Pins the schema that T-RTR-2 asserts against.

Every router — the trivial baselines, the learned artifacts, the oracle — implements
this interface so the eval harness and the agent treat them interchangeably (arm parity,
T-INF-1). The only independent variable across arms is which Router instance is wired in.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field


class RouterError(Exception):
    """Raised by a router on an unrecoverable routing failure. Routers do not recover from
    it. The request-path fallback is RouterAgent's (agent/app.py): it applies to any
    exception from any router, RouterError included, and routes to the agent's fallback
    alias with is_fallback true (T-RTR-3, BR1.1)."""


@dataclass(frozen=True)
class Decision:
    """The output contract of Router.predict() — asserted by T-RTR-2.

    scores maps EVERY candidate alias -> the router's score for it (higher = better).
    model_id is the Bedrock modelId of the chosen candidate (must be in the deployed
    pool — T-RTR-6; enforced by the agent as well as by Router._validate, BR1.2).
    latency_ms is embed + predict wall time, not generation.
    """

    model_id: str
    chosen_alias: str
    scores: dict[str, float]
    latency_ms: float
    is_fallback: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


class Router(abc.ABC):
    """A routing policy over a fixed candidate pool."""

    #: aliases the router may emit; guards drift between trained and live pool (T-RTR-6)
    pool_aliases: tuple[str, ...]

    @abc.abstractmethod
    def predict(self, query: str) -> Decision:
        """Return a routing Decision for a single query. Deterministic given the
        same query + weights + seed (T-RTR-1)."""
        raise NotImplementedError

    def _validate(self, decision: Decision) -> Decision:
        """Common guard used by concrete routers before returning."""
        if decision.chosen_alias not in self.pool_aliases:
            raise RouterError(
                f"router emitted alias {decision.chosen_alias!r} not in deployed pool "
                f"{self.pool_aliases} (T-RTR-6 drift guard)"
            )
        return decision
