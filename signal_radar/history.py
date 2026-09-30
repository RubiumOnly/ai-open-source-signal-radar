"""SQLite-backed run history and deterministic report exports.

The history store intentionally uses only the Python standard library.  Run
and report payloads are stored as JSON so the persisted representation follows
the public Pydantic contracts without duplicating every nested field in SQL.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import Annotation, Report, Run, RunEvent, RunResponse


DEFAULT_HISTORY_PATH = Path(__file__).resolve().parent.parent / "data" / "runs.sqlite3"


def _json_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _markdown_text(value: Any) -> str:
    """Keep user/source text from accidentally becoming Markdown structure."""

    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def report_to_markdown(report: Report, run: Run | None = None) -> str:
    """Render a compact, citation-oriented Markdown report without extra deps."""

    project = report.project
    summary = report.summary
    lines = [
        f"# Signal Radar: {_markdown_text(project.name)}",
        "",
        f"- 项目：`{_markdown_text(project.repository)}`",
        f"- 运行 ID：`{_markdown_text(run.run_id if run else report.run_id or '')}`",
        f"- 生成时间：{report.generated_at.isoformat()}",
        f"- 分析窗口：{report.window_days} 天",
        "",
        "## 摘要",
        "",
        f"- 风险等级：**{summary.risk_level}**",
        f"- 风险分数：{summary.risk_score:.1f}/100",
        f"- 事件数：{summary.events_count}",
        f"- 来源数：{summary.source_count}",
        f"- 证据覆盖率：{summary.coverage_pct:.1f}%",
    ]
    if run is not None:
        lines.extend([
            f"- 运行状态：**{run.status}**",
            f"- 开始时间：{run.started_at.isoformat()}",
            f"- 完成时间：{run.completed_at.isoformat() if run.completed_at else '未完成'}",
            f"- 运行预算：最多 {run.budget.max_steps} 步，{run.budget.timeout_seconds:.1f} 秒",
            f"- 取消请求：{'是' if run.cancel_requested else '否'}",
        ])
        if run.error:
            lines.append(f"- 错误：`{_markdown_text(run.error)}`")

    if report.events:
        lines.extend(["", "## 关键事件", ""])
        for event in report.events:
            title = _markdown_text(event.title) or event.id
            lines.append(
                f"- **{title}**（{event.risk_level}, {event.risk_score:.1f}）"
                f"：{_markdown_text(event.summary) or '暂无摘要'}"
            )

    if report.topics:
        lines.extend(["", "## 主题", "", "| 主题 | 数量 | 情绪 | 风险 |", "| --- | ---: | --- | ---: |"])
        for topic in report.topics:
            lines.append(
                f"| {_markdown_text(topic.name)} | {topic.count} | {topic.sentiment} | {topic.risk_score:.1f} |"
            )

    if report.evidence:
        lines.extend(["", "## 证据", ""])
        for evidence in report.evidence:
            title = _markdown_text(evidence.title) or evidence.id
            quote = _markdown_text(evidence.quote) or "（无可核验摘录）"
            lines.append(
                f"- **{title}**（{evidence.source}, {evidence.evidence_level}, "
                f"置信度 {evidence.confidence:.2f}）"
            )
            lines.append(f"  - 摘录：{quote}")
            lines.append(f"  - 来源：[打开原文]({evidence.url})")

    if report.sources:
        lines.extend(["", "## 来源状态", ""])
        for source in report.sources:
            detail = _markdown_text(source.detail or source.error or "")
            suffix = f"：{detail}" if detail else ""
            lines.append(f"- **{source.source}**：{source.status}，记录 {source.records}{suffix}")

    if report.access_status:
        lines.extend(["", "## 访问边界", ""])
        for access in report.access_status:
            reason = f"：{_markdown_text(access.reason)}" if access.reason else ""
            lines.append(f"- **{access.source}**：{access.status}，证据级别 {access.evidence_level}{reason}")

    lines.append("")
    return "\n".join(lines)


class HistoryStore:
    """Persist completed and failed runs in a small SQLite database.

    A fresh connection is used per operation for file-backed databases, which
    keeps the store safe when FastAPI dispatches synchronous handlers across
    worker threads. ``:memory:`` is supported for isolated tests.
    """

    def __init__(self, path: str | Path = DEFAULT_HISTORY_PATH):
        self.path = str(path)
        self._event_lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        self._db_path = self.path
        if self.path == ":memory:":
            # 共享缓存 URI 让后台线程各用自己的连接；锚点连接保留数据库生命周期。
            self._db_path = f"file:signal-radar-{uuid4().hex}?mode=memory&cache=shared"
            self._memory_connection = self._connect()
        else:
            db_path = Path(self.path).expanduser()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.path = str(db_path)
            self._db_path = self.path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._db_path,
            timeout=10,
            check_same_thread=False,
            uri=self.path == ":memory:",
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _connection(self) -> sqlite3.Connection:
        return self._connect()

    def _initialize(self) -> None:
        connection = self._connection()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    window_days INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    report_id TEXT,
                    error TEXT,
                    run_json TEXT NOT NULL,
                    report_json TEXT
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at DESC)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS run_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_json TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, seq)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS annotations (
                    annotation_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    value TEXT NOT NULL,
                    note TEXT,
                    reviewer TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    annotation_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_annotations_target ON annotations(target_type, target_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_annotations_run ON annotations(run_id, timestamp DESC)"
            )
            connection.commit()
        finally:
            if connection is not self._memory_connection:
                connection.close()

    def save(self, run: Run, report: Report | None = None) -> Run:
        """Insert or replace a run, including its optional report snapshot."""

        run_json = run.model_dump_json(exclude_none=False)
        report_json = report.model_dump_json(exclude_none=False) if report is not None else None
        connection = self._connection()
        try:
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, mode, status, subject, window_days, started_at,
                    completed_at, report_id, error, run_json, report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    mode=excluded.mode,
                    status=excluded.status,
                    subject=excluded.subject,
                    window_days=excluded.window_days,
                    started_at=excluded.started_at,
                    completed_at=excluded.completed_at,
                    report_id=excluded.report_id,
                    error=excluded.error,
                    run_json=excluded.run_json,
                    report_json=excluded.report_json
                """,
                (
                    run.run_id,
                    run.mode,
                    run.status,
                    run.subject,
                    run.window_days,
                    _json_datetime(run.started_at),
                    _json_datetime(run.completed_at),
                    run.report_id,
                    run.error,
                    run_json,
                    report_json,
                ),
            )
            connection.commit()
        finally:
            if connection is not self._memory_connection:
                connection.close()
        return run

    # Verbose aliases keep the storage contract discoverable to callers while
    # retaining the short methods used by the API handlers.
    save_run = save

    def _row_to_response(self, row: sqlite3.Row) -> RunResponse:
        run = Run.model_validate_json(row["run_json"])
        report_json = row["report_json"]
        report = Report.model_validate_json(report_json) if report_json else None
        return RunResponse(run=run, report=report)

    def list(self, *, limit: int = 20, offset: int = 0) -> list[Run]:
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        connection = self._connection()
        try:
            rows = connection.execute(
                "SELECT run_json FROM runs ORDER BY julianday(started_at) DESC, run_id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [Run.model_validate_json(row["run_json"]) for row in rows]
        finally:
            if connection is not self._memory_connection:
                connection.close()

    list_runs = list

    def get(self, run_id: str) -> RunResponse | None:
        connection = self._connection()
        try:
            row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return self._row_to_response(row) if row is not None else None
        finally:
            if connection is not self._memory_connection:
                connection.close()

    get_run = get

    def add_event(self, event: RunEvent) -> RunEvent:
        """持久化一条运行事件，保留同一运行内的到达顺序。"""

        with self._event_lock:
            connection = self._connection()
            try:
                connection.execute(
                    "INSERT INTO run_events(event_id, run_id, created_at, event_json) VALUES (?, ?, ?, ?)",
                    (event.id, event.run_id, _json_datetime(event.created_at), event.model_dump_json()),
                )
                connection.commit()
            finally:
                if connection is not self._memory_connection:
                    connection.close()
        return event

    def list_events(self, run_id: str, *, offset: int = 0, limit: int = 500) -> list[RunEvent]:
        """按写入顺序回放事件；SSE 可以从给定偏移继续读取。"""

        bounded_limit = max(1, min(int(limit), 1000))
        with self._event_lock:
            connection = self._connection()
            try:
                rows = connection.execute(
                    "SELECT event_json FROM run_events WHERE run_id = ? ORDER BY seq LIMIT ? OFFSET ?",
                    (run_id, bounded_limit, max(0, int(offset))),
                ).fetchall()
                return [RunEvent.model_validate_json(row["event_json"]) for row in rows]
            finally:
                if connection is not self._memory_connection:
                    connection.close()

    def add_annotation(self, annotation: Annotation) -> Annotation:
        """Persist one review label without replacing an existing annotation."""

        annotation_json = annotation.model_dump_json(exclude_none=False)
        connection = self._connection()
        try:
            connection.execute(
                """
                INSERT INTO annotations (
                    annotation_id, run_id, target_type, target_id, label, value,
                    note, reviewer, timestamp, annotation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation.id,
                    annotation.run_id,
                    annotation.target_type,
                    annotation.target_id,
                    annotation.label,
                    annotation.value,
                    annotation.note,
                    annotation.reviewer,
                    _json_datetime(annotation.timestamp),
                    annotation_json,
                ),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"annotation already exists: {annotation.id}") from exc
        finally:
            if connection is not self._memory_connection:
                connection.close()
        return annotation

    save_annotation = add_annotation

    def get_annotation(self, annotation_id: str) -> Annotation | None:
        connection = self._connection()
        try:
            row = connection.execute(
                "SELECT annotation_json FROM annotations WHERE annotation_id = ?",
                (annotation_id,),
            ).fetchone()
            return Annotation.model_validate_json(row["annotation_json"]) if row is not None else None
        finally:
            if connection is not self._memory_connection:
                connection.close()

    def list_annotations(
        self,
        *,
        run_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        label: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Annotation]:
        """List labels with optional report/target filters for review tooling."""

        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("run_id", run_id),
            ("target_type", target_type),
            ("target_id", target_id),
            ("label", label),
        ):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connection()
        try:
            rows = connection.execute(
                "SELECT annotation_json FROM annotations"
                + where
                + " ORDER BY julianday(timestamp) DESC, annotation_id DESC LIMIT ? OFFSET ?",
                (*values, limit, offset),
            ).fetchall()
            return [Annotation.model_validate_json(row["annotation_json"]) for row in rows]
        finally:
            if connection is not self._memory_connection:
                connection.close()

    annotations = list_annotations

    def export_annotations(
        self,
        *,
        run_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        label: str | None = None,
    ) -> list[Annotation]:
        """Return all matching labels for JSON evaluation-data export."""

        return self.list_annotations(
            run_id=run_id,
            target_type=target_type,
            target_id=target_id,
            label=label,
            limit=500,
            offset=0,
        )

    def markdown(self, run_id: str) -> str | None:
        response = self.get(run_id)
        if response is None or response.report is None:
            return None
        return report_to_markdown(response.report, response.run)

    get_markdown = markdown

    def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None


SQLiteHistoryStore = HistoryStore
RunHistoryStore = HistoryStore

__all__ = [
    "DEFAULT_HISTORY_PATH",
    "HistoryStore",
    "SQLiteHistoryStore",
    "RunHistoryStore",
    "Annotation",
    "report_to_markdown",
]
