"""Outbound email.

Velora has no bundled mail provider, so email is either configured (SMTP) or
unavailable. When it is unavailable the API says so plainly and account flows
report the exact next step instead of pretending a message was delivered.

Transports
----------
``smtp``  real delivery through an operator-provided SMTP relay.
``file``  development helper that writes ``.eml`` files to a local outbox so a
          developer can click verification links. Refused in production.
``none``  default. Explicitly unavailable.
"""

from __future__ import annotations

import json
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from .db import now_iso


@dataclass(frozen=True)
class SendResult:
    ok: bool
    transport: str
    detail: str
    stored_path: str | None = None

    def public(self) -> dict:
        payload = {"delivered": self.ok, "transport": self.transport, "detail": self.detail}
        if self.stored_path:
            payload["stored_path"] = self.stored_path
        return payload


class EmailService:
    def __init__(self, config):
        self.config = config

    # ---- status ---------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return self.config.email_configured

    def status(self) -> dict:
        if self.configured:
            return {"configured": True, "transport": self.config.email_transport, "reason": None}
        return {
            "configured": False,
            "transport": self.config.email_transport,
            "reason": (
                "Email delivery is not configured on this Velora instance, so verification and "
                "invitation messages cannot be sent."
            ),
        }

    # ---- sending --------------------------------------------------------------
    def send(self, to: str, subject: str, text_body: str, html_body: str | None = None) -> SendResult:
        if not self.configured:
            return SendResult(False, self.config.email_transport, "email transport is not configured")

        message = EmailMessage()
        sender_name, _, sender_email = self.config.email_from.partition("<")
        if sender_email:
            message["From"] = formataddr((sender_name.strip() or "Velora", sender_email.rstrip(">")))
        else:  # pragma: no cover - misconfigured operator
            message["From"] = self.config.email_from
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain="velora.local")
        message["Auto-Submitted"] = "auto-generated"
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

        if self.config.email_transport == "file":
            return self._write_to_outbox(message)
        return self._send_smtp(message)

    def _write_to_outbox(self, message: EmailMessage) -> SendResult:
        outbox = Path(self.config.email_outbox_path or "")
        outbox.mkdir(parents=True, exist_ok=True)
        stamp = now_iso().replace(":", "").replace("-", "")
        filename = f"{stamp}-{abs(hash(message['To'] or '')) % 10**6}.eml"
        path = outbox / filename
        path.write_bytes(message.as_bytes())
        # A small index makes the development outbox browsable.
        index_path = outbox / "index.jsonl"
        with index_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps({"to": message["To"], "subject": message["Subject"], "file": filename, "at": now_iso()})
                + "\n"
            )
        return SendResult(True, "file", "message written to the development outbox", str(path))

    def _send_smtp(self, message: EmailMessage) -> SendResult:
        host = self.config.smtp_host
        if not host:  # pragma: no cover - guarded by `configured`
            return SendResult(False, "smtp", "SMTP host is not configured")
        try:
            if self.config.smtp_port == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(host, self.config.smtp_port, timeout=15, context=context) as server:
                    self._authenticate(server)
                    server.send_message(message)
            else:
                with smtplib.SMTP(host, self.config.smtp_port, timeout=15) as server:
                    server.ehlo()
                    if self.config.smtp_starttls:
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    self._authenticate(server)
                    server.send_message(message)
        except Exception as exc:  # noqa: BLE001 - operator-facing diagnostics only
            # Never echo credentials: only the exception type and a short message.
            return SendResult(False, "smtp", f"SMTP delivery failed ({type(exc).__name__})")
        return SendResult(True, "smtp", "message accepted by the SMTP relay")

    def _authenticate(self, server: smtplib.SMTP) -> None:
        if self.config.smtp_username and self.config.smtp_password:
            server.login(self.config.smtp_username, self.config.smtp_password)

    # ---- templates ------------------------------------------------------------
    def send_verification(self, to: str, display_name: str, link: str, ttl_seconds: int) -> SendResult:
        hours = max(1, ttl_seconds // 3600)
        subject = "Confirm your Velora email"
        text = (
            f"Hi {display_name},\n\n"
            "Welcome to Velora. Confirm this email address to finish setting up your account:\n\n"
            f"{link}\n\n"
            f"This link works once and expires in {hours} hour{'s' if hours != 1 else ''}.\n"
            "If you did not create a Velora account, you can ignore this message.\n\n"
            "— Velora\n"
        )
        html = (
            f"<p>Hi {_escape(display_name)},</p>"
            "<p>Welcome to Velora. Confirm this email address to finish setting up your account:</p>"
            f'<p><a href="{_escape(link)}">Confirm my email</a></p>'
            f"<p>This link works once and expires in {hours} hour{'s' if hours != 1 else ''}. "
            "If you did not create a Velora account, you can ignore this message.</p>"
        )
        return self.send(to, subject, text, html)

    def send_invitation(self, to: str, role: str, link: str, ttl_seconds: int, inviter: str, note: str | None = None) -> SendResult:
        hours = max(1, ttl_seconds // 3600)
        role_label = "administrator" if role == "admin" else "creator"
        subject = f"Velora invitation: {role_label} access"
        note_block = f"\nMessage from {inviter}:\n{note}\n" if note else ""
        text = (
            f"{inviter} invited you to Velora with {role_label} access.\n"
            f"{note_block}\n"
            "Open this single-use link to review and accept the invitation:\n\n"
            f"{link}\n\n"
            f"The link expires in {hours} hour{'s' if hours != 1 else ''} and can only be used once.\n"
            "If you were not expecting this invitation, ignore this message and nothing changes.\n\n"
            "— Velora\n"
        )
        html = (
            f"<p>{_escape(inviter)} invited you to Velora with {role_label} access.</p>"
            + (f"<p>{_escape(note)}</p>" if note else "")
            + f'<p><a href="{_escape(link)}">Review the invitation</a></p>'
            + f"<p>The link expires in {hours} hour{'s' if hours != 1 else ''} and can only be used once.</p>"
        )
        return self.send(to, subject, text, html)


def _escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
