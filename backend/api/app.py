"""Application factory: run with `uvicorn backend.api.app:create_app --factory`."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.auth.email import Mailer
from backend.auth.ratelimit import RateLimiter
from backend.config import Settings, get_settings
from backend.db import connect_app


@dataclass
class Limiters:
    request_ip: RateLimiter = field(default_factory=lambda: RateLimiter(5, 3600))
    new_code_email: RateLimiter = field(default_factory=lambda: RateLimiter(3, 3600))
    admin_login_ip: RateLimiter = field(default_factory=lambda: RateLimiter(5, 900))


def create_app(settings: Settings | None = None, *, retriever=None, llm=None, mailer=None, embedder=None) -> FastAPI:
    from backend.api import access  # local imports avoid cycles with deps

    settings = settings or get_settings()
    settings.check_production()
    app = FastAPI(title="Onesi", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.mailer = mailer or Mailer(settings)
    app.state.limiters = Limiters()
    app.state.retriever = retriever
    app.state.llm = llm
    app.state.embedder = embedder
    connect_app(Path(settings.data_dir) / "app.db").close()  # run migrations at startup

    app.include_router(access.router)
    for name in ("admin", "assets", "chat"):  # routers added by later tasks
        try:
            module = __import__(f"backend.api.{name}", fromlist=["router"])
        except ModuleNotFoundError:
            continue
        app.include_router(module.router)

    web = Path(settings.web_dir)
    content = Path(settings.content_dir)

    @app.get("/healthz")
    def healthz():
        content_ok = (content / "content.db").is_file()
        try:
            connect_app(Path(settings.data_dir) / "app.db").close()
            db_ok = True
        except Exception:
            db_ok = False
        return {"ok": content_ok and db_ok, "content": content_ok, "app_db": db_ok}

    @app.get("/demo", include_in_schema=False)
    def demo_page():
        return FileResponse(web / "demo.html")

    @app.get("/admin", include_in_schema=False)
    def admin_page():
        return FileResponse(web / "admin.html")

    app.mount("/content/figures", StaticFiles(directory=content / "figures", check_dir=False), name="figures")
    app.mount("/content/cad", StaticFiles(directory=content / "cad", check_dir=False), name="cad")
    app.mount("/", StaticFiles(directory=web, html=True), name="site")
    return app
