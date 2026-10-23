# ADR-003 — PostgreSQL over SQLite

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The app stores users, loan applications, pre-qualification results, documents, audit events, and chat history (`chat_messages`). See [ADR-000](000-constraints.md).

## Decision

Use **PostgreSQL 15** running as a Docker service (free, no license cost).

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| SQLite | Single-writer lock — no concurrent writes. No native ENUM column type (emulated as TEXT, which breaks Alembic enum migrations). No JSONB column (needed for `rule_breakdown` in pre-qual results). A VP will immediately ask "why SQLite?" and there is no good answer beyond a single-file toy app. |
| MySQL / MariaDB | No native JSONB. ENUM handling differs from PostgreSQL in ways that cause Alembic migration issues. |
| Cloud-managed Postgres (Supabase, Neon free tier) | External service adds connectivity dependency, latency, and account overhead to a demo. Local Docker Postgres starts instantly, works offline, and is reproducible. |
| MongoDB | Document store with no JOIN support. The loan application data is highly relational (user → applications → documents → prequal results). SQL is the right model. |

## Why PostgreSQL specifically

- **`SELECT ... FOR UPDATE`** — the officer claim locks the row, so a second concurrent claim waits, then sees `under_review` and gets 409. Claims untouched for `CLAIM_TIMEOUT_HOURS` return to the queue.
- **JSONB** — stores the per-rule pre-qualification breakdown without a separate `prequal_rules` table.
- **Native ENUMs** (`user_role`, `application_status`, `prequal_decision`, `product_type`) — enforced at the DB level, not just the ORM.
- **`gen_random_uuid()`** — native UUID primary keys without the `uuid-ossp` extension (Postgres 13+).
- **Triggers** — a `BEFORE UPDATE OR DELETE` / `BEFORE TRUNCATE` trigger makes `audit_events` append-only for every database user, not just the app (migration `012`).
- **Advisory locks** — `pg_advisory_xact_lock` in `migrations/env.py` makes concurrent backends run migrations one at a time.

## Consequences

- Requires Docker on the developer's machine. Adds one service to `docker-compose.yml`.
- Alembic must use a sync `psycopg2` connection for migrations even though the app runtime uses async `asyncpg` — two drivers, two URL schemes. See [ADR-004](004-async-sqlalchemy.md).

## Production path

Managed PostgreSQL with encryption at rest and in transit, automated backups, and a read replica (AWS RDS, Azure Database for PostgreSQL Flexible Server, or Neon for serverless).
