"""用于回放和运行 Signal Radar 的 FastAPI 服务。"""

from __future__ import annotations

import uuid
import os
import inspect
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import (
    AccessStatusRecord,
    HealthResponse,
    ProjectInfo,
    Report,
    Run,
    RunBudget,
    RunRequest,
    RunResponse,
    SourceStatus,
)
from .history import DEFAULT_HISTORY_PATH, HistoryStore
from .repository import FixtureReportRepository, load_fixture_report
from .scoring import aggregate_report
from .sources import (
    BrowserUseSourceAdapter,
    GitHubSourceAdapter,
    HackerNewsSourceAdapter,
    RSSSourceAdapter,
    SourceFetchResult,
)

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


def _live_report(
    payload: RunRequest,
    *,
    run_id: str,
    github: GitHubSourceAdapter,
    browser: BrowserUseSourceAdapter,
    rss: RSSSourceAdapter | None = None,
    hackernews: HackerNewsSourceAdapter | None = None,
    budget: RunBudget | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[Report, list[SourceStatus]]:
    repository = _project_value(payload)
    budget = budget or _budget_for(payload, browser)
    requested_sources = [payload.source] if payload.source else payload.sources
    requested_sources = list(
        dict.fromkeys(str(item).lower().replace("-", "_") for item in requested_sources if item)
    )
    if not requested_sources:
        requested_sources = ["github"]
    if "all" in requested_sources:
        requested_sources = ["github", "rss", "hackernews", "browser_use"]
    if "media" in requested_sources:
        requested_sources.append("browser_use")

    results: list[SourceFetchResult] = []
    since = datetime.now(timezone.utc) - timedelta(days=payload.window_days)
    deadline = time.monotonic() + budget.timeout_seconds

    def cancelled() -> bool:
        return bool(cancel_event and cancel_event.is_set()) or time.monotonic() >= deadline

    if "github" in requested_sources or "github_api" in requested_sources:
        if cancelled():
            results.append(SourceFetchResult(status=_cancelled_status("Run budget expired before GitHub collection")))
        else:
            results.append(github.collect(repository, limit=payload.limit, since=since))
    if "browser_use" in requested_sources or "browser" in requested_sources or "dynamic" in requested_sources:
        if cancelled():
            results.append(SourceFetchResult(status=_cancelled_status("Run budget expired before Browser Use collection")))
        else:
            urls = list(payload.urls)
            if not urls:
                urls = [
                    f"https://github.com/{repository}/discussions",
                    f"https://github.com/{repository}/issues",
                ]
            remaining = max(0.1, deadline - time.monotonic())
            browser_budget = RunBudget(max_steps=budget.max_steps, timeout_seconds=min(budget.timeout_seconds, remaining))
            results.append(
                _browser_collect(
                    browser,
                    urls,
                    limit=payload.limit,
                    budget=browser_budget,
                    cancel_event=cancel_event,
                )
            )
    if "rss" in requested_sources or "feed" in requested_sources or "official" in requested_sources:
        if cancelled():
            results.append(SourceFetchResult(status=_cancelled_status("Run budget expired before RSS collection")))
        elif rss is None:
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
    if "hackernews" in requested_sources or "hacker_news" in requested_sources or "community" in requested_sources:
        if cancelled():
            results.append(SourceFetchResult(status=_cancelled_status("Run budget expired before Hacker News collection")))
        elif hackernews is None:
            results.append(
                SourceFetchResult(
                    status=SourceStatus(
                        source="Hacker News",
                        source_type="community",
                        status="disabled",
                        access_status="not_configured",
                        detail="Hacker News adapter is not configured",
                    )
                )
            )
        else:
            query = payload.community_query or repository.rsplit("/", 1)[-1]
            results.append(hackernews.collect(query, limit=payload.limit, since=since))
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


def create_app(
    *,
    repository: FixtureReportRepository | None = None,
    github_adapter: GitHubSourceAdapter | None = None,
    browser_adapter: BrowserUseSourceAdapter | None = None,
    rss_adapter: RSSSourceAdapter | None = None,
    hackernews_adapter: HackerNewsSourceAdapter | None = None,
    history_store: HistoryStore | None = None,
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
        max_steps=_env_int("SIGNAL_RADAR_BROWSER_MAX_STEPS", 12, 1, 40),
        timeout_seconds=_env_int("SIGNAL_RADAR_BROWSER_TIMEOUT_SECONDS", 180, 1, 900),
    )
    rss_adapter = rss_adapter or RSSSourceAdapter(
        feed_urls=tuple(
            item.strip()
            for item in os.getenv("SIGNAL_RADAR_RSS_FEEDS", "").split(",")
            if item.strip()
        ),
    )
    hackernews_adapter = hackernews_adapter or HackerNewsSourceAdapter(
        timeout=_env_int("SIGNAL_RADAR_HACKERNEWS_TIMEOUT", 8, 1, 60),
        max_limit=_env_int("SIGNAL_RADAR_HACKERNEWS_MAX_LIMIT", 50, 1, 100),
    )
    history_store = history_store or HistoryStore(
        os.getenv("SIGNAL_RADAR_HISTORY_DB") or DEFAULT_HISTORY_PATH
    )
    service = FastAPI(title="Signal Radar API", version=SERVICE_VERSION)
    cors_origins = [
        item.strip()
        for item in os.getenv(
            "SIGNAL_RADAR_CORS_ORIGINS",
            "http://localhost:4173,http://127.0.0.1:4173",
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
    service.state.active_controls: dict[str, RunControl] = {}

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
            report_value = load_fixture_report(_safe_fixture_path(repository, fixture))
        else:
            report_value = service.state.latest_report or repository.get_report()
        return report_value

    @service.get("/api/sources", response_model=list[SourceStatus])
    def sources() -> list[SourceStatus]:
        report_value = service.state.latest_report or repository.get_report()
        return report_value.sources

    @service.get("/api/runs", response_model=list[Run])
    def runs(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[Run]:
        return history_store.list(limit=limit, offset=offset)

    @service.get("/api/runs/{run_id}", response_model=RunResponse)
    def run_detail(run_id: str) -> RunResponse:
        response = history_store.get(run_id)
        if response is None:
            raise HTTPException(status_code=404, detail="run not found")
        return response

    @service.post("/api/runs/{run_id}/cancel", status_code=202)
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
        name="run_markdown",
    )
    service.add_api_route(
        "/api/runs/{run_id}/report.md",
        markdown_export,
        methods=["GET"],
        response_class=None,
        include_in_schema=False,
        name="run_report_markdown",
    )

    def execute(payload: RunRequest) -> RunResponse:
        run_id = payload.run_id or _run_id()
        if run_id in service.state.active_controls:
            raise HTTPException(status_code=409, detail="run_id is already active")
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
                    budget=budget,
                    cancel_event=control.cancel_event,
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

