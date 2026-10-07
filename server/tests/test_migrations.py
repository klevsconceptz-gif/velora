"""Schema tests: every migration applies cleanly, and the guarantees it promises hold.

These tests write to a throwaway database created by the harness, so they can
assert that append-only triggers really refuse an UPDATE or a DELETE instead of
trusting the comment at the top of the SQL file.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

from server import migrations as migrations_module  # noqa: E402
from server.db import Database, now_iso  # noqa: E402
from server.tests.harness import Client, VeloraTestCase  # noqa: E402

EXPECTED_TABLES = {
    "users", "sessions", "email_tokens", "admin_invitations", "account_actions",
    "creator_pages", "creator_applications", "creator_payouts", "creator_page_views", "tiers",
    "posts", "post_media", "post_tiers", "orientation_disclosures",
    "memberships", "payment_intents", "invoices", "ledger_entries", "webhook_events",
    "threads", "messages", "reports", "admin_notes", "audit_log", "rate_limits",
}

APPEND_ONLY_TABLES = ("ledger_entries", "webhook_events", "audit_log")


class MigrationRunnerTests(VeloraTestCase):
    """The runner itself: discovery, ordering, idempotence and checksums."""

    def test_every_migration_file_is_discovered_in_order(self):
        found = migrations_module.discover()
        self.assertEqual(len(found), 11, [name for name, _ in found])
        versions = [version for version, _ in found]
        self.assertEqual(versions, sorted(versions))
        self.assertEqual(versions, [f"{index:04d}" for index in range(1, 12)])
        filenames = [path.name for _, path in found]
        self.assertEqual(filenames[0], "0001_core_accounts.sql")
        self.assertEqual(filenames[-1], "0011_financial_integrity.sql")

    def test_status_reports_everything_applied_and_nothing_pending(self):
        state = migrations_module.status(self.db)
        self.assertEqual(state["pending"], [])
        self.assertEqual(state["problems"], [])
        self.assertEqual(len(state["applied"]), 11)

    def test_applying_twice_changes_nothing(self):
        applied = migrations_module.apply_all(self.db, log=lambda message: None)
        self.assertEqual(applied, [])
        state = migrations_module.status(self.db)
        self.assertEqual(len(state["applied"]), 11)

    def test_checksum_mismatch_is_reported(self):
        with self.db.transaction() as conn:
            conn.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = '0001'")
        with self.db.connection() as conn:
            problems = migrations_module.verify_checksums(conn)
        self.assertTrue(problems)
        self.assertIn("0001", problems[0])
        self.assertEqual(migrations_module.status(self.db)["problems"], problems)

    def test_a_fresh_database_has_the_expected_tables_and_no_rows(self):
        with self.db.connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            self.assertTrue(EXPECTED_TABLES.issubset(tables), EXPECTED_TABLES - tables)
            for table in ("users", "creator_pages", "posts", "tiers", "memberships",
                          "payment_intents", "invoices", "ledger_entries", "webhook_events",
                          "messages", "reports", "admin_notes", "audit_log"):
                count = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
                self.assertEqual(count, 0, f"{table} should start empty")

    def test_migrations_run_inside_a_transaction_and_roll_back_on_failure(self):
        """A broken migration must not leave half a schema behind."""
        broken = self.tmp / "broken-migrations"
        broken.mkdir()
        for name, path in migrations_module.discover():
            (broken / name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        (broken / "0012_broken.sql").write_text(
            "CREATE TABLE should_not_survive (id INTEGER PRIMARY KEY);\n"
            "THIS IS NOT SQL;\n",
            encoding="utf-8",
        )
        fresh = Database(self.tmp / "broken.db")
        with self.assertRaises(migrations_module.MigrationError):
            migrations_module.apply_all(fresh, migrations_dir=broken, log=lambda message: None)
        with fresh.connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertNotIn("should_not_survive", tables)

    def test_unique_indexes_protect_identity_fields(self):
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO users (email, password_hash, display_name, role, status,
                                   email_verified, adult_attested_at, orientation_visibility,
                                   created_at, updated_at)
                VALUES ('dup@velora.test', 'x', 'Dup', 'member', 'active', 0, 'now', 'private',
                        'now', 'now')
                """
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO users (email, password_hash, display_name, role, status,
                                       email_verified, adult_attested_at, orientation_visibility,
                                       created_at, updated_at)
                    VALUES ('dup@velora.test', 'y', 'Dup Two', 'member', 'active', 0, 'now',
                            'private', 'now', 'now')
                    """
                )


class AppendOnlyTests(VeloraTestCase):
    """Financial and moderation history is append-only in the schema itself.

    The rows are created through the real API (a settled payment writes the
    invoice, the three ledger entries and the frozen intent), so the triggers are
    tested against the shapes the application actually writes.
    """

    def setUp(self):
        super().setUp()
        self.admin = Client(self.app)
        self.register("schema-admin@velora.test", display_name="Schema Admin", client=self.admin)
        self.make_admin(self.admin, "schema-admin@velora.test")

        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, email="schema-creator@velora.test",
                                      handle="schema-creator")
        self.member = Client(self.app)
        self.register("schema-member@velora.test", display_name="Schema Member", client=self.member)
        order = self.start_checkout(self.member, self.seed["tier_id"])
        settled = self.settle_and_verify(self.member, order["order_ref"])
        self.assertEqual(settled.json["status"], "settled", settled.text)
        self.order_ref = order["order_ref"]

    def _refuses(self, statements: list[str]):
        for statement in statements:
            with self.assertRaises(sqlite3.IntegrityError, msg=statement):
                with self.db.transaction() as conn:
                    conn.execute(statement)

    def test_ledger_entries_cannot_be_updated_or_deleted(self):
        with self.db.connection() as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM ledger_entries")]
            entry_types = {row["entry_type"] for row in
                           conn.execute("SELECT entry_type FROM ledger_entries")}
        self.assertEqual(entry_types, {"member_payment", "platform_fee", "creator_earning"})
        self._refuses([f"UPDATE ledger_entries SET amount_cents = 1 WHERE id = {entry_id}"
                       for entry_id in ids] +
                      [f"DELETE FROM ledger_entries WHERE id = {entry_id}" for entry_id in ids])

    def test_webhook_events_cannot_be_updated_or_deleted(self):
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO webhook_events (provider, delivery_id, event_type, signature_valid,
                                            outcome, received_at)
                VALUES ('btcpay', 'delivery-1', 'InvoiceSettled', 1, 'settled', ?)
                """,
                (now_iso(),),
            )
        self._refuses(["UPDATE webhook_events SET outcome = 'tampered' WHERE id = 1",
                       "DELETE FROM webhook_events WHERE id = 1"])

    def test_audit_log_cannot_be_updated_or_deleted(self):
        with self.db.connection() as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM audit_log LIMIT 3")]
        self.assertTrue(ids)
        self._refuses([f"UPDATE audit_log SET action = 'rewritten' WHERE id = {entry_id}"
                       for entry_id in ids] +
                      [f"DELETE FROM audit_log WHERE id = {entry_id}" for entry_id in ids])

    def test_settled_invoices_and_their_intents_are_frozen(self):
        with self.db.connection() as conn:
            invoice = conn.execute("SELECT * FROM invoices WHERE order_ref = ?",
                                   (self.order_ref,)).fetchone()
            intent = conn.execute("SELECT * FROM payment_intents WHERE order_ref = ?",
                                  (self.order_ref,)).fetchone()
        self.assertEqual(invoice["status"], "settled")
        self.assertEqual(invoice["amount_cents"], 900)
        self.assertEqual(invoice["platform_fee_cents"], 90)
        self.assertEqual(invoice["creator_net_cents"], 810)
        self.assertEqual(intent["status"], "settled")

        self._refuses([
            "UPDATE invoices SET status = 'pending' WHERE id = %d" % invoice["id"],
            "UPDATE invoices SET amount_cents = 1 WHERE id = %d" % invoice["id"],
            "UPDATE invoices SET platform_fee_cents = 1 WHERE id = %d" % invoice["id"],
            "DELETE FROM invoices WHERE id = %d" % invoice["id"],
            "UPDATE payment_intents SET status = 'held' WHERE id = %d" % intent["id"],
        ])

    def test_settling_again_is_rejected_and_does_not_duplicate_history(self):
        with self.db.connection() as conn:
            before = conn.execute("SELECT COUNT(*) AS c FROM ledger_entries").fetchone()["c"]
            memberships = conn.execute(
                "SELECT COUNT(*) AS c FROM memberships WHERE user_id = "
                "(SELECT id FROM users WHERE email = 'schema-member@velora.test')"
            ).fetchone()["c"]
        replay = self.settle_and_verify(self.member, self.order_ref)
        self.assertEqual(replay.status, 200, replay.text)
        self.assertFalse(replay.json["verification"]["access_granted"])
        self.assertEqual(replay.json["verification"]["reason"], "already_settled")
        with self.db.connection() as conn:
            after = conn.execute("SELECT COUNT(*) AS c FROM ledger_entries").fetchone()["c"]
            memberships_after = conn.execute(
                "SELECT COUNT(*) AS c FROM memberships WHERE user_id = "
                "(SELECT id FROM users WHERE email = 'schema-member@velora.test')"
            ).fetchone()["c"]
        self.assertEqual(before, after)
        self.assertEqual(memberships, memberships_after)

    def test_deleting_a_user_never_cascades_into_settled_history(self):
        with self.db.connection() as conn:
            user_id = conn.execute("SELECT id FROM users WHERE email = 'schema-creator@velora.test'"
                                   ).fetchone()["id"]
            before = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        with self.db.connection() as conn:
            after = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
            still_there = conn.execute("SELECT COUNT(*) AS c FROM users WHERE id = ?",
                                       (user_id,)).fetchone()["c"]
        self.assertEqual(before, after)
        self.assertEqual(still_there, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
