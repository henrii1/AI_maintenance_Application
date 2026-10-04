import json

import httpx

from backend.auth.email import Mailer, access_code_email, admin_notification
from backend.config import Settings


def test_console_mode_records_outbox(capsys):
    s = Settings(_env_file=None)
    m = Mailer(s)
    assert m.send("a@b.co", "Hi", "<p>x</p>", "x") is True
    assert m.outbox[-1].to == "a@b.co"
    assert "Subject: Hi" in capsys.readouterr().out


def test_resend_payload_and_failure():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "1"})

    s = Settings(_env_file=None, resend_api_key="re_test", email_from="Onesi <hi@onesi.test>")
    m = Mailer(s, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert m.send("a@b.co", "Subj", "<b>h</b>", "t")
    assert seen["auth"] == "Bearer re_test"
    assert seen["body"] == {"from": "Onesi <hi@onesi.test>", "to": ["a@b.co"], "subject": "Subj",
                            "html": "<b>h</b>", "text": "t"}
    bad = Mailer(s, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert bad.send("a@b.co", "Subj", "h", "t") is False


def test_refused_email_is_printed_for_local_testing(capsys):
    # Without a verified domain Resend refuses non-owner recipients (403); the code must still be recoverable.
    refuse = httpx.MockTransport(lambda r: httpx.Response(
        403, json={"message": "You can only send testing emails to your own email address"}))
    s = Settings(_env_file=None, resend_api_key="re_test")
    m = Mailer(s, http=httpx.Client(transport=refuse))
    assert m.send("second@x.co", "Your Onesi access code", "<p>K7QM-4XPD</p>", "Code K7QM-4XPD") is False
    out = capsys.readouterr().out
    assert "NOT DELIVERED" in out and "second@x.co" in out and "K7QM-4XPD" in out
    assert "only send testing emails" in out


def test_templates():
    s = Settings(_env_file=None, public_base_url="https://onesi.example")
    subj, html, text = access_code_email(s, "t@x.co", "K7QM-4XPD")
    assert "K7QM-4XPD" in text and "https://onesi.example/demo?email=t%40x.co&code=K7QM-4XPD" in html
    subj, html, text = admin_notification(s, "t@x.co", "Tess <b>", "Acme", "pumps")
    assert "t@x.co" in subj and "https://onesi.example/admin" in text and "&lt;b&gt;" in html
