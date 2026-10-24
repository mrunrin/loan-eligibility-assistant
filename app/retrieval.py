"""Search index over the policy corpus: incremental ingestion, hybrid dense + BM25 search, cross-encoder rerank."""

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path

import requests
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_ollama import OllamaEmbeddings

try:
    # Only RERANKER_PROVIDER=local needs this (and torch); the Docker image leaves both out.
    from sentence_transformers import CrossEncoder
    _CROSS_ENCODER_AVAILABLE = True
except ImportError:
    CrossEncoder = None  # type: ignore[assignment, misc]
    _CROSS_ENCODER_AVAILABLE = False

from app.chunking import (
    CHUNKER_VERSION,
    _build_child_documents,
    _build_md_documents,
    _build_parent_documents,
    _ensure_nltk_punkt,
    _md_paths,
    _pdf_paths,
)
from app.config import (
    COHERE_API_KEY,
    EMBEDDING_MODEL,
    EMBEDDING_PROVIDER,
    JINA_API_KEY,
    KNOWN_LENDER_NAMES,
    OLLAMA_BASE_URL,
    RERANKER_MODEL,
    RERANKER_PROVIDER,
    VECTORSTORE_PATH,
)

logger = logging.getLogger(__name__)
_vectorstore: Chroma | None = None
_parent_documents: dict[str, Document] = {}
_child_documents: list[Document] = []
_bm25_index: dict = {}
_lender_files: dict[str, set[str]] = {}
_vectorstore_lock = threading.Lock()
_reranker_lock = threading.Lock()
COLLECTION_PREFIX = "loan_policy_"
# Stable collection name: incremental ingestion appends to this instead of creating a new one.
STABLE_COLLECTION = f"{COLLECTION_PREFIX}v1"
MANIFEST_FILENAME = "ingest_manifest.json"
DENSE_CANDIDATE_K = 20
BM25_CANDIDATE_K = 20
FINAL_EVIDENCE_K = 3
RERANKER_ENABLED = os.getenv("RERANKER_ENABLED", "true").lower() in {"1", "true", "yes"}
_reranker: "CrossEncoder | None" = None  # quoted: CrossEncoder is None when sentence-transformers is absent
_API_TIMEOUT = 30
_API_RETRIES = 4
_COMPOUND_SPLIT_RE = re.compile(
    r"(?:,?\s+and\s+(?:also\s+)?what\b)"
    r"|(?:,?\s+also,?\s+what\b)"
    r"|(?:\?\s+(?:also,?\s+)?what\b)"
    r"|(?:\?\s+(?:and\s+)?(?:additionally|furthermore),?\s+what\b)",
    re.I,
)
TOKEN_RE = re.compile(r"\d+(?:\.\d+)*|[a-z]+")


class RetrievalError(RuntimeError):
    """Raised when the vector store or embeddings are unavailable."""


class NomicEmbeddings(OllamaEmbeddings):
    """nomic-embed-text is trained with task prefixes; without them query and document vectors are misaligned."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return super().embed_documents([f"search_document: {t}" for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return super().embed_query(f"search_query: {text}")


def _post_with_retry(url: str, api_key: str, payload: dict) -> dict:
    """POST to a hosted model API; on 429/5xx wait (honouring Retry-After) and retry."""
    for attempt in range(_API_RETRIES):
        response = requests.post(
            url, json=payload, timeout=_API_TIMEOUT, headers={"Authorization": f"Bearer {api_key}"}
        )
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == _API_RETRIES - 1:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After", "")
            time.sleep(min(float(retry_after), 30) if retry_after.replace(".", "", 1).isdigit() else 2 ** (attempt + 1))
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("unreachable")


class JinaEmbeddings(Embeddings):
    """Jina embeddings API; query and passage vectors use their own task adapters, like nomic's prefixes."""

    url = "https://api.jina.ai/v1/embeddings"

    def __init__(self, model: str, api_key: str):
        self.model = model
        self.api_key = api_key

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        body = _post_with_retry(self.url, self.api_key, {"model": self.model, "task": task, "input": texts})
        return [item["embedding"] for item in sorted(body["data"], key=lambda item: item["index"])]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "retrieval.passage")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "retrieval.query")[0]


def _embeddings() -> Embeddings:
    if EMBEDDING_PROVIDER == "jina":
        if not JINA_API_KEY:
            raise RuntimeError("EMBEDDING_PROVIDER=jina needs JINA_API_KEY (free key: https://jina.ai/embeddings/).")
        return JinaEmbeddings(model=EMBEDDING_MODEL, api_key=JINA_API_KEY)
    embedding_class = NomicEmbeddings if "nomic" in EMBEDDING_MODEL else OllamaEmbeddings
    return embedding_class(model=EMBEDDING_MODEL, base_url=OLLAMA_BASE_URL)


def _cohere_scores(question: str, texts: list[str]) -> list[float]:
    """Relevance score (0..1) for every text, in input order, from Cohere Rerank."""
    if not COHERE_API_KEY:
        raise RuntimeError("RERANKER_PROVIDER=cohere needs COHERE_API_KEY (free trial key: https://dashboard.cohere.com/api-keys).")
    body = _post_with_retry(
        "https://api.cohere.com/v2/rerank", COHERE_API_KEY,
        {"model": RERANKER_MODEL, "query": question, "documents": texts, "top_n": len(texts)},
    )
    scores = [0.0] * len(texts)
    for result in body["results"]:
        scores[result["index"]] = float(result["relevance_score"])
    return scores


def _product_ok(document: Document, products: set[str] | None) -> bool:
    return not products or document.metadata.get("product", "general") in products


def _manifest_path() -> Path:
    return VECTORSTORE_PATH / MANIFEST_FILENAME


def _load_manifest() -> dict:
    p = _manifest_path()
    if not p.exists():
        return {"chunker_version": "", "files": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"chunker_version": "", "files": {}}


def _save_manifest(manifest: dict) -> None:
    VECTORSTORE_PATH.mkdir(parents=True, exist_ok=True)
    _manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


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


def _bm25_search(
    question: str, limit: int = BM25_CANDIDATE_K, source_files: set[str] | None = None,
    products: set[str] | None = None,
) -> list[tuple[Document, float]]:
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
        if source_files and _child_documents[index].metadata.get("source_file") not in source_files:
            continue
        if not _product_ok(_child_documents[index], products):
            continue
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
    if metadata.get("chunk_id"):
        return metadata["chunk_id"]
    return "|".join(
        str(metadata.get(key, "")) for key in ("source_file", "page", "section", "clause")
    ) + f"|{document.page_content[:80]}"


def _get_reranker():
    """Lazy-load the cross-encoder once per process (double-checked so concurrent calls load it once)."""
    global _reranker
    if not RERANKER_ENABLED or RERANKER_PROVIDER != "local" or not _CROSS_ENCODER_AVAILABLE:
        return None
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                logger.info("Loading cross-encoder reranker: %s", RERANKER_MODEL)
                _reranker = CrossEncoder(RERANKER_MODEL)
                logger.info("Cross-encoder reranker loaded.")
    return _reranker


def _rerank_candidates(
    question: str,
    dense_candidates: list[Document],
    bm25_candidates: list[tuple[Document, float]],
) -> list[tuple[Document, float | None]]:
    """Merge dense + BM25 candidates and rerank them (Cohere API or the local cross-encoder).

    Returns (document, score) pairs; score is None when the reranker is unavailable, in which
    case the relevance cutoff cannot be applied and the merged order is used.
    """
    documents: dict[str, Document] = {}
    for document in dense_candidates:
        documents[_candidate_id(document)] = document
    for document, _ in bm25_candidates:
        documents[_candidate_id(document)] = document

    unique_docs = list(documents.values())
    if not unique_docs:
        return []

    try:
        if RERANKER_ENABLED and RERANKER_PROVIDER == "cohere":
            ce_scores = _cohere_scores(question, [doc.page_content for doc in unique_docs])
        else:
            reranker = _get_reranker()
            if reranker is None:
                logger.warning("Reranker not available; returning top %d candidates without reranking.", FINAL_EVIDENCE_K)
                return [(doc, None) for doc in unique_docs[:FINAL_EVIDENCE_K]]
            pairs = [(question, doc.page_content) for doc in unique_docs]
            ce_scores = reranker.predict(pairs).tolist()
    except Exception:
        logger.exception("Reranking failed; falling back to first %d candidates.", FINAL_EVIDENCE_K)
        return [(doc, None) for doc in unique_docs[:FINAL_EVIDENCE_K]]

    ranked = sorted(zip(unique_docs, ce_scores, strict=False), key=lambda x: x[1], reverse=True)
    return [(doc, float(score)) for doc, score in ranked[:FINAL_EVIDENCE_K]]


def _delete_stale_collections(store_path: Path, keep: str) -> None:
    import chromadb

    client = chromadb.PersistentClient(path=str(store_path))
    for collection in client.list_collections():
        name = getattr(collection, "name", collection)
        if name != keep:
            client.delete_collection(name)
            logger.info("Deleted stale Chroma collection %s", name)


def _build_lender_map(parents: list[Document]) -> dict[str, set[str]]:
    """Lender name → policy files that mention it (in the file name or the text)."""
    texts: dict[str, list[str]] = {}
    for parent in parents:
        texts.setdefault(parent.metadata["source_file"], []).append(parent.page_content)
    mapping: dict[str, set[str]] = {}
    for name in KNOWN_LENDER_NAMES:
        pattern = re.compile(rf"\b{re.escape(name)}\b", re.I)
        files = {f for f, chunks in texts.items() if pattern.search(f) or any(pattern.search(c) for c in chunks)}
        mapping[name.lower()] = files
    return mapping


def initialize_vectorstore() -> None:
    """Incremental ingestion: only embed new/changed files; reuse existing embeddings for unchanged ones."""
    global _vectorstore, _parent_documents, _child_documents, _bm25_index, _lender_files
    with _vectorstore_lock:
        if _vectorstore is not None:
            return

        embeddings = _embeddings()
        store = Chroma(
            collection_name=STABLE_COLLECTION,
            persist_directory=str(VECTORSTORE_PATH),
            embedding_function=embeddings,
        )

        manifest = _load_manifest()
        force_rebuild = os.getenv("FORCE_REBUILD_VECTORSTORE", "").lower() in {"1", "true", "yes"}
        chunker_changed = manifest.get("chunker_version") != CHUNKER_VERSION
        # Vectors from different embedding models are not comparable (or even the same length).
        embedder = f"{EMBEDDING_PROVIDER}:{EMBEDDING_MODEL}"
        embedder_changed = manifest.get("embedder", "ollama:nomic-embed-text") != embedder

        if force_rebuild or chunker_changed or embedder_changed:
            if chunker_changed:
                logger.info("Chunker version changed (%s → %s); wiping and rebuilding all embeddings.",
                            manifest.get("chunker_version"), CHUNKER_VERSION)
            if embedder_changed:
                logger.info("Embedding model changed (%s → %s); wiping and rebuilding all embeddings.",
                            manifest.get("embedder", "ollama:nomic-embed-text"), embedder)
            # Only delete+recreate if there are existing chunks — deleting a just-created collection
            # and immediately creating one with the same name triggers a Rust-backend NotFoundError.
            existing_chunks = manifest.get("files", {})
            if existing_chunks:
                store.delete_collection()
                store = Chroma(
                    collection_name=STABLE_COLLECTION,
                    persist_directory=str(VECTORSTORE_PATH),
                    embedding_function=embeddings,
                )
            manifest = {"chunker_version": CHUNKER_VERSION, "embedder": embedder, "files": {}}

        all_paths = _pdf_paths() + _md_paths()
        if not all_paths:
            raise RuntimeError("No usable PDF/MD content found for retrieval.")

        current_hashes = {p.name: _file_hash(p) for p in all_paths}
        known_files: dict = manifest.get("files", {})

        new_or_changed = [p for p in all_paths
                          if current_hashes[p.name] != known_files.get(p.name, {}).get("hash")]
        deleted_names = {name for name in known_files if name not in current_hashes}

        # Delete chunks for removed or changed files (O(1) by stored IDs — no full scan)
        for fname in deleted_names | {p.name for p in new_or_changed if p.name in known_files}:
            old_ids = known_files.pop(fname, {}).get("chunk_ids", [])
            if old_ids:
                store.delete(ids=old_ids)
                logger.info("Removed %d stale chunks for %s.", len(old_ids), fname)

        # Parse ALL source files (fast — no Ollama calls) to rebuild in-memory BM25 + parent index
        parent_documents = _build_parent_documents() + _build_md_documents()
        if not parent_documents:
            raise RuntimeError("No usable PDF content was found for retrieval.")
        child_documents = _build_child_documents(parent_documents)

        # Embed only new/changed files
        if new_or_changed:
            new_names = {p.name for p in new_or_changed}
            new_children = [c for c in child_documents if c.metadata["source_file"] in new_names]
            # shortcut: 1 doc per Ollama call to stay under 7 GB RAM; add sleep so runner can GC.
            # A hosted API takes batches, which keeps the request count inside free-tier rate limits.
            batch_size = 1 if EMBEDDING_PROVIDER == "ollama" else 32
            for _i in range(0, len(new_children), batch_size):
                _batch = new_children[_i:_i + batch_size]
                for _attempt in range(4):
                    try:
                        store.add_documents(_batch, ids=[d.metadata["chunk_id"] for d in _batch])
                        break
                    except Exception:
                        if _attempt == 3:
                            raise
                        time.sleep(5)
                if batch_size == 1 and _i % 20 == 0:
                    time.sleep(1)  # let Ollama runner breathe every 20 chunks
            # Group chunk IDs by source file for the manifest
            by_file: dict[str, list[str]] = {}
            for c in new_children:
                by_file.setdefault(c.metadata["source_file"], []).append(c.metadata["chunk_id"])
            for fname, chunk_ids in by_file.items():
                known_files[fname] = {"hash": current_hashes[fname], "chunk_ids": chunk_ids}
            logger.info("Ingested %d new/changed file(s): %d chunks embedded.",
                        len(new_or_changed), len(new_children))
        elif not deleted_names:
            logger.info("All %d file(s) unchanged — reusing existing embeddings.", len(all_paths))

        manifest["chunker_version"] = CHUNKER_VERSION
        manifest["embedder"] = embedder
        manifest["files"] = known_files
        _save_manifest(manifest)

        _delete_stale_collections(VECTORSTORE_PATH, keep=STABLE_COLLECTION)
        _parent_documents = {d.metadata["parent_id"]: d for d in parent_documents}
        _child_documents = child_documents
        _bm25_index = _build_bm25_index(_child_documents)
        _lender_files = _build_lender_map(parent_documents)
        _vectorstore = store
        logger.info("Vector store ready: %s parent sections, %s evidence chunks.",
                    len(_parent_documents), len(_child_documents))

    try:
        _get_reranker()
    except Exception:
        logger.warning("Cross-encoder warm-up failed; it will be loaded on first query.")
    _ensure_nltk_punkt()


def is_ready() -> bool:
    return _vectorstore is not None


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
    tail = " ".join(p.strip() for p in parts[1:]).strip().rstrip("?")
    if tail:
        sub_queries.append(f"What {tail}?" if not tail[0].isupper() else f"{tail}?")
    return list(dict.fromkeys(sub_queries))


def requested_documents(question: str) -> set[str] | None:
    """Policy files the question explicitly names (e.g. "HDFC"), or None when it names none."""
    files: set[str] = set()
    named = False
    for name, name_files in _lender_files.items():
        if re.search(rf"\b{re.escape(name)}\b", question, re.I):
            named = True
            files |= name_files
    return files if named else None


def retrieve_evidence(
    question: str, source_files: set[str] | None = None, product: str | None = None
) -> list[tuple[Document, float | None]]:
    """Hybrid dense + BM25 retrieval with rerank. Raises RetrievalError on failure.

    product limits evidence to that product's chunks plus general (product-agnostic) ones.
    """
    try:
        initialize_vectorstore()
        assert _vectorstore is not None
        if source_files is None:
            source_files = requested_documents(question)
        products = {product, "general"} if product else None
        clauses = []
        if source_files:
            clauses.append({"source_file": {"$in": sorted(source_files)}})
        if products:
            clauses.append({"product": {"$in": sorted(products)}})
        where = None if not clauses else clauses[0] if len(clauses) == 1 else {"$and": clauses}
        all_dense: list[Document] = []
        all_bm25: list[tuple[Document, float]] = []
        for q in _decompose_question(question):
            all_dense.extend(_vectorstore.similarity_search(q, k=DENSE_CANDIDATE_K, filter=where))
            all_bm25.extend(_bm25_search(q, source_files=source_files, products=products))
    except Exception as exc:
        logger.exception("Hybrid retrieval failed")
        raise RetrievalError(str(exc)) from exc
    from app.tracing import langfuse

    with langfuse.start_as_current_observation(
        name="rerank", as_type="span",
        input={"query": question, "dense_candidates": len(all_dense), "bm25_candidates": len(all_bm25)},
        metadata={"model": RERANKER_MODEL if RERANKER_ENABLED else "disabled"},
    ) as span:
        ranked = _rerank_candidates(question, all_dense, all_bm25)
        span.update(output=[{"chunk_id": d.metadata.get("chunk_id"), "score": s} for d, s in ranked])
    return ranked
