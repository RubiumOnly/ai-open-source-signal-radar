"""README 和 Docker 冒烟检查使用的命令行入口。"""

from __future__ import annotations

import argparse
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
from .sources import BrowserUseSourceAdapter, GitHubSourceAdapter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Signal Radar offline replay/live collector")
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--fixture", type=Path, help="JSON report fixture for replay mode")
    parser.add_argument("--project", default="browser-use/browser-use", help="GitHub owner/name")
    parser.add_argument("--days", type=int, default=7, help="Analysis window in days")
    parser.add_argument("--limit", type=int, default=20, help="Maximum records per source")
    parser.add_argument("--output", type=Path, help="Write JSON to a file instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.days < 1 or args.limit < 1:
        raise SystemExit("--days and --limit must be positive")
    run_id = "cli-replay" if args.mode == "replay" else "cli-live"
    if args.mode == "replay":
        report = load_fixture_report(args.fixture)
        report = report.model_copy(update={"run_id": run_id, "window_days": args.days})
    else:
        request = RunRequest(mode="live", project=args.project, window_days=args.days, limit=args.limit)
        report, _ = _live_report(
            request,
            run_id=run_id,
            github=GitHubSourceAdapter(),
            browser=BrowserUseSourceAdapter(),
        )
    output = report.model_dump_json(indent=2, exclude_none=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

