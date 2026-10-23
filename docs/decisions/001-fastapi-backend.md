# ADR-001 — FastAPI as the Backend Framework

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The project needs an HTTP API layer that serves both the Streamlit frontend and exposes endpoints for the RAG chatbot, loan applications, auth, and admin operations. See [ADR-000](000-constraints.md).

## Decision

Use **FastAPI** (Python, async, ASGI).

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| Flask | No native async support — every DB or I/O call blocks the event loop thread. Under concurrent load this serialises all requests. No built-in request validation. |
| Django REST Framework | Ships with an ORM, admin panel, template engine — features we don't need. Heavier startup, ~50–80 MB more RAM. async support is add-on, not native. |
| Express / Node | Would require a second language and runtime. The team is Python-only. |
| aiohttp | Lower-level than FastAPI; requires writing routing and validation by hand. |

## Consequences

- **Pydantic validation** on every request body — bad input is rejected at the boundary, not inside business logic.
- **Auto-generated OpenAPI docs** at `/docs` — useful during development; restricted in production (see security ADRs).
- **Async DB driver required** — see [ADR-004](004-async-sqlalchemy.md).
- FastAPI's dependency injection (`Depends`) is used for auth, DB sessions, and rate limiting — keeps routes thin.

## Production path

Same — FastAPI is production-grade and used at scale by Uber, Netflix, and others. Add a Gunicorn supervisor with multiple Uvicorn workers and put an API gateway (APIM, Kong) in front.
