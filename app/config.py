import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
# All model work runs on hosted APIs: Jina embeddings, Cohere rerank, Groq answers.
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "jina-embeddings-v3")
JINA_API_KEY = os.getenv("JINA_API_KEY", "")
RERANKER_MODEL = os.getenv("RERANKER_MODEL", "rerank-v3.5")
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
# Small model for query rewriting + intent routing; Groq rate limits are per model, so this
# keeps routing calls out of the answer model's tokens-per-minute budget.
ROUTER_MODEL = os.getenv("ROUTER_MODEL", "qwen/qwen3.8-27b")
# Per-borrower daily token budget. 0 or empty = unlimited; unset = 50000 (officers/admins are always unlimited).
_budget_raw = os.getenv("GROQ_DAILY_BUDGET_TOKENS", "50000")
GROQ_DAILY_BUDGET_TOKENS: int | None = int(_budget_raw) if _budget_raw.strip().isdigit() else None
PDF_PATH = Path(os.getenv("PDF_PATH")) if os.getenv("PDF_PATH") else None
VECTORSTORE_PATH = Path(os.getenv("VECTORSTORE_PATH", str(BASE_DIR / "vectorstore")))
AUDIT_LOG_PATH = Path(os.getenv("AUDIT_LOG_PATH", str(BASE_DIR / "logs" / "audit.jsonl")))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "12"))
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "2000"))
# Reranker relevance floor (Cohere scores are 0..1); below it the bot refuses instead of guessing.
RERANK_MIN_SCORE = float(os.getenv("RERANK_MIN_SCORE", "0.0"))
# Lender names users may mention; each maps to the policy PDF whose text contains it.
KNOWN_LENDER_NAMES = [
    n.strip() for n in os.getenv("KNOWN_LENDER_NAMES", "HDFC").split(",") if n.strip()
]

# Database
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://loanbot:loanbot@localhost:5432/loanbot",
)

# JWT — the app refuses to start with a missing, short, or publicly known secret.
_KNOWN_PLACEHOLDER_SECRETS = {
    "insecure-dev-secret-change-in-prod",
    "change-me-to-a-random-64-char-string-in-production",
    "changeme",
    "secret",
}
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
if JWT_SECRET_KEY in _KNOWN_PLACEHOLDER_SECRETS or len(JWT_SECRET_KEY.encode()) < 32:
    raise RuntimeError(
        "JWT_SECRET_KEY must be set to a random value of at least 32 bytes. Generate one with: "
        "python -c \"import secrets; print(secrets.token_urlsafe(64))\""
    )
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "5"))
REFRESH_TOKEN_EXPIRE_HOURS = int(os.getenv("REFRESH_TOKEN_EXPIRE_HOURS", "1"))
# Encrypts TOTP secrets at rest. Falls back to a key derived from JWT_SECRET_KEY;
# set it explicitly so rotating the JWT secret does not invalidate enrolled authenticators.
DATA_ENCRYPTION_KEY = os.getenv("DATA_ENCRYPTION_KEY", "")

# Brute-force protection
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5"))
MAX_FAILED_OTP = int(os.getenv("MAX_FAILED_OTP", "5"))
# First lockout length; each repeat lockout doubles it (capped at 24 h) until the next successful sign-in.
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))
# Failed logins one client IP may make per LOCKOUT_MINUTES window (any accounts) before it gets 429.
IP_FAILED_LOGIN_LIMIT = int(os.getenv("IP_FAILED_LOGIN_LIMIT", "20"))
# Server-side session limits. The browser logs out after 5 idle minutes; this is the backstop if that is bypassed.
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "15"))
SESSION_MAX_HOURS = int(os.getenv("SESSION_MAX_HOURS", "12"))
# Peers allowed to set X-Forwarded-For (the Streamlit container / local reverse proxy).
TRUSTED_PROXIES = [
    p.strip()
    for p in os.getenv("TRUSTED_PROXIES", "127.0.0.1/32,::1/128,172.16.0.0/12").split(",")
    if p.strip()
]

# Officer claims older than this return to the shared queue (abandoned reviews).
CLAIM_TIMEOUT_HOURS = int(os.getenv("CLAIM_TIMEOUT_HOURS", "1"))

# File uploads
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))

# SMTP — leave SMTP_USER/SMTP_PASSWORD empty to disable email (dev mode)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
# Dev-only: lets an unverified user verify instantly when SMTP is not configured.
DEV_EMAIL_VERIFY = os.getenv("DEV_EMAIL_VERIFY", "false").lower() in {"1", "true", "yes"}
