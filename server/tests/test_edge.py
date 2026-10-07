"""The optional trusted-edge mode used behind the Cloudflare Worker (``cloudflare/``).

With ``VELORA_EDGE_SECRET`` set, the API only answers requests that carry the
shared secret and takes the caller's address from the proxy's own header; a
forwarded header sent by a client can never name a different address.
"""

from __future__ import annotations

import unittest

from ..config import build_config
from .harness import Client, VeloraTestCase

SECRET = "e" * 40
EDGE = {"x-velora-edge-secret": SECRET}


def signup_body(index: int) -> dict:
    return {
        "display_name": f"Person {index}", "email": f"person{index}@velora.test",
        "password": "correct-horse-9", "password_confirm": "correct-horse-9",
        "adult_attestation": True,
    }


class EdgeSecretConfigTests(unittest.TestCase):
    def test_a_short_secret_is_refused(self):
        with self.assertRaises(RuntimeError):
            build_config({"VELORA_ENV": "test", "VELORA_SECRET_KEY": "k" * 32,
                          "VELORA_EDGE_SECRET": "too-short"})

    def test_unset_means_no_edge_mode(self):
        config = build_config({"VELORA_ENV": "test", "VELORA_SECRET_KEY": "k" * 32})
        self.assertIsNone(config.edge_secret)


class EdgeModeTests(VeloraTestCase):
    extra_environ = {"VELORA_EDGE_SECRET": SECRET, "VELORA_TRUST_PROXY": "1"}

    def test_api_without_the_secret_is_refused(self):
        response = Client(self.app).get("/api/bootstrap")
        self.assertEqual(response.status, 403, response.text)
        self.assertEqual(response.json["error"]["code"], "edge_required")

    def test_a_wrong_secret_is_refused(self):
        response = Client(self.app).get("/api/bootstrap", headers={"x-velora-edge-secret": "x" * 40})
        self.assertEqual(response.status, 403)

    def test_mutations_without_the_secret_are_refused_before_anything_runs(self):
        response = Client(self.app).post("/api/auth/signup", json_body=signup_body(1))
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json["error"]["code"], "edge_required")

    def test_the_secret_lets_requests_through(self):
        response = Client(self.app).get("/api/bootstrap", headers=EDGE)
        self.assertEqual(response.status, 200, response.text)

    def test_health_stays_open_for_platform_checks(self):
        response = Client(self.app).get("/api/health")
        self.assertEqual(response.status, 200, response.text)

    def test_the_shell_is_not_gated(self):
        self.assertEqual(Client(self.app).get("/").status, 200)

    def test_refusals_still_carry_security_headers(self):
        response = Client(self.app).get("/api/bootstrap")
        self.assertEqual(response.header("x-content-type-options"), "nosniff")

    def test_rate_limits_follow_the_edge_client_address_only(self):
        # Five signups per address are allowed. A client cannot dodge that by
        # inventing forwarded headers: only x-velora-client-ip counts.
        def attempt(index, ip, **extra):
            headers = {**EDGE, "x-velora-client-ip": ip, **extra}
            return Client(self.app).post("/api/auth/signup", json_body=signup_body(index), headers=headers).status

        first = [attempt(i, "203.0.113.10", **{"x-forwarded-for": f"198.51.100.{i}",
                                               "cf-connecting-ip": f"198.51.100.{i + 50}"})
                 for i in range(7)]
        self.assertEqual(first.count(201), 5, first)
        self.assertEqual(first[-1], 429, first)
        # A different real client is unaffected.
        self.assertEqual(attempt(100, "203.0.113.11"), 201)

    def test_without_a_client_header_the_socket_address_is_used(self):
        statuses = [Client(self.app).post("/api/auth/signup", json_body=signup_body(i),
                                          headers={**EDGE, "x-forwarded-for": f"198.51.100.{i}"}).status
                    for i in range(7)]
        self.assertEqual(statuses.count(201), 5, statuses)


class NoEdgeModeTests(VeloraTestCase):
    def test_nothing_changes_when_unset(self):
        self.assertEqual(Client(self.app).get("/api/bootstrap").status, 200)
        # Without an edge secret a stray header is just a header.
        response = Client(self.app).get("/api/bootstrap", headers=EDGE)
        self.assertEqual(response.status, 200)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
