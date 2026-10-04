"""Access requests: a visitor asks for access; the admin approves, rejects, revokes or restores."""
from __future__ import annotations

import re
from datetime import datetime

from backend.db import day_key, iso

STATUSES = ("pending", "approved", "rejected", "revoked")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email)) and len(email) <= 254


def create_request(conn, email: str, name: str, company: str, note: str, now: datetime) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO access_requests (email, name, company, note, created_at)"
                       " VALUES (?, ?, ?, ?, ?)", (normalize_email(email), name.strip(), company.strip(),
                                                    note.strip(), iso(now)))
    return cur.rowcount == 1


def get_request(conn, email: str):
    return conn.execute("SELECT * FROM access_requests WHERE email = ?", (normalize_email(email),)).fetchone()


def set_status(conn, email: str, status: str, now: datetime) -> bool:
    assert status in STATUSES
    cur = conn.execute("UPDATE access_requests SET status = ?, decided_at = ? WHERE email = ?",
                       (status, iso(now), normalize_email(email)))
    return cur.rowcount == 1


def set_daily_limit(conn, email: str, limit: int | None) -> None:
    conn.execute("UPDATE access_requests SET daily_limit = ? WHERE email = ?", (limit, normalize_email(email)))


def set_email_error(conn, email: str, error: str | None) -> None:
    conn.execute("UPDATE access_requests SET email_error = ? WHERE email = ?", (error, normalize_email(email)))


def list_requests(conn, statuses: tuple[str, ...], now: datetime) -> list[dict]:
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"""SELECT r.*, COALESCE(u.questions, 0) AS questions_today,
                   (SELECT COALESCE(SUM(questions), 0) FROM usage_daily WHERE email = r.email) AS questions_total,
                   (SELECT MAX(last_seen_at) FROM sessions WHERE email = r.email) AS last_active
            FROM access_requests r LEFT JOIN usage_daily u ON u.email = r.email AND u.day = ?
            WHERE r.status IN ({marks}) ORDER BY r.created_at DESC""", (day_key(now), *statuses)).fetchall()
    return [dict(r) for r in rows]
