"""Paying in coins and Tether other than BTC, with the same fail-closed settlement rules."""

from __future__ import annotations

import sqlite3

from .harness import Client, VeloraTestCase

ETH = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
BSC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"


class MultiAssetBase(VeloraTestCase):
    wallets = {"eth": ETH, "usdt_trc20": TRON, "usdt_bep20": BSC}

    def setUp(self):
        super().setUp()
        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, price_cents=1250, wallets=self.wallets)
        self.member = self.register("multi-member@example.com")

    def quote(self, asset=None):
        suffix = f"&asset={asset}" if asset else ""
        return self.member.get(f"/api/payments/quote?tier_id={self.seed['tier_id']}{suffix}")

    def intent_row(self, order_ref):
        with self.db.connection() as conn:
            return dict(conn.execute("SELECT * FROM payment_intents WHERE order_ref = ?",
                                     (order_ref,)).fetchone())

    def invoice_row(self, order_ref):
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM invoices WHERE order_ref = ?", (order_ref,)).fetchone()
        return dict(row) if row else None


class OptionsTests(MultiAssetBase):
    def test_quote_lists_only_coins_the_creator_has_a_wallet_for(self):
        payload = self.quote().json
        self.assertTrue(payload["can_checkout"])
        options = {o["key"]: o for o in payload["payment_options"]}
        self.assertEqual(sorted(options), ["btc", "eth", "usdt_bep20", "usdt_trc20"])
        self.assertTrue(all(o["available"] for o in options.values()))
        self.assertEqual(options["usdt_trc20"]["symbol"], "USDT")
        self.assertEqual(options["usdt_trc20"]["network"], "Tron (TRC-20)")
        self.assertTrue(options["usdt_trc20"]["warning"])
        self.assertNotIn("address", options["eth"], "wallet addresses never reach a member")
        self.assertNotIn(ETH, self.quote().text)
        self.assertNotIn(TRON, self.quote().text)

    def test_a_coin_the_store_has_not_enabled_is_not_offered(self):
        self.fake.enabled_methods = {"BTC-CHAIN", "USDT_TRC20-CHAIN"}
        options = {o["key"]: o for o in self.quote().json["payment_options"]}
        self.assertTrue(options["btc"]["available"] and options["usdt_trc20"]["available"])
        self.assertFalse(options["eth"]["available"])
        self.assertEqual(options["eth"]["unavailable_reason"], "not_enabled_on_store")
        response = self.member.post("/api/payments/intents",
                                    json_body={"tier_id": self.seed["tier_id"], "asset": "eth"})
        self.assertEqual(response.status, 409, response.text)
        self.assertEqual(response.json["error"]["code"], "asset_unavailable")
        self.assertEqual(self.fake.created, [], "no invoice may be requested for an unavailable coin")

    def test_when_nothing_the_creator_accepts_is_payable_checkout_is_blocked(self):
        self.fake.enabled_methods = {"LTC-CHAIN"}
        payload = self.quote().json
        self.assertFalse(payload["can_checkout"])
        self.assertIn("no_payment_method_available", [b["code"] for b in payload["blockers"]])
        response = self.member.post("/api/payments/intents", json_body={"tier_id": self.seed["tier_id"]})
        self.assertEqual(response.status, 409, response.text)
        self.assertEqual(self.fake.created, [])

    def test_unreachable_store_fails_closed(self):
        self.fake.fail_next = "methods"
        payload = self.quote().json
        self.assertFalse(payload["can_checkout"])
        self.assertIn("checkout_unavailable", [b["code"] for b in payload["blockers"]])
        self.assertTrue(all(not o["available"] for o in payload["payment_options"]))
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM payment_intents").fetchone()["c"], 0)

    def test_store_methods_are_cached_briefly(self):
        self.quote()
        self.fake.fail_next = "methods"          # would fail if asked again
        self.assertTrue(self.quote().json["can_checkout"])

    def test_creator_without_any_wallet_cannot_be_paid(self):
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM creator_wallets")
        payload = self.quote().json
        self.assertEqual([b["code"] for b in payload["blockers"]], ["payout_address_required"])
        response = self.member.post("/api/payments/intents", json_body={"tier_id": self.seed["tier_id"]})
        self.assertEqual(response.status, 409)
        self.assertEqual(response.json["error"]["code"], "payout_address_required")

    def test_unknown_or_malformed_asset_is_a_validation_error(self):
        for asset in ("notacoin", 5, ["btc"]):
            response = self.member.post("/api/payments/intents",
                                        json_body={"tier_id": self.seed["tier_id"], "asset": asset})
            self.assertEqual(response.status, 400, f"{asset!r}: {response.text}")
            self.assertEqual(response.json["error"]["field"], "asset")
        self.assertEqual(self.fake.created, [])

    def test_a_coin_without_a_creator_wallet_cannot_be_chosen(self):
        response = self.member.post("/api/payments/intents",
                                    json_body={"tier_id": self.seed["tier_id"], "asset": "sol"})
        self.assertEqual(response.status, 409, response.text)
        self.assertEqual(response.json["error"]["code"], "asset_unavailable")

    def test_choice_is_required_when_several_coins_are_possible(self):
        response = self.member.post("/api/payments/intents", json_body={"tier_id": self.seed["tier_id"]})
        self.assertEqual(response.status, 400, response.text)
        self.assertEqual(response.json["error"]["field"], "asset")
        self.assertEqual(self.fake.created, [])

    def test_operator_allowlist_hides_other_coins(self):
        self.ctx.config = type(self.config)(**{**self.config.__dict__, "payment_methods": ("btc", "eth")})
        options = {o["key"]: o for o in self.quote().json["payment_options"]}
        self.assertFalse(options["usdt_trc20"]["available"])
        self.assertEqual(options["usdt_trc20"]["unavailable_reason"], "not_offered")
        self.assertTrue(options["eth"]["available"])

    def test_single_option_creators_keep_the_original_one_click_flow(self):
        other = Client(self.app)
        solo = self.seed_creator(other, email="solo@velora.test", handle="solo", page_name="Solo")
        order = self.start_checkout(self.member, solo["tier_id"])
        self.assertEqual(order["asset"], "btc")
        self.assertEqual(self.fake.created[-1]["paymentMethods"][0]["paymentMethod"], "BTC-CHAIN")


class CheckoutAndSettlementTests(MultiAssetBase):
    def test_invoice_is_requested_for_exactly_the_chosen_method(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="usdt_trc20")
        self.assertEqual(order["asset"], "usdt_trc20")
        self.assertEqual(order["asset_symbol"], "USDT")
        self.assertEqual(order["network"], "Tron (TRC-20)")
        self.assertEqual(order["payment_method"], "USDT_TRC20-CHAIN")
        self.assertEqual(order["asset_amount"], "12.5")            # $12.50 of a $1 stablecoin
        self.assertIsNone(order["btc_amount_sats"])
        invoice = self.invoice_for(order["order_ref"])
        self.assertEqual(invoice["paymentMethods"][0]["paymentMethod"], "USDT_TRC20-CHAIN")
        intent = self.intent_row(order["order_ref"])
        self.assertEqual((intent["asset"], intent["payment_method"]), ("usdt_trc20", "USDT_TRC20-CHAIN"))
        self.assertEqual(intent["status"], "pending")

    def test_requested_payment_methods_exclude_lightning_and_every_other_asset(self):
        sent = []
        original = self.fake.create_invoice

        def spy(**kwargs):
            sent.append(kwargs["payment_method"])
            return original(**kwargs)

        self.fake.create_invoice = spy
        self.start_checkout(self.member, self.seed["tier_id"], asset="eth")
        self.assertEqual(sent, ["ETH-CHAIN"])

    def test_ethereum_settlement_records_the_asset_and_the_matching_wallet(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="eth")
        self.assertEqual(order["asset_amount"], "0.004166666666666667")   # $12.50 at a pretend $3000
        response = self.settle_and_verify(self.member, order["order_ref"])
        self.assertEqual(response.json["status"], "settled", response.text)
        self.assertTrue(response.json["verification"]["access_granted"])

        invoice = self.invoice_row(order["order_ref"])
        self.assertEqual(invoice["asset"], "eth")
        self.assertEqual(invoice["payment_method"], "ETH-CHAIN")
        self.assertEqual(invoice["asset_amount_atomic"], "4166666666666667")
        self.assertEqual(invoice["btc_amount_sats"], 0)
        self.assertEqual(invoice["payout_address_snapshot"], ETH)
        self.assertEqual(invoice["payout_asset"], "eth")
        self.assertEqual((invoice["amount_cents"], invoice["platform_fee_cents"], invoice["creator_net_cents"]),
                         (1250, 125, 1125))
        with self.db.connection() as conn:
            ledger = conn.execute("SELECT * FROM ledger_entries WHERE invoice_id = ? ORDER BY id",
                                  (invoice["id"],)).fetchall()
        self.assertEqual([r["amount_cents"] for r in ledger], [1250, 125, 1125])
        self.assertIn("ETH", ledger[0]["memo"])
        self.assertIn("ETH", ledger[2]["memo"])
        self.assertEqual(self.member.get("/api/feed").status, 200)

        status = self.member.get("/api/payments/status").json
        self.assertEqual(status["invoices"][0]["asset"], "eth")
        self.assertEqual(status["invoices"][0]["asset_amount"], "0.004166666666666667")
        self.assertIsNone(status["invoices"][0]["btc_amount_sats"])
        self.assertNotIn(ETH, self.member.get("/api/payments/status").text)

    def test_tether_on_tron_and_an_18_decimal_tether_both_settle_exactly(self):
        for asset, wallet, atomic in (("usdt_trc20", TRON, "12500000"),
                                      ("usdt_bep20", BSC, "12500000000000000000")):
            order = self.start_checkout(self.member, self.seed["tier_id"], asset=asset)
            response = self.settle_and_verify(self.member, order["order_ref"])
            self.assertEqual(response.json["status"], "settled", f"{asset}: {response.text}")
            invoice = self.invoice_row(order["order_ref"])
            self.assertEqual(invoice["asset_amount_atomic"], atomic, asset)
            self.assertEqual(invoice["payout_address_snapshot"], wallet, asset)
            self.assertEqual(self.intent_row(order["order_ref"])["asset_amount_atomic"], atomic)

    def test_bitcoin_still_reports_the_original_sats_fields(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="btc")
        self.assertEqual(order["btc_amount_sats"], 20_833)
        self.assertEqual(order["asset_amount"], "0.00020833")
        response = self.settle_and_verify(self.member, order["order_ref"])
        self.assertEqual(response.json["status"], "settled")
        invoice = self.member.get("/api/payments/status").json["invoices"][0]
        self.assertEqual(invoice["btc_amount_sats"], 20_833)
        self.assertEqual(invoice["asset"], "btc")

    def test_paying_with_a_different_coin_than_chosen_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="usdt_trc20")
        # Member sends BTC (or anything else) against a Tether invoice.
        for method in ("BTC-CHAIN", "ETH-CHAIN", "BTC-LightningNetwork"):
            response = self.settle_and_verify(self.member, order["order_ref"], method=method, units=1250000)
            self.assertEqual(response.json["verification"]["reason"], "not_onchain", method)
            self.assertEqual(response.json["status"], "held", method)
        self.assertIsNone(self.invoice_row(order["order_ref"]))
        self.assertEqual(self.member.get("/api/feed").json["items"], [])

    def test_a_second_payment_in_another_asset_blocks_auto_settlement(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="eth")
        extra = {"status": "Settled", "amount": "0.001", "method": "BTC-CHAIN", "confirmations": 3,
                 "settledTime": "2026-01-01T00:00:00Z"}
        invoice = self.invoice_for(order["order_ref"])
        self.fake.settle(invoice["id"], extra_payment=extra)
        response = self.member.post(f"/api/payments/intents/{order['id']}/refresh")
        self.assertEqual(response.json["status"], "held", response.text)
        self.assertIsNone(self.invoice_row(order["order_ref"]))

    def test_underpaid_and_overpaid_stablecoin_invoices_are_held(self):
        for units, reason in ((12_499_999, "underpaid"), (12_500_001, "overpaid")):
            order = self.start_checkout(self.member, self.seed["tier_id"], asset="usdt_trc20")
            response = self.settle_and_verify(self.member, order["order_ref"], units=units)
            self.assertEqual(response.json["verification"]["reason"], reason)
            self.assertEqual(response.json["status"], "held")
            self.assertIsNone(self.invoice_row(order["order_ref"]))

    def test_unconfirmed_token_payment_is_held(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="usdt_trc20")
        response = self.settle_and_verify(self.member, order["order_ref"], confirmations=0)
        self.assertEqual(response.json["verification"]["reason"], "unconfirmed_onchain_payment")
        self.assertIsNone(self.invoice_row(order["order_ref"]))

    def test_replaying_a_settlement_never_grants_twice(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="eth")
        self.settle_and_verify(self.member, order["order_ref"])
        again = self.member.post(f"/api/payments/intents/{order['id']}/refresh")
        self.assertEqual(again.status, 200)
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM ledger_entries").fetchone()["c"], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM memberships").fetchone()["c"], 1)

    def test_the_snapshot_survives_a_later_wallet_change(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="eth")
        self.settle_and_verify(self.member, order["order_ref"])
        other = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        self.creator.put("/api/studio/wallets/eth", json_body={"address": other})
        self.assertEqual(self.invoice_row(order["order_ref"])["payout_address_snapshot"], ETH)

    def test_a_removed_wallet_leaves_the_snapshot_empty_for_operator_review(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="eth")
        self.creator.delete("/api/studio/wallets/eth")
        response = self.settle_and_verify(self.member, order["order_ref"])
        self.assertEqual(response.json["status"], "settled")
        invoice = self.invoice_row(order["order_ref"])
        self.assertIsNone(invoice["payout_address_snapshot"])
        self.assertIsNone(invoice["payout_asset"])

    def test_a_settled_order_keeps_its_asset(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="eth")
        self.settle_and_verify(self.member, order["order_ref"])
        with self.db.transaction() as conn:
            for column, value in (("asset", "btc"), ("payment_method", "BTC-CHAIN"),
                                  ("asset_amount_atomic", "1")):
                with self.assertRaises(sqlite3.IntegrityError, msg=column):
                    conn.execute(f"UPDATE payment_intents SET {column} = ? WHERE order_ref = ?",
                                 (value, order["order_ref"]))

    def test_unknown_stored_asset_does_not_break_serializers(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="eth")
        status = self.member.get("/api/payments/status")
        self.assertEqual(status.status, 200)
        self.assertEqual(status.json["intents"][0]["order_ref"], order["order_ref"])


class AdminVisibilityTests(MultiAssetBase):
    def test_admin_payment_listing_shows_the_asset(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="usdt_trc20")
        self.settle_and_verify(self.member, order["order_ref"])
        admin = self.register("multi-admin@velora.test", display_name="Ops")
        self.make_admin(admin, "multi-admin@velora.test")
        listing = admin.get("/api/admin/payments")
        self.assertEqual(listing.status, 200, listing.text)
        item = listing.json["items"][0]
        self.assertEqual(item["asset"], "usdt_trc20")
        self.assertEqual(item["asset_symbol"], "USDT")
        self.assertEqual(item["asset_amount"], "12.5")


class ExportTests(MultiAssetBase):
    def test_account_export_names_the_asset_of_each_attempt_and_invoice(self):
        order = self.start_checkout(self.member, self.seed["tier_id"], asset="usdt_trc20")
        self.settle_and_verify(self.member, order["order_ref"])
        export = self.member.get("/api/account/export")
        self.assertEqual(export.status, 200, export.text)
        attempt = export.json["payment_attempts"][0]
        invoice = export.json["settled_invoices"][0]
        self.assertEqual(attempt["asset"], "usdt_trc20")
        self.assertEqual(invoice["asset"], "usdt_trc20")
        self.assertEqual(invoice["asset_amount_atomic"], attempt["asset_amount_atomic"])
        self.assertNotIn("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", export.text)


class UnconfiguredTests(VeloraTestCase):
    btcpay = False

    def test_without_btcpay_no_coin_is_payable_and_no_order_is_created(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, wallets={"eth": ETH})
        member = self.register("unconfigured-multi@example.com")
        for asset in (None, "eth", "btc"):
            body = {"tier_id": seed["tier_id"]}
            if asset:
                body["asset"] = asset
            response = member.post("/api/payments/intents", json_body=body)
            self.assertEqual(response.status, 503, response.text)
        bootstrap = Client(self.app).get("/api/bootstrap").json
        self.assertFalse(bootstrap["checkout"]["available"])
        self.assertEqual(bootstrap["checkout"]["methods"], [])
        self.assertEqual(bootstrap["features"]["payment_methods"], [])
        # The coin catalogue is still published so creators can record wallets.
        self.assertIn("usdt_trc20", [a["key"] for a in bootstrap["payment_assets"]])
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM payment_intents").fetchone()["c"], 0)
