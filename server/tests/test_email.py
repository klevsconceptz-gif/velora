"""Email delivery: transports, honesty when unconfigured, and secret hygiene.

Velora never claims a message was delivered when it was not. These tests cover the
development outbox, a real SMTP conversation against a throwaway local server, and
the production rule that the file transport is refused.
"""

from __future__ import annotations

import socketserver
import sys
import threading
import unittest
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

from server.emaillib import EmailService  # noqa: E402
from server.tests.harness import Client, VeloraTestCase  # noqa: E402


class _SmtpHandler(socketserver.StreamRequestHandler):
    """The smallest SMTP conversation a relay needs to accept a message."""

    def handle(self):
        self.wfile.write(b"220 velora-test ESMTP\r\n")
        message_lines: list[bytes] = []
        in_data = False
        while True:
            line = self.rfile.readline()
            if not line:
                break
            if in_data:
                if line.strip() == b".":
                    in_data = False
                    self.server.messages.append(b"".join(message_lines))
                    self.wfile.write(b"250 2.0.0 Ok: queued\r\n")
                else:
                    message_lines.append(line)
                continue
            command = line.strip().upper()
            if command.startswith(b"EHLO"):
                self.wfile.write(b"250-velora-test\r\n250 AUTH PLAIN LOGIN\r\n")
            elif command.startswith(b"HELO"):
                self.wfile.write(b"250 velora-test\r\n")
            elif command.startswith(b"AUTH"):
                self.wfile.write(b"235 2.7.0 Authentication successful\r\n")
            elif command.startswith(b"MAIL FROM") or command.startswith(b"RCPT TO"):
                self.wfile.write(b"250 2.1.0 Ok\r\n")
            elif command.startswith(b"DATA"):
                in_data = True
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
            elif command.startswith(b"QUIT"):
                self.wfile.write(b"221 2.0.0 Bye\r\n")
                break
            else:
                self.wfile.write(b"250 2.0.0 Ok\r\n")


class _SmtpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.messages: list[bytes] = []


class SmtpTransportTests(VeloraTestCase):
    """A real (local) SMTP round trip: no provider, no network, no mock."""

    email_transport = "smtp"

    def setUp(self):
        super().setUp()
        self.server = _SmtpServer(("127.0.0.1", 0), _SmtpHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]
        self.config = replace(
            self.config,
            smtp_host="127.0.0.1",
            smtp_port=self.port,
            smtp_starttls=False,
            smtp_username=None,
            smtp_password=None,
            email_from="Velora <no-reply@velora.test>",
        )
        self.email = EmailService(self.config)

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_message_is_accepted_by_the_relay(self):
        result = self.email.send("member@velora.test", "Hello", "Plain body")
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.transport, "smtp")
        self.assertEqual(len(self.server.messages), 1)
        raw = self.server.messages[0]
        self.assertIn(b"To: member@velora.test", raw)
        self.assertIn(b"Subject: Hello", raw)
        self.assertIn(b"Auto-Submitted: auto-generated", raw)

    def test_verification_links_are_sent_verbatim_and_never_logged_as_secrets(self):
        result = self.email.send_verification(
            "member@velora.test", "Member", "https://velora.test/#/verify?token=abc123", 86400
        )
        self.assertTrue(result.ok, result.detail)
        message = self._decoded_message()
        self.assertIn("https://velora.test/#/verify?token=abc123", message.get_body("plain").get_content())
        self.assertIn("https://velora.test/#/verify?token=abc123", message.get_body("html").get_content())
        self.assertNotIn(b"abc123", result.detail.encode())

    def _decoded_message(self):
        from email import message_from_bytes
        from email import policy

        return message_from_bytes(self.server.messages[-1], policy=policy.default)

    def test_html_and_text_alternatives_are_both_present(self):
        self.email.send("member@velora.test", "Both", "Text version", "<p>HTML version</p>")
        message = self._decoded_message()
        self.assertEqual(message["Subject"], "Both")
        self.assertEqual(message.get_body("plain").get_content().strip(), "Text version")
        self.assertIn("HTML version", message.get_body("html").get_content())

    def test_relay_failure_is_reported_without_credentials(self):
        broken = replace(self.config, smtp_port=1, smtp_username="user@velora.test",
                         smtp_password="hunter2-should-never-appear")
        result = EmailService(broken).send("member@velora.test", "Nope", "Body")
        self.assertFalse(result.ok)
        self.assertEqual(result.transport, "smtp")
        self.assertNotIn("hunter2-should-never-appear", result.detail)
        self.assertIn("SMTP delivery failed", result.detail)


class OutboxTransportTests(VeloraTestCase):
    def test_dev_outbox_writes_an_eml_file_and_an_index(self):
        service = EmailService(self.config)
        result = service.send("dev@velora.test", "Development message", "Body text")
        self.assertTrue(result.ok, result.detail)
        self.assertEqual(result.transport, "file")
        written = Path(result.stored_path or "")
        self.assertTrue(written.is_file(), result.stored_path)
        self.assertIn(b"Development message", written.read_bytes())
        index = (self.outbox / "index.jsonl").read_text(encoding="utf-8")
        self.assertIn("dev@velora.test", index)

    def test_signup_reports_email_delivery_state_and_next_step(self):
        client = Client(self.app)
        response = client.post("/api/auth/signup", json_body={
            "display_name": "Outbox Person", "email": "outbox@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        })
        self.assertEqual(response.status, 201, response.text)
        self.assertTrue(response.json["verification"]["sent"])
        self.assertIn("confirmation link", response.json["next_step"])
        self.assertIn("self-attestation", response.json["attestation_notice"])
        self.assertTrue(self.latest_email())

    def test_bootstrap_never_exposes_smtp_details(self):
        payload = Client(self.app).get("/api/bootstrap").json
        status = payload["email_status"]
        self.assertTrue(status["configured"])
        self.assertEqual(status["transport"], "file")
        for forbidden in ("smtp_password", "smtp_username", "smtp_host", "api_key",
                          "test-secret-key-not-used-outside-tests"):
            self.assertNotIn(forbidden, Client(self.app).get("/api/bootstrap").text)


class UnconfiguredEmailTests(VeloraTestCase):
    email_transport = "none"

    def test_status_says_unavailable_with_a_reason(self):
        payload = Client(self.app).get("/api/bootstrap").json
        status = payload["email_status"]
        self.assertFalse(status["configured"])
        self.assertFalse(payload["features"]["email_configured"])
        self.assertIn("not configured", status["reason"])

    def test_sending_reports_failure_instead_of_pretending(self):
        result = EmailService(self.config).send("nobody@velora.test", "Subject", "Body")
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "email transport is not configured")
        self.assertFalse(result.public()["delivered"])

    def test_signup_still_succeeds_but_reports_the_operator_step(self):
        client = Client(self.app)
        response = client.post("/api/auth/signup", json_body={
            "display_name": "No Mail", "email": "nomail@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        })
        self.assertEqual(response.status, 201, response.text)
        self.assertFalse(response.json["verification"]["sent"])
        self.assertIn("cannot send email", response.json["next_step"])
        self.assertEqual(self.read_outbox(), [])

    def test_resend_is_rate_limited_and_does_not_leak_delivery_state(self):
        client = Client(self.app)
        client.post("/api/auth/signup", json_body={
            "display_name": "Unverified", "email": "unverified@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        })
        known = client.post("/api/auth/resend-verification",
                            json_body={"email": "unverified@velora.test"})
        unknown = client.post("/api/auth/resend-verification",
                              json_body={"email": "nobody@velora.test"})
        self.assertEqual(known.status, 200, known.text)
        self.assertEqual(unknown.status, 200, unknown.text)
        # The two responses must be indistinguishable apart from the masked address.
        for payload in (known.json, unknown.json):
            self.assertTrue(payload["accepted"])
            self.assertFalse(payload["sent"])
            self.assertEqual(payload["unavailable"], "email_not_configured")
            self.assertIn("operator", payload["notice"])
        self.assertEqual(set(known.json) - {"masked_email"}, set(unknown.json) - {"masked_email"})
        self.assertEqual(self.read_outbox(), [])


class ProductionEmailRulesTests(VeloraTestCase):
    environment = "production"
    email_transport = "none"

    def test_the_development_outbox_cannot_be_enabled_in_production(self):
        from server.config import build_config

        with self.assertRaises(RuntimeError):
            build_config({**self.environ, "VELORA_EMAIL_TRANSPORT": "file",
                          "VELORA_ENV": "production"})
        self.assertFalse(self.config.email_configured,
                         "production must never fall back to the development outbox")
        self.assertFalse(EmailService(self.config).status()["configured"])

    def test_verification_is_required_before_protected_actions(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="prod-creator@velora.test", handle="prod-page")

        # Signed up through the API and deliberately left unconfirmed: with no email
        # delivery there is no way to confirm, and checkout must refuse.
        member = Client(self.app)
        signup = member.post("/api/auth/signup", json_body={
            "display_name": "Prod Member", "email": "prod-member@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        })
        self.assertEqual(signup.status, 201, signup.text)
        self.assertEqual(member.login("prod-member@velora.test", "correct-horse-9").status, 200)
        self.assertFalse(member.get("/api/auth/session").json["user"]["email_verified"])
        checkout = member.post("/api/payments/intents", json_body={"tier_id": seed["tier_id"]})
        self.assertEqual(checkout.status, 403, checkout.text)
        self.assertEqual(checkout.json["error"]["code"], "email_verification_required")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
