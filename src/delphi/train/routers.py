"""Router families (design §4 Phase 2, D1/D2). Pure numpy — no torch/sklearn.

Three genuinely distinct learning approaches, each mapping a query embedding to a score
per candidate alias. They are analogues of the LLMRouter lineage the design names:
  * LinearRouter    — a per-candidate linear regressor on the quality label (mlprouter-ish).
  * PrototypeRouter — matrix-factorization / collaborative flavour: each candidate gets a
                      learned prototype vector; score = similarity to it (mfrouter-ish).
  * KNNRouter       — non-parametric: score = quality of the k nearest training queries
                      (a graph/neighbourhood router; strong when signal is local).

Every trained router exposes:
  * score(embedding, aliases) -> {alias: score}   (the LearnedRouter scorer contract)
  * to_state() / from_state()                      (numpy-only serialization for artifacts)

All are trained to PREDICT PER-CANDIDATE QUALITY, then the router picks argmax. This keeps
the three comparable and lets capture ratio (T-STAT-4) select among them on validation.
"""
from __future__ import annotations

import abc

import numpy as np


class TrainedRouter(abc.ABC):
    family: str
    aliases: tuple[str, ...]

    @abc.abstractmethod
    def score(self, embedding, aliases: tuple[str, ...]) -> dict[str, float]:
        """Map one query embedding -> {alias: predicted quality}. `aliases` is the live
        pool (LearnedRouter passes the trained pool); scores restricted to it."""

    @abc.abstractmethod
    def predict_quality(self, X: np.ndarray) -> np.ndarray:
        """Batch predict: (n, dim) -> (n, n_aliases) predicted quality. Used for selection."""

    @abc.abstractmethod
    def to_state(self) -> dict:
        ...

    @classmethod
    @abc.abstractmethod
    def from_state(cls, state: dict) -> "TrainedRouter":
        ...

    def _scores_from_row(self, row: np.ndarray, aliases: tuple[str, ...]) -> dict[str, float]:
        full = {a: float(row[j]) for j, a in enumerate(self.aliases)}
        return {a: full[a] for a in aliases if a in full}


# ---------------------------------------------------------------------------
class LinearRouter(TrainedRouter):
    family = "linear"

    def __init__(self, aliases, W: np.ndarray, b: np.ndarray):
        self.aliases = tuple(aliases)
        self.W = W  # (dim, n_aliases)
        self.b = b  # (n_aliases,)

    @classmethod
    def fit(cls, X: np.ndarray, Y: np.ndarray, aliases, l2: float = 1.0) -> "LinearRouter":
        """Ridge-regress each candidate's quality on the embedding (closed form)."""
        n, dim = X.shape
        Xb = np.hstack([X, np.ones((n, 1))])                 # bias column
        A = Xb.T @ Xb + l2 * np.eye(dim + 1)
        coef = np.linalg.solve(A, Xb.T @ Y)                  # (dim+1, n_aliases)
        return cls(aliases, coef[:-1], coef[-1])

    def predict_quality(self, X: np.ndarray) -> np.ndarray:
        return X @ self.W + self.b

    def score(self, embedding, aliases):
        row = np.asarray(embedding) @ self.W + self.b
        return self._scores_from_row(row, tuple(aliases))

    def to_state(self):
        return {"family": self.family, "aliases": list(self.aliases),
                "W": self.W, "b": self.b}

    @classmethod
    def from_state(cls, state):
        return cls(state["aliases"], np.asarray(state["W"]), np.asarray(state["b"]))


# ---------------------------------------------------------------------------
class PrototypeRouter(TrainedRouter):
    """Matrix-factorization flavour: learn a prototype embedding per candidate, weighted
    toward queries that candidate answers well. Score = cosine(query, prototype) scaled to
    the candidate's mean quality. Captures 'this kind of query suits this model' signal."""

    family = "prototype"

    def __init__(self, aliases, P: np.ndarray, scale: np.ndarray):
        self.aliases = tuple(aliases)
        self.P = P          # (n_aliases, dim) unit prototypes
        self.scale = scale  # (n_aliases,) mean quality per candidate

    @classmethod
    def fit(cls, X: np.ndarray, Y: np.ndarray, aliases) -> "PrototypeRouter":
        n, dim = X.shape
        n_alias = Y.shape[1]
        P = np.zeros((n_alias, dim))
        for j in range(n_alias):
            w = Y[:, j].copy()
            w = np.clip(w - w.mean(), 0, None)               # emphasise above-avg queries
            if w.sum() == 0:
                w = np.ones(n)
            proto = (X * w[:, None]).sum(axis=0)
            norm = np.linalg.norm(proto)
            P[j] = proto / norm if norm > 0 else proto
        scale = Y.mean(axis=0)
        return cls(aliases, P, scale)

    def predict_quality(self, X: np.ndarray) -> np.ndarray:
        sim = X @ self.P.T                                   # (n, n_aliases), X is unit-norm
        # blend similarity with each candidate's base rate so a weak-but-suited model can win
        return self.scale[None, :] + 50.0 * sim

    def score(self, embedding, aliases):
        e = np.asarray(embedding)
        row = self.scale + 50.0 * (self.P @ e)
        return self._scores_from_row(row, tuple(aliases))

    def to_state(self):
        return {"family": self.family, "aliases": list(self.aliases),
                "P": self.P, "scale": self.scale}

    @classmethod
    def from_state(cls, state):
        return cls(state["aliases"], np.asarray(state["P"]), np.asarray(state["scale"]))


# ---------------------------------------------------------------------------
class KNNRouter(TrainedRouter):
    """Non-parametric neighbourhood router: predicted quality of a candidate = mean quality
    it achieved on the k training queries nearest (cosine) to the input. Stores the training
    embeddings + label matrix. Strong when routing signal is local; heavier at inference."""

    family = "knn"

    def __init__(self, aliases, Xtr: np.ndarray, Ytr: np.ndarray, k: int = 15):
        self.aliases = tuple(aliases)
        self.Xtr = Xtr    # (n, dim) unit-norm
        self.Ytr = Ytr    # (n, n_aliases)
        self.k = k

    @classmethod
    def fit(cls, X, Y, aliases, k: int = 15) -> "KNNRouter":
        return cls(aliases, X, Y, k=min(k, len(X)) if len(X) else k)

    def _neighbours(self, E: np.ndarray) -> np.ndarray:
        sims = E @ self.Xtr.T                                # (m, n) cosine (unit vectors)
        idx = np.argsort(-sims, axis=1)[:, : self.k]         # top-k per row
        return idx

    def predict_quality(self, X: np.ndarray) -> np.ndarray:
        idx = self._neighbours(X)
        return np.stack([self.Ytr[nbr].mean(axis=0) for nbr in idx])

    def score(self, embedding, aliases):
        row = self.predict_quality(np.asarray(embedding)[None, :])[0]
        return self._scores_from_row(row, tuple(aliases))

    def to_state(self):
        return {"family": self.family, "aliases": list(self.aliases),
                "Xtr": self.Xtr, "Ytr": self.Ytr, "k": self.k}

    @classmethod
    def from_state(cls, state):
        return cls(state["aliases"], np.asarray(state["Xtr"]), np.asarray(state["Ytr"]),
                   k=int(state["k"]))


FAMILIES = {
    LinearRouter.family: LinearRouter,
    PrototypeRouter.family: PrototypeRouter,
    KNNRouter.family: KNNRouter,
}


def router_from_state(state: dict) -> TrainedRouter:
    return FAMILIES[state["family"]].from_state(state)
