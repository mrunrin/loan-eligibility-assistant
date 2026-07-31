# Loan Eligibility Assistant

A local AI-powered banking loan eligibility assistant built with FastAPI, Streamlit, ChromaDB, LangChain, and Ollama.

The assistant answers user questions about loan eligibility rules using Retrieval-Augmented Generation (RAG) over a loan policy PDF. It returns grounded answers with source citations and can be run locally or with the FastAPI backend inside Docker.

## Business Problem

Loan pre-qualification often takes staff time and can lead to inconsistent answers. This assistant provides a consistent, document-grounded way to answer common loan eligibility questions such as income requirements, credit score, age limits, documents, debt-to-income ratio, and loan tenure.

## Current Status

The project currently supports:

- FastAPI backend with `/ask` endpoint
- Streamlit frontend for user questions
- RAG pipeline over a loan eligibility PDF
- ChromaDB local vector store
- Ollama local LLM and embedding model
- Source citation in responses
- Langfuse tracing wrapper
- Docker support for the backend

## Architecture Flow

```text
User
  ↓
Streamlit Frontend
  ↓
FastAPI Backend `/ask`
  ↓
RAG Pipeline
  ↓
ChromaDB Vector Store
  ↓
Loan Eligibility PDF
  ↓
Ollama LLM
  ↓
Answer + Source Citation
  ↓
Streamlit UI

## Tech Stack

| Layer | Tool | Why It Is Used |
|---|---|---|
| Frontend | Streamlit | Simple Python-based UI for fast chatbot/demo development |
| Backend | FastAPI | API-first backend with automatic docs and clean request handling |
| RAG Framework | LangChain | Connects PDF loading, chunking, retrieval, and LLM calls |
| Vector Store | ChromaDB | Free local vector database for document search |
| LLM Runtime | Ollama | Runs local open-source models without paid API keys |
| Chat Model | llama3.2 | Local model used to generate answers |
| Embedding Model | nomic-embed-text | Local embedding model used for vector search |
| Tracing | Langfuse | Tracks API/RAG calls for observability and audit logs |
| Containerization | Docker | Packages the FastAPI backend for portable execution |

## Project Structure

```text
loan-eligibility-assistant/
├── app/
│   ├── main.py
│   ├── rag.py
│   ├── config.py
│   ├── tracing.py
│   └── guardrails_config.py
├── data/
│   └── loan_eligibility.pdf
├── frontend/
│   └── streamlit_app.py
├── scripts/
│   └── create_synthetic_pdf.py
├── .github/
│   └── workflows/
│       └── eval.yml
├── Dockerfile
├── requirements.txt
├── requirements-docker.txt
├── .gitignore
└── README.md

## Prerequisites

Install the following before running the project:

- Python 3.11
- Git
- Ollama
- Docker Desktop, optional for Docker execution

## Ollama Setup

Start the Ollama server:

```powershell
ollama serve
```

Open a new terminal and check installed models:

```powershell
ollama list
```

Pull the required chat model:

```powershell
ollama pull llama3.2
```

Pull the required embedding model:

```powershell
ollama pull nomic-embed-text
```

Verify the chat model:

```powershell
ollama run llama3.2 "Say only: model works"
```

Verify the embedding model:

```powershell
ollama run nomic-embed-text "test embedding"
```
## Run Locally

Open a terminal in the project folder:

```powershell
cd "C:\Mrunal\EXL\Capstone Project"
```

Create and activate a virtual environment:

```powershell
python -m venv capstone
```

```powershell
.\capstone\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Start Ollama:

```powershell
ollama serve
```

Open a new terminal, activate the environment again, and start the FastAPI backend:

```powershell
cd "C:\Capstone Project"
```

```powershell
.\capstone\Scripts\activate
```

```powershell
uvicorn app.main:app --reload
```

Open a new terminal, activate the environment again, and start the Streamlit frontend:

```powershell
cd "C:\Capstone Project"
```

```powershell
.\capstone\Scripts\activate
```

```powershell
streamlit run frontend/streamlit_app.py
```

Open the app:

```text
http://localhost:8501
```

Open the FastAPI docs:

```text
http://localhost:8000/docs
```

## Run Backend With Docker

This project can run the FastAPI backend inside Docker while keeping Ollama running locally on the host machine.

Start Ollama on the host machine:

```powershell
ollama serve
```

Open a new terminal in the project folder:

```powershell
cd "C:\Mrunal\EXL\Capstone Project"
```

Build the Docker image:

```powershell
docker build -t loan-eligibility-api .
```

Run the backend container:

```powershell
docker run --rm -p 8000:8000 -e OLLAMA_BASE_URL=http://host.docker.internal:11434 loan-eligibility-api
```

Open the FastAPI docs:

```text
http://localhost:8000/docs
```

Test the backend health route:

```powershell
curl http://localhost:8000/
```

Test the `/ask` endpoint:

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/ask" -Method Post -ContentType "application/json" -Body '{"question":"What credit score is required?"}'
```

Run the Streamlit frontend locally in another terminal:

```powershell
cd "C:\Mrunal\EXL\Capstone Project"
```

```powershell
.\capstone\Scripts\activate
```

```powershell
streamlit run frontend/streamlit_app.py
```


## API Endpoints

### Health Check

```http
GET /
```

Example request:

```powershell
curl http://localhost:8000/
```

Example response:

```json
{
  "message": "Loan Eligibility Assistant is running"
}
```

### Ask Question

```http
POST /ask
```

Example request:

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/ask" -Method Post -ContentType "application/json" -Body '{"question":"What credit score is required?"}'
```

Example response:
# Loan Eligibility Assistant

A local AI-powered banking loan eligibility assistant built with FastAPI, Streamlit, ChromaDB, LangChain, and Ollama.

The assistant answers user questions about loan eligibility rules using Retrieval-Augmented Generation (RAG) over a loan policy PDF. It returns grounded answers with source citations and can be run locally or with the FastAPI backend inside Docker.

## Business Problem

Loan pre-qualification often takes staff time and can lead to inconsistent answers. This assistant provides a consistent, document-grounded way to answer common loan eligibility questions such as income requirements, credit score, age limits, documents, debt-to-income ratio, and loan tenure.

## Current Status

The project currently supports:

- FastAPI backend with `/ask` endpoint
- Streamlit frontend for user questions
- RAG pipeline over a loan eligibility PDF
- ChromaDB local vector store
- Ollama local LLM and embedding model
- Source citation in responses
- Langfuse tracing wrapper
- Docker support for the backend

## Architecture Flow

```text
User
  ↓
Streamlit Frontend
  ↓
FastAPI Backend /ask
  ↓
RAG Pipeline
  ↓
ChromaDB Vector Store
  ↓
Loan Eligibility PDF
  ↓
Ollama LLM
  ↓
Answer + Source Citation
  ↓
Streamlit UI
```

## Tech Stack

| Layer | Tool | Why It Is Used |
|---|---|---|
| Frontend | Streamlit | Simple Python-based UI for fast chatbot/demo development |
| Backend | FastAPI | API-first backend with automatic docs and clean request handling |
| RAG Framework | LangChain | Connects PDF loading, chunking, retrieval, and LLM calls |
| Vector Store | ChromaDB | Free local vector database for document search |
| LLM Runtime | Ollama | Runs local open-source models without paid API keys |
| Chat Model | llama3.2 | Local model used to generate answers |
| Embedding Model | nomic-embed-text | Local embedding model used for vector search |
| Tracing | Langfuse | Tracks API/RAG calls for observability and audit logs |
| Containerization | Docker | Packages the FastAPI backend for portable execution |

## Project Structure

```text
loan-eligibility-assistant/
├── app/
│   ├── main.py
│   ├── rag.py
│   ├── config.py
│   ├── tracing.py
│   └── guardrails_config.py
├── data/
│   └── loan_eligibility.pdf
├── frontend/
│   └── streamlit_app.py
├── scripts/
│   └── create_synthetic_pdf.py
├── .github/
│   └── workflows/
│       └── eval.yml
├── Dockerfile
├── requirements.txt
├── requirements-docker.txt
├── .gitignore
└── README.md
```

## Prerequisites

Install the following before running the project:

- Python 3.11
- Git
- Ollama
- Docker Desktop, optional for Docker execution

## Ollama Setup

Start the Ollama server:

```powershell
ollama serve
```

Open a new terminal and check installed models:

```powershell
ollama list
```

Pull the required chat model:

```powershell
ollama pull llama3.2
```

Pull the required embedding model:

```powershell
ollama pull nomic-embed-text
```

Verify the chat model:

```powershell
ollama run llama3.2 "Say only: model works"
```

Verify the embedding model:

```powershell
ollama run nomic-embed-text "test embedding"
```

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

Activate the virtual environment on Windows:

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
```

```powershell
uvicorn app.main:app --reload
```

Start the Streamlit frontend in another terminal:

```powershell
.\capstone\Scripts\activate
```

```powershell
streamlit run frontend/streamlit_app.py
```

Open the Streamlit app:

```text
http://localhost:8501
```

Open the FastAPI docs:

```text
http://localhost:8000/docs
```

## Run Backend With Docker

This project can run the FastAPI backend inside Docker while keeping Ollama running locally on the host machine.

Start Ollama on the host machine:

```powershell
ollama serve
```

Build the Docker image from the project root:

```powershell
docker build -t loan-eligibility-api .
```

Run the backend container:

```powershell
docker run --rm -p 8000:8000 -e OLLAMA_BASE_URL=http://host.docker.internal:11434 loan-eligibility-api
```

Open the FastAPI docs:

```text
http://localhost:8000/docs
```

Test the backend health route:

```powershell
curl http://localhost:8000/
```

Test the `/ask` endpoint:

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/ask" -Method Post -ContentType "application/json" -Body '{"question":"What credit score is required?"}'
```

Run the Streamlit frontend locally in another terminal:

```powershell
.\capstone\Scripts\activate
```

```powershell
streamlit run frontend/streamlit_app.py
```

## API Endpoints

### Health Check

```http
GET /
```

Example request:

```powershell
curl http://localhost:8000/
```

Example response:

```json
{
  "message": "Loan Eligibility Assistant is running"
}
```

### Ask Question

```http
POST /ask
```

Example request:

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/ask" -Method Post -ContentType "application/json" -Body '{"question":"What credit score is required?"}'
```

Example response:

```json
{
  "answer": "The minimum credit score required for loan eligibility is 700.",
  "source": "data/loan_eligibility.pdf"
}
```

## Notes

- Ollama must be running before asking questions.
- The Docker setup keeps Ollama outside the container to avoid packaging large model files inside the image.
- The backend uses `OLLAMA_BASE_URL=http://host.docker.internal:11434` when running inside Docker.
- The app is designed to run fully locally without paid APIs.
```json
{
  "answer": "The minimum credit score required for loan eligibility is 700.",
  "source": "data/loan_eligibility.pdf"
}
```