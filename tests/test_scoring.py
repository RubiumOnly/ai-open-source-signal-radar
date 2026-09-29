"""风险评分和报告聚合的纯离线测试。"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from signal_radar.models import Article, Claim, Event, ProjectInfo, SourceStatus
from signal_radar.scoring import aggregate_report, score_risk


UTC = timezone.utc


class ScoringTests(unittest.TestCase):
    def test_risk_score_is_bounded_and_penalizes_unavailable_sources(self) -> None:
        event = Event(id="e1", title="timeout", risk_level="high", risk_score=70)
        claim = Claim(
            id="c1",
            text="任务经常超时",
            stance="oppose",
            sentiment="negative",
        )
        healthy = SourceStatus(source="github", status="ok", access_status="public")
        blocked = SourceStatus(source="forum", status="blocked", access_status="blocked")

        baseline = score_risk([event], [claim], [healthy])
        degraded = score_risk([event], [claim], [healthy, blocked])
        self.assertGreaterEqual(baseline, 0)
        self.assertLessEqual(degraded, 100)
        self.assertGreater(degraded, baseline)

    def test_aggregate_report_has_stable_counts_and_trend(self) -> None:
        published = datetime(2026, 9, 27, 12, tzinfo=UTC)
        article = Article(
            id="a1",
            url="https://example.test/issue/1",
            title="Timeout report",
            source="github",
            published_at=published,
        )
        claim = Claim(
            id="c1",
            text="任务经常超时",
            stance="oppose",
            sentiment="negative",
            article_ids=["a1"],
            topics=["reliability"],
        )
        event = Event(
            id="e1",
            title="长任务超时",
            category="reliability",
            risk_level="high",
            risk_score=70,
            occurred_at=published,
            article_ids=["a1"],
            claim_ids=["c1"],
        )
        report = aggregate_report(
            project=ProjectInfo(name="demo", repository="org/demo"),
            articles=[article],
            claims=[claim],
            events=[event],
            source_statuses=[SourceStatus(source="github", status="ok")],
            window_days=30,
            run_id="run-test",
        )

        self.assertEqual(report.summary.events_count, 1)
        self.assertEqual(report.summary.source_count, 1)
        self.assertEqual(report.summary.negative_count, 1)
        self.assertEqual(report.trends[0].date, "2026-09-27")
        self.assertEqual(report.trends[0].negative, 1)
        self.assertEqual(report.topics[0].name, "reliability")
        self.assertEqual(report.topics[0].count, 1)


if __name__ == "__main__":
    unittest.main()

