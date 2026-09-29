"""确定性的聚合与风险评分。

评分器刻意不调用 LLM，使回放结果稳定，也便于测试、审计和复现。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from hashlib import sha1
from typing import Iterable

from .models import (
    AccessStatusRecord,
    Article,
    Claim,
    Event,
    Evidence,
    ProjectInfo,
    Report,
    ReportSummary,
    SourceStatus,
    Topic,
    TrendPoint,
    utc_now,
)


RISK_WEIGHTS = {"low": 15.0, "medium": 40.0, "high": 70.0, "critical": 95.0}


def _risk_level(score: float) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def _date(value: datetime | None) -> str:
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.date().isoformat()


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{sha1(value.encode('utf-8')).hexdigest()[:10]}"


def score_risk(
    events: Iterable[Event] = (),
    claims: Iterable[Claim] = (),
    source_statuses: Iterable[SourceStatus] = (),
) -> float:
    """根据明确且可复现的信号计算有界风险分数。"""

    events = list(events)
    claims = list(claims)
    source_statuses = list(source_statuses)
    if events:
        event_score = sum(
            max(0.0, min(100.0, event.risk_score or RISK_WEIGHTS[event.risk_level]))
            for event in events
        ) / len(events)
    else:
        event_score = 0.0

    adverse = sum(1 for claim in claims if claim.stance in {"oppose", "against"} or claim.sentiment == "negative")
    positive = sum(
        1 for claim in claims if claim.stance in {"support", "supportive"} or claim.sentiment == "positive"
    )
    claim_score = min(100.0, adverse * 8.0 - positive * 2.0)
    source_penalty = sum(
        8.0
        for status in source_statuses
        if status.status in {"partial", "error", "blocked", "rate_limited", "auth_required", "cancelled"}
    )
    return round(max(0.0, min(100.0, event_score * 0.72 + claim_score * 0.2 + source_penalty)), 1)


def _build_topics(claims: list[Claim], events: list[Event]) -> list[Topic]:
    buckets: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        names = claim.topics or ["general feedback"]
        for name in names:
            buckets[name.strip().lower()].append(claim)
    for event in events:
        if event.category and not any(event.id in c.article_ids for c in claims):
            buckets[event.category.strip().lower()].extend([])

    topics: list[Topic] = []
    for name, bucket in buckets.items():
        negative = sum(1 for c in bucket if c.sentiment == "negative" or c.stance in {"oppose", "against"})
        positive = sum(1 for c in bucket if c.sentiment == "positive" or c.stance in {"support", "supportive"})
        sentiment = "negative" if negative > positive else "positive" if positive > negative else "neutral"
        topics.append(
            Topic(
                name=name,
                count=len(bucket),
                sentiment=sentiment,
                risk_score=round(min(100.0, negative / max(1, len(bucket)) * 100.0), 1),
                sample_claims=[claim.text for claim in bucket[:3]],
            )
        )
    topics.sort(key=lambda item: (-item.count, item.name))
    return topics


def _build_trends(articles: list[Article], claims: list[Claim], events: list[Event]) -> list[TrendPoint]:
    claim_by_article = {article_id: claim for claim in claims for article_id in claim.article_ids}
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: Counter())
    for article in articles:
        day = _date(article.published_at or article.retrieved_at)
        grouped[day]["mentions"] += 1
        claim = claim_by_article.get(article.id)
        sentiment = claim.sentiment if claim else "neutral"
        grouped[day][sentiment] += 1
    for event in events:
        day = _date(event.occurred_at)
        grouped[day]["risk_total"] += int(event.risk_score)
        grouped[day]["risk_count"] += 1
    result: list[TrendPoint] = []
    for day in sorted(grouped):
        bucket = grouped[day]
        result.append(
            TrendPoint(
                date=day,
                mentions=bucket["mentions"],
                positive=bucket["positive"],
                negative=bucket["negative"],
                neutral=bucket["neutral"],
                risk_score=round(bucket["risk_total"] / max(1, bucket["risk_count"]), 1),
            )
        )
    return result


def aggregate_report(
    *,
    project: ProjectInfo | None = None,
    articles: Iterable[Article] = (),
    claims: Iterable[Claim] = (),
    events: Iterable[Event] = (),
    evidence: Iterable[Evidence] = (),
    source_statuses: Iterable[SourceStatus] = (),
    access_status: Iterable[AccessStatusRecord] = (),
    window_days: int = 7,
    run_id: str | None = None,
    report_id: str | None = None,
    generated_at: datetime | None = None,
) -> Report:
    """按稳定顺序汇总采集器输出为报告。"""

    articles = sorted(list(articles), key=lambda item: (item.published_at or item.retrieved_at, item.id), reverse=True)
    claims = sorted(list(claims), key=lambda item: item.id)
    events = sorted(list(events), key=lambda item: (-(item.risk_score or 0.0), item.id))
    evidence = sorted(list(evidence), key=lambda item: item.id)
    source_statuses = sorted(list(source_statuses), key=lambda item: item.source)
    access_status = sorted(list(access_status), key=lambda item: item.source)

    score = score_risk(events, claims, source_statuses)
    ok_sources = sum(1 for status in source_statuses if status.status in {"ok", "replay"})
    source_count = len({status.source for status in source_statuses})
    coverage = (ok_sources / source_count * 100.0) if source_count else (100.0 if articles else 0.0)
    summary = ReportSummary(
        risk_score=score,
        risk_level=_risk_level(score),
        events_count=len(events),
        source_count=source_count,
        coverage_pct=round(coverage, 1),
        positive_count=sum(1 for claim in claims if claim.sentiment == "positive"),
        negative_count=sum(1 for claim in claims if claim.sentiment == "negative"),
        neutral_count=sum(1 for claim in claims if claim.sentiment in {"neutral", "unknown"}),
        unresolved_count=sum(1 for claim in claims if not claim.evidence_ids),
    )
    return Report(
        report_id=report_id or _stable_id("report", f"{run_id or 'replay'}:{project and project.repository}"),
        run_id=run_id,
        generated_at=generated_at or utc_now(),
        project=project or ProjectInfo(),
        window_days=window_days,
        summary=summary,
        trends=_build_trends(articles, claims, events),
        topics=_build_topics(claims, events),
        evidence=evidence,
        sources=source_statuses,
        access_status=access_status,
        articles=articles,
        claims=claims,
        events=events,
    )

