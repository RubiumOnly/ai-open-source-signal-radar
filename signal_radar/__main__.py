"""README 和 Docker 冒烟检查使用的命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from .api import _live_report
from .models import RunRequest
from .repository import load_fixture_report
from .sources import BrowserUseSourceAdapter, GitHubSourceAdapter, HackerNewsSourceAdapter, RSSSourceAdapter


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
        choices=("github", "rss", "official", "hackernews", "community", "browser_use", "all"),
        default="github",
    )
    parser.add_argument("--community-query", help="Hacker News query; defaults to the project name")
    parser.add_argument("--url", action="append", default=[], help="Dynamic URL; may be repeated")
    parser.add_argument("--feed-url", action="append", default=[], help="RSS/Atom feed URL; may be repeated")
    parser.add_argument("--enable-browser-use", action="store_true", help="Allow the Browser Use adapter")
    parser.add_argument("--browser-run-live", action="store_true", help="Actually run Browser Use after enabling it")
    parser.add_argument("--output", type=Path, help="Write JSON to a file instead of stdout")
    return parser


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.days < 1 or args.limit < 1:
        raise SystemExit("--days and --limit must be positive")
    if args.max_steps is not None and not 1 <= args.max_steps <= 40:
        raise SystemExit("--max-steps must be between 1 and 40")
    if args.timeout_seconds is not None and not 0 < args.timeout_seconds <= 900:
        raise SystemExit("--timeout-seconds must be between 0 and 900")
    run_id = "cli-replay" if args.mode == "replay" else "cli-live"
    if args.mode == "replay":
        report = load_fixture_report(args.fixture)
        report = report.model_copy(update={"run_id": run_id, "window_days": args.days})
    else:
        sources = ["github", "rss", "hackernews", "browser_use"] if args.source == "all" else [args.source]
        request = RunRequest(
            mode="live",
            project=args.project,
            window_days=args.days,
            limit=args.limit,
            sources=sources,
            urls=args.url,
            feed_urls=args.feed_url,
            community_query=args.community_query,
            max_steps=args.max_steps,
            timeout_seconds=args.timeout_seconds,
        )
        report, _ = _live_report(
            request,
            run_id=run_id,
            github=GitHubSourceAdapter(),
            browser=_browser_adapter(args),
            rss=RSSSourceAdapter(
                feed_urls=tuple(
                    item.strip()
                    for item in os.getenv("SIGNAL_RADAR_RSS_FEEDS", "").split(",")
                    if item.strip()
                )
            ),
            hackernews=HackerNewsSourceAdapter(
                timeout=float(_env_int("SIGNAL_RADAR_HACKERNEWS_TIMEOUT", 8, 1, 60)),
                max_limit=_env_int("SIGNAL_RADAR_HACKERNEWS_MAX_LIMIT", 50, 1, 100),
            ),
        )
    output = report.model_dump_json(indent=2, exclude_none=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

