"""FastAPI dependencies: settings, DB connections, viewer identity, retriever."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, TimestampSigner

from backend.auth.sessions import session_email
from backend.config import Settings
from backend.db import connect_app, utcnow
from backend.embeddings import get_embedder
from backend.retrieval.search import Retriever

SESSION_COOKIE = "onesi_session"
ADMIN_COOKIE = "onesi_admin"
ADMIN_MAX_AGE = 12 * 3600
_retriever_lock = threading.Lock()


@dataclass(frozen=True)
class Viewer:
    email: str
    is_admin: bool


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def app_db(request: Request):
    conn = connect_app(settings_of(request).data_dir / "app.db")
    try:
        yield conn
    finally:
        conn.close()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")


def admin_signer(settings: Settings) -> TimestampSigner:
    return TimestampSigner(settings.session_secret, salt="onesi-admin")


def is_admin(request: Request) -> bool:
    raw = request.cookies.get(ADMIN_COOKIE)
    if not raw:
        return False
    try:
        return admin_signer(settings_of(request)).unsign(raw, max_age=ADMIN_MAX_AGE) == b"admin"
    except BadSignature:
        return False


def current_viewer(request: Request, conn) -> Viewer | None:
    if is_admin(request):
        return Viewer("admin", True)
    token = request.cookies.get(SESSION_COOKIE)
    email = session_email(conn, token, utcnow()) if token else None
    return Viewer(email, False) if email else None


def require_viewer(request: Request, conn=Depends(app_db)) -> Viewer:
    viewer = current_viewer(request, conn)
    if viewer is None:
        raise HTTPException(401, "Sign in to use the demo.")
    return viewer


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(401, "Admin login required.")


def set_cookie(response, settings: Settings, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, httponly=True, samesite="lax",
                        secure=settings.secure_cookies, path="/")


def get_retriever(request: Request) -> Retriever:
    state = request.app.state
    if state.retriever is None:
        with _retriever_lock:
            if state.retriever is None:
                s = state.settings
                state.retriever = Retriever.open(s.content_dir, state.embedder or get_embedder(s.embed_cache_dir))
    return state.retriever
