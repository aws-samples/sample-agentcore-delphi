"""Configuration loading and the candidate-pool / pricing model.

The pricing figures live in config/models.yaml and are a pinned snapshot
(Threat #4). Everything cost-related in the codebase derives from Candidate.price_per_1k
so there is exactly one place to re-price when the snapshot is refreshed.
"""
from __future__ import annotations

import functools
import os
import pathlib
from dataclasses import dataclass

import yaml


def _resolve_config_dir() -> pathlib.Path:
    """Find the config directory across dev (repo) and deployed (container) layouts.

    Order: (1) $DELPHI_CONFIG_DIR override, (2) repo layout <root>/config, (3) a copy
    bundled next to this module (containers COPY config there). The container sets the
    env var explicitly, so this never depends on the installed package's depth on disk —
    the bug that took down the first deploy (parents[2] pointed outside site-packages).
    """
    env = os.environ.get("DELPHI_CONFIG_DIR")
    if env:
        return pathlib.Path(env)
    repo = pathlib.Path(__file__).resolve().parents[2] / "config"
    if repo.is_dir():
        return repo
    return pathlib.Path(__file__).resolve().parent / "config"


_CONFIG_DIR = _resolve_config_dir()


@dataclass(frozen=True)
class Price:
    """USD per 1K tokens."""

    input: float
    output: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        """Dollar cost of a single call given token counts."""
        return (input_tokens / 1000.0) * self.input + (
            output_tokens / 1000.0
        ) * self.output


@dataclass(frozen=True)
class Candidate:
    alias: str
    id: str
    family: str
    price: Price
    verified: bool
    note: str | None = None


@dataclass(frozen=True)
class Pool:
    candidates: tuple[Candidate, ...]
    judge_id: str
    judge_alias: str
    snapshot_date: str

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(c.alias for c in self.candidates)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(c.id for c in self.candidates)

    def by_alias(self, alias: str) -> Candidate:
        for c in self.candidates:
            if c.alias == alias:
                return c
        raise KeyError(f"no candidate with alias {alias!r}; pool={self.aliases}")

    def by_id(self, model_id: str) -> Candidate:
        for c in self.candidates:
            if c.id == model_id:
                return c
        raise KeyError(f"no candidate with id {model_id!r}")

    def price_spread(self) -> float:
        """Ratio of most-expensive to cheapest by blended (1:1) token price.

        H1's cost headroom is trivial unless this exceeds ~10x (design §2.3).
        """
        blended = [c.price.input + c.price.output for c in self.candidates]
        return max(blended) / min(blended)

    def unverified(self) -> tuple[Candidate, ...]:
        """Candidates whose prices still need Phase 0 confirmation."""
        return tuple(c for c in self.candidates if not c.verified)


@functools.lru_cache(maxsize=1)
def load_pool(path: pathlib.Path | None = None) -> Pool:
    if path is None:
        # DELPHI_MODELS_FILE lets a deployment pick a specific pool file (e.g. the
        # real-pool config for the live router arm) without a code change. Falls back
        # to models.yaml in the resolved config dir.
        env_file = os.environ.get("DELPHI_MODELS_FILE")
        path = pathlib.Path(env_file) if env_file else (_CONFIG_DIR / "models.yaml")
    data = yaml.safe_load(path.read_text())
    candidates = tuple(
        Candidate(
            alias=c["alias"],
            id=c["id"],
            family=c["family"],
            price=Price(c["price_per_1k"]["input"], c["price_per_1k"]["output"]),
            verified=c.get("verified", False),
            note=c.get("note"),
        )
        for c in data["candidates"]
    )
    return Pool(
        candidates=candidates,
        judge_id=data["judge"]["id"],
        judge_alias=data["judge"]["alias"],
        snapshot_date=data["pricing_snapshot"]["publication_date"],
    )
