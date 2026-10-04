from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SET_DIR = ROOT / "corpus" / "ariel-kbe"
MANUAL_PDF = SET_DIR / "docs" / "Ariel-KBE-Manual.pdf"


import pytest


@pytest.fixture
def settings(tmp_path):
    from backend.config import Settings
    return Settings(_env_file=None, data_dir=tmp_path / "data", content_dir=tmp_path / "content",
                    web_dir=ROOT / "web", admin_password="pw", admin_email="admin@onesi.test",
                    session_secret="test-secret", daily_question_limit=3, global_daily_limit=100)


@pytest.fixture
def make_client(settings):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.auth.email import Mailer

    def _make(**kwargs):
        kwargs.setdefault("mailer", Mailer(settings))
        return TestClient(create_app(settings, **kwargs))
    return _make


def approve_in_db(settings, email: str) -> str:
    from backend.auth import codes, requests as reqs
    from backend.db import connect_app, utcnow
    conn = connect_app(settings.data_dir / "app.db")
    reqs.create_request(conn, email, "Tess", "Acme", "", utcnow())
    reqs.set_status(conn, email, "approved", utcnow())
    return codes.issue_code(conn, email, utcnow())


def login(client, settings, email: str = "tech@example.com") -> None:
    code = approve_in_db(settings, email)
    r = client.post("/api/access/redeem", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
