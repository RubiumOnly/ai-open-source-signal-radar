"""离线报告评测。

评测只读取 JSON fixture 和确定性评分器，不访问网络、模型或浏览器。输出的
指标刻意区分 Pydantic 契约是否可解析，以及报告内部的证据和风险信号是否一致。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import Event, Report
from .scoring import _risk_level, score_risk


EVALUATOR_VERSION = "0.2.0"
SUCCESSFUL_SOURCE_STATUSES = {"ok", "replay"}
RISK_TOLERANCE = 0.1


def _pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 100.0
    return round(numerator / denominator * 100.0, 1)


def _error_text(error: Any) -> str:
    """把 Pydantic 错误压缩为稳定、可读且可序列化的文本。"""

    location = ".".join(str(part) for part in error.get("loc", ())) or "$"
    message = str(error.get("msg", "invalid value"))
    return f"{location}: {message}"


def _citation_metrics(report: Report) -> dict[str, Any]:
    evidence_ids = {item.id for item in report.evidence}

    def supported(references: list[str]) -> bool:
        return bool(references) and any(reference in evidence_ids for reference in references)

    supported_claims = sum(supported(claim.evidence_ids) for claim in report.claims)
    supported_events = sum(supported(event.evidence_ids) for event in report.events)
    claim_references = {reference for claim in report.claims for reference in claim.evidence_ids}
    event_references = {reference for event in report.events for reference in event.evidence_ids}
    orphan_references = sorted((claim_references | event_references) - evidence_ids)
    total_items = len(report.claims) + len(report.events)
    supported_items = supported_claims + supported_events
    return {
        "claims": {
            "supported": supported_claims,
            "total": len(report.claims),
            "pct": _pct(supported_claims, len(report.claims)),
        },
        "events": {
            "supported": supported_events,
            "total": len(report.events),
            "pct": _pct(supported_events, len(report.events)),
        },
        "overall_pct": _pct(supported_items, total_items),
        "evidence_count": len(report.evidence),
        "orphan_reference_count": len(orphan_references),
        "orphan_reference_ids": orphan_references,
    }


def _source_metrics(report: Report) -> dict[str, Any]:
    statuses = report.sources
    successful = [status for status in statuses if status.status in SUCCESSFUL_SOURCE_STATUSES]
    evidence_sources = {item.source for item in report.evidence if item.source}
    configured_sources = {status.source for status in statuses if status.source}
    represented_sources = configured_sources & evidence_sources
    available_pct = _pct(len(successful), len(statuses)) if statuses else 0.0
    evidence_pct = _pct(len(represented_sources), len(configured_sources)) if configured_sources else 0.0
    return {
        "configured": len(configured_sources),
        "successful": len(successful),
        "available_pct": available_pct,
        "with_evidence": len(represented_sources),
        "evidence_pct": evidence_pct,
        "failed_sources": sorted(
            status.source
            for status in statuses
            if status.status not in SUCCESSFUL_SOURCE_STATUSES
        ),
    }


def _event_metrics(report: Report) -> dict[str, Any]:
    expected_levels: dict[str, str] = {}
    level_mismatches: list[dict[str, Any]] = []
    for event in report.events:
        expected = _risk_level(event.risk_score)
        expected_levels[event.id] = expected
        if event.risk_level != expected:
            level_mismatches.append(
                {
                    "id": event.id,
                    "actual": event.risk_level,
                    "expected": expected,
                    "risk_score": event.risk_score,
                }
            )

    recomputed_score = score_risk(report.events, report.claims, report.sources)
    summary_score = report.summary.risk_score
    summary_level_expected = _risk_level(summary_score)
    score_matches = math.isclose(summary_score, recomputed_score, abs_tol=RISK_TOLERANCE)
    summary_count_matches = report.summary.events_count == len(report.events)
    return {
        "summary_count": report.summary.events_count,
        "actual_count": len(report.events),
        "summary_count_matches": summary_count_matches,
        "event_risk_levels_checked": len(report.events),
        "event_risk_level_mismatches": level_mismatches,
        "event_risk_levels_consistent": not level_mismatches,
        "summary_risk_score": summary_score,
        "recomputed_risk_score": recomputed_score,
        "summary_risk_score_matches": score_matches,
        "summary_risk_level": report.summary.risk_level,
        "expected_summary_risk_level": summary_level_expected,
        "summary_risk_level_matches": report.summary.risk_level == summary_level_expected,
    }


def _security_metrics(report: Report) -> dict[str, Any]:
    """检查报告是否保留了浏览器只读边界和元数据证据边界。

    采集器可以通过 ``extra`` 字段回传运行时安全计数，旧 fixture 没有该
    字段时按零处理。元数据证据必须没有正文摘录，避免将页面中的提示词或
    模型猜测冒充为可引用原文。
    """

    def _extra_int(value: Any, key: str) -> int:
        extra = getattr(value, "model_extra", None) or {}
        raw = extra.get(key, 0)
        if isinstance(raw, bool):
            return int(raw)
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            # 安全计数格式异常时保守地视为一次未授权目标，不能静默放行。
            return 1

    unauthorized_action_target = _extra_int(report, "unauthorized_action_target")
    unauthorized_action_target += sum(
        _extra_int(status, "unauthorized_action_target") for status in report.sources
    )
    metadata_only = [item for item in report.evidence if item.evidence_level == "metadata_only"]
    metadata_only_quotes_empty = all(not item.quote.strip() for item in metadata_only)
    return {
        "unauthorized_action_target": unauthorized_action_target,
        "metadata_only_evidence_count": len(metadata_only),
        "metadata_only_quotes_empty": metadata_only_quotes_empty,
    }


def evaluate_report(report: Report, *, fixture: str | None = None) -> dict[str, Any]:
    """评测一个已经通过 ``Report`` 契约校验的报告。"""

    citations = _citation_metrics(report)
    sources = _source_metrics(report)
    events = _event_metrics(report)
    security = _security_metrics(report)
    checks = {
        "schema_valid": True,
        "citations_present": citations["overall_pct"] == 100.0,
        "sources_available": sources["available_pct"] == 100.0,
        "event_count_consistent": events["summary_count_matches"],
        "risk_consistent": (
            events["event_risk_levels_consistent"]
            and events["summary_risk_score_matches"]
            and events["summary_risk_level_matches"]
        ),
        "unauthorized_action_target_zero": security["unauthorized_action_target"] == 0,
        "metadata_only_quotes_empty": security["metadata_only_quotes_empty"],
    }
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "fixture": fixture,
        "schema_valid": True,
        "schema_errors": [],
        "counts": {
            "articles": len(report.articles),
            "claims": len(report.claims),
            "events": len(report.events),
            "evidence": len(report.evidence),
            "sources": len(report.sources),
        },
        "citation_coverage": citations,
        "source_coverage": sources,
        "event_consistency": events,
        "security": security,
        "checks": checks,
        "passed": all(checks.values()),
    }


def evaluate_fixture(path: str | Path) -> dict[str, Any]:
    """读取并评测 fixture；输入错误也以 JSON 结果返回，不隐式回退到 demo。"""

    fixture_path = Path(path)
    fixture_label = str(path)
    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("report"), dict):
            payload = payload["report"]
        report = Report.model_validate(payload)
    except FileNotFoundError:
        return _invalid_result(fixture_label, [f"fixture not found: {fixture_label}"])
    except OSError as exc:
        return _invalid_result(fixture_label, [f"fixture read failed: {exc}"])
    except json.JSONDecodeError as exc:
        return _invalid_result(fixture_label, [f"invalid JSON at line {exc.lineno}, column {exc.colno}"])
    except ValidationError as exc:
        return _invalid_result(fixture_label, [_error_text(error) for error in exc.errors()])
    return evaluate_report(report, fixture=fixture_label)


def _invalid_result(fixture: str, errors: list[str]) -> dict[str, Any]:
    checks = {"schema_valid": False}
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "fixture": fixture,
        "schema_valid": False,
        "schema_errors": errors,
        "counts": {"articles": 0, "claims": 0, "events": 0, "evidence": 0, "sources": 0},
        "citation_coverage": None,
        "source_coverage": None,
        "event_consistency": None,
        "checks": checks,
        "passed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a Signal Radar report fixture")
    parser.add_argument("--fixture", required=True, type=Path, help="JSON report fixture")
    parser.add_argument("--output", type=Path, help="Write evaluation JSON to a file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = evaluate_fixture(args.fixture)
    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0 if result["schema_valid"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
