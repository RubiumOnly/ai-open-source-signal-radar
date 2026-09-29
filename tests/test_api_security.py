"""FastAPI replay endpoint security tests."""

from __future__ import annotations

import unittest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - API extra is optional
    TestClient = None  # type: ignore[assignment]

from signal_radar.api import create_app


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


if __name__ == "__main__":
    unittest.main()
