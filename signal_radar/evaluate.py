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

from .models import Annotation, Event, Report
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


def _performance_metrics(report: Report) -> dict[str, Any]:
    """汇总来源延迟、分页和增量缓存指标。

    旧 fixture 没有这些字段时返回 ``observed_sources=0``，不会把历史报告
    判为失败；新运行若显式提供候选数，则校验新增与重复记录是否闭合。
    """

    statuses = report.sources
    observed = [status for status in statuses if status.latency_ms is not None]
    latencies = sorted(float(status.latency_ms) for status in observed if status.latency_ms is not None)
    if latencies:
        p95_index = min(len(latencies) - 1, max(0, math.ceil(len(latencies) * 0.95) - 1))
        latency = {
            "min_ms": round(latencies[0], 1),
            "avg_ms": round(sum(latencies) / len(latencies), 1),
            "p95_ms": round(latencies[p95_index], 1),
            "max_ms": round(latencies[-1], 1),
        }
    else:
        latency = {"min_ms": None, "avg_ms": None, "p95_ms": None, "max_ms": None}
    cache_observed = [status for status in statuses if status.cache_hit or status.total_candidates > 0]
    candidates = sum(status.total_candidates for status in statuses)
    new_records = sum(status.new_records for status in statuses)
    duplicate_records = sum(status.duplicate_records for status in statuses)
    metric_mismatches = [
        status.source
        for status in statuses
        if status.total_candidates > 0
        and status.new_records + status.duplicate_records > status.total_candidates
    ]
    return {
        "observed_sources": len(observed),
        "latency_ms": latency,
        "pages": sum(status.pages for status in statuses),
        "cache_hit_pct": _pct(sum(1 for status in cache_observed if status.cache_hit), len(cache_observed)) if cache_observed else None,
        "new_records": new_records,
        "duplicate_records": duplicate_records,
        "total_candidates": candidates,
        "duplicate_rate_pct": _pct(duplicate_records, candidates) if candidates else None,
        "metric_mismatches": sorted(metric_mismatches),
        "metrics_consistent": not metric_mismatches,
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


def _annotation_metrics(report: Report, annotations: list[Annotation]) -> dict[str, Any]:
    """Check that human labels point at real report items.

    This is intentionally a consistency/coverage metric rather than an
    accuracy claim: correctness can only be measured after a separate gold
    set is supplied.  Exported annotations are nevertheless immediately
    consumable by this harness and expose orphan labels instead of silently
    dropping them.
    """

    targets = {
        "evidence": {item.id for item in report.evidence},
        "claim": {item.id for item in report.claims},
        "event": {item.id for item in report.events},
    }
    orphaned = [
        {
            "id": item.id,
            "target_type": item.target_type,
            "target_id": item.target_id,
        }
        for item in annotations
        if item.target_id not in targets[item.target_type]
    ]
    by_label: dict[str, int] = {}
    by_value: dict[str, int] = {}
    for item in annotations:
        by_label[item.label] = by_label.get(item.label, 0) + 1
        key = f"{item.label}:{item.value}"
        by_value[key] = by_value.get(key, 0) + 1
    valid_count = len(annotations) - len(orphaned)
    return {
        "count": len(annotations),
        "valid_target_count": valid_count,
        "orphan_count": len(orphaned),
        "target_coverage_pct": _pct(valid_count, len(annotations)),
        "by_label": dict(sorted(by_label.items())),
        "by_value": dict(sorted(by_value.items())),
        "orphaned": orphaned,
    }


def evaluate_report(
    report: Report,
    *,
    fixture: str | None = None,
    annotations: list[Annotation] | None = None,
    annotation_fixture: str | None = None,
) -> dict[str, Any]:
    """评测一个已经通过 ``Report`` 契约校验的报告。"""

    citations = _citation_metrics(report)
    sources = _source_metrics(report)
    events = _event_metrics(report)
    performance = _performance_metrics(report)
    security = _security_metrics(report)
    annotation_metrics = _annotation_metrics(report, annotations or [])
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
        "annotations_reference_report": annotation_metrics["orphan_count"] == 0,
        "source_metrics_consistent": performance["metrics_consistent"],
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
        "performance": performance,
        "security": security,
        "annotations": annotation_metrics,
        "annotation_fixture": annotation_fixture,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _load_annotations(path: str | Path) -> tuple[list[Annotation], list[str]]:
    """Load exported annotation JSON and return validation errors explicitly."""

    fixture_label = str(path)
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("annotations", payload)
        if not isinstance(payload, list):
            raise ValueError("annotation fixture must be a JSON array or an object with annotations")
        annotations: list[Annotation] = []
        errors: list[str] = []
        for index, item in enumerate(payload):
            try:
                annotations.append(Annotation.model_validate(item))
            except ValidationError as exc:
                errors.extend([f"annotations[{index}].{message}" for message in (_error_text(error) for error in exc.errors())])
        return annotations, errors
    except FileNotFoundError:
        return [], [f"annotation fixture not found: {fixture_label}"]
    except OSError as exc:
        return [], [f"annotation fixture read failed: {exc}"]
    except json.JSONDecodeError as exc:
        return [], [f"invalid annotation JSON at line {exc.lineno}, column {exc.colno}"]
    except ValueError as exc:
        return [], [str(exc)]


def evaluate_fixture(path: str | Path, *, annotations_path: str | Path | None = None) -> dict[str, Any]:
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
    annotations: list[Annotation] = []
    annotation_errors: list[str] = []
    if annotations_path is not None:
        annotations, annotation_errors = _load_annotations(annotations_path)
    result = evaluate_report(
        report,
        fixture=fixture_label,
        annotations=annotations,
        annotation_fixture=str(annotations_path) if annotations_path is not None else None,
    )
    result["annotation_schema_valid"] = not annotation_errors
    result["annotation_schema_errors"] = annotation_errors
    result["checks"]["annotation_schema_valid"] = not annotation_errors
    result["passed"] = all(result["checks"].values())
    return result


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
        "performance": None,
        "annotations": None,
        "annotation_fixture": None,
        "annotation_schema_valid": False,
        "annotation_schema_errors": [],
        "checks": checks,
        "passed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a Signal Radar report fixture")
    parser.add_argument("--fixture", required=True, type=Path, help="JSON report fixture")
    parser.add_argument("--output", type=Path, help="Write evaluation JSON to a file")
    parser.add_argument(
        "--annotations",
        type=Path,
        help="Optional exported annotation JSON from /api/annotations/export.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = evaluate_fixture(args.fixture, annotations_path=args.annotations)
    output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0 if result["schema_valid"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
