"""README 和 Docker 冒烟检查使用的命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from .api import _live_report
from .cache import DEFAULT_CACHE_PATH, SourceCache
from .history import HistoryStore
from .models import Run, RunRequest, RunResponse, SchedulerRequest
from .repository import load_fixture_report
from .scheduler import LocalScheduler, scheduler_request_from_env
from .sources import BrowserUseSourceAdapter, GitHubSourceAdapter, HackerNewsSourceAdapter, RedditSourceAdapter, RSSSourceAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Signal Radar offline replay/live collector")
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--fixture", type=Path, help="JSON report fixture for replay mode")
    parser.add_argument("--project", default="browser-use/browser-use", help="GitHub owner/name")
    parser.add_argument("--days", type=int, default=7, help="Analysis window in days")
    parser.add_argument("--limit", type=int, default=20, help="Maximum records per source")
    parser.add_argument("--max-steps", type=int, default=None, help="Browser Use step budget (1-40)")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Live run timeout budget")
    parser.add_argument(
        "--source",
        choices=("github", "rss", "official", "hackernews", "reddit", "community", "browser_use", "all"),
        default="github",
    )
    parser.add_argument("--community-query", help="Hacker News query; defaults to the project name")
    parser.add_argument("--reddit-query", help="Reddit query; defaults to the project name")
    parser.add_argument("--url", action="append", default=[], help="Dynamic URL; may be repeated")
    parser.add_argument("--feed-url", action="append", default=[], help="RSS/Atom feed URL; may be repeated")
    parser.add_argument("--enable-browser-use", action="store_true", help="Allow the Browser Use adapter")
    parser.add_argument("--browser-run-live", action="store_true", help="Actually run Browser Use after enabling it")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run the same read-only collection on an explicit bounded interval",
    )
    parser.add_argument(
        "--schedule-interval",
        type=float,
        default=None,
        help="Schedule interval in seconds (1-86400); requires --schedule",
    )
    parser.add_argument(
        "--schedule-max-runs",
        type=int,
        default=None,
        help="Maximum scheduled runs (1-1000); requires --schedule",
    )
    parser.add_argument(
        "--schedule-run-immediately",
        action="store_true",
        help="Run once immediately instead of waiting for the first interval",
    )
    parser.add_argument("--output", type=Path, help="Write JSON to a file instead of stdout")
    return parser


def _env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _browser_adapter(args: argparse.Namespace) -> BrowserUseSourceAdapter:
    domains = tuple(
        item.strip()
        for item in os.getenv("SIGNAL_RADAR_BROWSER_ALLOWED_DOMAINS", "").split(",")
        if item.strip()
    )
    return BrowserUseSourceAdapter(
        enabled=args.enable_browser_use or _env_flag("SIGNAL_RADAR_BROWSER_ENABLED"),
        run_live=args.browser_run_live or _env_flag("SIGNAL_RADAR_BROWSER_RUN_LIVE"),
        allowed_domains=domains,
        max_steps=_env_int("SIGNAL_RADAR_BROWSER_MAX_STEPS", 12, 1, 40),
        timeout_seconds=_env_int("SIGNAL_RADAR_BROWSER_TIMEOUT_SECONDS", 180, 1, 900),
    )


def _request_from_args(args: argparse.Namespace, *, run_id: str | None = None) -> RunRequest:
    """Build one bounded request shared by one-shot and scheduled CLI modes."""

    sources = ["github", "rss", "hackernews", "browser_use"] if args.source == "all" else [args.source]
    return RunRequest(
        mode=args.mode,
        project=args.project,
        window_days=args.days,
        limit=args.limit,
        sources=sources,
        urls=args.url,
        feed_urls=args.feed_url,
        community_query=args.community_query,
        max_steps=args.max_steps,
        timeout_seconds=args.timeout_seconds,
        run_id=run_id,
    )


def _scheduled_cli(args: argparse.Namespace) -> int:
    """Run a bounded local schedule and persist every tick in SQLite history."""

    env_config = scheduler_request_from_env()
    request = _request_from_args(args)
    config = SchedulerRequest(
        request=request,
        interval_seconds=args.schedule_interval if args.schedule_interval is not None else env_config.interval_seconds,
        max_runs=args.schedule_max_runs if args.schedule_max_runs is not None else env_config.max_runs,
        # CLI scheduling is opt-in, but the first tick is opt-in as well. This
        # keeps a copied command from unexpectedly hitting a live source.
        run_immediately=args.schedule_run_immediately,
    )
    history = HistoryStore(os.getenv("SIGNAL_RADAR_HISTORY_DB") or "data/runs.sqlite3")
    cache = SourceCache(os.getenv("SIGNAL_RADAR_CACHE_DB") or DEFAULT_CACHE_PATH) if _env_flag("SIGNAL_RADAR_CACHE_ENABLED", True) else None
    github = GitHubSourceAdapter(cache=cache, max_pages=_env_int("SIGNAL_RADAR_GITHUB_MAX_PAGES", 4, 1, 20))
    browser = _browser_adapter(args)
    rss = RSSSourceAdapter(
        feed_urls=tuple(item.strip() for item in os.getenv("SIGNAL_RADAR_RSS_FEEDS", "").split(",") if item.strip()),
        cache=cache,
    )
    hackernews = HackerNewsSourceAdapter(
        timeout=float(_env_int("SIGNAL_RADAR_HACKERNEWS_TIMEOUT", 8, 1, 60)),
        max_limit=_env_int("SIGNAL_RADAR_HACKERNEWS_MAX_LIMIT", 50, 1, 100),
        max_pages=_env_int("SIGNAL_RADAR_HACKERNEWS_MAX_PAGES", 4, 1, 20),
        cache=cache,
    )
    reddit = RedditSourceAdapter(
        timeout=float(_env_int("SIGNAL_RADAR_REDDIT_TIMEOUT", 8, 1, 60)),
        max_limit=_env_int("SIGNAL_RADAR_REDDIT_MAX_LIMIT", 50, 1, 100),
        max_pages=_env_int("SIGNAL_RADAR_REDDIT_MAX_PAGES", 4, 1, 20),
        cache=cache,
    )

    def execute(payload: RunRequest) -> RunResponse:
        run_id = payload.run_id or "cli-scheduled"
        started = datetime.now(timezone.utc)
        try:
            if payload.mode == "replay":
                report = load_fixture_report(args.fixture).model_copy(
                    update={"run_id": run_id, "window_days": payload.window_days}
                )
                statuses = report.sources
                status = "completed"
                error = None
            else:
                report, statuses = _live_report(
                    payload,
                    run_id=run_id,
                    github=github,
                    browser=browser,
                    rss=rss,
                    hackernews=hackernews,
                    reddit=reddit,
                )
                status = "completed" if all(item.status in {"ok", "replay"} for item in statuses) else "partial"
                error = None
            run = Run(
                run_id=run_id,
                mode=payload.mode,
                status=status,
                subject=payload.project or payload.subject,
                window_days=payload.window_days,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                report_id=report.report_id,
                source_statuses=statuses,
                error=error,
            )
            history.save(run, report)
            return RunResponse(run=run, report=report)
        except Exception as exc:
            run = Run(
                run_id=run_id,
                mode=payload.mode,
                status="failed",
                subject=payload.project or payload.subject,
                window_days=payload.window_days,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                error=str(exc),
            )
            history.save(run)
            return RunResponse(run=run, report=None)

    scheduler = LocalScheduler(execute)
    try:
        scheduler.start(config)
        state = scheduler.wait()
    except KeyboardInterrupt:
        state = scheduler.stop()
    finally:
        history.close()
        if cache is not None:
            cache.close()
    output = state.model_dump_json(indent=2, exclude_none=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        sys.stdout.write(output + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.days < 1 or args.limit < 1:
        raise SystemExit("--days and --limit must be positive")
    if args.max_steps is not None and not 1 <= args.max_steps <= 40:
        raise SystemExit("--max-steps must be between 1 and 40")
    if args.timeout_seconds is not None and not 0 < args.timeout_seconds <= 900:
        raise SystemExit("--timeout-seconds must be between 0 and 900")
    if args.schedule_interval is not None and not 1 <= args.schedule_interval <= 86400:
        raise SystemExit("--schedule-interval must be between 1 and 86400")
    if args.schedule_max_runs is not None and not 1 <= args.schedule_max_runs <= 1000:
        raise SystemExit("--schedule-max-runs must be between 1 and 1000")
    if (args.schedule_interval is not None or args.schedule_max_runs is not None or args.schedule_run_immediately) and not args.schedule:
        raise SystemExit("schedule options require --schedule")
    if args.schedule:
        return _scheduled_cli(args)
    run_id = "cli-replay" if args.mode == "replay" else "cli-live"
    if args.mode == "replay":
        report = load_fixture_report(args.fixture)
        report = report.model_copy(update={"run_id": run_id, "window_days": args.days})
    else:
        sources = ["github", "rss", "hackernews", "reddit", "browser_use"] if args.source == "all" else [args.source]
        request = RunRequest(
            mode="live",
            project=args.project,
            window_days=args.days,
            limit=args.limit,
            sources=sources,
            urls=args.url,
            feed_urls=args.feed_url,
            community_query=args.community_query,
            reddit_query=args.reddit_query,
            max_steps=args.max_steps,
            timeout_seconds=args.timeout_seconds,
        )
        cache = SourceCache(os.getenv("SIGNAL_RADAR_CACHE_DB") or DEFAULT_CACHE_PATH) if _env_flag("SIGNAL_RADAR_CACHE_ENABLED", True) else None
        try:
            report, _ = _live_report(
                request,
                run_id=run_id,
                github=GitHubSourceAdapter(
                    cache=cache,
                    max_pages=_env_int("SIGNAL_RADAR_GITHUB_MAX_PAGES", 4, 1, 20),
                ),
                browser=_browser_adapter(args),
                rss=RSSSourceAdapter(
                    feed_urls=tuple(
                        item.strip()
                        for item in os.getenv("SIGNAL_RADAR_RSS_FEEDS", "").split(",")
                        if item.strip()
                    ),
                    cache=cache,
                ),
                hackernews=HackerNewsSourceAdapter(
                    timeout=float(_env_int("SIGNAL_RADAR_HACKERNEWS_TIMEOUT", 8, 1, 60)),
                    max_limit=_env_int("SIGNAL_RADAR_HACKERNEWS_MAX_LIMIT", 50, 1, 100),
                    max_pages=_env_int("SIGNAL_RADAR_HACKERNEWS_MAX_PAGES", 4, 1, 20),
                    cache=cache,
                ),
                reddit=RedditSourceAdapter(
                    timeout=float(_env_int("SIGNAL_RADAR_REDDIT_TIMEOUT", 8, 1, 60)),
                    max_limit=_env_int("SIGNAL_RADAR_REDDIT_MAX_LIMIT", 50, 1, 100),
                    max_pages=_env_int("SIGNAL_RADAR_REDDIT_MAX_PAGES", 4, 1, 20),
                    cache=cache,
                ),
            )
        finally:
            if cache is not None:
                cache.close()
    output = report.model_dump_json(indent=2, exclude_none=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

