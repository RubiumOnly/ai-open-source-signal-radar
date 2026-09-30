"""增量缓存、条件请求和来源指标测试。"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from signal_radar.cache import SourceCache
from signal_radar.sources import GitHubSourceAdapter, HackerNewsSourceAdapter


class _Response:
    status = 200

    def __init__(self, payload, *, headers=None, status=200):
        self._payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.headers = headers or {}
        self.status = status

    def read(self, *_args):
        return self._payload

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class CacheTests(unittest.TestCase):
    def test_http_metadata_and_record_fingerprint_round_trip(self) -> None:
        cache = SourceCache(":memory:")
        self.addCleanup(cache.close)
        self.assertEqual(cache.request_headers("feed:one"), {})
        self.assertTrue(cache.save_response("feed:one", b"v1", etag='"a"', last_modified="today"))
        self.assertEqual(
            cache.request_headers("feed:one"),
            {"If-None-Match": '"a"', "If-Modified-Since": "today"},
        )
        self.assertFalse(cache.save_response("feed:one", b"v1", etag='"a"'))
        self.assertTrue(cache.save_response("feed:one", b"v2"))
        self.assertTrue(cache.register_record("github:org/repo", "issue-1", "hash-1"))
        self.assertFalse(cache.register_record("github:org/repo", "issue-1", "hash-1"))
        self.assertTrue(cache.register_record("github:org/repo", "issue-1", "hash-2"))

    def test_github_same_payload_is_reported_as_cache_hit_and_duplicate(self) -> None:
        payloads = [
            [{"id": 1, "html_url": "https://github.com/org/repo/releases/1", "tag_name": "v1", "body": "release", "published_at": "2026-09-28T00:00:00Z"}],
            [{"id": 2, "html_url": "https://github.com/org/repo/issues/2", "title": "works", "body": "works", "updated_at": "2026-09-27T00:00:00Z"}],
        ]
        cache = SourceCache(":memory:")
        self.addCleanup(cache.close)

        def opener(_request, timeout):
            self.assertGreater(timeout, 0)
            return _Response(payloads.pop(0), headers={"ETag": '"stable"'})

        adapter = GitHubSourceAdapter(opener=opener, cache=cache, max_pages=2)
        first = adapter.collect("org/repo", limit=5, since=datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(first.status.new_records, 2)
        self.assertEqual(first.status.duplicate_records, 0)

        payloads.extend([
            [{"id": 1, "html_url": "https://github.com/org/repo/releases/1", "tag_name": "v1", "body": "release", "published_at": "2026-09-28T00:00:00Z"}],
            [{"id": 2, "html_url": "https://github.com/org/repo/issues/2", "title": "works", "body": "works", "updated_at": "2026-09-27T00:00:00Z"}],
        ])
        second = adapter.collect("org/repo", limit=5, since=datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertTrue(second.status.cache_hit)
        self.assertEqual(second.status.records, 0)
        self.assertEqual(second.status.duplicate_records, 2)
        self.assertEqual(second.status.total_candidates, 2)

    def test_hackernews_paginates_when_page_is_full(self) -> None:
        pages = [
            {"hits": [{"objectID": "1", "title": "one", "created_at": "2026-09-29T00:00:00Z", "story_text": "one"}], "nbPages": 2},
            {"hits": [{"objectID": "2", "title": "two", "created_at": "2026-09-28T00:00:00Z", "story_text": "two"}], "nbPages": 2},
        ]
        requests = []

        def opener(request, timeout):
            self.assertGreater(timeout, 0)
            requests.append(request.full_url)
            return _Response(pages.pop(0))

        result = HackerNewsSourceAdapter(opener=opener, max_pages=3).collect("browser-use", limit=1)
        self.assertEqual(result.status.pages, 2)
        self.assertEqual(len(requests), 2)
        self.assertIn("page=0", requests[0])
        self.assertIn("page=1", requests[1])
        self.assertEqual(result.status.total_candidates, 1)

    def test_github_pull_requests_are_opt_in_and_traceable(self) -> None:
        payload = [{
            "id": 42,
            "html_url": "https://github.com/org/repo/pull/42",
            "title": "Improve retry handling",
            "body": "Document retry behavior.",
            "updated_at": "2026-09-29T00:00:00Z",
            "state": "open",
            "draft": False,
        }]

        def opener(request, timeout):
            self.assertIn("/pulls?", request.full_url)
            self.assertGreater(timeout, 0)
            return _Response(payload)

        result = GitHubSourceAdapter(opener=opener).collect_pull_requests(
            "org/repo", limit=5, since=datetime(2026, 9, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(result.status.source, "GitHub Pull Requests")
        self.assertEqual(result.status.records, 1)
        self.assertEqual(result.articles[0].metadata["state"], "open")
        self.assertEqual(result.claims[0].claim_type, "pull_request")

    def test_github_discussions_are_opt_in_and_traceable(self) -> None:
        payload = [{
            "id": 7,
            "number": 7,
            "html_url": "https://github.com/org/repo/discussions/7",
            "title": "Setup feedback",
            "body": "Installation fails on Windows.",
            "updated_at": "2026-09-29T00:00:00Z",
            "category": {"name": "Q&A"},
        }]

        def opener(request, timeout):
            self.assertIn("/discussions?", request.full_url)
            self.assertGreater(timeout, 0)
            return _Response(payload)

        result = GitHubSourceAdapter(opener=opener).collect_discussions(
            "org/repo", limit=5, since=datetime(2026, 9, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(result.status.source, "GitHub Discussions")
        self.assertEqual(result.status.records, 1)
        self.assertEqual(result.articles[0].metadata["category"], "Q&A")
        self.assertEqual(result.claims[0].claim_type, "discussion")

    def test_github_pull_request_comments_are_opt_in_and_traceable(self) -> None:
        payload = [{
            "id": 99,
            "html_url": "https://github.com/org/repo/pull/4#discussion_r99",
            "pull_request_url": "https://api.github.com/repos/org/repo/pulls/4",
            "body": "This change may break the retry path.",
            "updated_at": "2026-09-29T00:00:00Z",
            "user": {"login": "reviewer"},
            "path": "src/retry.py",
            "line": 42,
        }]

        def opener(request, timeout):
            self.assertIn("/pulls/comments?", request.full_url)
            self.assertGreater(timeout, 0)
            return _Response(payload)

        result = GitHubSourceAdapter(opener=opener).collect_pull_request_comments(
            "org/repo", limit=5, since=datetime(2026, 9, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(result.status.source, "GitHub PR Comments")
        self.assertEqual(result.status.records, 1)
        self.assertEqual(result.articles[0].author, "reviewer")
        self.assertEqual(result.claims[0].claim_type, "pull_request_comment")


if __name__ == "__main__":
    unittest.main()
