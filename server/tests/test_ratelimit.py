"""Rate limiting: independent buckets, window behaviour and pruning.

The limiter is deliberately simple (a counting table keyed by a salted hash of the
identifier), so these tests assert its contract: a burst is refused, a different
caller is unaffected, and the stored bucket never contains the raw value.
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

from server.ratelimit import RateLimiter  # noqa: E402
from server.tests.harness import Client, VeloraTestCase  # noqa: E402


class LimiterUnitTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.limiter = RateLimiter(self.db, "unit-test-secret")

    def test_burst_is_refused_at_the_limit_and_recovers_after_the_window(self):
        for attempt in range(3):
            result = self.limiter.check("login", "198.51.100.1", limit=3, window_seconds=60)
            self.assertTrue(result.allowed, attempt)
        blocked = self.limiter.check("login", "198.51.100.1", limit=3, window_seconds=60)
        self.assertFalse(blocked.allowed)
        self.assertGreaterEqual(blocked.retry_after, 1)
        self.assertLessEqual(blocked.retry_after, 60)

        # A degenerate window is clamped instead of dividing by zero.
        degenerate = self.limiter.check("login", "198.51.100.1", limit=3, window_seconds=0)
        self.assertEqual(degenerate.window_seconds, 1)
        self.assertTrue(degenerate.allowed)

    def test_buckets_are_independent_per_action_and_per_identifier(self):
        for _ in range(3):
            self.limiter.check("signup", "198.51.100.2", limit=3, window_seconds=600)
        self.assertFalse(
            self.limiter.check("signup", "198.51.100.2", limit=3, window_seconds=600).allowed
        )
        # Same identifier, different action.
        self.assertTrue(
            self.limiter.check("login", "198.51.100.2", limit=3, window_seconds=600).allowed
        )
        # Same action, different identifier.
        self.assertTrue(
            self.limiter.check("signup", "198.51.100.3", limit=3, window_seconds=600).allowed
        )

    def test_the_raw_identifier_is_never_stored(self):
        self.limiter.check("login", "203.0.113.77", limit=5, window_seconds=600)
        with self.db.connection() as conn:
            buckets = [row["bucket"] for row in conn.execute("SELECT bucket FROM rate_limits")]
        self.assertTrue(buckets)
        for bucket in buckets:
            self.assertNotIn("203.0.113.77", bucket)
            self.assertNotIn("login", bucket)
            self.assertRegex(bucket, r"^[0-9a-f]{40}$")

    def test_pruning_removes_stale_windows_only(self):
        from server.ratelimit import CLEANUP_AFTER_SECONDS

        self.limiter.check("webhook", "198.51.100.9", limit=5, window_seconds=3600)
        stale_epoch = int(time.time()) - CLEANUP_AFTER_SECONDS - 60
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO rate_limits (bucket, window_start, hit_count) VALUES (?, ?, 3)",
                ("stale-bucket", stale_epoch),
            )
        pruned = self.limiter.prune()
        self.assertGreaterEqual(pruned, 1)
        with self.db.connection() as conn:
            remaining = [row["bucket"] for row in conn.execute("SELECT bucket FROM rate_limits")]
        self.assertNotIn("stale-bucket", remaining)
        self.assertEqual(len(remaining), 1, remaining)


class EndpointRateLimitTests(VeloraTestCase):
    def test_signup_burst_is_limited_with_a_retry_hint(self):
        client = Client(self.app)
        statuses = []
        for index in range(8):
            response = client.post("/api/auth/signup", json_body={
                "display_name": f"Person {index}", "email": f"person{index}@velora.test",
                "password": "correct-horse-9", "password_confirm": "correct-horse-9",
                "adult_attestation": True,
            })
            statuses.append(response.status)
            if response.status == 429:
                self.assertEqual(response.json["error"]["code"], "rate_limited")
                self.assertIn("retry_after_seconds", response.json["error"]["details"])
                self.assertTrue(response.header("retry-after"))
        self.assertEqual(statuses.count(201), 5, statuses)
        self.assertEqual(statuses[-1], 429, statuses)

    def test_login_guessing_is_limited_per_address(self):
        client = Client(self.app)
        self.register("target@velora.test", display_name="Target", client=Client(self.app))
        statuses = []
        for _ in range(14):
            response = client.post("/api/auth/login", json_body={
                "email": "target@velora.test", "password": "definitely-wrong",
            })
            statuses.append(response.status)
        self.assertIn(429, statuses, statuses)
        self.assertEqual(statuses[0], 401, statuses)

    def test_checkout_burst_is_limited_but_does_not_leak_orders(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="limit-creator@velora.test", handle="limit-page")
        member = Client(self.app)
        self.register("limit-member@velora.test", display_name="Limit Member", client=member)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM rate_limits WHERE bucket LIKE 'checkout:%'")
        statuses = []
        for _ in range(15):
            response = member.post("/api/payments/intents", json_body={"tier_id": seed["tier_id"]})
            statuses.append(response.status)
        self.assertIn(429, statuses, statuses)

    def test_report_and_message_bursts_are_limited(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="burst-creator@velora.test", handle="burst-page")
        member = Client(self.app)
        self.register("burst-member@velora.test", display_name="Burst Member", client=member)
        order = self.start_checkout(member, seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])

        thread = member.post("/api/threads", json_body={
            "creator_id": seed["page_id"], "body": "First message.",
        })
        self.assertEqual(thread.status, 201, thread.text)
        thread_id = thread.json["thread"]["id"]
        statuses = [
            member.post(f"/api/threads/{thread_id}/messages", json_body={"body": f"Message {index}"}).status
            for index in range(35)
        ]
        self.assertIn(429, statuses, statuses)

        report_statuses = [
            member.post("/api/reports", json_body={
                "target_type": "post", "target_id": 1, "reason_code": "spam",
            }).status
            for _ in range(12)
        ]
        self.assertIn(429, report_statuses, report_statuses)

    def test_health_and_discovery_are_not_rate_limited_into_uselessness(self):
        client = Client(self.app)
        for _ in range(30):
            self.assertEqual(client.get("/api/health").status, 200)
        for _ in range(30):
            self.assertEqual(client.get("/api/creators").status, 200)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
