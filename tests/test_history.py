"""SQLite run history and report export tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import time
import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - API extra is optional
    TestClient = None  # type: ignore[assignment]

from signal_radar.history import HistoryStore, report_to_markdown
from signal_radar.models import Annotation, Report, Run, RunEvent
from signal_radar.repository import load_fixture_report


class HistoryStoreTests(unittest.TestCase):
    def test_annotations_round_trip_and_filter(self) -> None:
        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        annotation = Annotation(
            id="ann-1",
            run_id="run-1",
            target_type="evidence",
            target_id="ev-001",
            label="correctness",
            value="correct",
            note="The quote is directly visible in the issue.",
            reviewer="reviewer@example.test",
        )
        store.add_annotation(annotation)
        self.assertEqual(store.get_annotation("ann-1").value, "correct")  # type: ignore[union-attr]
        self.assertEqual(store.list_annotations(run_id="run-1")[0].target_id, "ev-001")
        self.assertEqual(store.export_annotations(label="correctness")[0].id, "ann-1")

    def test_annotation_value_is_constrained_by_label(self) -> None:
        with self.assertRaises(ValueError):
            Annotation(
                target_type="event",
                target_id="event-1",
                label="risk",
                value="maybe",
                reviewer="reviewer",
            )

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

    def test_run_events_round_trip_and_offset(self) -> None:
        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        first = RunEvent(id="event-1", run_id="run-events", type="started", stage="collect")
        second = RunEvent(id="event-2", run_id="run-events", type="completed", stage="done", records=3)
        store.add_event(first)
        store.add_event(second)
        self.assertEqual([event.id for event in store.list_events("run-events")], ["event-1", "event-2"])
        self.assertEqual(store.list_events("run-events", offset=1)[0].records, 3)

    def test_run_events_survive_store_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            first = HistoryStore(path)
            first.add_event(RunEvent(id="event-reopen", run_id="run-reopen", type="completed"))
            reopened = HistoryStore(path)
            self.assertEqual(reopened.list_events("run-reopen")[0].id, "event-reopen")
            first.close()
            reopened.close()

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
    def test_annotation_endpoints_validate_and_export_labels(self) -> None:
        from signal_radar.api import create_app

        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        client = TestClient(create_app(history_store=store))
        run = client.post("/api/run", json={"mode": "replay"}).json()["run"]
        report = client.get("/api/report").json()
        evidence_id = report["evidence"][0]["id"]
        payload = {
            "id": "ann-api-1",
            "run_id": run["run_id"],
            "target_type": "evidence",
            "target_id": evidence_id,
            "label": "correctness",
            "value": "correct",
            "reviewer": "local-reviewer",
        }
        response = client.post("/api/annotations", json=payload)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(client.get("/api/annotations", params={"run_id": run["run_id"]}).json()[0]["id"], "ann-api-1")
        exported = client.get("/api/annotations/export.json", params={"run_id": run["run_id"]})
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json()[0]["target_id"], evidence_id)
        duplicate = client.post("/api/annotations", json=payload)
        self.assertEqual(duplicate.status_code, 409)

    def test_annotation_endpoint_rejects_unknown_report_target(self) -> None:
        from signal_radar.api import create_app

        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        client = TestClient(create_app(history_store=store))
        run = client.post("/api/run", json={"mode": "replay"}).json()["run"]
        response = client.post(
            "/api/annotations",
            json={
                "run_id": run["run_id"],
                "target_type": "claim",
                "target_id": "claim-missing",
                "label": "stance",
                "value": "support",
                "reviewer": "local-reviewer",
            },
        )
        self.assertEqual(response.status_code, 422)

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

    def test_trace_events_and_follow_up_endpoint(self) -> None:
        from signal_radar.api import create_app

        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        client = TestClient(create_app(history_store=store))
        original = client.post("/api/run", json={"mode": "replay"}).json()["run"]
        events = client.get(f"/api/runs/{original['run_id']}/trace")
        self.assertEqual(events.status_code, 200)
        self.assertIn("completed", [item["type"] for item in events.json()])
        follow = client.post(
            f"/api/runs/{original['run_id']}/follow-up",
            json={"query": "只核验最近版本变化"},
        )
        self.assertEqual(follow.status_code, 202)
        follow_id = follow.json()["run"]["run_id"]
        self.assertTrue(follow_id)
        for _ in range(100):
            state = client.get(f"/api/runs/{follow_id}").json()["run"]["status"]
            trace = client.get(f"/api/runs/{follow_id}/trace").json()
            if state not in {"queued", "running"} and trace and trace[-1]["type"] in {"completed", "partial", "cancelled", "failed"}:
                break
            time.sleep(0.01)
        self.assertNotIn(state, {"queued", "running"})

    def test_unknown_run_returns_not_found(self) -> None:
        from signal_radar.api import create_app

        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        client = TestClient(create_app(history_store=store))
        self.assertEqual(client.get("/api/runs/missing").status_code, 404)
        self.assertEqual(client.get("/api/runs/missing/markdown").status_code, 404)


if __name__ == "__main__":
    unittest.main()
