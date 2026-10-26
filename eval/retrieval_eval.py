"""
Retrieval quality gate: recall@k and MRR of the hybrid retriever on eval/golden_retrieval.yaml.

A question is a hit when any of the top-k evidence chunks comes from a gold (document, page).
Cross-page chunks count for every page in their page_start..page_end range.

Usage:  python eval/retrieval_eval.py            (needs JINA_API_KEY and COHERE_API_KEY)
Env:    RECALL_THRESHOLD (default 0.80) — exit code 1 when recall@k falls below it.
"""

import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# Before app imports create the Langfuse client: eval traces stay out of the real-traffic dashboards.
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "eval")

from app.retrieval import FINAL_EVIDENCE_K, initialize_vectorstore, retrieve_evidence  # noqa: E402


def _pages(doc) -> set[tuple[str, int]]:
    m = doc.metadata
    start = int(m.get("page_start", m.get("page", 0)))
    end = int(m.get("page_end", start))
    return {(m.get("source_file", ""), p) for p in range(start, end + 1)}


def main() -> int:
    spec = yaml.safe_load((ROOT / "eval" / "golden_retrieval.yaml").read_text(encoding="utf-8"))
    docs = spec["documents"]
    threshold = float(os.getenv("RECALL_THRESHOLD", "0.80"))
    initialize_vectorstore()

    hits, rr_total, misses = 0, 0.0, []
    for item in spec["questions"]:
        gold = {(docs[d], int(p)) for d, p in item["gold"]}
        ranked = [doc for doc, _ in retrieve_evidence(item["q"])]
        rank = next((i for i, doc in enumerate(ranked, start=1) if _pages(doc) & gold), None)
        if rank:
            hits += 1
            rr_total += 1 / rank
        else:
            got = [f"{d.metadata.get('source_file', '')[:12]} p{d.metadata.get('page')}" for d in ranked]
            misses.append({"q": item["q"], "gold": sorted(gold), "got": got})

    n = len(spec["questions"])
    report = {"questions": n, f"recall@{FINAL_EVIDENCE_K}": round(hits / n, 3), "mrr": round(rr_total / n, 3)}
    for miss in misses:
        print(f"MISS: {miss['q']}\n   gold={miss['gold']}\n   got={miss['got']}")
    print(json.dumps(report))
    if report[f"recall@{FINAL_EVIDENCE_K}"] < threshold:
        print(f"FAIL: recall@{FINAL_EVIDENCE_K} below threshold {threshold}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
