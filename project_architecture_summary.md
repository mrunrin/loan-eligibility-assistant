# Architecture Summary: LoanMitra Portal + LoanMitra Assistant

## Overview
A loan origination portal (business loan, personal loan and credit card applications, deterministic
pre-qualification, officer review, admin) with **LoanMitra**, a grounded assistant that answers from the
policy knowledge base — 5 HDFC PDFs (personal and business loan agreements, credit card agreement, MITC and FAQ) + 4 HDFC/RBI
MD files + 1 LoanMitra portal policy MD file — with clause-level citations, and can also explain the borrower's own
application (or, for the reviewing officer or an admin, the application under review) or compute an EMI.
Student capstone demo; not an official HDFC Bank service.

---

## 1. Frontend (Streamlit, multi-page)
- `frontend/streamlit_app.py` — login (password → TOTP step, or first-login 2FA enrollment for staff),
  email verification, FAQ.
- `frontend/pages/` — Register, My Applications, Apply (product picker + 4-step form with optional product
  preferences and a required-document checklist), Application Detail (score, authorised document preview (image / PDF) and download, appeal,
  withdraw, officer decision; the pre-qual explanation depends on the role — borrower: a 🤖 "Ask here" card that
  opens the assistant on this application (approved: a fixed "you will hear from us by email" message);
  officer: an "AI generated summary" dropdown; admin: the full rule breakdown), Officer Queue (claim / take over
  → decide; "Approved / Rejected by me" tabs), Admin Dashboard (stats, roles, activate/deactivate; other admins
  shown as protected), AI Loan Assistant, Security (2FA, account deletion). My Applications shows a Chat button
  for every status except draft, approved and withdrawn.
- `frontend/nav.py` — the single place for `API_URL`, the authenticated `api()` helper (bearer token,
  forwards the client IP, one refresh-and-retry on 401, backend-down handling), page/role guards, sidebar,
  logout and the 5-minute browser idle timer.
- Tokens live in `st.session_state`, plus JS-readable cookies as a best-effort restore; a page refresh usually
  returns to the login page (kept on purpose). See Known Limitations in the README.

## 2. Backend (FastAPI)
- Routers: `auth` (register/login/refresh/logout/verify/delete), `mfa` (TOTP), `applications`
  (CRUD, submit, documents, withdraw, appeal), `prequal`, `officer`, `admin`; chat endpoints `/ask`,
  `/ask/stream`; `/health`.
- Middleware: TrustedHost → CORS (no credentials; bearer auth only) → request-ID → shared rate limiter
  (`app/limiter.py`, `user_or_ip` key — per-user bucket for authenticated endpoints, per-client-IP
  via trusted `X-Forwarded-For` for anonymous; `TRUSTED_PROXIES` CIDRs configured in `.env`).
  Rate limits: `/ask` + `/ask/stream` 20/min · login/register 10/min · token refresh 30/min
  · resend verification 3/4 h · MFA setup/confirm/validate 5–10/min.
- `/health` is 200 only when PostgreSQL and the vector store are ready.

### Cold-start sequence (blocks traffic until all 4 steps complete)
```
[1/4] Running DB migrations...          ← Alembic upgrade head (psycopg2, sync, advisory-locked)
[1/4] DB migrations applied ✓
[2/4] Reranker: Cohere API (rerank-v3.5) ✓  ← warns instead if COHERE_API_KEY is missing
[3/4] Loading vectorstore (Jina jina-embeddings-v3 embeddings)...  ← stops with a clear error if Jina fails
[3/4] Vectorstore ready ✓
[4/4] Server ready — accepting requests ✓
```
All `print(..., flush=True)` — always visible in the terminal regardless of log level.
The `lifespan` coroutine only `yield`s (opens for traffic) after step 4. A corrupt Chroma collection is
wiped and rebuilt once automatically.

## 3. Data layer
- PostgreSQL 15, SQLAlchemy 2 async (asyncpg) at runtime; Alembic (psycopg2) migrations `001`–`013`,
  applied automatically at startup (step 1) under a Postgres advisory lock.
- Tables: users, loan_applications, pre_qualification_results, documents, chat_sessions, chat_messages,
  audit_events. Officer-queue index on `loan_applications(status, created_at)`.
- Status machine: draft → (submit) pre_qual_pass | pre_qual_fail | manual_review → (officer claim)
  under_review → approved | rejected; pre_qual_fail → (borrower appeal with written reason) manual_review;
  `POST /applications/{id}/withdraw` from any state before a decision. Claims untouched for
  `CLAIM_TIMEOUT_HOURS` (1) can be taken over. Status writes take row locks.
- Submit needs the per-product document checklist (`REQUIRED_DOCUMENTS`). Every status change emails the
  borrower (failed rules listed in writing on pre_qual_fail). Documents are stored under random names with a
  SHA-256 hash checked on each download.
- Pre-qualification (`app/prequal.py`): per-product weighted rules plus knockout rules; self-reported inputs
  are flagged `unverified` in the stored JSONB breakdown.
- Product preferences (`loan_applications.preferences`, JSONB, migration 013): optional choices such as facility
  type, tenure, card type, add-on card and Credit Protect. Validated against fixed values, never scored, shown on
  the application page and passed to the assistant so it can explain them from the policy documents.
- Officer case summary (`app/case_summary.py`, `GET /prequal/{id}/summary`, officers only): one Groq call turns
  the rule results (amounts banded) into 5–6 lines, cached per pre-qual run, with a plain rules summary as the
  fallback. It explains the result; it never changes the score or the decision.
- The assistant's application context gives each status a meaning, a next step and a typical timeline
  ("a few working days" until an officer picks it up, "4–7 working days" for a decision under review).

## 4. Security model
- bcrypt passwords; JWT access (5 min) + rotating refresh (1 hour, hash stored; reuse revokes the session); every token carries `iss`/`aud`.
- Sessions end server-side after 15 idle minutes or 12 hours total (`users.last_seen_at`, `session_started_at`).
- Lockout after 5 failed passwords or TOTP codes, doubling on each repeat (15 min → 24 h max), with an email to the owner;
  one IP gets 429 after 20 failed logins in 15 min, across all accounts; email OTP from a CSPRNG, stored as
  an HMAC, burned after repeated failures.
- TOTP secrets Fernet-encrypted at rest; codes cannot be replayed; enabling 2FA uses a pending secret so
  `/2fa/setup` can never switch existing 2FA off. 2FA is mandatory for officers and admins.
- Admin actions: no super-admin. An admin cannot change the role of, or deactivate, themselves (400) or another
  admin (403); promoting a user to admin and re-activating an inactive admin are allowed. A deactivated user is
  rejected on their next request.
- Uploads: extension + magic-byte check, streamed 10 MB cap, draft-only, base-name sanitisation, orphan
  cleanup on failure, SHA-256 integrity check on download; downloads only for the owner or officers/admins.
- Audit: request-level JSONL log (question stored only as a hash) + `audit_events` rows for logins
  (success and failure, with IP), lockouts, refresh-token reuse, 2FA changes, document upload/download/
  integrity failures, claims, decisions, appeals, withdrawals, role/active changes, account deletion.
  A Postgres trigger makes `audit_events` append-only (no UPDATE/DELETE/TRUNCATE).

## 5. Chat assistant pipeline
- **Input**: `AskRequest` removes personal identifiers (Aadhaar with Verhoeff check, PAN, card with Luhn
  check, phone, email) at the API boundary, so they never reach a model, trace, log or stored history.
- **Precheck** (`policy_qa.precheck`): prompt-injection and off-topic refusals on the question and every
  history turn; greetings and name memory answered without a model.
- **Route** (`assistant.route`): one `ROUTER_MODEL` call (JSON mode) returns a standalone rewrite, an intent
  (`policy` / `my_application` / `emi` / `small_talk` / `out_of_scope`) and EMI slots; output is validated.
- **Tools**: policy search; application data scoped by SQL on the JWT user — a borrower's own applications, and for
  one selected application also the officer who claimed or decided it and any admin (reviewers also get the typed
  figures, document checklist and appeal; the prompt lists evidence for and against and never recommends a decision;
  each reviewer use writes an `assistant_application_viewed` audit event; user-typed fields quoted as inert data,
  names masked, amounts banded); deterministic EMI calculator.
- **Ingestion** (`app/doc_pipeline.py` + `app/chunking.py` + `app/retrieval.py`): PyMuPDF column-aware text with table regions
  removed, page classification (blank pages skipped), pdfplumber tables merged across pages and serialized
  as row chunks, clause-aware parent/child chunks (NLTK sentence splitting for long clauses). MD source headers
  become chunk metadata (`product`, `publisher`, `effective_date`).
- **Index**: ChromaDB, stable collection `loan_policy_v1`; per-file manifest (`ingest_manifest.json`)
  tracks SHA-256 hashes so only new/changed files are re-embedded on startup. A `CHUNKER_VERSION` bump
  (currently `v4`) wipes and rebuilds all; `FORCE_REBUILD_VECTORSTORE=true` does it on demand.
- **Retrieval**: dense (Jina `jina-embeddings-v3` with query / passage task adapters) + BM25,
  compound-question decomposition, Cohere rerank (`rerank-v3.5`), metadata filters (lender named in the question; product of the selected
  application), relevance floor (`RERANK_MIN_SCORE`) below which the bot says the policy does not specify the answer.
- **Generation**: top 3 passages as labelled, quoted evidence `[S1..S3]` with their publisher; the model must
  cite inline and credit HDFC Bank vs RBI; passages and user data are treated as data, not instructions.
  Retrieval failures return an explicit refusal.
- **Output**: PII redaction against the full evidence text on both `/ask` and `/ask/stream` (sentence-buffered).
- **Memory**: conversation history in `chat_messages` per (user, session); clients cannot inject turns.
- **Budget**: per-borrower daily token budget from Groq's reported usage (router + answer), reset at
  midnight IST; officers and admins unlimited.

## 6. Model providers
- Groq — `openai/gpt-oss-20b` answers; `qwen/qwen3.8-27b` rewrites and routes. 429s are retried.
- Jina — `jina-embeddings-v3` embeddings, needed at startup and for every policy question.
- Cohere — `rerank-v3.5` reranking; if it fails, answers use unranked passages and a warning is logged.
- No model runs locally.

## 7. Observability & evaluation
- Langfuse: one trace per chat request — `rewrite-and-route` generation, `hybrid-retrieval` with a nested
  `rerank` span, `llm-generation` — with token usage on each generation; all inputs/outputs pass through
  the PII mask before export. Streaming traces carry `ttft_ms`; eval traffic goes to the Langfuse `eval`
  environment. `eval/latency_report.py` gives p50/p95 latency and time to first words from the audit log.
- `eval/retrieval_eval.py` — recall@3 and MRR on 9 graded golden questions with gold pages (CI-gated).
- `promptfooconfig.yaml` + `eval/questions.yaml` — 9 answer-level cases with a deterministic numeric
  faithfulness check on every answer; CI gates on pass rate (`eval/check_pass_rate.py`).
- `app/tests/` — unit tests (pre-qualification engine, guardrails, chunking, Jina/Cohere clients) and API
  integration tests against PostgreSQL.

## 8. Deployment
- `docker-compose.yml`: postgres, backend (no local models, non-root, healthcheck on readiness), frontend,
  and an opt-in `seed` profile. API published on loopback only.
- `.github/workflows/eval.yml`: lint → migrations → pytest → retrieval gate → backend → promptfoo gate (pass rate + p95 latency).
