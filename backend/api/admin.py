"""Admin endpoints: login, review/approve requests, per-user limits, usage and spend."""
from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api.deps import ADMIN_COOKIE, ADMIN_MAX_AGE, admin_signer, app_db, client_ip, require_admin, \
    set_cookie, settings_of
from backend.auth import codes, quota, requests as reqs, sessions
from backend.auth.email import access_code_email
from backend.db import day_key, utcnow

router = APIRouter()
STATUS_GROUPS = {"pending": ("pending",), "approved": ("approved",), "closed": ("rejected", "revoked")}
EMAIL_FAILED = "Email delivery failed (Resend)."


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


class LimitIn(BaseModel):
    daily_limit: int | None = Field(default=None, ge=0, le=1000)


@router.post("/api/admin/login")
def login(body: LoginIn, request: Request, response: Response):
    settings = settings_of(request)
    if not settings.admin_password:
        raise HTTPException(503, "Admin is disabled: set ADMIN_PASSWORD.")
    if not request.app.state.limiters.admin_login_ip.hit(client_ip(request)):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    if not hmac.compare_digest(body.password.encode(), settings.admin_password.encode()):
        raise HTTPException(401, "Wrong password.")
    set_cookie(response, settings, ADMIN_COOKIE, admin_signer(settings).sign(b"admin").decode(), ADMIN_MAX_AGE)
    return {"ok": True}


@router.post("/api/admin/logout")
def logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}


def _row(conn, email: str) -> dict:
    rows = [r for r in reqs.list_requests(conn, reqs.STATUSES, utcnow()) if r["email"] == email]
    if not rows:
        raise HTTPException(404, "No such request.")
    return rows[0]


def _send_code(request: Request, conn, email: str) -> None:
    code = codes.issue_code(conn, email, utcnow())
    ok = request.app.state.mailer.send(email, *access_code_email(settings_of(request), email, code))
    reqs.set_email_error(conn, email, None if ok else EMAIL_FAILED)


@router.get("/api/admin/requests", dependencies=[Depends(require_admin)])
def list_requests(status: str = "pending", conn=Depends(app_db)):
    if status not in STATUS_GROUPS:
        raise HTTPException(422, "status must be pending, approved or closed")
    return reqs.list_requests(conn, STATUS_GROUPS[status], utcnow())


@router.post("/api/admin/requests/{email}/{action}", dependencies=[Depends(require_admin)])
def act(email: str, action: str, request: Request, conn=Depends(app_db)):
    email = reqs.normalize_email(email)
    if reqs.get_request(conn, email) is None:
        raise HTTPException(404, "No such request.")
    now = utcnow()
    if action == "approve":
        reqs.set_status(conn, email, "approved", now)
        _send_code(request, conn, email)
    elif action == "reject":
        reqs.set_status(conn, email, "rejected", now)
    elif action == "revoke":
        reqs.set_status(conn, email, "revoked", now)
        sessions.delete_sessions(conn, email)
    elif action == "restore":
        reqs.set_status(conn, email, "pending", now)
    elif action == "resend-code":
        _send_code(request, conn, email)
    elif action == "reset-quota":
        quota.reset_today(conn, email, now)
    else:
        raise HTTPException(404, "Unknown action.")
    return _row(conn, email)


@router.patch("/api/admin/requests/{email}", dependencies=[Depends(require_admin)])
def patch(email: str, body: LimitIn, conn=Depends(app_db)):
    email = reqs.normalize_email(email)
    if reqs.get_request(conn, email) is None:
        raise HTTPException(404, "No such request.")
    reqs.set_daily_limit(conn, email, body.daily_limit)
    return _row(conn, email)


@router.get("/api/admin/requests/{email}/conversations", dependencies=[Depends(require_admin)])
def conversations(email: str, conn=Depends(app_db)):
    email = reqs.normalize_email(email)
    out = []
    for conv in conn.execute("SELECT * FROM conversations WHERE email = ? ORDER BY created_at DESC LIMIT 50",
                             (email,)).fetchall():
        msgs = conn.execute("SELECT role, content, assets_json, created_at FROM messages WHERE conversation_id = ?"
                            " ORDER BY id", (conv["id"],)).fetchall()
        out.append({"id": conv["id"], "set_id": conv["set_id"], "created_at": conv["created_at"],
                    "messages": [{"role": m["role"], "content": m["content"], "created_at": m["created_at"],
                                  "assets": json.loads(m["assets_json"]) if m["assets_json"] else None}
                                 for m in msgs]})
    return out


@router.get("/api/admin/stats", dependencies=[Depends(require_admin)])
def stats(request: Request, conn=Depends(app_db)):
    now = utcnow()
    day = day_key(now)
    questions = conn.execute("SELECT COALESCE(SUM(questions), 0) FROM usage_daily WHERE day = ?", (day,)).fetchone()[0]
    cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE substr(created_at, 1, 10) = ?",
                        (day,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM access_requests WHERE status = 'pending'").fetchone()[0]
    return {"questions_today": questions, "global_limit": settings_of(request).global_daily_limit,
            "cost_today_usd": round(cost, 4), "pending": pending}
