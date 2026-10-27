# LoanMitra Portal — Technical Reference

A full technical description of the LoanMitra Portal (loan origination) and the LoanMitra Assistant (RAG chatbot).
It is written so that each technology section stands on its own: pick a stack, read its section, and you have
what it is, how this project uses it, the exact configuration, why it was chosen over the alternatives, its limits,
and ready-to-use slide points.

> Student capstone. Policy content is summarised from public HDFC Bank and RBI documents; the portal is not
> affiliated with or endorsed by HDFC Bank Ltd. All facts below were checked against the code at the time of writing.

---

## Contents

**Part A — Project overview**

- A1. [What it is](#a1-what-it-is)
- A2. [Features by role](#a2-features-by-role)
- A3. [Prerequisites](#a3-prerequisites)
- A4. [Setup and run locally](#a4-setup-and-run-locally)
- A5. [Run with Docker Compose](#a5-run-with-docker-compose)
- A6. [Demo accounts and a guided demo](#a6-demo-accounts-and-a-guided-demo)
- A7. [Tests, lint and evaluation](#a7-tests-lint-and-evaluation)
- A8. [Troubleshooting](#a8-troubleshooting)
- A9. [Repository documents](#a9-repository-documents)

**Part B — Technical reference**

1. [The system in one page](#1-the-system-in-one-page)
2. [Architecture](#2-architecture)
3. [Tech stack at a glance](#3-tech-stack-at-a-glance)
4. [Stack deep dives](#4-stack-deep-dives)
   - 4.1 [Frontend — Streamlit](#41-frontend--streamlit)
   - 4.2 [Backend API — FastAPI, Pydantic, Uvicorn](#42-backend-api--fastapi-pydantic-uvicorn)
   - 4.3 [Database — PostgreSQL, SQLAlchemy (async), Alembic](#43-database--postgresql-sqlalchemy-async-alembic)
   - 4.4 [Authentication and account security](#44-authentication-and-account-security)
   - 4.5 [Pre-qualification rule engine](#45-pre-qualification-rule-engine)
   - 4.6 [Document ingestion — PyMuPDF, pdfplumber, NLTK, LangChain splitters](#46-document-ingestion--pymupdf-pdfplumber-nltk-langchain-splitters)
   - 4.7 [Retrieval — ChromaDB, BM25, Jina embeddings, Cohere rerank](#47-retrieval--chromadb-bm25-jina-embeddings-cohere-rerank)
   - 4.8 [LLM layer — Groq (router, answers, case summary)](#48-llm-layer--groq-router-answers-case-summary)
   - 4.9 [Guardrails and PII protection](#49-guardrails-and-pii-protection)
   - 4.10 [Observability and audit — Langfuse, audit log, health](#410-observability-and-audit--langfuse-audit-log-health)
   - 4.11 [Email — aiosmtplib (Gmail SMTP)](#411-email--aiosmtplib-gmail-smtp)
   - 4.12 [Rate limiting — SlowAPI](#412-rate-limiting--slowapi)
   - 4.13 [File uploads](#413-file-uploads)
   - 4.14 [Containers — Docker and Docker Compose](#414-containers--docker-and-docker-compose)
   - 4.15 [CI/CD and quality — GitHub Actions, pytest, Ruff, pre-commit](#415-cicd-and-quality--github-actions-pytest-ruff-pre-commit)
   - 4.16 [Evaluation — golden retrieval set and Promptfoo](#416-evaluation--golden-retrieval-set-and-promptfoo)
5. [End-to-end flows](#5-end-to-end-flows)
6. [API reference](#6-api-reference)
7. [Data model](#7-data-model)
8. [Configuration reference](#8-configuration-reference)
9. [Non-functional characteristics](#9-non-functional-characteristics)
10. [Known limitations and production path](#10-known-limitations-and-production-path)
11. [Slide kit — key numbers and a suggested deck](#11-slide-kit--key-numbers-and-a-suggested-deck)

---

# Part A — Project overview

## A1. What it is

LoanMitra Portal is a loan origination web application with a built-in AI assistant.

- **Borrowers** apply for a **business loan, personal loan or credit card** through a 4-step form, upload their
  documents and get an **instant, explainable pre-qualification** result (pass, manual review or fail).
- **Loan officers** work a queue: claim an application, read an AI-generated case summary, check the documents and
  approve or reject with a written reason.
- **Admins** manage users and roles and see portal statistics.
- **LoanMitra Assistant** answers policy questions only from indexed HDFC Bank and RBI documents (with inline
  citations), explains a borrower's own application and next steps, calculates EMIs, and refuses anything else.

Everything runs on a normal laptop: the AI parts use hosted APIs (Groq, Jina, Cohere), so no GPU or local model is
needed, and every service used has a free tier.

## A2. Features by role

**Borrower**
- Register with email verification (6-digit code by email, or a dev-mode button when email is off); log in;
  optional TOTP 2FA from the Security page.
- Choose a product; fill a 4-step form; upload the product's required documents (PDF/JPG/PNG, up to 10 MB).
- Optional product preferences on step 2 (business loan: facility type, industry, tenure, balance transfer,
  collateral; personal loan: employer category, Credit Protect; credit card: card type, add-on card). Each has a
  hint with a question the assistant can answer from the policy documents; none affect the score. The last step
  asks the borrower to confirm they have read the MITC / loan agreement terms.
- Submit → instant pre-qualification score and decision; an email on every status change (failed rules listed
  in writing on a fail).
- Appeal a failed result with a written reason (→ manual review); withdraw any time before a decision.
- "Ask here" 🤖 on the application page opens the assistant already focused on that application: why it passed or
  failed, what the status means, how long it usually takes, how to improve a credit score.
- Approved applications show a fixed "you will hear from us by email" message.
- Delete the account (only when it has no applications).

**Loan officer**
- Mandatory 2FA, enrolled with a QR code on first login.
- Queue of applications that passed or need manual review; own in-progress reviews first, then oldest.
- Claim (others can no longer take it; abandoned claims return to the queue after 1 hour).
- Application detail: an **"AI generated summary"** of the pre-qualification result ("AI can make mistakes"
  note), documents with in-page preview and a tamper check, the borrower's appeal reason.
- "Ask the AI assistant about this application" on an application they claimed or decided: the assistant explains
  its figures, rule results, documents and appeal, and lists the policy for and against it with citations; it never
  recommends approving or rejecting. Each use is recorded as an `assistant_application_viewed` audit event.
- Approve or reject with a mandatory reason; "Approved by me / Rejected by me" history tabs.
- Cannot review their own application.

**Admin**
- Mandatory 2FA.
- Statistics: applications by status, users by role.
- Users list; change roles; activate/deactivate accounts. No super-admin: an admin cannot change or deactivate
  themselves or another admin.
- Full per-rule pre-qualification breakdown on application pages; can also use the officer queue and open the
  assistant on any application.

**LoanMitra Assistant (all logged-in users)**
- Grounded answers with `[S1]–[S3]` citations and a sources panel (publisher, document, page, clause, date, excerpt).
- Tools: policy search, the user's own application record (or, for the reviewing officer or an admin, the selected
  application), EMI calculator.
- Personal identifiers (Aadhaar, PAN, card, phone, email) are removed before any model sees them.
- Remembers the conversation; borrowers have a daily AI usage limit.

## A3. Prerequisites

| Need | Why |
|---|---|
| Python 3.13 (3.12+ works) | backend and frontend |
| PostgreSQL 15+ — or Docker Desktop | database |
| Free API keys: [Groq](https://console.groq.com/keys), [Jina](https://jina.ai/embeddings/), [Cohere](https://dashboard.cohere.com/api-keys) | answers, embeddings, reranking |
| Optional: Gmail App Password | real emails |
| Optional: [Langfuse](https://cloud.langfuse.com) keys | tracing |
| Optional: Node.js 20 | Promptfoo answer evaluation |

## A4. Setup and run locally

```powershell
git clone https://github.com/mrunrin/loan-eligibility-assistant.git
cd loan-eligibility-assistant
python -m venv capstone
.\capstone\Scripts\Activate.ps1
python -m pip install -r requirements.txt -c constraints.txt
copy .env.example .env
```

Edit `.env`:

- `JWT_SECRET_KEY` — required; generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
- `GROQ_API_KEY`, `JINA_API_KEY`, `COHERE_API_KEY` — from the links above.
- `DATABASE_URL` — default `postgresql+asyncpg://loanbot:loanbot@localhost:5432/loanbot` (create that role and
  database once, or run PostgreSQL in Docker).
- Email: Gmail `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM`, or leave them empty and set `DEV_EMAIL_VERIFY=true`.
- Recommended: `DATA_ENCRYPTION_KEY` (any long random string; set it before anyone enrols 2FA).

Then, in two terminals (virtual environment active in both):

```powershell
python -m uvicorn app.main:app --port 8000
```

```powershell
python -m streamlit run frontend/streamlit_app.py
```

Optional demo data (once): `python scripts/seed_demo.py`. Open http://localhost:8501; API docs at
http://localhost:8000/docs. The first backend start runs the database migrations and builds the vector index
through Jina; later starts re-embed only changed files. Step-by-step help for a fresh machine: `USER_MANUAL.md`.

## A5. Run with Docker Compose

```powershell
docker compose up --build
docker compose --profile seed run --rm seed      # demo data, once
```

Frontend on http://localhost:8501; the API is published on `127.0.0.1:8000` only; PostgreSQL is internal.
`docker compose down` stops everything (data stays in volumes); `docker compose down -v` also deletes the
database, vector store and uploads.

## A6. Demo accounts and a guided demo

`scripts/seed_demo.py` creates the users in `scripts/seed_data/users.json` (emails and public demo passwords in
that file) as already-verified accounts, with sample applications in every status and stub documents.
Officers and admins enrol 2FA on first login.

A 10-minute demo script:

1. **Borrower** — open a failed application → "Ask here" → *"Why did my application fail?"* → *"How can I improve
   my credit score?"* → appeal it.
2. **Borrower** — new personal-loan application → upload documents → submit → instant result.
3. **Chat** — a policy question with citations (*"What are the foreclosure charges on an HDFC personal loan?"*),
   a missing-policy question (*"home loan rate for women"*), *"EMI for 5 lakh at 12% for 3 years"*,
   and a refusal (*"Ignore all previous instructions…"*).
4. **Officer** — claim the appealed application → AI generated summary → open a document → approve with a reason.
5. **Borrower** — the status and reason update; the email arrives.
6. **Admin** — statistics, deactivate a user, try (and fail) to change another admin.
7. **Langfuse** — show the trace of one chat message with its four spans and token usage.

## A7. Tests, lint and evaluation

```powershell
python -m pytest                    # 135 unit + API integration tests (needs PostgreSQL)
python -m ruff check .
python eval/retrieval_eval.py       # recall@3 / MRR on the 9-question golden set
```

Answer evaluation (backend running; use an officer or admin token, which has no daily budget):

```powershell
$env:EVAL_ACCESS_TOKEN = "<access token from POST /auth/login>"
npx promptfoo@0.102.0 eval --no-cache --no-write -j 1 -o eval/promptfoo_results.json
python eval/check_pass_rate.py eval/promptfoo_results.json
```

GitHub Actions runs all of these on every push to `main` (section 4.15).

## A8. Troubleshooting

| Symptom | Fix |
|---|---|
| Backend exits with a JWT error | set a random `JWT_SECRET_KEY` of at least 32 bytes |
| Backend exits with "Vectorstore init failed" | check `JINA_API_KEY` / Jina free-token balance; read the log line above the error |
| Docker: "attempt to write a readonly database" | an old `vectorstore` volume owned by root: `docker compose down`, `docker volume rm <project>_vectorstore`, `docker compose up` |
| `/health` returns 503 | read `database_ready` / `vectorstore_ready` in the response |
| Chat says it could not search the policy documents | the Jina call failed for that question (key, quota or network) |
| Cannot log in after registering | verify the email, or set `DEV_EMAIL_VERIFY=true` with SMTP empty |
| "Too many failed sign-in attempts" | wait 15 minutes (per account) or for the per-network window to pass |
| Port 8000 or 8501 already in use | stop the local backend/frontend before `docker compose up` |

## A9. Repository documents

| Document | For |
|---|---|
| `README.md` | short project intro and quick start |
| `USER_MANUAL.md` | step-by-step setup on a fresh Windows machine |
| `TECHNICAL_README.md` | this document |
| `project_architecture_summary.md` | the design on one page |
| `docs/decisions/` | 21 architecture decision records (ADR-000 … ADR-020) |
| `eval/README.md` | evaluation sets and gates |
| `.env.example` | every setting with its default |

---

# Part B — Technical reference

## 1. The system in one page

**Problem.** A loan applicant wants to know quickly whether they qualify and why; a loan officer wants a short,
trustworthy queue; and both need answers grounded in the lender's actual policy, not in a chatbot's guess.

**Solution.** Two products in one codebase:

| Product | What it does |
|---|---|
| **LoanMitra Portal** | Borrowers register, apply for a business loan, personal loan or credit card, upload documents and get an instant, explainable pre-qualification result. Officers claim and decide applications with a written reason. Admins manage users. |
| **LoanMitra Assistant** | A grounded RAG chatbot. It answers policy questions only from indexed HDFC Bank and RBI documents (with citations), explains the user's own application from database records, calculates EMIs, and refuses everything else. |

**Roles.** `borrower` (apply, appeal, withdraw, chat, optional 2FA) · `officer` (queue, claim, decide, mandatory 2FA)
· `admin` (users, roles, activation, statistics, can also review, mandatory 2FA). There is no super-admin.

**Key numbers**

| Metric | Value |
|---|---|
| Backend API endpoints | 40 (6 routers + chat and health) |
| Frontend pages | 9 Streamlit pages + landing/login |
| Database tables / migrations | 7 tables · Alembic migrations 001–013 |
| Automated tests | 135 (unit + API integration against real PostgreSQL) |
| Knowledge base | 5 markdown files (4 HDFC/RBI + 1 LoanMitra portal policy) + 5 HDFC PDFs → roughly 1,050 evidence chunks |
| Retrieval quality gate | recall@3 = 1.0, MRR = 0.944 on a 9-question graded golden set |
| Answer quality gate | Promptfoo, 9 answer-level cases, pass rate ≥ 0.9, answer latency p95 ≤ 30 s |
| Hosted AI services | Groq (2 models), Jina (embeddings), Cohere (rerank), Langfuse (tracing) |
| Local models | none — no GPU, no torch, no Ollama |
| Code size | ~7,300 lines of Python in `app/` and `frontend/` |

---

## 2. Architecture

### 2.1 Containers and external services

```mermaid
flowchart LR
  U["Browser<br/>borrower · officer · admin"]
  subgraph DC["Docker Compose network"]
    FE["frontend<br/>Streamlit :8501"]
    BE["backend<br/>FastAPI :8000 (127.0.0.1 only)"]
    PG[("postgres<br/>PostgreSQL 15")]
    VS[("volume: vectorstore<br/>Chroma + manifest")]
    UP[("volume: uploads")]
    LG[("volume: logs<br/>audit.jsonl")]
  end
  subgraph EXT["Hosted services (HTTPS)"]
    GROQ["Groq<br/>router + answers + summary"]
    JINA["Jina<br/>embeddings"]
    COH["Cohere<br/>rerank"]
    LF["Langfuse<br/>tracing"]
    SMTP["Gmail SMTP"]
  end
  U -->|HTTP :8501| FE -->|REST + SSE, Bearer JWT| BE
  BE --> PG
  BE --> VS
  BE --> UP
  BE --> LG
  BE --> GROQ
  BE --> JINA
  BE --> COH
  BE -.-> LF
  BE --> SMTP
```

Only the frontend port is reachable from the network. The backend binds to `127.0.0.1:8000` (local tools such as
Promptfoo can reach it; other machines cannot) and PostgreSQL has no published port at all.

### 2.2 Code layout

```
app/                      FastAPI backend
  main.py                 app factory, middleware, lifespan (4-step startup), /ask, /ask/stream, /health, token budget
  config.py               every setting, read from environment / .env
  database.py, models.py  async SQLAlchemy engine/session and ORM models
  deps.py                 get_current_user (token + session checks), require_role
  auth.py                 JWT, bcrypt, TOTP secret encryption, session limits
  limiter.py              SlowAPI limiter, trusted-proxy client IP
  routers/                auth, mfa (2FA), applications, prequal, officer, admin
  prequal.py              rule engine (per-product rules, knockouts, EMI formula)
  assistant.py            chat orchestration: memory, intent router, application context, EMI tool
  policy_qa.py            precheck, grounded prompt, generation, streaming
  retrieval.py            ingestion manifest, Chroma, BM25, Jina client, Cohere client, hybrid search
  chunking.py             policy files → parent sections → sentence-level evidence chunks
  doc_pipeline.py         page classification, column-aware PDF text, cross-page tables
  guardrails_config.py    PII detection/redaction (Verhoeff, Luhn), injection/off-topic refusal, output guard
  case_summary.py         AI case summary for officers (one Groq call, rules fallback)
  llm.py                  Groq client (async + streaming, retries, token metering, Langfuse generations)
  tracing.py              Langfuse client with PII masking
  audit.py                audit.jsonl writer + audit_events rows
  email_utils.py          OTP, lockout and status emails
frontend/                 Streamlit app
  streamlit_app.py        landing, login, 2FA, email verification, FAQ
  nav.py                  API client (bearer token, refresh, client IP), guards, sidebar, idle timer
  pages/1–9               register, my applications, apply, detail, officer queue, admin, chat, security
migrations/               Alembic (sync psycopg2), versions 001–013
data/                     knowledge base: 5 .md (4 HDFC/RBI + LoanMitra portal policy) + 5 .pdf
eval/                     golden retrieval set, Promptfoo tests, gate scripts
scripts/seed_demo.py      demo users, applications and documents
docs/decisions/           ADR-000 … ADR-020 (architecture decision records)
.github/workflows/eval.yml  CI pipeline
Dockerfile, Dockerfile.frontend, docker-compose.yml, constraints.txt
```

### 2.3 Backend startup (blocks traffic until done)

```
[1/4] Running DB migrations...        Alembic upgrade head, under a Postgres advisory lock
[2/4] Reranker: Cohere API (rerank-v3.5)
[3/4] Loading vectorstore (Jina embeddings)...   embeds only new or changed files
[4/4] Server ready — accepting requests
```

The FastAPI `lifespan` coroutine yields (opens for traffic) only after step 4. A corrupt Chroma collection is
wiped and rebuilt once automatically. `/health` returns 200 only when the database and the vector store are ready.

---

## 3. Tech stack at a glance

| Layer | Technology | Version | Role in this project | ADR |
|---|---|---|---|---|
| Language | Python | 3.13 (CI, Docker, tested) | everything | 000 |
| Frontend | Streamlit (+ `streamlit[pdf]`), extra-streamlit-components, pandas | 1.60.0 / 0.1.71 / 3.0.5 | multipage UI, PDF preview, cookies, tables | 002 |
| API | FastAPI | 0.140.0 | REST + SSE API, dependency injection, OpenAPI | 001 |
| ASGI server | Uvicorn (`[standard]`) | 0.51.0 | serves the API | 001 |
| Validation | Pydantic (`[email]`) | 2.13.4 | request/response schemas, PII redaction in validators | 001 |
| Database | PostgreSQL | 15 (Docker, CI) | system of record, row locks, JSONB, triggers | 003 |
| ORM | SQLAlchemy `[asyncio]` + asyncpg | 2.0.51 / 0.32.0 | async data access | 004 |
| Migrations | Alembic + psycopg2-binary | 1.20.0 / 2.9.13 | versioned schema 001–013 | 004 |
| Auth | python-jose `[cryptography]`, passlib `[bcrypt]` + bcrypt, cryptography (Fernet), pyotp, qrcode | 3.5.0 / 1.7.4 + 4.0.1 / 50.0.2 / 2.10.0 / 8.2 | JWT, password hashing, encrypted TOTP, QR enrolment | 005, 013 |
| Rate limiting | SlowAPI | 0.1.10 | per-user / per-IP limits | 015 |
| Vector store | ChromaDB + langchain-chroma | 1.5.9 / 1.1.0 | persistent dense index | 010 |
| Sparse search | hand-rolled BM25 | — | keyword recall for exact terms | 008 |
| Embeddings | Jina `jina-embeddings-v3` (API) | — | passage/query vectors | 020 |
| Reranker | Cohere `rerank-v3.5` (API) | — | relevance scoring of candidates | 020 |
| LLM | Groq: `openai/gpt-oss-20b` (answers, summary), `qwen/qwen3.8-27b` (router) | — | generation and intent routing | 006, 018 |
| LLM plumbing | langchain-core, langchain-text-splitters, httpx, requests | 1.5.1 / 1.1.2 / 0.28.1 / 2.34.2 | document objects, splitting, HTTP clients | 008 |
| PDF parsing | PyMuPDF, pdfplumber | 1.28.2 / 0.11.10 | column-aware text, tables | 008 |
| NLP | NLTK (`punkt_tab`) | 3.10.3 | sentence segmentation for chunks | 008 |
| Observability | Langfuse | 4.14.1 | traces, generations, token usage | 014 |
| Email | aiosmtplib | 5.1.3 | OTP, lockout, status emails | 005 |
| Files | aiofiles, python-multipart | 25.1.0 / 0.0.32 | streamed uploads | 016 |
| Containers | Docker, Docker Compose | images `python:3.13-slim`, `postgres:15-alpine` | local deployment | 017 |
| CI | GitHub Actions | — | lint → migrate → test → eval gates | — |
| Quality | pytest, Ruff, pre-commit, PyYAML | 9.1.1 / 0.13.3 / 4.3.0 / 6.0.3 | tests, lint, hooks | — |
| Answer eval | Promptfoo (npx) | 0.102.0 | answer-level regression gate | — |
| Reproducibility | `constraints.txt` | 200+ pins | exact versions of every transitive package | — |

---

## 4. Stack deep dives

### 4.1 Frontend — Streamlit

**What it is.** A Python framework that turns a script into a web app; every widget interaction re-runs the script.

**How it is used.**
- `frontend/streamlit_app.py` is the landing + login page (password → TOTP step, or first-login 2FA enrolment with a
  QR code for staff), email verification and a two-column FAQ from `frontend/data/landing_faq.json`.
- Nine pages in `frontend/pages/`: Register, My Applications, Apply (product picker + 4-step form with optional product
  preferences + required-document checklist), Application Detail, Officer Queue, Admin Dashboard, AI Loan Assistant, Security.
- `frontend/nav.py` is the single API client: it adds the bearer token, forwards the browser IP in
  `X-Forwarded-For`, refreshes the access token once on 401, handles a backend that is down, guards pages by role,
  draws the sidebar and injects a 5-minute idle-logout timer.
- Role-specific UI on Application Detail:
  - **Borrower:** score + progress bar, then a 🤖 "Ask here" card that opens the assistant with this application
    pre-selected (pass, manual review, fail, under review, rejected); approved applications show a fixed
    "you will hear from us by email" message instead.
  - **Officer:** an "✨ AI generated summary" dropdown (see 4.8), the decision form, in-page PDF/image preview.
  - **Admin:** the full rule breakdown.
- Chat page streams the answer token by token from `POST /ask/stream` (Server-Sent Events) and shows citations.

**Configuration.** `.streamlit/config.toml` (`fileWatcherType = "none"`, no usage stats); `API_URL` env var.

**Why Streamlit.** Pure Python, fastest way for a one-person team to ship nine data-driven pages; built-in
widgets, `st.pdf`, `st.chat_message`. Rejected: React/Next.js (separate JS stack, much more code), Gradio (made
for ML demos, no multi-page RBAC), Dash (more verbose, chart-oriented).

**Limits.** Tokens live in `st.session_state` plus JS-readable cookies (best-effort restore; a page refresh usually
returns to login, kept on purpose). Every interaction re-runs the page script. Production path: React/Next.js with a
backend-for-frontend and httpOnly cookies.

**Slide points.** 9 role-aware pages in pure Python · live token streaming · AI help built into the application page.

---

### 4.2 Backend API — FastAPI, Pydantic, Uvicorn

**What it is.** FastAPI is an async Python web framework built on Starlette and Pydantic; Uvicorn is the ASGI server.

**How it is used.**
- 6 routers (`auth`, `mfa`, `applications`, `prequal`, `officer`, `admin`) plus `/ask`, `/ask/stream`, `/health`
  and `/` in `main.py` — 40 endpoints in total (section 6).
- Dependency injection does the security work: `get_current_user` (token type, signature, `iss`/`aud`, expiry,
  `token_version`, active + verified user, server-side session limits) and `require_role("officer", "admin")`.
- Pydantic models validate every request. The chat request model redacts PII **inside the validator**, so no
  later code ever sees the raw Aadhaar/PAN/card number.
- Middleware stack, outermost first: TrustedHost (`localhost`, `127.0.0.1`, `backend`, `*.localhost`) → CORS
  (`:8501` origins only, no credentials) → Request-ID (`X-Request-ID` on every request) → SlowAPI rate limiter.
- `StreamingResponse` with `text/event-stream` for the chat: `status`, `token` and `done` events.
- Slow work is kept off the event loop: retrieval runs in a thread pool; status emails use `BackgroundTasks`.

**Why FastAPI.** Async I/O (database, three AI APIs, SMTP), automatic OpenAPI docs at `/docs`, typed request
validation, first-class dependency injection. Rejected: Flask (sync, no built-in validation), Django (heavier ORM and
admin we do not need).

**Slide points.** 40 typed endpoints · security as reusable dependencies · SSE streaming · OpenAPI docs for free.

---

### 4.3 Database — PostgreSQL, SQLAlchemy (async), Alembic

**What it is.** PostgreSQL is the relational system of record; SQLAlchemy 2 is the ORM (async via asyncpg); Alembic
manages schema migrations (sync via psycopg2).

**How it is used.**
- 7 tables: `users`, `loan_applications`, `pre_qualification_results`, `documents`, `chat_sessions`,
  `chat_messages`, `audit_events` (section 7).
- **Row locks for correctness.** Officer claim and decision use `SELECT … FOR UPDATE`; a second concurrent claim
  waits, then sees `under_review` and gets 409. Admin role/active changes lock the target user row too.
- **JSONB** stores each pre-qualification rule breakdown (`rule_breakdown`) and the optional product preferences
  (`loan_applications.preferences`, validated by a strict Pydantic model that rejects unknown keys).
- **Append-only audit.** A Postgres trigger blocks `UPDATE`/`DELETE` on `audit_events` (only the FK `SET NULL` on
  user deletion is allowed).
- **Indexes** for the hot paths: officer queue `(status, created_at)`; chat history `(user_id, session_id, created_at)`.
- **Migrations 001–013** run automatically at backend startup, inside a Postgres **advisory lock** so two backend
  instances cannot migrate at once. Enum types are created idempotently.
- Application status machine (9 enum values):

```mermaid
stateDiagram-v2
  direction LR
  [*] --> draft
  draft --> pre_qual_pass: submit, score 70+
  draft --> manual_review: submit, score 50–69
  draft --> pre_qual_fail: submit, below 50 or knockout
  pre_qual_fail --> manual_review: borrower appeal
  pre_qual_pass --> under_review: officer claim
  manual_review --> under_review: officer claim
  under_review --> under_review: take over after 1 h idle
  under_review --> approved: decision + reason
  under_review --> rejected: decision + reason
  draft --> withdrawn
  pre_qual_pass --> withdrawn
  pre_qual_fail --> withdrawn
  manual_review --> withdrawn
  under_review --> withdrawn
```

(`submitted` exists in the enum; submit goes straight to a pre-qualification result.)

**Why PostgreSQL.** Row-level locking, JSONB, triggers, advisory locks and concurrent writers — none of which SQLite
offers. Rejected: SQLite (single writer, no `FOR UPDATE`), MongoDB (no relational integrity for an approval workflow).

**Slide points.** Concurrency-safe claims with row locks · tamper-evident audit via a trigger · zero-touch migrations.

---

### 4.4 Authentication and account security

**Building blocks:** python-jose (JWT), passlib + bcrypt (passwords), pyotp + qrcode (TOTP 2FA),
cryptography Fernet (secret encryption), HMAC (email OTP).

| Control | Implementation |
|---|---|
| Passwords | bcrypt via passlib (`bcrypt` pinned to 4.0.1 for passlib compatibility); strength rules at registration |
| Access token | JWT HS256, **5 min**, claims `iss=hdfc-loan-portal`, `aud=hdfc-loan-portal-api`, `token_version`, `type=access` |
| Refresh token | JWT, **1 hour**, rotated on every use; only its hash is stored; reusing an old one revokes the session |
| Revocation | `token_version` on the user; logout and security events increment it, invalidating every older token |
| Session limits | server-side: 15 min idle (`last_seen_at`, written at most once a minute) and 12 h absolute (`session_started_at`); browser: 5 min idle timer |
| 2FA (TOTP) | mandatory for officers/admins (forced enrolment on first login with a 10-minute enrolment token), optional for borrowers; codes cannot be replayed (`totp_last_counter`); a pending secret means `/2fa/setup` can never switch existing 2FA off |
| Secret at rest | TOTP secrets encrypted with Fernet; key = SHA-256 of `DATA_ENCRYPTION_KEY` (falls back to a key derived from the JWT secret) |
| Email verification | 6-digit OTP from a CSPRNG, stored as an HMAC, 15-minute expiry, burned after repeated failures; resend limited to 3 per 4 h |
| Brute force | account lock after 5 wrong passwords or 5 wrong 2FA codes: 15 min, doubling on each repeat up to 24 h, owner emailed; per-IP: 20 failed logins in 15 min → 429 for that network without locking the victims' accounts |
| Enumeration | registration returns the same reply whether or not the email exists, and always hashes (equal timing) |
| Secret hygiene | the app refuses to start with a missing, short (< 32 bytes) or publicly known `JWT_SECRET_KEY` |
| Deactivation | admin deactivation clears the refresh token; the user is rejected on the next request |

**Why this design.** Short-lived access tokens plus rotating refresh tokens give fast revocation without a session
table lookup on every request; `token_version` covers "log out everywhere". Rejected: server-side sessions only
(extra store), long-lived JWTs (no revocation), SMS OTP (cost, SIM-swap risk).

**Slide points.** Defence in depth: bcrypt + JWT rotation + TOTP + lockout + per-IP throttling + session limits.

---

### 4.5 Pre-qualification rule engine

**What it is.** `app/prequal.py` — a deterministic, explainable rules engine that scores every submitted
application. **The decision is never made by an LLM.**

**How it works.**
- Each product has its own rule set. Every rule returns pass/fail, points, a plain-language message, and flags:
  `knockout` (hard eligibility requirement) and `unverified` (based on self-reported figures).
- **Decision bands:** 70+ → `pre_qual_pass` · 50–69 → `manual_review` · below 50 → `pre_qual_fail`.
  **Any failed knockout → fail**, whatever the score. A credit score below 550 is a knockout for every product.

| Product | Rules (points) |
|---|---|
| Business loan | amount ₹1 L – ₹5 Cr (5 + 5, knockout) · credit score ≥ 650 (25) · business vintage ≥ 2 yrs (20) · annual revenue ≥ 2× loan (20) · EMI fits stated capacity (25) |
| Personal loan | knockouts worth 30: amount ₹25 K – ₹50 L, age 21–60, salaried, ≥ 2 yrs experience incl. 1 with current employer, net income ≥ ₹25 K/month · credit score ≥ 650 (35) · FOIR ≤ 50% incl. the new EMI (35) |
| Credit card | knockouts worth 30: age 18+, net income ≥ ₹25 K/month · credit score ≥ 650 (35) · FOIR ≤ 50% (35) |

- EMI formula `P × r × (1+r)^n / ((1+r)^n − 1)`, assuming 12% p.a. over 36 months for affordability.
- Knockout thresholds come from HDFC Bank's public eligibility pages; credit-score and FOIR thresholds are
  **portal policy** (HDFC publishes no universal CIBIL cutoff) and are labelled as such.
- Results are stored once per application (`UNIQUE application_id`, upsert) and re-runnable by officers.
- Submit is refused (422) until required fields and the product's document checklist are complete.
- A fail is not a dead end: the borrower is emailed the failed rules in writing (RBI Fair Practices Code) and can
  appeal with a written reason, which moves the application to `manual_review`.

**Why rules, not ML or LLM.** Lending decisions must be deterministic and explainable to the customer and the
regulator. Rejected: LLM scoring (non-deterministic, can hallucinate thresholds), ML scorecard (no labelled data;
listed as the production path, with the rule explanations kept).

**Slide points.** Deterministic, explainable decisions · knockouts vs scored rules · written reasons + appeal path.

---

### 4.6 Document ingestion — PyMuPDF, pdfplumber, NLTK, LangChain splitters

**Pipeline** (`app/doc_pipeline.py`, `app/chunking.py`, `app/retrieval.py`):

1. **Inputs:** 5 markdown knowledge-base files (`loanmitra_business_loan.md`, `loanmitra_personal_loan.md`,
   `loanmitra_credit_card.md`, `rbi_regulations.md`, and `loanmitra_portal_policy.md` — the portal's own rules on
   decisions, appeals, FOIR and debt consolidation, document checks, officer review and privacy, published as
   "LoanMitra (portal policy)", never as HDFC) and 5 HDFC PDFs (personal loan agreement, business loan terms,
   credit card MITC, cardmember agreement, card FAQ).
2. **PDFs:** each page is classified (`TC`, `SCHEDULE`, `PDC`, `ECS`, `FAQ`, `PROSE`, `BLANK`); text is extracted
   **column-aware** with table regions removed (PyMuPDF), tables are extracted separately (pdfplumber) and **merged
   when they continue onto the next page**. Each page type maps to a chunking strategy.
3. **Markdown:** each file holds several sources, each with a header block (`publisher`, `category`, `source_url`,
   `retrieved_on`). Headers become metadata, not searchable text.
4. **Parents:** sections split with `RecursiveCharacterTextSplitter` (1,800 chars, 150 overlap).
5. **Evidence chunks:** whole sentences (NLTK `punkt_tab`) grouped up to 800 characters with a 1-sentence overlap;
   never split mid-sentence; clause numbers (e.g. "4.2") kept as labels; tables chunked by whole rows.
6. **Metadata per chunk:** `chunk_id` (stable hash), `source_file`, page range, section/clause, `product`
   (`business_loan` / `personal_loan` / `credit_card` / `general`), `publisher`, `effective_date`.
7. **Incremental ingestion:** a manifest stores each file's SHA-256 and chunk IDs, the chunker version (`v4`) and
   the embedder (`jina:<model>`). On startup only new or changed files are embedded; deleted files' chunks are
   removed; a chunker or embedding-model change triggers one full rebuild.

**Why this design.** Loan agreements are two-column legal PDFs with schedules and tables split across pages; naive
fixed-size chunking cuts clauses in half and loses table rows. Sentence-level evidence with parent context gives
precise citations. Rejected: fixed 500-token chunks, OCR-only extraction.

**Slide points.** Layout-aware PDF parsing · cross-page tables · clause-level citations · incremental re-indexing.

---

### 4.7 Retrieval — ChromaDB, BM25, Jina embeddings, Cohere rerank

```mermaid
flowchart LR
  Q["Search query<br/>(router's standalone rewrite)"] --> S["Split compound question<br/>into sub-queries"]
  S --> F["Filters: lender named,<br/>product of selected application"]
  F --> D["Dense: Chroma top 20<br/>Jina retrieval.query"]
  F --> B["Sparse: BM25 top 20"]
  D --> M["Merge + de-duplicate"]
  B --> M
  M --> R["Cohere rerank-v3.5<br/>scores 0–1"]
  R --> T["Top 3 chunks above<br/>RERANK_MIN_SCORE → prompt"]
```

| Component | Detail |
|---|---|
| Vector store | ChromaDB `PersistentClient`, one stable collection `loan_policy_v1`, metadata filters on `source_file` and `product` |
| Embeddings | Jina `jina-embeddings-v3` over HTTPS; task adapters `retrieval.passage` (documents) and `retrieval.query` (questions); 32 chunks per request; 30 s timeout; up to 4 retries honouring `Retry-After` on 429/5xx |
| Sparse | in-process BM25 (k1 = 1.5, b = 0.75), rebuilt at startup, top 20 per sub-query |
| Fusion | union of dense + sparse candidates, de-duplicated by `chunk_id` |
| Reranker | Cohere `rerank-v3.5` (`/v2/rerank`), relevance 0–1; keep chunks with score ≥ `RERANK_MIN_SCORE` (default 0.0); final top 3 (`FINAL_EVIDENCE_K`) |
| Lender filter | a question naming "HDFC" is restricted to files that mention HDFC (excludes the RBI file) |
| Product filter | a chat about a selected application searches that product's chunks plus `general` ones |
| Degradation | rerank failure → first 3 merged candidates; retrieval failure → "could not search the policy documents" |

**Why hybrid + rerank.** Dense vectors catch paraphrases ("close the loan early" ≈ "foreclosure"); BM25 catches exact
terms, clause numbers and amounts that embeddings blur; a cross-encoder reranker reads query and passage together
and fixes the ordering. **Why hosted models (ADR-020).** The 7 GB laptop could not comfortably run Ollama +
PyTorch; hosted APIs removed ~1–2 GB of installs, made CI match production, and keep every free tier sufficient
(Jina: embeddings only; Cohere: 1 call per question). Rejected: FAISS (no metadata filtering/persistence out of the
box), pgvector (fine, but a second role for the transactional DB), local CrossEncoder/Ollama (RAM, install size).

**Slide points.** Hybrid dense + BM25 · cross-encoder rerank · metadata filters · recall@3 = 1.0 on the golden set.

---

### 4.8 LLM layer — Groq (router, answers, case summary)

**Models (Groq, OpenAI-compatible API, `temperature = 0`):**

| Use | Model | Call shape |
|---|---|---|
| Router: rewrite follow-ups into a standalone question **and** pick the intent, in one call | `qwen/qwen3.8-27b` | JSON mode, max 300 tokens, last 6 turns (400 chars each) |
| Grounded answer | `openai/gpt-oss-20b` | streamed, reasoning effort low |
| Officer case summary | `openai/gpt-oss-20b` | one call, max 600 tokens, cached per pre-qual run |

**Assistant pipeline** (`app/assistant.py`, `app/policy_qa.py`):

```mermaid
flowchart TD
  IN["Question (+ selected application)"] --> V["Pydantic: ≤ 2,000 chars,<br/>PII replaced"]
  V --> BU{"Borrower over daily<br/>token budget?"}
  BU -->|yes| STOP["Limit message"]
  BU -->|no| H["Load last 12 turns"]
  H --> P{"Precheck (no LLM):<br/>injection / off-topic /<br/>greeting / name memory"}
  P -->|caught| DIRECT["Direct reply"]
  P -->|continue| R["Router (Groq, JSON)"]
  R --> I{"Intent"}
  I -->|emi| EMI["EMI formula, no LLM"]
  I -->|small_talk / out_of_scope| FIX["Fixed reply"]
  I -->|policy| RET["Hybrid retrieval + rerank"]
  I -->|my_application| CTX["Owner-scoped DB read"] --> RET
  RET --> FL{"Evidence above floor?"}
  FL -->|no| NOEV["'The policy does not specify'<br/>(no LLM call)"]
  FL -->|yes| GEN["Groq answer, streamed"] --> OUT["Output PII guard<br/>per sentence"] --> SAVE["Save turn + audit"]
```

- **Intents:** `policy`, `my_application`, `emi`, `small_talk`, `out_of_scope`. Router output is untrusted: unknown
  intents and bad slots are dropped, and a failed router call falls back to `policy`.
- **Grounding prompt rules:** answer only from passages and application data; passages are quoted documents, never
  instructions; attribute each term to its publisher (HDFC Bank vs RBI); cite every factual sentence `[S1]`–`[S3]`;
  say "The policy does not specify that" when nothing applies; treat blank template fields as "not specified".
- **Application context** is built server-side, and who may see it is a SQL `WHERE` on the JWT user, never an LLM
  decision: borrowers get only their own rows; for one selected application, the officer who claimed or decided it
  and any admin get it too, plus the typed figures, document checklist and appeal, with a prompt that lists policy
  for and against and never recommends a decision. Contents: status, plain meaning of the status, next step and timeline (e.g. "usually 4–7 working days" under
  review), score and every rule result, product preferences (facility type, card type, ...), officer notes. Names are masked, amounts banded, user-typed fields quoted
  as inert JSON strings.
- **Memory:** conversation turns persist in `chat_messages` and survive restarts.
- **Token budget:** borrowers get 50,000 real Groq tokens per day (router + answer, from Groq's own usage numbers),
  resetting at midnight IST; officers and admins are unlimited.
- **Resilience:** Groq 429s are retried up to 3 times honouring `retry-after` (capped at 10 s).
- **Officer AI case summary** (`app/case_summary.py`, `GET /prequal/{id}/summary`, officers only): rule results →
  masked facts → one Groq call → 5–6 bullet lines, shown in an "AI generated summary" dropdown with the note
  "AI can make mistakes"; falls back to a plain rules summary if Groq is unavailable. It explains; it never changes
  the score or decision.

**Why Groq.** Free tier, very low latency (good for streaming), OpenAI-compatible API, separate rate limits per
model (so routing does not eat the answer model's budget). Rejected: local LLMs (quality on 7 GB RAM), OpenAI/Azure
(cost; listed as the production path with an India region).

**Slide points.** One-call router · grounded, cited answers · tools (EMI, own application) · cost-capped per user.

---

### 4.9 Guardrails and PII protection

`app/guardrails_config.py`, applied in layers:

| Layer | What it does |
|---|---|
| 1. Input length | Pydantic: max 2,000 characters |
| 2. PII redaction at the API boundary | Aadhaar (12 digits **validated with the Verhoeff checksum**), card numbers (**Luhn**), PAN, Indian mobile numbers, email → `[REDACTED]` placeholders; the question is still answered and the user sees a notice. Checksums mean loan amounts and account-like numbers are not falsely redacted |
| 3. Precheck refusals | prompt-injection patterns and off-topic requests (medical, legal, trading, personal) get a canned refusal before any LLM call; conversation history is checked too |
| 4. Prompt hygiene | user and staff text enters prompts as inert quoted data; passages are declared "not instructions" |
| 5. Context masking | exact names and amounts replaced by bands before reaching Groq |
| 6. Output guard | every streamed sentence is checked; PII that does not appear in the cited sources is redacted before it reaches the browser |
| 7. Tracing | Langfuse inputs/outputs pass through the same redaction (`mask=`) |

**Why.** PII must never reach a third-party LLM or a trace store; refusing outright would make the bot useless
("my PAN is …, am I eligible?"), so redaction-and-answer is the default. Rejected: guardrails-ai (removed —
heavy dependency for regex-level checks), Presidio (production path for broader entity coverage).

**Slide points.** Checksum-validated PII detection · redact, don't refuse · 7 defensive layers.

---

### 4.10 Observability and audit — Langfuse, audit log, health

- **Langfuse 4.14.1:** one trace per chat message (`loan-eligibility-ask` / `loan-eligibility-stream`) with spans
  `rewrite-and-route`, `hybrid-retrieval`, `rerank`, `llm-generation`, and `case-summary` for officer summaries.
  Each generation records model and Groq token usage. All traced data is PII-masked. Optional: empty keys disable it.
  Streaming traces carry `ttft_ms` (time until the user sees the first words). Promptfoo requests (`eval-` sessions)
  and the eval scripts go to the Langfuse **`eval` environment**, so they do not skew real-traffic latency charts.
- **`logs/audit.jsonl`:** one line per `/ask` request — request ID, session, **hash** of the question (not the text),
  status, latency, time to first words (`ttft_ms`, streaming), intent, sources, provider.
  `eval/latency_report.py` turns it into p50/p95 for total latency and time to first words, excluding eval traffic.
- **`audit_events` table (append-only):** logins (success and failure, with IP), lockouts, refresh-token reuse, 2FA
  changes, document upload/download/integrity failures, claims, decisions (with reason), appeals, withdrawals, role
  and activation changes, account deletion.
- **`/health`:** 200 only when the database and vector store are ready; used by Docker healthchecks and CI.
- **Request IDs:** every response carries `X-Request-ID`, matching log lines and trace metadata.

**Slide points.** Every AI call traced with cost · tamper-evident business audit · health-gated startup.

---

### 4.11 Email — aiosmtplib (Gmail SMTP)

- Async SMTP (STARTTLS, port 587) with a Gmail App Password; sender name "LoanMitra".
- Emails: verification OTP, account-locked notice, and a **status email on every application status change**
  (failed rules listed on `pre_qual_fail`, officer reason on approve/reject).
- Sent through FastAPI `BackgroundTasks` after the response, so users never wait on SMTP.
- With SMTP unset, email is off; `DEV_EMAIL_VERIFY=true` then enables an instant-verify button for local demos.

---

### 4.12 Rate limiting — SlowAPI

- One shared limiter (`app/limiter.py`). Key: the user ID from the JWT for authenticated routes, otherwise the
  client IP. `X-Forwarded-For` is trusted only from `TRUSTED_PROXIES` (default: loopback and the Docker network).
- Limits: chat 20/min · login and register 10/min · token refresh 30/min · resend verification 3 per 4 h · 2FA
  setup/confirm/validate 5–10/min. Plus the per-IP failed-login counter (20 per 15 min).
- Limitation: counters are in process memory (one worker; restart resets them). Production path: Redis.

---

### 4.13 File uploads

- Accepted: PDF, JPG, PNG — checked by extension **and magic bytes**; streamed to disk in chunks with a 10 MB cap.
- Only on draft applications, only by the owner; stored as `uploads/{application_id}/{random}_{sanitised name}`.
- SHA-256 computed on upload, stored in `documents.sha256`, and **re-checked on every download** (tampering is
  detected and audited). Partial files are deleted if a request fails.
- Downloads: owner, the assigned officer, or admins; stored paths are resolved and refused if outside the upload root.
- Officers and borrowers preview PDFs and images in-page (`st.pdf`, `st.image`).
- Required documents per product: business loan (Photo ID, Bank Statement, Business Registration), personal loan
  (Photo ID, Salary Slip, Bank Statement), credit card (Photo ID, Bank Statement).

---

### 4.14 Containers — Docker and Docker Compose

| Service | Image | Ports | Notes |
|---|---|---|---|
| `postgres` | `postgres:15-alpine` | none (internal) | healthcheck `pg_isready`; volume `postgres_data` |
| `backend` | built from `Dockerfile` (`python:3.13-slim`) | `127.0.0.1:8000` | non-root `appuser` (uid 1000); NLTK data baked in; volumes `vectorstore`, `uploads`, `audit_logs`; healthcheck on `/health` with a 900 s start period (first-time embedding) |
| `frontend` | built from `Dockerfile.frontend` (`python:3.13-slim`) | `8501` | starts only after the backend is healthy; `API_URL=http://backend:8000` |
| `seed` | backend image, profile `seed` | — | one-off demo data: `docker compose --profile seed run --rm seed` |

- Installs use `pip install -r requirements-*.txt -c constraints.txt`, so images get exactly the tested versions.
- No torch, no model downloads: the backend image only needs API keys at runtime (from `.env`, never baked in).
- Run: `docker compose up --build` → open http://localhost:8501.

---

### 4.15 CI/CD and quality — GitHub Actions, pytest, Ruff, pre-commit

**Pipeline** (`.github/workflows/eval.yml`, every push and PR to `main`, ~4 minutes, free on a public repo):

```
checkout → Python 3.13 + pip cache → install (requirements + constraints)
→ ruff check → alembic upgrade head (PostgreSQL 15 service container)
→ pytest (135 tests) → retrieval gate (recall@3 ≥ 0.80, real Jina + Cohere)
→ start backend (budget unlimited for the eval user) → mint eval user + token
→ Promptfoo (9 answer tests, one at a time) → pass-rate gate (≥ 0.9) + latency gate (p95 ≤ 30 s)
```

- API keys come from repository secrets and are given only to the steps that call those APIs.
- **pytest:** unit tests (rule engine boundaries, guardrails incl. Verhoeff/Luhn, chunking, router parsing, hosted
  clients with mocked HTTP) and API integration tests against a real PostgreSQL (auth, lockouts, 2FA, IDOR,
  uploads, claims, decisions, admin protection, audit append-only, session limits, AI summary access).
- **Ruff** for lint and import order; **pre-commit** runs Ruff, YAML checks, trailing whitespace, private-key
  detection and a 2 MB file-size limit.

---

### 4.16 Evaluation — golden retrieval set and Promptfoo

| Gate | Set | Metric | Current |
|---|---|---|---|
| Retrieval | `eval/golden_retrieval.yaml`: 9 graded questions (2 easy, 3 medium, 4 hard with distractor documents), each with the gold (document, page) | recall@3, MRR | 1.0, 0.944 |
| Answers | `eval/questions.yaml`: 9 Promptfoo cases — fact, missing-policy, lender filter, Aadhaar and PAN redaction, prompt injection, medical refusal, legitimate question not blocked, off-topic refusal | pass rate | gate ≥ 0.9 |
| Faithfulness | every Promptfoo case also checks that each number in the answer appears in the cited evidence or the question | deterministic | — |
| Latency | the same 9 Promptfoo answers, run one at a time against `/ask` | p50 / p95 | gate p95 ≤ 30 s |

Both sets are kept to 9 questions to stay inside free API tiers. They catch regressions; they do not prove
production accuracy (a paraphrase and held-out set is the next step).

---

## 5. End-to-end flows

### 5.1 Borrower applies

1. Register → email OTP → verify → login (optional 2FA).
2. Apply: pick product → `POST /applications` (draft) → `PATCH` per step → upload required documents.
3. `POST /applications/{id}/submit` → required fields and documents checked → rule engine runs → status set to
   pass / manual review / fail → result stored → status email sent.
4. Application Detail shows the score; "Ask here" opens the assistant pre-scoped to this application.
5. On fail: appeal with a written reason (→ `manual_review`) or re-apply.

### 5.2 Officer reviews

1. Login with mandatory TOTP → Officer Queue (own in-progress reviews first, then oldest unclaimed).
2. Claim (`SELECT … FOR UPDATE`) → `under_review`; claims idle for `CLAIM_TIMEOUT_HOURS` (1) can be taken over.
3. Application Detail: AI generated summary, documents with integrity check and in-page preview, appeal reason.
4. Decide approve/reject with a reason of at least 10 characters → audit row → borrower email.
5. "Approved by me / Rejected by me" tabs show the officer's history.

### 5.3 One chat message

Validation and PII redaction → budget check → history → precheck → router (1 Groq call) → tool or retrieval
(Jina query embedding + BM25 → Cohere rerank) → grounded answer streamed from Groq → per-sentence output guard →
turn saved, audit line written, Langfuse trace closed. Typical cost: 2 Groq calls, 1 Jina call, 1 Cohere call.

---

## 6. API reference

| Router | Method + path | Who | Purpose |
|---|---|---|---|
| main | `GET /` · `GET /health` | public | root, readiness |
| main | `POST /ask` · `POST /ask/stream` | logged-in | chat (JSON / SSE) |
| auth | `POST /auth/register` · `/login` · `/logout` · `/refresh` | public / logged-in | account lifecycle |
| auth | `POST /auth/verify-email` · `/resend-verification` · `/dev-verify` | public | email verification |
| auth | `GET /auth/me` · `DELETE /auth/me` | logged-in | profile, account deletion (only with no applications) |
| mfa | `POST /auth/2fa/setup` · `/confirm` · `/disable` | logged-in | optional 2FA for borrowers |
| mfa | `POST /auth/2fa/enroll/setup` · `/enroll/confirm` | staff (enrol token) | forced staff enrolment |
| mfa | `POST /auth/2fa/validate` | pending-2FA token | second login step |
| applications | `POST` · `GET /applications` · `GET` · `PATCH /applications/{id}` | owner | create, list, read, edit draft |
| applications | `POST /applications/{id}/submit` · `/withdraw` · `/appeal` | owner | lifecycle actions |
| applications | `POST` · `GET /applications/{id}/documents` · `GET …/documents/{doc_id}/download` | owner / staff | uploads and downloads |
| prequal | `GET /prequal/{id}` | owner / staff | stored result |
| prequal | `POST /prequal/{id}/run` | officer / admin | re-run rules |
| prequal | `GET /prequal/{id}/summary` | officer | AI case summary |
| officer | `GET /officer/queue` · `GET /officer/decisions` | officer / admin | queue, own history |
| officer | `POST /officer/{id}/claim` · `/decision` | officer / admin | review |
| admin | `GET /admin/users` · `GET /admin/stats` | admin | users, counts by status and role |
| admin | `POST /admin/users/{id}/role` · `/active` | admin | role change, activate/deactivate (not self, not other admins) |

Interactive docs: http://localhost:8000/docs (OpenAPI).

---

## 7. Data model

```mermaid
erDiagram
  users ||--o{ loan_applications : applies
  users |o--o{ loan_applications : reviews
  loan_applications ||--o| pre_qualification_results : "scored by"
  loan_applications ||--o{ documents : has
  users ||--o{ chat_messages : writes
  users |o--o{ chat_sessions : opens
  users |o--o{ audit_events : "acts in"
```

| Table | Key columns |
|---|---|
| `users` | email (unique), hashed_password, role, is_active, is_email_verified, token_version, refresh_token_hash, failed_login_attempts, lockout_count, locked_until, totp_secret (Fernet), totp_pending_secret, totp_last_counter, session_started_at, last_seen_at |
| `loan_applications` | applicant_id, status (9), product_type, loan amount/purpose, credit_score, business fields, individual fields (employment, DOB, experience, income, existing EMIs), preferences (JSONB, optional, never scored), reviewed_by, review_notes, reviewed_at, appeal_reason |
| `pre_qualification_results` | application_id (unique), score, decision, rule_breakdown (JSONB), ran_at |
| `documents` | application_id, document_type, file_name, file_path, sha256 |
| `chat_sessions` / `chat_messages` | user_id, session_id, role, content, intent |
| `audit_events` | event_type, user_id, request_id, payload (JSONB) — append-only |

---

## 8. Configuration reference

All settings come from environment variables (`.env` locally, `env_file` in Compose). Full list with defaults:
`.env.example`. The important ones:

| Variable | Default | Purpose |
|---|---|---|
| `JWT_SECRET_KEY` | — (required, ≥ 32 bytes) | token signing |
| `DATA_ENCRYPTION_KEY` | derived from JWT secret | Fernet key for TOTP secrets |
| `GROQ_API_KEY` / `GROQ_MODEL` / `ROUTER_MODEL` | — / `openai/gpt-oss-20b` / `qwen/qwen3.8-27b` | LLMs |
| `JINA_API_KEY` / `EMBEDDING_MODEL` | — / `jina-embeddings-v3` | embeddings |
| `COHERE_API_KEY` / `RERANKER_MODEL` / `RERANK_MIN_SCORE` | — / `rerank-v3.5` / `0.0` | reranking |
| `GROQ_DAILY_BUDGET_TOKENS` | `50000` | per-borrower daily cap (0 = unlimited) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` / `REFRESH_TOKEN_EXPIRE_HOURS` | 5 / 1 | token lifetimes |
| `SESSION_IDLE_MINUTES` / `SESSION_MAX_HOURS` | 15 / 12 | server-side session limits |
| `MAX_FAILED_LOGINS` / `MAX_FAILED_OTP` / `LOCKOUT_MINUTES` / `IP_FAILED_LOGIN_LIMIT` | 5 / 5 / 15 / 20 | brute-force protection |
| `CLAIM_TIMEOUT_HOURS` | 1 | abandoned officer claims return to the queue |
| `DATABASE_URL` | `postgresql+asyncpg://loanbot:loanbot@localhost:5432/loanbot` | database |
| `SMTP_*` / `DEV_EMAIL_VERIFY` | empty / false | email |
| `LANGFUSE_*` | empty | tracing (optional) |
| `FORCE_REBUILD_VECTORSTORE` | false | one-off full re-embed |

---

## 9. Non-functional characteristics

| Aspect | Approach |
|---|---|
| Hardware | runs on a 7 GB RAM laptop with no GPU; all model work is hosted |
| Cost | ₹0 — free tiers of Groq, Jina, Cohere, Langfuse; GitHub Actions free for public repos |
| Latency | answers stream token by token; status events ("Searching policy documents…") show progress while retrieving; measured per stage in Langfuse, p50/p95 and time to first words from `eval/latency_report.py`, and gated in CI (p95 ≤ 30 s) |
| Cost control | per-borrower token budget; EMI, greetings, refusals and "not specified" answers make no LLM call; summaries cached |
| Reproducibility | every package pinned through `constraints.txt`; Docker and CI use the same Python and pins |
| Resilience | retries with `Retry-After` on all AI APIs; rerank and AI-summary fallbacks; health-gated startup |
| Privacy | PII redacted before LLMs and traces; question text hashed in the audit log |
| Integrity | row locks, unique constraints, append-only audit trigger, document SHA-256 checks |

---

## 10. Known limitations and production path

| Today | Production path |
|---|---|
| Streamlit with JS-readable cookies | React/Next.js + backend-for-frontend with httpOnly cookies |
| Rate-limit counters, IP failure counter, token budget and AI-summary cache in process memory | Redis (shared, survives restarts, multiple workers) |
| Uploads on a local Docker volume | object storage (S3 / Azure Blob) with signed URLs |
| Status emails via in-process background tasks | job queue (Celery / RQ / arq) |
| Groq free tier | Azure OpenAI (India region) or a dedicated provider contract |
| Rules-only scoring, self-reported figures | bureau-backed data and a statistical scorecard, keeping rule-based explanations |
| Small evaluation sets (9 + 9) | larger, paraphrased and held-out sets; human review |
| Docker Compose on one machine | Kubernetes / managed containers, managed PostgreSQL, secrets manager |
| Aadhaar/PAN only detected and redacted | KYC verification against official services |

---

## 11. Slide kit — key numbers and a suggested deck

**Headline numbers:** 40 endpoints · 9 pages · 7 tables · 13 migrations · 135 tests · 21 ADRs · ~1,050 evidence
chunks · recall@3 1.0 · 3 hosted AI services · 0 local models · ₹0 running cost · ~4-minute CI.

**Suggested deck (one slide each):**

1. Problem and users (borrower, officer, admin)
2. Solution overview — portal + grounded assistant (section 1)
3. Architecture (diagram 2.1)
4. Tech stack table (section 3)
5. Frontend — role-aware UX and AI built into the application page (4.1)
6. Backend API and middleware (4.2)
7. Data and concurrency — status machine, row locks, append-only audit (4.3)
8. Security — authentication and account protection (4.4)
9. Explainable pre-qualification (4.5)
10. RAG ingestion — layout-aware PDFs and incremental indexing (4.6)
11. Hybrid retrieval and rerank (4.7)
12. LLM orchestration — router, grounded answers, tools, case summary (4.8)
13. Guardrails and privacy (4.9)
14. Observability and audit (4.10)
15. DevOps — Docker, CI gates, evaluation (4.14–4.16)
16. Results and limitations / production roadmap (sections 9–10)

Each slide maps to one section above; the "Slide points" lines are written to be pasted as bullets.
