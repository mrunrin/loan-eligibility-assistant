import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen3.5:2b")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
PDF_PATH = Path(os.getenv("PDF_PATH", str(BASE_DIR / "data" / "loan_eligibility.pdf")))
VECTORSTORE_PATH = Path(os.getenv("VECTORSTORE_PATH", str(BASE_DIR / "vectorstore")))
AUDIT_LOG_PATH = Path(os.getenv("AUDIT_LOG_PATH", str(BASE_DIR / "logs" / "audit.jsonl")))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "12"))
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "2000"))
