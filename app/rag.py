import logging
import re
import threading
from collections.abc import Iterator

from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import (
    CHAT_MODEL,
    EMBEDDING_MODEL,
    MAX_HISTORY_MESSAGES,
    OLLAMA_BASE_URL,
    PDF_PATH,
    VECTORSTORE_PATH,
)

logger = logging.getLogger(__name__)
_vectorstore: Chroma | None = None
_vectorstore_lock = threading.Lock()


def get_chat_model() -> ChatOllama:
    return ChatOllama(model=CHAT_MODEL, base_url=OLLAMA_BASE_URL,reasoning=False)


def initialize_vectorstore() -> None:
    """Load the persisted store once, or build it once when it is empty."""
    global _vectorstore
    with _vectorstore_lock:
        if _vectorstore is not None:
            return

        embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_BASE_URL)
        store = Chroma(persist_directory=str(VECTORSTORE_PATH), embedding_function=embeddings)
        if not store.get(limit=1).get("ids"):
            if not PDF_PATH.exists():
                raise FileNotFoundError(f"Policy PDF not found: {PDF_PATH}")
            documents = PyPDFLoader(str(PDF_PATH)).load()
            chunks = RecursiveCharacterTextSplitter(
                chunk_size=500, chunk_overlap=80
            ).split_documents(documents)
            store = Chroma.from_documents(
                documents=chunks,
                embedding=embeddings,
                persist_directory=str(VECTORSTORE_PATH),
            )
            logger.info("Built vector store with %s chunks", len(chunks))
        _vectorstore = store
        logger.info("Vector store is ready")


def _conversation_context(history: list[dict], question: str) -> str:
    previous = history[-MAX_HISTORY_MESSAGES:]
    if previous and previous[-1].get("role") == "user" and previous[-1].get("content", "").strip() == question.strip():
        previous = previous[:-1]
    return "\n".join(
        f"{message.get('role', 'user')}: {message.get('content', '')}"
        for message in previous
    )


def _profile_from_history(history: list[dict]) -> dict[str, str]:
    profile: dict[str, str] = {}
    for message in history:
        if message.get("role") != "user":
            continue
        text = message.get("content", "").strip()
        name = re.search(r"\bmy name is\s+([A-Za-z][A-Za-z '-]{0,48})[.!]?$", text, re.I)
        if name:
            profile["name"] = name.group(1).strip()
    return profile


def _memory_response(question: str, profile: dict[str, str]) -> dict | None:
    normalized = question.lower().replace("'", "").strip()
    asks_for_name = "name" in normalized and any(
        phrase in normalized for phrase in ("my name", "whats", "what is", "who am i")
    )
    introduces_name = normalized.startswith("my name is ")
    if introduces_name and profile.get("name"):
        return {
            "answer": f"Nice to meet you, {profile['name']}. I will remember that for this chat.",
            "source": "conversation history",
            "sources": [],
        }
    if asks_for_name:
        if profile.get("name"):
            return {
                "answer": f"Your name is {profile['name']}.",
                "source": "conversation history",
                "sources": [],
            }
        return {
            "answer": "You have not told me your name in this chat yet.",
            "source": "conversation history",
            "sources": [],
        }
    return None


def _is_small_talk(question: str) -> bool:
    normalized = question.lower().strip().rstrip("!?.")
    return normalized in {"hi", "hii", "hello", "hey", "thanks", "thank you", "bye", "good morning", "good evening"}


def _sources(documents: list) -> list[dict]:
    citations: list[dict] = []
    seen_pages: set[int] = set()
    for document in documents:
        page = int(document.metadata.get("page", 0)) + 1
        if page in seen_pages:
            continue
        seen_pages.add(page)
        citations.append({
            "document": PDF_PATH.name,
            "page": page,
            "excerpt": document.page_content[:500].strip(),
        })
    return citations


def prepare_answer(question: str, history: list[dict] | None = None) -> dict:
    history = history or []
    profile = _profile_from_history(history)
    memory_answer = _memory_response(question, profile)
    if memory_answer:
        return {"kind": "direct", **memory_answer}

    if _is_small_talk(question):
        return {
            "kind": "direct",
            "answer": "Hello. I can help with loan eligibility, income rules, credit scores, documents, and loan tenure.",
            "source": "general assistant response",
            "sources": [],
        }

    initialize_vectorstore()
    assert _vectorstore is not None
    documents = _vectorstore.similarity_search(question, k=3)
    sources = _sources(documents)
    policy_context = "\n\n".join(document.page_content for document in documents)
    conversation_context = _conversation_context(history, question)
    prompt = f"""
You are LoanBot, a banking loan eligibility assistant.

Answer only from the policy context. Use conversation history only to resolve follow-up references such as "what about self-employed applicants"; it is not a policy source.
If the policy context does not support an answer, say you cannot confirm it from the policy and invite a loan-eligibility question. Do not claim approval or guarantee eligibility.
Keep the answer clear, concise, and professional.

Conversation history:
{conversation_context or "No earlier messages."}

Policy context:
{policy_context}

User question:
{question}
"""
    return {
        "kind": "llm",
        "prompt": prompt,
        "source": f"{PDF_PATH.name}, page {sources[0]['page']}" if sources else "No policy source found",
        "sources": sources,
    }


def answer_question(question: str, history: list[dict] | None = None) -> dict:
    prepared = prepare_answer(question, history)
    if prepared["kind"] == "direct":
        return {key: value for key, value in prepared.items() if key != "kind"}
    response = get_chat_model().invoke(prepared["prompt"])
    return {
        "answer": response.content,
        "source": prepared["source"],
        "sources": prepared["sources"],
    }


def stream_answer(prepared: dict) -> Iterator[str]:
    if prepared["kind"] == "direct":
        for token in re.findall(r"\S+\s*", prepared["answer"]):
            yield token
        return
    for chunk in get_chat_model().stream(prepared["prompt"]):
        if chunk.content:
            yield chunk.content
