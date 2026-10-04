from tests.conftest import approve_in_db, login


def test_healthz_and_site(make_client):
    c = make_client()
    assert c.get("/healthz").json()["app_db"] is True
    assert c.get("/").status_code == 200
    assert "ONESI" in c.get("/index.html").text


def test_healthz_503_until_content_built(make_client, settings):
    from tests.content_fixture import build_tiny_content
    r = make_client().get("/healthz")
    assert r.status_code == 503 and r.json()["content"] is False
    build_tiny_content(settings.content_dir)
    r = make_client().get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_request_access_notifies_admin_once(make_client):
    c = make_client()
    body = {"email": "tech@example.com", "name": "Tess", "company": "Acme", "note": "pumps"}
    assert c.post("/api/access/request", json=body).json() == {"ok": True}
    assert c.post("/api/access/request", json=body).json() == {"ok": True}
    outbox = c.app.state.mailer.outbox
    assert len(outbox) == 1 and outbox[0].to == "admin@onesi.test"


def test_honeypot_and_invalid_email(make_client):
    c = make_client()
    r = c.post("/api/access/request", json={"email": "bot@x.com", "name": "b", "company": "b", "website": "spam"})
    assert r.json() == {"ok": True} and len(c.app.state.mailer.outbox) == 0
    r = c.post("/api/access/request", json={"email": "not-an-email", "name": "b", "company": "b"})
    assert r.status_code == 422


def test_request_rate_limited_per_ip(make_client):
    c = make_client()
    codes = [c.post("/api/access/request", json={"email": f"u{i}@x.com", "name": "n", "company": "c"}).status_code
             for i in range(6)]
    assert codes == [200] * 5 + [429]


def test_email_normalized_across_flow(make_client, settings):
    c = make_client()
    c.post("/api/access/request", json={"email": " Tech@Example.com", "name": "Tess", "company": "Acme"})
    code = approve_in_db(settings, "tech@example.com")
    r = c.post("/api/access/redeem", json={"email": "TECH@example.COM ", "code": code})
    assert r.status_code == 200
    assert c.get("/api/me").json()["email"] == "tech@example.com"


def test_redeem_sets_cookie_and_me(make_client, settings):
    c = make_client()
    assert c.get("/api/me").status_code == 401
    login(c, settings)
    me = c.get("/api/me").json()
    assert me == {"email": "tech@example.com", "is_admin": False, "remaining": 3, "limit": 3}
    assert "onesi_session" in c.cookies


def test_bad_code_is_generic(make_client, settings):
    c = make_client()
    approve_in_db(settings, "tech@example.com")
    r = c.post("/api/access/redeem", json={"email": "tech@example.com", "code": "AAAA-AAAA"})
    assert r.status_code == 400 and r.json()["detail"] == "That code is invalid or has expired."
    r = c.post("/api/access/redeem", json={"email": "nobody@example.com", "code": "AAAA-AAAA"})
    assert r.status_code == 400 and r.json()["detail"] == "That code is invalid or has expired."


def test_new_code_only_emails_approved_users(make_client, settings):
    c = make_client()
    c.post("/api/access/request", json={"email": "pending@x.com", "name": "P", "company": "C"})
    before = len(c.app.state.mailer.outbox)
    assert c.post("/api/access/new-code", json={"email": "pending@x.com"}).json() == {"ok": True}
    assert len(c.app.state.mailer.outbox) == before
    approve_in_db(settings, "ok@x.com")
    assert c.post("/api/access/new-code", json={"email": "OK@x.com"}).json() == {"ok": True}
    last = c.app.state.mailer.outbox[-1]
    assert last.to == "ok@x.com" and "Your Onesi access code" in last.subject


def test_logout(make_client, settings):
    c = make_client()
    login(c, settings)
    c.post("/api/access/logout")
    assert c.get("/api/me").status_code == 401


def test_production_misconfig_refuses_start(settings):
    import pytest

    from backend.api.app import create_app
    bad = settings.model_copy(update={"public_base_url": "https://x.example", "anthropic_api_key": ""})
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        create_app(bad)


def test_spoofed_forwarded_for_still_rate_limited(make_client):
    import random
    import string
    c = make_client()
    codes = []
    for i in range(6):
        random_ip = ".".join(str(random.randint(0, 255)) for _ in range(4))
        r = c.post("/api/access/request", json={"email": f"u{i}@x.com", "name": "n", "company": "c"},
                   headers={"X-Forwarded-For": f"{random_ip}, 203.0.113.9"})
        codes.append(r.status_code)
    assert codes == [200] * 5 + [429]


def test_session_cookie_flags(make_client, settings):
    from backend.api.app import create_app
    from backend.auth.email import Mailer
    from fastapi.testclient import TestClient

    # HTTP settings (secure_cookies = False)
    c = make_client()
    code = approve_in_db(settings, "tech@example.com")
    r = c.post("/api/access/redeem", json={"email": "tech@example.com", "code": code})
    assert r.status_code == 200
    cookie_header = r.headers.get("set-cookie")
    assert cookie_header is not None
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Secure" not in cookie_header

    # HTTPS settings (secure_cookies = True)
    https_settings = settings.model_copy(update={"public_base_url": "https://x.example", "anthropic_api_key": "k"})
    https_client = TestClient(create_app(https_settings, mailer=Mailer(https_settings)))
    code = approve_in_db(https_settings, "tech@example.com")
    r = https_client.post("/api/access/redeem", json={"email": "tech@example.com", "code": code})
    assert r.status_code == 200
    cookie_header = r.headers.get("set-cookie")
    assert cookie_header is not None
    assert "Secure" in cookie_header


def test_forged_or_expired_admin_cookie_rejected(make_client, settings, monkeypatch):
    from itsdangerous import TimestampSigner

    c = make_client()

    # Test forged cookie (signed with different secret)
    forged = TimestampSigner("wrong", salt="onesi-admin").sign(b"admin").decode()
    c.cookies.set("onesi_admin", forged)
    r = c.get("/api/me")
    assert r.status_code == 401

    # Test expired cookie (signed with right secret but max_age = -1)
    monkeypatch.setattr("backend.api.deps.ADMIN_MAX_AGE", -1)
    from backend.api.deps import admin_signer
    valid_cookie = admin_signer(settings).sign(b"admin").decode()
    c.cookies.set("onesi_admin", valid_cookie)
    r = c.get("/api/me")
    assert r.status_code == 401

    # Test valid fresh cookie
    monkeypatch.setattr("backend.api.deps.ADMIN_MAX_AGE", 12 * 3600)
    valid_cookie = admin_signer(settings).sign(b"admin").decode()
    c.cookies.set("onesi_admin", valid_cookie)
    r = c.get("/api/me")
    assert r.status_code == 200
    assert r.json()["is_admin"] is True


def test_known_and_unknown_emails_get_identical_responses(make_client, settings):
    c = make_client()

    # Request access: existing vs new email
    r1 = c.post("/api/access/request", json={"email": "tech@example.com", "name": "Tess", "company": "Acme"})
    r2 = c.post("/api/access/request", json={"email": "tech@example.com", "name": "Tess", "company": "Acme"})
    r3 = c.post("/api/access/request", json={"email": "other@example.com", "name": "Other", "company": "Corp"})
    assert r1.status_code == r3.status_code == 200
    assert r1.json() == r3.json()

    # New-code: approved vs unknown email
    approve_in_db(settings, "approved@x.com")
    r_approved = c.post("/api/access/new-code", json={"email": "approved@x.com"})
    r_unknown = c.post("/api/access/new-code", json={"email": "unknown@x.com"})
    assert r_approved.status_code == r_unknown.status_code == 200
    assert r_approved.json() == r_unknown.json()
