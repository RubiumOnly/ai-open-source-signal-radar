"""用于回放和运行 Signal Radar 的 FastAPI 服务。"""

from __future__ import annotations

import asyncio
import json
import uuid
import os
import inspect
import math
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .models import (
    AccessStatusRecord,
    Annotation,
    FollowUpRequest,
    HealthResponse,
    ProjectInfo,
    PlanRequest,
    PlanResponse,
    Report,
    Run,
    RunBudget,
    RunRequest,
    RunEvent,
    RunResponse,
    SchedulerRequest,
    SchedulerState,
    SourceStatus,
)
from .cache import DEFAULT_CACHE_PATH, SourceCache
from .history import DEFAULT_HISTORY_PATH, HistoryStore
from .repository import FixtureReportRepository, load_fixture_report
from .scoring import aggregate_report
from .sources import (
    BrowserUseSourceAdapter,
    GitHubSourceAdapter,
    HackerNewsSourceAdapter,
    RedditSourceAdapter,
    RSSSourceAdapter,
    SourceFetchResult,
)
from .scheduler import LocalScheduler, SchedulerAlreadyRunning, scheduler_request_from_env
from .planner import build_plan

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:  # FastAPI 对只使用模型的库用户仍是可选依赖。
    from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse
    from fastapi.staticfiles import StaticFiles
except ImportError:  # pragma: no cover - 仅在最小运行环境中触发
    Body = Depends = FastAPI = Header = HTTPException = Query = None  # type: ignore[assignment,misc]
    StreamingResponse = None  # type: ignore[assignment,misc]
    StaticFiles = None  # type: ignore[assignment,misc]
    CORSMiddleware = None  # type: ignore[assignment,misc]


SERVICE_VERSION = "0.2.0"


class RunControl:
    """Thread-safe cancellation handle shared by the API and live adapters."""

    def __init__(self) -> None:
        self.cancel_event = threading.Event()

    @property
    def cancel_requested(self) -> bool:
        return self.cancel_event.is_set()


def _budget_for(payload: RunRequest, browser: BrowserUseSourceAdapter) -> RunBudget:
    """Resolve request budget without allowing it to exceed adapter hard caps."""

    configured_steps = max(1, min(int(getattr(browser, "max_steps", 12)), 40))
    configured_timeout = max(1.0, min(float(getattr(browser, "timeout_seconds", 180.0)), 900.0))
    requested = payload.budget
    steps = payload.max_steps or (requested.max_steps if requested else configured_steps)
    timeout = payload.timeout_seconds or (requested.timeout_seconds if requested else configured_timeout)
    return RunBudget(
        max_steps=max(1, min(int(steps), configured_steps, 40)),
        timeout_seconds=max(0.1, min(float(timeout), configured_timeout, 900.0)),
    )


def _browser_collect(
    browser: BrowserUseSourceAdapter,
    urls: list[str],
    *,
    limit: int,
    budget: RunBudget,
    cancel_event: threading.Event | None,
) -> SourceFetchResult:
    """Call old injected adapters and new budget-aware adapters alike."""

    collector = browser.collect
    kwargs: dict[str, Any] = {"limit": limit}
    try:
        parameters = inspect.signature(collector).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_kwargs = any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    for name, value in (
        ("max_steps", budget.max_steps),
        ("timeout_seconds", budget.timeout_seconds),
        ("cancel_event", cancel_event),
    ):
        if accepts_kwargs or name in parameters:
            kwargs[name] = value
    return collector(urls, **kwargs)


def _cancelled_status(detail: str = "Run cancelled before all sources completed") -> SourceStatus:
    return SourceStatus(
        source="Run Orchestrator",
        source_type="system",
        status="cancelled",
        access_status="unavailable",
        detail=detail,
        error="cancelled",
    )

# Explicit fixture paths are useful for local replay, but must stay inside the
# repository's fixture directories. Resolving before containment checks also
# prevents a symlink from escaping the allowlist.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOTS = (
    PROJECT_ROOT / "fixtures",
    Path(__file__).resolve().parent / "fixtures",
)


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def _project_value(payload: RunRequest) -> str:
    return payload.repository or payload.project or payload.subject or "browser-use/browser-use"


def _copy_report_for_run(report: Report, run_id: str, *, window_days: int) -> Report:
    return report.model_copy(update={"run_id": run_id, "window_days": window_days})


def _safe_fixture_path(repository: FixtureReportRepository, raw_path: str | Path) -> Path:
    """校验回放 fixture，避免路径穿越、任意文件读取和 symlink 越界。"""

    try:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
        candidate = candidate.resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid fixture path") from exc
    roots = [root.resolve() for root in FIXTURE_ROOTS]
    configured = getattr(repository, "path", None)
    if configured is not None:
        roots.append(Path(configured).resolve().parent)
    inside_root = any(candidate == root or root in candidate.parents for root in roots)
    if candidate.suffix.lower() != ".json" or not inside_root:
        raise HTTPException(status_code=400, detail="fixture must be a JSON file under an allowed fixture directory")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="fixture not found")
    return candidate


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


def _metrics_snapshot(statuses: list[SourceStatus]) -> dict[str, Any]:
    """将来源状态压缩为工作台可展示的运行质量指标。"""

    latencies = sorted(float(status.latency_ms) for status in statuses if status.latency_ms is not None)
    p95 = latencies[min(len(latencies) - 1, max(0, math.ceil(len(latencies) * 0.95) - 1))] if latencies else None
    candidates = sum(status.total_candidates for status in statuses)
    duplicates = sum(status.duplicate_records for status in statuses)
    cache_observed = [status for status in statuses if status.cache_hit or status.total_candidates > 0]
    return {
        "source_count": len(statuses),
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "p95": round(p95, 1) if p95 is not None else None,
            "max": round(latencies[-1], 1) if latencies else None,
        },
        "pages": sum(status.pages for status in statuses),
        "new_records": sum(status.new_records for status in statuses),
        "duplicate_records": duplicates,
        "total_candidates": candidates,
        "duplicate_rate_pct": round(duplicates / candidates * 100.0, 1) if candidates else None,
        "cache_hit_pct": round(
            sum(1 for status in cache_observed if status.cache_hit) / len(cache_observed) * 100.0,
            1,
        ) if cache_observed else None,
    }


def _timeout_result(source: str, *, detail: str, latency_ms: float | None = None) -> SourceFetchResult:
    return SourceFetchResult(
        status=SourceStatus(
            source=source,
            source_type="system" if source == "Run Orchestrator" else "community",
            status="partial",
            access_status="unavailable",
            detail=detail,
            error="budget_timeout",
            latency_ms=latency_ms,
        )
    )


async def _live_report_async(
    payload: RunRequest,
    *,
    run_id: str,
    github: GitHubSourceAdapter,
    browser: BrowserUseSourceAdapter,
    rss: RSSSourceAdapter | None = None,
    hackernews: HackerNewsSourceAdapter | None = None,
    reddit: RedditSourceAdapter | None = None,
    budget: RunBudget | None = None,
    cancel_event: threading.Event | None = None,
    event_sink: Callable[[RunEvent], None] | None = None,
) -> tuple[Report, list[SourceStatus]]:
    """并行执行来源适配器，同时保留每个来源的显式状态。

    适配器仍保持同步接口，统一放进 ``asyncio.to_thread``，避免把现有
    注入式测试适配器和 Browser Use 同步兼容路径改成不可逆的异步 API。
    """

    repository = _project_value(payload)
    budget = budget or _budget_for(payload, browser)
    requested_sources = [payload.source] if payload.source else payload.sources
    requested_sources = list(
        dict.fromkeys(str(item).lower().replace("-", "_") for item in requested_sources if item)
    )
    if not requested_sources:
        requested_sources = ["github"]
    if "all" in requested_sources:
        requested_sources = ["github", "rss", "hackernews", "reddit", "browser_use"]
    if "media" in requested_sources:
        requested_sources.append("browser_use")

    since = datetime.now(timezone.utc) - timedelta(days=payload.window_days)
    deadline = time.monotonic() + budget.timeout_seconds

    def cancelled() -> bool:
        return bool(cancel_event and cancel_event.is_set()) or time.monotonic() >= deadline

    jobs: list[tuple[str, Callable[[], SourceFetchResult]]] = []

    def disabled(source: str, source_type: str, detail: str) -> SourceFetchResult:
        return SourceFetchResult(
            status=SourceStatus(
                source=source,
                source_type=source_type,
                status="disabled",
                access_status="not_configured",
                detail=detail,
            )
        )

    if "github" in requested_sources or "github_api" in requested_sources:
        jobs.append(("GitHub", lambda: github.collect(repository, limit=payload.limit, since=since)))
    if "github_prs" in requested_sources or "github_pull_requests" in requested_sources or "pull_requests" in requested_sources:
        collect_prs = getattr(github, "collect_pull_requests", None)
        if callable(collect_prs):
            jobs.append(("GitHub Pull Requests", lambda: collect_prs(repository, limit=payload.limit, since=since)))
        else:
            jobs.append(("GitHub Pull Requests", lambda: disabled("GitHub Pull Requests", "first_party", "GitHub PR adapter is not configured")))
    if "github_discussions" in requested_sources or "discussions" in requested_sources:
        collect_discussions = getattr(github, "collect_discussions", None)
        if callable(collect_discussions):
            jobs.append(("GitHub Discussions", lambda: collect_discussions(repository, limit=payload.limit, since=since)))
        else:
            jobs.append(("GitHub Discussions", lambda: disabled("GitHub Discussions", "community", "GitHub Discussions adapter is not configured")))
    if "github_pr_comments" in requested_sources or "pull_request_comments" in requested_sources or "pr_comments" in requested_sources:
        collect_comments = getattr(github, "collect_pull_request_comments", None)
        if callable(collect_comments):
            jobs.append(("GitHub PR Comments", lambda: collect_comments(repository, limit=payload.limit, since=since)))
        else:
            jobs.append(("GitHub PR Comments", lambda: disabled("GitHub PR Comments", "community", "GitHub PR comment adapter is not configured")))
    if "browser_use" in requested_sources or "browser" in requested_sources or "dynamic" in requested_sources:
        urls = list(payload.urls) or [
            f"https://github.com/{repository}/discussions",
            f"https://github.com/{repository}/issues",
        ]

        def collect_browser() -> SourceFetchResult:
            remaining = max(0.1, deadline - time.monotonic())
            browser_budget = RunBudget(
                max_steps=budget.max_steps,
                timeout_seconds=min(budget.timeout_seconds, remaining),
            )
            return _browser_collect(
                browser,
                urls,
                limit=payload.limit,
                budget=browser_budget,
                cancel_event=cancel_event,
            )

        jobs.append(("Browser Use", collect_browser))
    if "rss" in requested_sources or "feed" in requested_sources or "official" in requested_sources:
        if rss is None:
            jobs.append(("RSS/Atom", lambda: disabled("RSS/Atom", "official", "RSS adapter is not configured")))
        else:
            feed_urls = payload.feed_urls or list(rss.feed_urls)
            jobs.append(("RSS/Atom", lambda: rss.collect(feed_urls, limit=payload.limit, since=since)))
    if "hackernews" in requested_sources or "hacker_news" in requested_sources or "community" in requested_sources:
        if hackernews is None:
            jobs.append(("Hacker News", lambda: disabled("Hacker News", "community", "Hacker News adapter is not configured")))
        else:
            query = payload.community_query or repository.rsplit("/", 1)[-1]
            jobs.append(("Hacker News", lambda: hackernews.collect(query, limit=payload.limit, since=since)))
    if "reddit" in requested_sources or "reddit_api" in requested_sources:
        if reddit is None:
            jobs.append(("Reddit", lambda: disabled("Reddit", "community", "Reddit adapter is not configured")))
        else:
            query = payload.reddit_query or payload.community_query or repository.rsplit("/", 1)[-1]
            jobs.append(("Reddit", lambda: reddit.collect(query, limit=payload.limit, since=since)))

    async def run_job(source: str, job: Callable[[], SourceFetchResult]) -> SourceFetchResult:
        started = time.perf_counter()
        if event_sink:
            event_sink(RunEvent(
                id=f"event-{uuid.uuid4().hex[:12]}", run_id=run_id,
                type="source_started", stage="collect", source=source,
                message=f"开始采集 {source}",
            ))
        if cancelled():
            result = SourceFetchResult(status=_cancelled_status(f"Run budget expired before {source} collection"))
        else:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                result = await asyncio.wait_for(asyncio.to_thread(job), timeout=remaining)
            except asyncio.TimeoutError:
                result = _timeout_result(
                    source,
                    detail=f"{source} exceeded the run budget",
                    latency_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            except Exception as exc:  # noqa: BLE001 - 单来源失败应保留其它来源结果
                result = SourceFetchResult(
                    status=SourceStatus(
                        source=source,
                        source_type="community",
                        status="error",
                        access_status="error",
                        detail=f"{source} adapter failed",
                        error=str(exc)[:500],
                        latency_ms=round((time.perf_counter() - started) * 1000, 1),
                    )
                )
        if event_sink:
            status = result.status
            event_sink(RunEvent(
                id=f"event-{uuid.uuid4().hex[:12]}", run_id=run_id,
                type="source_completed", stage="collect", source=status.source,
                status=status.status, records=status.records, latency_ms=status.latency_ms,
                pages=status.pages, cache_hit=status.cache_hit,
                new_records=status.new_records, duplicate_records=status.duplicate_records,
                total_candidates=status.total_candidates,
                message=status.detail or f"{status.source} 采集完成",
            ))
        return result

    results = list(await asyncio.gather(*(run_job(source, job) for source, job in jobs)))
    if cancelled() and not any(result.status.status == "cancelled" for result in results):
        results.append(SourceFetchResult(status=_cancelled_status()))
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


def _live_report(
    payload: RunRequest,
    *,
    run_id: str,
    github: GitHubSourceAdapter,
    browser: BrowserUseSourceAdapter,
    rss: RSSSourceAdapter | None = None,
    hackernews: HackerNewsSourceAdapter | None = None,
    reddit: RedditSourceAdapter | None = None,
    budget: RunBudget | None = None,
    cancel_event: threading.Event | None = None,
    event_sink: Callable[[RunEvent], None] | None = None,
) -> tuple[Report, list[SourceStatus]]:
    """同步兼容入口；来源适配器在内部以有界并行方式执行。"""

    return asyncio.run(_live_report_async(
        payload,
        run_id=run_id,
        github=github,
        browser=browser,
        rss=rss,
        hackernews=hackernews,
        reddit=reddit,
        budget=budget,
        cancel_event=cancel_event,
        event_sink=event_sink,
    ))


def create_app(
    *,
    repository: FixtureReportRepository | None = None,
    github_adapter: GitHubSourceAdapter | None = None,
    browser_adapter: BrowserUseSourceAdapter | None = None,
    rss_adapter: RSSSourceAdapter | None = None,
    hackernews_adapter: HackerNewsSourceAdapter | None = None,
    reddit_adapter: RedditSourceAdapter | None = None,
    history_store: HistoryStore | None = None,
    source_cache: SourceCache | None = None,
    api_token: str | None = None,
) -> Any:
    """构建支持注入存储和适配器的应用，便于测试。

    ``SIGNAL_RADAR_API_TOKEN`` 未设置时维持本地开发的无认证行为；设置后，
    运行控制和历史导出接口要求 ``Authorization: Bearer <token>``。公开的
    健康检查、当前报告和来源状态接口仍可供 Dashboard 探活和读取。
    """

    if FastAPI is None:
        raise RuntimeError("FastAPI is not installed; install signal-radar[api] to run the API")
    configured_api_token = api_token if api_token is not None else os.getenv("SIGNAL_RADAR_API_TOKEN")
    configured_api_token = configured_api_token.strip() if configured_api_token else None
    repository = repository or FixtureReportRepository()
    cache_enabled = _env_flag("SIGNAL_RADAR_CACHE_ENABLED", True)
    source_cache = source_cache or (
        SourceCache(os.getenv("SIGNAL_RADAR_CACHE_DB") or DEFAULT_CACHE_PATH) if cache_enabled else None
    )
    github_adapter = github_adapter or GitHubSourceAdapter(
        cache=source_cache,
        max_pages=_env_int("SIGNAL_RADAR_GITHUB_MAX_PAGES", 4, 1, 20),
    )
    browser_adapter = browser_adapter or BrowserUseSourceAdapter(
        enabled=_env_flag("SIGNAL_RADAR_BROWSER_ENABLED"),
        run_live=_env_flag("SIGNAL_RADAR_BROWSER_RUN_LIVE"),
        allowed_domains=tuple(
            item.strip()
            for item in os.getenv("SIGNAL_RADAR_BROWSER_ALLOWED_DOMAINS", "").split(",")
            if item.strip()
        ),
        max_steps=_env_int("SIGNAL_RADAR_BROWSER_MAX_STEPS", 12, 1, 40),
        timeout_seconds=_env_int("SIGNAL_RADAR_BROWSER_TIMEOUT_SECONDS", 180, 1, 900),
    )
    rss_adapter = rss_adapter or RSSSourceAdapter(
        feed_urls=tuple(
            item.strip()
            for item in os.getenv("SIGNAL_RADAR_RSS_FEEDS", "").split(",")
            if item.strip()
        ),
        cache=source_cache,
    )
    hackernews_adapter = hackernews_adapter or HackerNewsSourceAdapter(
        timeout=_env_int("SIGNAL_RADAR_HACKERNEWS_TIMEOUT", 8, 1, 60),
        max_limit=_env_int("SIGNAL_RADAR_HACKERNEWS_MAX_LIMIT", 50, 1, 100),
        max_pages=_env_int("SIGNAL_RADAR_HACKERNEWS_MAX_PAGES", 4, 1, 20),
        cache=source_cache,
    )
    reddit_adapter = reddit_adapter or RedditSourceAdapter(
        timeout=_env_int("SIGNAL_RADAR_REDDIT_TIMEOUT", 8, 1, 60),
        max_limit=_env_int("SIGNAL_RADAR_REDDIT_MAX_LIMIT", 50, 1, 100),
        max_pages=_env_int("SIGNAL_RADAR_REDDIT_MAX_PAGES", 4, 1, 20),
        cache=source_cache,
    )
    history_store = history_store or HistoryStore(
        os.getenv("SIGNAL_RADAR_HISTORY_DB") or DEFAULT_HISTORY_PATH
    )
    service = FastAPI(title="Signal Radar API", version=SERVICE_VERSION)
    cors_origins = [
        item.strip()
        for item in os.getenv(
            "SIGNAL_RADAR_CORS_ORIGINS",
            "http://localhost:4174,http://127.0.0.1:4174,http://localhost:4173,http://127.0.0.1:4173",
        ).split(",")
        if item.strip()
    ]
    # Live POST runs must not be triggerable by arbitrary third-party origins.
    service.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    service.state.latest_report = None
    service.state.latest_run = None
    service.state.history_store = history_store
    service.state.source_cache = source_cache
    service.state.active_controls: dict[str, RunControl] = {}
    service.state.run_workers: dict[str, threading.Thread] = {}
    # Scheduler creation is deliberately side-effect free. It starts only when
    # the explicit schedule endpoint is called; deployments never schedule a
    # collection merely by importing ``app``.
    service.state.scheduler = None
    service.state.api_auth_enabled = bool(configured_api_token)

    def require_api_token(authorization: str | None = Header(default=None)) -> None:
        """校验受保护接口的 Bearer token，避免把凭据写入 URL 或响应。"""

        if not configured_api_token:
            return
        scheme, separator, presented = (authorization or "").partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not presented
            or not secrets.compare_digest(presented.strip(), configured_api_token)
        ):
            raise HTTPException(
                status_code=401,
                detail="valid Bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def append_run_event(event: RunEvent) -> None:
        """追加结构化运行事件；事件只包含状态和来源摘要，不包含思维链。"""

        history_store.add_event(event)

    def run_events(run_id: str) -> list[RunEvent]:
        return history_store.list_events(run_id, limit=1000)

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

    @service.post("/api/plan", response_model=PlanResponse)
    def plan(payload: PlanRequest) -> PlanResponse:
        """把自然语言研究简报解析为前端确认用的计划，不访问网络。"""

        return PlanResponse(plan=build_plan(payload))

    @service.get("/api/report", response_model=Report)
    def report(fixture: str | None = Query(default=None)) -> Report:
        if fixture:
            report_value = load_fixture_report(_safe_fixture_path(repository, fixture))
        else:
            report_value = service.state.latest_report or repository.get_report()
        return report_value

    @service.get("/api/sources", response_model=list[SourceStatus])
    def sources() -> list[SourceStatus]:
        report_value = service.state.latest_report or repository.get_report()
        return report_value.sources

    @service.get("/api/metrics")
    def metrics() -> dict[str, Any]:
        """返回当前报告与最近历史运行的来源质量指标。"""

        report_value = service.state.latest_report or repository.get_report()
        history = history_store.list(limit=100)
        history_statuses = [status for run in history for status in run.source_statuses]
        return {
            "current": _metrics_snapshot(report_value.sources),
            "history": _metrics_snapshot(history_statuses),
            "runs": len(history),
        }

    @service.get("/api/runs", response_model=list[Run], dependencies=[Depends(require_api_token)])
    def runs(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[Run]:
        return history_store.list(limit=limit, offset=offset)

    @service.get("/api/runs/{run_id}", response_model=RunResponse, dependencies=[Depends(require_api_token)])
    def run_detail(run_id: str) -> RunResponse:
        response = history_store.get(run_id)
        if response is None:
            raise HTTPException(status_code=404, detail="run not found")
        return response

    @service.get("/api/runs/{run_id}/events", response_model=list[RunEvent], dependencies=[Depends(require_api_token)])
    def run_event_list(run_id: str) -> list[RunEvent]:
        response = history_store.get(run_id)
        events = run_events(run_id)
        if response is None and not events:
            raise HTTPException(status_code=404, detail="run not found")
        return events

    @service.get("/api/runs/{run_id}/trace", response_model=list[RunEvent], dependencies=[Depends(require_api_token)])
    def run_trace(run_id: str) -> list[RunEvent]:
        """运行事件的回放别名，供 Trace 页面使用。"""

        return run_event_list(run_id)

    @service.get("/api/runs/{run_id}/stream", dependencies=[Depends(require_api_token)])
    def run_event_stream(run_id: str) -> Any:
        """以 SSE 重放或等待结构化运行事件，兼容前端工作台。"""

        response = history_store.get(run_id)
        if response is None and not run_events(run_id):
            raise HTTPException(status_code=404, detail="run not found")

        def generate():
            cursor = 0
            idle_deadline = time.monotonic() + 900.0
            terminal = {"completed", "partial", "cancelled", "failed"}
            while time.monotonic() < idle_deadline:
                events = history_store.list_events(run_id, offset=cursor, limit=100)
                for event in events:
                    payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
                    yield f"event: {event.type}\ndata: {payload}\n\n"
                    cursor += 1
                    if event.type in terminal:
                        return
                current = history_store.get(run_id)
                if current and current.run.status in terminal and not events:
                    return
                time.sleep(0.15)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    def _annotation_report(annotation: Annotation) -> Report | None:
        """Resolve the report whose ids may be manually reviewed."""

        if annotation.run_id:
            response = history_store.get(annotation.run_id)
            if response is None:
                raise HTTPException(status_code=404, detail="run not found for annotation")
            if response.report is None:
                raise HTTPException(status_code=422, detail="run has no report to annotate")
            return response.report
        return service.state.latest_report or repository.get_report()

    def _validate_annotation_target(annotation: Annotation) -> None:
        report_value = _annotation_report(annotation)
        if report_value is None:
            return
        targets = {
            "evidence": {item.id for item in report_value.evidence},
            "claim": {item.id for item in report_value.claims},
            "event": {item.id for item in report_value.events},
        }
        if annotation.target_id not in targets[annotation.target_type]:
            raise HTTPException(
                status_code=422,
                detail=f"unknown {annotation.target_type} target: {annotation.target_id}",
            )

    @service.get("/api/annotations", response_model=list[Annotation], dependencies=[Depends(require_api_token)])
    def annotations(
        run_id: str | None = Query(default=None, max_length=100),
        target_type: str | None = Query(default=None, max_length=20),
        target_id: str | None = Query(default=None, max_length=160),
        label: str | None = Query(default=None, max_length=32),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> list[Annotation]:
        return history_store.list_annotations(
            run_id=run_id,
            target_type=target_type,
            target_id=target_id,
            label=label,
            limit=limit,
            offset=offset,
        )

    @service.get("/api/annotations/export.json", response_model=list[Annotation], dependencies=[Depends(require_api_token)])
    def annotations_export(
        run_id: str | None = Query(default=None, max_length=100),
        target_type: str | None = Query(default=None, max_length=20),
        target_id: str | None = Query(default=None, max_length=160),
        label: str | None = Query(default=None, max_length=32),
    ) -> list[Annotation]:
        """Export review labels as a stable JSON dataset for offline evaluation."""

        return history_store.export_annotations(
            run_id=run_id,
            target_type=target_type,
            target_id=target_id,
            label=label,
        )

    @service.post("/api/annotations", response_model=Annotation, status_code=201, dependencies=[Depends(require_api_token)])
    def annotation_create(payload: Annotation) -> Annotation:
        _validate_annotation_target(payload)
        try:
            return history_store.add_annotation(payload)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @service.post("/api/runs/{run_id}/cancel", status_code=202, dependencies=[Depends(require_api_token)])
    def cancel_run(run_id: str) -> dict[str, str]:
        """Request cancellation of a live run; the worker observes the event."""

        control = service.state.active_controls.get(run_id)
        if control is None:
            response = history_store.get(run_id)
            if response is None:
                raise HTTPException(status_code=404, detail="run not found")
            raise HTTPException(status_code=409, detail="run is no longer active")
        control.cancel_event.set()
        return {"run_id": run_id, "status": "cancellation_requested"}

    def markdown_export(run_id: str) -> Any:
        from fastapi.responses import PlainTextResponse

        markdown = history_store.markdown(run_id)
        if markdown is None:
            raise HTTPException(status_code=404, detail="report not found for run")
        return PlainTextResponse(markdown, media_type="text/markdown; charset=utf-8")

    service.add_api_route(
        "/api/runs/{run_id}/markdown",
        markdown_export,
        methods=["GET"],
        response_class=None,
        dependencies=[Depends(require_api_token)],
        name="run_markdown",
    )
    service.add_api_route(
        "/api/runs/{run_id}/report.md",
        markdown_export,
        methods=["GET"],
        response_class=None,
        include_in_schema=False,
        dependencies=[Depends(require_api_token)],
        name="run_report_markdown",
    )

    def execute(payload: RunRequest) -> RunResponse:
        run_id = payload.run_id or _run_id()
        if run_id in service.state.active_controls:
            raise HTTPException(status_code=409, detail="run_id is already active")
        if not run_events(run_id):
            append_run_event(RunEvent(
                id=f"event-{uuid.uuid4().hex[:12]}", run_id=run_id,
                type="queued", stage="intake", message="运行已排队",
            ))
        if payload.query:
            try:
                parsed_plan = build_plan(PlanRequest(
                    query=payload.query,
                    project=payload.project or payload.repository,
                    window_days=payload.window_days,
                    research_mode=payload.research_mode,
                    sources=payload.sources,
                )).plan
                append_run_event(RunEvent(
                    id=f"event-{uuid.uuid4().hex[:12]}", run_id=run_id,
                    type="plan", stage="intake", message=parsed_plan.explanation,
                ))
            except Exception:
                # 计划预览失败不应阻止兼容的显式 RunRequest。
                pass
        started = datetime.now(timezone.utc)
        budget = _budget_for(payload, browser_adapter)
        control = RunControl()
        # Replay completes synchronously and has nothing useful to cancel.
        if payload.mode == "live":
            service.state.active_controls[run_id] = control
        running = Run(
            run_id=run_id,
            mode=payload.mode,
            status="running",
            subject=_project_value(payload),
            window_days=payload.window_days,
            started_at=started,
            budget=budget,
        )
        service.state.latest_run = running
        history_store.save(running, None)
        append_run_event(RunEvent(
            id=f"event-{uuid.uuid4().hex[:12]}", run_id=run_id,
            type="started", stage="collect", message="开始执行来源采集",
        ))
        if payload.mode == "replay":
            report_value = (
                load_fixture_report(_safe_fixture_path(repository, payload.fixture))
                if payload.fixture
                else repository.get_report()
            )
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
                    hackernews=hackernews_adapter,
                    reddit=reddit_adapter,
                    budget=budget,
                    cancel_event=control.cancel_event,
                    event_sink=append_run_event,
                )
                cancelled = control.cancel_requested or any(
                    item.status == "cancelled" or item.error == "cancelled" for item in statuses
                )
                status_name = "cancelled" if cancelled else "completed" if all(item.status in {"ok", "replay"} for item in statuses) else "partial"
                error = "cancelled" if cancelled else None
            except Exception as exc:  # 适配器异常不应拖垮 API 进程
                report_value = None
                statuses = []
                status_name = "cancelled" if control.cancel_requested else "failed"
                error = "cancelled" if control.cancel_requested else str(exc)

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
            budget=budget,
            cancel_requested=control.cancel_requested,
        )
        service.state.latest_run = run
        service.state.latest_report = report_value
        history_store.save(run, report_value)
        service.state.active_controls.pop(run_id, None)
        append_run_event(RunEvent(
            id=f"event-{uuid.uuid4().hex[:12]}", run_id=run_id,
            type=status_name if status_name in {"completed", "partial", "cancelled", "failed"} else "failed",
            stage="done", status=status_name,
            records=sum(item.records for item in statuses),
            message=error or "运行完成",
        ))
        return RunResponse(run=run, report=report_value)

    def _cancel_for_scheduler(run_id: str) -> bool:
        control = service.state.active_controls.get(run_id)
        if control is None:
            return False
        control.cancel_event.set()
        return True

    scheduler = LocalScheduler(execute, cancel_callback=_cancel_for_scheduler)
    service.state.scheduler = scheduler

    @service.get("/api/schedule", response_model=SchedulerState)
    def schedule_state() -> SchedulerState:
        return scheduler.snapshot()

    @service.post("/api/schedule", response_model=SchedulerState, status_code=202)
    def schedule_start(payload: SchedulerRequest | None = Body(default=None)) -> SchedulerState:
        """显式启动本地有界调度；默认不会自动启动。"""

        try:
            return scheduler.start(payload or scheduler_request_from_env())
        except SchedulerAlreadyRunning as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @service.post("/api/schedule/stop", response_model=SchedulerState, status_code=202)
    def schedule_stop() -> SchedulerState:
        return scheduler.stop()

    @service.delete("/api/schedule", response_model=SchedulerState, status_code=202)
    def schedule_delete() -> SchedulerState:
        return scheduler.stop()

    @service.post("/api/runs", response_model=RunResponse, status_code=202, dependencies=[Depends(require_api_token)])
    def run_async(payload: RunRequest | None = Body(default=None)) -> RunResponse:
        """启动后台运行，供 React 工作台订阅 ``/stream``。"""

        request = payload or RunRequest()
        run_id = request.run_id or _run_id()
        if run_id in service.state.run_workers or history_store.get(run_id) is not None:
            raise HTTPException(status_code=409, detail="run_id already exists")
        queued = Run(
            run_id=run_id,
            mode=request.mode,
            status="queued",
            subject=_project_value(request),
            window_days=request.window_days,
            started_at=datetime.now(timezone.utc),
            budget=_budget_for(request, browser_adapter),
        )
        history_store.save(queued, None)
        append_run_event(RunEvent(
            id=f"event-{uuid.uuid4().hex[:12]}", run_id=run_id,
            type="queued", stage="intake", message="运行已排队",
        ))

        def worker() -> None:
            try:
                execute(request.model_copy(update={"run_id": run_id}))
            except Exception as exc:  # noqa: BLE001 - 失败也要留下可查询状态
                failed = Run(
                    run_id=run_id,
                    mode=request.mode,
                    status="failed",
                    subject=_project_value(request),
                    window_days=request.window_days,
                    started_at=queued.started_at,
                    completed_at=datetime.now(timezone.utc),
                    error=str(exc)[:500],
                    budget=queued.budget,
                )
                history_store.save(failed, None)
                append_run_event(RunEvent(
                    id=f"event-{uuid.uuid4().hex[:12]}", run_id=run_id,
                    type="failed", stage="done", status="failed", message=str(exc)[:500],
                ))
            finally:
                service.state.run_workers.pop(run_id, None)

        thread = threading.Thread(target=worker, name=f"signal-radar-{run_id}", daemon=True)
        service.state.run_workers[run_id] = thread
        thread.start()
        return RunResponse(run=queued, report=None)

    @service.post("/api/runs/{run_id}/follow-up", response_model=RunResponse, status_code=202, dependencies=[Depends(require_api_token)])
    def follow_up(run_id: str, payload: FollowUpRequest) -> RunResponse:
        """基于已有报告创建新的、有界补查运行。"""

        original = history_store.get(run_id)
        if original is None:
            raise HTTPException(status_code=404, detail="run not found")
        if original.report is None:
            raise HTTPException(status_code=422, detail="run has no report to follow up")
        plan = build_plan(PlanRequest(
            query=payload.query,
            project=original.report.project.repository,
            window_days=payload.window_days or original.report.window_days,
            sources=payload.sources,
        ))
        follow_request = RunRequest(
            mode=payload.mode or original.run.mode,
            query=payload.query,
            project=plan.project,
            window_days=plan.window_days,
            research_mode=plan.research_mode,
            focus=plan.focus,
            sources=plan.sources,
        )
        return run_async(follow_request)

    @service.post("/api/run", response_model=RunResponse, dependencies=[Depends(require_api_token)])
    def run(payload: RunRequest | None = Body(default=None)) -> RunResponse:
        return execute(payload or RunRequest())

    @service.get("/api/run", response_model=RunResponse, dependencies=[Depends(require_api_token)])
    def run_get(mode: str = Query(default="replay"), window_days: int = Query(default=7, ge=1, le=3650)) -> RunResponse:
        if mode not in {"replay", "live"}:
            raise HTTPException(status_code=422, detail="mode must be replay or live")
        return execute(RunRequest(mode=mode, window_days=window_days))

    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    if StaticFiles is not None and (frontend_dist / "index.html").is_file():
        # API 路由已先注册，根挂载只负责生产环境的 React 工作台。
        service.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return service


app = create_app() if FastAPI is not None else None

__all__ = ["app", "create_app"]

