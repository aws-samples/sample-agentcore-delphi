# Phase 2 Router Training & Selection

Trains ≥3 router families on the training-split response matrix, selects the winner on **validation** by capture ratio (T-STAT-4) subject to the latency budget (T-RTR-4), emits a versioned artifact, and proves it reloads into the request-path `LearnedRouter`. Offline (design D5); the synthetic dry-run needs no AWS.

## Router families (pure numpy — fits the serverless-CPU story, D2)

All three predict **per-candidate quality** from a query embedding, then argmax. Comparable by construction, so capture ratio selects among them.

| Family | Idea | LLMRouter analogue | Inference cost |
|---|---|---|---|
| `linear` | ridge regressor per candidate on the quality label | mlprouter | trivial (one matmul) |
| `prototype` | learned prototype vector per candidate; score = base rate + similarity | mfrouter (matrix-factorization flavour) | trivial |
| `knn` | mean quality of the k nearest training queries (cosine) | graph/neighbourhood router | heavier (stores train set) |

Features: `HashingEncoder` (`router/features.py`) — signed word 1/2-gram feature hashing to a unit vector. Dependency-free, deterministic, expected to be fast on CPU (a design expectation, not measured on the target architecture), nothing to bake into the image beyond the code. Implements the `Embedder` interface, so it's swappable for an ONNX/Bedrock encoder later.

## Selection protocol (no leakage — Threat #6)

1. Featurize train prompts; fit every family on the **train** matrix.
2. For each family: compute **validation** capture ratio + P90 routing latency.
3. Winner = highest validation capture among families meeting the latency budget (150ms P90). If none pass, highest capture overall (and the latency miss is surfaced).
4. Test is scored **once**, at the end, with the already-selected router — never used to choose.

## Artifact (design D1 cold-start path)

`save_artifact` writes:
- `weights.npz` — the trained router's numpy arrays,
- `scalars.json` — non-array state (e.g. k),
- `manifest.json` — `Artifact` fields: family, trained_aliases, encoder, `artifact_hash`, `weights_uri`.

`load_learned_router(dir, pool)` reconstructs the deployable `LearnedRouter` — the exact object AgentCore pulls from S3 at cold start. Swap the local dir for an `s3://` prefix + download in Phase 3.

## Running

```bash
python scripts/run_phase2_synthetic.py                 # complementary pool
python scripts/run_phase2_synthetic.py --mode nested   # dominated pool
```

## What the dry-run shows (and a subtle, correct result)

**Complementary pool** (each advisor best in one domain):
```
family      capture  val_q   p90_ms  ok
*linear      0.849   92.45   ...     yes
 knn         0.849   92.45   ...     yes
 prototype   0.370   75.05   ...     yes
HELD-OUT TEST: router 90.51 vs best-single 73.48, oracle 97.78 -> CAPTURE 0.779 (H3 PASS)
```
The `p90_ms` column is elided above: it is in-process wall clock on whichever machine ran the script, excluding the container, the network and the artifact load, so it is not a latency claim and its values are not quoted.
On this synthetic pool the router's held-out score exceeds the best single advisor's because it routes per query; that illustrates the method and is not evidence about model quality. Linear and kNN tie because the synthetic signal (domain keywords) is near-linearly separable by hashed n-grams.

**Nested pool** (the synthetic H2 falsification fixture, in which apollo dominates everywhere): the router learns to always pick apollo — **capture 1.000 but router quality == best-single quality**. This is the important lesson baked into the harness: capture ratio (H3, learnability) and quality-dominance (H2) are *different claims*. A router can be perfectly learnable and still not beat any single model, because the oracle gap is zero. **The oracle gate (Phase 1), not the router, is what protects H2** — exactly as the design argues.

## Swapping in real training

Only the matrix source changes: build train/val/test matrices from real Bedrock generations (Phase 1 real run) instead of the synthetic council, then call `train_and_select` unchanged. To add a heavier family (a real BERT classifier via torch), implement the `TrainedRouter` interface in `train/routers.py` and register it in `FAMILIES`; selection and artifact code are family-agnostic.
