# Architecture Decision Records

Every significant technical choice in this project is recorded here as an ADR (Architecture Decision Record). Each ADR answers:
- **What** was decided
- **Why** this option and not the alternatives
- **What it costs** (RAM, latency, risk)
- **Production path** — what a real bank would use instead

See [ADR-000](000-constraints.md) for the hard constraints that govern every decision below.

---

## Decision Index

| # | Decision | Status | One-line why |
|---|---|---|---|
| [000](000-constraints.md) | Project constraints | Accepted | Fixed reference for all other ADRs |
| [001](001-fastapi-backend.md) | FastAPI as backend framework | Accepted | Async-native, auto docs, production-grade |
| [002](002-streamlit-frontend.md) | Streamlit as frontend | Accepted (demo trade-off) | One language, zero build pipeline |
| [003](003-postgresql.md) | PostgreSQL over SQLite | Accepted | Row locks, JSONB, enums — SQLite cannot do these |
| [004](004-async-sqlalchemy.md) | Async SQLAlchemy + Alembic | Accepted | Matches FastAPI event loop; schema versioned |
| [005](005-jwt-auth.md) | JWT access + refresh tokens | Accepted | Stateless, multi-client; token_version makes it revocable |
| [006](006-groq-llm.md) | Groq free tier for answers and routing | Accepted (with masking) | Only provider fast enough on 7 GB; synthetic data + server-side masking |
| [007](007-ollama-fallback.md) | Ollama for embeddings only (chat removed) | Accepted | Free, local, ~0.5 GB; Groq has no embeddings API |
| [008](008-hybrid-search.md) | Hybrid BM25 + vector search | Accepted | Exact clause terms need keyword match; vector alone misses them |
| [009](009-reranker.md) | CrossEncoder reranker (MiniLM) | Accepted | ~90 MB RAM, ~3 s latency vs 1.1 GB / 15 s for bge-base |
| [010](010-chroma.md) | Chroma as vector store | Accepted (pgvector upgrade path named) | Embedded, zero setup, free |
| [011](011-rule-prequal.md) | Rule-based pre-qualification engine | Accepted | Explainable to regulators; ML requires training data |
| [012](012-guardrails.md) | Regex guardrails: redact PII, refuse injection | Accepted | Zero RAM; checksum-validated IDs; Presidio is production path |
| [013](013-totp-mfa.md) | TOTP MFA (mandatory for staff) | Accepted | SMS costs money; TOTP is free and stronger |
| [014](014-langfuse.md) | Langfuse Cloud (masked) | Accepted | Self-host needs ClickHouse + Redis, doesn't fit 7 GB |
| [015](015-rate-limiting.md) | slowapi in-process rate limiting | Accepted (documented) | Single process; Redis is production path |
| [016](016-local-uploads.md) | Local disk for file uploads | Accepted (documented) | Free; S3 is production path |
| [017](017-docker-compose.md) | Docker Compose single-machine | Accepted | Reproducible on any laptop; Kubernetes is production path |
| [018](018-intent-routing.md) | Query understanding + tool routing for chat | Accepted | "Why was I rejected" lives in Postgres, not PDFs; follow-ups need a standalone rewrite before search |
| [019](019-multi-product-applications.md) | Personal loan + credit card applications | Accepted | One table + product_type; per-product rules from HDFC eligibility pages, with knockout rules |

---

## How to read a status

| Status | Meaning |
|---|---|
| **Accepted** | Implemented and defensible as-is |
| **Accepted (demo trade-off)** | Implemented; a known limitation is documented in the ADR |
| **Superseded by #N** | Replaced; the old ADR explains why |
| **Planned** | Decided but not yet implemented |
