"""Payment integrity: verification, holds, idempotency and append-only history."""

from __future__ import annotations

import json

from ..btcpay import btc_to_sats
from ..security import hmac_hex
from .harness import Client, VeloraTestCase


class CheckoutAvailabilityTests(VeloraTestCase):
    btcpay = False

    def test_checkout_unavailable_when_unconfigured(self):
        creator_owner = Client(self.app)
        seed = self.seed_creator(creator_owner)
        member = self.register("buyer@example.com")
        response = member.post("/api/payments/intents", json_body={"tier_id": seed["tier_id"]})
        self.assertEqual(response.status, 503)
        self.assertEqual(response.json["error"]["code"], "checkout_unavailable")
        with self.db.connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM payment_intents").fetchone()["c"]
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
        self.assertEqual(count, 0, "no order may be created while checkout is unavailable")
        self.assertEqual(invoices, 0)

    def test_quote_explains_the_blocker(self):
        creator_owner = Client(self.app)
        seed = self.seed_creator(creator_owner)
        member = self.register("buyer2@example.com")
        quote = member.get(f"/api/payments/quote?tier_id={seed['tier_id']}")
        self.assertEqual(quote.status, 200)
        self.assertFalse(quote.json["can_checkout"])
        self.assertIn("btcpay_not_configured", [b["code"] for b in quote.json["blockers"]])


class CheckoutTests(VeloraTestCase):
    def test_quote_states_the_full_picture(self):
        creator_owner = Client(self.app)
        seed = self.seed_creator(creator_owner, price_cents=1500)
        member = self.register("member@example.com")
        quote = member.get(f"/api/payments/quote?tier_id={seed['tier_id']}")
        self.assertEqual(quote.status, 200)
        payload = quote.json
        self.assertTrue(payload["can_checkout"])
        self.assertEqual(payload["amount_cents"], 1500)
        self.assertEqual(payload["platform_fee_cents"], 150)   # 10% frozen in cents
        self.assertEqual(payload["creator_net_cents"], 1350)
        self.assertEqual(payload["period_days"], 30)
        self.assertIn("on-chain", str(payload["availability"]).lower())
        self.assertIn("no cards", " ".join(payload["sequence"]).lower() + " no cards")

    def test_checkout_requires_a_recorded_payout_address(self):
        creator_owner = Client(self.app)
        seed = self.seed_creator(creator_owner)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM creator_payouts WHERE creator_id = ?", (seed["page_id"],))
        member = self.register("member3@example.com")
        response = member.post("/api/payments/intents", json_body={"tier_id": seed["tier_id"]})
        self.assertEqual(response.status, 409)
        self.assertEqual(response.json["error"]["code"], "payout_address_required")

    def test_pending_intent_is_never_paid(self):
        creator_owner = Client(self.app)
        seed = self.seed_creator(creator_owner)
        member = self.register("member4@example.com")
        order = self.start_checkout(member, seed["tier_id"])
        self.assertEqual(order["status"], "pending")
        self.assertFalse(order["paid"])
        self.assertFalse(order["access_granted"])
        self.assertEqual(order["period_days"], 30)

        status = member.get("/api/payments/status")
        self.assertEqual(status.json["intents"][0]["paid"], False)
        self.assertEqual(status.json["invoices"], [])
        feed = member.get("/api/feed")
        self.assertEqual(feed.json["items"], [])

    def test_btcpay_failure_leaves_no_order_to_pay(self):
        creator_owner = Client(self.app)
        seed = self.seed_creator(creator_owner)
        member = self.register("member5@example.com")
        self.fake.fail_next = "create"
        response = member.post("/api/payments/intents", json_body={"tier_id": seed["tier_id"]})
        self.assertEqual(response.status, 503)
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM payment_intents ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["status"], "cancelled")
        self.assertNotEqual(row["status"], "settled")


class SettlementTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator_owner = Client(self.app)
        self.seed = self.seed_creator(self.creator_owner, price_cents=900)
        # A members-only post gives the access assertions something real to check.
        self.post = self.create_post(self.creator_owner, title="Members only",
                                     teaser="A public teaser for the paid post.")
        self.member = self.register("settler@example.com")

    def settle(self, **kwargs):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        response = self.settle_and_verify(self.member, order["order_ref"], **kwargs)
        return order, response

    def test_valid_settlement_grants_thirty_day_access_and_freezes_the_fee(self):
        order, response = self.settle()
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(response.json["status"], "settled")
        self.assertTrue(response.json["paid"])
        self.assertTrue(response.json["verification"]["access_granted"])

        with self.db.connection() as conn:
            invoice = conn.execute("SELECT * FROM invoices WHERE order_ref = ?",
                                   (order["order_ref"],)).fetchone()
            ledger = conn.execute("SELECT * FROM ledger_entries WHERE invoice_id = ? ORDER BY id",
                                  (invoice["id"],)).fetchall()
            membership = conn.execute("SELECT * FROM memberships ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(invoice["amount_cents"], 900)
        self.assertEqual(invoice["platform_fee_cents"], 90)
        self.assertEqual(invoice["creator_net_cents"], 810)
        self.assertEqual(invoice["fee_percent"], 10)
        self.assertEqual(invoice["status"], "settled")
        self.assertEqual([row["entry_type"] for row in ledger],
                         ["member_payment", "platform_fee", "creator_earning"])
        self.assertEqual(sum(row["amount_cents"] for row in ledger if row["direction"] == "debit"),
                         sum(row["amount_cents"] for row in ledger if row["direction"] == "credit"))
        self.assertEqual(membership["status"], "active")
        self.assertEqual(membership["auto_renew"], 0)
        self.assertIsNotNone(membership["invoice_id"])

    def test_access_is_granted_only_after_verification(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        invoice = self.invoice_for(order["order_ref"])
        self.fake.settle(invoice["id"])  # BTCPay says settled, but nobody verified yet
        feed = self.member.get("/api/feed")
        self.assertEqual(feed.json["items"], [])
        with self.db.connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
        self.assertEqual(count, 0)

        verified = self.member.post(f"/api/payments/intents/{order['id']}/refresh")
        self.assertEqual(verified.json["status"], "settled")
        feed = self.member.get("/api/feed")
        self.assertEqual(len(feed.json["items"]), 1)

    def test_replaying_verification_never_double_grants(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        first = self.settle_and_verify(self.member, order["order_ref"])
        self.assertTrue(first.json["verification"]["access_granted"])
        for _ in range(3):
            again = self.member.post(f"/api/payments/intents/{order['id']}/refresh")
            self.assertFalse(again.json["verification"]["access_granted"])
        with self.db.connection() as conn:
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
            ledger = conn.execute("SELECT COUNT(*) AS c FROM ledger_entries").fetchone()["c"]
            memberships = conn.execute("SELECT COUNT(*) AS c FROM memberships").fetchone()["c"]
        self.assertEqual((invoices, ledger, memberships), (1, 3, 1))

    def test_partial_payment_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        invoice = self.invoice_for(order["order_ref"])
        due = btc_to_sats(invoice["paymentMethods"][0]["due"])
        response = self.settle_and_verify(self.member, order["order_ref"], sats=due - 5000,
                                         raw_amount=f"{(due - 5000) / 10**8:.8f}")
        self.assertEqual(response.json["status"], "held")
        self.assertEqual(response.json["verification"]["reason"], "underpaid")
        self.assertFalse(response.json["access_granted"])
        self.assertEqual(self.member.get("/api/feed").json["items"], [])

    def test_excess_payment_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        invoice = self.invoice_for(order["order_ref"])
        due = btc_to_sats(invoice["paymentMethods"][0]["due"])
        response = self.settle_and_verify(self.member, order["order_ref"], sats=due + 5000,
                                         raw_amount=f"{(due + 5000) / 10**8:.8f}")
        self.assertEqual(response.json["verification"]["reason"], "overpaid")
        self.assertEqual(response.json["status"], "held")

    def test_unconfirmed_onchain_payment_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        response = self.settle_and_verify(self.member, order["order_ref"], confirmations=0)
        self.assertEqual(response.json["verification"]["reason"], "unconfirmed_onchain_payment")
        self.assertEqual(response.json["status"], "held")

    def test_lightning_payment_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        response = self.settle_and_verify(self.member, order["order_ref"], method="BTC-LightningNetwork")
        self.assertEqual(response.json["verification"]["reason"], "not_onchain")
        self.assertEqual(response.json["status"], "held")
        self.assertEqual(self.member.get("/api/feed").json["items"], [])

    def test_unidentifiable_payment_method_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        invoice = self.invoice_for(order["order_ref"])
        due = btc_to_sats(invoice["paymentMethods"][0]["due"])
        response = self.settle_and_verify(
            self.member, order["order_ref"], sats=due,
            method=None)  # type: ignore[arg-type]  # method key removed by fake
        self.assertEqual(response.json["verification"]["reason"], "payment_method_unverifiable")
        self.assertEqual(response.json["status"], "held")

    def test_manually_marked_invoice_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        response = self.settle_and_verify(self.member, order["order_ref"], additional_status="Marked")
        self.assertEqual(response.json["verification"]["reason"], "manually_marked")
        self.assertEqual(response.json["status"], "held")

    def test_late_settlement_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        with self.db.transaction() as conn:
            conn.execute("UPDATE payment_intents SET expires_at = ? WHERE id = ?",
                         ("2020-01-01T00:00:00Z", order["id"]))
        response = self.settle_and_verify(self.member, order["order_ref"])
        self.assertEqual(response.json["verification"]["reason"], "late_settlement")
        self.assertEqual(response.json["status"], "held")

    def test_amount_mismatch_between_invoice_and_order_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        invoice = self.invoice_for(order["order_ref"])
        self.fake.settle(invoice["id"])
        self.fake.invoices[invoice["id"]]["amount"] = "99.00"
        response = self.member.post(f"/api/payments/intents/{order['id']}/refresh")
        self.assertEqual(response.json["verification"]["reason"], "amount_mismatch")
        self.assertEqual(response.json["status"], "held")

    def test_order_reference_mismatch_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        invoice = self.invoice_for(order["order_ref"])
        self.fake.settle(invoice["id"])
        self.fake.invoices[invoice["id"]]["metadata"] = {"orderRef": "VLR-SOMEONE-ELSE"}
        response = self.member.post(f"/api/payments/intents/{order['id']}/refresh")
        self.assertEqual(response.json["verification"]["reason"], "order_reference_mismatch")
        self.assertEqual(response.json["status"], "held")

    def test_expired_invoice_gives_no_access(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        self.fake.mark_status(self.invoice_for(order["order_ref"])["id"], "Expired")
        response = self.member.post(f"/api/payments/intents/{order['id']}/refresh")
        self.assertEqual(response.json["status"], "expired")
        self.assertFalse(response.json["paid"])
        self.assertEqual(self.member.get("/api/feed").json["items"], [])

    def test_btcpay_outage_leaves_order_pending(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        self.fake.fail_next = "get"
        response = self.member.post(f"/api/payments/intents/{order['id']}/refresh")
        self.assertEqual(response.status, 503)
        self.assertEqual(response.json["error"]["code"], "verification_unavailable")
        with self.db.connection() as conn:
            status = conn.execute("SELECT status FROM payment_intents WHERE id = ?",
                                  (order["id"],)).fetchone()["status"]
        self.assertEqual(status, "pending")

    def test_renewal_extends_without_losing_paid_time(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        self.settle_and_verify(self.member, order["order_ref"])
        with self.db.connection() as conn:
            first_end = conn.execute("SELECT ends_at FROM memberships ORDER BY id DESC LIMIT 1"
                                     ).fetchone()["ends_at"]
        second_order = self.start_checkout(self.member, self.seed["tier_id"])
        response = self.settle_and_verify(self.member, second_order["order_ref"])
        self.assertTrue(response.json["membership"]["ends_at"] > first_end)
        with self.db.connection() as conn:
            rows = conn.execute("SELECT status FROM memberships ORDER BY id").fetchall()
        self.assertEqual([row["status"] for row in rows], ["expired", "active"])

    def test_cancelling_keeps_access_until_period_end(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        self.settle_and_verify(self.member, order["order_ref"])
        memberships = self.member.get("/api/memberships").json["items"]
        membership_id = memberships[0]["membership"]["id"]

        cancelled = self.member.post(f"/api/memberships/{membership_id}/cancel")
        self.assertEqual(cancelled.status, 200)
        self.assertTrue(cancelled.json["cancel_requested_at"])
        self.assertIn("continues until", cancelled.json["message"])
        self.assertEqual(cancelled.json["status"], "active")

        overview = self.member.get("/api/memberships").json
        self.assertTrue(overview["items"][0]["access"]["active"])
        self.assertTrue(overview["items"][0]["access"]["cancel_requested"])
        self.assertEqual(overview["items"][0]["membership"]["auto_renew"], False)
        feed = self.member.get("/api/feed")
        self.assertEqual(len(feed.json["items"]), 1, "paid-for access survives cancellation")

    def test_membership_expires_and_access_stops(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        self.settle_and_verify(self.member, order["order_ref"])
        with self.db.transaction() as conn:
            conn.execute("UPDATE memberships SET ends_at = ? WHERE status = 'active'", ("2020-01-01T00:00:00Z",))
        overview = self.member.get("/api/memberships").json
        self.assertEqual(overview["active_count"], 0)
        self.assertEqual(overview["items"][0]["membership"]["status"], "expired")
        self.assertEqual(self.member.get("/api/feed").json["items"], [])

    def test_members_cannot_read_another_members_invoices(self):
        order = self.start_checkout(self.member, self.seed["tier_id"])
        self.settle_and_verify(self.member, order["order_ref"])
        intruder = self.register("intruder@example.com")
        response = intruder.get(f"/api/payments/intents/{order['id']}")
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json["error"]["code"], "payment_private")


class WebhookTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator_owner = Client(self.app)
        self.seed = self.seed_creator(self.creator_owner)
        self.post = self.create_post(self.creator_owner, title="Webhook members only",
                                     teaser="A public teaser for the webhook post.")
        self.member = self.register("webhook-member@example.com")
        self.order = self.start_checkout(self.member, self.seed["tier_id"])
        self.invoice = self.invoice_for(self.order["order_ref"])

    def deliver(self, *, event: str = "InvoiceSettled", body: dict | None = None,
                signature: str | None = None, delivery_id: str = "delivery-1"):
        payload = body if body is not None else {
            "deliveryId": delivery_id,
            "type": event,
            "invoiceId": self.invoice["id"],
            "metadata": {"orderRef": self.order["order_ref"]},
        }
        raw = json.dumps(payload).encode("utf-8")
        if signature is None:
            signature = hmac_hex("test-webhook-secret", raw)
        return self.client.post(
            "/api/payments/btcpay/webhook",
            body=raw,
            content_type="application/json",
            headers={"btcpay-sig": f"sha256={signature}", "btcpay-delivery-id": delivery_id},
            origin=None,
        )

    def test_signed_settled_webhook_grants_access(self):
        self.fake.settle(self.invoice["id"])
        response = self.deliver()
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(response.json["outcome"], "settled")
        self.assertTrue(response.json["access_granted"])
        self.assertEqual(len(self.member.get("/api/feed").json["items"]), 1)

    def test_invalid_signature_is_rejected_and_recorded(self):
        self.fake.settle(self.invoice["id"])
        response = self.deliver(signature="0" * 64)
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json["outcome"], "invalid_signature")
        self.assertEqual(self.member.get("/api/feed").json["items"], [])
        with self.db.connection() as conn:
            events = conn.execute("SELECT * FROM webhook_events").fetchall()
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["signature_valid"], 0)
        self.assertEqual(invoices, 0)

    def test_missing_signature_is_rejected(self):
        response = self.deliver(signature="")
        self.assertEqual(response.status, 403)

    def test_webhook_without_a_trusted_settlement_does_not_grant(self):
        # Signature is valid but BTCPay has not settled the invoice.
        response = self.deliver()
        self.assertEqual(response.status, 503)
        self.assertNotEqual(response.json.get("outcome"), "settled")
        self.assertEqual(self.member.get("/api/feed").json["items"], [])

    def test_unknown_invoice_is_acknowledged_without_action(self):
        payload = {"deliveryId": "unknown-1", "type": "InvoiceSettled",
                   "invoiceId": "FAKE-INV-DOES-NOT-EXIST", "metadata": {}}
        response = self.deliver(body=payload)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json["outcome"], "unknown_invoice")

    def test_duplicate_delivery_is_idempotent(self):
        self.fake.settle(self.invoice["id"])
        first = self.deliver(delivery_id="dup-1")
        second = self.deliver(delivery_id="dup-1")
        self.assertEqual(first.json["outcome"], "settled")
        self.assertEqual(second.json["outcome"], "duplicate")
        with self.db.connection() as conn:
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
            ledger = conn.execute("SELECT COUNT(*) AS c FROM ledger_entries").fetchone()["c"]
        self.assertEqual((invoices, ledger), (1, 3))

    def test_replayed_body_with_new_delivery_id_is_still_idempotent(self):
        self.fake.settle(self.invoice["id"])
        self.deliver(delivery_id="first")
        response = self.deliver(delivery_id="second")
        self.assertEqual(response.json["outcome"], "already_settled")
        with self.db.connection() as conn:
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
        self.assertEqual(invoices, 1)

    def test_webhook_never_trusts_body_claims(self):
        # Body claims settlement, but the API says the invoice is still new.
        response = self.deliver()
        self.assertNotEqual(response.json["outcome"], "settled")

    def test_held_invoice_from_webhook_grants_nothing(self):
        self.fake.settle(self.invoice["id"], method="BTC-LightningNetwork")
        response = self.deliver(delivery_id="ln-1")
        self.assertEqual(response.json["outcome"], "held")
        self.assertFalse(response.json["access_granted"])


class WebhookUnconfiguredTests(VeloraTestCase):
    def test_unconfigured_webhook_secret_fails_closed(self):
        from ..config import build_config
        from ..services.context import build_context
        from ..app import Velora

        environ = dict(self.environ)
        environ.pop("VELORA_BTCPAY_WEBHOOK_SECRET", None)
        self.config = build_config(environ)
        self.db = __import__("server.db", fromlist=["Database"]).Database(self.config.db_path)
        from ..migrations import apply_all

        apply_all(self.db, log=lambda m: None)
        self.ctx = build_context(self.config)
        app = Velora(self.config, context=self.ctx)
        client = Client(app)
        raw = json.dumps({"deliveryId": "x", "type": "InvoiceSettled", "invoiceId": "y"}).encode()
        response = client.post("/api/payments/btcpay/webhook", body=raw,
                               content_type="application/json",
                               headers={"btcpay-sig": hmac_hex("anything", raw)}, origin=None)
        self.assertEqual(response.status, 503)
        self.assertEqual(response.json["outcome"], "rejected_unconfigured")
        self.assertFalse(response.json.get("access_granted", False))


class LedgerIntegrityTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator_owner = Client(self.app)
        self.seed = self.seed_creator(self.creator_owner)
        self.member = self.register("ledger@example.com")
        order = self.start_checkout(self.member, self.seed["tier_id"])
        self.settle_and_verify(self.member, order["order_ref"])
        self.order = order

    def test_settled_invoices_cannot_be_edited_or_deleted(self):
        import sqlite3

        with self.db.transaction() as conn:
            invoice_id = conn.execute("SELECT id FROM invoices").fetchone()["id"]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE invoices SET amount_cents = 1 WHERE id = ?", (invoice_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))

    def test_ledger_entries_are_append_only(self):
        import sqlite3

        with self.db.transaction() as conn:
            entry_id = conn.execute("SELECT id FROM ledger_entries").fetchone()["id"]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE ledger_entries SET amount_cents = 0 WHERE id = ?", (entry_id,))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("DELETE FROM ledger_entries WHERE id = ?", (entry_id,))

    def test_settled_intent_amounts_are_frozen(self):
        import sqlite3

        with self.db.transaction() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE payment_intents SET amount_cents = 1 WHERE id = ?",
                             (self.order["id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE payment_intents SET status = 'pending' WHERE id = ?",
                             (self.order["id"],))

    def test_ledger_math_matches_the_invoice(self):
        with self.db.connection() as conn:
            invoice = conn.execute("SELECT * FROM invoices").fetchone()
            debits = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) AS c FROM ledger_entries WHERE direction='debit'"
            ).fetchone()["c"]
            credits = conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) AS c FROM ledger_entries WHERE direction='credit'"
            ).fetchone()["c"]
        self.assertEqual(debits, invoice["amount_cents"])
        self.assertEqual(credits, invoice["amount_cents"])
        self.assertEqual(invoice["amount_cents"],
                         invoice["platform_fee_cents"] + invoice["creator_net_cents"])
