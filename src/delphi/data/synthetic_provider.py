"""Synthetic advisors — fake "models" that answer Delphi Trials offline.

Each advisor has a competence profile over domains. On a query, the advisor returns the
CORRECT answer with probability = its competence in that query's domain, otherwise a
plausible wrong answer. Because the competence peaks differ by advisor, the response
matrix has non-nested error sets and the oracle gate (T-STAT-1) passes.

This stands in for `agent.bedrock.ConverseClient` during the dry-run: same interface
(converse -> text + token counts), no network. Deterministic per (advisor, query) via a
seeded hash, so no wall-clock/global-random (harness constraint) and fully replayable.

Two modes:
  * "complementary" (default) — each advisor best in one domain; oracle gap is large.
  * "nested" — one advisor (apollo) dominates everywhere; oracle gap ~0, demonstrating
    the kill/pivot path the design gates on.
"""
from __future__ import annotations

import hashlib

from delphi.agent.bedrock import ConverseClient, ConverseResult, GenerationParams
from delphi.data.schema import Query, TaskType

# competence[advisor][domain] = P(correct). Rows sum to a "personality", not to 1.
_COMPLEMENTARY = {
    "pythia":   {"prophecy": 0.95, "facts": 0.55, "reckoning": 0.45, "craft": 0.40, "lore": 0.70},
    "apollo":   {"prophecy": 0.70, "facts": 0.95, "reckoning": 0.60, "craft": 0.55, "lore": 0.80},
    "hermes":   {"prophecy": 0.50, "facts": 0.60, "reckoning": 0.65, "craft": 0.95, "lore": 0.55},
    "athena":   {"prophecy": 0.55, "facts": 0.65, "reckoning": 0.95, "craft": 0.70, "lore": 0.60},
    "calliope": {"prophecy": 0.75, "facts": 0.60, "reckoning": 0.40, "craft": 0.45, "lore": 0.95},
    "sibyl":    {"prophecy": 0.68, "facts": 0.68, "reckoning": 0.66, "craft": 0.64, "lore": 0.70},
}
# apollo dominates every domain -> nested error sets, tiny oracle gap. apollo is near-
# perfect so that on the rare query it misses, the others usually miss too, leaving the
# oracle little to gain over apollo alone -> gap collapses below the gate threshold.
_NESTED = {
    a: {d: (0.98 if a == "apollo" else 0.45) for d in _COMPLEMENTARY[a]}
    for a in _COMPLEMENTARY
}

_PROFILES = {"complementary": _COMPLEMENTARY, "nested": _NESTED}


def _unit(advisor: str, query_id: str) -> float:
    """Deterministic pseudo-uniform in [0,1) from (advisor, query) — replaces random()."""
    h = hashlib.sha256(f"{advisor}:{query_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _wrong_answer(q: Query, advisor: str) -> str:
    """A plausible-but-wrong answer, deterministic per (advisor, query)."""
    u = _unit(advisor + "_wrong", q.id)
    if q.task_type == TaskType.KNOWLEDGE_MC:
        letters = [c for c in "ABCD" if c != (q.answer or "A")]
        return letters[int(u * len(letters))]
    if q.task_type == TaskType.MATH:
        true = int(q.answer or "0")
        off = 1 + int(u * 5)
        return str(true + (off if u < 0.5 else -off))
    if q.task_type == TaskType.CODE:
        # a function that returns None -> fails the test battery
        fn = q.metadata.get("entry_point", "f")
        return f"def {fn}(*a, **k):\n    return None"
    if q.task_type == TaskType.OPEN_QA:
        return "a mistaken mutter from the smoke"
    return "The smoke is silent; no omen comes."  # open-ended "bad" prophecy


def _right_answer(q: Query) -> str:
    if q.task_type == TaskType.CODE:
        return _reference_code(q)
    if q.task_type == TaskType.OPEN_ENDED:
        return "When the bronze gates groan, seek the path you fear; there lies your fortune."
    return q.answer or ""


def _reference_code(q: Query) -> str:
    fn = q.metadata["entry_point"]
    bodies = {
        "laurels": f"def {fn}(n):\n    return sum(range(1, n + 1))",
        "is_omen": f"def {fn}(n):\n    return n % 2 == 0",
        "echo": f"def {fn}(s):\n    return s[::-1]",
        "tribute": f"def {fn}(xs):\n    return max(xs)",
        "chant": (f"def {fn}(n):\n    r = 1\n    for k in range(2, n + 1):\n"
                  f"        r *= k\n    return r"),
        "oracle_count": f"def {fn}(s):\n    return sum(c in 'aeiou' for c in s)",
        "omens": f"def {fn}(xs):\n    return [x for x in xs if x % 2 == 0]",
        "is_palindrome": f"def {fn}(s):\n    return s == s[::-1]",
        "pilgrims": f"def {fn}(xs):\n    return len(xs)",
        "tithe": f"def {fn}(n):\n    return n // 10",
        "honor": f"def {fn}(a, b):\n    return a if a > b else b",
        "runes": f"def {fn}(s):\n    return s.upper()",
    }
    return bodies[fn]


class SyntheticCouncil(ConverseClient):
    """Drop-in ConverseClient replacement backed by the competence profiles."""

    def __init__(self, mode: str = "complementary"):
        if mode not in _PROFILES:
            raise ValueError(f"mode must be one of {list(_PROFILES)}")
        self._profile = _PROFILES[mode]
        # map delphi.<advisor>-v1 -> advisor
        self.mode = mode

    @staticmethod
    def _advisor_of(model_id: str) -> str:
        # "delphi.pythia-v1" -> "pythia"
        return model_id.split(".", 1)[1].rsplit("-", 1)[0]

    def answer(self, advisor: str, q: Query) -> tuple[str, bool]:
        p = self._profile[advisor][q.domain]
        correct = _unit(advisor, q.id) < p
        text = _right_answer(q) if correct else _wrong_answer(q, advisor)
        return text, correct

    def converse(self, model_id: str, prompt: str, params: GenerationParams) -> ConverseResult:
        # The matrix builder passes the Query via a prompt registry; but to honour the
        # ConverseClient contract (prompt string only), the builder uses answer() directly.
        raise NotImplementedError(
            "SyntheticCouncil is driven via answer(advisor, query) by the matrix builder, "
            "not the string-only converse() path"
        )
