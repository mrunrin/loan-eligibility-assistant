# ADR-004 — Async SQLAlchemy 2.x + Alembic

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The app uses FastAPI (async) and PostgreSQL. The ORM and migration tool choices must match. See [ADR-001](001-fastapi-backend.md) and [ADR-003](003-postgresql.md).

## Decision

**ORM:** SQLAlchemy 2.x with `asyncpg` driver (`postgresql+asyncpg://`).  
**Migrations:** Alembic with a sync `psycopg2` connection for the migration runner only.

## Why async SQLAlchemy

FastAPI runs on an asyncio event loop. A synchronous DB driver (`psycopg2`) blocks the event loop thread during every query — under concurrent load this serialises all requests to a single thread. With `asyncpg` + `AsyncSession`, DB I/O is awaited, freeing the event loop to handle other requests concurrently.

Key settings:
- `expire_on_commit=False` — prevents lazy-load errors after `await db.commit()` in async contexts (SQLAlchemy would try to reload expired attributes synchronously).
- `pool_pre_ping=True` — health-checks each connection before use; prevents 500 errors after Postgres restarts.

## Why two drivers (asyncpg + psycopg2)

Alembic's migration runner is synchronous. It cannot use an async engine directly. The solution: `migrations/env.py` reads the same `DATABASE_URL` as the app and swaps the driver to `postgresql+psycopg2://` for migrations only; the app runtime uses `postgresql+asyncpg://`. Both point to the same database. `psycopg2-binary` is used (not `psycopg2`) — the binary wheel is fine for dev and Docker; production compiles from source for security.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| SQLModel | Tiangolo's library combining SQLAlchemy + Pydantic. Immature async support, known issues with relationship loading in async contexts. |
| Tortoise ORM | Less mature than SQLAlchemy, smaller ecosystem, fewer Alembic equivalents. |
| Raw asyncpg (no ORM) | More performant but requires hand-writing all SQL, mapping result rows to Python objects, and managing transactions manually. Too much boilerplate for a solo developer. |
| `create_all()` instead of Alembic | `create_all()` has no concept of migration history — it cannot alter existing tables. Breaks as soon as the schema needs to change in a deployed environment. |

## Consequences

- One connection string to maintain (`DATABASE_URL` in `.env`); the psycopg2 variant is derived from it in `migrations/env.py`.
- Migrations run at backend startup (`alembic upgrade head` in the lifespan). A Postgres advisory lock in `migrations/env.py` makes concurrent replicas migrate one at a time; in production, migrations run as a separate job before the new app version starts.

## Production path

Same stack. Add a dedicated migration job in the CI/CD pipeline (`alembic upgrade head` as a pre-deploy step), separate from the app container.
