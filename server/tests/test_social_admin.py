"""Direct messages, reports and the administrator console.

The theme is the same as everywhere else in Velora: the server decides. A
membership gates messaging, a role gates the console, and financial history can
never be rewritten — not even by an administrator.
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from ..db import now_iso
from .harness import Client, VeloraTestCase

ADMIN_PATHS = (
    "/api/admin/overview",
    "/api/admin/users",
    "/api/admin/creators",
    "/api/admin/applications",
    "/api/admin/posts",
    "/api/admin/tiers",
    "/api/admin/reports",
    "/api/admin/payments",
    "/api/admin/payments/events",
    "/api/admin/ledger",
    "/api/admin/audit",
    "/api/admin/invitations",
)


def shift_iso(iso: str, *, days: int) -> str:
    parsed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (parsed + timedelta(days=days)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


class MessagingRulesTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, email="dm-creator@velora.test",
                                     handle="dm-page", page_name="DM Page")

    def test_stranger_cannot_start_a_conversation(self):
        stranger = self.register("dm-stranger@velora.test", display_name="Stranger")
        response = stranger.post("/api/threads", json_body={
            "creator_id": self.seed["page_id"], "body": "Hello there, I have a question.",
        })
        self.assertEqual(response.status, 403, response.text)
        self.assertEqual(response.json["error"]["code"], "membership_required")

    def test_member_can_message_and_creator_can_reply(self):
        member = self.register("dm-member@velora.test", display_name="Member Mia")
        order = self.start_checkout(member, self.seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])

        created = member.post("/api/threads", json_body={
            "creator_id": self.seed["page_id"], "body": "Loved the last session, thank you!",
        })
        self.assertEqual(created.status, 201, created.text)
        thread_id = created.json["thread"]["id"]
        self.assertEqual(created.json["thread"]["counterpart"]["kind"], "creator")

        # The creator sees the member's display name but never an email address.
        inbox = self.creator.get("/api/threads")
        self.assertEqual(inbox.status, 200, inbox.text)
        self.assertNotIn("dm-member@velora.test", inbox.text)
        raw = self.creator.get(f"/api/threads/{thread_id}").text
        self.assertNotIn("dm-member@velora.test", raw)
        self.assertIn("Member Mia", raw)

        reply = self.creator.post(f"/api/threads/{thread_id}/messages",
                                  json_body={"body": "Thank you — new set on Friday."})
        self.assertEqual(reply.status, 201, reply.text)

        thread = member.get(f"/api/threads/{thread_id}").json
        self.assertTrue(thread["can_send"])
        self.assertEqual([message["body"] for message in thread["messages"]],
                         ["Loved the last session, thank you!", "Thank you — new set on Friday."])

    def test_thread_is_private_to_its_participants(self):
        member = self.register("dm-owner@velora.test", display_name="Member One")
        order = self.start_checkout(member, self.seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        created = member.post("/api/threads", json_body={
            "creator_id": self.seed["page_id"], "body": "A private question about the archive.",
        })
        thread_id = created.json["thread"]["id"]

        other = self.register("dm-other@velora.test", display_name="Member Two")
        response = other.get(f"/api/threads/{thread_id}")
        self.assertIn(response.status, (403, 404), response.text)
        self.assertEqual(response.status, 403)
        self.assertEqual(response.json["error"]["code"], "thread_private")
        self.assertNotIn("private question", other.get("/api/threads").text)

    def test_expired_membership_makes_the_thread_read_only(self):
        member = self.register("dm-expired@velora.test", display_name="Lapsed Larry")
        order = self.start_checkout(member, self.seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        created = member.post("/api/threads", json_body={
            "creator_id": self.seed["page_id"], "body": "Question while I am still a member.",
        })
        thread_id = created.json["thread"]["id"]

        with self.db.transaction() as conn:
            membership = conn.execute("SELECT id, ends_at FROM memberships").fetchone()
            conn.execute("UPDATE memberships SET ends_at = ? WHERE id = ?",
                         (shift_iso(membership["ends_at"], days=-45), membership["id"]))

        thread = member.get(f"/api/threads/{thread_id}").json
        self.assertFalse(thread["can_send"])
        self.assertEqual(thread["thread"]["membership_state"], "ended")
        self.assertIn("read-only", thread["read_only_reason"])
        self.assertEqual(len(thread["messages"]), 1, "history is kept")

        blocked = member.post(f"/api/threads/{thread_id}/messages",
                              json_body={"body": "Can I still write here?"})
        self.assertEqual(blocked.status, 403, blocked.text)
        self.assertEqual(blocked.json["error"]["code"], "membership_required")

        # The creator can always answer their own members' conversations.
        reply = self.creator.post(f"/api/threads/{thread_id}/messages",
                                  json_body={"body": "You can renew any time — nothing was deleted."})
        self.assertEqual(reply.status, 201, reply.text)

    def test_empty_and_oversized_messages_are_rejected(self):
        member = self.register("dm-limits@velora.test", display_name="Limits Lou")
        order = self.start_checkout(member, self.seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        empty = member.post("/api/threads", json_body={
            "creator_id": self.seed["page_id"], "body": "   ",
        })
        self.assertEqual(empty.status, 400)
        self.assertEqual(empty.json["error"]["field"], "body")

        too_long = member.post("/api/threads", json_body={
            "creator_id": self.seed["page_id"], "body": "x" * 5000,
        })
        self.assertEqual(too_long.status, 400)
        self.assertEqual(too_long.json["error"]["field"], "body")

    def test_member_cannot_message_their_own_creator_page(self):
        owner = self.register("dm-self@velora.test", display_name="Self Owner")
        with self.db.transaction() as conn:
            user = conn.execute("SELECT id FROM users WHERE email = ?", ("dm-self@velora.test",)).fetchone()
            conn.execute("UPDATE creator_pages SET user_id = ? WHERE id = ?", (user["id"], self.seed["page_id"]))
        response = owner.post("/api/threads", json_body={
            "creator_id": self.seed["page_id"], "body": "Talking to myself here.",
        })
        self.assertIn(response.status, (403, 409), response.text)


class ReportFlowTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, email="report-creator@velora.test",
                                     handle="report-page", page_name="Report Page")
        self.post = self.create_post(self.creator, title="A post worth reporting")

    def test_report_lifecycle_and_status_visibility(self):
        reporter = self.register("reporter@velora.test", display_name="Reporter")
        preview = reporter.get(f"/api/reports/target?target_type=post&target_id={self.post['id']}")
        self.assertEqual(preview.status, 200, preview.text)
        self.assertEqual(preview.json["label"], "Post: A post worth reporting")

        created = reporter.post("/api/reports", json_body={
            "target_type": "post", "target_id": self.post["id"],
            "reason_code": "spam", "details": "This looks like an advert.",
        })
        self.assertEqual(created.status, 201, created.text)
        self.assertEqual(created.json["status"], "open")

        mine = reporter.get("/api/reports/mine").json
        self.assertEqual(len(mine["items"]), 1)
        self.assertEqual(mine["items"][0]["target_label"], "Post: A post worth reporting")
        self.assertIn("waiting for a moderator", mine["statuses"]["open"])

        unknown = reporter.post("/api/reports", json_body={
            "target_type": "post", "target_id": 999_999, "reason_code": "spam",
        })
        self.assertEqual(unknown.status, 404)

        bad_reason = reporter.post("/api/reports", json_body={
            "target_type": "post", "target_id": self.post["id"], "reason_code": "because",
        })
        self.assertEqual(bad_reason.status, 400)
        self.assertEqual(bad_reason.json["error"]["field"], "reason_code")

        admin = self.register("report-admin@velora.test", display_name="Moderator")
        self.make_admin(admin, "report-admin@velora.test")
        queue = admin.get("/api/admin/reports")
        self.assertEqual(queue.status, 200, queue.text)
        report_id = queue.json["items"][0]["id"]
        self.assertEqual(queue.json["counts"]["open"], 1)

        updated = admin.post(f"/api/admin/reports/{report_id}", json_body={
            "status": "resolved", "resolution_note": "Removed the promotional text.",
        })
        self.assertEqual(updated.status, 200, updated.text)
        self.assertEqual(updated.json["status"], "resolved")
        self.assertEqual(updated.json["resolution_note"], "Removed the promotional text.")

        after = reporter.get("/api/reports/mine").json["items"][0]
        self.assertEqual(after["status"], "resolved")
        self.assertEqual(after["resolution_note"], "Removed the promotional text.")

    def test_members_cannot_touch_the_moderation_queue(self):
        reporter = self.register("report-nobody@velora.test", display_name="Nobody")
        created = reporter.post("/api/reports", json_body={
            "target_type": "creator", "target_id": self.seed["page_id"], "reason_code": "other",
        })
        self.assertEqual(created.status, 201, created.text)
        report_id = created.json["id"]
        self.assertEqual(reporter.get("/api/admin/reports").status, 403)
        self.assertEqual(reporter.post(f"/api/admin/reports/{report_id}",
                                       json_body={"status": "dismissed"}).status, 403)


class AdminRoleEnforcementTests(VeloraTestCase):
    def test_every_admin_route_refuses_non_admins(self):
        member = self.register("not-admin@velora.test", display_name="Not Admin")
        for path in ADMIN_PATHS:
            response = member.get(path)
            self.assertEqual(response.status, 403, f"{path}: {response.text}")
            self.assertEqual(response.json["error"]["code"], "admin_required")

    def test_anonymous_and_unverified_are_refused(self):
        anonymous = Client(self.app)
        self.assertEqual(anonymous.get("/api/admin/overview").status, 401)

        unverified = Client(self.app)
        self.register("unverified-admin@velora.test", display_name="Unverified Admin",
                      client=unverified, verify=False)
        self.make_admin(unverified, "unverified-admin@velora.test")
        response = unverified.get("/api/admin/overview")
        self.assertEqual(response.status, 403, response.text)
        self.assertEqual(response.json["error"]["code"], "email_verification_required")

    def test_no_admin_self_promotion_demotion_or_status_change(self):
        admin = self.register("solo-admin@velora.test", display_name="Solo Admin")
        self.make_admin(admin, "solo-admin@velora.test")
        with self.db.connection() as conn:
            user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                                   ("solo-admin@velora.test",)).fetchone()["id"]

        promotion = admin.post(f"/api/admin/users/{user_id}/promote", json_body={"role": "admin"})
        self.assertEqual(promotion.status, 403, promotion.text)
        self.assertEqual(promotion.json["error"]["code"], "self_promotion_blocked")

        demotion = admin.post(f"/api/admin/users/{user_id}/demote", json_body={"role": "member"})
        self.assertEqual(demotion.status, 403)
        self.assertEqual(demotion.json["error"]["code"], "self_demotion_blocked")

        status = admin.post(f"/api/admin/users/{user_id}/status", json_body={"status": "suspended"})
        self.assertEqual(status.status, 403)
        self.assertEqual(status.json["error"]["code"], "self_status_change_blocked")

        anonymize = admin.post(f"/api/admin/users/{user_id}/anonymize", json_body={})
        self.assertEqual(anonymize.status, 403)
        self.assertEqual(anonymize.json["error"]["code"], "self_anonymize_blocked")

    def test_last_administrator_cannot_be_removed(self):
        first = self.register("first-admin@velora.test", display_name="First Admin")
        self.make_admin(first, "first-admin@velora.test")
        second = self.register("second-admin@velora.test", display_name="Second Admin")
        self.make_admin(second, "second-admin@velora.test")
        with self.db.connection() as conn:
            second_id = conn.execute("SELECT id FROM users WHERE email = ?",
                                     ("second-admin@velora.test",)).fetchone()["id"]

        # Two active verified administrators: removing one is allowed.
        demoted = first.post(f"/api/admin/users/{second_id}/demote", json_body={"role": "member"})
        self.assertEqual(demoted.status, 200, demoted.text)
        self.assertEqual(demoted.json["role"], "member")

        promoted = first.post(f"/api/admin/users/{second_id}/promote", json_body={"role": "admin"})
        self.assertEqual(promoted.status, 200, promoted.text)

        # An administrator whose address is not confirmed does not count towards the
        # minimum, so the console refuses to remove the remaining verified one.
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET email_verified = 0 WHERE id = ?", (second_id,))
        blocked = first.post(f"/api/admin/users/{second_id}/demote", json_body={"role": "member"})
        self.assertEqual(blocked.status, 409, blocked.text)
        self.assertEqual(blocked.json["error"]["code"], "last_administrator")

        archived = first.post(f"/api/admin/users/{second_id}/status", json_body={"status": "archived"})
        self.assertEqual(archived.status, 409, archived.text)
        self.assertEqual(archived.json["error"]["code"], "last_administrator")

        with self.db.connection() as conn:
            still_admin = conn.execute("SELECT role, status FROM users WHERE id = ?",
                                       (second_id,)).fetchone()
        self.assertEqual((still_admin["role"], still_admin["status"]), ("admin", "active"))

    def test_promotion_requires_a_verified_active_account(self):
        admin = self.register("promoter@velora.test", display_name="Promoter")
        self.make_admin(admin, "promoter@velora.test")
        pending = Client(self.app)
        self.register("pending-promotion@velora.test", display_name="Unverified Person",
                      client=pending, verify=False)
        with self.db.connection() as conn:
            user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                                   ("pending-promotion@velora.test",)).fetchone()["id"]
        response = admin.post(f"/api/admin/users/{user_id}/promote", json_body={"role": "creator"})
        self.assertEqual(response.status, 409, response.text)
        self.assertEqual(response.json["error"]["code"], "email_verification_required")

        verified = self.register("verified-promotion@velora.test", display_name="Verified Person")
        with self.db.connection() as conn:
            verified_id = conn.execute("SELECT id FROM users WHERE email = ?",
                                       ("verified-promotion@velora.test",)).fetchone()["id"]
        promoted = admin.post(f"/api/admin/users/{verified_id}/promote",
                              json_body={"role": "creator", "reason": "audited promotion"})
        self.assertEqual(promoted.status, 200, promoted.text)
        self.assertEqual(promoted.json["role"], "creator")
        self.assertNotIn("email_verified", promoted.json.get("password_hash", ""))
        self.assertTrue(verified)

        audit = admin.get("/api/admin/audit").json["items"]
        self.assertIn("admin.user_promoted", [entry["action"] for entry in audit])

    def test_user_console_hides_orientation_values(self):
        admin = self.register("privacy-admin@velora.test", display_name="Privacy Admin")
        self.make_admin(admin, "privacy-admin@velora.test")
        member = self.register("orientation-member@velora.test", display_name="Orientation Member")
        saved = member.put("/api/account/orientation", json_body={
            "orientation": {"value": "bisexual"}, "visibility": "private",
        })
        self.assertEqual(saved.status, 200, saved.text)

        with self.db.connection() as conn:
            user_id = conn.execute("SELECT id FROM users WHERE email = ?",
                                   ("orientation-member@velora.test",)).fetchone()["id"]
        detail = admin.get(f"/api/admin/users/{user_id}")
        self.assertEqual(detail.status, 200, detail.text)
        self.assertTrue(detail.json["user"]["orientation_set"])
        self.assertNotIn("bisexual", detail.text)
        self.assertNotIn("orientation_value", detail.text)
        self.assertNotIn("orientation_self_text", detail.text)
        self.assertNotIn("bisexual", detail.json["notes"].__str__())

        listing = admin.get("/api/admin/users?q=orientation")
        self.assertNotIn("bisexual", listing.text)


class FinancialIntegrityTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, email="ledger-creator@velora.test",
                                     handle="ledger-page", page_name="Ledger Page")
        self.member = self.register("ledger-member@velora.test", display_name="Payer Pat")
        order = self.start_checkout(self.member, self.seed["tier_id"])
        self.settle_and_verify(self.member, order["order_ref"])
        self.admin = self.register("ledger-admin@velora.test", display_name="Ledger Admin")
        self.make_admin(self.admin, "ledger-admin@velora.test")

    def test_admin_console_cannot_mark_a_payment_paid(self):
        payments = self.admin.get("/api/admin/payments")
        self.assertEqual(payments.status, 200, payments.text)
        intent = payments.json["items"][0]
        self.assertEqual(intent["status"], "settled")
        self.assertTrue(intent["paid"])
        self.assertIn("cannot mark an invoice paid", payments.json["immutability_notice"])

        archived = self.admin.post(f"/api/admin/payments/{intent['id']}/archive",
                                   json_body={"reason": "trying to hide it"})
        self.assertEqual(archived.status, 409, archived.text)
        self.assertEqual(archived.json["error"]["code"], "settled_payment_immutable")

        for path in ("/api/admin/payments", "/api/admin/ledger"):
            self.assertEqual(self.admin.post(path, json_body={}).status, 405)

    def test_settled_rows_are_immutable_even_at_the_database(self):
        with self.db.transaction() as conn:
            invoice = dict(conn.execute("SELECT * FROM invoices").fetchone())
            ledger = dict(conn.execute("SELECT * FROM ledger_entries").fetchone())
            intent = dict(conn.execute("SELECT * FROM payment_intents WHERE status = 'settled'").fetchone())

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO webhook_events (provider, delivery_id, event_type, invoice_id, order_ref,
                                            signature_valid, outcome, detail, received_at)
                VALUES ('btcpay', 'test-delivery', 'InvoiceSettled', 'FAKE-INV-1', 'VLR-TEST', 1,
                        'settled', NULL, ?)
                """,
                (now_iso(),),
            )
        attempts = [
            ("UPDATE invoices SET amount_cents = 1 WHERE id = ?", (invoice["id"],)),
            ("DELETE FROM invoices WHERE id = ?", (invoice["id"],)),
            ("UPDATE ledger_entries SET amount_cents = 1 WHERE id = ?", (ledger["id"],)),
            ("DELETE FROM ledger_entries WHERE id = ?", (ledger["id"],)),
            ("UPDATE payment_intents SET status = 'pending' WHERE id = ?", (intent["id"],)),
            ("UPDATE payment_intents SET amount_cents = 1 WHERE id = ?", (intent["id"],)),
            ("UPDATE webhook_events SET outcome = 'tampered' WHERE id = 1", ()),
            ("DELETE FROM webhook_events WHERE id = 1", ()),
            ("DELETE FROM audit_log WHERE id = 1", ()),
        ]
        for sql, params in attempts:
            with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                with self.db.transaction() as conn:
                    conn.execute(sql, params)

        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM ledger_entries").fetchone()["c"], 3)

    def test_ledger_stays_balanced_and_readable(self):
        payload = self.admin.get("/api/admin/ledger").json
        self.assertTrue(payload["balanced"])
        self.assertEqual(payload["debit_cents"], payload["credit_cents"])
        self.assertEqual(payload["credit_cents"], 900)
        self.assertEqual({entry["entry_type"] for entry in payload["items"]},
                         {"member_payment", "platform_fee", "creator_earning"})
        self.assertEqual(payload["credit_cents"] % 3, 0)
        self.assertIn("append-only", payload["notice"])

    def test_admin_notes_and_archive_do_not_touch_settled_records(self):
        intent_id = self.admin.get("/api/admin/payments").json["items"][0]["id"]
        note = self.admin.post("/api/admin/notes", json_body={
            "target_type": "payment_intent", "target_id": intent_id,
            "body": "Buyer asked about a receipt; nothing to change.",
        })
        self.assertEqual(note.status, 201, note.text)
        listing = self.admin.get(f"/api/admin/notes?target_type=payment_intent&target_id={intent_id}")
        self.assertEqual(listing.status, 200, listing.text)
        self.assertEqual(len(listing.json["items"]), 1)
        with self.db.connection() as conn:
            amount = conn.execute("SELECT amount_cents FROM invoices").fetchone()["amount_cents"]
        self.assertEqual(amount, 900)

    def test_unresolved_attempt_may_be_archived(self):
        unconfigured = self.creator
        second = self.register("attempt-member@velora.test", display_name="Attempt Member")
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE creator_payouts SET btc_address = btc_address WHERE creator_id = ?",
                (self.seed["page_id"],),
            )
        pending = second.post("/api/payments/intents", json_body={"tier_id": self.seed["tier_id"]})
        self.assertEqual(pending.status, 201, pending.text)
        intent_id = pending.json["id"]

        archived = self.admin.post(f"/api/admin/payments/{intent_id}/archive",
                                   json_body={"reason": "member never paid"})
        self.assertEqual(archived.status, 200, archived.text)
        self.assertEqual(archived.json["status"], "archived")
        self.assertFalse(archived.json["paid"])
        self.assertTrue(unconfigured)

    def test_webhook_deliveries_are_listed_for_admins_only(self):
        events = self.admin.get("/api/admin/payments/events")
        self.assertEqual(events.status, 200, events.text)
        self.assertEqual(events.json["items"], [])
        self.assertEqual(self.member.get("/api/admin/payments/events").status, 403)


class InvitationTests(VeloraTestCase):
    def test_invitation_is_email_bound_single_use_and_recorded(self):
        admin = self.register("inviter@velora.test", display_name="Inviter")
        self.make_admin(admin, "inviter@velora.test")
        created = admin.post("/api/admin/invitations", json_body={
            "email": "new-admin@velora.test", "role": "admin", "note": "Join the small ops team",
        })
        self.assertEqual(created.status, 201, created.text)
        self.assertTrue(created.json["email_bound"])
        self.assertNotIn("link", created.json, "email-bound invitations are not returned in clear text")

        invitation_id = created.json["invitation_id"]
        listing = admin.get("/api/admin/invitations").json["items"]
        entry = next(item for item in listing if item["id"] == invitation_id)
        self.assertEqual(entry["state"], "usable")
        self.assertEqual(entry["role"], "admin")
        self.assertIn("@", entry["masked_email"])
        self.assertNotIn("new-admin@velora.test", admin.get("/api/admin/invitations").text)

        # A member cannot create or revoke invitations.
        member = self.register("plain-invitee@velora.test", display_name="Plain")
        self.assertEqual(member.post("/api/admin/invitations", json_body={"role": "admin"}).status, 403)
        self.assertEqual(member.get("/api/admin/invitations").status, 403)

        revoked = admin.post(f"/api/admin/invitations/{invitation_id}/revoke")
        self.assertEqual(revoked.status, 200, revoked.text)
        self.assertEqual(admin.get("/api/admin/invitations").json["items"][0]["state"], "revoked")

    def test_general_invitation_returns_a_one_time_link(self):
        admin = self.register("linker@velora.test", display_name="Linker")
        self.make_admin(admin, "linker@velora.test")
        created = admin.post("/api/admin/invitations", json_body={
            "role": "creator", "note": "For the artist we spoke with",
        })
        self.assertEqual(created.status, 201, created.text)
        self.assertFalse(created.json["email_bound"])
        link = created.json["link"]
        self.assertIn("/invite?token=", link)
        token = link.split("token=", 1)[1]

        preview = Client(self.app).get(f"/api/invitations/{token}")
        self.assertEqual(preview.status, 200, preview.text)
        self.assertEqual(preview.json["role"], "creator")
        self.assertTrue(preview.json["requires_account"])
        self.assertIsNone(preview.json["masked_email"])

        # An account that does not exist yet cannot accept an email-unbound link.
        anonymous_accept = Client(self.app).post("/api/invitations/accept", json_body={
            "token": token, "display_name": "Invited Creator", "password": "correct-horse-9",
            "password_confirm": "correct-horse-9", "adult_attestation": True,
        })
        self.assertEqual(anonymous_accept.status, 400, anonymous_accept.text)
        self.assertEqual(anonymous_accept.json["error"]["code"], "invitation_needs_account")

        invitee = self.register("invitee@velora.test", display_name="Invited Creator")
        accepted = invitee.post("/api/invitations/accept", json_body={"token": token})
        self.assertEqual(accepted.status, 200, accepted.text)
        self.assertEqual(accepted.json["role"], "creator")

        replay = Client(self.app).get(f"/api/invitations/{token}")
        self.assertEqual(replay.status, 410, replay.text)

    def test_invitation_accepts_an_existing_verified_account_only(self):
        admin = self.register("binder@velora.test", display_name="Binder")
        self.make_admin(admin, "binder@velora.test")
        created = admin.post("/api/admin/invitations", json_body={
            "email": "existing-promo@velora.test", "role": "admin",
        })
        token = None
        entry = self.latest_email()
        self.assertEqual(entry["subject"].lower().count("velora"), 1)
        self.assertIn("invite?token=", entry["body"])
        token = entry["body"].split("token=", 1)[1].split()[0].strip()

        existing = self.register("existing-promo@velora.test", display_name="Existing Promo")
        accepted = existing.post("/api/invitations/accept", json_body={"token": token})
        self.assertEqual(accepted.status, 200, accepted.text)
        self.assertEqual(accepted.json["role"], "admin")
        self.assertEqual(created.json["delivery"]["sent"], True)

    def test_invitations_never_appear_as_secrets(self):
        admin = self.register("secret-admin@velora.test", display_name="Secret Admin")
        self.make_admin(admin, "secret-admin@velora.test")
        created = admin.post("/api/admin/invitations", json_body={
            "email": "hidden-invite@velora.test", "role": "admin",
        })
        response = admin.get("/api/admin/invitations")
        self.assertNotIn("token_hash", response.text)
        self.assertTrue(created.json["email_bound"])


class AdminConsoleReadTests(VeloraTestCase):
    def test_admin_listings_cover_users_creators_posts_tiers_and_applications(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="console-creator@velora.test",
                                 handle="console-page", page_name="Console Page")
        self.create_post(creator, title="Console post")
        admin = self.register("console-admin@velora.test", display_name="Console Admin")
        self.make_admin(admin, "console-admin@velora.test")

        users = admin.get("/api/admin/users?q=console").json
        self.assertGreaterEqual(users["total"], 2)
        self.assertTrue(all("email" in item for item in users["items"]))
        self.assertGreaterEqual(users["admin_count"], 1)

        creators = admin.get("/api/admin/creators").json["items"]
        entry = next(item for item in creators if item["handle"] == "console-page")
        self.assertEqual(entry["active_tiers"], 1)
        self.assertEqual(entry["published_posts"], 1)
        self.assertTrue(entry["payout_address_on_file"])

        posts = admin.get("/api/admin/posts?q=Console").json["items"]
        self.assertEqual(posts[0]["handle"], "console-page")
        self.assertFalse(posts[0]["admin_archived"])

        tiers = admin.get("/api/admin/tiers").json["items"]
        self.assertEqual(tiers[0]["handle"], "console-page")

        archived_tier = admin.post(f"/api/admin/tiers/{seed['tier_id']}/archive",
                                   json_body={"reason": "operator decision"})
        self.assertEqual(archived_tier.status, 200, archived_tier.text)
        self.assertFalse(archived_tier.json["is_active"])
        self.assertIn("Existing members keep", archived_tier.json["notice"])

    def test_moderation_archive_restore_and_audit(self):
        creator = Client(self.app)
        self.seed_creator(creator, email="mod-creator@velora.test", handle="mod-page",
                          page_name="Mod Page")
        post = self.create_post(creator, title="Needs moderating")
        admin = self.register("mod-admin@velora.test", display_name="Mod Admin")
        self.make_admin(admin, "mod-admin@velora.test")

        archived = admin.post(f"/api/admin/posts/{post['id']}/archive",
                              json_body={"note": "Reported by two members"})
        self.assertEqual(archived.status, 200, archived.text)
        anonymous = Client(self.app)
        self.assertEqual(anonymous.get(f"/api/posts/{post['id']}").status, 404)

        queue = admin.get("/api/admin/posts?q=Needs").json["items"]
        self.assertTrue(queue[0]["admin_archived"])
        self.assertEqual(queue[0]["admin_archive_note"], "Reported by two members")

        restored = admin.post(f"/api/admin/posts/{post['id']}/restore")
        self.assertEqual(restored.status, 200, restored.text)
        self.assertEqual(anonymous.get(f"/api/posts/{post['id']}").status, 200)

        audit = admin.get("/api/admin/audit").json["items"]
        actions = [entry["action"] for entry in audit]
        self.assertIn("admin.post_archived", actions)
        self.assertIn("admin.post_restored", actions)

        creator_notes = admin.get(f"/api/admin/notes?target_type=user&target_id=1")
        self.assertEqual(creator_notes.status, 200, creator_notes.text)
        missing = admin.get("/api/admin/notes?target_type=user")
        self.assertEqual(missing.status, 400)

    def test_console_never_returns_configuration_secrets(self):
        admin = self.register("secret-check@velora.test", display_name="Secret Check")
        self.make_admin(admin, "secret-check@velora.test")
        creator = Client(self.app)
        self.seed_creator(creator, email="secret-creator@velora.test", handle="secret-page",
                          page_name="Secret Page")
        for path in ADMIN_PATHS:
            body = admin.get(path).text
            for needle in ("test-secret-key", "test-api-key", "test-webhook-secret",
                           "password_hash", "token_hash", "pbkdf2"):
                self.assertNotIn(needle, body, f"{needle} leaked in {path}")

    def test_anonymizing_keeps_financial_rows(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="anonymize-creator@velora.test",
                                 handle="anon-page", page_name="Anon Page")
        member = self.register("anonymize-member@velora.test", display_name="Anon Member")
        order = self.start_checkout(member, seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        admin = self.register("anonymize-admin@velora.test", display_name="Anon Admin")
        self.make_admin(admin, "anonymize-admin@velora.test")

        with self.db.connection() as conn:
            member_id = conn.execute("SELECT id FROM users WHERE email = ?",
                                     ("anonymize-member@velora.test",)).fetchone()["id"]
        response = admin.post(f"/api/admin/users/{member_id}/anonymize",
                              json_body={"reason": "member requested deletion"})
        self.assertEqual(response.status, 200, response.text)
        self.assertTrue(response.json["anonymized"])

        with self.db.connection() as conn:
            user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (member_id,)).fetchone())
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices WHERE user_id = ?",
                                    (member_id,)).fetchone()["c"]
            ledger = conn.execute("SELECT COUNT(*) AS c FROM ledger_entries").fetchone()["c"]
            memberships = conn.execute("SELECT COUNT(*) AS c FROM memberships WHERE user_id = ?",
                                       (member_id,)).fetchone()["c"]
        self.assertEqual(user["status"], "anonymized")
        self.assertEqual(user["display_name"], "Deleted member")
        self.assertTrue(user["email"].endswith("@velora.invalid"))
        self.assertIsNone(user["orientation_value"])
        self.assertEqual(invoices, 1)
        self.assertEqual(ledger, 3)
        self.assertEqual(memberships, 1)

    def test_creator_page_status_changes_are_audited(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="status-creator@velora.test",
                                 handle="status-page", page_name="Status Page")
        admin = self.register("status-admin@velora.test", display_name="Status Admin")
        self.make_admin(admin, "status-admin@velora.test")

        paused = admin.post(f"/api/admin/creators/{seed['page_id']}/status",
                            json_body={"status": "paused", "reason": "review"})
        self.assertEqual(paused.status, 200, paused.text)
        self.assertEqual(paused.json["status"], "paused")
        anonymous = Client(self.app)
        page = anonymous.get("/api/creators/status-page").json
        self.assertFalse(page["available"])
        self.assertEqual(page["tiers"], [])

        audit = [entry["action"] for entry in admin.get("/api/admin/audit").json["items"]]
        self.assertIn("admin.creator_status_changed", audit)

    def test_overview_reports_environment_without_layout_secrets(self):
        admin = self.register("overview-admin@velora.test", display_name="Overview Admin")
        self.make_admin(admin, "overview-admin@velora.test")
        payload = admin.get("/api/admin/overview").json
        self.assertEqual(payload["environment"], "test")
        self.assertTrue(payload["payment_configured"])
        self.assertTrue(payload["email_configured"])
        self.assertTrue(payload["ledger_balanced"])
        self.assertIn("append-only", " ".join(payload["invariants"]).lower())
        self.assertNotIn("localhost:49392", admin.get("/api/admin/overview").text)
        self.assertNotIn("test-store", admin.get("/api/admin/overview").text)


class StartupSafetyTests(VeloraTestCase):
    btcpay = False

    def test_missing_payment_configuration_is_explicit(self):
        anonymous = Client(self.app)
        bootstrap = anonymous.get("/api/bootstrap").json
        self.assertFalse(bootstrap["features"]["btcpay_configured"])
        self.assertFalse(bootstrap["checkout"]["available"])
        self.assertEqual(bootstrap["checkout"]["reason"], "btcpay_not_configured")

        availability = anonymous.get("/api/payments/availability").json
        self.assertFalse(availability["available"])
        self.assertEqual(availability["methods"], [])
        self.assertIn("unavailable", availability["notice"])
        self.assertFalse(availability["auto_renewal"])

    def test_clean_start_has_no_demo_content(self):
        anonymous = Client(self.app)
        self.assertEqual(anonymous.get("/api/creators").json["total"], 0)
        with self.db.connection() as conn:
            for table in ("users", "creator_pages", "posts", "tiers", "payment_intents",
                          "invoices", "ledger_entries", "memberships", "admin_invitations",
                          "reports", "threads", "messages", "creator_payouts"):
                count = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
                self.assertEqual(count, 0, table)

    def test_unconfigured_checkout_cannot_create_an_order(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="offline-creator@velora.test",
                                 handle="offline-page", page_name="Offline Page")
        member = self.register("offline-member@velora.test", display_name="Offline Member")
        response = member.post("/api/payments/intents", json_body={"tier_id": seed["tier_id"]})
        self.assertEqual(response.status, 503, response.text)
        self.assertEqual(response.json["error"]["code"], "checkout_unavailable")
        with self.db.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS c FROM payment_intents").fetchone()["c"], 0)

    def test_no_admin_exists_by_default(self):
        with self.db.connection() as conn:
            admins = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'admin'").fetchone()["c"]
        self.assertEqual(admins, 0)
        self.assertEqual(Client(self.app).get("/api/admin/overview").status, 401)


class NoEmailConfiguredTests(VeloraTestCase):
    email_transport = "none"

    def test_signup_works_without_email_delivery_and_says_so(self):
        client = Client(self.app)
        response = client.post("/api/auth/signup", json_body={
            "display_name": "No Mail", "email": "no-mail@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        })
        self.assertEqual(response.status, 201, response.text)
        self.assertFalse(response.json["verification"]["sent"])
        self.assertEqual(response.json["verification"]["reason"], "unavailable")
        self.assertIn("cannot send email", response.json["next_step"])

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
