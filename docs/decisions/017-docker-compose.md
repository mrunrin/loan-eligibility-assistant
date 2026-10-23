# ADR-017 — Docker Compose as the Local Orchestrator

**Status:** Accepted  
**Date:** 2026-10-21

## Context

The application has four runtime components: PostgreSQL (data), Chroma (vectors, in-process), FastAPI backend, and Streamlit frontend, plus Ollama for embeddings on the host. These must start in the correct order, share a network, and be reproducible on any machine with Docker installed. See [ADR-000](000-constraints.md).

## Decision

Use **Docker Compose** (`docker-compose.yml`) as the sole local orchestrator.

## Service map

| Service | Image | Startup dependency |
|---|---|---|
| `postgres` | `postgres:15-alpine` | None |
| `backend` | `./Dockerfile` | `postgres` healthy |
| `frontend` | `./Dockerfile.frontend` | `backend` healthy |
| `seed` (profile `seed`, run on demand) | `./Dockerfile` | `backend` healthy |

Chroma runs **in-process** inside the backend container — no separate service (see [ADR-010](010-chroma.md)). Ollama runs on the host and is reached at `http://host.docker.internal:11434`. The API is published on `127.0.0.1:8000` only; the browser talks to Streamlit on port 8501.

## Why Docker Compose, not bare Docker / shell scripts

A shell script like `start.sh` that runs `docker run ...` four times cannot express startup dependencies or shared networks cleanly. Compose gives `depends_on` with healthcheck conditions (not just "started"), named volumes, and a single `docker-compose up --build` entrypoint.

## Healthchecks

- `postgres`: `pg_isready -U loanbot` — confirms PostgreSQL is accepting connections, not just that the container is running.
- `backend`: a small Python `urllib` request to `http://localhost:8000/health` (no `curl` in the slim image) — healthy only when the database and vector store are ready, i.e. after migrations and indexing.
- `frontend`: Streamlit's `/_stcore/health`.

`depends_on: condition: service_healthy` means the frontend never starts against a backend that is still running migrations.

## Why not Kubernetes

Kubernetes is the right orchestrator for multi-replica production deployments. For a single-machine demo, Kubernetes adds 1–2 GB RAM overhead (even K3s) and a steep learning curve for no additional capability over Compose. The VP evaluation is about the AI pipeline and system design, not the orchestrator choice.

## Named volumes

| Volume | Purpose |
|---|---|
| `postgres_data` | PostgreSQL data directory — survives container restarts |
| `uploads` | Uploaded documents — survives container restarts |
| `vectorstore` | ChromaDB persistent storage and the ingest manifest |
| `audit_logs` | `audit.jsonl` request log |
| `hf_cache` | Downloaded cross-encoder model, so it is fetched only once |

## Consequences

- `docker-compose up --build` is the single command to start the full stack.
- Alembic migrations run at backend startup (`lifespan` in `main.py`). The backend healthcheck only reports healthy after migrations complete; a Postgres advisory lock serialises migrations if several backends start together.
- `.env` is read by Compose via `env_file: .env` — the user copies `.env.example` to `.env` and fills in secrets before first run.

## Production path

Replace Compose with Kubernetes manifests (or Helm charts). The Docker images are identical — only the orchestration layer changes. Add a separate migration job (`initContainer` or a Job) that runs before the backend deployment, so migrations do not run inside the application container on production.
