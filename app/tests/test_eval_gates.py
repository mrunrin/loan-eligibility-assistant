import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))

from check_pass_rate import main, percentile  # noqa: E402


def _results(tmp_path, latencies, failures=0):
    rows = [{"success": i >= failures, "latencyMs": ms} for i, ms in enumerate(latencies)]
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"results": {"results": rows}}), encoding="utf-8")
    return str(path)


def test_percentile_is_nearest_rank():
    assert percentile([5, 1, 3, 2, 4], 50) == 3
    assert percentile(list(range(1, 21)), 95) == 19
    assert percentile([7], 95) == 7


def test_gate_passes_and_fails_on_latency_and_pass_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("LATENCY_P95_MS", "10000")
    assert main(_results(tmp_path, [4000] * 9)) == 0
    assert main(_results(tmp_path, [4000] * 8 + [25000])) == 1  # one very slow answer is the p95 of 9
    assert main(_results(tmp_path, [4000] * 9, failures=2)) == 1  # 7/9 is under the 0.9 pass rate
