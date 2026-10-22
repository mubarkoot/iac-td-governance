"""Stakeholder notification (Step 5): stdout, generic JSON webhook, e-mail."""

from __future__ import annotations

import json
import os
import smtplib
import sys
from email.message import EmailMessage

import requests


class Notifier:
    def __init__(self, cfg):
        self.nc = cfg.notify
        self.blueprint = cfg.blueprint_name

    def send(self, subject: str, body: str, severity: str = "info", payload: dict | None = None) -> list[str]:
        sent, errors = [], []
        for ch in self.nc.channels:
            try:
                getattr(self, f"_{ch}")(subject, body, severity, payload or {})
                sent.append(ch)
            except Exception as exc:  # notification must never break auditing
                errors.append(f"{ch}: {exc}")
        for e in errors:
            print(f"[tdgov] notification error: {e}", file=sys.stderr)
        return sent

    def _stdout(self, subject, body, severity, payload):
        print(f"[tdgov:{severity}] {subject}\n{body}")

    def _webhook(self, subject, body, severity, payload):
        url = os.environ.get(self.nc.webhook_url_env)
        if not url:
            raise RuntimeError(f"{self.nc.webhook_url_env} not set")
        # "text" works for Slack/Mattermost/Teams incoming webhooks; the full
        # payload is attached for programmatic consumers.
        msg = {"text": f"*[{severity.upper()}] {subject}*\n{body}", "blueprint": self.blueprint,
               "severity": severity, "details": payload}
        r = requests.post(url, data=json.dumps(msg, default=str), headers={"Content-Type": "application/json"}, timeout=10)
        r.raise_for_status()

    def _email(self, subject, body, severity, payload):
        if not (self.nc.smtp_host and self.nc.email_from and self.nc.email_to):
            raise RuntimeError("smtp_host, email_from and email_to must be configured")
        msg = EmailMessage()
        msg["Subject"] = f"[tdgov:{severity}] {subject}"
        msg["From"] = self.nc.email_from
        msg["To"] = ", ".join(self.nc.email_to)
        msg.set_content(body)
        with smtplib.SMTP(self.nc.smtp_host, self.nc.smtp_port, timeout=15) as s:
            s.starttls()
            user, pw = os.environ.get(self.nc.smtp_user_env), os.environ.get(self.nc.smtp_password_env)
            if user and pw:
                s.login(user, pw)
            s.send_message(msg)
