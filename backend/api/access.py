"""Visitor-facing access endpoints: request access, redeem a code, get a new code, sign out, who am I."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.api.deps import SESSION_COOKIE, Viewer, app_db, client_ip, require_viewer, set_cookie, settings_of
from backend.auth import codes, quota, requests as reqs, sessions
from backend.auth.email import access_code_email, admin_notification
from backend.db import utcnow

router = APIRouter()
INVALID_CODE = "That code is invalid or has expired."


class AccessRequestIn(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(min_length=1, max_length=120)
    company: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=1000)
    website: str = ""  # honeypot: humans never see this field


class RedeemIn(BaseModel):
    email: str = Field(max_length=254)
    code: str = Field(max_length=32)


class EmailIn(BaseModel):
    email: str = Field(max_length=254)


@router.post("/api/access/request")
def request_access(body: AccessRequestIn, request: Request, conn=Depends(app_db)):
    if body.website:
        return {"ok": True}
    email = reqs.normalize_email(body.email)
    if not reqs.valid_email(email):
        raise HTTPException(422, "Enter a valid email address.")
    if not request.app.state.limiters.request_ip.hit(client_ip(request)):
        raise HTTPException(429, "Too many requests. Try again later.")
    settings = settings_of(request)
    if reqs.create_request(conn, email, body.name, body.company, body.note, utcnow()) and settings.admin_email:
        request.app.state.mailer.send(settings.admin_email,
                                      *admin_notification(settings, email, body.name, body.company, body.note))
    return {"ok": True}


@router.post("/api/access/redeem")
def redeem(body: RedeemIn, request: Request, response: Response, conn=Depends(app_db)):
    email = reqs.normalize_email(body.email)
    result = codes.redeem_code(conn, email, body.code, client_ip(request), utcnow())
    if result is not codes.RedeemResult.OK:
        raise HTTPException(400, INVALID_CODE)
    token = sessions.create_session(conn, email, utcnow())
    set_cookie(response, settings_of(request), SESSION_COOKIE, token, int(sessions.SESSION_TTL.total_seconds()))
    return {"ok": True, "email": email}


@router.post("/api/access/new-code")
def new_code(body: EmailIn, request: Request, conn=Depends(app_db)):
    email = reqs.normalize_email(body.email)
    if not request.app.state.limiters.new_code_email.hit(email):
        return {"ok": True}
    req = reqs.get_request(conn, email)
    if req is not None and req["status"] == "approved":
        code = codes.issue_code(conn, email, utcnow())
        request.app.state.mailer.send(email, *access_code_email(settings_of(request), email, code))
    return {"ok": True}


@router.post("/api/access/logout")
def logout(request: Request, response: Response, conn=Depends(app_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sessions.delete_session(conn, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/api/me")
def me(request: Request, viewer: Viewer = Depends(require_viewer), conn=Depends(app_db)):
    st = quota.status(conn, viewer.email, settings_of(request), utcnow(), is_admin=viewer.is_admin)
    return {"email": viewer.email, "is_admin": viewer.is_admin, "remaining": st.remaining, "limit": st.limit}
