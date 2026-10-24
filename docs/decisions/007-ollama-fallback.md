# ADR-007 — Ollama: Embeddings Only (Chat Removed)

**Status:** Superseded by [ADR-020](020-hosted-embeddings-rerank.md) (2026-10-24) — kept as history  
**Date:** 2026-10-21

## Context

Ollama was originally used for both embeddings (`nomic-embed-text`) and as an optional local chat model. See [ADR-000](000-constraints.md) for RAM constraints.

## Decision

Ollama is **retained for embeddings only** (`nomic-embed-text`, via the `NomicEmbeddings` wrapper in `app/retrieval.py`). The local chat model (`qwen3.5:2b`) and the warmup endpoint have been removed.

Groq is the only answer provider (it has no embeddings API). There is no provider selector in the UI.

**Task prefixes:** `nomic-embed-text` is trained with task prefixes, so documents are embedded as `search_document: …` and questions as `search_query: …`. Without them query and document vectors are misaligned and recall drops. Adding the prefixes bumped `CHUNKER_VERSION` to `v4`, which re-embeds everything once.

**Why embeddings stay local:**
- `nomic-embed-text` is only ~0.5 GB and runs on CPU
- Documents are embedded once at ingest time (only new or changed files on later starts); each question needs one small query embedding
- Keeping embeddings local means no query text leaves the system at retrieval time — only the final prompt (masked) goes to Groq

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Keep Ollama chat as a fallback | qwen3.5:2b gives noticeably worse answers than Groq's 70B+ models. Adds 2–3 GB RAM cost. |
| Use a cloud embedding API | Every query term would go to an external service. Local embeddings are free, fast, and private. |
| Keep the provider selector in the UI | It confuses borrowers ("what is Groq?") and raises the "are you sending data to multiple US services?" question without benefit. |

## Consequences

- `CHAT_MODEL`, `OLLAMA_WARMUP_TIMEOUT`, `HF_*` config vars are removed.
- If Ollama is not running at startup, the backend stops with a clear "Ollama is not running" message (documented in the README). If it stops later, policy questions return an explicit "could not search the policy documents" answer; EMI and application-status answers still work.

## Production path

Hosted embedding endpoint (Azure OpenAI `text-embedding-3-small` in the India region, or a private Ollama cluster) to eliminate the Ollama startup dependency.
