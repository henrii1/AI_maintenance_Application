"""app.db: runtime user data on the persistent volume."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

MIGRATIONS = [
    """
    CREATE TABLE access_requests (
      id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, company TEXT NOT NULL,
      note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', daily_limit INTEGER,
      email_error TEXT, created_at TEXT NOT NULL, decided_at TEXT);
    CREATE TABLE access_codes (
      id INTEGER PRIMARY KEY, email TEXT NOT NULL, code_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
      used_at TEXT, created_at TEXT NOT NULL);
    CREATE INDEX access_codes_email ON access_codes (email);
    CREATE TABLE sessions (
      token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL);
    CREATE TABLE redeem_attempts (
      id INTEGER PRIMARY KEY, email TEXT NOT NULL, ip TEXT NOT NULL, at TEXT NOT NULL, success INTEGER NOT NULL);
    CREATE TABLE usage_daily (
      email TEXT NOT NULL, day TEXT NOT NULL, questions INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (email, day));
    CREATE TABLE conversations (
      id TEXT PRIMARY KEY, email TEXT NOT NULL, set_id TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE messages (
      id INTEGER PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
      query TEXT, assets_json TEXT, tools_json TEXT, created_at TEXT NOT NULL);
    CREATE INDEX messages_conv ON messages (conversation_id, id);
    CREATE TABLE llm_calls (
      id INTEGER PRIMARY KEY, conversation_id TEXT, email TEXT, call_type TEXT NOT NULL, model TEXT NOT NULL,
      input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cache_read_tokens INTEGER NOT NULL,
      web_searches INTEGER NOT NULL, cost_usd REAL NOT NULL, created_at TEXT NOT NULL);
    """,
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def day_key(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).date().isoformat()


def connect_app(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN; {sql} PRAGMA user_version = {i}; COMMIT;")


@contextmanager
def tx(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
