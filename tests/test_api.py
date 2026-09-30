"""实时编排层的离线测试。"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
import time

from signal_radar.api import _live_report
from signal_radar.api import create_app
from signal_radar.models import RunRequest, SourceStatus
from signal_radar.sources import SourceFetchResult


class _FakeGitHub:
    def __init__(self) -> None:
        self.calls = []

    def collect(self, repository, *, limit, since):
        self.calls.append((repository, limit, since))
        return SourceFetchResult(
            status=SourceStatus(source="GitHub", source_type="first_party", status="ok", records=0)
        )


class _FakeBrowser:
    def collect(self, urls, *, limit):
        return SourceFetchResult(
            status=SourceStatus(source="Browser Use", source_type="dynamic", status="disabled", access_status="not_configured")
        )


class _FakeRSS:
    def __init__(self) -> None:
        self.feed_urls = ("https://example.com/feed.xml",)
        self.calls = []

    def collect(self, urls, *, limit, since):
        self.calls.append((list(urls), limit, since))
        return SourceFetchResult(
            status=SourceStatus(source="Example Blog", source_type="official", status="ok", records=0)
        )


class _FakeHackerNews:
    def __init__(self) -> None:
        self.calls = []

    def collect(self, query, *, limit, since):
        self.calls.append((query, limit, since))
        return SourceFetchResult(
            status=SourceStatus(source="Hacker News", source_type="community", status="ok", records=0)
        )


class LiveOrchestrationTests(unittest.TestCase):
    def test_requested_sources_are_collected_with_a_shared_deadline(self) -> None:
        class Slow:
            def __init__(self, source: str) -> None:
                self.source = source
                self.feed_urls = ()

            def collect(self, *args, **kwargs):
                time.sleep(0.12)
                return SourceFetchResult(
                    status=SourceStatus(source=self.source, source_type="community", status="ok", records=0)
                )

        started = time.perf_counter()
        _live_report(
            RunRequest(mode="live", project="org/repo", sources=["github", "rss", "hackernews"]),
            run_id="run-parallel",
            github=Slow("GitHub"),
            browser=_FakeBrowser(),
            rss=Slow("RSS/Atom"),
            hackernews=Slow("Hacker News"),
        )
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.28)

    def test_fixture_endpoint_rejects_paths_outside_fixture_roots(self) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(create_app())
        response = client.get("/api/report", params={"fixture": "../.env"})
        self.assertEqual(response.status_code, 400)

    def test_window_is_forwarded_to_github_and_rss_sources(self) -> None:
        github = _FakeGitHub()
        rss = _FakeRSS()
        result, statuses = _live_report(
            RunRequest(
                mode="live",
                project="org/repo",
                window_days=14,
                limit=3,
                sources=["github", "rss"],
                feed_urls=["https://custom.example/feed.xml"],
            ),
            run_id="run-test",
            github=github,
            browser=_FakeBrowser(),
            rss=rss,
        )

        self.assertEqual(result.window_days, 14)
        self.assertEqual([status.source for status in statuses], ["GitHub", "Example Blog"])
        self.assertEqual(github.calls[0][0:2], ("org/repo", 3))
        self.assertEqual(rss.calls[0][0], ["https://custom.example/feed.xml"])
        self.assertAlmostEqual((datetime.now(timezone.utc) - github.calls[0][2]).days, 14, delta=1)

    def test_official_source_does_not_start_browser_adapter(self) -> None:
        github = _FakeGitHub()
        browser = _FakeBrowser()
        rss = _FakeRSS()
        _live_report(
            RunRequest(mode="live", project="org/repo", sources=["official"]),
            run_id="run-official",
            github=github,
            browser=browser,
            rss=rss,
        )
        self.assertEqual(len(rss.calls), 1)

    def test_community_alias_forwards_project_query_to_hackernews(self) -> None:
        hackernews = _FakeHackerNews()
        _live_report(
            RunRequest(mode="live", project="org/repo", sources=["community"], limit=4),
            run_id="run-community",
            github=_FakeGitHub(),
            browser=_FakeBrowser(),
            hackernews=hackernews,
        )
        self.assertEqual(hackernews.calls[0][0:2], ("repo", 4))

    def test_custom_community_query_is_forwarded(self) -> None:
        hackernews = _FakeHackerNews()
        _live_report(
            RunRequest(
                mode="live",
                project="org/repo",
                sources=["hackernews"],
                community_query="browser-use agent",
            ),
            run_id="run-community-custom",
            github=_FakeGitHub(),
            browser=_FakeBrowser(),
            hackernews=hackernews,
        )
        self.assertEqual(hackernews.calls[0][0], "browser-use agent")

    def test_metrics_endpoint_returns_current_and_history_snapshots(self) -> None:
        from fastapi.testclient import TestClient

        from signal_radar.history import HistoryStore

        store = HistoryStore(":memory:")
        self.addCleanup(store.close)
        client = TestClient(create_app(history_store=store))
        response = client.post("/api/run", json={"mode": "replay"})
        self.assertEqual(response.status_code, 200)
        metrics = client.get("/api/metrics")
        self.assertEqual(metrics.status_code, 200)
        payload = metrics.json()
        self.assertIn("current", payload)
        self.assertIn("history", payload)
        self.assertEqual(payload["runs"], 1)

    def test_capabilities_endpoint_exposes_read_only_browser_boundary_without_secrets(self) -> None:
        from fastapi.testclient import TestClient

        client = TestClient(create_app(api_token="secret-token"))
        response = client.get("/api/capabilities")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["read_only"])
        self.assertTrue(payload["api_auth_enabled"])
        self.assertIn("allowed_domains", payload["browser_use"])
        self.assertNotIn("api_key", str(payload).lower())


if __name__ == "__main__":
    unittest.main()
