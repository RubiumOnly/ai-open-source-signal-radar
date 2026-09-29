"""用于回放和运行 Signal Radar 的 FastAPI 服务。"""

from __future__ import annotations

import uuid
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import (
    AccessStatusRecord,
    HealthResponse,
    ProjectInfo,
    Report,
    Run,
    RunRequest,
    RunResponse,
    SourceStatus,
)
from .repository import FixtureReportRepository, load_fixture_report
from .scoring import aggregate_report
from .sources import BrowserUseSourceAdapter, GitHubSourceAdapter, RSSSourceAdapter, SourceFetchResult

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:  # FastAPI 对只使用模型的库用户仍是可选依赖。
    from fastapi import Body, FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:  # pragma: no cover - 仅在最小运行环境中触发
    Body = FastAPI = HTTPException = Query = None  # type: ignore[assignment,misc]
    CORSMiddleware = None  # type: ignore[assignment,misc]


SERVICE_VERSION = "0.1.0"


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _project_value(payload: RunRequest) -> str:
    return payload.repository or payload.project or payload.subject or "browser-use/browser-use"


def _copy_report_for_run(report: Report, run_id: str, *, window_days: int) -> Report:
    return report.model_copy(update={"run_id": run_id, "window_days": window_days})


def _access_from_sources(statuses: list[SourceStatus]) -> list[AccessStatusRecord]:
    return [
        AccessStatusRecord(
            source=status.source,
            status=status.access_status,
            reason=status.detail or status.error,
            evidence_level="full_text" if status.records else "none",
        )
        for status in statuses
    ]


def _live_report(
    payload: RunRequest,
    *,
    run_id: str,
    github: GitHubSourceAdapter,
    browser: BrowserUseSourceAdapter,
    rss: RSSSourceAdapter | None = None,
) -> tuple[Report, list[SourceStatus]]:
    repository = _project_value(payload)
    requested_sources = [payload.source] if payload.source else payload.sources
    requested_sources = [str(item).lower().replace("-", "_") for item in requested_sources if item]
    if not requested_sources:
        requested_sources = ["github"]
    if "all" in requested_sources:
        requested_sources = ["github", "rss", "browser_use"]
    if "media" in requested_sources:
        requested_sources.append("browser_use")

    results: list[SourceFetchResult] = []
    since = datetime.now(timezone.utc) - timedelta(days=payload.window_days)
    if "github" in requested_sources or "github_api" in requested_sources:
        results.append(github.collect(repository, limit=payload.limit, since=since))
    if "browser_use" in requested_sources or "browser" in requested_sources or "dynamic" in requested_sources:
        urls = list(payload.urls)
        if not urls:
            urls = [
                f"https://github.com/{repository}/discussions",
                f"https://github.com/{repository}/issues",
            ]
        results.append(browser.collect(urls, limit=payload.limit))
    if "rss" in requested_sources or "feed" in requested_sources or "official" in requested_sources:
        if rss is None:
            results.append(
                SourceFetchResult(
                    status=SourceStatus(
                        source="RSS/Atom",
                        source_type="official",
                        status="disabled",
                        access_status="not_configured",
                        detail="RSS adapter is not configured",
                    )
                )
            )
        else:
            feed_urls = payload.feed_urls or list(rss.feed_urls)
            results.append(rss.collect(feed_urls, limit=payload.limit, since=since))
    if not results:
        results.append(
            SourceFetchResult(
                status=SourceStatus(
                    source="requested sources",
                    source_type="system",
                    status="error",
                    access_status="error",
                    detail="No supported source was requested",
                    error="unsupported_source",
                )
            )
        )

    statuses = [result.status for result in results]
    articles = [article for result in results for article in result.articles]
    claims = [claim for result in results for claim in result.claims]
    events = [event for result in results for event in result.events]
    evidence = [item for result in results for item in result.evidence]
    project = ProjectInfo(name=repository.split("/")[-1], repository=repository)
    report = aggregate_report(
        project=project,
        articles=articles,
        claims=claims,
        events=events,
        evidence=evidence,
        source_statuses=statuses,
        access_status=_access_from_sources(statuses),
        window_days=payload.window_days,
        run_id=run_id,
    )
    return report, statuses


def create_app(
    *,
    repository: FixtureReportRepository | None = None,
    github_adapter: GitHubSourceAdapter | None = None,
    browser_adapter: BrowserUseSourceAdapter | None = None,
    rss_adapter: RSSSourceAdapter | None = None,
) -> Any:
    """构建支持注入存储和适配器的应用，便于测试。"""

    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed; install signal-radar[api] to run the API")
    repository = repository or FixtureReportRepository()
    github_adapter = github_adapter or GitHubSourceAdapter()
    browser_adapter = browser_adapter or BrowserUseSourceAdapter(
        enabled=_env_flag("SIGNAL_RADAR_BROWSER_ENABLED"),
        run_live=_env_flag("SIGNAL_RADAR_BROWSER_RUN_LIVE"),
        allowed_domains=tuple(
            item.strip()
            for item in os.getenv("SIGNAL_RADAR_BROWSER_ALLOWED_DOMAINS", "").split(",")
            if item.strip()
        ),
        max_steps=int(os.getenv("SIGNAL_RADAR_BROWSER_MAX_STEPS", "12")),
    )
    rss_adapter = rss_adapter or RSSSourceAdapter(
        feed_urls=tuple(
            item.strip()
            for item in os.getenv("SIGNAL_RADAR_RSS_FEEDS", "").split(",")
            if item.strip()
        ),
    )
    service = FastAPI(title="Signal Radar API", version=SERVICE_VERSION)
    # API 默认只读。宽松策略便于本地/静态仪表盘使用，部署时可收窄来源。
    service.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    service.state.latest_report = None
    service.state.latest_run = None

    @service.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        fixture_path = getattr(repository, "path", None)
        fixture_ok = True if fixture_path is None else Path(fixture_path).exists()
        if not fixture_ok:
            # 仓储包含内置演示回退，因此可选文件缺失不应让回放服务不健康。
            try:
                repository.get_report()
                fixture_ok = True
            except Exception:
                fixture_ok = False
        return HealthResponse(status="ok" if fixture_ok else "degraded", version=SERVICE_VERSION, replay_available=True)

    @service.get("/api/report", response_model=Report)
    def report(fixture: str | None = Query(default=None)) -> Report:
        if fixture:
            report_value = load_fixture_report(Path(fixture))
        else:
            report_value = service.state.latest_report or repository.get_report()
        return report_value

    @service.get("/api/sources", response_model=list[SourceStatus])
    def sources() -> list[SourceStatus]:
        report_value = service.state.latest_report or repository.get_report()
        return report_value.sources

    def execute(payload: RunRequest) -> RunResponse:
        run_id = _run_id()
        started = datetime.now(timezone.utc)
        if payload.mode == "replay":
            report_value = load_fixture_report(Path(payload.fixture)) if payload.fixture else repository.get_report()
            report_value = _copy_report_for_run(report_value, run_id, window_days=payload.window_days)
            statuses = report_value.sources
            status_name = "completed"
            error = None
        else:
            try:
                report_value, statuses = _live_report(
                    payload,
                    run_id=run_id,
                    github=github_adapter,
                    browser=browser_adapter,
                    rss=rss_adapter,
                )
                status_name = "completed" if all(item.status in {"ok", "replay"} for item in statuses) else "partial"
                error = None
            except Exception as exc:  # 适配器异常不应拖垮 API 进程
                report_value = None
                statuses = []
                status_name = "failed"
                error = str(exc)

        completed = datetime.now(timezone.utc)
        run = Run(
            run_id=run_id,
            mode=payload.mode,
            status=status_name,
            subject=_project_value(payload),
            window_days=payload.window_days,
            started_at=started,
            completed_at=completed,
            report_id=report_value.report_id if report_value else None,
            source_statuses=statuses,
            error=error,
        )
        service.state.latest_run = run
        service.state.latest_report = report_value
        return RunResponse(run=run, report=report_value)

    @service.post("/api/run", response_model=RunResponse)
    def run(payload: RunRequest | None = Body(default=None)) -> RunResponse:
        return execute(payload or RunRequest())

    @service.get("/api/run", response_model=RunResponse)
    def run_get(mode: str = Query(default="replay"), window_days: int = Query(default=7, ge=1, le=3650)) -> RunResponse:
        if mode not in {"replay", "live"}:
            raise HTTPException(status_code=422, detail="mode must be replay or live")
        return execute(RunRequest(mode=mode, window_days=window_days))

    return service


app = create_app() if FastAPI is not None else None

__all__ = ["app", "create_app"]

