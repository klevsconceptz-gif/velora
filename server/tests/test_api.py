"""Cross-cutting API behaviour: the route surface, gating, and error handling.

One generated test per registered route proves that every endpoint is either
public on purpose or refuses an anonymous caller, and that no endpoint can be
reached with the wrong method. The remaining tests cover the transport-level
guarantees a client depends on: JSON error envelopes, CSRF, origin checks, body
limits, security headers and the health endpoint.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

from server.routes import ROUTES  # noqa: E402
from server.routing import (  # noqa: E402
    AUTH_ADMIN,
    AUTH_CREATOR,
    AUTH_NONE,
    AUTH_OPTIONAL,
    AUTH_REQUIRED,
    AUTH_VERIFIED,
    CONVERTERS,
)
from server.tests.harness import Client, VeloraTestCase  # noqa: E402

SAMPLE_VALUES = {"int": "1", "handle": "sample-creator", "slug": "sample-page", "str": "VLR-EXAMPLE"}

PROTECTED = (AUTH_REQUIRED, AUTH_VERIFIED, AUTH_CREATOR, AUTH_ADMIN)
# A public endpoint may legitimately answer with any of these; what it must never
# do is deny a visitor access to something public, or fail with a server error.
PUBLIC_STATUSES = {200, 201, 202, 204, 400, 404, 405, 409, 410, 413, 415, 422, 429, 503}


def sample_path(route) -> str:
    path = route.path
    for name, kind in route.converters.items():
        path = path.replace(f"<{kind}:{name}>", SAMPLE_VALUES.get(kind, "example"))
    return path


class RouteSurfaceTests(VeloraTestCase):
    """Generated coverage: every route answers with an intentional status."""

    def _call(self, route, client: Client, body=None):
        path = sample_path(route)
        if route.method in ("GET", "HEAD", "DELETE"):
            return client.request(route.method, path)
        return client.request(route.method, path, json_body=body if body is not None else {})

    def test_route_table_is_complete_and_consistent(self):
        self.assertGreaterEqual(len(ROUTES), 90, len(ROUTES))
        seen = set()
        for route in ROUTES:
            self.assertIn(route.auth,
                          (AUTH_NONE, AUTH_OPTIONAL, AUTH_REQUIRED, AUTH_VERIFIED,
                           AUTH_CREATOR, AUTH_ADMIN))
            key = (route.method, route.path)
            self.assertNotIn(key, seen, f"duplicate route {key}")
            seen.add(key)
            self.assertTrue(route.path.startswith("/api/"), route.path)
            self.assertTrue(callable(route.handler))
            for name, kind in route.converters.items():
                self.assertIn(kind, CONVERTERS, f"{route.path}: unknown converter {kind}")


def _generate_route_tests():
    def make_test(route):
        def test(self):
            client = Client(self.app)
            response = self._call(route, client)
            path = sample_path(route)
            if route.auth in PROTECTED:
                self.assertEqual(response.status, 401,
                                 f"{route.method} {path} should require a session, got "
                                 f"{response.status} {response.text[:200]}")
                self.assertEqual(response.json["error"]["code"], "unauthenticated")
            elif route.path == "/api/payments/btcpay/webhook":
                # Signature-authenticated, never session-authenticated: an
                # unsigned delivery is refused, and that is not a login prompt.
                self.assertNotEqual(response.status, 401, response.text)
                self.assertEqual(response.json["outcome"], "invalid_signature")
            else:
                self.assertNotIn(response.status, (401, 403),
                                 f"{route.method} {path} must stay reachable without a session")
                self.assertLess(response.status, 500,
                                f"{route.method} {path} failed: {response.text[:200]}")
                self.assertIn(response.status, PUBLIC_STATUSES,
                              f"{route.method} {path} returned an unexpected {response.status}")
            self.assertNotIn("traceback", response.text.lower())
            self.assertNotIn("sqlite", response.text.lower())

        test.__name__ = "test_route_" + route.method.lower() + "_" + (
            route.path.strip("/").replace("/", "_").replace("<", "").replace(">", "")
            .replace(":", "_").replace(".", "_") or "root"
        )
        return test

    for route in ROUTES:
        generated = make_test(route)
        setattr(RouteSurfaceTests, generated.__name__, generated)


_generate_route_tests()


class TransportTests(VeloraTestCase):
    def test_unknown_api_route_returns_a_json_error_envelope(self):
        response = Client(self.app).get("/api/does-not-exist")
        self.assertEqual(response.status, 404)
        self.assertEqual(response.json["error"]["code"], "not_found")
        self.assertIn("message", response.json["error"])

    def test_wrong_method_reports_405_without_a_stack_trace(self):
        client = Client(self.app)
        response = client.request("DELETE", "/api/bootstrap")
        self.assertEqual(response.status, 405, response.text)
        self.assertEqual(response.json["error"]["code"], "method_not_allowed")

    def test_error_bodies_never_include_internals(self):
        client = Client(self.app)
        for path, method in (("/api/unknown", "GET"), ("/api/admin/overview", "GET"),
                             ("/api/payments/intake", "POST")):
            response = client.request(method, path, json_body={})
            lowered = response.text.lower()
            for leak in ("traceback", "file \"/", "sqlite3", "password_hash", "secret"):
                self.assertNotIn(leak, lowered, f"{method} {path} leaked {leak}")

    def test_security_headers_are_set_on_api_responses(self):
        response = Client(self.app).get("/api/privacy/summary")
        self.assertEqual(response.header("x-content-type-options"), "nosniff")
        self.assertEqual(response.header("x-frame-options"), "DENY")
        self.assertEqual(response.header("referrer-policy"), "same-origin")
        self.assertIn("geolocation=()", response.header("permissions-policy", ""))
        self.assertEqual(response.header("cross-origin-opener-policy"), "same-origin")

    def test_framing_is_restricted_by_default_and_configurable(self):
        from dataclasses import replace

        from server.app import Velora, security_headers
        from server.services.context import build_context

        # The test environment is production-like: no framing, with the legacy header.
        headers = security_headers(self.config)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

        embedded = replace(self.config, frame_ancestors="*")
        headers = security_headers(embedded)
        self.assertNotIn("X-Frame-Options", headers,
                         "X-Frame-Options DENY would override a permissive frame-ancestors")
        self.assertIn("frame-ancestors *", headers["Content-Security-Policy"])
        response = Client(Velora(embedded, context=build_context(embedded))).get("/")
        self.assertNotIn("x-frame-options", response.headers)

        # A development instance may be framed so it can sit inside a preview pane.
        from server.config import build_config

        dev = build_config({"VELORA_ENV": "development",
                            "VELORA_SECRET_KEY": "dev-secret-for-framing-test"})
        self.assertTrue(dev.frame_ancestors)
        self.assertNotEqual(dev.frame_ancestors, "'none'")
        restricted = build_config({"VELORA_ENV": "development",
                                   "VELORA_SECRET_KEY": "dev-secret-for-framing-test",
                                   "VELORA_FRAME_ANCESTORS": "'none'"})
        self.assertEqual(restricted.frame_ancestors, "'none'")

    def test_csrf_is_required_for_session_mutations(self):
        member = Client(self.app)
        self.register("csrf@velora.test", display_name="CSRF Member", client=member)
        token = member.csrf_token
        member.csrf_token = None
        blocked = member.patch("/api/account/profile", json_body={"bio": "no token"})
        self.assertEqual(blocked.status, 403, blocked.text)
        self.assertIn(blocked.json["error"]["code"], ("csrf_required", "csrf_invalid"))

        member.csrf_token = token
        allowed = member.patch("/api/account/profile", json_body={"bio": "with token"})
        self.assertEqual(allowed.status, 200, allowed.text)

    def test_cross_origin_mutations_are_refused(self):
        member = Client(self.app)
        self.register("origin@velora.test", display_name="Origin Member", client=member)
        crossed = member.request("PATCH", "/api/account/profile",
                                 json_body={"bio": "cross site"},
                                 origin="https://evil.example")
        self.assertEqual(crossed.status, 403, crossed.text)
        self.assertEqual(crossed.json["error"]["code"], "origin_rejected")
        # A same-origin mutation still succeeds.
        same = member.request("PATCH", "/api/account/profile", json_body={"bio": "same site"},
                              origin="http://velora.test")
        self.assertEqual(same.status, 200, same.text)

    def test_oversized_json_bodies_are_refused(self):
        member = Client(self.app)
        self.register("big@velora.test", display_name="Big Body", client=member)
        response = member.patch("/api/account/profile",
                                json_body={"bio": "x" * (256 * 1024)})
        self.assertIn(response.status, (400, 413), response.text)

    def test_non_json_bodies_are_refused_with_a_clear_error(self):
        member = Client(self.app)
        self.register("form@velora.test", display_name="Form Member", client=member)
        response = member.request("PATCH", "/api/account/profile", body=b"bio=hello",
                                  content_type="application/x-www-form-urlencoded")
        self.assertIn(response.status, (400, 415), response.text)
        self.assertIn("message", response.json["error"])

    def test_health_reports_readiness_and_configuration(self):
        payload = Client(self.app).get("/api/health").json
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["database"], "ready")
        self.assertEqual(payload["migrations_applied"], 12)
        self.assertTrue(payload["btcpay_configured"])
        self.assertNotIn("secret", json.dumps(payload).lower())

    def test_health_reports_needs_migrations_on_an_empty_database(self):
        from server.app import Velora
        from server.config import build_config
        from server.services.context import build_context

        config = build_config({**self.environ, "VELORA_DB_PATH": str(self.tmp / "empty.db")})
        app = Velora(config, context=build_context(config))
        response = Client(app).get("/api/health")
        self.assertEqual(response.status, 503)
        self.assertEqual(response.json["database"], "needs_migrations")
        self.assertFalse(response.json["ok"])

    def test_bootstrap_describes_the_instance_without_secrets(self):
        body = Client(self.app).get("/api/bootstrap").text
        payload = json.loads(body)
        for key in ("features", "email_status", "checkout", "categories", "orientation_options",
                    "report_reasons", "password_requirements", "session"):
            self.assertIn(key, payload)
        self.assertEqual(payload["password_requirements"]["min_length"], 8)
        for secret in ("test-secret-key-not-used-outside-tests", "test-api-key",
                       "test-webhook-secret", "bc1q", "password_hash"):
            self.assertNotIn(secret, body)

    def test_query_parameters_are_validated(self):
        client = Client(self.app)
        for path in ("/api/creators?page=0", "/api/creators?per_page=0", "/api/creators?page=abc",
                     "/api/creators?per_page=1000"):
            response = client.get(path)
            self.assertIn(response.status, (200, 400), f"{path} -> {response.status}")
            if response.status == 400:
                self.assertIn(response.json["error"]["code"],
                              ("validation_error", "invalid_request"))

    def test_trailing_slashes_do_not_create_duplicate_endpoints(self):
        client = Client(self.app)
        self.assertEqual(client.get("/api/creators/").status, client.get("/api/creators").status)

    def test_head_requests_do_not_return_a_body(self):
        response = Client(self.app).request("HEAD", "/api/privacy/summary")
        self.assertIn(response.status, (200, 405))
        if response.status == 200:
            self.assertEqual(response.body, b"")

    def test_categories_and_privacy_summary_are_public_and_stable(self):
        client = Client(self.app)
        categories = client.get("/api/categories").json["items"]
        self.assertTrue(categories)
        for category in categories:
            self.assertTrue({"key", "label"} <= set(category), set(category))
        summary = client.get("/api/privacy/summary").json
        self.assertIn("not_collected", summary)
        self.assertIn("rights", summary)
        joined = " ".join(summary["not_collected"]).lower()
        for promise in ("phone", "government id", "seed phrase", "location"):
            self.assertIn(promise, joined)

    def test_no_endpoint_uses_geolocation_or_country_gating(self):
        client = Client(self.app)
        container = client.get("/api/bootstrap").text + client.get("/api/payments/availability").text
        for needle in ("country", "region", "geo", "vpn", "jurisdiction"):
            self.assertNotIn(needle, container.lower())

    def test_session_cookie_is_httponly_and_samesite(self):
        member = Client(self.app)
        response = member.post("/api/auth/signup", json_body={
            "display_name": "Cookie Person", "email": "cookie@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        })
        self.assertEqual(response.status, 201, response.text)
        login = member.post("/api/auth/login", json_body={
            "email": "cookie@velora.test", "password": "correct-horse-9",
        })
        cookie = login.header("set-cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie, "secure cookies are opt-in for local development")

    def test_logout_clears_the_session_server_side(self):
        member = Client(self.app)
        self.register("logout@velora.test", display_name="Logout Person", client=member)
        self.assertTrue(member.get("/api/auth/session").json["authenticated"])
        member.post("/api/auth/logout")
        self.assertFalse(member.get("/api/auth/session").json["authenticated"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
