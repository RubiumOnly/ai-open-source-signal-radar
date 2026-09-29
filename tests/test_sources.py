"""数据源适配器的离线边界测试。"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from unittest.mock import patch

from datetime import datetime, timezone

from signal_radar.sources import (
    BrowserRecord,
    BrowserUseSourceAdapter,
    GitHubSourceAdapter,
    HackerNewsSourceAdapter,
    RSSSourceAdapter,
)


class _Response:
    status = 200

    def __init__(self, payload):
        self._payload = (
            payload.encode("utf-8")
            if isinstance(payload, str)
            else json.dumps(payload).encode("utf-8")
        )

    def read(self, *_args):
        return self._payload

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SourceAdapterTests(unittest.TestCase):
    HN_JSON = {
        "hits": [
            {
                "objectID": "123",
                "title": "browser-use release discussion",
                "url": "https://example.com/post",
                "author": "alice",
                "created_at": "2026-09-29T10:00:00.000Z",
                "points": 42,
                "num_comments": 8,
                "story_text": "The new browser-use release fixes timeout errors.",
                "_tags": ["story"],
            },
            {
                "objectID": "124",
                "story_id": "123",
                "story_title": "browser-use release discussion",
                "author": "bob",
                "created_at": "2026-01-01T10:00:00.000Z",
                "comment_text": "The old version is slow.",
                "_tags": ["comment"],
            },
            {
                "objectID": "125",
                "title": "Metadata-only mention",
                "created_at": "2026-09-28T10:00:00.000Z",
                "_tags": ["story"],
            },
        ]
    }

    RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <channel>
        <title>Signal Blog</title>
        <item>
          <title>Release: faster retries</title>
          <link>https://blog.example.com/releases/retries</link>
          <guid>retries-1</guid>
          <pubDate>Mon, 28 Sep 2026 10:00:00 GMT</pubDate>
          <dc:creator>Maintainer</dc:creator>
          <description><![CDATA[Retries fixed timeout errors.]]></description>
        </item>
        <item>
          <title>Old post</title>
          <link>https://blog.example.com/old</link>
          <pubDate>Mon, 01 Jan 2024 10:00:00 GMT</pubDate>
          <description>Older context.</description>
        </item>
      </channel>
    </rss>"""

    ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Official Updates</title>
      <entry>
        <id>tag:example.com,2026:update-1</id>
        <title>New browser support</title>
        <link rel="alternate" href="https://example.com/updates/browser" />
        <updated>2026-09-29T08:00:00Z</updated>
        <author><name>Team</name></author>
        <summary>Improved browser compatibility.</summary>
        <content type="html"><![CDATA[<p>Improved browser compatibility.</p>]]></content>
      </entry>
    </feed>"""

    def test_rss_adapter_maps_entries_and_filters_since(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request.full_url, timeout))
            return _Response(self.RSS_XML)

        result = RSSSourceAdapter(opener=opener).collect(
            ["https://blog.example.com/feed.xml"],
            limit=10,
            since=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result.status.status, "ok")
        self.assertEqual(len(result.articles), 1)
        self.assertEqual(result.articles[0].author, "Maintainer")
        self.assertEqual(result.articles[0].metadata["format"], "rss")
        self.assertEqual(result.evidence[0].evidence_level, "full_text")
        self.assertEqual(requests[0][0], "https://blog.example.com/feed.xml")
        self.assertGreater(requests[0][1], 0)

    def test_hackernews_maps_public_hits_and_filters_since(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request.full_url, timeout))
            return _Response(self.HN_JSON)

        result = HackerNewsSourceAdapter(opener=opener).collect(
            "browser-use", limit=10, since=datetime(2026, 9, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(result.status.status, "ok")
        self.assertEqual(result.status.source, "Hacker News")
        self.assertEqual(len(result.articles), 2)
        self.assertEqual(result.articles[0].metadata["collector"], "hackernews_algolia")
        self.assertEqual(result.articles[0].source_type, "community")
        self.assertEqual(result.evidence[0].evidence_level, "full_text")
        self.assertEqual(result.evidence[1].evidence_level, "metadata_only")
        self.assertIn("tags=story%2Ccomment", requests[0][0])
        self.assertIn("numericFilters=created_at_i%3E%3D", requests[0][0])
        self.assertGreater(requests[0][1], 0)

    def test_hackernews_requires_query_and_surfaces_http_failures(self) -> None:
        missing = HackerNewsSourceAdapter(opener=lambda *_args, **_kwargs: _Response(self.HN_JSON)).collect("")
        self.assertEqual(missing.status.error, "missing_query")

        def opener(_request, timeout):
            raise TimeoutError("timed out")

        failed = HackerNewsSourceAdapter(opener=opener).collect("browser-use")
        self.assertEqual(failed.status.status, "error")
        self.assertIn("timed out", failed.status.error or "")

        def rate_limited_opener(_request, timeout):
            response = _Response({"message": "slow down"})
            response.status = 429
            return response

        limited = HackerNewsSourceAdapter(opener=rate_limited_opener).collect("browser-use")
        self.assertEqual(limited.status.status, "rate_limited")
        self.assertEqual(limited.status.access_status, "rate_limited")

    def test_atom_adapter_maps_namespaces_and_deduplicates(self) -> None:
        payloads = {"https://example.com/atom.xml": self.ATOM_XML}

        def opener(request, timeout):
            return _Response(payloads[request.full_url])

        adapter = RSSSourceAdapter(opener=opener)
        result = adapter.collect(["https://example.com/atom.xml", "https://example.com/atom.xml"], limit=5)
        self.assertEqual(result.status.status, "ok")
        self.assertEqual(len(result.articles), 1)
        self.assertEqual(result.articles[0].url, "https://example.com/updates/browser")
        self.assertEqual(result.articles[0].metadata["format"], "atom")
        self.assertEqual(result.claims[0].claim_type, "official_update")

    def test_feed_limit_is_global_and_malformed_xml_is_explicit(self) -> None:
        def opener(request, timeout):
            return _Response(self.RSS_XML)

        limited = RSSSourceAdapter(opener=opener, max_limit=1).collect(
            ["https://blog.example.com/feed.xml"], limit=50
        )
        self.assertEqual(limited.status.status, "ok")
        self.assertEqual(len(limited.articles), 1)

        def malformed_opener(request, timeout):
            return _Response("<rss><channel>")

        malformed = RSSSourceAdapter(opener=malformed_opener).collect(
            ["https://blog.example.com/feed.xml"]
        )
        self.assertEqual(malformed.status.status, "error")
        self.assertIn("invalid_xml", malformed.status.error or "")

    def test_feed_failures_are_explicit_and_do_not_leak_credentials(self) -> None:
        def opener(_request, timeout):
            raise TimeoutError("timed out")

        result = RSSSourceAdapter(opener=opener).collect(["https://example.com/feed.xml"])
        self.assertEqual(result.status.status, "error")
        self.assertEqual(result.status.access_status, "error")
        self.assertIn("timed out", result.status.error or "")
        self.assertNotIn("DEEPSEEK", (result.status.detail or "").upper())
    def test_browser_use_is_safe_without_dependency_or_opt_in(self) -> None:
        result = BrowserUseSourceAdapter().collect(["https://github.com/org/repo/issues"])
        self.assertIn(result.status.status, {"disabled", "auth_required"})
        self.assertEqual(result.articles, [])

    def test_browser_use_rejects_urls_outside_allowlist(self) -> None:
        adapter = BrowserUseSourceAdapter(enabled=True, run_live=True, api_key="test", allowed_domains=["github.com"])
        result = adapter.collect(["https://example.com/post"])
        self.assertEqual(result.status.status, "blocked")
        self.assertEqual(result.status.error, "domain_not_allowed")

    def test_browser_use_requires_allowlist_for_live_mode(self) -> None:
        adapter = BrowserUseSourceAdapter(enabled=True, run_live=True, api_key="test")
        result = adapter.collect(["https://example.com/post"])
        self.assertEqual(result.status.status, "blocked")
        self.assertEqual(result.status.error, None)
        self.assertIn("allowlist", result.status.detail or "")

    def test_browser_use_async_path_requires_allowlist_too(self) -> None:
        adapter = BrowserUseSourceAdapter(enabled=True, run_live=True, api_key="test")
        result = asyncio.run(adapter.async_collect(["https://example.com/post"]))
        self.assertEqual(result.status.status, "blocked")
        self.assertIn("allowlist", result.status.detail or "")

    def test_github_adapter_maps_public_release_and_issue(self) -> None:
        payloads = [
            [{"id": 1, "html_url": "https://github.com/org/repo/releases/tag/v1", "tag_name": "v1", "body": "Fixed timeout", "published_at": "2026-09-28T00:00:00Z"}],
            [{"id": 2, "html_url": "https://github.com/org/repo/issues/2", "title": "Browser timeout", "body": "The task fails", "updated_at": "2026-09-27T00:00:00Z"}],
        ]

        def opener(_request, timeout):
            self.assertGreater(timeout, 0)
            return _Response(payloads.pop(0))

        result = GitHubSourceAdapter(opener=opener).collect("org/repo", limit=5)
        self.assertEqual(result.status.status, "ok")
        self.assertEqual(len(result.articles), 2)
        self.assertEqual(len(result.evidence), 2)
        self.assertTrue(any(claim.sentiment == "negative" for claim in result.claims))

    def test_github_since_is_sent_to_issues_and_filters_old_records(self) -> None:
        payloads = [
            [{"id": 1, "html_url": "https://github.com/org/repo/releases/tag/v1", "tag_name": "v1", "body": "new release", "published_at": "2026-09-29T00:00:00Z"}],
            [
                {"id": 2, "html_url": "https://github.com/org/repo/issues/2", "title": "new issue", "body": "works", "updated_at": "2026-09-29T00:00:00Z"},
                {"id": 3, "html_url": "https://github.com/org/repo/issues/3", "title": "old issue", "body": "bug", "updated_at": "2026-01-01T00:00:00Z"},
            ],
        ]
        urls = []

        def opener(request, timeout):
            urls.append(request.full_url)
            return _Response(payloads.pop(0))

        result = GitHubSourceAdapter(opener=opener).collect(
            "org/repo",
            limit=5,
            since=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(result.status.status, "ok")
        self.assertEqual({article.title for article in result.articles}, {"v1", "new issue"})
        self.assertIn("since=2026-09-01T00%3A00%3A00Z", urls[1])

    def test_rss_rejects_credentials_and_private_hosts_without_opening(self) -> None:
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return _Response(self.RSS_XML)

        result = RSSSourceAdapter(opener=opener).collect(
            ["https://user:secret@example.com/feed.xml", "http://127.0.0.1/feed.xml"]
        )
        self.assertEqual(result.status.status, "error")
        self.assertIn("invalid_url", result.status.error or "")
        self.assertNotIn("secret", result.status.error or "")
        self.assertEqual(calls, [])

    def test_rss_limits_number_of_feed_requests(self) -> None:
        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            return _Response(self.RSS_XML)

        result = RSSSourceAdapter(opener=opener, max_feeds=1).collect(
            ["https://one.example/feed.xml", "https://two.example/feed.xml"]
        )
        self.assertEqual(result.status.status, "ok")
        self.assertEqual(calls, ["https://one.example/feed.xml"])

    def test_browser_use_opt_in_maps_structured_history(self) -> None:
        class FakeHistory:
            structured_output = {
                "records": [
                    {
                        "title": "安装体验改善",
                        "url": "https://github.com/org/repo/discussions/1",
                        "source": "GitHub Discussions",
                        "excerpt": "新的安装说明解决了部分问题。",
                        "stance": "support",
                        "sentiment": "positive",
                        "topics": ["setup"],
                        "confidence": 0.88,
                    }
                ]
            }

            def final_result(self):
                return self.structured_output

        class FakeAgent:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def run(self, max_steps):
                self.max_steps = max_steps
                return FakeHistory()

        class FakeSession:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def stop(self):
                return None

        llm_calls = []

        def fake_chat_openai(**kwargs):
            llm_calls.append(kwargs)
            return kwargs

        fake_module = types.SimpleNamespace(
            Agent=FakeAgent,
            ChatOpenAI=fake_chat_openai,
            BrowserSession=FakeSession,
        )
        adapter = BrowserUseSourceAdapter(
            enabled=True,
            run_live=True,
            deepseek_api_key="test",
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
            allowed_domains=["github.com"],
            max_steps=3,
        )
        with patch.dict(sys.modules, {"browser_use": fake_module}):
            result = adapter.collect(["https://github.com/org/repo/discussions"])
        self.assertEqual(result.status.status, "ok")
        self.assertEqual(result.articles[0].title, "安装体验改善")
        self.assertEqual(result.claims[0].stance, "support")
        self.assertEqual(llm_calls[0]["base_url"], "https://api.deepseek.com")
        self.assertEqual(llm_calls[0]["model"], "deepseek-chat")

    def test_deepseek_prefers_browser_use_native_wrapper(self) -> None:
        calls = []

        class FakeDeepSeek:
            def __init__(self, **kwargs):
                calls.append(kwargs)
                self.kwargs = kwargs

        class UnexpectedOpenAI:
            def __init__(self, **_kwargs):
                raise AssertionError("generic ChatOpenAI should not be selected for DeepSeek")

        fake_module = types.SimpleNamespace(
            ChatDeepSeek=FakeDeepSeek,
            ChatOpenAI=UnexpectedOpenAI,
        )
        adapter = BrowserUseSourceAdapter(
            enabled=True,
            run_live=True,
            deepseek_api_key="test",
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        llm = adapter._build_llm(fake_module)
        self.assertIsInstance(llm, FakeDeepSeek)
        self.assertEqual(calls, [{
            "model": "deepseek-chat",
            "api_key": "test",
            "base_url": "https://api.deepseek.com",
        }])

    def test_deepseek_falls_back_when_native_wrapper_signature_differs(self) -> None:
        class IncompatibleDeepSeek:
            def __init__(self, **_kwargs):
                raise TypeError("legacy wrapper")

        class GenericOpenAI:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        fake_module = types.SimpleNamespace(
            ChatDeepSeek=IncompatibleDeepSeek,
            ChatOpenAI=GenericOpenAI,
        )
        adapter = BrowserUseSourceAdapter(
            enabled=True,
            run_live=True,
            deepseek_api_key="test",
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
        )
        llm = adapter._build_llm(fake_module)
        self.assertIsInstance(llm, GenericOpenAI)
        self.assertEqual(llm.kwargs["base_url"], "https://api.deepseek.com")

    def test_browser_use_honours_record_limit_after_model_output(self) -> None:
        adapter = BrowserUseSourceAdapter(allowed_domains=["github.com"])
        records = [
            {"title": "one", "url": "https://github.com/org/repo/issues/1"},
            {"title": "two", "url": "https://github.com/org/repo/issues/2"},
        ]
        # Use the public Pydantic contract so this test catches truncation
        # independently of the live model and browser runtime.
        result = adapter._materialise(
            [BrowserRecord(**record) for record in records],
            ["github.com"],
            max_records=1,
        )
        self.assertEqual(len(result.articles), 1)

    def test_browser_use_downgrades_unverified_excerpt_and_date(self) -> None:
        adapter = BrowserUseSourceAdapter(allowed_domains=["github.com"])
        record = BrowserRecord(
            title="Issue title",
            url="https://github.com/org/repo/issues/1",
            excerpt="guessed body",
            published_at="2026-09-29",
        )
        result = adapter._materialise([record], ["github.com"], max_records=1)
        self.assertIsNone(result.articles[0].published_at)
        self.assertIsNone(result.articles[0].excerpt)
        self.assertEqual(result.evidence[0].evidence_level, "metadata_only")
        self.assertEqual(result.evidence[0].quote, "")


if __name__ == "__main__":
    unittest.main()
