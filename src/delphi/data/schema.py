"""Query / Dataset / Split — the data types the whole Phase 1 pipeline moves around.

A Query carries everything the matrix builder and scorers need: the prompt, the task
type (which selects the native scorer), the ground-truth answer (when one exists), and a
stable id. `domain` is the semantic bucket a query belongs to; the synthetic generator
uses it to decide which advisor should win, but the router/scorers never see it — it is
metadata for analysis only, never a routing feature.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from enum import Enum


class TaskType(str, Enum):
    MATH = "math"                 # exact-match on a numeric answer
    CODE = "code"                 # pass@1 via sandboxed execution
    KNOWLEDGE_MC = "knowledge_mc" # exact-match on a choice letter
    OPEN_QA = "open_qa"           # F1 against a reference string
    OPEN_ENDED = "open_ended"     # no ground truth -> judge only


@dataclass(frozen=True)
class Query:
    id: str
    prompt: str
    task_type: TaskType
    domain: str                       # analysis bucket, NOT a routing feature
    answer: str | None = None         # ground truth; None for OPEN_ENDED
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def make_id(prompt: str) -> str:
        return hashlib.sha1(prompt.encode(), usedforsecurity=False).hexdigest()[:16]


@dataclass(frozen=True)
class Dataset:
    name: str
    queries: tuple[Query, ...]

    def __len__(self) -> int:
        return len(self.queries)

    def prompts(self) -> list[str]:
        return [q.prompt for q in self.queries]

    def content_hash(self) -> str:
        """Stable hash of the dataset content — feeds the replayability manifest (T-INF-4)."""
        h = hashlib.sha256()
        h.update(self.name.encode())
        for q in sorted(self.queries, key=lambda x: x.id):
            h.update(q.id.encode())
            h.update(q.prompt.encode())
            h.update(q.task_type.value.encode())
            h.update((q.answer or "").encode())
        return h.hexdigest()


@dataclass(frozen=True)
class Split:
    train: Dataset
    validation: Dataset
    test: Dataset

    def manifest(self) -> dict:
        """The hashed manifest that makes an eval run reproducible (T-INF-4)."""
        return {
            "train": {"n": len(self.train), "hash": self.train.content_hash()},
            "validation": {"n": len(self.validation), "hash": self.validation.content_hash()},
            "test": {"n": len(self.test), "hash": self.test.content_hash()},
        }
