"""Response-matrix builder (design §4 Phase 1) — the expensive step, plus its cost cap.

For every (query, candidate) pair: get the candidate's answer, score it (native or judge),
record the quality and the token cost. Emits a ResponseMatrix plus per-query Usage records
for cost accounting (T-INF-2). Enforces the dollar cap from the budget model.

Two answer sources, behind one interface:
  * SyntheticCouncil.answer(advisor, query) — offline dry-run, no Bedrock.
  * a real Converse path — Phase 1 proper (not wired here; needs creds).

Execution safety (U4, BR4.1, BR4.4, NFR11.2). The answers' provenance is `synthetic` only when
`type(council) is SyntheticCouncil` - an identity check that a subclass or a look-alike fails
- and `model_output` otherwise. The opt-in is resolved ONCE, before anything else, and held
for the whole build. A model_output build that is not opted in and holds any CODE query
raises UnsandboxedExecutionRefused before the first `council.answer`: on the real path every
answer is a paid call. One CodeRunner serves the build and is closed in a `finally`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from delphi.config import Pool
from delphi.data.schema import Dataset, Query, TaskType
from delphi.data.synthetic_provider import SyntheticCouncil
from delphi.eval.cost import Usage
from delphi.eval.matrix import ResponseMatrix
from delphi.eval.runner import CodeRunner
from delphi.eval.scoring import (
    DEFAULT_TIMEOUT_S,
    MODEL_OUTPUT,
    SYNTHETIC,
    Judge,
    UnsandboxedExecutionRefused,
    resolve_opt_in,
    score_native,
)


class BudgetExceeded(RuntimeError):
    """Raised when projected/actual spend crosses the hard cap (review point #1)."""


@dataclass
class BuildResult:
    matrix: ResponseMatrix
    usages: list[Usage]           # flat list across all (query, candidate) pairs
    total_cost: float
    judged_pairs: int
    native_pairs: int


# rough token model for synthetic runs — real runs use Bedrock's reported usage
_APPROX_INPUT_TOKENS = 600
_APPROX_OUTPUT_TOKENS = 350


def answer_provenance(council) -> str:
    """synthetic only for a council whose type IS SyntheticCouncil (D-U4-3, BR4.1)."""
    return SYNTHETIC if type(council) is SyntheticCouncil else MODEL_OUTPUT


def build_response_matrix(
    ds: Dataset,
    pool: Pool,
    council,                       # SyntheticCouncil (has .answer); duck-typed
    judge: Judge,
    *,
    cost_cap_usd: float | None = None,
    allow_exec: bool | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    input_tokens: int = _APPROX_INPUT_TOKENS,
    output_tokens: int = _APPROX_OUTPUT_TOKENS,
) -> BuildResult:
    """Build M[query][candidate] with quality scores, tracking cost against the cap.

    Refuses before it spends (BR4.4): see the module docstring. `allow_exec` and `timeout_s`
    reach every CODE cell through the build's one runner.
    """
    opt = resolve_opt_in(allow_exec)  # once, before anything else (SD-U4-10)
    provenance = answer_provenance(council)
    if provenance == MODEL_OUTPUT and not opt.opted_in and any(
            q.task_type == TaskType.CODE for q in ds.queries):
        raise UnsandboxedExecutionRefused(None)
    runner = (CodeRunner(provenance, opt, timeout_s=timeout_s)
              if provenance == SYNTHETIC or opt.opted_in else None)
    try:
        return _build(ds, pool, council, judge, provenance, runner, cost_cap_usd,
                      input_tokens, output_tokens)
    finally:
        if runner is not None:
            runner.close()


def _build(ds, pool, council, judge, provenance, runner, cost_cap_usd, input_tokens,
           output_tokens) -> BuildResult:
    aliases = pool.aliases
    n = len(ds)
    scores = np.zeros((n, len(aliases)))
    usages: list[Usage] = []
    total_cost = 0.0
    judged = 0
    native = 0

    for i, q in enumerate(ds.queries):
        for j, alias in enumerate(aliases):
            cand = pool.by_alias(alias)
            advisor = alias  # synthetic: alias == advisor name
            text, _correct = council.answer(advisor, q)

            if q.task_type == TaskType.OPEN_ENDED:
                scores[i, j] = judge.score(text, q)
                judged += 1
            else:
                scores[i, j] = score_native(text, q, provenance=provenance, runner=runner)
                native += 1

            usage = Usage(alias, input_tokens, output_tokens)
            usages.append(usage)
            total_cost += cand.price.cost(input_tokens, output_tokens)
            if cost_cap_usd is not None and total_cost > cost_cap_usd:
                raise BudgetExceeded(
                    f"response-matrix build exceeded cap ${cost_cap_usd:.2f} at "
                    f"${total_cost:.2f} (query {i + 1}/{n}); raise the cap or shrink the set"
                )

    matrix = ResponseMatrix(
        queries=tuple(q.id for q in ds.queries),
        aliases=aliases,
        scores=scores,
    )
    return BuildResult(matrix, usages, total_cost, judged, native)
