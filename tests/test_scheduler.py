"""Local bounded scheduler tests; no network or model calls are used."""

from __future__ import annotations

import threading
import time
import unittest

from signal_radar.models import Run, RunRequest, RunResponse, SchedulerRequest
from signal_radar.scheduler import LocalScheduler, SchedulerAlreadyRunning, validate_scheduler_request


class LocalSchedulerTests(unittest.TestCase):
    def test_default_schedule_is_not_started_and_immediate_run_is_bounded(self) -> None:
        calls: list[str] = []

        def callback(request: RunRequest) -> RunResponse:
            calls.append(request.run_id or "")
            run = Run(run_id=request.run_id or "missing", status="completed", subject=request.project or request.subject)
            return RunResponse(run=run)

        scheduler = LocalScheduler(callback)
        self.assertFalse(scheduler.is_running)
        state = scheduler.start(
            SchedulerRequest(
                request=RunRequest(mode="replay", project="org/repo"),
                interval_seconds=1,
                max_runs=1,
                run_immediately=True,
            )
        )
        self.assertTrue(state.enabled)
        state = scheduler.wait(timeout=3)
        self.assertEqual(state.status, "completed")
        self.assertEqual(state.runs_started, 1)
        self.assertEqual(state.runs_completed, 1)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("schedule-"))

    def test_stop_prevents_future_ticks_and_requests_active_cancellation(self) -> None:
        started = threading.Event()
        released = threading.Event()
        cancelled: list[str] = []

        def callback(request: RunRequest) -> RunResponse:
            started.set()
            released.wait(timeout=2)
            return RunResponse(run=Run(run_id=request.run_id or "missing", status="completed"))

        scheduler = LocalScheduler(callback, cancel_callback=lambda run_id: cancelled.append(run_id) or True)
        scheduler.start(
            SchedulerRequest(
                request=RunRequest(mode="replay"),
                interval_seconds=1,
                max_runs=3,
                run_immediately=True,
            )
        )
        self.assertTrue(started.wait(timeout=2))
        state = scheduler.stop(join_timeout=0.1)
        self.assertIn(state.status, {"stopping", "stopped"})
        self.assertEqual(len(cancelled), 1)
        released.set()
        state = scheduler.wait(timeout=3)
        self.assertEqual(state.status, "stopped")
        self.assertEqual(state.runs_started, 1)

    def test_second_start_is_rejected(self) -> None:
        scheduler = LocalScheduler(lambda request: RunResponse(run=Run(run_id=request.run_id or "x")))
        scheduler.start(
            SchedulerRequest(request=RunRequest(mode="replay"), interval_seconds=1, max_runs=10)
        )
        self.addCleanup(scheduler.stop)
        with self.assertRaises(SchedulerAlreadyRunning):
            scheduler.start(SchedulerRequest(request=RunRequest(mode="replay"), interval_seconds=1, max_runs=1))

    def test_validation_rejects_unknown_source_and_bounds_urls(self) -> None:
        with self.assertRaises(ValueError):
            validate_scheduler_request(
                SchedulerRequest(request=RunRequest(sources=["write_comments"]))
            )
        with self.assertRaises(ValueError):
            validate_scheduler_request(
                SchedulerRequest(request=RunRequest(urls=[f"https://example.com/{i}" for i in range(21)]))
            )


try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "FastAPI extra is not installed")
class SchedulerEndpointTests(unittest.TestCase):
    def test_schedule_is_disabled_until_explicit_start_and_can_stop(self) -> None:
        from signal_radar.api import create_app
        from signal_radar.history import HistoryStore

        store = HistoryStore(":memory:")
        app = create_app(history_store=store)
        self.addCleanup(store.close)
        client = TestClient(app)

        self.assertEqual(client.get("/api/schedule").json()["status"], "disabled")
        response = client.post(
            "/api/schedule",
            json={
                "request": {"mode": "replay", "project": "org/repo"},
                "interval_seconds": 1,
                "max_runs": 1,
                "run_immediately": True,
            },
        )
        self.assertEqual(response.status_code, 202)
        schedule_id = response.json()["schedule_id"]
        deadline = time.monotonic() + 3
        state = client.get("/api/schedule").json()
        while state["status"] not in {"completed", "stopped"} and time.monotonic() < deadline:
            time.sleep(0.05)
            state = client.get("/api/schedule").json()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["schedule_id"], schedule_id)
        self.assertEqual(state["runs_completed"], 1)
        self.assertGreaterEqual(len(client.get("/api/runs").json()), 1)
        self.assertEqual(client.post("/api/schedule/stop").status_code, 202)


if __name__ == "__main__":
    unittest.main()
