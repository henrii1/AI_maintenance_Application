"""Daily question quotas, reserved atomically before LLM calls and released on failure."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.config import Settings
from backend.db import day_key, tx


@dataclass(frozen=True)
class QuotaStatus:
    allowed: bool
    remaining: int | None
    limit: int | None
    reason: str | None  # "user" | "global" | None
    resets_at: str


def _resets_at(now: datetime) -> str:
    tomorrow = (now.astimezone(timezone.utc) + timedelta(days=1)).date()
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc).isoformat()


def user_limit(conn, email: str, settings: Settings) -> int:
    row = conn.execute("SELECT daily_limit FROM access_requests WHERE email = ?", (email,)).fetchone()
    return row["daily_limit"] if row and row["daily_limit"] is not None else settings.daily_question_limit


def _used(conn, email: str, day: str) -> int:
    row = conn.execute("SELECT questions FROM usage_daily WHERE email = ? AND day = ?", (email, day)).fetchone()
    return row["questions"] if row else 0


def status(conn, email: str, settings: Settings, now: datetime, is_admin: bool = False) -> QuotaStatus:
    if is_admin:
        return QuotaStatus(True, None, None, None, _resets_at(now))
    limit = user_limit(conn, email, settings)
    remaining = max(limit - _used(conn, email, day_key(now)), 0)
    return QuotaStatus(remaining > 0, remaining, limit, None if remaining else "user", _resets_at(now))


def try_reserve(conn, email: str, settings: Settings, now: datetime, is_admin: bool = False) -> QuotaStatus:
    resets = _resets_at(now)
    day = day_key(now)
    with tx(conn):
        global_used = conn.execute("SELECT COALESCE(SUM(questions), 0) FROM usage_daily WHERE day = ?",
                                   (day,)).fetchone()[0]
        if not is_admin:
            if global_used >= settings.global_daily_limit:
                return QuotaStatus(False, 0, None, "global", resets)
            limit = user_limit(conn, email, settings)
            used = _used(conn, email, day)
            if used >= limit:
                return QuotaStatus(False, 0, limit, "user", resets)
        conn.execute("INSERT INTO usage_daily (email, day, questions) VALUES (?, ?, 1)"
                     " ON CONFLICT (email, day) DO UPDATE SET questions = questions + 1", (email, day))
    if is_admin:
        return QuotaStatus(True, None, None, None, resets)
    return QuotaStatus(True, limit - used - 1, limit, None, resets)


def release(conn, email: str, now: datetime) -> None:
    conn.execute("UPDATE usage_daily SET questions = MAX(questions - 1, 0) WHERE email = ? AND day = ?",
                 (email, day_key(now)))


def reset_today(conn, email: str, now: datetime) -> None:
    conn.execute("DELETE FROM usage_daily WHERE email = ? AND day = ?", (email, day_key(now)))
