# Project Summary: LoanBot (EXL Banking Assistant)

## Overview
**LoanBot** is a specialized Retrieval-Augmented Generation (RAG) AI assistant built to parse, retrieve, and accurately answer complex questions based on unstructured banking and loan policy PDFs. The system prioritizes local, private inference while maintaining the ability to fall back to cloud-based LLMs for faster processing.

---

## 1. Frontend (User Interface Layer)
- **Framework:** Built entirely in **Streamlit**.
- **Interface:** A modern, sleek chat interface featuring a native chat layout.
- **Features:** 
  - Real-time token streaming.
  - Live status spinners that explicitly show backend progress (e.g., "Retrieving chunks", "Reranking with Cross-Encoder").
  - A bottom-pinned chat bar with an embedded Model Selector.
  - An expandable "View Sources" UI that cites the exact PDF name, page number, section, clause, and raw text excerpt the AI used.

---

## 2. Backend (API Layer)
- **Framework:** **FastAPI** providing high-performance async endpoints.
- **Endpoints:**
  - `/ask/stream`: Handles real-time Server-Sent Events (SSE) to stream both processing statuses and generated LLM text to the UI.
  - `/ask`: A synchronous JSON endpoint for programmatic queries.
  - `/health`: Exposes readiness states for the vector database and the local Ollama daemon.
- **Observability & Auditing:**
  - **Langfuse Integration:** Traces inputs, outputs, LLM latency, and token generation.
  - **Audit Logger:** A custom tracker (`app/audit.py`) that logs every request ID, session ID, question hash, latency, and source pages into a local `audit.jsonl` file.

---

## 3. RAG Pipeline (Data & Retrieval Layer)
The core intelligence of LoanBot relies on a highly sophisticated Hybrid Retrieval engine.

- **Ingestion & Chunking:**
  - **PyPDF2** parses text from complex legal agreements.
  - **Regex Heading Extraction:** Scans for legal section headers and clauses (e.g., "9.1", "p)") to ensure chunks retain their legal context.
  - **NLTK `sent_tokenize`:** Lightning-fast, statistical sentence-aware chunking that intelligently splits documents at actual punctuation marks, guaranteeing that legal sentences are never cut in half mid-word.
  - **Local Caching:** Parsed and chunked texts are cached to a local JSON file (`documents_cache.json`) for instant recovery.
- **Hybrid Retrieval Engine:**
  - **Dense Search (Semantic):** Uses local `nomic-embed-text` vectors stored in **ChromaDB** to understand the *meaning* of a question.
  - **Sparse Search (Keyword):** Uses **BM25** statistical term matching to catch exact policy limits, acronyms, and specific numbers.
  - **Cross-Encoder Reranking:** Merges both candidate pools and scores them against the user's question using a neural `BAAI/bge-reranker-base` model. This surfaces the most relevant evidence based on actual semantic reasoning, circumventing false-positive keyword matches.

---

## 4. Generative AI Models (LLM Layer)
- **Primary Processing:** Fully local, offline, and private text generation via **Ollama** (e.g., `qwen3.5:2b`).
- **Cloud Failover Routing:** Built-in ability to instantly route inference requests to the **Groq** cloud or **Hugging Face** APIs for lightning-fast speeds or heavier reasoning models (e.g., `llama3-8b-8192`), based on the frontend dropdown selection.
- **Fault Tolerance:** If a cloud provider fails or an API key is missing, the backend seamlessly prints a warning and falls back to local Ollama.

---

## 5. Deployment & State Management
- **Containerization:** Managed via `docker-compose.yml`, including volume mounts for HuggingFace model caching and ChromaDB persistence to prevent image bloat.
- **Short-term Memory:** Tracks conversational context (up to the last 12 messages) allowing the AI to smoothly handle follow-up pronouns and user profiling (e.g., remembering the user's name).
