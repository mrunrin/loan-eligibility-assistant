import json
import logging
import math
import os
import re
import sys
import threading
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import nltk

import requests
from langchain_chroma import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_core.documents import Document
from langchain_ollama import ChatOllama, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import CrossEncoder

from app.config import (
    CHAT_MODEL,
    DATA_DIR,
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
_parent_documents: dict[str, Document] = {}
_child_documents: list[Document] = []
_bm25_index: dict = {}
_vectorstore_lock = threading.Lock()
CHROMA_COLLECTION_NAME = "loan_policy_clause_hybrid_v2"
DENSE_CANDIDATE_K = 20   # widened: more candidates for cross-encoder to pick from
BM25_CANDIDATE_K = 20    # widened: more candidates for cross-encoder to pick from
FINAL_EVIDENCE_K = 5
RRF_K = 60
RERANKER_MODEL = "BAAI/bge-reranker-base"
_reranker: CrossEncoder | None = None
CHUNK_MAX_CHARS = 800       # target max chars per sentence-level chunk
CHUNK_OVERLAP_SENTS = 1     # sentences carried over from previous chunk for context
_COMPOUND_SPLIT_RE = re.compile(
    r"(?:,?\s+and\s+(?:also\s+)?what\b)"
    r"|(?:,?\s+also,?\s+what\b)"
    r"|(?:\?\s+(?:also,?\s+)?what\b)"
    r"|(?:\?\s+(?:and\s+)?(?:additionally|furthermore),?\s+what\b)",
    re.I,
)
PARENT_HEADING_RE = re.compile(
    r"^(\d{1,2}\.\s+[A-Z][A-Z0-9 ,/&()'-]{3,}|SCHEDULE\b|WHEREAS:|LOAN AGREEMENT\b)"
)
CLAUSE_RE = re.compile(r"(?<!\d)(\d{1,2}(?:\.\d+)+)\.?\s+")
TOKEN_RE = re.compile(r"\d+(?:\.\d+)*|[a-z]+")
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "for", "of", "in", "on",
    "to", "at", "by", "with", "from", "and", "or", "not", "it", "its",
    "this", "that", "as", "if", "any", "all", "no",
})


_CACHE_FILENAME = "documents_cache.json"


def _is_interactive() -> bool:
    """True only when running locally with a real terminal (not inside Docker)."""
    return not os.path.exists("/.dockerenv") and sys.stdin.isatty()


def _cache_path() -> Path:
    return VECTORSTORE_PATH / _CACHE_FILENAME


def _save_documents_cache(parents: list[Document], children: list[Document]) -> None:
    VECTORSTORE_PATH.mkdir(parents=True, exist_ok=True)
    payload = {
        "parents": [{"page_content": d.page_content, "metadata": d.metadata} for d in parents],
        "children": [{"page_content": d.page_content, "metadata": d.metadata} for d in children],
    }
    _cache_path().write_text(json.dumps(payload), encoding="utf-8")
    logger.info("Documents cache saved to %s", _cache_path())


def _load_documents_cache() -> tuple[list[Document], list[Document]] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        parents = [Document(page_content=d["page_content"], metadata=d["metadata"]) for d in data["parents"]]
        children = [Document(page_content=d["page_content"], metadata=d["metadata"]) for d in data["children"]]
        return parents, children
    except Exception:
        logger.warning("Documents cache is corrupt or unreadable; will rebuild from PDFs.")
        return None


def _rebuild_requested(has_existing: bool) -> bool:
    """Return True when the ingestion layer should be rebuilt.

    Priority:
    1. FORCE_REBUILD_VECTORSTORE env var (works in Docker and locally).
    2. Interactive yes/no prompt — only shown when a real terminal is attached
       and the store already has data (i.e. this is not a first-time build).
    """
    if os.getenv("FORCE_REBUILD_VECTORSTORE", "").lower() in {"1", "true", "yes"}:
        logger.info("FORCE_REBUILD_VECTORSTORE is set; rebuilding ingestion layer.")
        return True
    if has_existing and _is_interactive():
        answer = input(
            "\nVector store already has data. Rebuild the ingestion layer from PDFs? [y/N]: "
        ).strip().lower()
        return answer in {"y", "yes"}
    return False


def get_chat_model() -> ChatOllama:
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
        "https://router.huggingface.co/v1/chat/completions",
        headers={"Authorization": f"Bearer {HF_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": HF_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 350,
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def _stream_groq(prompt: str) -> Iterator[str]:
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "stream": True,
        },
        stream=True,
        timeout=120,
    )
    response.raise_for_status()
    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        data = line[6:]
        if data == "[DONE]":
            break
        try:
            content = json.loads(data)["choices"][0]["delta"].get("content", "")
            if content:
                yield content
        except (json.JSONDecodeError, KeyError):
            continue


def _call_fallback_model(prompt: str) -> str:
    if FALLBACK_MODEL_PROVIDER == "groq" and GROQ_API_KEY:
        return _call_groq(prompt)
    if FALLBACK_MODEL_PROVIDER in {"hf", "huggingface"} and HF_API_KEY:
        return _call_huggingface(prompt)
    raise RuntimeError("No fallback model provider is configured.")


def _pdf_paths() -> list[Path]:
    if PDF_PATH is not None:
        if not PDF_PATH.exists():
            raise FileNotFoundError(f"Policy PDF not found: {PDF_PATH}")
        return [PDF_PATH]

    paths = sorted(DATA_DIR.glob("*.pdf"))
    if not paths:
        raise FileNotFoundError(f"No PDF files found in {DATA_DIR}")
    return paths


def _section_title(text: str) -> str:
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" .;-")
        if line and not line.upper().startswith("VER "):
            return line[:120]
    return "Untitled section"


def _clean_page_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        clean = re.sub(r"\s+", " ", line).strip()
        if not clean or clean.upper().startswith("VER "):
            continue
        lines.append(clean)
    return "\n".join(lines)


def _is_parent_heading(line: str) -> bool:
    clean = re.sub(r"\s+", " ", line).strip()
    return bool(PARENT_HEADING_RE.match(clean))


def _split_page_into_sections(text: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return []

    sections: list[str] = []
    current: list[str] = []
    for line in lines:
        if current and _is_parent_heading(line):
            sections.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append("\n".join(current))
    return sections


def _build_parent_documents() -> list[Document]:
    parents: list[Document] = []
    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1800,
        chunk_overlap=150,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    for pdf_path in _pdf_paths():
        pages = PyPDFLoader(str(pdf_path)).load()
        for page_doc in pages:
            page = int(page_doc.metadata.get("page", 0)) + 1
            source_file = pdf_path.name
            page_text = _clean_page_text(page_doc.page_content)
            if not page_text:
                continue

            page_sections = _split_page_into_sections(page_text) or [page_text]
            parent_texts: list[str] = []
            for section in page_sections:
                parent_texts.extend(parent_splitter.split_text(section))

            for index, parent_text in enumerate(parent_texts, start=1):
                parent_id = f"{source_file}:p{page}:s{index}"
                parents.append(Document(
                    page_content=parent_text,
                    metadata={
                        "parent_id": parent_id,
                        "source_file": source_file,
                        "source": str(pdf_path),
                        "page": page,
                        "section": _section_title(parent_text),
                    },
                ))
    return parents


def _clause_label(text: str) -> str:
    match = CLAUSE_RE.search(text)
    return match.group(1) if match else ""


def _split_by_clause(text: str) -> list[tuple[str, str]]:
    matches = list(CLAUSE_RE.finditer(text))
    if not matches:
        return [(text, "")]

    chunks: list[tuple[str, str]] = []
    prefix = text[:matches[0].start()].strip()
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if index == 0 and prefix:
            chunk = f"{prefix}\n{chunk}"
        chunks.append((chunk, match.group(1)))
    return chunks


def _ensure_nltk_punkt() -> None:
    """Download the NLTK punkt sentence tokenizer data if not already cached."""
    try:
        nltk.data.find("tokenizers/punkt_tab")
    except LookupError:
        logger.info("Downloading NLTK punkt_tab tokenizer data...")
        nltk.download("punkt_tab", quiet=True)


def _sentence_chunks(text: str) -> list[str]:
    """Split text into chunks at sentence boundaries — never mid-sentence.

    How it works:
    - Tokenise into sentences using nltk.sent_tokenize (handles legal abbreviations
      like "Clause 9.1." or "Rs." without splitting at those dots).
    - Accumulate whole sentences into a chunk until adding the next sentence
      would push past CHUNK_MAX_CHARS.
    - When the limit would be exceeded, flush the current chunk and start a new
      one, carrying CHUNK_OVERLAP_SENTS sentence(s) forward for context.
    - If a single sentence is longer than CHUNK_MAX_CHARS it becomes its own
      chunk — we never split inside a sentence.
    """
    _ensure_nltk_punkt()
    sentences = nltk.sent_tokenize(text)
    if not sentences:
        return [text] if text.strip() else []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent)
        # +1 accounts for the space that joins sentences
        if current_len + sent_len + 1 > CHUNK_MAX_CHARS and current:
            chunks.append(" ".join(current))
            # Carry over the last N sentences so cross-chunk context is preserved
            current = current[-CHUNK_OVERLAP_SENTS:]
            current_len = sum(len(s) + 1 for s in current)
        current.append(sent)
        current_len += sent_len + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


def _build_child_documents(parents: list[Document]) -> list[Document]:
    """Build child evidence chunks from parent sections using sentence-level splitting.

    Each clause text is first kept whole if it fits within CHUNK_MAX_CHARS.
    For longer clauses, _sentence_chunks() groups whole sentences into chunks —
    never splitting mid-sentence — and carries one sentence of overlap between
    adjacent chunks so context is not lost at boundaries.
    """
    children: list[Document] = []
    for parent in parents:
        for clause_text, clause in _split_by_clause(parent.page_content):
            # Keep short clauses as a single chunk; split longer ones by sentence
            split_texts = (
                _sentence_chunks(clause_text)
                if len(clause_text) > CHUNK_MAX_CHARS
                else [clause_text]
            )
            for child in split_texts:
                if not child.strip():
                    continue   # skip empty chunks
                children.append(Document(
                    page_content=child,
                    metadata={
                        "parent_id": parent.metadata["parent_id"],
                        "source_file": parent.metadata["source_file"],
                        "source": parent.metadata["source"],
                        "page": parent.metadata["page"],
                        "section": parent.metadata["section"],
                        "clause": clause or _clause_label(child),
                    },
                ))
    return children


def _tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _build_bm25_index(documents: list[Document]) -> dict:
    tokenized_docs = [_tokenize(document.page_content) for document in documents]
    term_counts = [Counter(tokens) for tokens in tokenized_docs]
    doc_lengths = [len(tokens) for tokens in tokenized_docs]
    doc_count = len(documents)
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized_docs:
        document_frequency.update(set(tokens))

    idf = {
        term: math.log(1 + (doc_count - frequency + 0.5) / (frequency + 0.5))
        for term, frequency in document_frequency.items()
    }
    return {
        "term_counts": term_counts,
        "doc_lengths": doc_lengths,
        "avg_doc_length": sum(doc_lengths) / doc_count if doc_count else 0,
        "idf": idf,
    }


def _bm25_search(question: str, limit: int = BM25_CANDIDATE_K) -> list[tuple[Document, float]]:
    if not _child_documents or not _bm25_index:
        return []

    query_terms = _tokenize(question)
    if not query_terms:
        return []

    k1 = 1.5
    b = 0.75
    avgdl = _bm25_index["avg_doc_length"] or 1
    scores: list[tuple[int, float]] = []
    for index, term_counts in enumerate(_bm25_index["term_counts"]):
        doc_length = _bm25_index["doc_lengths"][index] or 1
        score = 0.0
        for term in query_terms:
            frequency = term_counts.get(term, 0)
            if not frequency:
                continue
            idf = _bm25_index["idf"].get(term, 0.0)
            denominator = frequency + k1 * (1 - b + b * doc_length / avgdl)
            score += idf * (frequency * (k1 + 1)) / denominator
        if score > 0:
            scores.append((index, score))

    scores.sort(key=lambda item: item[1], reverse=True)
    return [(_child_documents[index], score) for index, score in scores[:limit]]


def _candidate_id(document: Document) -> str:
    metadata = document.metadata
    return "|".join(
        str(metadata.get(key, ""))
        for key in ("source_file", "page", "section", "clause")
    ) + f"|{document.page_content[:80]}"


def _get_reranker() -> CrossEncoder:
    """Lazy-load the cross-encoder model once per process."""
    global _reranker
    if _reranker is None:
        logger.info("Loading cross-encoder reranker: %s", RERANKER_MODEL)
        _reranker = CrossEncoder(RERANKER_MODEL)
        logger.info("Cross-encoder reranker loaded.")
    return _reranker


def _rerank_candidates(
    question: str,
    dense_candidates: list[Document],
    bm25_candidates: list[tuple[Document, float]],
) -> list[Document]:
    """Merge dense + BM25 candidates then rerank with a neural CrossEncoder.

    The old RRF + term-coverage approach boosted chunks that shared surface
    words with the question (e.g. Clause 5.3 contains "stop payment" verbatim)
    and silently dropped semantically critical chunks like Clause 9.1(d) and
    9.3(a) that answer the *meaning* of the question but use different wording.

    The CrossEncoder reads (question, chunk) as a pair and scores by actual
    semantic relevance, so it correctly surfaces multi-clause answers that
    require reasoning across paraphrased legal text.
    """
    # --- Step 1: merge and deduplicate all candidates ---
    documents: dict[str, Document] = {}
    for document in dense_candidates:
        key = _candidate_id(document)
        documents[key] = document
    for document, _ in bm25_candidates:
        key = _candidate_id(document)
        documents[key] = document

    unique_docs = list(documents.values())
    if not unique_docs:
        return []

    # --- Step 2: score every unique candidate with the cross-encoder ---
    try:
        reranker = _get_reranker()
        pairs = [(question, doc.page_content) for doc in unique_docs]
        ce_scores: list[float] = reranker.predict(pairs).tolist()
    except Exception:
        logger.exception(
            "Cross-encoder reranking failed; falling back to first %d candidates.",
            FINAL_EVIDENCE_K,
        )
        return unique_docs[:FINAL_EVIDENCE_K]

    # --- Step 3: sort by cross-encoder score, return top-k (no drop threshold) ---
    ranked = sorted(zip(unique_docs, ce_scores), key=lambda x: x[1], reverse=True)
    return [doc for doc, _ in ranked[:FINAL_EVIDENCE_K]]


def initialize_vectorstore() -> None:
    """Load the persisted store once per process; skip re-embedding when data exists."""
    global _vectorstore, _parent_documents, _child_documents, _bm25_index
    with _vectorstore_lock:
        if _vectorstore is not None:
            return

        embeddings = OllamaEmbeddings(model=EMBEDDING_MODEL, base_url=OLLAMA_BASE_URL)
        store = Chroma(
            collection_name=CHROMA_COLLECTION_NAME,
            persist_directory=str(VECTORSTORE_PATH),
            embedding_function=embeddings,
        )
        has_existing = bool(store.get(limit=1).get("ids"))
        cached = _load_documents_cache() if has_existing else None
        rebuild = _rebuild_requested(has_existing)

        if cached and not rebuild:
            parent_documents, child_documents = cached
            logger.info(
                "Loaded ingestion layer from cache (%s parents, %s chunks).",
                len(parent_documents), len(child_documents),
            )
        else:
            parent_documents = _build_parent_documents()
            if not parent_documents:
                raise RuntimeError("No usable PDF content was found for retrieval.")
            child_documents = _build_child_documents(parent_documents)
            _save_documents_cache(parent_documents, child_documents)
            if has_existing and rebuild:
                store.delete_collection()
            store = Chroma.from_documents(
                documents=child_documents,
                embedding=embeddings,
                persist_directory=str(VECTORSTORE_PATH),
                collection_name=CHROMA_COLLECTION_NAME,
            )
            logger.info("Built vector store with %s evidence chunks.", len(child_documents))

        _parent_documents = {d.metadata["parent_id"]: d for d in parent_documents}
        _child_documents = child_documents
        _bm25_index = _build_bm25_index(_child_documents)
        _vectorstore = store
        logger.info(
            "Vector store ready: %s parent sections, %s evidence chunks.",
            len(_parent_documents), len(_child_documents),
        )

    # Warm up the cross-encoder so the first query doesn't pay model-load latency
    try:
        _get_reranker()
    except Exception:
        logger.warning("Cross-encoder warm-up failed; it will be loaded on first query.")

    # Ensure NLTK punkt tokenizer data is downloaded (used by _sentence_chunks)
    _ensure_nltk_punkt()


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


def _truncate_excerpt(text: str, limit: int = 700) -> str:
    """Return excerpt ending at a complete sentence, never mid-word or mid-sentence."""
    if len(text) <= limit:
        return text
    window = text[:limit]
    # Find the last sentence-ending punctuation followed by a space or end of window
    # Covers: ". ", ".\n", "? ", "! ", "; " patterns common in legal text
    sentence_end = re.compile(r"[.?!;]\s")
    matches = list(sentence_end.finditer(window))
    if matches:
        # Use the last sentence boundary found — always a complete sentence
        last = matches[-1]
        return window[:last.end()].rstrip()
    # No sentence boundary found — fall back to last space to avoid mid-word cut
    last_space = window.rfind(" ")
    if last_space > limit // 2:
        return window[:last_space].rstrip() + "…"
    return window.rstrip() + "…"


def _sources(documents: list) -> list[dict]:
    citations: list[dict] = []
    seen: set[tuple[str, int, str, str]] = set()
    for document in documents:
        page = int(document.metadata.get("page", 1))
        source_file = document.metadata.get("source_file") or Path(document.metadata.get("source", "policy.pdf")).name
        section = document.metadata.get("section", "Policy section")
        clause = document.metadata.get("clause", "")
        key = (source_file, page, section, clause)
        if key in seen:
            continue
        seen.add(key)
        label = f"Clause {clause}" if clause else section
        citations.append({
            "document": source_file,
            "page": page,
            "section": label,
            "excerpt": _truncate_excerpt(document.page_content),
        })
    return citations


def _load_pdf_documents() -> list[Document]:
    return _build_parent_documents()


def _decompose_question(question: str) -> list[str]:
    """Return sub-queries for compound questions; always includes the original."""
    parts = _COMPOUND_SPLIT_RE.split(question)
    if len(parts) < 2:
        return [question]
    sub_queries = [question]
    first = parts[0].strip().rstrip(",")
    if not first.endswith("?"):
        first += "?"
    sub_queries.append(first)
    # Rejoin trailing parts as a standalone question
    tail = " ".join(p.strip() for p in parts[1:]).strip().rstrip("?")
    if tail:
        sub_queries.append(f"What {tail}?" if not tail[0].isupper() else f"{tail}?")
    return list(dict.fromkeys(sub_queries))  # deduplicate, preserve order


def _retrieve_documents(question: str) -> list:
    try:
        initialize_vectorstore()
        assert _vectorstore is not None
        sub_queries = _decompose_question(question)
        all_dense: list[Document] = []
        all_bm25: list[tuple[Document, float]] = []
        for q in sub_queries:
            all_dense.extend(_vectorstore.similarity_search(q, k=DENSE_CANDIDATE_K))
            all_bm25.extend(_bm25_search(q))
        return _rerank_candidates(question, all_dense, all_bm25)
    except Exception:
        logger.exception("Hybrid retrieval failed")
        return []


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
    policy_context = "\n\n".join(
        (
            f"Source: {document.metadata.get('source_file', 'policy.pdf')} "
            f"- page {document.metadata.get('page', 1)} "
            f"- section {document.metadata.get('section', 'Policy section')} "
            f"- clause {document.metadata.get('clause') or 'not specified'}\n"
            f"{document.page_content}"
        )
        for document in documents
    )
    conversation_context = _conversation_context(history, question)
    prompt = f"""
You are LoanBot, a banking loan eligibility assistant.

Grounding rules:
- READ every policy passage carefully before deciding whether the information is present.
- Use only facts explicitly written in the policy context.
- Do not add advice, requirements, assumptions, banking best practices, or common lending knowledge.
- Do not mention documents, verification, approval, rejection, or next steps unless those exact ideas appear in the policy context.
- If the policy gives only numbers or limits, answer only with those numbers or limits.
- ONLY say "The policy does not specify that." when the answer is genuinely absent from ALL policy passages below.
- Some passages are blank template fields (e.g. "Rate of interest: ___" or "% p.a." with nothing before it). If another passage gives the actual value, use that value — do not let blank template fields override a passage that contains a real number.
- Use conversation history only to resolve follow-up references such as "what about self-employed applicants"; it is not a policy source.
- Keep the answer brief and factual.
- IMPORTANT: If the user's question mentions a specific agreement name (e.g. "Utkarsh", "MBBL", "Jana"), ignore that name. Answer using whatever policy context passages are provided below — they ARE the relevant agreements.
- IMPORTANT: If ANY policy passage below directly addresses the user's question, you MUST use it to answer. Do NOT say "The policy does not specify that" when the answer is clearly present in the context.

Example 1 — answer IS in the context, answer it directly:
User question: What salary is required?
Policy context: Salaried applicants must have a minimum monthly income of Rs. 30,000. Self-employed applicants must have a minimum monthly income of Rs. 50,000.
Correct answer: Salaried applicants need a minimum monthly income of Rs. 30,000. Self-employed applicants need a minimum monthly income of Rs. 50,000.
Incorrect answer: Applicants should provide documents to verify income before applying.

Example 2 — answer IS in a table or schedule entry, still answer it:
User question: On what date is the EMI due?
Policy context: 18. Pre-EMI/EMI Due date   11th date of every Month
Correct answer: The EMI is due on the 11th of every month.
Wrong answer: The policy does not specify that.

Example 3 — answer IS a duration buried in a clause, still answer it:
User question: How long can a borrower travel abroad for a short visit?
Policy context: the Borrower/s may leave India for the purpose of short visits/trips not exceeding a period of 60 (Sixty) days at any given point of time
Correct answer: A borrower may leave India for short visits not exceeding 60 days at any one time.
Wrong answer: The policy does not specify that.

Example 4 — answer IS present even when user mentions a specific agreement name:
User question: Can a borrower dispute EMI calculation and withhold EMI under the Utkarsh agreement?
Policy context: 3.9. Any dispute being raised about the computation of any EMI will not entitle the Borrower/s to withhold payment of EMI or any portion thereof.
Correct answer: No. Under the agreement, raising a dispute about EMI computation does not entitle the borrower to withhold payment of EMI or any portion thereof. The obligation to pay EMI is absolute and unconditional.
Wrong answer: The policy does not specify that.

Conversation history:
{conversation_context or "No earlier messages."}

Policy context:
{policy_context or "No relevant policy evidence was retrieved."}

User question:
{question}
"""
    return {
        "kind": "llm",
        "prompt": prompt,
        "source": (
            f"{sources[0]['document']}, page {sources[0]['page']}"
            if sources
            else "No policy source found"
        ),
        "sources": sources,
    }


def answer_question(question: str, history: list[dict] | None = None, provider: str = "local") -> dict:
    prepared = prepare_answer(question, history)
    if prepared["kind"] == "direct":
        return {key: value for key, value in prepared.items() if key != "kind"}
    prompt = prepared["prompt"]
    ready, reason = _provider_ready(provider)
    if not ready:
        logger.warning("Provider '%s' not ready: %s. Falling back to local.", provider, reason)
        provider = "local"
    try:
        if provider == "groq":
            answer = _call_groq(prompt)
        elif provider in ("hf", "huggingface"):
            answer = _call_huggingface(prompt)
        else:
            answer = get_chat_model().invoke(prompt).content
    except Exception:
        logger.exception("Provider '%s' failed; falling back to local Ollama", provider)
        try:
            answer = get_chat_model().invoke(prompt).content
        except Exception:
            logger.exception("Local Ollama also failed")
            answer = "Sorry, all providers are currently unavailable. Please check your configuration."
    return {
        "answer": answer,
        "source": prepared["source"],
        "sources": prepared["sources"],
    }


def _provider_ready(provider: str) -> tuple[bool, str]:
    if provider == "groq" and not GROQ_API_KEY:
        return False, "Groq API key is not configured. Add GROQ_API_KEY to your .env file."
    if provider in ("hf", "huggingface") and not HF_API_KEY:
        return False, "Hugging Face API key is not configured. Add HF_API_KEY to your .env file."
    return True, ""


def stream_answer(prepared: dict, provider: str = "local") -> Iterator[str]:
    if prepared["kind"] == "direct":
        for token in re.findall(r"\S+\s*", prepared["answer"]):
            yield token
        return
    prompt = prepared["prompt"]
    ready, reason = _provider_ready(provider)
    if not ready:
        yield f"⚠️ {reason} Falling back to local Ollama.\n\n"
        provider = "local"
    try:
        if provider == "groq":
            yield from _stream_groq(prompt)
        elif provider in ("hf", "huggingface"):
            answer = _call_huggingface(prompt)
            for token in re.findall(r"\S+\s*", answer):
                yield token
        else:
            for chunk in get_chat_model().stream(prompt):
                if chunk.content:
                    yield chunk.content
    except Exception:
        logger.exception("Provider '%s' failed; falling back to local Ollama", provider)
        yield "\n\n⚠️ Provider failed. Falling back to local Ollama.\n\n"
        try:
            for chunk in get_chat_model().stream(prompt):
                if chunk.content:
                    yield chunk.content
        except Exception:
            yield "\n\n[All providers failed. Please check your configuration.]"
