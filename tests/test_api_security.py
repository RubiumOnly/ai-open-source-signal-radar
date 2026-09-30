"""FastAPI replay endpoint security tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - API extra is optional
    TestClient = None  # type: ignore[assignment]

from signal_radar.api import create_app
from signal_radar.history import HistoryStore


@unittest.skipIf(TestClient is None, "FastAPI extra is not installed")
class ReplayEndpointSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def test_report_accepts_repository_fixture(self) -> None:
        response = self.client.get("/api/report", params={"fixture": "fixtures/demo_report.json"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["project"]["repository"], "browser-use/browser-use")

    def test_report_rejects_fixture_path_traversal(self) -> None:
        response = self.client.get("/api/report", params={"fixture": "fixtures/../.env"})
        self.assertEqual(response.status_code, 400)

    def test_report_rejects_malformed_fixture_path(self) -> None:
        response = self.client.get("/api/report", params={"fixture": "bad\x00path.json"})
        self.assertEqual(response.status_code, 400)

    def test_replay_run_rejects_fixture_path_traversal(self) -> None:
        response = self.client.post(
            "/api/run",
            json={"mode": "replay", "fixture": "fixtures/../.env"},
        )
        self.assertEqual(response.status_code, 400)

    def test_cors_is_limited_to_local_dashboard_origins(self) -> None:
        allowed = self.client.get("/api/health", headers={"Origin": "http://localhost:4173"})
        workbench = self.client.get("/api/health", headers={"Origin": "http://localhost:4174"})
        denied = self.client.get("/api/health", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://localhost:4173")
        self.assertEqual(workbench.headers.get("access-control-allow-origin"), "http://localhost:4174")
        self.assertIsNone(denied.headers.get("access-control-allow-origin"))

    def test_configured_token_protects_run_and_history_endpoints(self) -> None:
        store = HistoryStore(":memory:")
        app = create_app(api_token="test-token", history_store=store)
        self.addCleanup(store.close)
        client = TestClient(app)

        # Read-only observability remains available to health checks and the dashboard.
        self.assertEqual(client.get("/api/health").status_code, 200)
        self.assertEqual(client.get("/api/report").status_code, 200)

        unauthenticated = client.post("/api/run", json={"mode": "replay"})
        self.assertEqual(unauthenticated.status_code, 401)
        self.assertEqual(unauthenticated.headers.get("www-authenticate"), "Bearer")
        self.assertEqual(client.get("/api/runs").status_code, 401)

        headers = {"Authorization": "Bearer test-token"}
        created = client.post("/api/run", json={"mode": "replay"}, headers=headers)
        self.assertEqual(created.status_code, 200)
        run_id = created.json()["run"]["run_id"]
        self.assertEqual(client.get("/api/runs", headers=headers).status_code, 200)
        self.assertEqual(client.get(f"/api/runs/{run_id}/markdown", headers=headers).status_code, 200)
        self.assertEqual(client.post(f"/api/runs/{run_id}/cancel").status_code, 401)

    def test_configured_token_rejects_wrong_scheme_and_token(self) -> None:
        store = HistoryStore(":memory:")
        app = create_app(api_token="test-token", history_store=store)
        self.addCleanup(store.close)
        client = TestClient(app)

        for value in ("Basic test-token", "Bearer wrong-token", "Bearer"):
            response = client.post(
                "/api/run",
                json={"mode": "replay"},
                headers={"Authorization": value},
            )
            self.assertEqual(response.status_code, 401)

    def test_environment_token_enables_authentication(self) -> None:
        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        with patch.dict("os.environ", {"SIGNAL_RADAR_API_TOKEN": "from-env"}):
            client = TestClient(create_app(history_store=store))
            self.assertEqual(client.post("/api/run", json={"mode": "replay"}).status_code, 401)
            self.assertEqual(
                client.post(
                    "/api/run",
                    json={"mode": "replay"},
                    headers={"Authorization": "Bearer from-env"},
                ).status_code,
                200,
            )

    def test_plan_and_structured_run_events_are_available(self) -> None:
        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        client = TestClient(create_app(history_store=store))
        planned = client.post("/api/plan", json={
            "query": "分析 browser-use 最近 14 天的安装问题",
        })
        self.assertEqual(planned.status_code, 200)
        self.assertEqual(planned.json()["plan"]["window_days"], 14)

        created = client.post("/api/run", json={"mode": "replay"})
        self.assertEqual(created.status_code, 200)
        run_id = created.json()["run"]["run_id"]
        events = client.get(f"/api/runs/{run_id}/events")
        self.assertEqual(events.status_code, 200)
        event_types = [item["type"] for item in events.json()]
        self.assertIn("started", event_types)
        self.assertIn("completed", event_types)
        stream = client.get(f"/api/runs/{run_id}/stream")
        self.assertEqual(stream.status_code, 200)
        self.assertIn("event: completed", stream.text)


if __name__ == "__main__":
    unittest.main()
