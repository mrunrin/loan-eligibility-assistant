# ADR-008 — Hybrid Search: BM25 + Dense Vector

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The knowledge base consists of legal loan agreements, RBI policy documents, and HDFC product sheets. Users ask questions like "what is the penal charge under clause 12.3?" and "what is the maximum loan tenure?" See [ADR-000](000-constraints.md).

## Decision

Use **hybrid retrieval**: keyword BM25 search combined with dense vector search, then reranked with the Cohere Rerank API. See [ADR-020](020-hosted-embeddings-rerank.md) for the reranker choice.

## Why hybrid, not vector-only

Legal documents contain exact clause references ("clause 12.3", "schedule III"), defined terms ("the Borrower", "the Lender"), and numeric values ("12%", "₹50,00,000"). Dense vector search encodes semantic meaning but can miss exact string matches — "penal charges" may not retrieve a passage that says "penal interest" unless the embeddings happen to be close. BM25 catches exact terms that vector search misses.

Conversely, BM25 misses paraphrases and synonyms. "What happens if I miss a payment?" will not match "consequences of default" by keyword. Vector search catches these.

The combination covers both.

## Why hand-rolled BM25 (~55 lines in retrieval.py)

The standard `rank_bm25` Python package would add a dependency for exactly 55 lines of well-understood math. The implementation is a direct translation of the BM25 formula (k1=1.5, b=0.75) with no edge cases. Adding a package for this would be over-engineering.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Vector-only search | Misses exact clause numbers and defined terms — high miss rate on legal documents. |
| BM25-only search | Misses semantic paraphrases and synonyms. |
| Elasticsearch / OpenSearch | A third service (~1–2 GB RAM) just for search. Chroma already handles vector search; BM25 is 55 lines of Python. |
| ColBERT / PLAID | Late interaction models with significantly higher RAM and complexity. No benefit demonstrated on this corpus size. |

## Consequences

- Two retrieval passes per question. Wall-clock cost is dominated by the embedding model (already loaded), not the BM25 pass (~1 ms).
- Duplicate documents can appear in combined results. Deduplication by `chunk_id` is applied before reranking.
- Both passes apply the same metadata filters: `source_file` when the question names a lender (`KNOWN_LENDER_NAMES`), and `product` (the selected application's product plus `general` rules) when the borrower picks an application in the chat. The product filter works per chunk, because one file (`rbi_regulations.md`) mixes credit-card, personal-loan and general rules.
- The top 3 reranked chunks (`FINAL_EVIDENCE_K`) become the evidence `[S1]…[S3]`.

## Production path

OpenSearch with a `knn_vector` field and BM25 scoring — gives both retrieval methods in one managed service with horizontal scaling.
