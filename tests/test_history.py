"""SQLite run history and report export tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - API extra is optional
    TestClient = None  # type: ignore[assignment]

from signal_radar.history import HistoryStore, report_to_markdown
from signal_radar.models import Report, Run
from signal_radar.repository import load_fixture_report


class HistoryStoreTests(unittest.TestCase):
    def test_memory_store_round_trips_run_and_report(self) -> None:
        report = load_fixture_report()
        run = Run(
            run_id="run-history-1",
            mode="replay",
            status="completed",
            subject=report.project.repository,
            report_id=report.report_id,
        )
        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        store.save(run, report)

        response = store.get(run.run_id)
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.run.run_id, run.run_id)
        self.assertEqual(response.report.project.repository, report.project.repository)
        self.assertEqual(store.list()[0].run_id, run.run_id)

    def test_file_store_survives_new_store_instance_and_orders_newest_first(self) -> None:
        report = load_fixture_report()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.sqlite3"
            first = HistoryStore(path)
            now = datetime.now(timezone.utc)
            older = Run(run_id="run-old", subject="old", started_at=now - timedelta(minutes=1))
            newer = Run(run_id="run-new", subject="new", started_at=now)
            first.save(older, report)
            first.save(newer, report)
            reopened = HistoryStore(path)
            self.assertEqual([item.run_id for item in reopened.list()], ["run-new", "run-old"])
            self.assertEqual(reopened.get("run-old").run.subject, "old")  # type: ignore[union-attr]

    def test_failed_run_is_persisted_without_report(self) -> None:
        run = Run(run_id="run-failed", status="failed", subject="org/repo", error="source unavailable")
        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        store.save(run)
        response = store.get("run-failed")
        self.assertIsNotNone(response)
        assert response is not None
        self.assertEqual(response.run.status, "failed")
        self.assertIsNone(response.report)
        self.assertIsNone(store.markdown("run-failed"))

    def test_markdown_export_keeps_citations_and_sections(self) -> None:
        report = load_fixture_report()
        run = Run(run_id="run-markdown", subject=report.project.repository)
        markdown = report_to_markdown(report, run)
        self.assertIn("## 摘要", markdown)
        self.assertIn("## 证据", markdown)
        self.assertIn(report.evidence[0].url, markdown)
        self.assertIn("run-markdown", markdown)


@unittest.skipIf(TestClient is None, "FastAPI extra is not installed")
class HistoryEndpointTests(unittest.TestCase):
    def test_run_endpoints_use_persisted_store(self) -> None:
        from signal_radar.api import create_app

        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        client = TestClient(create_app(history_store=store))
        response = client.post("/api/run", json={"mode": "replay"})
        self.assertEqual(response.status_code, 200)
        run_id = response.json()["run"]["run_id"]

        listing = client.get("/api/runs")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["run_id"], run_id)

        detail = client.get(f"/api/runs/{run_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["report"]["run_id"], run_id)

        markdown = client.get(f"/api/runs/{run_id}/markdown")
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("text/markdown", markdown.headers["content-type"])
        self.assertIn("## 证据", markdown.text)

        alias = client.get(f"/api/runs/{run_id}/report.md")
        self.assertEqual(alias.status_code, 200)
        self.assertEqual(alias.text, markdown.text)

    def test_unknown_run_returns_not_found(self) -> None:
        from signal_radar.api import create_app

        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        client = TestClient(create_app(history_store=store))
        self.assertEqual(client.get("/api/runs/missing").status_code, 404)
        self.assertEqual(client.get("/api/runs/missing/markdown").status_code, 404)


if __name__ == "__main__":
    unittest.main()
