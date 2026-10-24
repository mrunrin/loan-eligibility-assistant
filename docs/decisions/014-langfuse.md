# ADR-014 — Langfuse for LLM Observability

**Status:** Accepted  
**Date:** 2026-10-21 (revised 2026-10-23: full-pipeline traces with token usage)

## Context

Each chat message makes up to two Groq calls (router and answer) around a retrieval step (Jina embeddings + Cohere rerank, [ADR-020](020-hosted-embeddings-rerank.md)). Without observability, it is impossible to know which questions are slow, which retrievals are failing, what the token costs are, or whether the guardrails are triggering incorrectly. See [ADR-006](006-groq-llm.md) and [ADR-000](000-constraints.md).

## Decision

Use **Langfuse Cloud** (free tier) through the Langfuse Python SDK, with every traced input and output passed through the PII mask (`app/tracing.py`, using `redact_pii` from [ADR-012](012-guardrails.md)) before it leaves the process.

## Why Langfuse, not LangSmith or Weights & Biases

- **Langfuse** — open source (MIT), purpose-built for LLM traces, generous free cloud tier, and a self-host option for production.
- **LangSmith** — tied to LangChain's cloud; no self-host on a free plan.
- **Weights & Biases** — ML experiment tracking, not LLM observability. More overhead for this use case.
- **Custom JSONL audit log** (`app/audit.py`) — already in the codebase for request-level audit (hashed question, outcome, latency). Langfuse captures the AI-layer details (prompts, chunks, scores, tokens) that the JSONL log deliberately does not.

## Why Langfuse Cloud, not self-hosted

Self-hosted Langfuse v3 needs PostgreSQL, ClickHouse, Redis and object storage — well over the ~7 GB RAM budget. The cloud tier is free, and only synthetic, PII-masked data is sent.

## What is traced

One trace per chat request (`loan-eligibility-ask` or `loan-eligibility-stream`):

| Observation | Type | Data captured |
|---|---|---|
| root | span | question (identifiers already removed), answer, status, intent, total Groq tokens (`groq_tokens`), request/session ids |
| `rewrite-and-route` | generation | router model, conversation input, JSON output (standalone question, intent, slots), token usage |
| `hybrid-retrieval` | retriever | search query, final chunk ids, pages and scores |
| `rerank` (inside retrieval) | span | dense and BM25 candidate counts, reranker model, final chunk ids and scores |
| `llm-generation` | generation | answer model, prompt (masked application context), answer, token usage (streaming included) |

Generations are created inside the Groq client (`app/llm.py`), so every Groq call is traced the same way on `/ask` and `/ask/stream`. Guardrail refusals show up as `status: blocked` on the root span.

## Consequences

- Configured with `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` in `.env`; tests set `LANGFUSE_TRACING_ENABLED=false`.
- Traces are flushed on shutdown; tracing failures are logged, not raised.
- The token usage on each generation is the same figure the per-borrower budget charges ([ADR-006](006-groq-llm.md)).

## Production path

Self-hosted Langfuse (or Langfuse Cloud with a data-processing agreement) in-region. Add alert rules for p95 latency > 5 s, retrieval-failure rate and guardrail trigger rate.
