# Project Guide

This guide explains how the Loan Eligibility Assistant works, what each file does, how to run it, how to switch models, and how to debug the common issues that appear during local development.

## What This Project Does

The project is a local chatbot for banking loan eligibility questions.

The user asks a question in the Streamlit UI. The frontend sends the question and chat history to the FastAPI backend. The backend retrieves relevant policy text from a local ChromaDB vector store, sends that context to a local Ollama model, and returns a grounded answer with source citations.

The application is designed to stay free and local. It does not require paid LLM APIs.

## Main Runtime Flow

```text
1. User types a question in Streamlit.
2. Streamlit stores the message in session state.
3. Streamlit sends question, history, and session_id to FastAPI.
4. FastAPI validates the request using Pydantic.
5. The backend checks whether the question can be answered directly from conversation memory.
6. If it is a policy question, the backend searches ChromaDB for relevant PDF chunks.
7. The retrieved chunks are inserted into the LLM prompt.
8. Ollama generates the answer locally.
9. FastAPI returns the answer and citations.
10. Streamlit displays the bot response and sources.
```

## File Overview

### `app/main.py`

This is the FastAPI entry point.

It creates the API app, defines request models, validates incoming questions, exposes health routes, handles normal responses, handles streaming responses, writes audit logs, and wraps calls with Langfuse tracing.

Important routes:

- `/` confirms the app is running.
- `/health` reports model, embedding model, vector store readiness, and Ollama readiness.
- `/ask` returns a normal JSON answer.
- `/ask/stream` streams tokens so the frontend can show the answer word by word.

If this file is missing, there is no backend API for Streamlit to call.

### `app/rag.py`

This is the brain of the assistant.

It loads the PDF, chunks the document, creates or loads the ChromaDB vector store, retrieves relevant chunks, builds prompts, calls Ollama, handles simple small talk, and handles conversation memory such as the user's name.

Important functions:

- `get_chat_model()` creates the Ollama chat model.
- `initialize_vectorstore()` prepares ChromaDB.
- `prepare_answer()` decides whether to answer directly or use RAG.
- `answer_question()` returns a complete answer.
- `stream_answer()` streams the answer token by token.

If this file is weak, the model may answer vaguely, ignore history, or hallucinate outside the policy.

### `app/config.py`

This centralizes settings.

It reads environment variables for model names, Ollama URL, PDF path, vector store path, audit log path, and history limits.

The most important values are:

```env
CHAT_MODEL=qwen3.5:2b
EMBEDDING_MODEL=nomic-embed-text:latest
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_WARMUP_TIMEOUT=20
```

If this file has a wrong model name, the app may fail even when Ollama is running.

### `app/tracing.py`

This creates the Langfuse client.

Langfuse is used for observability. The app is written so Langfuse failures should not stop the chatbot.

If Langfuse credentials are missing, the core app can still run.

### `app/audit.py`

This writes local JSONL audit logs.

It stores metadata such as request ID, session ID, hashed question, status, latency, and source pages. It avoids storing raw user questions in the audit log.

This helps explain auditability during a demo.

### `frontend/streamlit_app.py`

This is the chatbot UI.

It stores conversation history in `st.session_state`, sends requests to FastAPI, streams the assistant response, and shows citations inside "View Sources".

If the browser refreshes, Streamlit session state resets. That is acceptable for this demo unless persistent chat history is added later.

### `data/loan_eligibility.pdf`

This is the policy knowledge source.

The RAG system can only answer policy questions that are supported by this PDF. If the PDF is thin, answers will be limited.

To improve answer quality, improve this PDF first.

### `vectorstore/`

This is the local ChromaDB storage folder.

It is generated from the PDF and should not be committed to GitHub. If the PDF changes, the vector store may need to be rebuilt.

### `Dockerfile`

This packages the FastAPI backend.

The backend image does not include Ollama models. Ollama should run on the host machine, and the container connects to it through `host.docker.internal`.

This keeps the Docker image smaller and avoids copying large local models into the image.

### `Dockerfile.frontend`

This packages the Streamlit frontend. It installs only Streamlit and Requests, copies the frontend folder, and runs Streamlit on port `8501`.

### `docker-compose.yml`

This runs the backend and frontend containers together. The frontend calls the backend through the Docker service name `backend`, while the backend calls host Ollama through `host.docker.internal`.

### `.env.example`

This shows the environment variables a developer can copy into `.env`. Real API keys should only go in `.env`, not in Git.

### `requirements.txt`

This is used for local development.

Install it inside the virtual environment:

```powershell
pip install -r requirements.txt
```

### `requirements-docker.txt`

This is used for Docker builds if the Dockerfile references it.

It can be slimmer than `requirements.txt` by excluding local-only tools.

### `.gitignore`

This prevents secrets and generated files from being committed.

Important ignored items:

```text
.env
capstone/
vectorstore/
logs/
__pycache__/
```

## Local Run Commands

Terminal 1:

```powershell
ollama serve
```

Terminal 2:

```powershell
.\capstone\Scripts\activate
uvicorn app.main:app --reload
```

Terminal 3:

```powershell
.\capstone\Scripts\activate
streamlit run frontend/streamlit_app.py
```

Open:

```text
http://localhost:8501
```

## Docker Run Commands

Start Ollama on the host:

```powershell
ollama serve
```

Run backend and frontend together:

```powershell
docker compose up --build
```

Stop backend and frontend:

```powershell
docker compose down
```

Run only the backend container:

```powershell
docker build -t loan-eligibility-api .
docker run --rm -p 8000:8000 -e OLLAMA_BASE_URL=http://host.docker.internal:11434 loan-eligibility-api
```

## How To Switch Models

First check exact installed model names:

```powershell
ollama list
```

Then set the model in `.env`:

```env
CHAT_MODEL=qwen3.5:2b
```

The value must exactly match the model tag from `ollama list`.

Restart FastAPI after changing `.env`:

```powershell
uvicorn app.main:app --reload
```

Test the model directly before testing the app:

```powershell
ollama run qwen3.5:2b "Reply only: ready"
```

If this command is slow, the application will also be slow.

## API Fallback

The primary model path is Ollama:

```text
FastAPI -> Ollama -> qwen3.5:2b
```

The fallback path is optional:

```text
FastAPI -> Groq or Hugging Face API
```

Fallback variables:

```env
FALLBACK_MODEL_PROVIDER=none
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
HF_API_KEY=
HF_MODEL=mistralai/Mistral-7B-Instruct-v0.3
```

Fallback helps when local generation fails. It does not put any API key into Docker images; keys are injected at runtime through `.env`.

## Thinking Mode Notes

Some Qwen-style models support a thinking or reasoning mode. If thinking is enabled, responses can become much slower.

The application creates the chat model in `app/rag.py`:

```python
def get_chat_model() -> ChatOllama:
    return ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL, reasoning=False)
```

If the installed `langchain_ollama` version does not support `reasoning=False`, the backend may raise an error. In that case, either remove the parameter or use the parameter supported by that version.

For a live demo, prefer stable speed over maximum reasoning capability.

## Recommended Demo Model Choice

Recommended:

```env
CHAT_MODEL=qwen3.5:2b
EMBEDDING_MODEL=nomic-embed-text:latest
```

Use this as the current project model after confirming:

- RAM usage stays reasonable.
- `ollama run qwen3.5:2b "Reply only: ready"` is fast.
- `/health` returns a usable backend state.
- Streamlit does not timeout on the first policy question.

## Common Problems And Fixes

### Streamlit says backend is unavailable

Check FastAPI:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Check Ollama:

```powershell
ollama list
```

Test the model:

```powershell
ollama run qwen3.5:2b "Reply only: ready"
```

Most likely causes:

- FastAPI is not running.
- Ollama is not running.
- The model name in `.env` is wrong.
- The model is loading too slowly and Streamlit timed out.
- RAM is too full.

### FastAPI is stuck at startup

The backend may be warming up the LLM. Larger or thinking-capable models can delay startup.

If this happens with `qwen3.5:2b`, test the model directly in Ollama and consider making the warmup non-blocking.

### Answers are vague

Most vague answers come from one of these:

- The PDF does not contain enough policy detail.
- The retrieved chunks are not relevant.
- The prompt is too loose.
- The model is too small for the wording of the question.

The best first fix is usually improving `data/loan_eligibility.pdf`.

### The bot forgets the user's name

The frontend must send full chat history to the backend. The backend can only remember messages that Streamlit sends in `history`.

Streamlit session state is browser-session memory. It disappears after refresh.

### ChromaDB seems stale

Stop the backend and delete:

```text
vectorstore/
```

Then restart FastAPI. The vector store will rebuild from the PDF.

### Docker cannot reach Ollama

When backend runs inside Docker and Ollama runs on Windows host, use:

```powershell
-e OLLAMA_BASE_URL=http://host.docker.internal:11434
```

Inside Docker, `localhost` means the container itself, not the host machine.

## What Is Done So Far

- Project skeleton
- Loan eligibility PDF ingestion
- ChromaDB vector store
- Ollama local chat and embedding models
- FastAPI backend
- `/ask` endpoint
- `/ask/stream` endpoint
- `/health` endpoint
- Streamlit chatbot frontend
- Session-based conversation history
- Source citations
- Langfuse wrapper
- Local audit logging
- Docker backend and frontend support
- GitHub repository setup

## What Can Be Improved Next

- Add Promptfoo evaluations for accuracy and faithfulness.
- Add GitHub Actions with eval gates.
- Add richer loan policy content to the PDF.
- Add persistent chat history with SQLite if browser refresh persistence is needed.
- Add better frontend timeout messages for slow local models.
- Add a non-blocking model warmup so FastAPI starts quickly even with large models.
- Add an architecture diagram image for the README.
- Add automated tests for memory, small talk, and policy retrieval behavior.

## Best Way To Continue Development

Before changing code:

```powershell
git status
```

After changing code:

```powershell
python -m compileall app frontend
```

Run backend:

```powershell
uvicorn app.main:app --reload
```

Run frontend:

```powershell
streamlit run frontend/streamlit_app.py
```

Commit clean work:

```powershell
git add .
git commit -m "Describe the change"
git push
```

For model experiments, change one thing at a time:

```env
CHAT_MODEL=your-model-name
```

Restart backend, test `/health`, then ask one known question:

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/ask" -Method Post -ContentType "application/json" -Body '{"question":"What credit score is required?","history":[]}'
```
