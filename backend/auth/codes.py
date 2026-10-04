"""One-time access codes: issue, redeem, lock out brute force."""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from enum import Enum

from backend.auth.requests import get_request, normalize_email
from backend.db import iso, tx

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_TTL = timedelta(days=7)
LOCKOUT_ATTEMPTS = 5
LOCKOUT_WINDOW = timedelta(hours=1)


class RedeemResult(Enum):
    OK = "ok"
    INVALID = "invalid"
    LOCKED = "locked"


def generate_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str) -> str:
    c = re.sub(r"[^A-Za-z0-9]", "", code).upper()
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c


def hash_secret(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def issue_code(conn, email: str, now: datetime) -> str:
    code = generate_code()
    conn.execute("INSERT INTO access_codes (email, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
                 (normalize_email(email), hash_secret(code), iso(now + CODE_TTL), iso(now)))
    return code


def redeem_code(conn, email: str, code: str, ip: str, now: datetime) -> RedeemResult:
    email = normalize_email(email)
    with tx(conn):
        fails = conn.execute("SELECT COUNT(*) FROM redeem_attempts WHERE email = ? AND success = 0 AND at > ?",
                             (email, iso(now - LOCKOUT_WINDOW))).fetchone()[0]
        if fails >= LOCKOUT_ATTEMPTS:
            return RedeemResult.LOCKED
        req = get_request(conn, email)
        row = None
        if req is not None and req["status"] == "approved":
            row = conn.execute("SELECT id FROM access_codes WHERE email = ? AND code_hash = ? AND used_at IS NULL"
                               " AND expires_at > ?", (email, hash_secret(normalize_code(code)), iso(now))).fetchone()
        success = False
        if row is not None:
            cur = conn.execute("UPDATE access_codes SET used_at = ? WHERE id = ? AND used_at IS NULL", (iso(now), row["id"]))
            success = cur.rowcount == 1
        conn.execute("INSERT INTO redeem_attempts (email, ip, at, success) VALUES (?, ?, ?, ?)",
                     (email, ip, iso(now), int(success)))
    return RedeemResult.OK if success else RedeemResult.INVALID
