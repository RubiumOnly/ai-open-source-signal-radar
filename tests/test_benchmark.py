"""离线 benchmark runner 测试。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from signal_radar.benchmark import main, run_benchmark


ROOT = Path(__file__).resolve().parents[1]


class BenchmarkTests(unittest.TestCase):
    def test_fixture_benchmark_reports_pass_rate_and_latency(self) -> None:
        result = run_benchmark([ROOT / "fixtures" / "prompt_injection_browser_use.json"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["case_count"], 1)
        self.assertEqual(result["pass_rate_pct"], 100.0)
        self.assertIsNotNone(result["duration_ms"]["p95"])

    def test_cli_writes_json_and_returns_failure_for_invalid_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.json"
            invalid = Path(directory) / "invalid.json"
            invalid.write_text(json.dumps({"summary": {"risk_score": "bad"}}), encoding="utf-8")
            exit_code = main(["--fixture", str(invalid), "--output", str(output)])
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["case_count"], 1)
        self.assertFalse(payload["passed"])


if __name__ == "__main__":
    unittest.main()

