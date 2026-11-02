"""Real answer source for the response matrix: real Bedrock models through Converse.

This is the swap the synthetic pipeline was built for (build_matrix module docstring): the
SyntheticCouncil is replaced by real models, and everything downstream - native scoring, the
oracle gate, router training - is unchanged. It lives beside build_matrix rather than in it
so the synthetic path and its pinned figures are untouched, and it lives in scripts/, outside
the package, because the package keeps agent/bedrock.py as its only Converse call site
(T-FP-U2-3). The judge's request differs (no temperature), so it cannot reuse that seam
unchanged. Differences from build_matrix:

  * Cost uses Bedrock's reported usage block per call, not the 600/350-token model.
  * Judge calls are paid calls too, so their cost counts against the same cap.
  * CODE queries are refused outright. Their scorer executes model output outside a
    sandbox (scoring module docstring), and this path has no opt-in at all.
  * Every answered cell is appended to a JSONL cache as soon as it is paid for, so an
    interrupted or repeated run never pays twice and every raw answer stays inspectable.
    The cap is enforced on the cache's cumulative spend: it bounds the whole run.

Calls run on a small thread pool. A throttled call bills no tokens, so ThrottlingException
is retried here with backoff; the client itself stays single-attempt (agent.bedrock).
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import numpy as np
import yaml

from delphi.agent.bedrock import BedrockConverseClient, ConverseResult, GenerationParams
from delphi.config import Pool, Price
from delphi.data.schema import Dataset, Query, TaskType
from delphi.eval.build_matrix import BudgetExceeded
from delphi.eval.cost import Usage
from delphi.eval.matrix import ResponseMatrix
from delphi.eval.scoring import MODEL_OUTPUT, Judge, score_native

_THROTTLE_TRIES = 6


@dataclass(frozen=True)
class JudgeSpec:
    alias: str
    id: str
    price: Price


def load_judge(path: pathlib.Path) -> JudgeSpec:
    """The judge block of a pool file, with the price load_pool does not read."""
    j = yaml.safe_load(path.read_text())["judge"]
    return JudgeSpec(j["alias"], j["id"], Price(j["price_per_1k"]["input"],
                                                j["price_per_1k"]["output"]))


def _throttled(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in ("ThrottlingException", "ServiceUnavailableException",
                    "ModelNotReadyException")


def _with_backoff(fn):
    for attempt in range(_THROTTLE_TRIES):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is a throttle
            if not _throttled(exc) or attempt == _THROTTLE_TRIES - 1:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def provider_default_aliases(path: pathlib.Path) -> frozenset[str]:
    """Arms whose model rejects `temperature` and runs at its provider default (v3: Claude)."""
    cands = yaml.safe_load(path.read_text())["candidates"]
    return frozenset(c["alias"] for c in cands if c.get("sampling") == "provider-default")


class RealCouncil:
    """Answers a query with a real candidate model. Same generation params for every arm,
    except the named `provider_default` arms, which get maxTokens only (their model rejects
    temperature). Those calls join every text block, so a leading reasoning block is skipped.
    The other arms go through BedrockConverseClient unchanged, as in v1 and v2."""

    def __init__(self, client: BedrockConverseClient, pool: Pool,
                 params: GenerationParams | None = None,
                 provider_default: frozenset[str] = frozenset()):
        self._client = client
        self._pool = pool
        self._params = params or GenerationParams()
        self._provider_default = provider_default

    def answer(self, alias: str, q: Query) -> ConverseResult:
        model_id = self._pool.by_alias(alias).id
        if alias not in self._provider_default:
            return _with_backoff(lambda: self._client.converse(model_id, q.prompt, self._params))

        def call():
            resp = self._client._ensure_client().converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": q.prompt}]}],
                inferenceConfig={"maxTokens": self._params.max_tokens},
            )
            text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
            u = resp["usage"]
            return ConverseResult(text, u["inputTokens"], u["outputTokens"], model_id)

        return _with_backoff(call)


_JUDGE_PROMPT = """You are grading one answer in a model evaluation.

The request was:
<request>
{request}
</request>

The answer was:
<answer>
{answer}
</answer>

Score the answer from 0 to 10 on how well it does what the request asks: a prophecy that is
on the stated topic, cryptic in style, and still carries real, usable counsel. 0 is a refusal,
an off-topic reply or no prophecy; 5 is adequate but generic; 10 is excellent. Length is not
merit. Reply with the integer score only."""

#: v2 adds one clause for its form constraints (pre-registration, results/).
JUDGE_PROMPT_V2 = _JUDGE_PROMPT.replace(
    "and still carries real, usable counsel.",
    "and still carries real, usable counsel, and meets any form the request specifies (line\n"
    "count, length, rhyme, opening word, required image, riddle).")


class ConverseJudge(Judge):
    """An out-of-pool judge model, scoring OPEN_ENDED answers 0-10, reported as 0-100.

    GPT-5.4 rejects the temperature field, so the request carries maxTokens only; the
    client is the same single-attempt bedrock-runtime client the candidates use.
    """

    def __init__(self, client: BedrockConverseClient, spec: JudgeSpec, max_tokens: int = 2048,
                 prompt: str = _JUDGE_PROMPT):
        self._prompt = prompt
        self._client = client
        self.spec = spec
        self._max_tokens = max_tokens

    def judge(self, prediction: str, query: Query) -> tuple[float, ConverseResult, str]:
        prompt = self._prompt.format(request=query.prompt, answer=prediction)

        def call():
            resp = self._client._ensure_client().converse(
                modelId=self.spec.id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": self._max_tokens},
            )
            text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
            u = resp["usage"]
            return ConverseResult(text, u["inputTokens"], u["outputTokens"], self.spec.id)

        res = _with_backoff(call)
        m = re.search(r"\b(10|[0-9])\b", res.text)
        if m is None:
            raise ValueError(f"judge reply has no 0-10 score for query {query.id}: {res.text!r}")
        return float(m.group(1)) * 10.0, res, res.text

    def score(self, prediction: str, query: Query) -> float:
        return self.judge(prediction, query)[0]


_STOP = {"the", "a", "an", "of", "from", "to", "who", "all"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _mc_lenient(text: str, q: Query) -> float:
    """The choice the answer states: a leading letter, "answer is X", the first "X)", or the
    one option whose text it names. Upper-case letters only, so the article "a" never counts."""
    options = dict(re.findall(r"^([A-D])\) (.+)$", q.prompt, flags=re.M))
    pick = None
    for pat in (r"^\W*([A-D])(?:\)|\.|:|\s*$)", r"answer is\W*([A-D])\b", r"\b([A-D])\)"):
        m = re.search(pat, text.strip())
        if m:
            pick = m.group(1)
            break
    if pick is None:
        named = [k for k, v in options.items() if re.search(rf"\b{re.escape(v)}\b", text, re.I)]
        pick = named[0] if len(named) == 1 else None
    return 100.0 if pick == (q.answer or "") else 0.0


def _math_lenient(text: str, q: Query) -> float:
    """The answer's final number: the last one after "answer is", else the last overall."""
    t = text.replace(",", "")
    found = re.findall(r"answer is\D{0,20}?(-?\d+)", t, flags=re.I)
    if found:
        return 100.0 if found[-1] == (q.answer or "").strip() else 0.0
    nums = re.findall(r"-?\d+", t)
    return 100.0 if nums and nums[-1] == (q.answer or "").strip() else 0.0


def _lore_lenient(text: str, q: Query) -> float:
    """Recall of the reference's content words, punctuation ignored, so a correct sentence
    is not penalised for its length."""
    gold = [w for w in _words(q.answer or "") if w not in _STOP] or _words(q.answer or "")
    have = set(_words(text))
    return 100.0 * sum(w in have for w in gold) / len(gold)


def lenient_score(text: str, q: Query) -> float:
    """A second reading of the same answer for verbose real-model output. The strict score
    (score_native) is the pipeline's own and stays the headline-comparable figure."""
    if q.task_type == TaskType.KNOWLEDGE_MC:
        return _mc_lenient(text, q)
    if q.task_type == TaskType.MATH:
        return _math_lenient(text, q)
    if q.task_type == TaskType.OPEN_QA:
        return _lore_lenient(text, q)
    raise ValueError(f"{q.task_type} has no lenient scorer")


def cell_score(rec: dict, q: Query, scoring: str) -> float:
    """strict: the cached pipeline score. lenient: re-read from the cached answer (judge
    cells keep the judge's score under both)."""
    if scoring == "strict" or rec["scorer"] == "judge":
        return rec["score"]
    if scoring == "lenient":
        return lenient_score(rec["answer"], q)
    raise ValueError(f"scoring must be strict or lenient, got {scoring!r}")


@dataclass
class RealBuild:
    matrix: ResponseMatrix
    cells: dict[tuple[str, str], dict]   # (query_id, alias) -> cached record
    new_calls: int
    run_spend: float                     # cumulative across the cache, judge included

    def usages(self, alias: str | None = None) -> list[Usage]:
        """Candidate-call usages (judge excluded), in matrix row order."""
        out = []
        for qid in self.matrix.queries:
            for a in self.matrix.aliases:
                if alias is None or a == alias:
                    c = self.cells[(qid, a)]
                    out.append(Usage(a, c["input_tokens"], c["output_tokens"]))
        return out


class AnswerCache:
    """Append-only JSONL of paid cells; the source of truth for the run's spend."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        self.cells: dict[tuple[str, str], dict] = {}
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.cells[(rec["query_id"], rec["alias"])] = rec
        self._lock = threading.Lock()

    @property
    def spend(self) -> float:
        # under the lock: worker threads add cells while the submit loop checks the cap
        with self._lock:
            cells = list(self.cells.values())
        return sum(c["cost_usd"] + c.get("judge_cost_usd", 0.0) for c in cells)

    def add(self, rec: dict) -> None:
        with self._lock:
            self.cells[(rec["query_id"], rec["alias"])] = rec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(rec) + "\n")


REAL_DOMAINS = ("prophecy", "facts", "reckoning", "lore")


def trials_without_craft(n_per_domain: int, seed: int):
    """The synthetic run's dataset and split, minus craft, before any call is made.

    The same generator and seed produce the same queries; dropping craft after generation
    (not before) keeps every other query identical, and the hash split assigns each one to
    the same partition the synthetic run did.
    """
    import warnings

    from delphi.data import synthetic
    from delphi.data.split import split_dataset

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        full = synthetic.generate(n_per_domain=n_per_domain, seed=seed)
    ds = Dataset(f"{full.name}-nocraft",
                 tuple(q for q in full.queries if q.domain in REAL_DOMAINS))
    return ds, split_dataset(ds, seed=seed)


#: The v1 answer cache that run_phase1_real.py writes. Raw model answers are not published
#: with the sample, so this lives under the git-ignored data/ directory.
V1_CACHE = pathlib.Path("data/real_run/cells.jsonl")


def load_trials(name: str):
    """(dataset, split, output dir, judge prompt) for v1 or the pre-registered v2."""
    if name == "v1":
        ds, split = trials_without_craft(80, 20260714)
        return ds, split, pathlib.Path("data/real_run"), _JUDGE_PROMPT
    if name in ("v2", "v3"):
        import warnings

        from delphi.data import synthetic
        from delphi.data.split import split_dataset
        from trials_v2 import generate_v2

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            v1_prompts = synthetic.generate(n_per_domain=80, seed=20260714).prompts()
        ds, _kept = generate_v2(n_per_domain=250, seed=20261028, exclude=v1_prompts)
        split = split_dataset(ds, train=0.5, validation=0.2, seed=20261028)
        return ds, split, pathlib.Path(f"data/real_run_{name}"), JUDGE_PROMPT_V2
    if name == "v4":
        # Confirmation set: fresh questions from the v2 generator, disjoint from v1 and v2,
        # 130 per domain (lore's remaining capacity). Every question is test.
        import warnings

        from delphi.data import synthetic
        from delphi.data.schema import Split
        from trials_v2 import generate_v2

        v2, _, _, _ = load_trials("v2")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            v1_prompts = synthetic.generate(n_per_domain=80, seed=20260714).prompts()
        ds, kept = generate_v2(n_per_domain=130, seed=20261029,
                               exclude=v1_prompts + v2.prompts())
        if set(kept.values()) != {130}:
            raise RuntimeError(f"v4 generator shortfall: {kept}")
        empty = Dataset(f"{ds.name}:none", ())
        return ds, Split(empty, empty, ds), pathlib.Path("data/real_run_v4"), JUDGE_PROMPT_V2
    raise ValueError(f"unknown trials {name!r}")


def prior_spend(name: str) -> float:
    """Spend already made outside this dataset's cache, which the run-wide cap also covers.
    The v3 cache starts as a copy of v2's cells, so v3 counts only v1 as prior."""
    if name == "v4":   # v1 + the v3 cache (which holds v2's cells too)
        return AnswerCache(V1_CACHE).spend + AnswerCache(pathlib.Path(
            "data/real_run_v3/cells.jsonl")).spend
    return AnswerCache(V1_CACHE).spend if name in ("v2", "v3") else 0.0


def pool_config(name: str) -> pathlib.Path:
    return pathlib.Path("results/models_v3pool.yaml" if name in ("v3", "v4")
                        else "results/models_realrun.yaml")


def build_real_matrix(ds: Dataset, pool: Pool, council: RealCouncil, judge: ConverseJudge,
                      cache: AnswerCache, *, cost_cap_usd: float, workers: int = 6,
                      scoring: str = "strict") -> RealBuild:
    """M[query][candidate] from real answers, stopping at the run's dollar cap.

    The cap is checked before each call is submitted, on the cache's cumulative spend, so
    the overshoot is bounded by the calls already in flight (at most `workers` cells).
    """
    code = [q.id for q in ds.queries if q.task_type == TaskType.CODE]
    if code:
        raise ValueError(f"refusing {len(code)} CODE queries: the real path never executes "
                         "model output; filter domain 'craft' out first")
    aliases = pool.aliases
    todo = [(q, a) for q in ds.queries for a in aliases if (q.id, a) not in cache.cells]
    stop = threading.Event()

    def cell(q: Query, alias: str) -> None:
        if stop.is_set():
            return
        res = council.answer(alias, q)
        cost = pool.by_alias(alias).price.cost(res.input_tokens, res.output_tokens)
        rec = {"query_id": q.id, "domain": q.domain, "task_type": q.task_type.value,
               "alias": alias, "model_id": res.model_id, "answer": res.text,
               "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
               "cost_usd": cost}
        if q.task_type == TaskType.OPEN_ENDED:
            score, jr, raw = judge.judge(res.text, q)
            rec.update(score=score, scorer="judge", judge_id=jr.model_id, judge_raw=raw,
                       judge_input_tokens=jr.input_tokens,
                       judge_output_tokens=jr.output_tokens,
                       judge_cost_usd=judge.spec.price.cost(jr.input_tokens, jr.output_tokens))
        else:
            rec.update(score=score_native(res.text, q, provenance=MODEL_OUTPUT),
                       scorer="native")
        cache.add(rec)

    new_calls = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = []
        for q, a in todo:
            if cache.spend > cost_cap_usd:
                stop.set()
                break
            futures.append(ex.submit(cell, q, a))
            new_calls += 1
        for f in as_completed(futures):
            f.result()
    if cache.spend > cost_cap_usd:
        raise BudgetExceeded(f"real run reached cap ${cost_cap_usd:.2f} at "
                             f"${cache.spend:.4f}; answers so far are cached in {cache.path}")

    scores = np.array([[cell_score(cache.cells[(q.id, a)], q, scoring) for a in aliases]
                       for q in ds.queries])
    matrix = ResponseMatrix(tuple(q.id for q in ds.queries), aliases, scores)
    return RealBuild(matrix, cache.cells, new_calls, cache.spend)
