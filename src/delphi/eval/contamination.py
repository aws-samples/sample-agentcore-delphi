"""Train/eval contamination guard (T-STAT-6).

Zero overlap required between router training queries and evaluation queries, checked
both exactly and by MinHash near-duplicate detection. Pure-stdlib MinHash so there is
no datasketch dependency for a check this simple.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"\w+")


def _shingles(text: str, k: int = 5) -> set[str]:
    """Word-level k-shingles, lowercased."""
    tokens = _TOKEN_RE.findall(text.lower())
    if len(tokens) < k:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _minhash(shingles: set[str], num_perm: int = 64) -> tuple[int, ...]:
    """MinHash signature: for each of num_perm hash seeds, the min hash over shingles."""
    if not shingles:
        return tuple([0] * num_perm)
    sig = []
    for seed in range(num_perm):
        mn = min(
            int(hashlib.sha1(f"{seed}:{s}".encode(), usedforsecurity=False).hexdigest(), 16)
            for s in shingles
        )
        sig.append(mn)
    return tuple(sig)


def _estimated_jaccard(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum(x == y for x, y in zip(a, b)) / len(a)


class NearDupFilter:
    """Greedy near-duplicate filter over a stream of texts, using the same MinHash
    criterion as check_contamination. Keeping only items that pass this filter
    guarantees a dataset with no internal near-dups — so any split of it is clean.
    """

    def __init__(self, *, near_dup_threshold: float = 0.8, num_perm: int = 64):
        self._threshold = near_dup_threshold
        self._num_perm = num_perm
        self._sigs: list[tuple[int, ...]] = []
        self._exact: set[str] = set()

    def accept(self, text: str) -> bool:
        """Return True and remember `text` if it is neither an exact nor near duplicate
        of anything accepted so far; otherwise return False."""
        norm = text.strip().lower()
        if norm in self._exact:
            return False
        sig = _minhash(_shingles(text), self._num_perm)
        if any(_estimated_jaccard(sig, s) >= self._threshold for s in self._sigs):
            return False
        self._exact.add(norm)
        self._sigs.append(sig)
        return True


@dataclass(frozen=True)
class ContaminationReport:
    exact_overlap: int
    near_dup_pairs: int
    clean: bool


def check_contamination(
    train: list[str],
    evalset: list[str],
    *,
    near_dup_threshold: float = 0.8,
    num_perm: int = 64,
) -> ContaminationReport:
    """T-STAT-6: assert zero exact + near-duplicate overlap between splits."""
    train_norm = {t.strip().lower() for t in train}
    exact = sum(1 for e in evalset if e.strip().lower() in train_norm)

    train_sigs = [_minhash(_shingles(t), num_perm) for t in train]
    near = 0
    for e in evalset:
        esig = _minhash(_shingles(e), num_perm)
        if any(
            _estimated_jaccard(esig, tsig) >= near_dup_threshold for tsig in train_sigs
        ):
            near += 1

    return ContaminationReport(
        exact_overlap=exact,
        near_dup_pairs=near,
        clean=(exact == 0 and near == 0),
    )
