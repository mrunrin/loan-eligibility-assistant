"""
Gate on promptfoo's overall pass rate instead of failing on any single flaky case.

Usage: python eval/check_pass_rate.py eval/promptfoo_results.json
Env:   PASS_RATE_THRESHOLD (default 0.9)
"""

import json
import os
import sys
from pathlib import Path


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
    return 0 if rate >= threshold else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python eval/check_pass_rate.py <promptfoo results json>")
    raise SystemExit(main(sys.argv[1]))
