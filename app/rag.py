import logging
import re
import threading
from collections.abc import Iterator

import requests
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import (
    CHAT_MODEL,
    EMBEDDING_MODEL,
    FALLBACK_MODEL_PROVIDER,
    GROQ_API_KEY,
    GROQ_MODEL,
    HF_API_KEY,
    HF_MODEL,
    MAX_HISTORY_MESSAGES,
    OLLAMA_BASE_URL,
    PDF_PATH,
    VECTORSTORE_PATH,
)

logger = logging.getLogger(__name__)
_vectorstore: Chroma | None = None
_vectorstore_lock = threading.Lock()


def is_ollama_available() -> bool:
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        return response.status_code == 200
    except Exception:
        return False


def get_chat_model() -> ChatOllama:
    if not is_ollama_available():
        raise RuntimeError(f"Ollama is not available at {OLLAMA_BASE_URL}.")
    return ChatOllama(
        model=CHAT_MODEL,
        base_url=OLLAMA_BASE_URL,
        reasoning=False,
        temperature=0,
    )


def _call_groq(prompt: str) -> str:
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "reasoning_effort": "low",
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _call_huggingface(prompt: str) -> str:
    response = requests.post(
        f"https://api-inference.huggingface.co/models/{HF_MODEL}",
        headers={"Authorization": f"Bearer {HF_API_KEY}"},
        json={"inputs": prompt, "parameters": {"max_new_tokens": 350}},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, list) and payload:
        return payload[0].get("generated_text", "").replace(prompt, "").strip()
    return str(payload)


def _call_fallback_model(prompt: str) -> str:
    if FALLBACK_MODEL_PROVIDER == "groq" and GROQ_API_KEY:
        return _call_groq(prompt)
    if FALLBACK_MODEL_PROVIDER in {"hf", "huggingface"} and HF_API_KEY:
        return _call_huggingface(prompt)
    raise RuntimeError(
        "No fallback model provider is configured. Set FALLBACK_MODEL_PROVIDER to 'groq' or 'huggingface' and provide the matching API key, or install and run Ollama."
    )


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


def _load_pdf_documents() -> list:
    if not PDF_PATH.exists():
        raise FileNotFoundError(f"Policy PDF not found: {PDF_PATH}")
    return PyPDFLoader(str(PDF_PATH)).load()


def _retrieve_documents(question: str) -> list:
    try:
        initialize_vectorstore()
        assert _vectorstore is not None
        return _vectorstore.similarity_search(question, k=3)
    except Exception:
        logger.exception("Vector retrieval failed; falling back to raw PDF context")
        return _load_pdf_documents()


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

    documents = _retrieve_documents(question)
    sources = _sources(documents)
    policy_context = "\n\n".join(document.page_content for document in documents)
    conversation_context = _conversation_context(history, question)
    prompt = f"""
You are LoanBot, a banking loan eligibility assistant.

Grounding rules:
- Use only facts explicitly written in the policy context.
- Do not add advice, requirements, assumptions, banking best practices, or common lending knowledge.
- Do not mention documents, verification, approval, rejection, or next steps unless those exact ideas appear in the policy context.
- If the policy gives only numbers or limits, answer only with those numbers or limits.
- If the policy context does not support an answer, say: "The policy does not specify that."
- Use conversation history only to resolve follow-up references such as "what about self-employed applicants"; it is not a policy source.
- Keep the answer brief and factual.

Example:
User question: What salary is required?
Policy context: Salaried applicants must have a minimum monthly income of Rs. 30,000. Self-employed applicants must have a minimum monthly income of Rs. 50,000.
Correct answer: Salaried applicants need a minimum monthly income of Rs. 30,000. Self-employed applicants need a minimum monthly income of Rs. 50,000.
Incorrect answer: Applicants should provide documents to verify income before applying.

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

    if not is_ollama_available():
        logger.warning("Ollama is not available; using configured fallback provider")
        answer = _call_fallback_model(prepared["prompt"])
        return {
            "answer": answer,
            "source": prepared["source"],
            "sources": prepared["sources"],
        }

    try:
        response = get_chat_model().invoke(prepared["prompt"])
        answer = response.content
    except Exception:
        logger.exception("Ollama invoke failed; trying fallback provider")
        answer = _call_fallback_model(prepared["prompt"])
    return {
        "answer": answer,
        "source": prepared["source"],
        "sources": prepared["sources"],
    }


def stream_answer(prepared: dict) -> Iterator[str]:
    if prepared["kind"] == "direct":
        for token in re.findall(r"\S+\s*", prepared["answer"]):
            yield token
        return

    if not is_ollama_available():
        logger.warning("Ollama is not available; using configured fallback provider")
        answer = _call_fallback_model(prepared["prompt"])
        for token in re.findall(r"\S+\s*", answer):
            yield token
        return

    try:
        for chunk in get_chat_model().stream(prepared["prompt"]):
            if chunk.content:
                yield chunk.content
    except Exception:
        logger.exception("Ollama streaming failed; trying fallback provider")
        answer = _call_fallback_model(prepared["prompt"])
        for token in re.findall(r"\S+\s*", answer):
            yield token
