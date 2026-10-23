import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import iterate_in_threadpool

from app import assistant, policy_qa, retrieval
from app.audit import write_audit_event
from app.config import (
    DEV_EMAIL_VERIFY,
    EMBEDDING_MODEL,
    MAX_MESSAGE_CHARS,
)
from app.database import AsyncSessionLocal, engine, get_db
from app.deps import get_current_user
from app.email_utils import EMAIL_ENABLED
from app.guardrails_config import PII_NOTICE, check_output, redact_input
from app.limiter import limiter, user_or_ip
from app.llm import start_usage_meter
from app.models import User
from app.routers import admin, applications, auth, mfa, officer, prequal
from app.tracing import langfuse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
# A complete sentence (or line) ready to be redacted and emitted while streaming.
_SENTENCE_END_RE = re.compile(r"[.!?;:](?=\s)|\n")


def _startup_log(step: int, total: int, msg: str) -> None:
    """Always-visible startup progress line regardless of log level."""
    print(f"[{step}/{total}] {msg}", flush=True)


async def _init_vectorstore_until_ready() -> None:
    """Ping Ollama first (fail hard if not running), then load the vectorstore.
    Self-heals ChromaDB UUID corruption by wiping the dir and rebuilding once."""
    import shutil
    import urllib.request
    from app.config import OLLAMA_BASE_URL, VECTORSTORE_PATH

    os.environ["VECTORSTORE_NO_PROMPT"] = "1"

    # Fast-fail: a clear error beats a silent retry loop.
    try:
        urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
    except Exception:
        raise RuntimeError(
            f"\n\nOllama is not running at {OLLAMA_BASE_URL}.\n"
            "Start it first:  ollama serve\n"
            "Then pull the embedding model:  ollama pull nomic-embed-text\n"
        )

    try:
        await run_in_threadpool(retrieval.initialize_vectorstore)
        return
    except Exception as exc:
        _is_chroma_corrupt = (
            type(exc).__name__ == "NotFoundError"
            and "chromadb" in getattr(type(exc), "__module__", "")
        )
        if not _is_chroma_corrupt:
            logger.exception("Vectorstore init failed")
            raise RuntimeError(
                f"\n\nVectorstore init failed. Ollama is up at {OLLAMA_BASE_URL} but embedding failed.\n"
                "Make sure the embedding model is pulled:  ollama pull nomic-embed-text\n"
            )

    # ChromaDB collection UUID is out of sync with on-disk data (corrupt state).
    # Wipe the entire vectorstore directory and rebuild from scratch — once.
    _startup_log(3, 4, "ChromaDB collection corrupt — wiping vectorstore and rebuilding (takes 1-2 min)...")
    shutil.rmtree(VECTORSTORE_PATH, ignore_errors=True)
    VECTORSTORE_PATH.mkdir(parents=True, exist_ok=True)
    retrieval._vectorstore = None  # reset in-memory guard so initialize_vectorstore runs again
    await run_in_threadpool(retrieval.initialize_vectorstore)  # raises if it fails again


@asynccontextmanager
async def lifespan(application: FastAPI):
    # ── Step 1: DB migrations ────────────────────────────────────────────────
    _startup_log(1, 4, "Running DB migrations...")

    def _run_migrations() -> None:
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config("alembic.ini"), "head")

    try:
        await run_in_threadpool(_run_migrations)
        _startup_log(1, 4, "DB migrations applied ✓")
    except Exception:
        logger.exception("Alembic migration failed — aborting startup")
        raise

    # ── Step 2: Reranker model warm-up (heavy on first run) ─────────────────
    _startup_log(2, 4, "Loading reranker model (may download ~90 MB on first run)...")
    try:
        await run_in_threadpool(retrieval._get_reranker)
        _startup_log(2, 4, "Reranker model loaded ✓")
    except Exception:
        logger.warning("Reranker warm-up failed — will retry on first query")

    # ── Step 3: Vectorstore + Ollama embeddings ──────────────────────────────
    _startup_log(3, 4, "Connecting to Ollama + loading vectorstore...")
    await _init_vectorstore_until_ready()
    _startup_log(3, 4, "Vectorstore ready ✓")

    # ── Step 4: Open for traffic ─────────────────────────────────────────────
    _startup_log(4, 4, "Server ready — accepting requests ✓")
    yield

    try:
        langfuse.flush()
    except Exception:
        logger.warning("Langfuse flush skipped during shutdown")


app = FastAPI(title="Loan Management Portal", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Middleware stack (outermost → innermost). Auth is a Bearer header, never a cookie,
# so there is no session cookie and no CSRF surface.
# ---------------------------------------------------------------------------

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["localhost", "127.0.0.1", "backend", "*.localhost"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _REQUEST_ID_RE.match(incoming) else str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth.router)
app.include_router(mfa.router)
app.include_router(applications.router)
app.include_router(prequal.router)
app.include_router(officer.router)
app.include_router(admin.router)


# ---------------------------------------------------------------------------
# Per-borrower daily Groq token budget (officers and admins are exempt)
# shortcut: in-process memory, resets on restart; move to Redis for multi-worker
# ---------------------------------------------------------------------------

from app.config import GROQ_DAILY_BUDGET_TOKENS  # noqa: E402 — after other config imports

_IST = timezone(timedelta(hours=5, minutes=30))  # India has no DST, so a fixed offset is exact


class _BudgetStore:
    """Groq tokens used per borrower per IST calendar day, counted from Groq's own usage figures.

    Every Groq call in a request counts (router/rewriter + answer). A request is allowed while the
    borrower is under budget, so the last one of the day can overshoot by its own size.
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[int, str]] = {}  # user_id → (tokens_used, IST date)

    def _today(self) -> str:
        return datetime.now(_IST).date().isoformat()  # resets at midnight IST

    def _used(self, user_id: str) -> int:
        used, day = self._data.get(user_id, (0, ""))
        return used if day == self._today() else 0

    def allowed(self, user_id: str) -> bool:
        if not GROQ_DAILY_BUDGET_TOKENS:
            return True
        return self._used(user_id) < GROQ_DAILY_BUDGET_TOKENS

    def record(self, user_id: str, tokens: int) -> None:
        if tokens:
            self._data[user_id] = (self._used(user_id) + tokens, self._today())


_budget = _BudgetStore()


def _within_budget(user: User) -> bool:
    return user.role != "borrower" or _budget.allowed(str(user.id))


def _charge(user: User, meter: list[int]) -> None:
    """Add the request's real Groq tokens (router + answer) to a borrower's daily total."""
    if user.role == "borrower":
        _budget.record(str(user.id), meter[0])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: str = Field(default="default", min_length=1, max_length=100)
    # The server loads this application itself (owner-scoped); clients never send application data.
    application_id: uuid.UUID | None = None

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question cannot be blank")
        return redact_input(value.strip())

    @property
    def notice(self) -> str | None:
        return PII_NOTICE if "REDACTED]" in self.question else None


# Blocked questions may contain PII; failures carry no useful context.
_REMEMBERED_STATUSES = {"ok", "memory", "small_talk", "no_evidence", "calculator", "clarify", "no_application", "out_of_scope"}


async def _remember(db: AsyncSession, user: User, body: AskRequest, status: str, answer: str, intent: str | None) -> None:
    if status in _REMEMBERED_STATUSES:
        await assistant.save_turn(db, user.id, body.session_id, body.question, answer, intent)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    return {"message": "Loan Eligibility Assistant is running"}


@app.get("/health")
async def health():
    """200 only when the database and the vector store are usable; Ollama is informational."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    vectorstore_ready = retrieval.is_ready()
    ok = db_ok and vectorstore_ready
    body = {
        "status": "ok" if ok else "degraded",
        "database_ready": db_ok,
        "vectorstore_ready": vectorstore_ready,
        "embedding_model": EMBEDDING_MODEL,
        "email_enabled": EMAIL_ENABLED,
        "dev_email_verify": DEV_EMAIL_VERIFY and not EMAIL_ENABLED,
    }
    return JSONResponse(status_code=200 if ok else 503, content=body)


@app.post("/ask")
@limiter.limit("20/minute", key_func=user_or_ip)
async def ask(
    request: Request,
    body: AskRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request_id = request.state.request_id
    started = time.perf_counter()
    if not _within_budget(current_user):
        return JSONResponse(status_code=429, content={
            "answer": "You have reached your daily AI usage limit. Please try again tomorrow.",
            "source": "budget_exceeded", "sources": [],
        })
    meter = start_usage_meter()
    with langfuse.start_as_current_observation(
        name="loan-eligibility-ask",
        input={"question": body.question},
        metadata={"request_id": request_id, "session_id": body.session_id},
    ) as span:
        try:
            history = await assistant.load_history(db, current_user.id, body.session_id)
            prepared = await assistant.prepare(body.question, history, current_user, db, body.application_id)
            result = {**await policy_qa.generate(prepared), "intent": prepared["intent"], "notice": body.notice}
        except Exception:
            logger.exception("ask failed request_id=%s", request_id)
            write_audit_event(
                request_id=request_id, session_id=body.session_id, question=body.question, status="failed",
                latency_ms=int((time.perf_counter() - started) * 1000), requested_provider="groq",
            )
            span.update(level="ERROR", status_message="ask failed")
            _charge(current_user, meter)
            return JSONResponse(status_code=503, content={
                "answer": "HDFC Loan Bot could not complete that request. Please try again.",
                "source": "service unavailable",
                "sources": [],
            })
        span.update(output={"answer": result["answer"], "status": result["status"], "intent": result["intent"]},
                    metadata={"groq_tokens": meter[0]})
    _charge(current_user, meter)

    latency_ms = int((time.perf_counter() - started) * 1000)
    await _remember(db, current_user, body, result["status"], result["answer"], result["intent"])
    write_audit_event(
        request_id=request_id, session_id=body.session_id, question=body.question, status=result["status"],
        latency_ms=latency_ms, sources=result.get("sources"), requested_provider="groq",
        answered_provider=result.get("answered_provider"), intent=result["intent"],
    )
    logger.info("ask %s request_id=%s latency_ms=%d", result["status"], request_id, latency_ms)
    return result


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.post("/ask/stream")
@limiter.limit("20/minute", key_func=user_or_ip)
async def ask_stream(request: Request, body: AskRequest, current_user: User = Depends(get_current_user)):
    request_id = request.state.request_id
    if not _within_budget(current_user):
        async def _budget_exceeded():
            yield _sse("error", {"message": "You have reached your daily AI usage limit. Please try again tomorrow."})
        return StreamingResponse(_budget_exceeded(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    async def events() -> AsyncIterator[str]:
        meter = start_usage_meter()
        try:
            async for chunk in _stream_events(meter):
                yield chunk
        finally:
            _charge(current_user, meter)  # also when the client disconnects mid-answer

    async def _stream_events(meter: list[int]) -> AsyncIterator[str]:
        started = time.perf_counter()
        loop = asyncio.get_running_loop()
        stages: asyncio.Queue[str] = asyncio.Queue()
        prepared: dict | None = None
        async with AsyncSessionLocal() as db:
            with langfuse.start_as_current_observation(
                name="loan-eligibility-stream",
                input={"question": body.question},
                metadata={"request_id": request_id, "session_id": body.session_id},
            ) as span:
                try:
                    history = await assistant.load_history(db, current_user.id, body.session_id)
                    # Real progress: routing and retrieval report each stage, retrieval from a worker thread.
                    task = asyncio.ensure_future(assistant.prepare(
                        body.question, history, current_user, db, body.application_id,
                        lambda msg: loop.call_soon_threadsafe(stages.put_nowait, msg),
                    ))
                    while not task.done():
                        getter = asyncio.ensure_future(stages.get())
                        done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
                        if getter in done:
                            yield _sse("status", {"message": getter.result()})
                        else:
                            getter.cancel()
                    while not stages.empty():
                        yield _sse("status", {"message": stages.get_nowait()})
                    prepared = task.result()

                    if prepared["kind"] == "llm":
                        yield _sse("status", {"message": "Generating answer..."})

                    evidence = prepared["evidence_texts"]
                    emitted, pending = "", ""
                    # The Groq call traces itself as a Langfuse generation with token usage (app.llm).
                    async for token in iterate_in_threadpool(policy_qa.stream_answer(prepared)):
                        pending += token
                        # Emit only whole sentences, after PII redaction against the evidence.
                        last_end = None
                        for match in _SENTENCE_END_RE.finditer(pending):
                            last_end = match.end()
                        if last_end:
                            safe = check_output(pending[:last_end], evidence)
                            pending = pending[last_end:]
                            emitted += safe
                            yield _sse("token", {"text": safe})
                    if pending:
                        safe = check_output(pending, evidence)
                        emitted += safe
                        yield _sse("token", {"text": safe})

                    status = prepared.get("status", "ok")
                    intent = prepared.get("intent")
                    result = {"answer": emitted, "source": prepared["source"], "sources": prepared["sources"],
                              "status": status, "intent": intent, "notice": body.notice}
                    span.update(output={"answer": emitted, "status": status, "intent": intent},
                                metadata={"groq_tokens": meter[0]})
                    await _remember(db, current_user, body, status, emitted, intent)
                    write_audit_event(
                        request_id=request_id, session_id=body.session_id, question=body.question, status=status,
                        latency_ms=int((time.perf_counter() - started) * 1000), sources=result["sources"],
                        requested_provider="groq", answered_provider=prepared.get("answered_provider"), intent=intent,
                    )
                    yield _sse("done", result)
                except Exception:
                    logger.exception("stream failed request_id=%s", request_id)
                    span.update(level="ERROR", status_message="stream failed")
                    write_audit_event(
                        request_id=request_id, session_id=body.session_id, question=body.question, status="failed",
                        latency_ms=int((time.perf_counter() - started) * 1000), requested_provider="groq",
                    )
                    yield _sse("error", {"message": "HDFC Loan Bot could not complete that request. Please try again."})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
