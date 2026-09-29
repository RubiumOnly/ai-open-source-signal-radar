"""实时编排层的离线测试。"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

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


class LiveOrchestrationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
