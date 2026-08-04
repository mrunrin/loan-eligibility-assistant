# Loan Eligibility Assistant

A local AI-powered banking loan eligibility assistant built with FastAPI, Streamlit, ChromaDB, LangChain, Ollama, Docker, and Langfuse.

The application answers questions about loan eligibility rules using Retrieval-Augmented Generation over a local policy PDF. It runs fully locally, keeps source citations with answers, supports a chatbot-style Streamlit interface, and can run either directly on the machine or with the FastAPI backend inside Docker.

## Business Problem

Loan pre-qualification takes staff time and can produce inconsistent answers. This assistant provides a consistent, document-grounded way to answer common loan eligibility questions about income, credit score, age, documents, debt-to-income ratio, tenure, and applicant type.

## Current Features

- FastAPI backend with `/ask`, `/ask/stream`, `/health`, and `/` endpoints
- Streamlit chatbot frontend
- Streaming answers in the UI
- Conversation history within the same browser session
- RAG over a local loan eligibility PDF
- ChromaDB local vector store
- Ollama local chat model and embedding model
- Source citations with document, page, and excerpt
- Langfuse tracing wrapper that does not crash the app if tracing fails
- Local audit logs in JSONL format
- Docker support for the FastAPI backend

## Architecture Flow

```text
User
  ->
Streamlit Chatbot UI
  ->
FastAPI Backend
  ->
RAG Pipeline
  ->
ChromaDB Vector Store
  ->
Loan Eligibility PDF
  ->
Ollama LLM
  ->
Answer + Source Citation
  ->
Streamlit Chatbot UI
```

## Tech Stack

| Layer | Tool | Purpose |
|---|---|---|
| Frontend | Streamlit | Local chatbot UI |
| Backend | FastAPI | API layer for chat requests |
| RAG Framework | LangChain | PDF loading, chunking, retrieval, and LLM calls |
| Vector Store | ChromaDB | Local semantic search over policy chunks |
| LLM Runtime | Ollama | Local open-source model execution |
| Embeddings | Ollama embeddings | Local vector embeddings for retrieval |
| Tracing | Langfuse | Observability around API/RAG calls |
| Audit Logs | JSONL file | Local request metadata logging |
| Containerization | Docker | Portable backend runtime |

## Project Structure

```text
loan-eligibility-assistant/
|-- app/
|   |-- main.py
|   |-- rag.py
|   |-- config.py
|   |-- tracing.py
|   |-- audit.py
|   `-- guardrails_config.py
|-- data/
|   `-- loan_eligibility.pdf
|-- frontend/
|   `-- streamlit_app.py
|-- scripts/
|   `-- create_synthetic_pdf.py
|-- Dockerfile
|-- requirements.txt
|-- requirements-docker.txt
|-- .gitignore
|-- PROJECT_GUIDE.md
`-- README.md
```

## Prerequisites

Install these tools before running the project:

- Python 3.11
- Git
- Ollama
- Docker Desktop, only if running the backend with Docker

## Ollama Setup

Start Ollama:

```powershell
ollama serve
```

In a separate terminal, check available models:

```powershell
ollama list
```

Pull the current chat model:

```powershell
ollama pull qwen3.5:2b
```

Pull the recommended embedding model:

```powershell
ollama pull nomic-embed-text
```

Test the chat model:

```powershell
ollama run qwen3.5:2b "Say only: model works"
```

## Environment Variables

Create a `.env` file in the project root if it does not already exist:

```env
CHAT_MODEL=qwen3.5:2b
EMBEDDING_MODEL=nomic-embed-text:latest
OLLAMA_BASE_URL=http://localhost:11434
```

Optional Langfuse variables:

```env
LANGFUSE_PUBLIC_KEY=your_public_key
LANGFUSE_SECRET_KEY=your_secret_key
LANGFUSE_HOST=https://cloud.langfuse.com
```

The app still runs if Langfuse is not configured.

## Run Locally

Clone the repository:

```powershell
git clone https://github.com/mrunrin/loan-eligibility-assistant.git
```

Move into the project folder:

```powershell
cd loan-eligibility-assistant
```

Create a virtual environment:

```powershell
python -m venv capstone
```

Activate the virtual environment:

```powershell
.\capstone\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Start Ollama in one terminal:

```powershell
ollama serve
```

Start the FastAPI backend in another terminal:

```powershell
.\capstone\Scripts\activate
uvicorn app.main:app --reload
```

Start the Streamlit frontend in another terminal:

```powershell
.\capstone\Scripts\activate
streamlit run frontend/streamlit_app.py
```

Open the chatbot:

```text
http://localhost:8501
```

Open the FastAPI docs:

```text
http://localhost:8000/docs
```

## Run Backend With Docker

The Docker setup runs the FastAPI backend in a container while Ollama continues running on the host machine. Ollama models are not copied into the Docker image, which keeps the image smaller.

Start Ollama on the host:

```powershell
ollama serve
```

Build the backend image:

```powershell
docker build -t loan-eligibility-api .
```

Run the backend container:

```powershell
docker run --rm -p 8000:8000 -e OLLAMA_BASE_URL=http://host.docker.internal:11434 loan-eligibility-api
```

Test the backend:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Run Streamlit locally in another terminal:

```powershell
.\capstone\Scripts\activate
streamlit run frontend/streamlit_app.py
```

## API Examples

Health check:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

Ask a normal JSON question:

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/ask" -Method Post -ContentType "application/json" -Body '{"question":"What credit score is required?","history":[]}'
```

Expected response shape:

```json
{
  "answer": "The minimum credit score required for loan eligibility is 700.",
  "source": "loan_eligibility.pdf, page 1",
  "sources": [
    {
      "document": "loan_eligibility.pdf",
      "page": 1,
      "excerpt": "..."
    }
  ]
}
```

## Model Switching

Use `.env` to change the chat model:

```env
CHAT_MODEL=qwen3.5:2b
```

The model name must exactly match a tag shown by:

```powershell
ollama list
```

When using Qwen thinking models, keep thinking disabled in the application model setup if the installed `langchain_ollama` version supports it. The model is created in `app/rag.py` inside `get_chat_model()`.

For live demos, `qwen3.5:2b` is the current selected model. If it becomes slow, first check RAM usage and test the model directly with Ollama before changing application code.

## Troubleshooting

If Streamlit says the backend is unavailable, check FastAPI:

```powershell
Invoke-RestMethod http://localhost:8000/health
```

If the backend is running but responses are slow, test Ollama directly:

```powershell
ollama run qwen3.5:2b "Reply only: ready"
```

If using Docker and Ollama cannot connect, make sure the container uses:

```powershell
-e OLLAMA_BASE_URL=http://host.docker.internal:11434
```

If the vector store behaves strangely, stop the backend, delete the local `vectorstore/` folder, and restart the backend so it rebuilds from the PDF.

## Git Notes

Do not commit local runtime folders or secrets:

```text
.env
capstone/
vectorstore/
logs/
__pycache__/
```

Normal commit flow:

```powershell
git status
git add .
git commit -m "Update documentation"
git push
```
