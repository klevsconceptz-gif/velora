"""Test harness: isolated database, fake BTCPay transport, WSGI test client.

No real credentials are ever involved. Email uses the ``file`` transport writing
into a temporary outbox, and BTCPay is replaced by an in-process fake that the
tests drive explicitly.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ..config import build_config
from ..db import Database, now_iso
from ..migrations import apply_all
from ..services.context import build_context
from ..app import Velora

WEB_ROOT = Path(__file__).resolve().parent.parent.parent / "web"


class FakeBtcPay:
    """In-process stand-in for a hosted BTCPay Server.

    Tests configure the invoice payloads explicitly, so every settlement path —
    settled, partial, overpaid, late, offline, non-on-chain, mismatched — can be
    exercised without touching a network.
    """

    def __init__(self):
        self.invoices: dict[str, dict] = {}
        self.created: list[dict] = []
        self.next_id = 1
        self.fail_next: str | None = None

    def create_invoice(self, *, amount_cents: int, order_ref: str, item_description: str,
                       redirect_url: str, ttl_minutes: int) -> dict:
        if self.fail_next == "create":
            self.fail_next = None
            from ..btcpay import BtcPayError

            raise BtcPayError("fake transport failure", kind="transport")
        invoice_id = f"FAKE-INV-{self.next_id}"
        self.next_id += 1
        sats = int(round(amount_cents / 100 / 60_000 * 10**8))  # pretend BTC = $60k
        payload = {
            "id": invoice_id,
            "status": "New",
            "amount": f"{amount_cents / 100:.2f}",
            "currency": "USD",
            "rate": "60000.00",
            "checkoutLink": f"https://btcpay.test/i/{invoice_id}",
            "paymentMethods": [
                {
                    "paymentMethod": "BTC-CHAIN",
                    "destination": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
                    "due": f"{sats / 10**8:.8f}",
                    "rate": "60000.00",
                }
            ],
            "metadata": {"orderRef": order_ref, "orderId": order_ref,
                         "itemDesc": item_description, "platform": "velora"},
            "payments": [],
            "additionalStatus": None,
            "checkout": {"redirectURL": redirect_url},
        }
        self.invoices[invoice_id] = payload
        self.created.append(payload)
        return payload

    def get_invoice(self, invoice_id: str) -> dict:
        if self.fail_next == "get":
            self.fail_next = None
            from ..btcpay import BtcPayError

            raise BtcPayError("fake transport failure", kind="transport")
        if invoice_id not in self.invoices:
            from ..btcpay import BtcPayError

            raise BtcPayError("invoice not found", kind="api_error", status=404)
        return self.invoices[invoice_id]

    # ---- helpers used by tests ------------------------------------------------
    def settle(self, invoice_id: str, *, sats: int | None = None, confirmations: int = 2,
               method: str = "BTC-CHAIN", raw_amount: str | None = None, extra_payment: dict | None = None,
               additional_status: str | None = None, status: str = "Settled") -> dict:
        invoice = self.invoices[invoice_id]
        target_sats = sats if sats is not None else int(
            round(float(invoice["amount"]) / 60_000 * 10**8))
        invoice["status"] = status
        invoice["additionalStatus"] = additional_status
        amount_btc = raw_amount or f"{target_sats / 10**8:.8f}"
        payment = {
            "status": "Settled",
            "amount": amount_btc,
            "currency": "BTC",
            "method": method,
            "confirmations": confirmations,
            "settledTime": "2026-01-01T00:00:00Z",
        }
        invoice["payments"] = [payment]
        if extra_payment:
            invoice["payments"].append(extra_payment)
        return invoice

    def mark_status(self, invoice_id: str, status: str) -> dict:
        self.invoices[invoice_id]["status"] = status
        return self.invoices[invoice_id]


class FakeTransport:
    """Feeds :class:`FakeBtcPay` responses into :class:`BtcPayClient`."""

    def __init__(self, fake: FakeBtcPay, config):
        self.fake = fake
        self.config = config

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None):
        payload = json.loads(body.decode("utf-8")) if body else None
        if method == "POST" and url.endswith("/invoices"):
            result = self.fake.create_invoice(
                amount_cents=int(round(float(payload["amount"]) * 100)),
                order_ref=payload["metadata"]["orderRef"],
                item_description=payload["metadata"]["itemDesc"],
                redirect_url=payload["checkout"]["redirectURL"],
                ttl_minutes=payload["checkout"]["expirationMinutes"],
            )
        elif method == "GET" and "/invoices/" in url:
            result = self.fake.get_invoice(url.rsplit("/", 1)[-1])
        else:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected BTCPay request: {method} {url}")
        return 200, json.dumps(result).encode("utf-8")


class Response:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def json(self):
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except json.JSONDecodeError:  # pragma: no cover - non-JSON bodies (media)
            return {}

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def header(self, name: str, default=None):
        lowered = name.lower()
        for key, value in self.headers:
            if key.lower() == lowered:
                return value
        return default

    def cookies(self) -> dict:
        jar = {}
        for key, value in self.headers:
            if key.lower() == "set-cookie":
                parts = value.split(";")[0]
                if "=" in parts:
                    name, _, val = parts.partition("=")
                    jar[name.strip()] = val
        return jar


class Client:
    """Tiny WSGI test client with a cookie jar and CSRF header handling."""

    def __init__(self, app: Velora):
        self.app = app
        self.cookies: dict[str, str] = {}
        self.csrf_token: str | None = None
        self.headers: dict[str, str] = {}

    def request(self, method: str, path: str, *, json_body=None, body: bytes | None = None,
                content_type: str | None = None, headers: dict | None = None,
                origin: str | None = "http://velora.test", host: str = "velora.test") -> Response:
        payload = b""
        if json_body is not None:
            payload = json.dumps(json_body).encode("utf-8")
            content_type = content_type or "application/json"
        elif body is not None:
            payload = body
        if method.upper() in ("GET", "HEAD"):
            payload = b""

        environ = {
            "REQUEST_METHOD": method.upper(),
            "PATH_INFO": path.split("?")[0],
            "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
            "SERVER_NAME": host,
            "SERVER_PORT": "80",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(payload),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "CONTENT_LENGTH": str(len(payload)),
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_HOST": host,
            "HTTP_USER_AGENT": "VeloraTest/1.0",
        }
        if content_type:
            environ["CONTENT_TYPE"] = content_type
        if self.cookies:
            environ["HTTP_COOKIE"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
        if origin:
            environ["HTTP_ORIGIN"] = origin
        if self.csrf_token:
            environ["HTTP_X_VELORA_CSRF"] = self.csrf_token
        for key, value in (headers or {}).items():
            environ[f"HTTP_{key.upper().replace('-', '_')}"] = value
        for key, value in self.headers.items():
            environ[f"HTTP_{key.upper().replace('-', '_')}"] = value

        captured = {}

        def start_response(status, response_headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = response_headers

        chunks = self.app(environ, start_response)
        raw = b"".join(chunks)
        status_code = int(captured["status"].split(" ", 1)[0])
        response = Response(status_code, captured["headers"], raw)
        self.cookies.update(response.cookies())
        if "velora_session" in response.cookies() and not response.cookies()["velora_session"]:
            self.cookies.pop("velora_session", None)
        return response

    # convenience wrappers -----------------------------------------------------
    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self.request("POST", path, **kwargs)

    def patch(self, path, **kwargs):
        return self.request("PATCH", path, **kwargs)

    def put(self, path, **kwargs):
        return self.request("PUT", path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request("DELETE", path, **kwargs)

    def login(self, email: str, password: str) -> Response:
        response = self.post("/api/auth/login", json_body={"email": email, "password": password})
        if response.status == 200:
            self.csrf_token = response.json.get("session", {}).get("csrf_token")
            session = self.get("/api/auth/session")
            self.csrf_token = session.json.get("csrf_token")
        return response


class VeloraTestCase(unittest.TestCase):
    """Base class wiring a fresh Velora instance around each test."""

    email_transport = "file"
    btcpay = True
    environment = "test"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="velora-test-"))
        self.outbox = self.tmp / "outbox"
        self.db_path = self.tmp / "velora.db"
        self.media_root = self.tmp / "media"
        environ = {
            "VELORA_ENV": self.environment,
            "VELORA_DB_PATH": str(self.db_path),
            "VELORA_MEDIA_ROOT": str(self.media_root),
            "VELORA_SECRET_KEY": "test-secret-key-not-used-outside-tests",
            "VELORA_EMAIL_TRANSPORT": self.email_transport,
            "VELORA_EMAIL_FROM": "Velora Test <no-reply@velora.test>",
            "VELORA_EMAIL_OUTBOX": str(self.outbox),
            "VELORA_PBKDF2_ITERATIONS": "1000",
            "VELORA_SECURE_COOKIES": "never",
        }
        if self.btcpay:
            environ.update(
                {
                    "VELORA_BTCPAY_URL": "http://localhost:49392",
                    "VELORA_BTCPAY_STORE_ID": "test-store",
                    "VELORA_BTCPAY_API_KEY": "test-api-key",
                    "VELORA_BTCPAY_WEBHOOK_SECRET": "test-webhook-secret",
                }
            )
        self.environ = environ
        self.config = build_config(environ)
        self.db = Database(self.config.db_path)
        apply_all(self.db, log=lambda message: None)
        self.ctx = build_context(self.config)
        self.fake = FakeBtcPay()
        patcher = _ClientPatcher(self.ctx, self.fake, self.config)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = Velora(self.config, context=self.ctx)
        self.client = Client(self.app)

    def tearDown(self):
        try:
            shutil.rmtree(self.tmp, ignore_errors=True)
        except OSError:  # pragma: no cover
            pass

    # ---- helpers -------------------------------------------------------------
    def read_outbox(self) -> list[dict]:
        index = self.outbox / "index.jsonl"
        if not index.exists():
            return []
        entries = []
        for line in index.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
        return entries

    def latest_email(self) -> dict:
        """Decode the newest message from the development outbox."""
        import email
        from email import policy

        entries = self.read_outbox()
        self.assertTrue(entries, "expected at least one email in the development outbox")
        entry = entries[-1]
        path = self.outbox / entry["file"]
        message = email.message_from_bytes(path.read_bytes(), policy=policy.default)
        texts = []
        htmls = []
        for part in message.walk():
            if part.is_multipart():
                continue
            content = part.get_content()
            if part.get_content_type() == "text/plain":
                texts.append(str(content))
            elif part.get_content_type() == "text/html":
                htmls.append(str(content))
        entry["body"] = "\n".join(texts)
        entry["html"] = "\n".join(htmls)
        return entry

    def token_from_email(self, entry: dict, key: str = "token") -> str:
        import re

        for match in re.findall(r"[?&]([a-zA-Z0-9_]+)=([A-Za-z0-9_\-\.]+)", entry["body"]):
            if match[0] == key:
                return match[1]
        raise AssertionError(f"no {key} found in the email body")

    def register(self, email: str, password: str = "correct-horse-9", display_name: str = "Sam Rivers",
                 client: Client | None = None, verify: bool = True) -> Client:
        client = client or Client(self.app)
        response = client.post(
            "/api/auth/signup",
            json_body={
                "display_name": display_name,
                "email": email,
                "password": password,
                "password_confirm": password,
                "adult_attestation": True,
            },
        )
        if response.status != 201:
            raise AssertionError(f"signup failed: {response.status} {response.text}")
        if verify:
            if self.email_transport == "none":
                # No transport, no message: mark the address verified the way the
                # operator CLI would, instead of asserting on an empty outbox.
                with self.db.transaction() as conn:
                    conn.execute(
                        "UPDATE users SET email_verified = 1, email_verified_at = ? "
                        "WHERE email = ?",
                        (now_iso(), email),
                    )
            else:
                entry = self.latest_email()
                token = self.token_from_email(entry)
                verify_response = client.post("/api/auth/verify-email", json_body={"token": token})
                if verify_response.status != 200:
                    raise AssertionError(f"verification failed: {verify_response.text}")
        login = client.login(email, password)
        if login.status != 200:
            raise AssertionError(f"login failed: {login.text}")
        return client

    def make_admin(self, client: Client, email: str):
        """Promote an existing verified account directly (tests only)."""
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET role = 'admin' WHERE email = ?", (email,))

    def seed_creator(self, client: Client, *, email: str = "creator@velora.test",
                     handle: str = "sample-creator", page_name: str = "Sample Studio",
                     category: str = "art", price_cents: int = 900,
                     address: str = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq") -> dict:
        """Create a creator page, tier and payout address through the API + CLI-style SQL."""
        self.register(email, display_name=page_name, client=client)
        with self.db.transaction() as conn:
            user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            from ..db import now_iso

            page_id = conn.execute(
                """
                INSERT INTO creator_pages (user_id, handle, page_name, category, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (user["id"], handle, page_name, category, now_iso(), now_iso()),
            ).lastrowid
            conn.execute("UPDATE users SET role = 'creator' WHERE id = ?", (user["id"],))
            tier_id = conn.execute(
                """
                INSERT INTO tiers (creator_id, name, description, price_cents, position, is_active,
                                   created_at, updated_at)
                VALUES (?, 'Supporter', 'Members-only posts', ?, 0, 1, ?, ?)
                """,
                (page_id, price_cents, now_iso(), now_iso()),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO creator_payouts (creator_id, btc_address, address_kind, saved_at)
                VALUES (?, ?, 'bech32', ?)
                """,
                (page_id, address, now_iso()),
            )
        return {"page_id": page_id, "tier_id": tier_id, "handle": handle}

    def create_post(self, client: Client, *, title="Behind the scenes", visibility="members",
                    teaser="A short public teaser about this post.", body="Private detail.",
                    publish=True, tier_ids=None) -> dict:
        response = client.post(
            "/api/studio/posts",
            json_body={
                "title": title,
                "teaser": teaser,
                "body": body,
                "visibility": visibility,
                "publish": publish,
                "tier_ids": tier_ids or [],
            },
        )
        if response.status != 201:
            raise AssertionError(f"post creation failed: {response.status} {response.text}")
        return response.json

    # ---- payment helpers -----------------------------------------------------
    def start_checkout(self, client: Client, tier_id: int) -> dict:
        response = client.post("/api/payments/intents", json_body={"tier_id": tier_id})
        if response.status != 201:
            raise AssertionError(f"checkout failed: {response.status} {response.text}")
        return response.json

    def invoice_for(self, order_ref: str) -> dict:
        for invoice in self.fake.invoices.values():
            if invoice["metadata"]["orderRef"] == order_ref:
                return invoice
        raise AssertionError(f"no fake invoice for {order_ref}")

    def settle_and_verify(self, client: Client, order_ref: str, *, confirmations: int = 2,
                          sats: int | None = None, method: str = "BTC-CHAIN",
                          raw_amount: str | None = None, status: str = "Settled",
                          additional_status: str | None = None) -> Response:
        invoice = self.invoice_for(order_ref)
        self.fake.settle(invoice["id"], confirmations=confirmations, sats=sats, method=method,
                         raw_amount=raw_amount, status=status, additional_status=additional_status)
        return client.post(f"/api/payments/intents/{self._intent_id(client, order_ref)}/refresh")


    def _intent_id(self, client: Client, order_ref: str) -> int:
        response = client.get(f"/api/payments/orders/{order_ref}")
        self.assertEqual(response.status, 200, response.text)
        return response.json["id"]


class _ClientPatcher:
    """Routes every ``BtcPayClient`` inside one test context to the fake."""

    def __init__(self, ctx, fake: FakeBtcPay, config):
        self.ctx = ctx
        self.fake = fake
        self.config = config

    def start(self):
        import server.btcpay as btcpay_module

        self._original_from_config = btcpay_module.BtcPayClient.from_config
        transport = FakeTransport(self.fake, self.config)

        def from_config(cls, config, **kwargs):
            kwargs.setdefault("transport", transport)
            return cls(config, **kwargs)

        btcpay_module.BtcPayClient.from_config = classmethod(from_config)  # type: ignore[assignment]

    def stop(self):
        import server.btcpay as btcpay_module

        btcpay_module.BtcPayClient.from_config = self._original_from_config  # type: ignore[assignment]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


PNG_BYTES = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFgwJ/lK3fNwAAAABJRU5ErkJggg=="
)
JPEG_BYTES = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwc"
    b"KDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAA"
    b"AAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)
