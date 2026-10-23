"""
Document-aware PDF ingestion building blocks, shared by app.chunking.

  classify_page()           page → doc_type (TC, SCHEDULE, PDC, ECS, FAQ, PROSE, BLANK)
  extract_page_text()       column-aware text, optionally excluding table regions
  merge_cross_page_tables() joins a table that continues onto the next page
  STRATEGY_MAP              doc_type → chunking strategy (add a type = add one entry)
  run_pipeline()            the whole flow → list[Chunk] with stable chunk_id + page range
"""

import hashlib
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

# ── Output shape ──────────────────────────────────────────────────────────────


@dataclass
class Chunk:
    chunk_id: str     # stable: derived from content + position, not from global ordering
    doc_name: str
    doc_type: str
    section: str
    page_start: int
    page_end: int     # differs from page_start for cross-page tables
    chunk_index: int  # position in the document (ordering only, not part of the id)
    text: str

    def to_dict(self) -> dict:
        return asdict(self)


def make_chunk_id(doc_name: str, page_start: int, section: str, text: str) -> str:
    """Same document + page + section + text → same id, so re-ingesting is an idempotent upsert."""
    slug = re.sub(r"\W+", "_", f"{doc_name}_{section}").lower()[:40]
    digest = hashlib.sha256(f"{doc_name}|{page_start}|{section}|{text}".encode()).hexdigest()[:12]
    return f"{slug}_{digest}"


def dedupe_ids(ids: list[str]) -> list[str]:
    """Identical text repeated in one section would collide; suffix repeats deterministically."""
    seen: dict[str, int] = {}
    out = []
    for cid in ids:
        n = seen.get(cid, 0)
        seen[cid] = n + 1
        out.append(cid if n == 0 else f"{cid}_{n}")
    return out


# ── Page classifier ───────────────────────────────────────────────────────────
# Form headings first (a cheque/mandate form also carries a document barcode), then barcodes,
# FAQ last so a T&C page with a "Q1." line stays TC.

# Barcodes are often rendered with spaces between digits ("TC9 2 5 9 4 0 0 0 1").
_TC_RE = re.compile(r"\bTC\s*(?:\d\s*){9,}")
_SC_RE = re.compile(r"\bSC\s*(?:\d\s*){9,}")
_ECS_RE = re.compile(r"ECS\s*MANDATE|ELECTRONIC\s*CLEARING\s*SERVICE", re.I)
_PDC_RE = re.compile(r"\bPDC\b|POST[\s-]DATED\s+CHEQUE", re.I)
_FAQ_RE = re.compile(r"^(?:Q|Query)\s*\d+\s*[.:]", re.M | re.I)
_HEAD_RE = re.compile(r"^[A-Z][A-Z\s\-\/]{3,}$", re.M)
# Page counters ("7/12") and barcode lines that sit above a page's real title.
_PAGE_FURNITURE_RE = re.compile(r"^[\d/\s]+$|^(?:TC|SC)\s*(?:\d\s*){5,}$")


def _heading_lines(text: str, n: int = 3) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join([ln for ln in lines if not _PAGE_FURNITURE_RE.match(ln)][:n])


def classify_page(text: str) -> str:
    if len(text.split()) < 10:
        return "BLANK"
    # Form markers count only in the page's heading lines: agreements mention "post-dated
    # cheque" inside ordinary clauses, and those pages must not be classified as forms.
    heading = _heading_lines(text)
    if _PDC_RE.search(heading):
        return "PDC"
    if _ECS_RE.search(heading):
        return "ECS"
    if _TC_RE.search(text):
        return "TC"
    if _SC_RE.search(text):
        return "SCHEDULE"
    if _FAQ_RE.search(text):
        return "FAQ"
    return "PROSE"


# ── Column-aware text extractor ───────────────────────────────────────────────

BBox = tuple[float, float, float, float]  # x0, top, x1, bottom (PDF points, y grows downward)


def _inside(block, boxes: list[BBox]) -> bool:
    cx, cy = (block[0] + block[2]) / 2, (block[1] + block[3]) / 2
    return any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)


def _join(blocks) -> str:
    return "\n".join(b[4].strip() for b in blocks)


def extract_page_text(page, exclude: list[BBox] | None = None) -> str:
    """
    PyMuPDF page → text in reading order.

    Two-column is detected per page: several blocks sit entirely left of the midpoint, several
    entirely right, and few blocks span it. Full-width blocks above the columns (titles) are
    read first, then the left column, the right column, and remaining full-width blocks.
    """
    blocks = [b for b in page.get_text("blocks") if b[4].strip()]
    if exclude:
        blocks = [b for b in blocks if not _inside(b, exclude)]
    if not blocks:
        return ""

    mid = page.rect.width / 2
    tol = page.rect.width * 0.02
    lefts = [b for b in blocks if b[2] <= mid + tol]
    rights = [b for b in blocks if b[0] >= mid - tol]
    spanning = [b for b in blocks if b not in lefts and b not in rights]

    if len(lefts) > 2 and len(rights) > 2 and len(spanning) <= max(2, len(blocks) // 5):
        columns_top = min(b[1] for b in lefts + rights)
        header = sorted((b for b in spanning if b[1] < columns_top), key=lambda b: b[1])
        footer = sorted((b for b in spanning if b[1] >= columns_top), key=lambda b: b[1])
        parts = [
            _join(header),
            _join(sorted(lefts, key=lambda b: b[1])),
            _join(sorted(rights, key=lambda b: b[1])),
            _join(footer),
        ]
        return "\n\n".join(p for p in parts if p)

    # Single column: sort by row (coarse y bucket) then x
    return _join(sorted(blocks, key=lambda b: (round(b[1] / 20), b[0])))


# ── Cross-page table merger ───────────────────────────────────────────────────


def merge_cross_page_tables(per_page: list[list[list]]) -> list[dict]:
    """
    per_page: per-page table lists (pdfplumber extract_tables()), page 1 first.
    A table continues onto the next page when it is the first table on page N+1, the pending
    table ended on page N, the column count matches, and its first row is not a repeated header.

    Returns: [{"table": rows, "page_start": N, "page_end": M}, ...]
    """
    result: list[dict] = []
    pending: list[list] | None = None
    pending_start = pending_end = 0

    for page_idx, tables in enumerate(per_page):
        page_num = page_idx + 1
        for i, table in enumerate(t for t in tables if t):
            continues = (
                pending is not None
                and i == 0
                and page_num == pending_end + 1
                and len(table[0]) == len(pending[0])
                and table[0] != pending[0]
            )
            if continues:
                pending.extend(table)
                pending_end = page_num
                continue
            if pending is not None:
                result.append({"table": pending, "page_start": pending_start, "page_end": pending_end})
            pending = list(table)
            pending_start = pending_end = page_num

    if pending is not None:
        result.append({"table": pending, "page_start": pending_start, "page_end": pending_end})
    return result


def serialize_table(table: list[list]) -> str:
    """Convert a table (list of rows) to 'Header: value | Header: value' lines."""
    if not table:
        return ""
    headers = [str(c or "").strip() for c in table[0]]
    rows = []
    for row in table[1:]:
        cells = [str(c or "").strip() for c in row]
        if any(cells):
            rows.append(" | ".join(f"{h}: {v}" if h else v for h, v in zip(headers, cells, strict=False) if v))
    return "\n".join(rows)


# ── Chunking strategies — each returns [(section, body), ...] ─────────────────


def _heading_chunks(text: str) -> list[tuple[str, str]]:
    """Split on ALL-CAPS headings — best for legal T&C prose."""
    matches = list(_HEAD_RE.finditer(text))
    if not matches:
        return [("GENERAL", text)]
    out = []
    preamble = text[: matches[0].start()].strip()
    if preamble:  # text continuing from the previous page, before this page's first heading
        out.append(("(continued)", preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            out.append((m.group(0).strip(), body))
    return out or [("GENERAL", text)]


def _split_recursive(text: str, seps: list[str], size: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    if not seps or seps[0] == "":
        return [text[i:i + size] for i in range(0, len(text), size)]
    sep = seps[0]
    out: list[str] = []
    cur = ""
    for part in text.split(sep):
        part = part.strip()
        if not part:
            continue
        cand = f"{cur}{sep}{part}" if cur else part
        if len(cand) <= size:
            cur = cand
            continue
        if cur:
            out.append(cur)
            cur = ""
        if len(part) > size:
            out.extend(_split_recursive(part, seps[1:], size))
        else:
            cur = part
    if cur:
        out.append(cur)
    return out


def _recursive_chunks(text: str, size: int = 600, overlap: int = 80) -> list[tuple[str, str]]:
    """Paragraph → line → sentence → word → character fallback; overlap applied once."""
    pieces = _split_recursive(text, ["\n\n", "\n", ". ", " ", ""], size)
    out = [
        (pieces[i - 1][-overlap:] + " " + p) if i > 0 and overlap else p
        for i, p in enumerate(pieces)
    ]
    return [("GENERAL", c) for c in out]


def _faq_chunks(text: str) -> list[tuple[str, str]]:
    """One chunk per Q&A pair — keeps question + answer together."""
    qa_re = re.compile(
        r"((?:Q|Query)\s*\d+\s*[.:][^\n]+)\n(.*?)(?=(?:Q|Query)\s*\d+\s*[.:]|\Z)",
        re.S | re.I,
    )
    pairs = [(m.group(1).strip(), m.group(2).strip()) for m in qa_re.finditer(text)]
    return pairs if pairs else [("GENERAL", text)]


# ── Strategy map ──────────────────────────────────────────────────────────────
# doc_type → (label, text strategy). Tables on any non-skipped page are serialized separately.

TextStrategy = Callable[[str], list[tuple[str, str]]]

STRATEGY_MAP: dict[str, tuple[str, TextStrategy | None]] = {
    "TC": ("heading-based", _heading_chunks),
    "PROSE": ("recursive", _recursive_chunks),
    "FAQ": ("faq-pairs", _faq_chunks),
    "SCHEDULE": ("table-serial", _recursive_chunks),  # tables serialized + leftover text chunked
    "PDC": ("skip", None),  # form pages: mostly blank cells
    "ECS": ("skip", None),
    "BLANK": ("skip", None),
}


def is_skipped(doc_type: str) -> bool:
    return STRATEGY_MAP.get(doc_type, STRATEGY_MAP["PROSE"])[0] == "skip"


# ── Page-level extraction shared with app.chunking ─────────────────────────────────


@dataclass
class PageInfo:
    page: int
    doc_type: str
    text: str  # column-aware text with table regions removed


def extract_document(pdf_path: Path) -> tuple[list[PageInfo], list[dict]]:
    """One pass over a PDF: per-page classified text plus cross-page-merged tables."""
    import pdfplumber
    import pymupdf as fitz

    per_page_tables: list[list] = []
    table_boxes: list[list[BBox]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            found = page.find_tables()
            table_boxes.append([t.bbox for t in found])
            per_page_tables.append([t.extract() for t in found])

    pages: list[PageInfo] = []
    with fitz.open(str(pdf_path)) as doc:
        for i, page in enumerate(doc):
            full_text = extract_page_text(page)
            doc_type = classify_page(full_text)
            boxes = table_boxes[i] if i < len(table_boxes) else []
            text = extract_page_text(page, exclude=boxes) if boxes else full_text
            pages.append(PageInfo(i + 1, doc_type, text))

    return pages, merge_cross_page_tables(per_page_tables)


# ── Full pipeline ─────────────────────────────────────────────────────────────


def run_pipeline(pdf_path: Path) -> list[Chunk]:
    """Classify → extract (column-aware) → merge tables → chunk by doc_type → attach metadata."""
    doc_name = pdf_path.name
    pages, tables = extract_document(pdf_path)
    doc_type_by_page = {p.page: p.doc_type for p in pages}
    tables_by_page: dict[int, list[dict]] = defaultdict(list)
    for t in tables:
        tables_by_page[t["page_start"]].append(t)

    raw: list[tuple[str, str, int, int, str]] = []  # doc_type, section, page_start, page_end, text
    for p in pages:
        if is_skipped(p.doc_type):
            continue
        for t_idx, tbl in enumerate(tables_by_page.get(p.page, []), start=1):
            body = serialize_table(tbl["table"])
            if body:
                raw.append((p.doc_type, f"TABLE {t_idx}", tbl["page_start"], tbl["page_end"], body))
        _, strategy = STRATEGY_MAP.get(p.doc_type, STRATEGY_MAP["PROSE"])
        for section, body in strategy(p.text) if strategy and p.text.strip() else []:
            if body.strip():
                raw.append((p.doc_type, section, p.page, p.page, body))

    # Tables starting on a skipped page are dropped with it; a table *continuing* onto one is kept.
    raw = [r for r in raw if not is_skipped(doc_type_by_page.get(r[2], "PROSE"))]
    ids = dedupe_ids([make_chunk_id(doc_name, r[2], r[1], r[4]) for r in raw])
    return [
        Chunk(cid, doc_name, doc_type, section, start, end, idx, text)
        for idx, (cid, (doc_type, section, start, end, text)) in enumerate(zip(ids, raw, strict=False))
    ]


if __name__ == "__main__":
    # Self-check of the pure helpers (no PDF needed): python -m app.doc_pipeline
    merged = merge_cross_page_tables([[[["H1", "H2"], ["a", "1"]]], [[["b", "2"]]], [[["c", "3"]]], [], [[["d", "4"]]]])
    assert [(t["page_start"], t["page_end"]) for t in merged] == [(1, 3), (5, 5)], merged
    assert len(merged[0]["table"]) == 4
    para = "word " * 80  # 400 chars, fits in one chunk
    chunks = _recursive_chunks(para.strip() + "\n\n" + "x" * 1300, size=600, overlap=0)
    texts = [c for _, c in chunks]
    assert sum(t.count("word") for t in texts) == 80, "paragraph duplicated or lost"
    assert all(len(t) <= 600 for t in texts)
    assert _heading_chunks("carry over text\nSECTION ONE\nbody")[0] == ("(continued)", "carry over text")
    assert classify_page("TC123456789012 terms " + "word " * 10) == "TC"
    assert classify_page("TC123456789012\nQ1. what is this " + "word " * 10) == "TC"
    clause_page = "1. INTEREST\nInterest clause " + "word " * 20 + "\nmore text\nthe borrower shall give a post-dated cheque"
    assert classify_page(clause_page) == "PROSE"
    assert classify_page("PDC/SPDC DECLARATION\nDate:\n" + "word " * 20) == "PDC"
    assert dedupe_ids(["a", "a", "b"]) == ["a", "a_1", "b"]
    spaced = "7/12\n7/12\nSC9 2 5 9 4 0 0 0 1\nPOST DATED CHEQUE ACKNOWLEDGEMENT LETTER\n" + "word " * 20
    assert classify_page(spaced) == "PDC"
    assert classify_page("1/12\nTC9 2 5 9 4 0 0 0 1\nTerms and Conditions " + "word " * 20) == "TC"
    assert make_chunk_id("d.pdf", 1, "S", "t") == make_chunk_id("d.pdf", 1, "S", "t")
    print("doc_pipeline self-check passed")
