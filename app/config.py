import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen3.5:2b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_WARMUP_TIMEOUT = int(os.getenv("OLLAMA_WARMUP_TIMEOUT", "20"))
FALLBACK_MODEL_PROVIDER = os.getenv("FALLBACK_MODEL_PROVIDER", "none").lower()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
HF_API_KEY = os.getenv("HF_API_KEY", "")
HF_MODEL = os.getenv("HF_MODEL", "mistralai/Mistral-7B-Instruct-v0.3")
PDF_PATH = Path(os.getenv("PDF_PATH")) if os.getenv("PDF_PATH") else None
VECTORSTORE_PATH = Path(os.getenv("VECTORSTORE_PATH", str(BASE_DIR / "vectorstore")))
AUDIT_LOG_PATH = Path(os.getenv("AUDIT_LOG_PATH", str(BASE_DIR / "logs" / "audit.jsonl")))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "12"))
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "2000"))
