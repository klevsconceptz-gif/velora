"""Requests that exercise the real socket layer.

The in-process test client hands the app a ``BytesIO``, so it cannot catch the
failures that matter at the network edge:

* if the server answers "too large" and closes the socket while the client is
  still uploading, the browser shows a *network error* and the user never sees
  Velora's explanation;
* if a client stalls mid-body and no socket timeout is set, a worker thread is
  pinned indefinitely.

Both are checked here by running the app the way ``serve`` does — through
``server.cli.build_server`` — and talking to it over TCP.
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

from server import cli as cli_module  # noqa: E402
from server.app import Velora  # noqa: E402
from server.tests.harness import Client, VeloraTestCase  # noqa: E402

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class _ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class RawClientMixin:
    """Hand-built HTTP over TCP against whatever server the test started."""

    port: int

    def request(self, method: str, path: str, body: bytes = b"", headers: dict | None = None,
                *, client: Client | None = None, timeout: float = 60.0):
        """Send a raw request; return (status, headers, body).

        The whole response is drained, which is what proves the server answered
        rather than resetting the connection mid-request.
        """
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        try:
            lines = [f"{method} {path} HTTP/1.1", "Host: velora.test", "Connection: close"]
            if client is not None and client.cookies:
                lines.append("Cookie: " + "; ".join(f"{k}={v}" for k, v in client.cookies.items()))
            if client is not None and client.csrf_token and method not in ("GET", "HEAD"):
                lines.append(f"X-Velora-CSRF: {client.csrf_token}")
            if client is not None:
                lines.append("Origin: http://velora.test")
            for key, value in (headers or {}).items():
                lines.append(f"{key}: {value}")
            if body:
                lines.append(f"Content-Length: {len(body)}")
            lines.append("")
            connection.sendall(("\r\n".join(lines) + "\r\n").encode("utf-8"))
            for start in range(0, len(body), 64 * 1024):
                connection.sendall(body[start:start + 64 * 1024])
            raw = self.drain(connection)
        finally:
            connection.close()
        head, _, payload = raw.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        status = int(status_line.split(" ")[1])
        parsed = {}
        for line in head.split(b"\r\n")[1:]:
            if b":" in line:
                key, _, value = line.decode("latin-1").partition(":")
                parsed[key.strip().lower()] = value.strip()
        return status, parsed, payload

    @staticmethod
    def drain(connection) -> bytes:
        chunks = []
        while True:
            chunk = connection.recv(64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def status_code(raw: bytes) -> int:
        return int(raw.split(b"\r\n", 1)[0].split(b" ")[1])


class RealSocketTests(RawClientMixin, VeloraTestCase):
    """Run the app the way `serve` does, then talk to it over TCP."""

    def setUp(self):
        super().setUp()
        self.httpd = make_server("127.0.0.1", 0, Velora(self.config, context=self.ctx),
                                 server_class=_ThreadingWSGIServer)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    # ---- fixtures ------------------------------------------------------------

    def signed_in_member(self, email: str = "uploader@velora.test") -> Client:
        return self.register(email, display_name="Upload Person")

    def signed_in_creator(self) -> tuple[Client, int]:
        creator = Client(self.app)
        self.seed_creator(creator, email="socket-creator@velora.test",
                          handle="socket-page", page_name="Socket Page")
        login = creator.login("socket-creator@velora.test", "correct-horse-9")
        self.assertEqual(login.status, 200, login.text)
        draft = creator.post("/api/studio/posts", json_body={
            "title": "Socket post", "teaser": "A teaser for the socket upload test.",
            "body": "body", "visibility": "members", "publish": False})
        self.assertEqual(draft.status, 201, draft.text)
        return creator, draft.json["id"]

    # ---- tests ---------------------------------------------------------------

    def test_oversized_upload_returns_a_readable_413(self):
        """Past the route's upload budget: the 413 must arrive readable, not as a reset."""
        member = self.signed_in_member()
        body = b"\x89PNG\r\n\x1a\n" + b"\x00" * (self.config.max_upload_bytes + 64 * 1024)
        status, headers, payload = self.request(
            "POST", "/api/studio/posts/1/media?filename=big.png", body,
            {"Content-Type": "image/png"}, client=member,
        )
        self.assertEqual(status, 413, f"{status}: {payload[:200]!r}")
        error = json.loads(payload)["error"]
        self.assertEqual(error["code"], "payload_too_large")
        self.assertIn("larger than Velora accepts", error["message"])
        self.assertEqual(headers.get("content-type"), "application/json; charset=utf-8")

    def test_oversized_json_body_returns_a_readable_413(self):
        member = self.signed_in_member()
        body = json.dumps({"bio": "x" * (self.config.max_body_bytes + 4096)}).encode("utf-8")
        status, _, payload = self.request(
            "PATCH", "/api/account/profile", body,
            {"Content-Type": "application/json"}, client=member,
        )
        self.assertEqual(status, 413, f"{status}: {payload[:200]!r}")
        self.assertEqual(json.loads(payload)["error"]["code"], "payload_too_large")

    def test_oversized_upload_is_refused_before_authentication_too(self):
        """An anonymous oversized upload must still get a readable answer."""
        status, _, payload = self.request(
            "POST", "/api/studio/posts/1/media?filename=big.png",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * (self.config.max_upload_bytes + 64 * 1024),
            {"Content-Type": "image/png"},
        )
        self.assertEqual(status, 413, f"{status}: {payload[:200]!r}")

    def test_an_image_just_over_the_limit_gets_the_friendly_message(self):
        """Inside the route budget but too big for an image: 400 with the real reason."""
        member = self.signed_in_member()
        body = b"\x89PNG\r\n\x1a\n" + b"\x00" * (self.config.max_upload_bytes + 4096)
        status, _, payload = self.request(
            "POST", "/api/studio/posts/1/media?filename=big.png", body,
            {"Content-Type": "image/png"}, client=member,
        )
        self.assertEqual(status, 400, f"{status}: {payload[:200]!r}")
        error = json.loads(payload)["error"]
        self.assertEqual(error["code"], "invalid_media")
        self.assertIn("smaller", error["message"])

    def test_the_server_keeps_serving_after_rejecting_a_large_body(self):
        member = self.signed_in_member()
        status, _, _ = self.request(
            "POST", "/api/studio/posts/1/media?filename=big.png",
            b"\x89PNG\r\n\x1a\n" + b"\x00" * (self.config.max_upload_bytes + 64 * 1024),
            {"Content-Type": "image/png"}, client=member,
        )
        self.assertEqual(status, 413)
        status, _, payload = self.request("GET", "/api/health")
        self.assertEqual(status, 200, payload[:200])
        self.assertTrue(json.loads(payload)["ok"])

    def test_an_image_within_the_limit_is_accepted(self):
        """The boundary must not be off by one in either direction."""
        creator, draft_id = self.signed_in_creator()
        status, _, payload = self.request(
            "POST", f"/api/studio/posts/{draft_id}/media?filename=small.png", PNG_1PX,
            {"Content-Type": "image/png"}, client=creator,
        )
        self.assertEqual(status, 201, f"{status}: {payload[:200]!r}")


class StalledClientTests(RawClientMixin, VeloraTestCase):
    """A client that stops sending must not hold a worker thread forever."""

    def setUp(self):
        super().setUp()
        self.timeout = 0.5
        original = cli_module.SOCKET_TIMEOUT_SECONDS
        cli_module.SOCKET_TIMEOUT_SECONDS = self.timeout
        self.addCleanup(setattr, cli_module, "SOCKET_TIMEOUT_SECONDS", original)
        self.httpd = cli_module.build_server(self.config, "127.0.0.1", 0,
                                             Velora(self.config, context=self.ctx))
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def test_a_stalled_upload_is_dropped_instead_of_pinning_the_thread(self):
        started = time.monotonic()
        connection = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            # Promise 200 kilobytes and then send nothing at all.
            connection.sendall(b"POST /api/auth/signup HTTP/1.1\r\nHost: velora.test\r\n"
                               b"Content-Type: application/json\r\nContent-Length: 204800\r\n\r\n")
            connection.settimeout(10)
            raw = self.drain(connection)
        finally:
            connection.close()
        elapsed = time.monotonic() - started
        status = self.status_code(raw) if raw else None
        self.assertEqual(status, 408, f"expected a clean timeout answer, got {raw[:160]!r}")
        body = raw.partition(b"\r\n\r\n")[2]
        self.assertIn(b"request_timeout", body, body[:160])
        self.assertNotIn(b"internal_error", body, body[:160])
        self.assertLess(elapsed, self.timeout * 8, f"took {elapsed:.1f}s to drop a stalled client")

    def test_genuine_requests_still_work_on_the_same_server(self):
        status, _, payload = self.request("GET", "/api/health")
        self.assertEqual(status, 200, payload[:200])
        member = self.register("stall-user@velora.test", display_name="Stall Person")
        status, _, payload = self.request("POST", "/api/auth/logout", b"", {}, client=member)
        self.assertEqual(status, 200, f"{status}: {payload[:200]!r}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
