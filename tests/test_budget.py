from __future__ import annotations

import asyncio
import unittest
import threading

from fastapi.testclient import TestClient

from signal_radar.api import RunControl, _live_report, create_app
from signal_radar.history import HistoryStore
from signal_radar.models import RunBudget, RunRequest, SourceStatus
from signal_radar.sources import BrowserRunCancelled, BrowserUseSourceAdapter, SourceFetchResult


class _BudgetBrowser:
    max_steps = 12
    timeout_seconds = 30.0

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def collect(self, urls, *, limit, max_steps, timeout_seconds, cancel_event):
        self.calls.append(
            {
                "urls": list(urls),
                "limit": limit,
                "max_steps": max_steps,
                "timeout_seconds": timeout_seconds,
                "cancel_event": cancel_event,
            }
        )
        return SourceFetchResult(
            status=SourceStatus(source="Browser Use", source_type="dynamic", status="ok", records=0)
        )


class _NoopGitHub:
    def collect(self, repository, *, limit, since):
        return SourceFetchResult(status=SourceStatus(source="GitHub", status="ok"))


class RunBudgetTests(unittest.TestCase):
    def test_browser_agent_timeout_is_enforced(self) -> None:
        class SlowAgent:
            async def run(self, *, max_steps):
                await asyncio.sleep(0.2)

        adapter = BrowserUseSourceAdapter(max_steps=4, timeout_seconds=5)
        with self.assertRaises(asyncio.TimeoutError):
            asyncio.run(
                adapter._run_agent_with_controls(
                    SlowAgent(), max_steps=2, timeout_seconds=0.01
                )
            )

    def test_browser_agent_cancel_event_is_observed(self) -> None:
        class SlowAgent:
            async def run(self, *, max_steps):
                await asyncio.sleep(0.2)

        adapter = BrowserUseSourceAdapter(max_steps=4, timeout_seconds=5)
        cancel_event = threading.Event()
        cancel_event.set()
        with self.assertRaises(BrowserRunCancelled):
            asyncio.run(
                adapter._run_agent_with_controls(
                    SlowAgent(),
                    max_steps=2,
                    timeout_seconds=1,
                    cancel_event=cancel_event,
                )
            )

    def test_live_orchestration_forwards_bounded_budget(self) -> None:
        browser = _BudgetBrowser()
        _live_report(
            RunRequest(
                mode="live",
                project="org/repo",
                sources=["browser_use"],
                urls=["https://example.com/discussions"],
                max_steps=5,
                timeout_seconds=8,
            ),
            run_id="budget-test",
            github=_NoopGitHub(),
            browser=browser,
            budget=RunBudget(max_steps=5, timeout_seconds=8),
            cancel_event=threading.Event(),
        )
        self.assertEqual(browser.calls[0]["max_steps"], 5)
        self.assertLessEqual(float(browser.calls[0]["timeout_seconds"]), 8)
        self.assertIsNotNone(browser.calls[0]["cancel_event"])

    def test_cancel_endpoint_sets_active_event(self) -> None:
        app = create_app(history_store=HistoryStore(":memory:"))
        self.addCleanup(app.state.history_store.close)
        control = RunControl()
        app.state.active_controls["run-cancel"] = control
        client = TestClient(app)

        response = client.post("/api/runs/run-cancel/cancel")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"run_id": "run-cancel", "status": "cancellation_requested"})
        self.assertTrue(control.cancel_event.is_set())

    def test_cancel_endpoint_rejects_finished_run(self) -> None:
        app = create_app(history_store=HistoryStore(":memory:"))
        self.addCleanup(app.state.history_store.close)
        client = TestClient(app)
        response = client.post("/api/runs/missing/cancel")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
