"""Creator wallet addresses: per-network validation, privacy, application flow, upgrade path."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .. import migrations as migrations_module
from ..config import build_config
from ..db import Database
from ..wallets import (
    ASSETS,
    CATALOG,
    b58check_encode,
    eip55_checksum,
    format_atomic,
    keccak256,
    parse_asset_list,
    parse_method_overrides,
    to_atomic,
    validate_address,
)
from ..validation import ValidationError
from .harness import Client, VeloraTestCase

# Well-known public addresses (published in specs, explorers and docs).
VALID = {
    "btc": ["bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
            "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
            "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"],
    "bch": ["bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6a", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"],
    "eth": ["0xdAC17F958D2ee523a2206206994597C13D831ec7", "0xdac17f958d2ee523a2206206994597c13d831ec7"],
    "bnb": ["0xdAC17F958D2ee523a2206206994597C13D831ec7"],
    "trx": ["TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"],
    "usdt_trc20": ["TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"],
    "usdt_erc20": ["0xdAC17F958D2ee523a2206206994597C13D831ec7"],
    "usdt_bep20": ["0xdAC17F958D2ee523a2206206994597C13D831ec7"],
    "usdc_erc20": ["0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"],
    "sol": ["So11111111111111111111111111111111111111112"],
    "usdt_sol": ["So11111111111111111111111111111111111111112"],
    "xrp": ["rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"],
    "xmr": ["888tNkZrPN6JsEgekjMnABU4TBzc2Dt29EPAvkRxbANsAnjyPbb3iQ1YBRk1UXcdRsiKc9dhwMVgN5S9cQUiyoogDavup3H"],
    # Generated from the encoder, because these networks have no famous example address.
    "ltc": [b58check_encode(bytes([0x30]) + b"\x02" * 20), b58check_encode(bytes([0x32]) + b"\x03" * 20)],
    "doge": [b58check_encode(bytes([0x1E]) + b"\x01" * 20)],
}

BTC = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
ETH = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
TRON = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"


class CatalogTests(VeloraTestCase):
    def test_every_catalog_asset_has_a_valid_example_address(self):
        self.assertEqual(sorted(VALID), sorted(ASSETS), "every asset needs a known-good address")
        for key, addresses in VALID.items():
            for address in addresses:
                with self.subTest(asset=key, address=address):
                    self.assertEqual(validate_address(key, address)["address"].lower(), address.lower()
                                     if key not in ("bch",) else address.lower())

    def test_catalog_covers_the_major_coins_and_tether_on_every_network(self):
        keys = set(ASSETS)
        self.assertTrue({"btc", "eth", "ltc", "sol", "xrp", "xmr", "doge", "bch", "bnb", "trx"} <= keys)
        tether = [a for a in CATALOG if a.symbol == "USDT"]
        self.assertEqual(sorted(a.network for a in tether),
                         ["BNB Smart Chain (BEP-20)", "Ethereum (ERC-20)", "Solana (SPL)", "Tron (TRC-20)"])
        self.assertTrue(all(a.stablecoin and a.warning for a in tether))

    def test_typos_are_caught_by_checksums(self):
        bad = [
            ("btc", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdr"),     # bech32 checksum
            ("btc", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN3"),            # base58check
            ("eth", "0xdAC17F958D2ee523a2206206994597C13D831ec8"),     # EIP-55 mixed-case
            ("trx", "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6u"),
            ("xrp", "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTi"),
            ("bch", "bitcoincash:qpm2qsznhks23z7629mms6s4cwef74vcwvy22gdx6b"),
        ]
        for key, address in bad:
            with self.subTest(asset=key), self.assertRaises(ValidationError):
                validate_address(key, address)

    def test_an_address_from_the_wrong_network_is_refused(self):
        wrong = [("btc", ETH), ("eth", BTC), ("eth", TRON), ("trx", ETH), ("usdt_trc20", ETH),
                 ("usdt_erc20", TRON), ("ltc", BTC), ("btc", VALID["ltc"][0]), ("sol", ETH),
                 ("xmr", BTC), ("doge", BTC)]
        for key, address in wrong:
            with self.subTest(asset=key, address=address[:12]), self.assertRaises(ValidationError):
                validate_address(key, address)

    def test_recovery_phrases_and_private_keys_are_refused(self):
        phrase = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
        for key in ASSETS:
            for candidate in (phrase, "5Kb8kLf9zgWQnogidDA76MzPL6TsZZY36hWXMssSzNydYXYB9KF",
                              "0x" + "ab" * 32, "", "   ", "not-an-address-at-all"):
                with self.subTest(asset=key, candidate=candidate[:10]), self.assertRaises(ValidationError):
                    validate_address(key, candidate)
        with self.assertRaises(ValidationError) as caught:
            validate_address("btc", phrase)
        self.assertIn("recovery phrase", caught.exception.message)

    def test_non_string_and_unknown_assets_are_refused(self):
        for value in (None, 12, [], {}, True):
            with self.assertRaises(ValidationError):
                validate_address("btc", value)
        with self.assertRaises(ValidationError) as caught:
            validate_address("dogecoin2", BTC)
        self.assertEqual(caught.exception.field, "asset")

    def test_evm_addresses_are_normalised_to_eip55(self):
        self.assertEqual(validate_address("eth", ETH.lower())["address"], ETH)
        self.assertEqual(validate_address("usdt_erc20", ETH.upper().replace("0X", "0x"))["address"], ETH)
        with self.assertRaises(ValidationError):
            validate_address("eth", "0x" + "0" * 40)

    def test_keccak_and_checksum_primitives(self):
        self.assertEqual(keccak256(b"").hex(),
                         "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")
        self.assertEqual(eip55_checksum("5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"),
                         "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")

    def test_amount_helpers_are_exact_for_18_decimals(self):
        one_and_a_half = 1_500_000_000_000_000_000
        self.assertEqual(to_atomic("1.5", 18), one_and_a_half)
        self.assertEqual(format_atomic(one_and_a_half, 18), "1.5")
        self.assertEqual(format_atomic(1, 8), "0.00000001")
        self.assertEqual(format_atomic(10 ** 20, 18), "100")
        self.assertIsNone(to_atomic("-1", 8))
        self.assertIsNone(to_atomic("NaN", 8))
        self.assertIsNone(to_atomic("abc", 8))


class ConfigTests(VeloraTestCase):
    def test_default_offers_the_whole_catalog(self):
        self.assertEqual(self.config.offered_assets, tuple(ASSETS))

    def test_operator_can_narrow_the_offer(self):
        config = build_config({**self.environ, "VELORA_PAYMENT_METHODS": "btc, usdt_trc20 ,btc"})
        self.assertEqual(config.offered_assets, ("btc", "usdt_trc20"))
        self.assertEqual(config.public_features()["payment_methods"], ["btc", "usdt_trc20"])

    def test_unknown_assets_in_configuration_are_rejected_at_startup(self):
        with self.assertRaises(RuntimeError):
            parse_asset_list("btc,notacoin")
        with self.assertRaises(RuntimeError):
            parse_method_overrides("notacoin=FOO-CHAIN")
        with self.assertRaises(RuntimeError):
            parse_method_overrides("btc=has spaces!")

    def test_method_id_overrides(self):
        config = build_config({**self.environ, "VELORA_PAYMENT_METHOD_IDS": "usdt_trc20=USDT-TRON"})
        self.assertEqual(config.method_id("usdt_trc20"), "USDT-TRON")
        self.assertEqual(config.method_id("btc"), "BTC-CHAIN")


class WalletApiTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, email="wallet-owner@velora.test", handle="wallet-owner",
                                      page_name="Wallet Owner")

    def test_creator_can_record_a_wallet_for_each_coin(self):
        for key, address in (("eth", ETH), ("usdt_trc20", TRON), ("usdt_erc20", ETH)):
            response = self.creator.put(f"/api/studio/wallets/{key}", json_body={"address": address})
            self.assertEqual(response.status, 200, response.text)
        payout = self.creator.get("/api/studio/wallets").json["payout"]
        self.assertEqual([w["asset"]["key"] for w in payout["wallets"]], ["btc", "eth", "usdt_trc20", "usdt_erc20"])
        self.assertEqual(payout["btc_address"], BTC)            # original response shape still present
        self.assertEqual(len(payout["supported_assets"]), len(CATALOG))
        tether = next(w for w in payout["wallets"] if w["asset"]["key"] == "usdt_trc20")
        self.assertEqual(tether["asset"]["symbol"], "USDT")
        self.assertEqual(tether["asset"]["network"], "Tron (TRC-20)")

    def test_replacing_and_removing_a_wallet(self):
        self.creator.put("/api/studio/wallets/eth", json_body={"address": ETH})
        other = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
        replaced = self.creator.put("/api/studio/wallets/eth", json_body={"address": other})
        eth = [w for w in replaced.json["payout"]["wallets"] if w["asset"]["key"] == "eth"]
        self.assertEqual([w["address"] for w in eth], [other])
        removed = self.creator.delete("/api/studio/wallets/eth")
        self.assertEqual(removed.status, 200, removed.text)
        self.assertNotIn("eth", [w["asset"]["key"] for w in removed.json["payout"]["wallets"]])
        self.assertEqual(self.creator.delete("/api/studio/wallets/eth").status, 404)
        self.assertEqual(self.creator.delete("/api/studio/wallets/notacoin").status, 404)

    def test_invalid_wallets_are_rejected_with_the_field_named(self):
        cases = [("btc", ETH), ("eth", BTC), ("usdt_trc20", ETH), ("eth", ""), ("eth", None),
                 ("eth", "abandon " * 12)]
        for key, address in cases:
            response = self.creator.put(f"/api/studio/wallets/{key}", json_body={"address": address})
            self.assertEqual(response.status, 400, f"{key} {address!r}: {response.text}")
            self.assertEqual(response.json["error"]["field"], "address")
        unknown = self.creator.put("/api/studio/wallets/notacoin", json_body={"address": ETH})
        self.assertEqual(unknown.status, 400)
        self.assertEqual(unknown.json["error"]["field"], "asset")

    def test_original_payout_endpoint_still_records_the_bitcoin_wallet(self):
        response = self.creator.put("/api/studio/payout",
                                    json_body={"btc_address": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"})
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(response.json["payout"]["btc_address"], "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
        self.assertEqual(response.json["payout"]["address_kind"], "p2pkh")
        bad = self.creator.put("/api/studio/payout", json_body={"btc_address": ETH})
        self.assertEqual(bad.status, 400)
        self.assertEqual(bad.json["error"]["field"], "btc_address")

    def test_wallets_are_private_to_the_owner_and_admins(self):
        self.creator.put("/api/studio/wallets/eth", json_body={"address": ETH})
        member = self.register("wallet-member@velora.test", display_name="Member")
        self.assertEqual(member.get("/api/studio/wallets").status, 403)
        self.assertEqual(member.put("/api/studio/wallets/eth", json_body={"address": ETH}).status, 403)
        self.assertEqual(member.delete("/api/studio/wallets/eth").status, 403)
        anonymous = Client(self.app)
        self.assertEqual(anonymous.get("/api/studio/wallets").status, 401)
        other = Client(self.app)
        self.seed_creator(other, email="other-owner@velora.test", handle="other-owner", page_name="Other")
        self.assertEqual(other.get(f"/api/admin/creators/{self.seed['page_id']}/payout").status, 403)

        admin = self.register("wallet-admin@velora.test", display_name="Ops")
        self.make_admin(admin, "wallet-admin@velora.test")
        view = admin.get(f"/api/admin/creators/{self.seed['page_id']}/payout")
        self.assertEqual(view.status, 200)
        self.assertIn(ETH, [w["address"] for w in view.json["payout"]["wallets"]])

        # No public surface exposes any wallet address.
        for path in (f"/api/creators/{self.seed['handle']}", "/api/creators", "/api/bootstrap"):
            text = Client(self.app).get(path).text
            for address in (BTC, ETH):
                self.assertNotIn(address, text, path)
        self.assertNotIn(ETH, member.get("/api/payments/status").text)

    def test_unverified_creators_cannot_record_wallets(self):
        client = Client(self.app)
        self.register("unverified-wallet@velora.test", display_name="Unverified", client=client, verify=False)
        self.assertEqual(client.put("/api/studio/wallets/eth", json_body={"address": ETH}).status, 403)

    def test_accounts_without_a_creator_page_cannot_record_wallets(self):
        member = self.register("nopage@velora.test", display_name="No Page")
        response = member.put("/api/studio/wallets/eth", json_body={"address": ETH})
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json["error"]["code"], "creator_page_required")

    def test_wallet_changes_are_audited_without_the_address(self):
        self.creator.put("/api/studio/wallets/eth", json_body={"address": ETH})
        self.creator.delete("/api/studio/wallets/eth")
        with self.db.connection() as conn:
            rows = conn.execute("SELECT action, meta FROM audit_log WHERE action LIKE 'creator.wallet_%' "
                                "ORDER BY id").fetchall()
        self.assertEqual([r["action"] for r in rows], ["creator.wallet_saved", "creator.wallet_removed"])
        self.assertTrue(all(ETH not in (r["meta"] or "") for r in rows))

    def test_studio_overview_lists_wallets(self):
        self.creator.put("/api/studio/wallets/usdt_trc20", json_body={"address": TRON})
        overview = self.creator.get("/api/studio/overview").json
        keys = [w["asset"]["key"] for w in overview["payout"]["wallets"]]
        self.assertEqual(keys, ["btc", "usdt_trc20"])


class ApplicationWalletTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.admin = self.register("app-admin@velora.test", display_name="Ops")
        self.make_admin(self.admin, "app-admin@velora.test")
        self.applicant = self.register("applicant@velora.test", display_name="Applicant")
        self.pitch = "I make weekly illustrated essays about maps and memory, with sketchbook process posts."

    def apply(self, wallets):
        return self.applicant.post("/api/creators/apply", json_body={
            "category": "art", "pitch": self.pitch, "wallets": wallets})

    def test_wallets_entered_while_applying_become_the_creators_wallets_on_approval(self):
        response = self.apply({"btc": BTC, "usdt_trc20": TRON, "eth": ETH.lower(), "sol": ""})
        self.assertEqual(response.status, 201, response.text)
        self.assertEqual(response.json["wallet_assets"], ["btc", "eth", "usdt_trc20"])
        self.assertNotIn(TRON, response.text)

        listing = self.admin.get("/api/admin/applications").json["items"]
        self.assertEqual(listing[0]["wallet_assets"], ["btc", "eth", "usdt_trc20"])
        self.assertNotIn("wallets_json", listing[0])

        approved = self.admin.post(f"/api/admin/applications/{response.json['id']}/review",
                                   json_body={"decision": "approve"})
        self.assertEqual(approved.status, 200, approved.text)
        payout = self.applicant.get("/api/studio/wallets").json["payout"]
        self.assertEqual({w["asset"]["key"]: w["address"] for w in payout["wallets"]},
                         {"btc": BTC, "eth": ETH, "usdt_trc20": TRON})

    def test_rejected_application_creates_no_wallets(self):
        response = self.apply({"btc": BTC})
        self.admin.post(f"/api/admin/applications/{response.json['id']}/review",
                        json_body={"decision": "reject"})
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM creator_wallets").fetchone()["c"], 0)

    def test_wallets_are_optional_when_applying(self):
        self.assertEqual(self.apply({}).status, 201)

    def test_bad_wallets_block_the_application(self):
        for wallets, field in (({"btc": ETH}, "wallets.btc"), ({"notacoin": ETH}, "wallets"),
                               ({"eth": "abandon " * 12}, "wallets.eth"), ([ETH], "wallets"),
                               ("0xabc", "wallets")):
            response = self.apply(wallets)
            self.assertEqual(response.status, 400, f"{wallets!r}: {response.text}")
            self.assertEqual(response.json["error"]["field"], field)
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM creator_applications").fetchone()["c"], 0)


class UpgradeFromSingleAddressTests(VeloraTestCase):
    def test_existing_btc_payout_addresses_carry_over(self):
        """A database created before multi-asset support keeps its creators' addresses."""
        tmp = Path(tempfile.mkdtemp(prefix="velora-upgrade-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        old_dir = tmp / "old"
        old_dir.mkdir()
        for path in sorted(migrations_module.MIGRATIONS_DIR.glob("*.sql")):
            if not path.name.startswith("0012"):
                shutil.copy(path, old_dir / path.name)
        db = Database(str(tmp / "upgrade.db"))
        migrations_module.apply_all(db, old_dir, log=lambda m: None)
        with db.transaction() as conn:
            conn.execute("INSERT INTO users (email, display_name, password_hash, role, status, "
                         "email_verified, adult_attested_at, created_at, updated_at) VALUES "
                         "('old@x.test','Old','x','creator','active',1,'2025-01-01T00:00:00Z',"
                         "'2025-01-01T00:00:00Z','2025-01-01T00:00:00Z')")
            conn.execute("INSERT INTO creator_pages (user_id, handle, page_name, category, status, created_at, "
                         "updated_at) VALUES (1,'old-page','Old Page','art','active','2025-01-01T00:00:00Z',"
                         "'2025-01-01T00:00:00Z')")
            conn.execute("INSERT INTO creator_payouts (creator_id, btc_address, address_kind, saved_at) "
                         f"VALUES (1, '{BTC}', 'bech32', '2025-01-01T00:00:00Z')")
        applied = migrations_module.apply_all(db, log=lambda m: None)
        self.assertEqual(applied, ["0012"])
        with db.connection() as conn:
            wallets = [dict(r) for r in conn.execute("SELECT * FROM creator_wallets").fetchall()]
            tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertEqual([(w["creator_id"], w["asset"], w["address"], w["address_kind"]) for w in wallets],
                         [(1, "btc", BTC, "bech32")])
        self.assertNotIn("creator_payouts", tables)
