"""Policy files -> parent sections -> clause/sentence-level evidence chunks (pure functions, no state)."""

import logging
import re
from pathlib import Path

import nltk
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import DATA_DIR, PDF_PATH
from app.doc_pipeline import dedupe_ids, extract_document, make_chunk_id, serialize_table

logger = logging.getLogger(__name__)
# Bump when chunking logic changes — forces a full wipe+rebuild of all embeddings.
# v4: query/passage task prefixes on embeddings; product/publisher/effective_date metadata; MD source headers stripped.
CHUNKER_VERSION = "v4"
PRODUCTS = ("business_loan", "personal_loan", "credit_card")
CHUNK_MAX_CHARS = 800       # target max chars per sentence-level chunk
CHUNK_OVERLAP_SENTS = 1     # sentences carried over from previous chunk for context
MIN_TABLE_CHARS = 60        # serialized tables shorter than this are signature boxes, not content
PARENT_HEADING_RE = re.compile(
    r"^(\d{1,2}\.\s+[A-Z][A-Z0-9 ,/&()'-]{3,}|SCHEDULE\b|WHEREAS:|LOAN AGREEMENT\b)"
)
CLAUSE_RE = re.compile(r"(?<!\d)(\d{1,2}(?:\.\d+)+)\.?\s+")


def _pdf_paths() -> list[Path]:
    if PDF_PATH is not None:
        if not PDF_PATH.exists():
            raise FileNotFoundError(f"Policy PDF not found: {PDF_PATH}")
        return [PDF_PATH]

    paths = sorted(DATA_DIR.glob("*.pdf"))
    if not paths:
        raise FileNotFoundError(f"No PDF files found in {DATA_DIR}")
    return paths


def _md_paths() -> list[Path]:
    return sorted(DATA_DIR.glob("*.md"))


def _pdf_product(source_file: str) -> str:
    """Product a policy PDF covers, from its file name ('business' wins: personal_business_loan_agreement.pdf)."""
    name = source_file.lower()
    if "business" in name:
        return "business_loan"
    if "card" in name or name.startswith("cc-"):
        return "credit_card"
    if "personal" in name or name.startswith("pl_"):
        return "personal_loan"
    return "general"


# A source header inside a knowledge-base file: "---", then "key: value" lines, then "---".
_SOURCE_HEADER_RE = re.compile(r"^---\r?\n((?:[A-Za-z_]+:.*\r?\n)+)---\r?\n", re.M)


def _md_sources(text: str) -> list[tuple[dict[str, str], str]]:
    """Split a knowledge-base file into (header fields, body) per source; headers are metadata, not policy text."""
    matches = list(_SOURCE_HEADER_RE.finditer(text))
    if not matches:
        return [({}, text)]
    sources = []
    for match, nxt in zip(matches, [*matches[1:], None], strict=False):
        fields = dict(
            (key.strip(), value.strip()) for key, value in (line.split(":", 1) for line in match.group(1).splitlines())
        )
        sources.append((fields, text[match.end(): nxt.start() if nxt else len(text)]))
    return sources


def _md_metadata(fields: dict[str, str]) -> dict[str, str]:
    category = fields.get("category", "").split(".")
    product = category[1] if len(category) > 1 and category[1] in PRODUCTS else "general"
    return {
        "product": product,
        "publisher": fields.get("publisher", ""),
        "effective_date": fields.get("retrieved_on", ""),
    }


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
    """Column-aware pages (tables removed) → section parents; merged tables → their own parents."""
    parents: list[Document] = []
    parent_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1800,
        chunk_overlap=150,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    for pdf_path in _pdf_paths():
        source_file = pdf_path.name
        # All bundled PDFs are HDFC Bank documents; their effective date is not machine-readable.
        pdf_meta = {"product": _pdf_product(source_file), "publisher": "HDFC Bank", "effective_date": ""}
        pages, tables = extract_document(pdf_path)
        doc_type_by_page = {p.page: p.doc_type for p in pages}
        for page_info in pages:
            # Only empty pages are skipped: form-like pages (PDC declarations) still carry undertakings.
            if page_info.doc_type == "BLANK":
                continue
            page = page_info.page
            page_text = _clean_page_text(page_info.text)
            if not page_text:
                continue

            parent_texts: list[str] = []
            for section in _split_page_into_sections(page_text) or [page_text]:
                parent_texts.extend(parent_splitter.split_text(section))

            for index, parent_text in enumerate(parent_texts, start=1):
                parents.append(Document(
                    page_content=parent_text,
                    metadata={
                        "parent_id": f"{source_file}:p{page}:s{index}",
                        "source_file": source_file,
                        "source": str(pdf_path),
                        "page": page,
                        "page_start": page,
                        "page_end": page,
                        "doc_type": page_info.doc_type,
                        "section": _section_title(parent_text),
                        "is_table": False,
                        **pdf_meta,
                    },
                ))

        for t_index, table in enumerate(tables, start=1):
            body = serialize_table(table["table"])
            if len(body) < MIN_TABLE_CHARS:
                continue
            start, end = table["page_start"], table["page_end"]
            pages_label = f"p.{start}" if start == end else f"pp.{start}-{end}"
            parents.append(Document(
                page_content=body,
                metadata={
                    "parent_id": f"{source_file}:table{t_index}",
                    "source_file": source_file,
                    "source": str(pdf_path),
                    "page": start,
                    "page_start": start,
                    "page_end": end,
                    "doc_type": doc_type_by_page.get(start, "PROSE"),
                    "section": f"Table ({pages_label})",
                    "is_table": True,
                    **pdf_meta,
                },
            ))
    return parents



def _build_md_documents() -> list[Document]:
    """Split markdown knowledge-base files into parent documents for retrieval."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1800,
        chunk_overlap=150,
        separators=["\n## ", "\n### ", "\n\n", "\n", ". ", " ", ""],
    )
    parents: list[Document] = []
    for md_path in _md_paths():
        source_file = md_path.name
        index = 0
        # Each merged file holds several sources; their headers become chunk metadata instead of indexed text.
        for fields, body in _md_sources(md_path.read_text(encoding="utf-8")):
            meta = _md_metadata(fields)
            for chunk in splitter.split_text(body):
                if not chunk.strip(" \n-#"):
                    continue
                index += 1
                parents.append(Document(
                    page_content=chunk,
                    metadata={
                        "parent_id": f"{source_file}:s{index}",
                        "source_file": source_file,
                        "source": str(md_path),
                        "page": 1,
                        "page_start": 1,
                        "page_end": 1,
                        "doc_type": "PROSE",
                        "section": _section_title(chunk),
                        "is_table": False,
                        **meta,
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
    """Group whole sentences up to CHUNK_MAX_CHARS, carrying CHUNK_OVERLAP_SENTS forward.

    A single sentence longer than the limit becomes its own chunk — never split mid-sentence.
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
        if current_len + sent_len + 1 > CHUNK_MAX_CHARS and current:
            chunks.append(" ".join(current))
            current = current[-CHUNK_OVERLAP_SENTS:]
            current_len = sum(len(s) + 1 for s in current)
        current.append(sent)
        current_len += sent_len + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


def _table_row_chunks(text: str) -> list[str]:
    """Group serialized table rows into chunks without splitting a row."""
    chunks: list[str] = []
    current = ""
    for row in text.splitlines():
        if current and len(current) + len(row) + 1 > CHUNK_MAX_CHARS:
            chunks.append(current)
            current = row
        else:
            current = f"{current}\n{row}" if current else row
    if current:
        chunks.append(current)
    return chunks


def _build_child_documents(parents: list[Document]) -> list[Document]:
    """Clause-level evidence chunks (sentence-split when long); table parents split by row."""
    children: list[Document] = []
    for parent in parents:
        if parent.metadata.get("is_table"):
            pieces = [(text, "") for text in _table_row_chunks(parent.page_content)]
        else:
            pieces = []
            for clause_text, clause in _split_by_clause(parent.page_content):
                split_texts = (
                    _sentence_chunks(clause_text) if len(clause_text) > CHUNK_MAX_CHARS else [clause_text]
                )
                pieces.extend((child, clause or _clause_label(child)) for child in split_texts)

        for child, clause in pieces:
            if not child.strip():
                continue
            meta = {k: parent.metadata[k] for k in (
                "parent_id", "source_file", "source", "page", "page_start", "page_end", "doc_type", "section",
                "product", "publisher", "effective_date",
            )}
            meta["clause"] = clause
            children.append(Document(page_content=child, metadata=meta))

    ids = dedupe_ids([
        make_chunk_id(c.metadata["source_file"], c.metadata["page_start"],
                      f"{c.metadata['section']}|{c.metadata['clause']}", c.page_content)
        for c in children
    ])
    for child, chunk_id in zip(children, ids, strict=False):
        child.metadata["chunk_id"] = chunk_id
    return children
