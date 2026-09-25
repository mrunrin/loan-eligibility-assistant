# Loan Eligibility Assistant: Capstone Completion Checklist

Last reviewed: 2026-09-23

## Current State And Resume Notes

This checklist is the project's single planning and continuation document. It combines the completion plan with the current architecture, decisions, and the next work to resume.

**Current demo flow:** Streamlit chat -> FastAPI (`/ask`, `/ask/stream`, `/health`) -> Hybrid Retrieval (ChromaDB + Custom BM25) -> Cross-Encoder Reranking (`BAAI/bge-reranker-base`) -> Prompt Injection -> Ollama (`qwen3.5:2b`) or configured Groq/Hugging Face fallback -> answer and citations -> Langfuse trace and JSONL audit record.

**Current data:** `data/` contains `MBBL_Business_Loan_Agreement_New.pdf` and `Businees-Loan-for-Entitiy(Unsecured)-Agreement.pdf`. These are business-loan agreement PDFs; the former synthetic `loan_eligibility.pdf` is no longer in the working tree. Treat claims as limited to these documents and do not describe the data as a complete eligibility policy.

**Retrieval status:** `app/rag.py` builds parent sections using regex heading splits, and now splits child chunks using NLTK **sentence-level chunking** to ensure legal sentences are kept whole. Retrieval uses a hybrid approach (ChromaDB dense search + BM25 lexical search) with a candidate pool of 40 (20 each). Candidates are then reranked using a neural Cross-Encoder (`BAAI/bge-reranker-base`) to select the top 5 most semantically relevant chunks. 

**Current next step:** Address the issue with letter-based sub-clauses in the Jana document (e.g., `p)`, `q)`, `r)`). Currently, `CLAUSE_RE` only matches numeric clauses (like `9.1`), causing letter-based clauses to inherit the parent section's title, which can be truncated mid-sentence and mismatch the actual child text. After fixing this, proceed to Promptfoo evaluations.

**Architecture decisions to retain:** keep Ollama outside Docker so model weights do not inflate the app images; Docker Compose runs frontend and backend. ChromaDB remains the local vector store for dense retrieval, supplemented by the local BM25 index. The Cross-Encoder reranker runs locally via `sentence-transformers` and caches models on the host machine. Hosted generation fallback is optional and selected by environment configuration.

## Capstone Goal

Demonstrate that a streaming RAG chatbot can answer questions about a loan eligibility policy, show supporting sources, record auditable request metadata, and catch answer regressions before code is merged.

**Business problem:** Loan pre-qualification takes staff time and can produce inconsistent answers.

**Required build:** Streaming chat over eligibility rules, FastAPI backend, Promptfoo evaluation gates in CI/CD, and audit logs.

**Success measures:** Eligibility-answer accuracy, faithfulness to retrieved evidence, regression-blocking behavior, and response latency.

## Existing Foundation

- [x] FastAPI backend with `/ask`, `/ask/stream`, and `/health` endpoints.
- [x] Streamlit chat UI with streamed answers and browser-session conversation history.
- [x] ChromaDB dense retrieval over two business-loan agreement PDFs in `data/`.
- [x] Parent-section and child-text chunking exists; clause-aware child splitting is not complete.
- [x] Ollama model and embeddings configured; optional Groq/Hugging Face generation fallback exists.
- [x] Dockerfiles and Docker Compose for the frontend and backend; Ollama stays outside the containers.
- [x] Langfuse integration and JSONL audit logging are present.
- [ ] Automated tests are not present yet.
- [ ] `.github/workflows/eval.yml` is empty; no CI evaluation gate exists yet.
- [ ] Groundedness validation is not connected between generation and response.
- [ ] Source relevance is not yet reliable for every question; page citations can show irrelevant text.

## Work Plan

### 0. Review The Current Evidence Pipeline

- [ ] Choose representative questions from both agreement PDFs, including exact clause-number queries and questions requiring multiple clauses.
- [ ] Record retrieved child/parent text, source file, page, and relevance for each question before changing retrieval.
- [ ] Review the all-parent fallback on retrieval failure; ensure a retrieval error cannot be mistaken for relevant policy evidence.
- [ ] Preserve each source PDF's page metadata and identify where PDF extraction merges, drops, or misorders clause text.

**Done when:** the team can point to the retrieval stage that introduces each irrelevant citation and has a baseline to compare against.

### 0.1 Improve Clause Retrieval

- [x] Split child chunks by clause identifiers (for example 4.1, 4.2) under their main section when the source structure supports reliable detection; retain a bounded text-split fallback for unnumbered content. *(Implemented sentence-aware splitting)*
- [x] Add BM25 lexical retrieval alongside Chroma dense retrieval so legal terms, defined phrases, and clause numbers can match exactly.
- [x] Merge and rerank candidates using the selected local approach; keep the candidate count and final evidence count small and configurable. *(Implemented Cross-Encoder reranking)*
- [x] Provide only selected evidence to generation and show citations for those evidence passages, including document, page, and clause/section metadata when available.
- [ ] Evaluate hybrid retrieval and reranking against the recorded dense-only baseline, including latency and whether the cited passage actually supports the answer. *(Partially done manually, pending Promptfoo suite)*

**Done when:** targeted questions retrieve the supporting clause, irrelevant passages are excluded from displayed sources, and answer quality/latency are compared with the baseline.

### 1. Agree On The Demo Data And Questions

- [ ] Confirm the two business-loan agreements are the intended demo data and label/document them accurately; do not call them a synthetic eligibility policy.
- [ ] Review the relevant clauses and agree on the facts the assistant is allowed to state.
- [ ] Create a small, reviewed question set covering income, credit score, age, debt-to-income, tenure, self-employed applicants, follow-ups, and questions not answered by the PDF.
- [ ] For every evaluation question, write down the expected facts and the policy passage/page that supports them.

**Done when:** another team member can use the question set and independently identify the expected answer and evidence.

### 2. Make Answers And Sources Faithful

- [ ] Trace a question through clause parsing, retrieval, prompt construction, generated answer, and displayed citation to identify why irrelevant source text appears.
- [ ] Make citations point to the retrieved evidence passage and page that supports the answer; retain separate evidence passages when multiple rules are needed.
- [ ] Ensure retrieval failure is reported as a retrieval failure rather than silently presented as relevant whole-document evidence.
- [ ] Add a standalone groundedness check between generation and response formatting.
- [ ] Choose and document one failure behavior: return a clear safe answer when generated claims are unsupported.
- [ ] Keep personal chat memory distinct from policy facts and policy citations.

**Done when:** the salary question cites the income rules, the credit-score question cites the score rule, and unsupported policy questions do not receive invented requirements.

### 3. Add Automated Checks

- [ ] Add pytest tests for retrieval/source formatting, memory behavior, groundedness pass/fail, and provider/error handling.
- [ ] Add FastAPI tests for request validation, `/health`, and streaming token, completion, and error events.
- [ ] Add Promptfoo evaluations using the reviewed question set.
- [ ] Score answer facts, faithfulness to policy evidence, citation relevance, and safe handling of missing information.
- [ ] Record baseline scores and agree on minimum pass thresholds before making the checks merge-blocking.
- [ ] Fill in `.github/workflows/eval.yml` to install dependencies and run tests plus Promptfoo on pull requests.
- [ ] Confirm a deliberately failing evaluation causes the GitHub check to fail; then confirm a passing change succeeds.

**Done when:** the GitHub pull-request check blocks a regression and provides enough output to understand what failed.

### 4. Verify Audit Logs And Tracing

- [ ] Confirm each request records a request ID, outcome, latency, and source pages in the audit log.
- [ ] Check that the audit record is useful for the demo and does not contain API keys or unnecessary personal conversation text.
- [ ] Confirm Langfuse failure does not prevent a user from receiving an answer.
- [ ] Capture latency consistently, including time to first streamed token and total response time.

**Done when:** one demo request can be followed from API log/audit entry to its source pages and measured response time.

### 5. Validate Local And Docker Runs

- [ ] Follow the README from a clean project start and confirm the local backend/frontend commands work.
- [ ] Run the backend and frontend with `docker compose up --build`; confirm the UI can reach FastAPI and FastAPI can reach host Ollama.
- [ ] Confirm the vector store and audit log use the intended persistent Docker volumes.
- [ ] Verify missing Ollama, slow generation, and API errors produce understandable messages in the UI.
- [ ] Ensure `.env.example` has the settings needed for a clean run and contains no real secrets.
- [ ] Update README instructions only where the tested run differs from the written steps.

**Done when:** a teammate can follow the documented setup and run the demo without relying on the developer's existing local state.

### 6. Measure And Rehearse The Demo

- [ ] Run the full Promptfoo set and report accuracy/faithfulness and citation results.
- [ ] Measure time to first token and total latency on the demo machine; state the machine/model conditions with the result.
- [ ] Rehearse one in-policy answer, a follow-up, a missing-policy question, source inspection, and a deliberate regression blocked by CI.
- [ ] Prepare a short architecture explanation: Streamlit → FastAPI → retrieval in ChromaDB → policy PDF evidence → Ollama generation → answer, citation, trace, and audit event.
- [ ] Prepare a brief explanation of why Ollama is outside Docker and what the optional hosted fallback changes.
- [ ] List known limitations honestly: synthetic policy, small evaluation set, local/single-instance vector store, and no real lending decisions.

**Done when:** the demo runs end to end and the team can show measured results for all four success measures.

## Recommended Stack For This Capstone

- **Streamlit:** fastest way to demonstrate the chat experience and source display.
- **FastAPI:** clear API boundary, request validation, health endpoint, and streaming transport.
- **LangChain + ChromaDB + BM25 + CrossEncoder:** PDF ingestion, local dense retrieval, BM25 hybrid search, and `bge-reranker-base` cross-encoder reranking for maximum precision on legal agreements.
- **NLTK:** robust sentence-aware chunking to preserve legal boundaries.
- **Ollama:** local model and embeddings without packaging model weights into Docker images.
- **Promptfoo + GitHub Actions:** repeatable question-based evaluations and a visible merge-blocking regression check.
- **Langfuse + JSONL audit log:** trace inspection and a simple record of request outcome, latency, and source pages.
- **Docker Compose:** repeatable local deployment of frontend and backend while Ollama remains a host service.

Keep this stack for the capstone. A managed database, Kubernetes, customer authentication, and a production frontend are outside the required demo scope unless the mentor or panel specifically asks for them.

## Priority Order

1. Agree on policy facts and the evaluation questions.
2. Fix evidence and citation relevance; add groundedness validation.
3. Add pytest and Promptfoo checks, then make the GitHub workflow block regressions.
4. Verify audit/tracing behavior and latency measurements.
5. Rehearse the documented local and Docker runs.
6. Present the results, architecture, and known limitations clearly.
