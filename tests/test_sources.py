"""数据源适配器的离线边界测试。"""

from __future__ import annotations

import json
import sys
import types
import unittest
from unittest.mock import patch

from signal_radar.sources import BrowserUseSourceAdapter, GitHubSourceAdapter


class _Response:
    status = 200

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class SourceAdapterTests(unittest.TestCase):
    def test_browser_use_is_safe_without_dependency_or_opt_in(self) -> None:
        result = BrowserUseSourceAdapter().collect(["https://github.com/org/repo/issues"])
        self.assertIn(result.status.status, {"disabled", "auth_required"})
        self.assertEqual(result.articles, [])

    def test_browser_use_rejects_urls_outside_allowlist(self) -> None:
        adapter = BrowserUseSourceAdapter(enabled=True, run_live=True, api_key="test", allowed_domains=["github.com"])
        result = adapter.collect(["https://example.com/post"])
        self.assertEqual(result.status.status, "blocked")
        self.assertEqual(result.status.error, "domain_not_allowed")

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


if __name__ == "__main__":
    unittest.main()
