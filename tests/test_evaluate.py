"""离线评测 harness 的确定性测试。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from signal_radar.evaluate import evaluate_fixture, evaluate_report, main
from signal_radar.models import Article, Claim, Event, Evidence, ProjectInfo, SourceStatus
from signal_radar.scoring import aggregate_report


UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]


def _coherent_report():
    published = datetime(2026, 9, 27, 12, tzinfo=UTC)
    article = Article(
        id="a1",
        url="https://example.test/issue/1",
        title="Timeout report",
        source="github",
        published_at=published,
    )
    evidence = Evidence(
        id="ev1",
        article_id=article.id,
        url=article.url,
        source="github",
        title=article.title,
        quote="Long tasks time out.",
        confidence=0.9,
        published_at=published,
    )
    claim = Claim(
        id="c1",
        text="Long tasks can time out.",
        stance="oppose",
        sentiment="negative",
        evidence_ids=[evidence.id],
        article_ids=[article.id],
    )
    event = Event(
        id="e1",
        title="Timeout reports",
        category="reliability",
        risk_level="high",
        risk_score=70,
        sentiment="negative",
        occurred_at=published,
        article_ids=[article.id],
        claim_ids=[claim.id],
        evidence_ids=[evidence.id],
    )
    return aggregate_report(
        project=ProjectInfo(name="demo", repository="org/demo"),
        articles=[article],
        claims=[claim],
        events=[event],
        evidence=[evidence],
        source_statuses=[SourceStatus(source="github", status="ok", access_status="public", records=1)],
        window_days=7,
        run_id="evaluation-test",
    )


class EvaluationTests(unittest.TestCase):
    def test_coherent_report_passes_reproducible_checks(self) -> None:
        result = evaluate_report(_coherent_report())
        self.assertTrue(result["schema_valid"])
        self.assertTrue(result["passed"])
        self.assertEqual(result["citation_coverage"]["overall_pct"], 100.0)
        self.assertEqual(result["source_coverage"]["available_pct"], 100.0)
        self.assertTrue(result["event_consistency"]["summary_count_matches"])
        self.assertTrue(result["event_consistency"]["summary_risk_score_matches"])

    def test_inconsistent_fixture_surfaces_semantic_inconsistency(self) -> None:
        result = evaluate_fixture(ROOT / "fixtures" / "evaluation_inconsistent.json")
        self.assertTrue(result["schema_valid"])
        self.assertFalse(result["passed"])
        self.assertFalse(result["event_consistency"]["summary_count_matches"])
        self.assertFalse(result["event_consistency"]["summary_risk_score_matches"])

    def test_invalid_fixture_is_reported_without_demo_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps({"summary": {"risk_score": "bad"}}), encoding="utf-8")
            result = evaluate_fixture(path)
        self.assertFalse(result["schema_valid"])
        self.assertFalse(result["passed"])
        self.assertTrue(result["schema_errors"])

    def test_cli_writes_sorted_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evaluation.json"
            exit_code = main(
                ["--fixture", str(ROOT / "fixtures" / "demo_report.json"), "--output", str(output)]
            )
            self.assertEqual(exit_code, 0)
            output_text = output.read_text(encoding="utf-8")
            payload = json.loads(output_text)
        self.assertIn('"citation_coverage"', output_text)
        self.assertEqual(payload["evaluator_version"], "0.1.0")


if __name__ == "__main__":
    unittest.main()
