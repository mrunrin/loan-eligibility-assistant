# ADR-020 — Hosted APIs for Embeddings and Reranking (Jina + Cohere)

**Status:** Accepted  
**Date:** 2026-10-24  
**Supersedes:** [ADR-007](007-ollama-fallback.md) (Ollama embeddings), [ADR-009](009-reranker.md) (local CrossEncoder)

## Context

Embeddings ran on a local Ollama `nomic-embed-text` and reranking on a local `cross-encoder/ms-marco-MiniLM-L-6-v2` (sentence-transformers + torch). On the 7 GB laptop ([ADR-000](000-constraints.md)) that meant:

- Two extra local runtimes to install and keep running (Ollama, ~1–2 GB of torch), and a backend that refused to start when Ollama was down.
- Embedding one chunk per request to stay inside RAM, so a full re-index took several minutes.
- A CI job that downloaded Ollama and its models on every run, and still did not test what users run.

Chat answers already used a hosted API (Groq, [ADR-006](006-groq-llm.md)).

## Decision

All model work runs on hosted APIs; nothing runs locally.

| Job | Model | Provider |
|---|---|---|
| Embeddings | `jina-embeddings-v3` (query / passage task adapters) | Jina API (`JINA_API_KEY`) |
| Reranking | `rerank-v3.5` (scores 0–1) | Cohere API (`COHERE_API_KEY`) |
| Answers, routing | see ADR-006 | Groq API |

- Embeddings are sent in batches of 32 to stay inside free-tier request limits; 429/5xx responses are retried with `Retry-After`.
- `RERANK_MIN_SCORE` defaults to `0.0` on Cohere's 0–1 scale.
- The ingest manifest records the embedder (`jina:<model>`); changing the model rebuilds the vector store once.
- CI runs the same providers with keys from repository secrets, so the gates test what ships.

**Verification (2026-10-24):** the retrieval gate on the 9 graded golden questions gave recall@3 = 1.0 and MRR = 0.944.

## Failure behaviour

- **Jina unavailable:** the backend stops at startup, or the question returns "could not search the loan policy documents". There is no silent fallback for embeddings.
- **Cohere unavailable or key missing:** answers are built from the unranked top 3 candidates, the relevance floor is skipped, and a warning is logged (startup step 2 also warns when the key is missing).

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Keep Ollama + local CrossEncoder | Extra installs and RAM on the 7 GB laptop; slow re-index; CI could not match it without multi-GB downloads |
| Keep both as optional providers | Two code paths and two sets of docs to keep in sync; the local path was untested and drifted |
| OpenAI embeddings | Paid only; Jina has a free key with no card |

## Consequences

- **Privacy:** the question text, after PII redaction (`redact_input` in the `/ask` request model), and the candidate passages are sent to Jina and Cohere. The corpus is public bank and RBI documents; use synthetic data only ([ADR-006](006-groq-llm.md)).
- **Quotas:** Cohere's trial key allows about 1,000 calls/month and 10 rerank calls/minute (one call per question). CI uses the same keys, about 25 calls per run.
- **No offline mode:** the app needs internet and three API keys.

## Production path

Paid tiers with an SLA, or self-hosted embedding and rerank endpoints on GPU inside the bank's network so no query text leaves it.
