"""
Chat latency percentiles from the audit log (logs/audit.jsonl), excluding promptfoo eval traffic.

total  = whole request (/ask and /ask/stream); ttft = time until the user sees the first words (/ask/stream only).

Usage: python eval/latency_report.py [path/to/audit.jsonl]   (default: AUDIT_LOG_PATH from .env)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from check_pass_rate import percentile  # noqa: E402

from app.config import AUDIT_LOG_PATH  # noqa: E402
from app.tracing import EVAL_SESSION_PREFIX  # noqa: E402


def main(path: Path) -> int:
    if not path.exists():
        print(f"No audit log at {path}")
        return 1
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    real = [e for e in events if e.get("status") != "failed"
            and not str(e.get("session_id") or "").startswith(EVAL_SESSION_PREFIX)]
    for label, key in (("total", "latency_ms"), ("ttft", "ttft_ms")):
        values = [e[key] for e in real if e.get(key)]
        if not values:
            print(f"{label:5}: no data yet")
            continue
        print(f"{label:5}: p50 {percentile(values, 50) / 1000:.1f}s · p95 {percentile(values, 95) / 1000:.1f}s"
              f" · max {max(values) / 1000:.1f}s  (n={len(values)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else AUDIT_LOG_PATH))
