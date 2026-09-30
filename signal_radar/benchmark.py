"""离线评测基准运行器。

它在固定 JSON fixture 上重复调用现有评测 harness，汇总通过率和耗时，
用于比较来源/报告契约变化。整个过程不访问网络、模型或浏览器。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Iterable

from .evaluate import EVALUATOR_VERSION, evaluate_fixture


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES = (
    PROJECT_ROOT / "fixtures" / "prompt_injection_browser_use.json",
    PROJECT_ROOT / "fixtures" / "demo_report.json",
)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * percentile) - 1))
    return round(ordered[index], 3)


def run_benchmark(fixtures: Iterable[str | Path]) -> dict[str, object]:
    """评测一组 fixture 并返回稳定、可序列化的汇总结果。"""

    cases: list[dict[str, object]] = []
    durations: list[float] = []
    for raw_path in fixtures:
        path = Path(raw_path)
        started = time.perf_counter()
        result = evaluate_fixture(path)
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        durations.append(duration_ms)
        cases.append({
            "fixture": str(path),
            "duration_ms": duration_ms,
            "schema_valid": result.get("schema_valid", False),
            "passed": result.get("passed", False),
            "checks": result.get("checks") or {},
            "schema_errors": result.get("schema_errors") or [],
        })
    passed = sum(1 for case in cases if case["passed"] is True)
    return {
        "benchmark_version": "0.1.0",
        "evaluator_version": EVALUATOR_VERSION,
        "case_count": len(cases),
        "passed_count": passed,
        "pass_rate_pct": round(passed / len(cases) * 100.0, 1) if cases else 0.0,
        "duration_ms": {
            "min": round(min(durations), 3) if durations else None,
            "avg": round(sum(durations) / len(durations), 3) if durations else None,
            "p95": _percentile(durations, 0.95),
            "max": round(max(durations), 3) if durations else None,
        },
        "failed_fixtures": [case["fixture"] for case in cases if case["passed"] is not True],
        "cases": cases,
        "passed": bool(cases) and passed == len(cases),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Signal Radar offline fixture benchmarks")
    parser.add_argument("--fixture", action="append", type=Path, help="Fixture JSON; may be repeated")
    parser.add_argument("--output", type=Path, help="Write benchmark JSON to a file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    fixtures = args.fixture or list(DEFAULT_FIXTURES)
    result = run_benchmark(fixtures)
    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0 if result["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

