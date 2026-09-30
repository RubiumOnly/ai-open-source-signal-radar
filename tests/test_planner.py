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


if __name__ == "__main__":
    unittest.main()
