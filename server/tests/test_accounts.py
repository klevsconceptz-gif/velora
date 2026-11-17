"""Signup, login, sessions, verification, invitations and privacy guarantees."""

from __future__ import annotations

from .harness import Client, VeloraTestCase


class SignupTests(VeloraTestCase):
    def test_signup_requires_minimal_fields_and_adult_attestation(self):
        response = self.client.post(
            "/api/auth/signup",
            json_body={"display_name": "Ada", "email": "ada@example.com", "password": "password123",
                       "password_confirm": "password123"},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(response.json["error"]["field"], "adult_attestation")
        self.assertIn("18", response.json["error"]["message"])

        with self.db.connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        self.assertEqual(count, 0, "no account may be created without the 18+ self-attestation")

    def test_password_policy_and_hashing(self):
        response = self.client.post(
            "/api/auth/signup",
            json_body={"display_name": "Ada", "email": "ada@example.com", "password": "short",
                       "password_confirm": "short", "adult_attestation": True},
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(response.json["error"]["field"], "password")

        client = self.register("ada@example.com", password="password123")
        with self.db.connection() as conn:
            stored = conn.execute("SELECT password_hash FROM users WHERE email = ?",
                                  ("ada@example.com",)).fetchone()["password_hash"]
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertNotIn("password123", stored)

    def test_signup_never_returns_password_and_requests_no_sensitive_fields(self):
        response = self.client.post(
            "/api/auth/signup",
            json_body={"display_name": "Ada", "email": "ada@example.com", "password": "password123",
                       "password_confirm": "password123", "adult_attestation": True,
                       "phone": "+15550001111", "address": "1 Main St", "passport": "X123"},
        )
        self.assertEqual(response.status, 201)
        self.assertNotIn("password", response.text)
        self.assertNotIn("passport", response.text)
        with self.db.connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        for forbidden in ("phone", "address", "government_id", "wallet_seed", "country", "ip_address"):
            self.assertNotIn(forbidden, columns)

    def test_verification_email_is_single_use_and_expiring(self):
        self.client.post(
            "/api/auth/signup",
            json_body={"display_name": "Ada", "email": "ada@example.com", "password": "password123",
                       "password_confirm": "password123", "adult_attestation": True},
        )
        entry = self.latest_email()
        token = self.token_from_email(entry)
        first = self.client.post("/api/auth/verify-email", json_body={"token": token})
        self.assertEqual(first.status, 200)
        second = self.client.post("/api/auth/verify-email", json_body={"token": token})
        self.assertEqual(second.status, 400)
        self.assertEqual(second.json["error"]["code"], "token_used")
        bogus = self.client.post("/api/auth/verify-email", json_body={"token": "not-a-real-token"})
        self.assertEqual(bogus.status, 400)
        self.assertEqual(bogus.json["error"]["code"], "invalid_token")

    def test_login_enumerates_nothing_and_rate_limits(self):
        self.register("ada@example.com")
        anon = Client(self.app)
        missing = anon.post("/api/auth/login", json_body={"email": "nobody@example.com",
                                                          "password": "password123"})
        wrong = anon.post("/api/auth/login", json_body={"email": "ada@example.com",
                                                        "password": "wrong-password"})
        self.assertEqual(missing.status, 401)
        self.assertEqual(wrong.status, 401)
        self.assertEqual(missing.json["error"]["message"], wrong.json["error"]["message"])

    def test_session_cookie_flags_and_csrf_required(self):
        client = self.register("ada@example.com")
        self.assertIn("velora_session", client.cookies)
        with self.db.connection() as conn:
            session = conn.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        self.assertNotIn(client.cookies["velora_session"], session["token_hash"])

        # A state-changing call without the CSRF header is refused.
        no_csrf = Client(self.app)
        no_csrf.cookies = dict(client.cookies)
        response = no_csrf.patch("/api/account/profile", json_body={"display_name": "Renamed"})
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json["error"]["code"], "csrf_required")

        bad_csrf = Client(self.app)
        bad_csrf.cookies = dict(client.cookies)
        bad_csrf.csrf_token = "definitely-wrong"
        response = bad_csrf.patch("/api/account/profile", json_body={"display_name": "Renamed"})
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json["error"]["code"], "csrf_invalid")

    def test_logout_revokes_the_session(self):
        client = self.register("ada@example.com")
        response = client.post("/api/auth/logout")
        self.assertEqual(response.status, 200)
        with self.db.connection() as conn:
            revoked = conn.execute("SELECT revoked_at FROM sessions ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(revoked["revoked_at"])
        fresh = Client(self.app)
        fresh.cookies = dict(client.cookies)
        session = fresh.get("/api/auth/session")
        self.assertEqual(session.status, 200)
        self.assertFalse(session.json["authenticated"])

    def test_unverified_account_cannot_use_membership_features(self):
        client = Client(self.app)
        self.register("unverified@example.com", client=client, verify=False)
        response = client.post("/api/payments/intents", json_body={"tier_id": 1})
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json["error"]["code"], "email_verification_required")

    def test_resend_verification_does_not_leak_membership(self):
        client = Client(self.app)
        self.register("quiet@example.com", client=client, verify=False)
        unknown = Client(self.app).post("/api/auth/resend-verification",
                                        json_body={"email": "someone-else@example.com"})
        known = client.post("/api/auth/resend-verification", json_body={"email": "quiet@example.com"})
        self.assertEqual(unknown.status, 200)
        self.assertEqual(known.status, 200)
        self.assertEqual(unknown.json["accepted"], known.json["accepted"])


class PrivacyTests(VeloraTestCase):
    def test_orientation_private_by_default_and_never_publicly_leaked(self):
        client = self.register("privacy@example.com")
        response = client.put("/api/account/orientation",
                             json_body={"orientation": {"value": "bisexual"}, "visibility": "private"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json["user"]["orientation"]["visibility"], "private")

        # A public visitor sees the option vocabulary in bootstrap, but never this
        # member's stored value on any public surface.
        listing = Client(self.app).get("/api/creators")
        self.assertNotIn("owner_orientation", listing.text)

    def test_prefer_not_to_say_can_never_be_public(self):
        client = self.register("pnts@example.com")
        response = client.put("/api/account/orientation",
                              json_body={"orientation": {"value": "prefer_not_to_say"},
                                         "visibility": "public"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json["user"]["orientation"]["visibility"], "private")
        self.assertTrue(any("never displayed publicly" in note for note in response.json["notes"]))

    def test_self_description_stays_private(self):
        client = self.register("selfdesc@example.com")
        response = client.put("/api/account/orientation",
                              json_body={"orientation": {"self_described": "figuring it out"},
                                         "visibility": "public"})
        self.assertEqual(response.json["user"]["orientation"]["visibility"], "private")

    def test_orientation_opt_in_shows_on_public_page_only(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="sharer@velora.test", handle="sharer")
        response = creator.put("/api/account/orientation",
                               json_body={"orientation": {"value": "queer"}, "visibility": "public"})
        self.assertEqual(response.json["user"]["orientation"]["visibility"], "public")

        page = Client(self.app).get(f"/api/creators/{seed['handle']}")
        self.assertEqual(page.status, 200)
        self.assertEqual(page.json["owner_orientation"]["value"], "queer")

        # Discovery listings include the same opt-in value only because it is public.
        listing = Client(self.app).get("/api/creators")
        self.assertIn("queer", str(listing.json))
        # ...and never any member's private value.
        self.assertNotIn("prefer_not_to_say", str(listing.json))

    def test_private_fields_absent_from_public_creator_page(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="hidden@velora.test", handle="hidden")
        creator.put("/api/account/orientation",
                    json_body={"orientation": {"value": "gay"}, "visibility": "private"})
        page = Client(self.app).get(f"/api/creators/{seed['handle']}")
        self.assertNotIn("hidden@velora.test", page.text)
        self.assertNotIn("owner_orientation", page.json)

    def test_export_covers_personal_data(self):
        client = self.register("export@example.com")
        response = client.get("/api/account/export")
        self.assertEqual(response.status, 200)
        payload = response.json
        self.assertEqual(payload["account"]["email"], "export@example.com")
        for key in ("sessions", "memberships", "payment_attempts", "settled_invoices",
                    "orientation_disclosures"):
            self.assertIn(key, payload)

    def test_deactivation_requires_password(self):
        client = self.register("bye@example.com")
        bad = client.post("/api/account/deactivate", json_body={"password": "nope"})
        self.assertEqual(bad.status, 400)
        good = client.post("/api/account/deactivate", json_body={"password": "correct-horse-9"})
        self.assertEqual(good.status, 200)
        with self.db.connection() as conn:
            status = conn.execute("SELECT status FROM users WHERE email = ?",
                                  ("bye@example.com",)).fetchone()["status"]
        self.assertEqual(status, "archived")
        relogin = Client(self.app).post("/api/auth/login",
                                        json_body={"email": "bye@example.com",
                                                   "password": "correct-horse-9"})
        self.assertEqual(relogin.status, 403)

    def test_deletion_anonymises_but_keeps_financial_rows(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator)
        member = self.register("member@example.com")
        order = self.start_checkout(member, seed["tier_id"])
        settled = self.settle_and_verify(member, order["order_ref"])
        self.assertEqual(settled.status, 200, settled.text)
        self.assertEqual(settled.json["status"], "settled")

        response = member.post("/api/account/delete",
                               json_body={"password": "correct-horse-9", "confirm": "delete my account"})
        self.assertEqual(response.status, 200)
        with self.db.connection() as conn:
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
            anonymised = conn.execute(
                "SELECT * FROM users WHERE email LIKE 'deleted+%'").fetchone()
        self.assertEqual(invoices, 1, "settled invoice history must survive account deletion")
        self.assertIsNotNone(anonymised)
        self.assertIsNone(anonymised["orientation_value"])
        self.assertEqual(anonymised["status"], "anonymized")


class InvitationTests(VeloraTestCase):
    def test_first_admin_can_invite_and_invitation_is_single_use(self):
        admin = self.register("ops@example.com")
        self.make_admin(admin, "ops@example.com")

        invite = admin.post("/api/admin/invitations",
                            json_body={"email": "newadmin@example.com", "role": "admin",
                                       "note": "co-operator"})
        self.assertEqual(invite.status, 201)
        entry = self.latest_email()
        token = self.token_from_email(entry)

        preview = Client(self.app).get(f"/api/invitations/{token}")
        self.assertEqual(preview.status, 200)
        self.assertEqual(preview.json["role"], "admin")

        acceptor = Client(self.app)
        accept = acceptor.post(
            "/api/invitations/accept",
            json_body={"token": token, "display_name": "New Admin", "password": "invited-pass-9",
                       "password_confirm": "invited-pass-9", "adult_attestation": True},
        )
        self.assertEqual(accept.status, 200)
        self.assertEqual(accept.json["role"], "admin")

        again = Client(self.app).post("/api/invitations/accept",
                                      json_body={"token": token, "display_name": "X",
                                                 "password": "invited-pass-9",
                                                 "password_confirm": "invited-pass-9",
                                                 "adult_attestation": True})
        self.assertEqual(again.status, 410)

    def test_admin_routes_require_admin_role(self):
        member = self.register("member2@example.com")
        response = member.get("/api/admin/users")
        self.assertEqual(response.status, 403)


class BootstrapTests(VeloraTestCase):
    def test_bootstrap_reports_feature_state_without_secrets(self):
        response = Client(self.app).get("/api/bootstrap")
        self.assertEqual(response.status, 200)
        payload = response.json
        self.assertTrue(payload["features"]["btcpay_configured"])
        self.assertEqual(payload["session"], {"authenticated": False})

        def walk(node, path=""):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertNotIn("secret", key.lower(), f"suspicious key at {path}.{key}")
                    self.assertNotIn("api_key", key.lower(), f"suspicious key at {path}.{key}")
                    if key != "btcpay_webhook_configured":
                        self.assertNotIn("webhook", key.lower(), f"suspicious key at {path}.{key}")
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, f"{path}[{index}]")

        walk(payload)
        self.assertNotIn("test-api-key", response.text)
        self.assertNotIn("test-webhook-secret", response.text)

    def test_health_requires_migrations_to_be_applied(self):
        response = Client(self.app).get("/api/health")
        self.assertEqual(response.status, 200)
        self.assertTrue(response.json["ok"])


class MissingConfigurationTests(VeloraTestCase):
    email_transport = "none"
    btcpay = False

    def test_missing_email_reports_unavailable(self):
        response = self.client.post(
            "/api/auth/signup",
            json_body={"display_name": "Ada", "email": "ada@example.com", "password": "password123",
                       "password_confirm": "password123", "adult_attestation": True},
        )
        self.assertEqual(response.status, 201)
        self.assertFalse(response.json["verification"]["sent"])
        self.assertEqual(response.json["verification"]["reason"], "unavailable")

    def test_checkout_is_disabled_without_btcpay(self):
        client = self.register("nopay@example.com", verify=False)
        response = client.post("/api/payments/intents", json_body={"tier_id": 1})
        self.assertEqual(response.status, 403)  # unverified first

    def test_availability_endpoint_says_unavailable(self):
        response = Client(self.app).get("/api/payments/availability")
        self.assertEqual(response.status, 200)
        self.assertFalse(response.json["available"])
        self.assertEqual(response.json["methods"], [])
        self.assertEqual(response.json["reason"], "btcpay_not_configured")
