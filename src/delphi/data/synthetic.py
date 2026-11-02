"""The Delphi Trials — a fun, fully-offline synthetic dataset.

Petitioners climb Mount Parnassus to consult the Oracle. Each poses a trial drawn from
one of five domains. The trials have REAL ground truth (computed here), so the native
scorers (exact-match / pass@1 / F1) actually work — this is a genuine end-to-end
exercise of the pipeline, not mocked scores.

The point of the theme is that each domain has a natural "best advisor" (see
synthetic_provider), giving the response matrix non-nested error sets so the oracle gate
passes. Deterministic: all randomness is seeded, and no wall-clock is used (the harness
forbids Date.now/random-without-seed), so runs are reproducible.
"""
from __future__ import annotations

import random

from delphi.data.schema import Dataset, Query, TaskType

# --- flavour text pools -----------------------------------------------------
_PETITIONERS = [
    "a weary shepherd", "a Spartan general", "an Athenian merchant", "a lovesick poet",
    "a exiled prince", "a rival oracle", "a curious child", "a doubting philosopher",
]
_GREEK_NAMES = [
    "Kleon", "Xanthe", "Theron", "Iola", "Damon", "Phaedra", "Nikias", "Berenike",
]


def _pick(rng: random.Random, seq):
    return seq[rng.randrange(len(seq))]


# --- domain: PROPHECY (open-ended, judged) ----------------------------------
def _prophecy(rng: random.Random, i: int) -> Query:
    name = _pick(rng, _GREEK_NAMES)
    who = _pick(rng, _PETITIONERS)
    topic = _pick(rng, ["a coming war", "a long voyage", "an uncertain marriage",
                        "a failing harvest", "a treacherous ally", "a hidden inheritance",
                        "a rival's ambition", "a cursed bloodline", "a lost brother",
                        "an omen of eagles", "a broken oath", "a famine to come"])
    prompt = (f"{name}, {who}, kneels before the Oracle and asks for a prophecy "
              f"concerning {topic}. Deliver a suitably cryptic yet wise prophecy.")
    return Query(Query.make_id(prompt), prompt, TaskType.OPEN_ENDED, domain="prophecy")


# --- domain: FACTS (knowledge_mc, exact-match on a letter) -------------------
# (question, correct-answer, distractor pool). We shuffle choices per-instance so the
# same base fact yields different prompts, giving a larger space of distinct items.
_FACTS = [
    ("Which titan holds up the sky?", "Atlas", ["Cronus", "Prometheus", "Helios", "Oceanus"]),
    ("Who is the messenger of the gods?", "Hermes", ["Ares", "Apollo", "Pan", "Dionysus"]),
    ("What river must the dead cross?", "Styx", ["Nile", "Tiber", "Achelous", "Lethe"]),
    ("Which goddess sprang from Zeus's head?", "Athena",
     ["Hera", "Aphrodite", "Demeter", "Artemis"]),
    ("Who flew too close to the sun?", "Icarus",
     ["Daedalus", "Bellerophon", "Perseus", "Phaethon"]),
    ("Who guards the underworld's gate?", "Cerberus", ["Chimera", "Hydra", "Argus", "Typhon"]),
    ("Who opened a jar of all evils?", "Pandora", ["Helen", "Medea", "Circe", "Cassandra"]),
    ("Which hero slew Medusa?", "Perseus", ["Theseus", "Jason", "Achilles", "Hector"]),
    ("What turned to gold at a king's touch?", "everything",
     ["gold only", "nothing", "wine", "bread"]),
    ("Who wove and unwove a burial shroud?", "Penelope",
     ["Andromache", "Clytemnestra", "Helen", "Electra"]),
    ("Which musician charmed Hades?", "Orpheus", ["Amphion", "Marsyas", "Linus", "Thamyris"]),
    ("Who cut the Gordian knot?", "Alexander", ["Midas", "Croesus", "Leonidas", "Pericles"]),
]


def _facts(rng: random.Random, i: int) -> Query:
    q, correct, distractors = _pick(rng, _FACTS)
    picks = [correct] + [distractors[k] for k in rng.sample(range(len(distractors)), 3)]
    rng.shuffle(picks)
    letters = ["A", "B", "C", "D"]
    ans = letters[picks.index(correct)]
    body = "\n".join(f"{L}) {v}" for L, v in zip(letters, picks))
    prompt = f"Answer with the single letter.\n{q}\n{body}"
    return Query(Query.make_id(prompt), prompt, TaskType.KNOWLEDGE_MC,
                 domain="facts", answer=ans)


# --- domain: RECKONING (math, exact-match on a number) ----------------------
def _reckoning(rng: random.Random, i: int) -> Query:
    a, b = rng.randint(2, 40), rng.randint(2, 40)
    kind = rng.randrange(3)
    if kind == 0:
        prompt = (f"A merchant stacks {a} amphorae, then {b} more. How many amphorae in all? "
                  f"Answer with a number.")
        ans = a + b
    elif kind == 1:
        prompt = (f"An army of {a} rows has {b} soldiers each. How many soldiers total? "
                  f"Answer with a number.")
        ans = a * b
    else:
        big = a * b
        prompt = (f"{big} drachmae are split evenly among {a} heirs. How many each? "
                  f"Answer with a number.")
        ans = b
    return Query(Query.make_id(prompt), prompt, TaskType.MATH, domain="reckoning", answer=str(ans))


# --- domain: CRAFT (code, pass@1 via execution) -----------------------------
_CODE_TASKS = [
    ("Write a Python function `laurels(n)` returning the sum of 1..n.",
     "laurels", [((5,), 15), ((1,), 1), ((10,), 55)]),
    ("Write a Python function `is_omen(n)` returning True if n is even.",
     "is_omen", [((4,), True), ((7,), False), ((0,), True)]),
    ("Write a Python function `echo(s)` returning the string reversed.",
     "echo", [(("delphi",), "ihpled"), (("a",), "a")]),
    ("Write a Python function `tribute(xs)` returning the max of a list.",
     "tribute", [(([3, 9, 2],), 9), (([-1, -5],), -1)]),
    ("Write a Python function `chant(n)` returning n factorial.",
     "chant", [((0,), 1), ((4,), 24), ((5,), 120)]),
    ("Write a Python function `oracle_count(s)` returning the number of vowels in s.",
     "oracle_count", [(("delphi",), 2), (("aeiou",), 5), (("xyz",), 0)]),
    ("Write a Python function `omens(xs)` returning only the even numbers of a list.",
     "omens", [(([1, 2, 3, 4],), [2, 4]), (([5, 7],), [])]),
    ("Write a Python function `is_palindrome(s)` returning True if s reads the same backwards.",
     "is_palindrome", [(("anna",), True), (("delphi",), False)]),
    ("Write a Python function `pilgrims(xs)` returning the count of items.",
     "pilgrims", [(([1, 2, 3],), 3), (([],), 0)]),
    ("Write a Python function `tithe(n)` returning 10 percent of n as an integer.",
     "tithe", [((100,), 10), ((55,), 5)]),
    ("Write a Python function `honor(a, b)` returning the greater of two numbers.",
     "honor", [((3, 9), 9), ((-1, -4), -1)]),
    ("Write a Python function `runes(s)` returning s in upper case.",
     "runes", [(("delphi",), "DELPHI"), (("A",), "A")]),
]


def _craft(rng: random.Random, i: int) -> Query:
    desc, fn, cases = _pick(rng, _CODE_TASKS)
    scribe = _pick(rng, _GREEK_NAMES)
    prompt = (f"The scribe {scribe} needs an incantation. {desc} "
              f"Return only the function definition.")
    # ground truth is the test battery, stashed in metadata for the pass@1 scorer
    return Query(Query.make_id(prompt), prompt, TaskType.CODE, domain="craft",
                 answer=fn, metadata={"entry_point": fn, "tests": cases})


# --- domain: LORE (open_qa, F1 against a reference) -------------------------
_LORE = [
    ("What is the Oracle of Delphi also called?", "the Pythia"),
    ("On which mountain does the Oracle sit?", "Mount Parnassus"),
    ("What vapour was said to inspire the priestess?", "pneuma from a chasm"),
    ("Which god's temple houses the Oracle?", "the temple of Apollo"),
    ("What was inscribed at Delphi's forecourt?", "know thyself"),
    ("What stone marked Delphi as the center of the world?", "the omphalos"),
    ("Which python did Apollo slay at Delphi?", "the serpent Python"),
    ("What festival was held at Delphi every four years?", "the Pythian Games"),
    ("Who were the priests that interpreted the Oracle?", "the prophetai"),
    ("What tripod did the priestess sit upon?", "the sacred bronze tripod"),
    ("Which king famously misread a prophecy about an empire?", "Croesus of Lydia"),
    ("What season did the Oracle fall silent each year?", "the three winter months"),
    ("What laurel plant was sacred to Apollo at Delphi?", "the bay laurel"),
    ("Whom did the maxim 'nothing in excess' advise?", "all who entered"),
]


def _lore(rng: random.Random, i: int) -> Query:
    q, ans = _pick(rng, _LORE)
    asker = _pick(rng, _GREEK_NAMES)
    prompt = f"{asker} asks, and you answer briefly: {q}"
    return Query(Query.make_id(prompt), prompt, TaskType.OPEN_QA, domain="lore", answer=ans)


_GENERATORS = {
    "prophecy": _prophecy,
    "facts": _facts,
    "reckoning": _reckoning,
    "craft": _craft,
    "lore": _lore,
}


def generate(n_per_domain: int = 60, seed: int = 20260714) -> Dataset:
    """Build the Delphi Trials dataset with roughly balanced domains.

    Uses a near-duplicate filter so the dataset has NO internal near-dups; therefore any
    downstream train/val/test split is contamination-clean by construction (T-STAT-6).
    Deterministic given (n_per_domain, seed). If a domain's finite template space can't
    supply n_per_domain distinct items, we log a warning and take what's available
    rather than silently truncating.

    Total size ~= 5 * n_per_domain (subject to per-domain template capacity).
    """
    from delphi.eval.contamination import NearDupFilter

    rng = random.Random(seed)  # noqa: S311 - synthetic data generation, not cryptographic
    queries: list[Query] = []
    shortfalls: dict[str, int] = {}
    for domain, gen in _GENERATORS.items():
        ndf = NearDupFilter()
        kept = 0
        attempts = 0
        max_attempts = n_per_domain * 200
        while kept < n_per_domain and attempts < max_attempts:
            q = gen(rng, kept)
            attempts += 1
            if ndf.accept(q.prompt):
                queries.append(q)
                kept += 1
        if kept < n_per_domain:
            shortfalls[domain] = kept
    if shortfalls:
        import warnings

        warnings.warn(
            "synthetic generator hit template capacity (no silent truncation): "
            + ", ".join(f"{d}={k}/{n_per_domain}" for d, k in shortfalls.items()),
            stacklevel=2,
        )
    rng.shuffle(queries)
    return Dataset(name=f"delphi-trials-{n_per_domain}x5-seed{seed}", queries=tuple(queries))
