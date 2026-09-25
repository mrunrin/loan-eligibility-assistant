# LoanBot — Project Context File

**Purpose:** Hand this file to any model to give it full context of this project without needing conversation history.

---

## What This Project Is

A **streaming RAG chatbot** (LoanBot) that answers questions about two business-loan agreement PDFs. Built as an EXL capstone project.

- **Demo flow:** Streamlit chat → FastAPI (`/ask/stream`) → hybrid retrieval from ChromaDB → Ollama (local) or Groq/HuggingFace (API) → streamed answer + source citations → Langfuse trace + JSONL audit log
- **Data:** Two real business-loan agreement PDFs in `data/`
  - `MBBL_Business_Loan_Agreement_New.pdf` (called "Utkarsh" in test sets)
  - `Businees-Loan-for-Entitiy(Unsecured)-Agreement.pdf` (called "Jana" in test sets)
- **Not** a synthetic eligibility policy — these are real legal agreement documents

---

## Directory Structure

```
Capstone Project/
├── app/
│   ├── config.py          # all env-var reading; single source of truth for settings
│   ├── main.py            # FastAPI app: /ask, /ask/stream, /health endpoints
│   ├── rag.py             # RAG pipeline: ingestion, retrieval, reranking, generation
│   ├── audit.py           # JSONL audit log writer
│   ├── tracing.py         # Langfuse client init
│   └── guardrails_config.py  # empty (not yet implemented)
├── frontend/
│   └── streamlit_app.py   # Streamlit chat UI with model provider selector
├── data/
│   ├── MBBL_Business_Loan_Agreement_New.pdf
│   └── Businees-Loan-for-Entitiy(Unsecured)-Agreement.pdf
├── vectorstore/           # ChromaDB persisted store (gitignored)
├── logs/                  # audit.jsonl (gitignored)
├── Dockerfile             # backend image
├── Dockerfile.frontend    # frontend image
├── docker-compose.yml     # runs backend + frontend; Ollama stays on host
├── .env.example           # template for .env (no real secrets)
├── checklist.md           # project completion checklist
└── PROJECT_CONTEXT.md     # this file
```

---

## Key Configuration (`app/config.py` + `.env`)

| Variable | Default | Purpose |
|---|---|---|
| `CHAT_MODEL` | `qwen3.5:2b` | Ollama local model |
| `EMBEDDING_MODEL` | `nomic-embed-text:latest` | Ollama embeddings |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `GROQ_API_KEY` | _(empty)_ | Groq API key |
| `GROQ_MODEL` | `openai/gpt-oss-20b` | Groq model ID |
| `HF_API_KEY` | _(empty)_ | HuggingFace API key |
| `HF_MODEL` | `mistralai/Mistral-7B-Instruct-v0.3` | HF model ID |
| `FALLBACK_MODEL_PROVIDER` | `none` | Auto-fallback: `none`, `groq`, or `huggingface` |
| `VECTORSTORE_PATH` | `./vectorstore` | ChromaDB persist dir |
| `DATA_DIR` | `./data` | PDF source dir |
| `FORCE_REBUILD_VECTORSTORE` | _(unset)_ | Set `true` to re-embed from scratch |
| `MAX_HISTORY_MESSAGES` | `12` | Conversation turns sent to backend |
| `MAX_MESSAGE_CHARS` | `2000` | Max chars per message |

---

## RAG Pipeline (`app/rag.py`) — Full Detail

### Ingestion (run once at startup)

1. Load all PDFs from `DATA_DIR` with `PyPDFLoader`
2. Clean each page (strip whitespace/version headers)
3. Split page text into **parent sections** at heading boundaries (`PARENT_HEADING_RE` regex matches numbered headings like `8. OBLIGATIONS`, `SCHEDULE`, `WHEREAS:`)
4. Each parent section → split into **child chunks** (≤450 chars, 80-char overlap) with clause-aware splitting (`CLAUSE_RE` detects `8.2`, `9.1` etc.)
5. Store child chunks in ChromaDB collection `loan_policy_clause_hybrid_v2`
6. Build in-memory BM25 index over all child chunks
7. Cache parent + child documents to `vectorstore/documents_cache.json`

### Retrieval Constants

```python
DENSE_CANDIDATE_K = 12   # top-k from ChromaDB dense similarity
BM25_CANDIDATE_K  = 12   # top-k from BM25 lexical search
FINAL_EVIDENCE_K  = 5    # evidence chunks passed to the model
RRF_K             = 60   # RRF denominator
```

### Retrieval Flow

```
question
  → _decompose_question()         # split "Would X... and what Y?" into sub-queries
  → for each sub-query:
      ChromaDB similarity_search(k=12)   # dense semantic
      _bm25_search(k=12)                 # lexical BM25
  → _rerank_candidates()          # RRF + coverage scoring + schedule boost
  → top FINAL_EVIDENCE_K chunks   # passed to LLM prompt
```

### `_decompose_question(question)` (added to fix multi-clause answers)

Splits compound questions like *"Would X be an Event of Default, and what action can the lender take?"* into:
1. Full original question
2. First part: `"Would X be an Event of Default?"`
3. Second part: `"What action can the lender take afterward?"`

Each sub-query runs independent retrieval. Documents that appear in results for multiple sub-queries accumulate higher RRF scores.

### `_rerank_candidates()` scoring

For each candidate chunk, score = sum of:

| Component | Formula | Max contribution |
|---|---|---|
| Dense RRF | `1 / (60 + rank)` | ~0.016 per appearance |
| BM25 RRF | `1 / (60 + rank)` | ~0.016 per appearance |
| BM25 score bonus | `min(bm25_score / 20, 0.05)` | 0.05 |
| **Coverage ratio** | `(query_terms_in_doc / meaningful_query_terms) × 0.12` | 0.12 |
| Clause exact match | +0.08 if clause number appears in question | 0.08 |
| Schedule section boost | +0.04 if section is schedule/amortization/loan details | 0.04 |

After scoring, **drop any chunk scoring below 38% of the top chunk's score** to filter irrelevant noise. Always keep at least 1, at most `FINAL_EVIDENCE_K`.

Coverage ratio uses a stopword-filtered query term set (`_STOPWORDS` frozenset of ~40 common words).

### Parent-Child Lookup

After reranking, `_sources()` formats the top chunks as citations. The LLM receives the child chunk text (compact, clause-specific) but with parent section metadata (title, page, clause number) for citation display.

### Prompt (`prepare_answer`)

System instructions + 3 few-shot examples covering:
1. Positive: answer is in context → answer directly
2. Negative (table/schedule): answer is in a schedule row → still extract it, don't say "not specified"
3. Negative (clause): answer is in a clause → still extract it

Key prompt rules:
- Use only facts from policy context
- Never invent advice, requirements, next steps
- "The policy does not specify that." ONLY when genuinely absent from ALL passages
- Blank template fields (`% p.a.` with nothing before) must not override passages with real values

---

## Generation Providers (`app/rag.py`)

Three providers, selected per-request via `model_provider` field:

| Provider | When usable | Notes |
|---|---|---|
| `local` | Always (Ollama running) | `qwen3.5:2b` — small, fails on complex multi-source questions |
| `groq` | `GROQ_API_KEY` set | `openai/gpt-oss-20b` — fast, handles complex questions well |
| `huggingface` | `HF_API_KEY` set | `mistralai/Mistral-7B-Instruct-v0.3` — not tested |

**Provider readiness check (`_provider_ready`):** validates API key is set *before* any network call. If not ready, shows `⚠️ <reason>. Falling back to local Ollama.` in the chat stream.

**Streaming:** Groq uses SSE streaming via `requests(stream=True)`. HuggingFace uses batch call then token-by-token yield. Local uses LangChain `ChatOllama.stream()`.

**Fallback on failure:** if chosen provider fails mid-stream, the chat shows `⚠️ Provider failed. Falling back to local Ollama.` and continues with local.

---

## FastAPI Backend (`app/main.py`)

### Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Health ping |
| `/health` | GET | Returns `vectorstore_ready`, `ollama_ready`, model names |
| `/ask` | POST | Non-streaming answer (used for testing) |
| `/ask/stream` | POST | SSE streaming answer (used by Streamlit) |

### Request Schema (`AskRequest`)

```python
question: str           # 1–2000 chars, stripped
history: list[Message]  # up to 12 messages, role = "user"|"assistant"
session_id: str | None  # for audit log correlation
model_provider: str     # "local" | "groq" | "huggingface"
```

### SSE Event Format

```
event: token
data: {"text": "<token>"}

event: done
data: {"answer": "...", "source": "...", "sources": [...]}

event: error
data: {"message": "..."}
```

### Startup (lifespan)

1. `initialize_vectorstore()` — loads/builds ChromaDB + BM25 index
2. Ollama warmup — sends a test prompt with `OLLAMA_WARMUP_TIMEOUT` second timeout

---

## Streamlit Frontend (`frontend/streamlit_app.py`)

- `st.columns([1, 4])` layout: compact provider dropdown + `st.chat_input` side by side
- Provider dropdown (`label_visibility="collapsed"`) shows: `🖥️ Local (Ollama)`, `⚡ Groq`, `🤗 Hugging Face`
- `model_provider` sent in JSON body of every request
- On 422 response: shows raw validation error
- On timeout (ReadTimeout): friendly message about local model loading
- Source citations in `st.expander("View Sources")` showing document, page, section, excerpt

---

## Audit Log (`app/audit.py`)

Appends one JSON line per request to `logs/audit.jsonl`:

```json
{
  "timestamp": "2026-09-25T10:00:00Z",
  "request_id": "<uuid>",
  "session_id": "<uuid>",
  "question_hash": "<sha256>",  // hashed, not plaintext
  "status": "completed|failed",
  "latency_ms": 1234,
  "source_pages": [8, 2]
}
```

---

## Docker Setup

- `docker-compose.yml` runs `backend` (port 8000) and `frontend` (port 8501)
- Ollama runs **on the host**, not in Docker — backend reaches it via `host.docker.internal:11434`
- ChromaDB and audit logs use named Docker volumes (`vectorstore`, `audit_logs`)
- `.env` file is bind-mounted via `env_file:` in the backend service

---

## Known Issues / Pending Work

### Retrieval

- **Multi-clause answers** (e.g. "Is X an Event of Default, and what can the lender do?"): requires Clause 9.1(d) AND Clause 9.3(a) — `_decompose_question` was added to address this but not yet verified to work
- **Schedule page entries** (interest rate, processing fee, SMA/NPA data) were historically outscored by clause prose from page 2 — fixed by: wider candidate pool (12+12) + +0.04 schedule section boost
- **Blank template fields** (e.g. `% p.a.` with no value before it) confused the local model — fixed by explicit prompt rule

### Generation

- `qwen3.5:2b` (local) frequently says "The policy does not specify that." even when the answer is clearly in retrieved sources — especially when 3+ of 4 sources are irrelevant noise
- Groq (`openai/gpt-oss-20b`) handles these cases correctly
- HuggingFace provider: not tested end-to-end; `HF_MODEL` in `.env.example` is `mistralai/Mistral-7B-Instruct-v0.3` but user's actual `.env` may have wrong value

### Not Yet Done (from checklist)

- [ ] Automated pytest tests
- [ ] `.github/workflows/eval.yml` — CI evaluation gate (file exists but is empty)
- [ ] Promptfoo evaluation suite (26 questions exist but not in Promptfoo format)
- [ ] Groundedness check (validate generated answer is supported by retrieved sources)
- [ ] Docker end-to-end validation from clean state
- [ ] Demo rehearsal + latency measurement

---

## 26-Question Test Set (verified by human)

Questions confirmed answerable from the two PDFs. "MBBL" = `MBBL_Business_Loan_Agreement_New.pdf`, "Jana" = `Businees-Loan-for-Entitiy(Unsecured)-Agreement.pdf`.

Key questions and expected facts:

| Question | Expected answer | Source |
|---|---|---|
| What is the interest rate? | Fixed, 24% p.a. diminishing | MBBL Schedule (page 15) |
| When is EMI due? | 11th of every month | MBBL Schedule |
| How long can borrower leave India? | Not exceeding 60 days at any one time | MBBL Clause 8.2 (page 8) |
| Is stop-payment an Event of Default? | Yes, Clause 5.3 + 9.1(d); lender can demand full repayment within 7 Business Days | MBBL Clause 9.1(d) + 9.3(a) |
| What is the processing fee? | Per Schedule | MBBL Schedule |

---

## How to Run Locally

```bash
# 1. Start Ollama and pull models
ollama pull qwen3.5:2b
ollama pull nomic-embed-text

# 2. Copy and fill .env
cp .env.example .env
# Add GROQ_API_KEY if using Groq

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start backend
uvicorn app.main:app --reload

# 5. Start frontend (separate terminal)
streamlit run frontend/streamlit_app.py
```

## How to Run with Docker

```bash
docker compose up --build
# Ollama must already be running on the host
```

---

## Important Code Locations

| What | File | Lines |
|---|---|---|
| Retrieval constants | `app/rag.py` | 54–57 |
| Query decomposition | `app/rag.py` | `_decompose_question()` |
| Reranking + coverage scoring | `app/rag.py` | `_rerank_candidates()` |
| Provider routing + fallback | `app/rag.py` | `stream_answer()`, `answer_question()` |
| Provider readiness check | `app/rag.py` | `_provider_ready()` |
| Few-shot prompt | `app/rag.py` | `prepare_answer()` |
| Request schema + provider validation | `app/main.py` | `AskRequest` class |
| SSE streaming endpoint | `app/main.py` | `ask_stream()` |
| Provider dropdown UI | `frontend/streamlit_app.py` | `_model_col, _` columns block |
