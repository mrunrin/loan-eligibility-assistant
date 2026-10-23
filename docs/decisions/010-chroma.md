# ADR-010 — ChromaDB as the Vector Store

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The RAG pipeline needs a vector store to persist document embeddings and run similarity search. The store must run locally with no external service. See [ADR-000](000-constraints.md) and [ADR-008](008-hybrid-search.md).

## Decision

Use **ChromaDB** (`chromadb`, through `langchain_chroma`) in persistent mode, stored at `VECTORSTORE_PATH` (default `./vectorstore/`) in the collection `loan_policy_v1`.

## Why ChromaDB

| Criterion | ChromaDB | FAISS | Qdrant | Weaviate |
|---|---|---|---|---|
| Persistent by default | Yes (`PersistentClient`) | No (in-memory; manual `index.save`) | Yes (Docker service) | Yes (Docker service) |
| Pure Python, no separate process | Yes | Yes | No | No |
| Metadata filtering | Yes | No | Yes | Yes |
| RAM footprint (this corpus) | ~50 MB | ~20 MB | ~300 MB (Docker) | ~500 MB (Docker) |
| Collection API (add/query/delete) | Yes | Partial | Yes | Yes |

FAISS is faster at raw ANN search but has no metadata filtering, no built-in persistence, and no delete-by-id (relevant for when documents are removed from the knowledge base). Qdrant and Weaviate both require a running Docker service — a second infrastructure component for a demo that already has PostgreSQL.

## Alternatives rejected

- **Qdrant / Weaviate** — additional Docker service (~300–500 MB RAM). Not justified for 9 source files.
- **FAISS** — no metadata filter, no collection management, no persistent client. Manual index serialization is error-prone.
- **pgvector** — would consolidate vector and relational storage into PostgreSQL. Reasonable choice but adds a PostgreSQL extension and a migration step; ChromaDB is already integrated and working.

## Consequences

- The `vectorstore/` directory is created on first run and persists between restarts (the `vectorstore` volume in Docker). It is listed in `.gitignore`, so embeddings are never committed.
- `ingest_manifest.json` records each source file's SHA-256 and chunk ids, so only new or changed files are re-embedded; a `CHUNKER_VERSION` bump re-embeds everything. A corrupt collection is wiped and rebuilt once at startup.
- Each chunk stores `source_file`, `page`, `section`, `clause`, `product`, `publisher` and `effective_date` metadata, used for filtering and citations.
- The BM25 index is rebuilt in memory on each backend restart by re-parsing the source files (no embedding calls). Acceptable for 9 files; at scale, persist the BM25 index or use OpenSearch.

## Production path

Qdrant, pgvector or a managed vector DB (Pinecone, Weaviate Cloud), with the Chroma calls in `app/retrieval.py` (`initialize_vectorstore`, `retrieve_evidence`) swapped for the new client. The rest of the pipeline only sees `retrieve_evidence()`.
