"""Client-address handling: proxy headers, validation, and the no-geolocation rule."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

from server.netutil import (  # noqa: E402
    CF_CONNECTING_IP,
    forwarded_client_ip,
    normalize_ip,
)
from server.app import Velora  # noqa: E402
from server.services.context import build_context  # noqa: E402
from server.tests.harness import Client, VeloraTestCase  # noqa: E402


class NormalizeIpTests(unittest.TestCase):
    def test_accepts_ipv4_and_ipv6_and_canonicalises(self):
        self.assertEqual(normalize_ip(" 203.0.113.7 "), "203.0.113.7")
        self.assertEqual(normalize_ip("203.0.113.7:443"), "203.0.113.7")
        self.assertEqual(normalize_ip("2001:0db8::1"), "2001:db8::1")
        self.assertEqual(normalize_ip("[2001:db8::1]"), "2001:db8::1")
        # A bracketed address with a port is also accepted.
        self.assertEqual(normalize_ip("[2001:db8::1]:443"), "2001:db8::1")

    def test_rejects_junk_that_could_poison_a_log_or_a_bucket(self):
        for value in (None, "", "   ", "unknown", "a-very-long-" + "x" * 200,
                      "203.0.113.7; DROP TABLE sessions", "127.0.0.1'n'", "<script>"):
            self.assertIsNone(normalize_ip(value), value)


class ForwardedHeaderTests(unittest.TestCase):
    def test_cloudflare_header_wins_then_real_ip_then_first_hop(self):
        self.assertEqual(
            forwarded_client_ip({CF_CONNECTING_IP: "198.51.100.4",
                                 "x-forwarded-for": "203.0.113.9, 10.0.0.1"}),
            "198.51.100.4",
        )
        self.assertEqual(forwarded_client_ip({"x-real-ip": "198.51.100.5"}), "198.51.100.5")
        self.assertEqual(
            forwarded_client_ip({"x-forwarded-for": "198.51.100.6, 10.0.0.1"}),
            "198.51.100.6",
        )

    def test_header_values_are_validated_and_skipped_when_bogus(self):
        self.assertEqual(
            forwarded_client_ip({"x-forwarded-for": "not-an-ip, 198.51.100.7"}),
            "198.51.100.7",
        )
        self.assertIsNone(forwarded_client_ip({"cf-connecting-ip": "definitely not an ip"}))


class ProxyTrustTests(VeloraTestCase):
    def trusted_client(self) -> Client:
        """A client whose context has been told to trust proxy headers."""
        from dataclasses import replace

        config = replace(self.config, trust_proxy=True)
        return Client(Velora(config, context=build_context(config)))

    def test_forwarded_headers_are_ignored_unless_the_operator_opts_in(self):
        spoofed = {CF_CONNECTING_IP: "198.51.100.9"}
        self.assertFalse(self.config.trust_proxy)
        self.assertEqual(Client(self.app).get("/api/creators", headers=spoofed).status, 200)
        self.assertEqual(self.trusted_client().get("/api/creators", headers=spoofed).status, 200)

    def test_rate_limiting_still_applies_when_trust_proxy_is_on(self):
        anonymous = self.trusted_client()
        headers = {CF_CONNECTING_IP: "198.51.100.11"}
        statuses = []
        for index in range(7):
            response = anonymous.post("/api/auth/signup", headers=headers, json_body={
                "display_name": f"Person {index}", "email": f"proxy{index}@velora.test",
                "password": "correct-horse-9", "password_confirm": "correct-horse-9",
                "adult_attestation": True,
            })
            statuses.append(response.status)
        self.assertIn(429, statuses, statuses)
        self.assertEqual(statuses[0], 201, statuses)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
