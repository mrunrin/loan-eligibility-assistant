# LoanMitra Portal + LoanMitra Assistant

> **Deep technical reference:** [TECHNICAL_README.md](TECHNICAL_README.md) — every technology in the stack, its
> configuration, design decisions, data model, API and evaluation, organised for building presentation slides.

A loan origination portal with a grounded AI policy assistant. Borrowers apply for a business loan,
personal loan or credit card through a multi-step form, a deterministic rule engine pre-qualifies each
application, loan officers claim and decide applications, and admins manage users. **LoanMitra**
answers questions from public HDFC Bank and RBI documents using hybrid retrieval-augmented generation,
with inline clause citations, and can also explain the borrower's own application status or calculate an EMI.

This is a student capstone demo, not an official HDFC Bank service.

Stack: FastAPI · PostgreSQL (SQLAlchemy async + Alembic) · Streamlit · ChromaDB · BM25 ·
Cohere (reranking) · Groq (answers) · Jina (embeddings) · Langfuse · Docker · GitHub Actions.

**New here? Follow [USER_MANUAL.md](USER_MANUAL.md)** — clone to a running backend and frontend, step by step.

See [project_architecture_summary.md](project_architecture_summary.md) for the design in one page and
[docs/decisions/](docs/decisions/README.md) for the reasoning behind each choice.

## Features

**Portal**
- Registration with email verification (SMTP, or an explicit dev-mode switch), login, TOTP 2FA
  (mandatory for officers and admins, optional for borrowers).
- Roles: borrower, officer, admin — enforced by the API on every endpoint.
- Three products (business loan, personal loan, credit card), each with its own 4-step form, required
  document checklist and pre-qualification rules.
- Document upload (PDF/JPG/PNG, content-checked, 10 MB cap, SHA-256 tamper check on every download), with in-page image and PDF preview.
- Explainable pre-qualification: weighted rules plus hard knock-out rules → pass / manual review / fail;
  self-reported figures are marked *unverified*. Borrowers get the reasons from the assistant ("Ask here") and
  the status email, officers see an AI-generated case summary, admins see the full per-rule breakdown.
- Borrowers can appeal a pre-qualification fail (moves it to manual review) and withdraw before a decision.
- Officer queue: claim an application (`under_review`), then approve or reject with a mandatory reason;
  officers cannot decide their own applications; claims untouched for 1 h return to the queue.
- The borrower is emailed on every status change; a fail email lists the failed rules in writing.
- Admin dashboard: application/user statistics, role management, account activation and deactivation. There is no super-admin: an admin cannot change the role of, or deactivate, themselves or another admin.

**LoanMitra (RAG + tools)**
- A small router model rewrites each message into a standalone question and picks a tool:
  policy search, the borrower's own application record, or an EMI calculator.
- Column-aware PDF ingestion with cross-page table merging and clause-aware chunking; every chunk carries
  `product`, `publisher` and `effective_date` metadata.
- Hybrid retrieval (ChromaDB dense with Jina query/passage embeddings + BM25) → Cohere rerank → relevance floor.
  When the borrower picks an application in the chat, search is limited to that product plus general rules.
- Answers cite their evidence inline as `[S1]…[S3]` and credit each term to its publisher (HDFC Bank or RBI);
  the sources panel shows publisher, document, page, clause, date and excerpt.
- Guardrails: personal identifiers (Aadhaar, PAN, card, phone, email) are removed from the question before
  any model sees them; off-topic and prompt-injection inputs are refused; outputs are PII-redacted (also
  while streaming). User-typed application fields enter the prompt as quoted data, never as instructions.
- Conversation memory per user and session, stored in PostgreSQL.
- Per-borrower daily token budget counted from Groq's reported usage, reset at midnight IST.

## Architecture

```mermaid
flowchart TD
    U[Browser] --> S[Streamlit multi-page UI]
    S -->|Bearer JWT| A[FastAPI]
    A --> DB[(PostgreSQL)]
    A --> G{Guardrails: PII removed, injection / off-topic refused}
    G --> RT[Router model: rewrite + intent]
    RT -->|policy| R[Hybrid retrieval]
    RT -->|my application| DB
    RT -->|emi| E[EMI calculator]
    R --> C[(ChromaDB)]
    R --> B[BM25]
    C --> X[Cohere rerank + relevance floor]
    B --> X
    X --> L[Answer model: Groq]
    L --> O[Output PII guard] --> S
    A -.-> LF[Langfuse traces, PII-masked]
    A -.-> AU[audit.jsonl + audit_events table]
```

## Prerequisites

- Python 3.13 (3.12+ works; some pinned packages, such as numpy, need 3.12)
- PostgreSQL 15+ (or Docker)
- Free API keys for [Jina](https://jina.ai/embeddings/) (embeddings) and [Cohere](https://dashboard.cohere.com/api-keys)
  (reranking). All model work runs on hosted APIs; nothing is downloaded or run locally.
- A free [Groq API key](https://console.groq.com/keys) for chat answers
- Node.js 20 (only for the Promptfoo evaluation)

## Configuration

```powershell
copy .env.example .env
```

Then edit `.env`:

- `JWT_SECRET_KEY` — **required**; the backend refuses to start without a random value of at least 32 bytes.
  Generate one with `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
- `DATABASE_URL` — defaults to `postgresql+asyncpg://loanbot:loanbot@localhost:5432/loanbot`.
- SMTP settings for real email (verification codes, lockout notices, status updates), **or** leave
  `SMTP_USER`/`SMTP_PASSWORD` empty and set `DEV_EMAIL_VERIFY=true` to verify new accounts instantly from the UI.
- `GROQ_API_KEY` — needed for chat answers.
- `JINA_API_KEY` — **required** for embeddings; the backend stops at startup without it.
- `COHERE_API_KEY` — **required** for reranking; without it answers are built from unranked passages.
- Optional: Langfuse keys, `DATA_ENCRYPTION_KEY` (TOTP-at-rest key). The other values can stay as they are.

## Run locally

```powershell
python -m venv capstone
.\capstone\Scripts\activate
python -m pip install -r requirements.txt -c constraints.txt

python -m alembic upgrade head      # also runs automatically when the backend starts
python scripts/seed_demo.py         # optional demo users/applications (sign-ins in scripts/seed_data/users.json)
```

Start the backend and the frontend in two terminals:

```powershell
python -m uvicorn app.main:app --port 8000
```

```powershell
python -m streamlit run frontend/streamlit_app.py
```

Open http://localhost:8501. API docs: http://localhost:8000/docs.

The first backend start builds the vector index from `data/*.pdf` and `data/*.md` (embedded through the
Jina API). Incremental ingestion: a per-file manifest tracks SHA-256 hashes so only new or changed files
are re-embedded on restart. A `CHUNKER_VERSION` bump wipes and re-embeds everything automatically;
`FORCE_REBUILD_VECTORSTORE=true` does the same on demand.

### Demo accounts

`scripts/seed_demo.py` creates the users in `scripts/seed_data/users.json` as already-verified accounts.
Their passwords are the public demo values in that file's `password` field. Officers and admins are taken
through 2FA enrollment on their first login.

## Run with Docker Compose

PostgreSQL, the backend and the frontend run in containers.

```powershell
docker compose up --build
docker compose --profile seed run --rm seed     # optional demo data
```

The frontend is on http://localhost:8501. The API is published on `127.0.0.1:8000` only.
`docker compose down -v` also deletes the database, vector store and uploads volumes.

## Tests, lint and evaluation

```powershell
python -m pytest            # unit tests (engine, guardrails, chunking) + API integration tests (need PostgreSQL)
python -m ruff check .
python eval/retrieval_eval.py   # recall@3 / MRR on 9 golden questions
```

Promptfoo answer evaluation (backend running; use an officer or admin access token, which has no daily token budget):

```powershell
$env:EVAL_ACCESS_TOKEN = "<access token from POST /auth/login>"
npx promptfoo@0.102.0 eval --no-cache --no-write -o eval/promptfoo_results.json
python eval/check_pass_rate.py eval/promptfoo_results.json
```

CI (`.github/workflows/eval.yml`) runs lint → migrations → pytest → retrieval gate
(recall@3 ≥ 0.80) → Promptfoo gate (pass rate ≥ 90%, including a numeric-faithfulness check on every answer).
See [eval/README.md](eval/README.md).

## Pre-qualification rules

Business loan:

| Rule | Pass condition | Points |
|---|---|---|
| Minimum loan amount | ≥ ₹1,00,000 | 5 (knockout) |
| Maximum loan amount | ≤ ₹5,00,00,000 | 5 (knockout) |
| Credit score | ≥ 650 (below 550 is a knockout) | 25 |
| Business vintage | ≥ 2 years | 20 |
| Revenue-to-loan ratio | annual revenue ≥ 2 × loan | 20 |
| EMI affordability | monthly capacity ≥ EMI at 12% p.a. over 36 months | 25 |

Personal loan and credit card rules (age, salaried-only, work experience, minimum income, credit score,
FOIR ≤ 50%) are listed in [ADR-019](docs/decisions/019-multi-product-applications.md).

A failed knockout rule means fail whatever the score. Otherwise score ≥ 70 → pass, 50–69 → manual review,
< 50 → fail. Credit score, revenue, EMI capacity, income and FOIR are self-reported and marked *unverified*
until an officer checks them. Money is computed with `Decimal` and the EMI is rounded to the paisa.

## Model providers

| Job | Model | Runs on |
|---|---|---|
| Answers | `GROQ_MODEL` (default `openai/gpt-oss-20b`) | Groq cloud |
| Rewrite + intent routing | `ROUTER_MODEL` (default `qwen/qwen3.8-27b`) | Groq cloud |
| Embeddings | `EMBEDDING_MODEL` (default `jina-embeddings-v3`) | Jina API |
| Reranking | `RERANKER_MODEL` (default `rerank-v3.5`) | Cohere API |

If Jina is unavailable the request fails with a clear error. If Cohere is unavailable, answers are built from
unranked passages and the backend logs a warning.

## Observability

- Langfuse (when keys are set): one trace per chat request with the rewrite/route generation, hybrid
  retrieval, rerank and answer generation, each with token usage. All traced inputs/outputs pass through the PII mask first.
- `audit_events` table (append-only, enforced by a Postgres trigger): logins (success and failure, with IP),
  lockouts, refresh-token reuse, 2FA changes, document upload/download/integrity failures, claims, decisions,
  appeals, withdrawals, role and activation changes, account deletion.
- `logs/audit.jsonl`: one line per chat request — request id, session id, SHA-256 of the question,
  outcome (`ok`, `blocked`, `no_evidence`, `retrieval_failed`, …), intent, latency, source pages, providers.
- Every response carries an `X-Request-ID` header.

## Security summary

- bcrypt passwords; JWT access tokens (5 min) and rotating refresh tokens (1 hour) with `iss`/`aud`
  claims; reusing an old refresh token revokes the session.
- Sessions end server-side after 15 idle minutes or 12 hours total; the browser logs out after 5 idle minutes.
- Lockout after 5 failed passwords or 2FA codes, doubling on each repeat (15 min → 24 h), with an email to
  the owner; one IP gets 429 after 20 failed logins in 15 minutes.
- TOTP secrets encrypted at rest (Fernet); codes cannot be replayed.

## Project status

Done: authentication with 2FA, lockouts and server-side session limits; roles; three application products;
pre-qualification with knockouts; appeals and withdrawals; status emails; officer and admin workflows;
document upload/download with integrity checks; hybrid RAG with citations, product and lender filtering;
intent routing; guardrails on input and output; chat memory in PostgreSQL; full-pipeline Langfuse tracing;
append-only audit trail; migrations `001`–`012`; unit and API integration tests; retrieval and answer
quality gates in CI; Docker Compose with a seed profile.

Open before the final demo:
- Run a clean `docker compose up --build` end to end.
- Re-run `eval/retrieval_eval.py` after any re-embed and compare recall@3 / MRR.
- Measure time-to-first-token and total latency on the demo machine.
- Push a deliberately broken change to confirm the CI gates fail, and make the "RAG Quality Gate"
  check required in the repository's branch protection settings.
- Rehearse: in-policy answer, follow-up, missing-policy question, own-application question, EMI, source inspection.

## Known limitations

- Refreshing the browser page usually returns you to the login page (kept on purpose, like a bank site).
  Streamlit keeps JWTs in session state and also writes them to browser cookies that JavaScript can read, as a
  best-effort session restore that rarely succeeds; a production frontend would use httpOnly cookies through a
  backend-for-frontend.
- Rate-limit counters, per-IP failed-login counters and the daily token budget live in process memory:
  one backend worker only, and a restart resets them. Use Redis or a database table before scaling out.
- Refresh tokens are one active session per user: logging in on a second device ends the first session.
- Streamlit forwards the browser's IP in `X-Forwarded-For` (trusted only from `TRUSTED_PROXIES`), and
  logged-in routes are limited per user. Without a reverse proxy in front of Streamlit, that IP is
  whatever Docker's port proxy reports.
- Uploaded files are stored on local disk (a Docker volume), not object storage; status emails are sent by
  in-process background tasks, not a job queue.
- The policy corpus is 5 HDFC PDFs (personal loan, business loan, credit card) + 4 HDFC/RBI
  MD files — not a full bank policy set; the evaluation sets are small (9 retrieval,
  9 answer cases) and the faithfulness check covers numbers only.

## Troubleshooting

- **Backend exits at startup with a JWT error** — set `JWT_SECRET_KEY` in `.env` (see Configuration).
- **Backend exits at startup with "Vectorstore init failed"** — check `JINA_API_KEY` and your Jina free-token
  balance; the log above the error shows the API response.
- **`/health` returns 503** — check `database_ready` / `vectorstore_ready` in the body.
- **Policy questions answer "I could not search the loan policy documents"** — the Jina API failed for that
  question (key, quota or network); every question embeds the query through it.
- **Cannot log in after registering** — verify the email (or set `DEV_EMAIL_VERIFY=true` with SMTP off).
