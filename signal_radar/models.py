"""Signal Radar 服务共用的数据契约。

模型保持稳定且精简的接口；同时允许额外字段，使新采集器生成的回放
fixture 仍可由旧版本 API 提供服务。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """返回用于模型默认值的带时区时间戳。"""

    return datetime.now(timezone.utc)


RiskLevel = Literal["low", "medium", "high", "critical"]
SourceAccessStatus = Literal[
    "public",
    "auth_required",
    "blocked",
    "metadata_only",
    "not_configured",
    "ok",
    "error",
    "rate_limited",
    "unavailable",
]
SourceRunStatus = Literal[
    "ok",
    "replay",
    "partial",
    "disabled",
    "auth_required",
    "blocked",
    "metadata_only",
    "rate_limited",
    "unavailable",
    "error",
    "cancelled",
]
Sentiment = Literal["positive", "negative", "neutral", "mixed", "unknown"]
Stance = Literal["support", "supportive", "oppose", "against", "uncertain", "mixed", "neutral"]


class ContractModel(BaseModel):
    """API 契约共用的基类。

    ``extra=allow`` 让前端和采集器可以独立演进，同时继续严格校验服务
    依赖的核心字段。
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class ProjectInfo(ContractModel):
    name: str = "browser-use"
    repository: str = "browser-use/browser-use"
    description: str | None = None
    version: str | None = None
    last_release_at: datetime | None = None


class ReportSummary(ContractModel):
    risk_score: float = Field(default=0.0, ge=0.0, le=100.0)
    risk_level: RiskLevel = "low"
    events_count: int = Field(default=0, ge=0)
    source_count: int = Field(default=0, ge=0)
    coverage_pct: float = Field(default=0.0, ge=0.0, le=100.0)
    positive_count: int = Field(default=0, ge=0)
    negative_count: int = Field(default=0, ge=0)
    neutral_count: int = Field(default=0, ge=0)
    unresolved_count: int = Field(default=0, ge=0)


class TrendPoint(ContractModel):
    date: str
    mentions: int = Field(default=0, ge=0)
    positive: int = Field(default=0, ge=0)
    negative: int = Field(default=0, ge=0)
    neutral: int = Field(default=0, ge=0)
    risk_score: float = Field(default=0.0, ge=0.0, le=100.0)


class Topic(ContractModel):
    name: str
    count: int = Field(default=0, ge=0)
    sentiment: Sentiment = "unknown"
    risk_score: float = Field(default=0.0, ge=0.0, le=100.0)
    sample_claims: list[str] = Field(default_factory=list)


class Article(ContractModel):
    id: str
    url: str
    title: str
    source: str
    source_type: str = "community"
    author: str | None = None
    excerpt: str | None = None
    content: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    access_status: SourceAccessStatus = "public"
    content_hash: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(ContractModel):
    id: str
    article_id: str | None = None
    url: str
    source: str
    title: str | None = None
    quote: str
    evidence_level: Literal["full_text", "excerpt", "metadata_only"] = "full_text"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    published_at: datetime | None = None
    retrieved_at: datetime = Field(default_factory=utc_now)
    content_hash: str | None = None


class Claim(ContractModel):
    id: str
    text: str
    claim_type: str = "feedback"
    stance: Stance = "uncertain"
    sentiment: Sentiment = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    article_ids: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)


class Event(ContractModel):
    id: str
    title: str
    category: str = "feedback"
    summary: str | None = None
    risk_level: RiskLevel = "low"
    risk_score: float = Field(default=0.0, ge=0.0, le=100.0)
    sentiment: Sentiment = "unknown"
    occurred_at: datetime | None = None
    article_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SourceStatus(ContractModel):
    source: str = "unknown"
    source_type: str = "community"
    status: SourceRunStatus = "ok"
    access_status: SourceAccessStatus = "public"
    records: int = Field(default=0, ge=0)
    detail: str | None = None
    error: str | None = None
    fetched_at: datetime = Field(default_factory=utc_now)
    latency_ms: float | None = Field(default=None, ge=0.0)
    authenticated: bool = False


class AccessStatusRecord(ContractModel):
    source: str
    status: SourceAccessStatus = "public"
    reason: str | None = None
    evidence_level: Literal["full_text", "excerpt", "metadata_only", "none"] = "full_text"


class Report(ContractModel):
    report_id: str = "replay-demo"
    run_id: str | None = None
    generated_at: datetime = Field(default_factory=utc_now)
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    window_days: int = Field(default=7, ge=1, le=3650)
    summary: ReportSummary = Field(default_factory=ReportSummary)
    trends: list[TrendPoint] = Field(default_factory=list)
    topics: list[Topic] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    sources: list[SourceStatus] = Field(default_factory=list)
    access_status: list[AccessStatusRecord] = Field(default_factory=list)
    articles: list[Article] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)


class RunBudget(ContractModel):
    """单次运行可观察、可校验的资源预算。"""

    max_steps: int = Field(default=12, ge=1, le=40)
    timeout_seconds: float = Field(default=180.0, gt=0.0, le=900.0)


class RunRequest(ContractModel):
    mode: Literal["replay", "live"] = "replay"
    subject: str = "browser-use/browser-use"
    # ``project`` 和 ``source`` 兼容仪表盘的简化表单。
    project: str | None = None
    repository: str | None = None
    window_days: int = Field(default=7, ge=1, le=3650)
    limit: int = Field(default=20, ge=1, le=100)
    sources: list[str] = Field(default_factory=lambda: ["github"])
    source: str | None = None
    community_query: str | None = None
    urls: list[str] = Field(default_factory=list)
    feed_urls: list[str] = Field(default_factory=list)
    fixture: str | None = None
    # Live Browser Use 的运行级预算；不传时沿用服务端适配器默认值。
    max_steps: int | None = Field(default=None, ge=1, le=40)
    timeout_seconds: float | None = Field(default=None, gt=0.0, le=900.0)
    budget: RunBudget | None = None
    # 允许客户端在异步取消前预先指定一个可追踪的运行 ID。
    run_id: str | None = Field(default=None, min_length=1, max_length=80)


class Run(ContractModel):
    run_id: str = ""
    # ``id`` 兼容使用短任务契约的客户端。
    id: str | None = None
    mode: Literal["replay", "live"] = "replay"
    status: Literal["queued", "running", "completed", "partial", "failed", "cancelled"] = "completed"
    subject: str = ""
    window_days: int = Field(default=7, ge=1, le=3650)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    report_id: str | None = None
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    error: str | None = None
    budget: RunBudget = Field(default_factory=RunBudget)
    cancel_requested: bool = False

    @model_validator(mode="after")
    def sync_ids(self) -> "Run":
        if not self.run_id and self.id:
            self.run_id = self.id
        if self.run_id and not self.id:
            self.id = self.run_id
        return self


class RunResponse(ContractModel):
    run: Run
    report: Report | None = None


class HealthResponse(ContractModel):
    status: Literal["ok", "degraded"]
    service: str = "signal-radar"
    version: str = "0.2.0"
    replay_available: bool = True


