"""Transactional email via Resend; prints to the console when no API key is configured."""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from html import escape
from urllib.parse import quote

import httpx

from backend.config import Settings

log = logging.getLogger(__name__)
RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class SentEmail:
    to: str
    subject: str
    html: str
    text: str


class Mailer:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=10)
        self.outbox: deque[SentEmail] = deque(maxlen=50)

    def send(self, to: str, subject: str, html: str, text: str) -> bool:
        self.outbox.append(SentEmail(to, subject, html, text))
        if not self.settings.resend_api_key:
            print(f"\n===== EMAIL (console mode) =====\nTo: {to}\nSubject: {subject}\n\n{text}\n"
                  f"================================\n", flush=True)
            return True
        try:
            r = self.http.post(RESEND_URL, headers={"Authorization": f"Bearer {self.settings.resend_api_key}"},
                               json={"from": self.settings.email_from, "to": [to], "subject": subject,
                                     "html": html, "text": text})
            r.raise_for_status()
            return True
        except httpx.HTTPError as e:
            log.error("email to %s failed: %s", to, e)
            return False


def admin_notification(settings: Settings, email: str, name: str, company: str, note: str) -> tuple[str, str, str]:
    url = f"{settings.public_base_url}/admin"
    subject = f"Onesi access request: {email}"
    text = f"{name} ({company}) <{email}> requested demo access.\n\nNote: {note or '-'}\n\nReview: {url}"
    html = (f"<p><b>{escape(name)}</b> ({escape(company)}) &lt;{escape(email)}&gt; requested demo access.</p>"
            f"<p>Note: {escape(note or '-')}</p><p><a href=\"{url}\">Review requests</a></p>")
    return subject, html, text


def access_code_email(settings: Settings, email: str, code: str) -> tuple[str, str, str]:
    link = f"{settings.public_base_url}/demo?email={quote(email)}&code={code}"
    subject = "Your Onesi access code"
    text = (f"Your Onesi demo access code is {code}\n\nOpen the demo: {link}\n\n"
            f"The code works once and expires in 7 days.")
    html = (f"<p>Your Onesi demo access code is</p><p style=\"font-size:24px;letter-spacing:2px\"><b>{code}</b></p>"
            f"<p><a href=\"{link}\">Open the demo</a></p><p>The code works once and expires in 7 days.</p>")
    return subject, html, text
