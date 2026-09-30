"""公开来源的轻量增量缓存。

缓存只保存 HTTP 校验器、响应摘要和记录指纹，不保存页面正文、Cookie 或
模型密钥。它服务于定时运行和重复研究：来源返回同一条记录时可以跳过重复
分析，同时保留新增记录数和缓存命中信息供报告审计。缓存命中时来源仍会返回
完整的当前窗口快照，避免持续监控把“没有新增”误显示成“没有信号”。
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4


DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "source-cache.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:32]


@dataclass(frozen=True)
class CacheDelta:
    """一批候选记录相对于上次运行的变化。"""

    new_records: int = 0
    duplicate_records: int = 0


class SourceCache:
    """线程安全、可跨进程重启复用的 SQLite 元数据缓存。"""

    def __init__(self, path: str | Path = DEFAULT_CACHE_PATH):
        self.path = str(path)
        self._lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        self._db_path = self.path
        if self.path == ":memory:":
            self._db_path = f"file:signal-radar-cache-{uuid4().hex}?mode=memory&cache=shared"
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
        return connection

    def _connection(self) -> sqlite3.Connection:
        return self._connect()

    def _initialize(self) -> None:
        connection = self._connection()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS http_cache (
                    request_key TEXT PRIMARY KEY,
                    etag TEXT,
                    last_modified TEXT,
                    content_hash TEXT,
                    seen_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS source_records (
                    source_key TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (source_key, record_key)
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_source_records_seen ON source_records(seen_at)")
            connection.commit()
        finally:
            if connection is not self._memory_connection:
                connection.close()

    def request_headers(self, request_key: str) -> dict[str, str]:
        """返回可直接附加到 GET 请求的条件请求头。"""

        with self._lock:
            connection = self._connection()
            try:
                row = connection.execute(
                    "SELECT etag, last_modified FROM http_cache WHERE request_key = ?",
                    (request_key,),
                ).fetchone()
                if row is None:
                    return {}
                headers: dict[str, str] = {}
                if row["etag"]:
                    headers["If-None-Match"] = str(row["etag"])
                if row["last_modified"]:
                    headers["If-Modified-Since"] = str(row["last_modified"])
                return headers
            finally:
                if connection is not self._memory_connection:
                    connection.close()

    def save_response(
        self,
        request_key: str,
        payload: bytes,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> bool:
        """保存响应元数据，返回响应内容是否发生变化。"""

        content_hash = _hash_bytes(payload)
        with self._lock:
            connection = self._connection()
            try:
                previous = connection.execute(
                    "SELECT content_hash FROM http_cache WHERE request_key = ?",
                    (request_key,),
                ).fetchone()
                changed = previous is None or previous["content_hash"] != content_hash
                connection.execute(
                    """
                    INSERT INTO http_cache(request_key, etag, last_modified, content_hash, seen_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(request_key) DO UPDATE SET
                        etag=excluded.etag,
                        last_modified=excluded.last_modified,
                        content_hash=excluded.content_hash,
                        seen_at=excluded.seen_at
                    """,
                    (request_key, etag, last_modified, content_hash, _now()),
                )
                connection.commit()
                return changed
            finally:
                if connection is not self._memory_connection:
                    connection.close()

    def register_records(self, source_key: str, records: Iterable[tuple[str, str]]) -> CacheDelta:
        """登记一批 ``(record_key, content_hash)`` 并返回新增/重复计数。"""

        new_records = 0
        duplicate_records = 0
        with self._lock:
            connection = self._connection()
            try:
                for record_key, content_hash in records:
                    key = str(record_key).strip()
                    digest = str(content_hash).strip()
                    if not key or not digest:
                        continue
                    previous = connection.execute(
                        "SELECT content_hash FROM source_records WHERE source_key = ? AND record_key = ?",
                        (source_key, key),
                    ).fetchone()
                    if previous is not None and previous["content_hash"] == digest:
                        duplicate_records += 1
                    else:
                        new_records += 1
                    connection.execute(
                        """
                        INSERT INTO source_records(source_key, record_key, content_hash, seen_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(source_key, record_key) DO UPDATE SET
                            content_hash=excluded.content_hash,
                            seen_at=excluded.seen_at
                        """,
                        (source_key, key, digest, _now()),
                    )
                connection.commit()
            finally:
                if connection is not self._memory_connection:
                    connection.close()
        return CacheDelta(new_records=new_records, duplicate_records=duplicate_records)

    def register_record(self, source_key: str, record_key: str, content_hash: str) -> bool:
        """登记单条记录并返回它是否为新增或内容更新。"""

        return self.register_records(source_key, [(record_key, content_hash)]).new_records == 1

    def close(self) -> None:
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None


__all__ = ["CacheDelta", "DEFAULT_CACHE_PATH", "SourceCache"]
