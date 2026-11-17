"""Server-side content gating: locked posts, media bytes and the entitled feed."""

from __future__ import annotations

from .harness import JPEG_BYTES, PNG_BYTES, Client, VeloraTestCase


class GatingTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, email="gatekeeper@velora.test", handle="gatekeeper")
        self.public_post = self.create_post(self.creator, title="A public note",
                                            visibility="public", teaser="", body="Everyone can read this.",
                                            publish=True)
        self.locked_post = self.create_post(self.creator, title="Members only",
                                            visibility="members",
                                            teaser="A safe public teaser about the locked post.",
                                            body="SECRET BODY CONTENT", publish=True)
        with self.db.connection() as conn:
            self.seed["page_id"] = conn.execute("SELECT id FROM creator_pages WHERE handle = 'gatekeeper'"
                                                ).fetchone()["id"]

    def test_public_visitor_can_read_public_post(self):
        response = Client(self.app).get(f"/api/posts/{self.public_post['id']}")
        self.assertEqual(response.status, 200)
        self.assertFalse(response.json["locked"])
        self.assertEqual(response.json["body"], "Everyone can read this.")
        self.assertEqual(response.json["access"], "public")

    def test_locked_post_returns_teaser_only(self):
        response = Client(self.app).get(f"/api/posts/{self.locked_post['id']}")
        self.assertEqual(response.status, 200)
        payload = response.json
        self.assertTrue(payload["locked"])
        self.assertEqual(payload["access"], "locked")
        self.assertEqual(payload["teaser"], "A safe public teaser about the locked post.")
        self.assertIsNone(payload["body"])
        self.assertEqual(payload["media"], [])
        self.assertEqual(payload["tier_ids"], [])
        self.assertNotIn("SECRET BODY CONTENT", response.text)

    def test_signed_in_unsupporting_member_still_sees_teaser_only(self):
        outsider = self.register("outsider@example.com")
        response = outsider.get(f"/api/posts/{self.locked_post['id']}")
        self.assertTrue(response.json["locked"])
        self.assertNotIn("SECRET BODY CONTENT", response.text)

    def test_member_with_active_membership_reads_the_body(self):
        member = self.register("supporter@example.com")
        order = self.start_checkout(member, self.seed["tier_id"])
        settled = self.settle_and_verify(member, order["order_ref"])
        self.assertTrue(settled.json["access_granted"])

        response = member.get(f"/api/posts/{self.locked_post['id']}")
        self.assertFalse(response.json["locked"])
        self.assertEqual(response.json["body"], "SECRET BODY CONTENT")
        self.assertEqual(response.json["access"], "member")

    def test_expired_membership_loses_access_again(self):
        member = self.register("lapsed@example.com")
        order = self.start_checkout(member, self.seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        with self.db.transaction() as conn:
            conn.execute("UPDATE memberships SET ends_at = ? WHERE status = 'active'",
                         ("2020-01-01T00:00:00Z",))
        response = member.get(f"/api/posts/{self.locked_post['id']}")
        self.assertTrue(response.json["locked"])
        self.assertNotIn("SECRET BODY CONTENT", response.text)

    def test_creator_always_reads_own_drafts(self):
        draft = self.create_post(self.creator, title="Draft", visibility="members", publish=False)
        response = self.creator.get(f"/api/studio/posts/{draft['id']}")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.json["body"], "Private detail.")
        stranger = Client(self.app).get(f"/api/posts/{draft['id']}")
        self.assertEqual(stranger.status, 404)

    def test_gated_media_bytes_are_never_sent_to_unauthorised_clients(self):
        draft = self.create_post(self.creator, title="With image", visibility="members",
                                 body="A picture follows.", publish=True)
        upload = self.creator.post(f"/api/studio/posts/{draft['id']}/media?filename=shot.png",
                                  body=PNG_BYTES, content_type="image/png")
        self.assertEqual(upload.status, 201, upload.text)
        media_id = upload.json["id"]

        anon = Client(self.app).get(f"/api/posts/{draft['id']}/media/{media_id}")
        self.assertEqual(anon.status, 402)
        self.assertEqual(anon.json["error"]["code"], "membership_required")

        post_as_anon = Client(self.app).get(f"/api/posts/{draft['id']}")
        self.assertEqual(post_as_anon.json["media"], [], "media identifiers stay out of locked payloads")

        member = self.register("viewer@example.com")
        order = self.start_checkout(member, self.seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        allowed = member.get(f"/api/posts/{draft['id']}/media/{media_id}")
        self.assertEqual(allowed.status, 200)
        self.assertEqual(allowed.body, PNG_BYTES)
        self.assertEqual(allowed.header("content-type"), "image/png")

        owner = self.creator.get(f"/api/posts/{draft['id']}/media/{media_id}")
        self.assertEqual(owner.status, 200)

    def test_admin_archived_post_stops_being_served(self):
        admin = self.register("moderator@example.com")
        self.make_admin(admin, "moderator@example.com")
        response = admin.post(f"/api/admin/posts/{self.locked_post['id']}/archive",
                              json_body={"note": "reported"})
        self.assertEqual(response.status, 200)
        for client in (self.creator, Client(self.app)):
            fetched = client.get(f"/api/posts/{self.locked_post['id']}")
            self.assertEqual(fetched.status, 404)

    def test_media_upload_rejects_non_images(self):
        response = self.creator.post(f"/api/studio/posts/{self.public_post['id']}/media",
                                     body=b"<svg onload=alert(1)></svg>", content_type="image/svg+xml")
        self.assertEqual(response.status, 400)
        self.assertEqual(response.json["error"]["code"], "invalid_media")

    def test_media_upload_sniffs_content_and_ignores_claimed_type(self):
        response = self.creator.post(f"/api/studio/posts/{self.public_post['id']}/media",
                                     body=JPEG_BYTES, content_type="text/html")
        self.assertEqual(response.status, 201, response.text)
        self.assertEqual(response.json["content_type"], "image/jpeg")

    def test_other_creators_cannot_attach_media_to_someone_elses_post(self):
        intruder = Client(self.app)
        self.seed_creator(intruder, email="intruder@velora.test", handle="intruder")
        response = intruder.post(f"/api/studio/posts/{self.locked_post['id']}/media",
                                 body=PNG_BYTES, content_type="image/png")
        self.assertEqual(response.status, 404)

    def test_members_only_post_requires_a_teaser(self):
        response = self.creator.post("/api/studio/posts", json_body={
            "title": "No teaser", "body": "Body", "visibility": "members", "publish": True, "teaser": "",
        })
        self.assertEqual(response.status, 400)
        self.assertEqual(response.json["error"]["field"], "teaser")

    def test_member_feed_lists_only_reachable_creators(self):
        member = self.register("feeder@example.com")
        order = self.start_checkout(member, self.seed["tier_id"])
        self.settle_and_verify(member, order["order_ref"])
        feed = member.get("/api/feed")
        self.assertEqual(feed.status, 200)
        titles = [item["title"] for item in feed.json["items"]]
        self.assertIn("Members only", titles)
        self.assertIn("A public note", titles)
        self.assertTrue(all(not item["locked"] for item in feed.json["items"]))
        self.assertEqual(feed.json["memberships"][0]["handle"], "gatekeeper")
        self.assertNotIn("SECRET BODY", str(feed.json["memberships"]))

        stranger = self.register("stranger@example.com")
        self.assertEqual(stranger.get("/api/feed").json["items"], [])
        self.assertEqual(stranger.get("/api/feed").json["empty_reason"], "no_memberships")

    def test_public_page_feed_hides_locked_bodies(self):
        page = Client(self.app).get("/api/creators/gatekeeper")
        self.assertEqual(page.status, 200)
        locked = [post for post in page.json["posts"] if post["title"] == "Members only"][0]
        self.assertTrue(locked["locked"])
        self.assertIsNone(locked["body"])
        self.assertNotIn("SECRET BODY CONTENT", page.text)

    def test_creator_feed_endpoint_matches_page(self):
        response = Client(self.app).get("/api/creators/gatekeeper/posts")
        self.assertEqual(response.status, 200)
        self.assertEqual(len(response.json["items"]), 2)
        self.assertTrue(any(item["locked"] for item in response.json["items"]))

    def test_unknown_post_is_404(self):
        self.assertEqual(Client(self.app).get("/api/posts/99999").status, 404)
        self.assertEqual(Client(self.app).get("/api/posts/99999/media/1").status, 404)


class PostAuthoringTests(VeloraTestCase):
    def setUp(self):
        super().setUp()
        self.creator = Client(self.app)
        self.seed = self.seed_creator(self.creator, handle="author")
        with self.db.connection() as conn:
            self.page_id = conn.execute("SELECT id FROM creator_pages WHERE handle = 'author'"
                                        ).fetchone()["id"]

    def test_publish_unpublish_and_archive_lifecycle(self):
        post = self.create_post(self.creator, title="Lifecycle", publish=False)
        self.assertEqual(post["status"], "draft")
        published = self.creator.post(f"/api/studio/posts/{post['id']}/publish")
        self.assertEqual(published.json["status"], "published")
        self.assertIsNotNone(published.json["published_at"])
        unpublished = self.creator.post(f"/api/studio/posts/{post['id']}/unpublish")
        self.assertEqual(unpublished.json["status"], "draft")
        archived = self.creator.post(f"/api/studio/posts/{post['id']}/archive")
        self.assertTrue(archived.json["archived"])
        studio = self.creator.get("/api/studio/posts?status=archived")
        self.assertEqual([item["id"] for item in studio.json["items"]], [post["id"]])

    def test_update_post_validates_and_keeps_tiers(self):
        tier_ids = self.creator.post("/api/studio/tiers",
                                     json_body={"name": "VIP", "price": "25", "description": "All access"})
        self.assertEqual(tier_ids.status, 201, tier_ids.text)
        tier_id = tier_ids.json["id"]
        post = self.create_post(self.creator, tier_ids=[tier_id])
        self.assertEqual(post["tier_ids"], [tier_id])

        updated = self.creator.patch(f"/api/studio/posts/{post['id']}",
                                     json_body={"title": "Renamed", "teaser": "A fresh teaser for members."})
        self.assertEqual(updated.json["title"], "Renamed")
        self.assertEqual(updated.json["tier_ids"], [tier_id])

        foreign = self.creator.patch(f"/api/studio/posts/{post['id']}",
                                     json_body={"tier_ids": [99999]})
        self.assertEqual(foreign.status, 400)

    def test_tier_price_validation(self):
        too_low = self.creator.post("/api/studio/tiers", json_body={"name": "Cheap", "price": "0.50"})
        self.assertEqual(too_low.status, 400)
        bad_text = self.creator.post("/api/studio/tiers", json_body={"name": "Odd", "price": "free"})
        self.assertEqual(bad_text.status, 400)
        good = self.creator.post("/api/studio/tiers", json_body={"name": "Standard", "price": "12.50"})
        self.assertEqual(good.json["price_cents"], 1250)
        self.assertEqual(good.json["price_display"], "$12.50")

    def test_tier_pause_keeps_existing_members(self):
        tier = self.creator.post("/api/studio/tiers", json_body={"name": "Monthly", "price": "9"}).json
        member = self.register("existing@example.com")
        order = self.start_checkout(member, tier["id"])
        self.settle_and_verify(member, order["order_ref"])
        paused = self.creator.post(f"/api/studio/tiers/{tier['id']}/active",
                                   json_body={"is_active": False})
        self.assertFalse(paused.json["is_active"])
        self.assertIn("Existing members keep", paused.json["notice"])
        overview = member.get("/api/memberships").json
        self.assertTrue(overview["items"][0]["access"]["active"])
        blocked = member.post("/api/payments/intents", json_body={"tier_id": tier["id"]})
        self.assertEqual(blocked.status, 409)

    def test_deleting_media_removes_the_row(self):
        post = self.create_post(self.creator, title="Media")
        upload = self.creator.post(f"/api/studio/posts/{post['id']}/media?filename=x.png",
                                   body=PNG_BYTES, content_type="image/png")
        media_id = upload.json["id"]
        removed = self.creator.delete(f"/api/studio/posts/{post['id']}/media/{media_id}")
        self.assertTrue(removed.json["deleted"])
        self.creator.post(f"/api/studio/posts/{post['id']}/publish")
        gone = self.creator.get(f"/api/posts/{post['id']}/media/{media_id}")
        self.assertEqual(gone.status, 404)
