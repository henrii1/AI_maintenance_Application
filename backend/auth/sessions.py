"""Opaque session tokens (stored hashed) for approved users."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from backend.auth.codes import hash_secret
from backend.auth.requests import normalize_email
from backend.db import iso

SESSION_TTL = timedelta(days=30)


def create_session(conn, email: str, now: datetime) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions (token_hash, email, created_at, expires_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                 (hash_secret(token), normalize_email(email), iso(now), iso(now + SESSION_TTL), iso(now)))
    return token


def session_email(conn, token: str, now: datetime) -> str | None:
    th = hash_secret(token)
    row = conn.execute("SELECT s.email FROM sessions s JOIN access_requests r ON r.email = s.email"
                       " WHERE s.token_hash = ? AND s.expires_at > ? AND r.status = 'approved'",
                       (th, iso(now))).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (iso(now), th))
    return row["email"]


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_secret(token),))


def delete_sessions(conn, email: str) -> None:
    conn.execute("DELETE FROM sessions WHERE email = ?", (normalize_email(email),))
