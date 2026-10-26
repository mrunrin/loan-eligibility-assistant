"""
Gate on promptfoo's overall pass rate instead of failing on any single flaky case, and on answer latency.

Usage: python eval/check_pass_rate.py eval/promptfoo_results.json
Env:   PASS_RATE_THRESHOLD (default 0.9)
       LATENCY_P95_MS (default 30000) — exit code 1 when the p95 answer latency is above it.
"""

import json
import math
import os
import sys
from pathlib import Path


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile: the smallest value with at least pct% of values at or below it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def main(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    results = data.get("results", data)
    rows = results.get("results", [])
    passed = sum(1 for r in rows if r.get("success"))
    total = len(rows)
    if total == 0:
        print("FAIL: no promptfoo results found")
        return 1
    threshold = float(os.getenv("PASS_RATE_THRESHOLD", "0.9"))
    rate = passed / total
    for r in rows:
        if not r.get("success"):
            desc = (r.get("testCase") or {}).get("description") or (r.get("vars") or {}).get("question")
            print(f"FAILED: {desc}: {(r.get('gradingResult') or {}).get('reason') or r.get('error')}")
    print(f"promptfoo pass rate {passed}/{total} = {rate:.2%} (threshold {threshold:.0%})")
    ok = rate >= threshold

    # promptfoo times each request to the backend's /ask (end-to-end: route, retrieval, rerank, answer).
    latencies = [r["latencyMs"] for r in rows if r.get("latencyMs")]
    if latencies:
        limit = float(os.getenv("LATENCY_P95_MS", "30000"))
        p50, p95 = percentile(latencies, 50), percentile(latencies, 95)
        print(f"answer latency p50 {p50 / 1000:.1f}s · p95 {p95 / 1000:.1f}s · max {max(latencies) / 1000:.1f}s "
              f"over {len(latencies)} answers (p95 limit {limit / 1000:.0f}s)")
        if p95 > limit:
            print("FAIL: p95 answer latency is above the limit")
            ok = False
    else:
        print("WARN: promptfoo results have no latency data")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python eval/check_pass_rate.py <promptfoo results json>")
    raise SystemExit(main(sys.argv[1]))
