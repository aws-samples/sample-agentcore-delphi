"""Deterministic train/validation/test splitting + manifest.

Splits by a seeded hash of query id so the assignment is stable across runs and
independent of query order (T-INF-4 replayability). The contamination guard (T-STAT-6)
is a no-op here because a hash-partition is disjoint by construction, but the pipeline
still runs check_contamination as a belt-and-suspenders assertion.
"""
from __future__ import annotations

import hashlib

from delphi.data.schema import Dataset, Query, Split


def _bucket(query_id: str, seed: int) -> float:
    h = hashlib.sha256(f"{seed}:{query_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def split_dataset(
    ds: Dataset, *, train: float = 0.6, validation: float = 0.2, seed: int = 20260714
) -> Split:
    """Partition into train/val/test by hashed id. `test` gets the remainder."""
    if not (0 < train < 1 and 0 < validation < 1 and train + validation < 1):
        raise ValueError("train/validation must be fractions summing to < 1")
    tr: list[Query] = []
    va: list[Query] = []
    te: list[Query] = []
    for q in ds.queries:
        b = _bucket(q.id, seed)
        if b < train:
            tr.append(q)
        elif b < train + validation:
            va.append(q)
        else:
            te.append(q)
    return Split(
        train=Dataset(f"{ds.name}:train", tuple(tr)),
        validation=Dataset(f"{ds.name}:val", tuple(va)),
        test=Dataset(f"{ds.name}:test", tuple(te)),
    )
