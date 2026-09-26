import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from langfuse import observe
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import iterate_in_threadpool

from app.audit import write_audit_event
from app.config import (
    CHAT_MODEL,
    EMBEDDING_MODEL,
    FALLBACK_MODEL_PROVIDER,
    MAX_HISTORY_MESSAGES,
    MAX_MESSAGE_CHARS,
    OLLAMA_WARMUP_TIMEOUT,
)
from app.rag import (
    answer_question,
    get_chat_model,
    initialize_vectorstore,
    prepare_answer,
    stream_answer,
)
from app.tracing import langfuse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.vectorstore_ready = False
    application.state.ollama_ready = False
    try:
        await run_in_threadpool(initialize_vectorstore)
        application.state.vectorstore_ready = True
    except Exception:
        logger.exception("Vector store startup failed")
    try:
        await asyncio.wait_for(
            run_in_threadpool(get_chat_model().invoke, "Reply with ready."),
            timeout=OLLAMA_WARMUP_TIMEOUT,
        )
        application.state.ollama_ready = True
        logger.info("LoanBot startup completed")
    except TimeoutError:
        logger.warning("Ollama warmup timed out; app will start and try per request")
    except Exception:
        logger.exception("Ollama warmup failed")
    yield
    try:
        langfuse.flush()
    except Exception:
        logger.warning("Langfuse flush skipped during shutdown")


app = FastAPI(title="Loan Eligibility Assistant", lifespan=lifespan)


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[Message] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    session_id: str | None = Field(default=None, max_length=100)
    model_provider: str = Field(default="local")

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question cannot be blank")
        return value.strip()

    @field_validator("model_provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in {"local", "groq", "huggingface"}:
            raise ValueError("model_provider must be 'local', 'groq', or 'huggingface'")
        return value


def _history(request: AskRequest) -> list[dict]:
    return [message.model_dump() for message in request.history]


def _record_trace(question: str, output: dict) -> None:
    try:
        langfuse.update_current_span(input=question, output=output)
        langfuse.flush()
    except Exception:
        logger.warning("Langfuse trace update skipped")


@app.get("/")
def root():
    return {"message": "Loan Eligibility Assistant is running"}


@app.get("/health")
def health():
    vectorstore_ready = getattr(app.state, "vectorstore_ready", False)
    ollama_ready = getattr(app.state, "ollama_ready", False)
    return {
        "status": "ok" if vectorstore_ready and ollama_ready else "degraded",
        "model": CHAT_MODEL,
        "embedding_model": EMBEDDING_MODEL,
        "fallback_provider": FALLBACK_MODEL_PROVIDER,
        "vectorstore_ready": vectorstore_ready,
        "ollama_ready": ollama_ready,
    }


@app.post("/ask")
@observe(name="loan-eligibility-ask")
async def ask(request: AskRequest):
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    try:
        result = await run_in_threadpool(answer_question, request.question, _history(request), request.model_provider)
        _record_trace(request.question, result)
        latency_ms = int((time.perf_counter() - started) * 1000)
        write_audit_event(request_id=request_id, session_id=request.session_id, question=request.question, status="completed", latency_ms=latency_ms, sources=result.get("sources"))
        logger.info("ask completed request_id=%s session_id=%s latency_ms=%d", request_id, request.session_id, latency_ms)
        return result
    except Exception:
        logger.exception("ask failed request_id=%s session_id=%s", request_id, request.session_id)
        write_audit_event(request_id=request_id, session_id=request.session_id, question=request.question, status="failed", latency_ms=int((time.perf_counter() - started) * 1000))
        return JSONResponse(status_code=503, content={"answer": "I am having trouble reaching the local loan knowledge base. Please make sure Ollama is running and try again.", "source": "service unavailable", "sources": []})


@app.post("/ask/stream")
@observe(name="loan-eligibility-stream")
async def ask_stream(request: AskRequest):
    request_id = str(uuid.uuid4())

    async def events() -> AsyncIterator[str]:
        answer = ""
        started = time.perf_counter()

        def _status(msg: str) -> str:
            return f"event: status\ndata: {json.dumps({'message': msg})}\n\n"

        try:
            # --- Fake live status messages (wrapper, does not touch rag.py) ---
            yield _status("Analyzing your question...")
            await asyncio.sleep(0.8)

            yield _status("Searching policy documents with ChromaDB & BM25...")
            await asyncio.sleep(1.2)

            yield _status("Reranking candidates with Cross-Encoder...")
            await asyncio.sleep(1.5)

            yield _status("Building prompt and calling LLM...")
            await asyncio.sleep(0)

            # --- Real blocking call in threadpool (rag.py untouched) ---
            prepared = await run_in_threadpool(prepare_answer, request.question, _history(request))
            provider = request.model_provider

            # --- Stream LLM tokens ---
            async for token in iterate_in_threadpool(stream_answer(prepared, provider)):
                answer += token
                yield f"event: token\ndata: {json.dumps({'text': token})}\n\n"
                await asyncio.sleep(0)

            result = {"answer": answer, "source": prepared["source"], "sources": prepared["sources"]}
            _record_trace(request.question, result)
            write_audit_event(
                request_id=request_id,
                session_id=request.session_id,
                question=request.question,
                status="completed",
                latency_ms=int((time.perf_counter() - started) * 1000),
                sources=result["sources"],
            )
            yield f"event: done\ndata: {json.dumps(result)}\n\n"

        except Exception:
            logger.exception("stream failed request_id=%s", request_id)
            write_audit_event(
                request_id=request_id,
                session_id=request.session_id,
                question=request.question,
                status="failed",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
            yield f"event: error\ndata: {json.dumps({'message': 'LoanBot could not complete that request. Please try again.'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Request-ID": request_id})
