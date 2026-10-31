"""SQLite access layer.

One connection is shared process-wide behind a lock (SQLite in WAL mode handles
this comfortably for a single-user app). Every statement is parameterised - no
SQL string is ever built from user or model input. Async callers wrap the
synchronous methods in :func:`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from nova.db.models import (
    AuditEntry,
    AuditStatus,
    ChatMessage,
    MemoryFact,
    Note,
    Reminder,
    RiskTier,
    Todo,
)
from nova.logging_setup import get_logger

log = get_logger(__name__)

T = TypeVar("T")

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_created ON notes(created_at DESC);

CREATE TABLE IF NOT EXISTS todos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL,
    done          INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    completed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_todos_done ON todos(done, created_at DESC);

CREATE TABLE IF NOT EXISTS reminders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL DEFAULT 'user',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC);

CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tool         TEXT NOT NULL,
    args_preview TEXT NOT NULL DEFAULT '',
    risk         TEXT NOT NULL DEFAULT 'safe',
    status       TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at DESC);

CREATE TABLE IF NOT EXISTS preferences (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

# Values redacted before an audit row is written.
_SENSITIVE_ARG_KEYS = frozenset({"token", "password", "secret", "api_key", "authorization"})


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def redact_args(args: dict[str, Any], *, limit: int = 300) -> str:
    """Render tool arguments for the audit log, redacting secrets."""
    safe: dict[str, Any] = {}
    for key, value in args.items():
        if key.lower() in _SENSITIVE_ARG_KEYS:
            safe[key] = "***redacted***"
        elif isinstance(value, str) and len(value) > 120:
            safe[key] = value[:120] + "..."
        else:
            safe[key] = value
    try:
        rendered = json.dumps(safe, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = str(safe)
    return rendered[:limit]


class Database:
    """Thread-safe SQLite wrapper with a small typed query surface."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
            timeout=10.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self.migrate()

    # ------------------------------------------------------------- plumbing
    def _configure(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")

    def migrate(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION:d}")

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return cursor.fetchall()

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return cursor.rowcount

    def _insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return int(cursor.lastrowid or 0)

    # ----------------------------------------------------------------- notes
    def create_note(self, title: str, body: str = "") -> Note:
        stamp = _iso(_now())
        note_id = self._insert(
            "INSERT INTO notes (title, body, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (title, body, stamp, stamp),
        )
        return self.get_note(note_id) or Note(
            id=note_id,
            title=title,
            body=body,
            created_at=_parse(stamp),
            updated_at=_parse(stamp),
        )

    def get_note(self, note_id: int) -> Note | None:
        rows = self._query("SELECT * FROM notes WHERE id = ?", (note_id,))
        return self._row_to_note(rows[0]) if rows else None

    def list_notes(self, limit: int = 50) -> list[Note]:
        rows = self._query("SELECT * FROM notes ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row_to_note(row) for row in rows]

    def search_notes(self, query: str, limit: int = 20) -> list[Note]:
        pattern = f"%{query}%"
        rows = self._query(
            "SELECT * FROM notes WHERE title LIKE ? OR body LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (pattern, pattern, limit),
        )
        return [self._row_to_note(row) for row in rows]

    def delete_note(self, note_id: int) -> bool:
        return self._execute("DELETE FROM notes WHERE id = ?", (note_id,)) > 0

    @staticmethod
    def _row_to_note(row: sqlite3.Row) -> Note:
        return Note(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]),
        )

    # ----------------------------------------------------------------- todos
    def add_todo(self, text: str) -> Todo:
        stamp = _iso(_now())
        todo_id = self._insert(
            "INSERT INTO todos (text, done, created_at) VALUES (?, 0, ?)",
            (text, stamp),
        )
        return Todo(id=todo_id, text=text, done=False, created_at=_parse(stamp))

    def list_todos(self, *, include_done: bool = False, limit: int = 50) -> list[Todo]:
        if include_done:
            rows = self._query(
                "SELECT * FROM todos ORDER BY done ASC, created_at DESC LIMIT ?", (limit,)
            )
        else:
            rows = self._query(
                "SELECT * FROM todos WHERE done = 0 ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [self._row_to_todo(row) for row in rows]

    def complete_todo(self, todo_id: int) -> bool:
        return (
            self._execute(
                "UPDATE todos SET done = 1, completed_at = ? WHERE id = ? AND done = 0",
                (_iso(_now()), todo_id),
            )
            > 0
        )

    def delete_todo(self, todo_id: int) -> bool:
        return self._execute("DELETE FROM todos WHERE id = ?", (todo_id,)) > 0

    def find_todos(self, text: str, limit: int = 5) -> list[Todo]:
        rows = self._query(
            "SELECT * FROM todos WHERE text LIKE ? ORDER BY done ASC, created_at DESC LIMIT ?",
            (f"%{text}%", limit),
        )
        return [self._row_to_todo(row) for row in rows]

    @staticmethod
    def _row_to_todo(row: sqlite3.Row) -> Todo:
        return Todo(
            id=row["id"],
            text=row["text"],
            done=bool(row["done"]),
            created_at=_parse(row["created_at"]),
            completed_at=_parse(row["completed_at"]) if row["completed_at"] else None,
        )

    # ------------------------------------------------------------- reminders
    def add_reminder(self, text: str, due_at: datetime) -> Reminder:
        stamp = _iso(_now())
        reminder_id = self._insert(
            "INSERT INTO reminders (text, due_at, created_at, status) VALUES (?, ?, ?, 'pending')",
            (text, _iso(due_at), stamp),
        )
        return Reminder(
            id=reminder_id,
            text=text,
            due_at=due_at,
            created_at=_parse(stamp),
            status="pending",
        )

    def list_reminders(self, *, pending_only: bool = True, limit: int = 50) -> list[Reminder]:
        if pending_only:
            rows = self._query(
                "SELECT * FROM reminders WHERE status = 'pending' ORDER BY due_at ASC LIMIT ?",
                (limit,),
            )
        else:
            rows = self._query("SELECT * FROM reminders ORDER BY due_at DESC LIMIT ?", (limit,))
        return [self._row_to_reminder(row) for row in rows]

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        rows = self._query("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return self._row_to_reminder(rows[0]) if rows else None

    def set_reminder_status(
        self, reminder_id: int, status: str
    ) -> bool:  # 'fired' | 'cancelled' | 'pending'
        if status not in {"pending", "fired", "cancelled"}:
            raise ValueError(f"invalid reminder status: {status!r}")
        return (
            self._execute(
                "UPDATE reminders SET status = ? WHERE id = ?",
                (status, reminder_id),
            )
            > 0
        )

    @staticmethod
    def _row_to_reminder(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            text=row["text"],
            due_at=_parse(row["due_at"]),
            created_at=_parse(row["created_at"]),
            status=row["status"],
        )

    # ---------------------------------------------------------------- memory
    def remember(self, content: str, source: str = "user") -> MemoryFact:
        stamp = _iso(_now())
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO memories (content, source, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(content) DO UPDATE SET created_at = excluded.created_at",
                (content, source, stamp),
            )
        rows = self._query("SELECT * FROM memories WHERE content = ?", (content,))
        row = rows[0]
        return MemoryFact(
            id=row["id"],
            content=row["content"],
            source=row["source"],
            created_at=_parse(row["created_at"]),
        )

    def list_memories(self, limit: int = 100) -> list[MemoryFact]:
        rows = self._query("SELECT * FROM memories ORDER BY created_at DESC LIMIT ?", (limit,))
        return [
            MemoryFact(
                id=row["id"],
                content=row["content"],
                source=row["source"],
                created_at=_parse(row["created_at"]),
            )
            for row in rows
        ]

    def forget_memory(self, memory_id: int) -> bool:
        return self._execute("DELETE FROM memories WHERE id = ?", (memory_id,)) > 0

    def forget_matching(self, text: str) -> int:
        return self._execute("DELETE FROM memories WHERE content LIKE ?", (f"%{text}%",))

    def clear_memories(self) -> int:
        return self._execute("DELETE FROM memories", ())

    # -------------------------------------------------------------- messages
    def add_message(self, role: str, content: str) -> int:
        return self._insert(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, _iso(_now())),
        )

    def recent_messages(self, limit: int = 20) -> list[ChatMessage]:
        rows = self._query(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        messages = [
            ChatMessage(
                id=row["id"],
                role=row["role"],
                content=row["content"],
                created_at=_parse(row["created_at"]),
            )
            for row in rows
        ]
        messages.reverse()
        return messages

    def clear_messages(self) -> int:
        return self._execute("DELETE FROM messages", ())

    # ----------------------------------------------------------------- audit
    def log_tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        risk: RiskTier,
        status: AuditStatus,
        detail: str = "",
        duration_ms: int = 0,
    ) -> int:
        return self._insert(
            "INSERT INTO audit_log (tool, args_preview, risk, status, detail, duration_ms, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                tool,
                redact_args(args),
                risk,
                status,
                detail[:500],
                duration_ms,
                _iso(_now()),
            ),
        )

    def list_audit(self, limit: int = 100) -> list[AuditEntry]:
        rows = self._query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [
            AuditEntry(
                id=row["id"],
                tool=row["tool"],
                args_preview=row["args_preview"],
                risk=row["risk"],
                status=row["status"],
                detail=row["detail"],
                duration_ms=row["duration_ms"],
                created_at=_parse(row["created_at"]),
            )
            for row in rows
        ]

    def clear_audit(self) -> int:
        return self._execute("DELETE FROM audit_log", ())

    # ----------------------------------------------------------- preferences
    def set_preference(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO preferences (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def get_preferences(self) -> dict[str, str]:
        rows = self._query("SELECT key, value FROM preferences", ())
        return {row["key"]: row["value"] for row in rows}


_database: Database | None = None
_database_lock = threading.Lock()


def get_database(path: Path | None = None) -> Database:
    """Return (and lazily create) the process-wide database."""
    global _database
    with _database_lock:
        if _database is None:
            if path is None:
                from nova.config import get_settings

                path = get_settings().db_path
            _database = Database(path)
            log.info("database.ready", path=str(path))
        return _database


def reset_database() -> None:
    """Close and forget the singleton (used by tests and shutdown)."""
    global _database
    with _database_lock:
        if _database is not None:
            _database.close()
            _database = None


async def run_db(func: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking database call off the event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)
