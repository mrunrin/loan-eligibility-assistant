# ADR-009 — CrossEncoder Reranker Choice

**Status:** Superseded by [ADR-020](020-hosted-embeddings-rerank.md) (2026-10-24) — kept as history  
**Date:** 2026-10-21

## Context

After hybrid retrieval (BM25 + dense vector), the top-N candidates are reranked with a cross-encoder before being passed to the LLM. The choice of model affects RAM, latency and retrieval quality. See [ADR-000](000-constraints.md): only ~7 GB RAM usable.

## Decision

Use `cross-encoder/ms-marco-MiniLM-L-6-v2`.

| Metric | bge-reranker-base (old) | ms-marco-MiniLM-L-6-v2 (current) |
|---|---|---|
| RAM on CPU | ~1.1 GB | ~90 MB |
| Latency (40 pairs, laptop CPU) | ~15 s | ~1–3 s |
| Model size on disk | ~1.1 GB | ~90 MB |
| Score range | 0–1 (sigmoid) | raw logits (~-10 to +10) |

`RERANK_MIN_SCORE` was updated from `0.02` to `-5.0` to match ms-marco's logit scale. The threshold is also settable via the `RERANK_MIN_SCORE` environment variable without a code change.

**Verification:** After deploying, run `python eval/retrieval_eval.py` and confirm recall@3 ≥ 0.80 (the CI gate threshold; k = `FINAL_EVIDENCE_K` = 3).

Reranking runs in a worker thread (the whole retrieval step is called through `run_in_threadpool`), so CPU-bound `predict()` does not block the event loop. Each rerank is a `rerank` span in Langfuse with candidate counts and final scores. `RERANKER_ENABLED=false` skips it (top candidates are used unranked).

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| BAAI/bge-reranker-base | 1.1 GB RAM and 15 s latency — takes up too much of the 7 GB budget |
| BAAI/bge-reranker-large | ~2.5 GB RAM — exceeds budget |
| No reranker | BM25-only ranking misses semantic relevance; dense-only misses exact clause terms |
| GPU acceleration | No GPU available on the demo laptop |

## Consequences

- **Threshold re-tuning required:** ms-marco outputs logits, not 0–1 probabilities. Any environment that was overriding `RERANK_MIN_SCORE` must update the value.
- **Download on first run:** Hugging Face downloads the model (~90 MB) on first startup if not cached (cold-start step 2). In Docker it is cached in the `hf_cache` volume, so later starts reuse it.

## Production path

A GPU-hosted reranker endpoint (Cohere Rerank API, or a self-hosted bge-large on GPU) with sub-100 ms latency.
