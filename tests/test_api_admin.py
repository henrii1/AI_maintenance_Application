from backend.db import connect_app, iso, utcnow
from tests.conftest import login


def admin(c):
    assert c.post("/api/admin/login", json={"password": "pw"}).status_code == 200


def test_login_wrong_password_and_rate_limit(make_client):
    c = make_client()
    assert c.get("/api/admin/requests").status_code == 401
    codes = [c.post("/api/admin/login", json={"password": "nope"}).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]


def test_approve_emails_code_that_redeems(make_client):
    c = make_client()
    c.post("/api/access/request", json={"email": "tech@example.com", "name": "Tess", "company": "Acme"})
    admin(c)
    pending = c.get("/api/admin/requests?status=pending").json()
    assert [r["email"] for r in pending] == ["tech@example.com"]
    assert c.post("/api/admin/requests/tech@example.com/approve").json()["status"] == "approved"
    mail = c.app.state.mailer.outbox[-1]
    assert mail.to == "tech@example.com"
    code = mail.text.split("access code is ")[1].split()[0]
    c.post("/api/admin/logout")
    assert c.post("/api/access/redeem", json={"email": "tech@example.com", "code": code}).status_code == 200


def test_reject_revoke_restore(make_client, settings):
    c = make_client()
    login(c, settings, "tech@example.com")
    assert c.get("/api/me").status_code == 200
    a = make_client()
    admin(a)
    assert a.post("/api/admin/requests/tech@example.com/revoke").json()["status"] == "revoked"
    assert c.get("/api/me").status_code == 401          # sessions deleted
    closed = a.get("/api/admin/requests?status=closed").json()
    assert closed[0]["email"] == "tech@example.com"
    assert a.post("/api/admin/requests/tech@example.com/restore").json()["status"] == "pending"
    assert a.post("/api/admin/requests/nobody@x.com/approve").status_code == 404
    assert a.post("/api/admin/requests/tech@example.com/explode").status_code == 404


def test_limit_quota_reset_resend_stats(make_client, settings):
    c = make_client()
    login(c, settings, "tech@example.com")
    conn = connect_app(settings.data_dir / "app.db")
    conn.execute("INSERT INTO usage_daily (email, day, questions) VALUES ('tech@example.com', ?, 2)",
                 (utcnow().date().isoformat(),))
    conn.execute("INSERT INTO llm_calls (conversation_id, email, call_type, model, input_tokens, output_tokens,"
                 " cache_read_tokens, web_searches, cost_usd, created_at) VALUES ('c','tech@example.com','chat',"
                 "'claude-haiku-4-5',1,1,0,0,0.0123,?)", (iso(utcnow()),))
    a = make_client()
    admin(a)
    assert a.patch("/api/admin/requests/tech@example.com", json={"daily_limit": 20}).status_code == 200
    assert c.get("/api/me").json()["remaining"] == 18
    a.post("/api/admin/requests/tech@example.com/reset-quota")
    assert c.get("/api/me").json()["remaining"] == 20
    before = len(a.app.state.mailer.outbox)
    a.post("/api/admin/requests/tech@example.com/resend-code")
    assert len(a.app.state.mailer.outbox) == before + 1
    stats = a.get("/api/admin/stats").json()
    assert stats["cost_today_usd"] == 0.0123 and stats["global_limit"] == 100 and stats["pending"] == 0
    assert a.get("/api/admin/requests/tech@example.com/conversations").json() == []
    assert a.get("/api/me").json()["is_admin"] is True


def test_approve_records_email_failure(make_client):
    from backend.auth.email import Mailer

    class Failing(Mailer):
        def send(self, *a, **k):
            return False

    c = make_client(mailer=None)
    c.app.state.mailer = Failing(c.app.state.settings)
    c.post("/api/access/request", json={"email": "t@x.com", "name": "T", "company": "C"})
    admin(c)
    row = c.post("/api/admin/requests/t@x.com/approve").json()
    assert row["status"] == "approved" and row["email_error"] == "Email delivery failed (Resend)."
