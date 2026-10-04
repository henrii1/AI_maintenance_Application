from tests.conftest import approve_in_db, login


def test_healthz_and_site(make_client):
    c = make_client()
    assert c.get("/healthz").json()["app_db"] is True
    assert c.get("/").status_code == 200
    assert "ONESI" in c.get("/index.html").text


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
