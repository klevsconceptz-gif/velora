"""Creator studio: authorization, page/tier/post management and payout privacy.

Everything here is checked through the HTTP surface, because the guarantee is
server-side: a member cannot read or change another creator's studio, member
emails never reach a creator, and a payout address is visible only to its owner
and authorized administrators.
"""

from __future__ import annotations

import unittest

from .harness import Client, VeloraTestCase


class StudioAccessTests(VeloraTestCase):
    def test_member_without_page_cannot_open_studio(self):
        client = self.register("plain-member@velora.test", display_name="Plain Member")
        for path in ("/api/studio/overview", "/api/studio/tiers", "/api/studio/posts",
                     "/api/studio/members", "/api/studio/payout"):
            response = client.get(path)
            self.assertEqual(response.status, 403, f"{path}: {response.text}")
            self.assertEqual(response.json["error"]["code"], "creator_page_required")

    def test_anonymous_cannot_open_studio(self):
        anonymous = Client(self.app)
        for path in ("/api/studio/overview", "/api/studio/tiers", "/api/studio/posts",
                     "/api/studio/members", "/api/studio/payout"):
            self.assertEqual(anonymous.get(path).status, 401, path)

    def test_creator_cannot_touch_another_creators_post(self):
        first = Client(self.app)
        self.seed_creator(self.client if False else first, email="one@velora.test", handle="one-page",
                          page_name="One Page")
        post = self.create_post(first, title="One's private draft", publish=False)

        second = Client(self.app)
        self.seed_creator(second, email="two@velora.test", handle="two-page", page_name="Two Page")

        self.assertEqual(second.get(f"/api/studio/posts/{post['id']}").status, 404)
        self.assertEqual(second.patch(f"/api/studio/posts/{post['id']}",
                                      json_body={"title": "Hijacked"}).status, 404)
        self.assertEqual(second.post(f"/api/studio/posts/{post['id']}/publish").status, 404)
        self.assertEqual(second.post(f"/api/studio/posts/{post['id']}/archive").status, 404)

    def test_tier_cannot_be_edited_by_another_creator(self):
        owner = Client(self.app)
        seed = self.seed_creator(owner, email="owner@velora.test", handle="owner-page",
                                 page_name="Owner Page")
        intruder = Client(self.app)
        self.seed_creator(intruder, email="intruder@velora.test", handle="intruder-page",
                          page_name="Intruder Page")
        response = intruder.patch(f"/api/studio/tiers/{seed['tier_id']}",
                                  json_body={"name": "Stolen", "price": "5.00"})
        self.assertEqual(response.status, 404, response.text)
        listing = owner.get("/api/studio/tiers").json["items"]
        self.assertEqual(listing[0]["name"], "Supporter")

    def test_unverified_member_cannot_write_to_studio(self):
        client = Client(self.app)
        self.register("unverified@velora.test", display_name="Unverified", client=client, verify=False)
        seed = self.seed_creator(Client(self.app), email="seed@velora.test", handle="seed-page",
                                 page_name="Seed Page")
        # The unverified account has no page, so the first gate is the page check.
        response = client.post("/api/studio/posts", json_body={
            "title": "Nope", "teaser": "Teaser text long enough.", "body": "Body",
            "visibility": "members",
        })
        self.assertEqual(response.status, 403, response.text)
        self.assertIn(response.json["error"]["code"], ("creator_page_required", "email_verification_required"))
        self.assertTrue(seed["handle"])

    def test_studio_overview_reports_only_own_numbers(self):
        client = Client(self.app)
        seed = self.seed_creator(client, email="only@velora.test", handle="only-page",
                                 page_name="Only Page")
        overview = client.get("/api/studio/overview")
        self.assertEqual(overview.status, 200, overview.text)
        payload = overview.json
        self.assertEqual(payload["page"]["handle"], "only-page")
        self.assertEqual(payload["stats"]["active_members"], 0)
        self.assertEqual(payload["stats"]["settled_invoice_count"], 0)
        self.assertEqual(payload["stats"]["gross_cents"], 0)
        self.assertEqual(len(payload["tiers"]), 1)
        self.assertEqual(payload["tiers"][0]["id"], seed["tier_id"])


class StudioPageTests(VeloraTestCase):
    def test_page_details_update_and_pause(self):
        client = Client(self.app)
        self.seed_creator(client, email="editor@velora.test", handle="editor-page",
                          page_name="Editor Page")
        response = client.patch("/api/studio/page", json_body={
            "tagline": "Quiet films about loud places",
            "about": "I publish one essay and two photos every week.",
            "category": "writing",
        })
        self.assertEqual(response.status, 200, response.text)
        page = response.json["page"]
        self.assertEqual(page["tagline"], "Quiet films about loud places")
        self.assertEqual(page["category"], "writing")
        self.assertEqual(page["handle"], "editor-page")

        paused = client.patch("/api/studio/page", json_body={"status": "paused"})
        self.assertEqual(paused.status, 200, paused.text)
        self.assertEqual(paused.json["page"]["status"], "paused")

        # A paused page disappears from discovery and closes its tiers.
        anonymous = Client(self.app)
        listing = anonymous.get("/api/creators").json
        self.assertEqual([item["handle"] for item in listing["items"]], [])
        quote = client.get(f"/api/payments/quote?tier_id=1")
        self.assertEqual(quote.status, 200)
        self.assertFalse(quote.json["can_checkout"])
        self.assertIn("page_unavailable",
                      [blocker["code"] for blocker in quote.json["blockers"]])

    def test_unknown_category_is_rejected(self):
        client = Client(self.app)
        self.seed_creator(client, email="cat@velora.test", handle="cat-page", page_name="Cat Page")
        response = client.patch("/api/studio/page", json_body={"category": "not-a-category"})
        self.assertEqual(response.status, 400)
        self.assertEqual(response.json["error"]["field"], "category")


class StudioTierTests(VeloraTestCase):
    def test_tier_prices_are_validated_as_usd_cents(self):
        client = Client(self.app)
        self.seed_creator(client, email="prices@velora.test", handle="price-page",
                          page_name="Price Page")
        cheap = client.post("/api/studio/tiers", json_body={"name": "Too cheap", "price": "0.50"})
        self.assertEqual(cheap.status, 400)
        self.assertEqual(cheap.json["error"]["field"], "price")

        created = client.post("/api/studio/tiers",
                              json_body={"name": "Supporter", "price": "12.50",
                                         "description": "Weekly notes"})
        self.assertEqual(created.status, 201, created.text)
        self.assertEqual(created.json["price_cents"], 1250)
        self.assertEqual(created.json["period_days"], 30)

    def test_pausing_a_tier_keeps_paid_access_and_closes_new_sales(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="pause@velora.test", handle="pause-page",
                                 page_name="Pause Page")
        member = self.register("pause-member@velora.test", display_name="Pause Member")
        order = self.start_checkout(member, seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        self.assertEqual(len(member.get("/api/memberships").json["items"]), 1)

        paused = creator.post(f"/api/studio/tiers/{seed['tier_id']}/active",
                              json_body={"is_active": False})
        self.assertEqual(paused.status, 200, paused.text)
        self.assertFalse(paused.json["is_active"])
        self.assertIn("Existing members keep", paused.json["notice"])

        memberships = member.get("/api/memberships").json["items"]
        self.assertTrue(memberships[0]["access"]["active"])

        new_member = self.register("pause-new@velora.test", display_name="New Member")
        blocked = new_member.post("/api/payments/intents", json_body={"tier_id": seed["tier_id"]})
        self.assertEqual(blocked.status, 409, blocked.text)
        self.assertEqual(blocked.json["error"]["code"], "tier_inactive")

    def test_non_boolean_active_flag_is_rejected(self):
        client = Client(self.app)
        seed = self.seed_creator(client, email="flag@velora.test", handle="flag-page",
                                 page_name="Flag Page")
        response = client.post(f"/api/studio/tiers/{seed['tier_id']}/active",
                               json_body={"is_active": "yes"})
        self.assertEqual(response.status, 400)
        self.assertEqual(response.json["error"]["field"], "is_active")


class StudioMemberListTests(VeloraTestCase):
    def test_creator_sees_members_without_emails(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="listing@velora.test", handle="listing-page",
                                 page_name="Listing Page")
        member = self.register("quiet-member@velora.test", display_name="Quiet Member")
        order = self.start_checkout(member, seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])

        response = creator.get("/api/studio/members")
        self.assertEqual(response.status, 200, response.text)
        self.assertEqual(response.json["privacy_note"], "Member email addresses are never shared with creators.")
        raw = response.text
        self.assertNotIn("quiet-member@velora.test", raw)
        self.assertNotIn("@velora.test", raw)
        item = response.json["items"][0]
        self.assertEqual(item["member_name"], "Quiet Member")
        self.assertTrue(item["active"])
        self.assertEqual(item["tier_name"], "Supporter")
        self.assertEqual(sorted(item.keys()),
                         ["active", "cancel_requested_at", "ends_at", "member_id", "member_name",
                          "membership_id", "started_at", "status", "tier_id", "tier_name"])

    def test_member_cannot_read_a_creator_member_list(self):
        creator = Client(self.app)
        self.seed_creator(creator, email="private-list@velora.test", handle="private-page",
                          page_name="Private Page")
        member = self.register("nosy@velora.test", display_name="Nosy Member")
        self.assertEqual(member.get("/api/studio/members").status, 403)


class PayoutAddressTests(VeloraTestCase):
    def test_owner_and_admin_see_the_address_but_others_cannot(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="payout@velora.test", handle="payout-page",
                                 page_name="Payout Page")
        address = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"

        owner_view = creator.get("/api/studio/payout")
        self.assertEqual(owner_view.status, 200, owner_view.text)
        self.assertEqual(owner_view.json["payout"]["btc_address"], address)
        self.assertIn("does not send funds", owner_view.json["payout"]["notice"])

        member = self.register("payout-member@velora.test", display_name="Member")
        self.assertEqual(member.get("/api/studio/payout").status, 403)

        admin = self.register("payout-admin@velora.test", display_name="Ops")
        self.make_admin(admin, "payout-admin@velora.test")
        admin_view = admin.get(f"/api/admin/creators/{seed['page_id']}/payout")
        self.assertEqual(admin_view.status, 200, admin_view.text)
        self.assertEqual(admin_view.json["payout"]["btc_address"], address)

        other_creator = Client(self.app)
        self.seed_creator(other_creator, email="other@velora.test", handle="other-page",
                          page_name="Other Page")
        # A different creator cannot read this address through the admin route either.
        self.assertEqual(other_creator.get(f"/api/admin/creators/{seed['page_id']}/payout").status, 403)

    def test_address_is_never_in_public_payloads(self):
        creator = Client(self.app)
        address = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"
        self.seed_creator(creator, email="leak@velora.test", handle="leak-page",
                          page_name="Leak Page", address=address)
        anonymous = Client(self.app)
        for path in ("/api/creators", "/api/creators/leak-page", "/api/creators/leak-page/posts",
                     "/api/privacy/summary", "/api/bootstrap", "/api/payments/availability"):
            response = anonymous.get(path)
            self.assertNotIn(address, response.text, path)
            self.assertNotIn("btc_address", response.text, path)

    def test_saving_an_address_moves_no_funds(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="saver@velora.test", handle="saver-page",
                                 page_name="Saver Page")
        response = creator.put("/api/studio/payout",
                               json_body={"btc_address": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"})
        self.assertEqual(response.status, 200, response.text)
        payout = response.json["payout"]
        self.assertEqual(payout["btc_address"], "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2")
        self.assertEqual(payout["address_kind"], "p2pkh")

        # Saving a destination creates no financial record of any kind.
        with self.db.connection() as conn:
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
            ledger = conn.execute("SELECT COUNT(*) AS c FROM ledger_entries").fetchone()["c"]
            intents = conn.execute("SELECT COUNT(*) AS c FROM payment_intents").fetchone()["c"]
        self.assertEqual((invoices, ledger, intents), (0, 0, 0))
        self.assertTrue(seed["page_id"])

    def test_seed_phrases_and_private_keys_are_rejected(self):
        creator = Client(self.app)
        self.seed_creator(creator, email="reject@velora.test", handle="reject-page",
                          page_name="Reject Page")
        for candidate in (
            "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
            "5Kb8kLf9zgWQnogidDA76MzPL6TsZZY36hWXMssSzNydYXYB9KF",
            "not-an-address-at-all",
            "",
        ):
            response = creator.put("/api/studio/payout", json_body={"btc_address": candidate})
            self.assertEqual(response.status, 400, f"{candidate!r}: {response.text}")
            self.assertEqual(response.json["error"]["field"], "btc_address")

    def test_payout_requires_verified_creator(self):
        client = Client(self.app)
        self.register("unverified-payout@velora.test", display_name="Unverified", client=client,
                      verify=False)
        response = client.put("/api/studio/payout",
                              json_body={"btc_address": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"})
        self.assertEqual(response.status, 403, response.text)


class StudioPostAuthoringTests(VeloraTestCase):
    def test_post_lifecycle_is_reflected_in_public_listings(self):
        creator = Client(self.app)
        self.seed_creator(creator, email="life@velora.test", handle="life-page",
                          page_name="Life Page")
        post = self.create_post(creator, title="Draft first", publish=False)
        self.assertEqual(post["status"], "draft")

        anonymous = Client(self.app)
        self.assertEqual(anonymous.get("/api/creators/life-page").json["post_count"], 0)

        creator.post(f"/api/studio/posts/{post['id']}/publish")
        listed = anonymous.get("/api/creators/life-page").json
        self.assertEqual(listed["post_count"], 1)
        self.assertEqual(listed["posts"][0]["title"], "Draft first")

        creator.post(f"/api/studio/posts/{post['id']}/unpublish")
        self.assertEqual(anonymous.get("/api/creators/life-page").json["post_count"], 0)

    def test_members_only_post_requires_a_teaser(self):
        creator = Client(self.app)
        self.seed_creator(creator, email="teaser@velora.test", handle="teaser-page",
                          page_name="Teaser Page")
        response = creator.post("/api/studio/posts", json_body={
            "title": "No teaser", "teaser": "", "body": "Secret", "visibility": "members",
        })
        self.assertEqual(response.status, 400)
        self.assertEqual(response.json["error"]["field"], "teaser")

    def test_owner_preview_serves_own_media_only(self):
        creator = Client(self.app)
        self.seed_creator(creator, email="media@velora.test", handle="media-page",
                          page_name="Media Page")
        post = self.create_post(creator, title="With image")
        from .harness import PNG_BYTES

        upload = creator.post(f"/api/studio/posts/{post['id']}/media?filename=note.png",
                              body=PNG_BYTES, content_type="image/png")
        self.assertEqual(upload.status, 201, upload.text)
        media_id = upload.json["id"]

        preview = creator.get(f"/api/studio/media/{media_id}")
        self.assertEqual(preview.status, 200)
        self.assertEqual(preview.body, PNG_BYTES)
        self.assertEqual(preview.header("cache-control"), "private, no-store")

        stranger = self.register("media-stranger@velora.test", display_name="Stranger")
        # A member with no creator page is stopped at the studio gate.
        self.assertEqual(stranger.get(f"/api/studio/media/{media_id}").status, 403)

        # Another creator with a real page is told the media does not exist.
        other = Client(self.app)
        self.seed_creator(other, email="media-other@velora.test", handle="media-other",
                          page_name="Other Media Page")
        self.assertEqual(other.get(f"/api/studio/media/{media_id}").status, 404)

    def test_studio_post_listing_never_contains_member_emails(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="list-posts@velora.test", handle="posts-page",
                                 page_name="Posts Page")
        self.create_post(creator, title="Visible post")
        member = self.register("hidden-member@velora.test", display_name="Hidden Member")
        order = self.start_checkout(member, seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])

        raw = creator.get("/api/studio/posts").text
        self.assertNotIn("hidden-member@velora.test", raw)
        page = creator.get("/api/studio/overview").text
        self.assertNotIn("hidden-member@velora.test", page)
        members = creator.get("/api/studio/members").text
        self.assertNotIn("hidden-member@velora.test", members)


class ApplicationFlowTests(VeloraTestCase):
    def test_application_requires_a_pitch_and_a_verified_account(self):
        client = Client(self.app)
        self.register("applicant@velora.test", display_name="Applicant", client=client)
        short = client.post("/api/creators/apply",
                            json_body={"category": "art", "pitch": "too short"})
        self.assertEqual(short.status, 400)

        created = client.post("/api/creators/apply", json_body={
            "category": "art",
            "pitch": "I paint large landscapes and publish process photos every week for members.",
            "desired_handle": "applicant-page",
        })
        self.assertEqual(created.status, 201, created.text)
        self.assertEqual(created.json["status"], "pending")
        self.assertEqual(created.json["desired_handle"], "applicant-page")

        duplicate = client.post("/api/creators/apply", json_body={
            "category": "art",
            "pitch": "A second application while the first one is still waiting for review.",
        })
        self.assertEqual(duplicate.status, 409)
        self.assertEqual(duplicate.json["error"]["code"], "application_pending")

        mine = client.get("/api/creators/applications/mine")
        self.assertEqual(mine.json["application"]["status"], "pending")

    def test_admin_review_creates_the_page_and_checks_handle_conflicts(self):
        applicant = self.register("review-me@velora.test", display_name="Review Me")
        created = applicant.post("/api/creators/apply", json_body={
            "category": "music",
            "pitch": "I record one acoustic session a month and share the stems with members.",
            "desired_handle": "review-me",
        })
        self.assertEqual(created.status, 201, created.text)
        application_id = created.json["id"]

        admin = self.register("reviewer@velora.test", display_name="Reviewer")
        self.make_admin(admin, "reviewer@velora.test")

        taken = Client(self.app)
        self.seed_creator(taken, email="taken@velora.test", handle="taken-handle",
                          page_name="Taken Page")

        conflict = admin.post(f"/api/admin/applications/{application_id}/review",
                              json_body={"decision": "approve", "handle": "taken-handle"})
        self.assertEqual(conflict.status, 409, conflict.text)
        self.assertEqual(conflict.json["error"]["code"], "handle_taken")

        approved = admin.post(f"/api/admin/applications/{application_id}/review",
                              json_body={"decision": "approve", "handle": "review-me",
                                         "note": "Welcome aboard."})
        self.assertEqual(approved.status, 200, approved.text)
        self.assertEqual(approved.json["status"], "approved")
        self.assertEqual(approved.json["handle"], "review-me")

        again = admin.post(f"/api/admin/applications/{application_id}/review",
                           json_body={"decision": "reject"})
        self.assertEqual(again.status, 409)
        self.assertEqual(again.json["error"]["code"], "already_reviewed")

        studio = applicant.get("/api/studio/overview")
        self.assertEqual(studio.status, 200, studio.text)
        self.assertEqual(studio.json["page"]["handle"], "review-me")

    def test_rejection_message_is_visible_to_the_applicant(self):
        applicant = self.register("rejected@velora.test", display_name="Rejected")
        created = applicant.post("/api/creators/apply", json_body={
            "category": "other",
            "pitch": "I would like to publish a monthly zine about urban gardening for members.",
        })
        admin = self.register("rejecter@velora.test", display_name="Rejecter")
        self.make_admin(admin, "rejecter@velora.test")
        response = admin.post(f"/api/admin/applications/{created.json['id']}/review",
                              json_body={"decision": "reject",
                                         "note": "We are not accepting this category yet."})
        self.assertEqual(response.status, 200, response.text)
        mine = applicant.get("/api/creators/applications/mine").json["application"]
        self.assertEqual(mine["status"], "rejected")
        self.assertEqual(mine["decision_note"], "We are not accepting this category yet.")


class LegacyStudioContractTests(VeloraTestCase):
    """Small guards on shapes the browser depends on."""

    def test_studio_overview_shape(self):
        client = Client(self.app)
        self.seed_creator(client, email="shape@velora.test", handle="shape-page",
                          page_name="Shape Page")
        payload = client.get("/api/studio/overview").json
        self.assertEqual(sorted(payload.keys()), ["page", "payout", "recent_posts", "stats", "tiers"])
        for key in ("active_members", "total_members", "published_posts", "page_views",
                    "gross_cents", "platform_fee_cents", "net_cents", "settled_invoice_count",
                    "net_last_30_days_cents", "invoices_last_30_days", "open_payment_attempts"):
            self.assertIn(key, payload["stats"])

    def test_tier_listing_marks_active_members(self):
        creator = Client(self.app)
        seed = self.seed_creator(creator, email="marker@velora.test", handle="marker-page",
                                 page_name="Marker Page")
        member = self.register("counter@velora.test", display_name="Counter")
        order = self.start_checkout(member, seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        items = creator.get("/api/studio/tiers").json["items"]
        self.assertEqual(items[0]["active_members"], 1)

    def test_upload_limit_is_enforced(self):
        creator = Client(self.app)
        self.seed_creator(creator, email="limit@velora.test", handle="limit-page",
                          page_name="Limit Page")
        post = self.create_post(creator, title="Limits")
        # Just over the upload limit: the media layer explains the real limit.
        oversized = b"\x89PNG\r\n\x1a\n" + b"x" * (self.config.max_upload_bytes + 128)
        response = creator.post(f"/api/studio/posts/{post['id']}/media?filename=big.png",
                                body=oversized, content_type="image/png")
        self.assertEqual(response.status, 400, response.text)
        self.assertEqual(response.json["error"]["code"], "invalid_media")

        # Far over it: the request never reaches the handler.
        huge = b"\x89PNG\r\n\x1a\n" + b"x" * (self.config.max_upload_bytes + 8192)
        too_big = creator.post(f"/api/studio/posts/{post['id']}/media?filename=huge.png",
                               body=huge, content_type="image/png")
        self.assertEqual(too_big.status, 413, too_big.text)
        self.assertEqual(too_big.json["error"]["code"], "payload_too_large")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
