"""DB 접근 계층.

- 기본: SQLite 파일 (로컬 개발·테스트용, 설치 불필요)
- 배포: PostgreSQL (Supabase 무료 플랜 등) — DATABASE_URL 만 바꾸면 같은 코드로 동작

두 DB에서 같은 SQL이 돌도록 다음 규칙을 지킨다.
- 모든 PK는 Python에서 만든 TEXT UUID (AUTOINCREMENT / SERIAL 차이 회피)
- 시각은 UTC ISO 문자열(TEXT)
- 파라미터 자리표시자는 '?' 로 쓰고, PostgreSQL에서는 '%s' 로 자동 변환
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

KST = timezone(timedelta(hours=9))

SCHEMA: list[str] = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        nickname TEXT UNIQUE NOT NULL,
        pin_hash TEXT NOT NULL,
        pin_salt TEXT NOT NULL,
        consent_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        num_chunks INTEGER NOT NULL,
        num_chars INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chunks (
        id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        page INTEGER,
        content TEXT NOT NULL,
        embedding TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chunks_user ON chunks(user_id)",
    """CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        category TEXT NOT NULL,
        content TEXT NOT NULL,
        weight INTEGER NOT NULL,
        source TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)",
    """CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        sources TEXT,
        tools_used TEXT,
        rating INTEGER,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id)",
    """CREATE TABLE IF NOT EXISTS quiz_attempts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        topic TEXT NOT NULL,
        num_questions INTEGER NOT NULL,
        num_correct INTEGER NOT NULL,
        details TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS feedback (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        satisfaction INTEGER,
        usefulness INTEGER,
        ease INTEGER,
        recommend INTEGER,
        liked TEXT,
        improve TEXT,
        tester_group TEXT,
        app_version TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        event_type TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, event_type)",
]

USER_TABLES = ("chunks", "documents", "memories", "messages", "quiz_attempts", "events")


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def kst_today_start_utc() -> str:
    """한국 시간 기준 오늘 0시를 UTC ISO 문자열로 (일일 사용량 제한 계산용)."""
    start = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).isoformat(timespec="microseconds")


def to_kst_display(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


class Database:
    def __init__(self, url: str):
        self.url = url
        self._lock = threading.RLock()
        self._conn: Any = None
        if url.startswith("sqlite:///"):
            self.kind = "sqlite"
            self._path = url[len("sqlite:///"):]
            if self._path != ":memory:":
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        elif url.startswith(("postgres://", "postgresql://")):
            self.kind = "postgres"
        else:
            raise ValueError("DATABASE_URL은 sqlite:///경로 또는 postgresql://... 형식이어야 합니다.")

    # ---------- 연결 ----------
    def _connect(self) -> Any:
        if self.kind == "sqlite":
            conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
            if self._path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL")
            return conn
        import psycopg2  # 배포(PostgreSQL)에서만 필요

        conn = psycopg2.connect(self.url, connect_timeout=10)
        conn.autocommit = True
        return conn

    def _conn_or_connect(self) -> Any:
        if self._conn is None:
            self._conn = self._connect()
        return self._conn

    def _is_connection_error(self, exc: Exception) -> bool:
        if self.kind != "postgres":
            return False
        try:
            import psycopg2

            return isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError))
        except Exception:  # pragma: no cover
            return False

    def _run(self, fn: Callable[[Any], Any]) -> Any:
        with self._lock:
            try:
                return fn(self._conn_or_connect())
            except Exception as exc:
                if self._is_connection_error(exc):  # 끊긴 연결은 한 번 다시 연결
                    self._conn = None
                    return fn(self._conn_or_connect())
                raise

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    # ---------- 실행 ----------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        def fn(conn: Any) -> int:
            cur = conn.cursor()
            try:
                cur.execute(self._sql(sql), tuple(params))
                return cur.rowcount
            finally:
                cur.close()

        return self._run(fn)

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        rows = [tuple(p) for p in seq]
        if not rows:
            return

        def fn(conn: Any) -> None:
            cur = conn.cursor()
            try:
                if self.kind == "postgres":
                    from psycopg2.extras import execute_batch

                    execute_batch(cur, self._sql(sql), rows, page_size=100)
                else:
                    cur.execute("BEGIN")
                    try:
                        cur.executemany(sql, rows)
                        cur.execute("COMMIT")
                    except Exception:
                        cur.execute("ROLLBACK")
                        raise
            finally:
                cur.close()

        self._run(fn)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        def fn(conn: Any) -> list[dict[str, Any]]:
            cur = conn.cursor()
            try:
                cur.execute(self._sql(sql), tuple(params))
                if cur.description is None:
                    return []
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                cur.close()

        return self._run(fn)

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = 0) -> Any:
        row = self.query_one(sql, params)
        if not row:
            return default
        value = next(iter(row.values()))
        return default if value is None else value

    def init_schema(self) -> None:
        for stmt in SCHEMA:
            self.execute(stmt)

    def delete_user_data(self, user_id: str) -> None:
        """사용자의 모든 데이터 삭제. 피드백은 익명화(user_id 제거)하여 통계용으로만 남긴다."""
        for table in USER_TABLES:
            self.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        self.execute("UPDATE feedback SET user_id = NULL WHERE user_id = ?", (user_id,))
        self.execute("DELETE FROM users WHERE id = ?", (user_id,))
