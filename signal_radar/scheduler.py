"""标准库实现的本地、只读、有界后台调度器。

调度器不负责采集或写入数据，它只按固定间隔调用应用已经注入的运行
回调。这样 API 可以复用现有 ``execute`` 编排、预算和 ``HistoryStore``，
而不会复制一套容易漂移的运行逻辑。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .models import RunRequest, SchedulerRequest, SchedulerState


RunCallback = Callable[[RunRequest], Any]
CancelCallback = Callable[[str], bool]

_ALLOWED_SOURCES = {
    "github",
    "github_api",
    "github_prs",
    "github_pull_requests",
    "pull_requests",
    "github_discussions",
    "discussions",
    "github_pr_comments",
    "pull_request_comments",
    "pr_comments",
    "rss",
    "feed",
    "official",
    "hackernews",
    "hacker_news",
    "community",
    "browser_use",
    "browser",
    "dynamic",
    "all",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def scheduler_request_from_env() -> SchedulerRequest:
    """读取显式调度配置；未开启时也只返回配置，不启动线程。"""

    source = os.getenv("SIGNAL_RADAR_SCHEDULER_SOURCE", "github").strip().lower() or "github"
    sources = [item.strip().lower() for item in os.getenv("SIGNAL_RADAR_SCHEDULER_SOURCES", source).split(",") if item.strip()]
    feed_urls = [item.strip() for item in os.getenv("SIGNAL_RADAR_SCHEDULER_FEEDS", "").split(",") if item.strip()]
    urls = [item.strip() for item in os.getenv("SIGNAL_RADAR_SCHEDULER_URLS", "").split(",") if item.strip()]
    request = RunRequest(
        mode=os.getenv("SIGNAL_RADAR_SCHEDULER_MODE", "replay").strip().lower() or "replay",
        project=os.getenv("SIGNAL_RADAR_SCHEDULER_PROJECT", "browser-use/browser-use"),
        window_days=_env_int("SIGNAL_RADAR_SCHEDULER_WINDOW_DAYS", 7, 1, 3650),
        limit=_env_int("SIGNAL_RADAR_SCHEDULER_LIMIT", 20, 1, 100),
        sources=sources[:4],
        urls=urls[:20],
        feed_urls=feed_urls[:20],
        community_query=os.getenv("SIGNAL_RADAR_SCHEDULER_COMMUNITY_QUERY") or None,
    )
    return SchedulerRequest(
        request=request,
        interval_seconds=_env_float("SIGNAL_RADAR_SCHEDULER_INTERVAL_SECONDS", 3600.0, 1.0, 86400.0),
        max_runs=_env_int("SIGNAL_RADAR_SCHEDULER_MAX_RUNS", 100, 1, 1000),
        run_immediately=_env_bool("SIGNAL_RADAR_SCHEDULER_RUN_IMMEDIATELY", False),
    )


def validate_scheduler_request(config: SchedulerRequest) -> SchedulerRequest:
    """验证调度请求的边界，避免错误配置造成来源风暴或非只读来源。"""

    request = config.request
    sources = list(dict.fromkeys(str(item).strip().lower().replace("-", "_") for item in request.sources if str(item).strip()))
    if request.source:
        sources.append(str(request.source).strip().lower().replace("-", "_"))
    sources = list(dict.fromkeys(sources or ["github"]))
    unknown = sorted(set(sources) - _ALLOWED_SOURCES)
    if unknown:
        raise ValueError(f"unsupported scheduler source: {', '.join(unknown)}")
    if len(sources) > 4 or len(request.urls) > 20 or len(request.feed_urls) > 20:
        raise ValueError("scheduler source and URL lists are bounded at 4 sources and 20 URLs")
    # Browser Use and all built-in adapters are read-only. The scheduler does
    # not accept arbitrary actions, callbacks, or navigation commands.
    bounded_request = request.model_copy(
        update={
            "sources": sources,
            "urls": list(request.urls),
            "feed_urls": list(request.feed_urls),
            "run_id": None,
        }
    )
    return config.model_copy(update={"request": bounded_request})


class SchedulerAlreadyRunning(RuntimeError):
    """Raised when a second schedule is started without stopping the first."""


class LocalScheduler:
    """Run a bounded ``RunRequest`` sequence on one daemon thread.

    Only one callback is in flight at a time. ``stop`` prevents future ticks and
    asks the optional cancellation callback to stop a currently running live
    request. The scheduler itself never creates write actions or owns secrets.
    """

    def __init__(self, callback: RunCallback, cancel_callback: CancelCallback | None = None) -> None:
        self._callback = callback
        self._cancel_callback = cancel_callback
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._config: SchedulerRequest | None = None
        self._schedule_id: str | None = None
        self._current_run_id: str | None = None
        self._state = SchedulerState()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> SchedulerState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def start(self, config: SchedulerRequest) -> SchedulerState:
        config = validate_scheduler_request(config)
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise SchedulerAlreadyRunning("scheduler is already active")
            self._stop_event = threading.Event()
            self._config = config
            self._schedule_id = f"schedule-{uuid.uuid4().hex[:12]}"
            now = _now()
            self._state = SchedulerState(
                enabled=True,
                status="scheduled",
                schedule_id=self._schedule_id,
                interval_seconds=config.interval_seconds,
                max_runs=config.max_runs,
                run_immediately=config.run_immediately,
                started_at=now,
                next_run_at=now if config.run_immediately else now + timedelta(seconds=config.interval_seconds),
            )
            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"signal-radar-{self._schedule_id}",
                daemon=True,
            )
            self._thread.start()
            return self.snapshot()

    def stop(self, *, cancel_active: bool = True, join_timeout: float = 5.0) -> SchedulerState:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                if self._state.enabled:
                    self._state = self._state.model_copy(
                        update={"enabled": False, "status": "stopped", "stopped_at": _now(), "next_run_at": None}
                    )
                return self.snapshot()
            self._stop_event.set()
            run_id = self._current_run_id
            self._state = self._state.model_copy(
                update={"enabled": False, "status": "stopping", "next_run_at": None, "stopped_at": _now()}
            )
        if cancel_active and run_id and self._cancel_callback is not None:
            try:
                self._cancel_callback(run_id)
            except Exception:
                # Cancellation is best effort; the worker still observes the
                # stop event before scheduling another tick.
                pass
        thread.join(timeout=max(0.0, min(float(join_timeout), 30.0)))
        with self._lock:
            if not thread.is_alive() and self._state.status == "stopping":
                self._state = self._state.model_copy(update={"status": "stopped"})
            return self.snapshot()

    def wait(self, timeout: float | None = None) -> SchedulerState:
        """等待当前调度线程结束，便于 CLI 和离线测试使用。"""

        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout=None if timeout is None else max(0.0, float(timeout)))
        return self.snapshot()

    def _run_loop(self) -> None:
        config = self._config
        schedule_id = self._schedule_id
        if config is None or schedule_id is None:
            return
        if config.run_immediately:
            self._run_once(config, schedule_id)
        while not self._stop_event.is_set():
            with self._lock:
                if self._state.runs_started >= config.max_runs:
                    self._state = self._state.model_copy(
                        update={"enabled": False, "status": "completed", "next_run_at": None, "stopped_at": _now()}
                    )
                    return
                self._state = self._state.model_copy(
                    update={"status": "scheduled", "next_run_at": _now() + timedelta(seconds=config.interval_seconds)}
                )
            if self._stop_event.wait(config.interval_seconds):
                break
            self._run_once(config, schedule_id)
        with self._lock:
            if self._state.status == "stopping" or self._stop_event.is_set():
                self._state = self._state.model_copy(update={"enabled": False, "status": "stopped", "next_run_at": None})

    def _run_once(self, config: SchedulerRequest, schedule_id: str) -> None:
        with self._lock:
            if self._stop_event.is_set() or self._state.runs_started >= config.max_runs:
                return
            run_number = self._state.runs_started + 1
            run_id = f"{schedule_id}-run-{run_number:04d}"
            self._current_run_id = run_id
            self._state = self._state.model_copy(
                update={
                    "status": "running",
                    "runs_started": run_number,
                    "last_run_id": run_id,
                    "last_run_at": _now(),
                    "next_run_at": None,
                    "last_error": None,
                }
            )
        request = config.request.model_copy(update={"run_id": run_id})
        result: Any = None
        error: str | None = None
        try:
            result = self._callback(request)
        except Exception as exc:  # A failed tick must not kill future ticks.
            error = str(exc) or exc.__class__.__name__
        run_status = getattr(getattr(result, "run", result), "status", None) if result is not None else None
        if run_status == "failed" and not error:
            error = getattr(getattr(result, "run", result), "error", None) or "scheduled run failed"
        with self._lock:
            self._current_run_id = None
            completed = self._state.runs_completed + (1 if result is not None and not error else 0)
            self._state = self._state.model_copy(
                update={
                    "runs_completed": completed,
                    "last_run_status": run_status or ("failed" if error else "completed"),
                    "last_error": error,
                    "status": "stopping" if self._stop_event.is_set() else "scheduled",
                }
            )


__all__ = [
    "LocalScheduler",
    "SchedulerAlreadyRunning",
    "scheduler_request_from_env",
    "validate_scheduler_request",
]
