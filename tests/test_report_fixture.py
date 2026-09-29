"""离线报告契约测试：不访问网络，也不要求模型或 browser-use。"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from signal_radar.models import Report


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "demo_report.json"


class DemoReportContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_fixture_is_complete_demo_report(self) -> None:
        required = {
            "run_id",
            "project",
            "generated_at",
            "window_days",
            "summary",
            "trends",
            "topics",
            "evidence",
            "sources",
            "access_status",
        }
        self.assertTrue(required.issubset(self.report))
        self.assertEqual(self.report["project"]["repository"], "browser-use/browser-use")
        self.assertGreaterEqual(self.report["summary"]["coverage_pct"], 0)
        self.assertLessEqual(self.report["summary"]["coverage_pct"], 100)

    def test_fixture_matches_pydantic_report_contract(self) -> None:
        report = Report.model_validate(self.report)
        self.assertEqual(report.run_id, self.report["run_id"])
        self.assertEqual(len(report.evidence), len(self.report["evidence"]))

    def test_evidence_has_traceable_citations(self) -> None:
        self.assertGreater(len(self.report["evidence"]), 0)
        for item in self.report["evidence"]:
            for field in ("id", "url", "title", "source", "quote", "content_hash", "confidence"):
                self.assertTrue(item.get(field), f"missing evidence field: {field}")
            self.assertTrue(item["url"].startswith(("http://", "https://")))
            self.assertGreaterEqual(item["confidence"], 0)
            self.assertLessEqual(item["confidence"], 1)

    def test_access_boundary_is_visible(self) -> None:
        csdn = next(item for item in self.report["access_status"] if item["source"] == "csdn")
        self.assertEqual(csdn["status"], "auth_required")
        self.assertEqual(csdn["evidence_level"], "none")
        self.assertNotIn("csdn", {item.get("source", item.get("name", "")).lower() for item in self.report["sources"]})

    def test_topic_references_have_supporting_evidence(self) -> None:
        evidence_ids = {item["id"] for item in self.report["evidence"]}
        for event in self.report.get("events", []):
            for evidence_id in event.get("evidence_ids", []):
                self.assertIn(evidence_id, evidence_ids)


if __name__ == "__main__":
    unittest.main()

