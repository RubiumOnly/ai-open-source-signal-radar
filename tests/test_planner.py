"""研究计划解析的确定性契约测试。"""

from __future__ import annotations

import unittest

from signal_radar.models import PlanRequest
from signal_radar.planner import build_plan


class PlannerTests(unittest.TestCase):
    def test_extracts_repository_window_topics_and_sources(self) -> None:
        plan = build_plan(PlanRequest(
            query="分析 https://github.com/acme/tool 最近 14 天的安装问题和 Reddit 反馈",
        ))
        self.assertEqual(plan.project, "acme/tool")
        self.assertEqual(plan.window_days, 14)
        self.assertIn("安装与兼容性", plan.focus)
        self.assertIn("github", plan.sources)
        self.assertIn("reddit", plan.sources)

    def test_defaults_to_open_source_monitoring_sources(self) -> None:
        plan = build_plan(PlanRequest(query="browser-use 的版本变化"))
        self.assertEqual(plan.project, "browser-use/browser-use")
        self.assertEqual(plan.window_days, 30)
        self.assertEqual(plan.sources[:3], ["github", "rss", "hackernews"])

    def test_preserves_explicit_public_urls_for_confirmed_dynamic_collection(self) -> None:
        plan = build_plan(PlanRequest(
            query="查看 https://www.csdn.net/article/123，分析登录后可见的安装反馈",
        ))
        self.assertEqual(plan.urls, ["https://www.csdn.net/article/123"])
        self.assertIn("browser_use", plan.sources)
        self.assertIn("1 个明确页面", plan.explanation)

    def test_drops_private_or_credential_bearing_urls(self) -> None:
        plan = build_plan(PlanRequest(
            query="检查 http://127.0.0.1:8000/debug 和 https://user:secret@example.com/post",
        ))
        self.assertEqual(plan.urls, [])

    def test_explicit_deep_github_url_selects_browser_use_without_repo_root_overhead(self) -> None:
        detail = build_plan(PlanRequest(query="分析 https://github.com/acme/tool/issues/42"))
        self.assertIn("browser_use", detail.sources)
        root = build_plan(PlanRequest(query="分析 https://github.com/acme/tool"))
        self.assertNotIn("browser_use", root.sources)


if __name__ == "__main__":
    unittest.main()
