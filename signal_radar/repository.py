"""回放报告存储和 fixture 辅助工具。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    AccessStatusRecord,
    Article,
    Claim,
    Event,
    Evidence,
    ProjectInfo,
    Report,
    SourceStatus,
)
from .scoring import aggregate_report


PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE_PATH = PACKAGE_DIR / "fixtures" / "report.json"
PROJECT_FIXTURE_PATH = PACKAGE_DIR.parent / "fixtures" / "demo_report.json"


def _default_fixture_path() -> Path:
    """优先使用包内 fixture，其次使用仓库根目录的演示 fixture。"""

    return DEFAULT_FIXTURE_PATH if DEFAULT_FIXTURE_PATH.exists() else PROJECT_FIXTURE_PATH


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _demo_report() -> Report:
    """没有 fixture 文件时返回确定性的离线报告。"""

    project = ProjectInfo(
        name="browser-use",
        repository="browser-use/browser-use",
        description="Make websites accessible for AI agents",
        version="0.10.x",
        last_release_at=_parse_datetime("2026-09-24T09:00:00Z"),
    )
    articles = [
        Article(
            id="github-issue-1024",
            url="https://github.com/browser-use/browser-use/issues/1024",
            title="Browser session occasionally times out on long tasks",
            source="GitHub Issues",
            source_type="first_party",
            excerpt="Several users reported timeout retries after a long-running task.",
            published_at=_parse_datetime("2026-09-26T11:00:00Z"),
            content_hash="demo-1024",
            tags=["reliability", "timeout"],
        ),
        Article(
            id="github-release-010x",
            url="https://github.com/browser-use/browser-use/releases",
            title="browser-use 0.10.x released",
            source="GitHub Releases",
            source_type="first_party",
            excerpt="Release adds improved retry handling and browser session hooks.",
            published_at=_parse_datetime("2026-09-24T09:00:00Z"),
            content_hash="demo-release",
            tags=["release", "retry"],
        ),
        Article(
            id="hackernews-demo",
            url="https://news.ycombinator.com/item?id=demo",
            title="Show HN: Browser automation for AI agents",
            source="Hacker News",
            source_type="community",
            excerpt="Developers discuss setup complexity and useful abstractions.",
            published_at=_parse_datetime("2026-09-22T15:30:00Z"),
            content_hash="demo-hn",
            tags=["community", "setup"],
        ),
    ]
    evidence = [
        Evidence(
            id="ev-1024",
            article_id="github-issue-1024",
            url=articles[0].url,
            source=articles[0].source,
            title=articles[0].title,
            quote="Several users reported timeout retries after a long-running task.",
            confidence=0.94,
            published_at=articles[0].published_at,
            content_hash=articles[0].content_hash,
        ),
        Evidence(
            id="ev-release",
            article_id="github-release-010x",
            url=articles[1].url,
            source=articles[1].source,
            title=articles[1].title,
            quote="Release adds improved retry handling and browser session hooks.",
            confidence=0.99,
            published_at=articles[1].published_at,
            content_hash=articles[1].content_hash,
        ),
        Evidence(
            id="ev-hn",
            article_id="hackernews-demo",
            url=articles[2].url,
            source=articles[2].source,
            title=articles[2].title,
            quote="Developers discuss setup complexity and useful abstractions.",
            confidence=0.82,
            published_at=articles[2].published_at,
            content_hash=articles[2].content_hash,
        ),
    ]
    claims = [
        Claim(
            id="claim-timeout",
            text="Long-running tasks can time out and require retries.",
            claim_type="reliability",
            stance="oppose",
            sentiment="negative",
            confidence=0.9,
            evidence_ids=["ev-1024"],
            article_ids=["github-issue-1024"],
            topics=["reliability", "timeouts"],
        ),
        Claim(
            id="claim-retry",
            text="The latest release improves retry handling.",
            claim_type="release",
            stance="support",
            sentiment="positive",
            confidence=0.92,
            evidence_ids=["ev-release"],
            article_ids=["github-release-010x"],
            topics=["reliability", "release"],
        ),
        Claim(
            id="claim-setup",
            text="Developers find the initial setup more complex than expected.",
            claim_type="usability",
            stance="oppose",
            sentiment="negative",
            confidence=0.72,
            evidence_ids=["ev-hn"],
            article_ids=["hackernews-demo"],
            topics=["setup"],
        ),
    ]
    events = [
        Event(
            id="event-timeout",
            title="Timeout reports are recurring",
            category="reliability",
            summary="Community issues mention timeout retries in long tasks.",
            risk_level="high",
            risk_score=68,
            sentiment="negative",
            occurred_at=articles[0].published_at,
            article_ids=["github-issue-1024"],
            claim_ids=["claim-timeout"],
            evidence_ids=["ev-1024"],
        ),
        Event(
            id="event-release",
            title="Retry handling improved in the latest release",
            category="release",
            summary="Release notes describe retry and session improvements.",
            risk_level="low",
            risk_score=18,
            sentiment="positive",
            occurred_at=articles[1].published_at,
            article_ids=["github-release-010x"],
            claim_ids=["claim-retry"],
            evidence_ids=["ev-release"],
        ),
    ]
    statuses = [
        SourceStatus(
            source="GitHub",
            source_type="first_party",
            status="replay",
            access_status="public",
            records=2,
            detail="Fixture replay",
        ),
        SourceStatus(
            source="Hacker News",
            source_type="community",
            status="replay",
            access_status="public",
            records=1,
            detail="Fixture replay",
        ),
    ]
    access = [
        AccessStatusRecord(source="GitHub", status="public", evidence_level="full_text"),
        AccessStatusRecord(source="Hacker News", status="public", evidence_level="excerpt"),
        AccessStatusRecord(
            source="CSDN",
            status="auth_required",
            reason="Article body requires an authenticated browser session",
            evidence_level="none",
        ),
    ]
    return aggregate_report(
        project=project,
        articles=articles,
        claims=claims,
        events=events,
        evidence=evidence,
        source_statuses=statuses,
        access_status=access,
        window_days=7,
        report_id="replay-demo",
        generated_at=_parse_datetime("2026-09-29T02:00:00Z"),
    )


def load_fixture_report(path: str | Path | None = None) -> Report:
    """读取报告 JSON fixture，同时兼容原始对象和 ``{"report": ...}`` 结构。"""

    fixture_path = Path(path) if path else _default_fixture_path()
    if not fixture_path.exists():
        return _demo_report()
    try:
        payload: Any = json.loads(fixture_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("report"), dict):
            payload = payload["report"]
        return Report.model_validate(payload)
    except (OSError, ValueError, TypeError):
        # 可选 fixture 损坏时不应导致只读演示 API 失败。
        return _demo_report()


class FixtureReportRepository:
    """供 FastAPI 应用和测试使用的轻量仓储抽象。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else _default_fixture_path()

    def get_report(self) -> Report:
        return load_fixture_report(self.path)

