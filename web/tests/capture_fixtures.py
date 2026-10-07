"""Capture real API responses for the frontend render check.

The browser UI cannot be exercised by the Python test suite, but it can be made
to render against *real* payloads. This script boots Velora in process with the
isolated test harness (fake BTCPay, development email transport), builds a small
but complete world — a creator with tiers and posts, a subscribed member, a
thread, a report, a held payment and an administrator — and records the JSON the
server actually returns for every endpoint the SPA calls.

The result is written to ``web/tests/fixtures/api.json`` and consumed by
``web/tests/render.mjs``, which loads the shipped view modules in Node against a
minimal DOM stub and renders every route.

Run it manually after changing the API::

    python3 web/tests/capture_fixtures.py

No secrets are involved: the fake BTCPay credentials and the development secret
key are generated inside the test harness and never written to the fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from server.tests.harness import JPEG_BYTES, Client, VeloraTestCase  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "api.json"


class _World(VeloraTestCase):
    """A harness instance that is set up and torn down by hand."""

    def runTest(self):  # pragma: no cover - unittest compatibility only
        pass


def clear_rate_limits(case: _World) -> None:
    """The fixture script signs several accounts up in a row from one address."""
    with case.db.transaction() as conn:
        conn.execute("DELETE FROM rate_limits")


def build_world(case: _World):
    admin = Client(case.app)
    case.register("ops@velora.test", display_name="Ops", client=admin)
    case.make_admin(admin, "ops@velora.test")

    creator = Client(case.app)
    seed = case.seed_creator(creator, email="creator@velora.test", handle="sample-creator",
                             page_name="Sample Studio", category="art", price_cents=1500)
    public_post = case.create_post(creator, title="Public sketchbook", visibility="public",
                                   teaser="A public teaser.", body="A public page body.")
    members_post = case.create_post(creator, title="Members-only process notes",
                                    visibility="members", teaser="What the last week looked like.",
                                    body="Detailed members-only notes.")
    creator.patch(f"/api/studio/posts/{members_post['id']}", json_body={
        "title": members_post["title"], "teaser": members_post["teaser"],
        "body": members_post["body"], "visibility": "members",
        "tier_ids": [seed["tier_id"]],
    })
    creator.post(f"/api/studio/posts/{members_post['id']}/media?filename=sketch.jpg",
                 body=JPEG_BYTES, content_type="image/jpeg")
    draft = case.create_post(creator, title="Draft in progress", visibility="public",
                             teaser="Teaser for the draft.", body="Draft body.", publish=False)
    creator.post(f"/api/studio/tiers", json_body={
        "name": "Patron", "price_cents": 3500, "description": "Higher tier with WIP streams.",
    })

    member = Client(case.app)
    clear_rate_limits(case)
    case.register("member@velora.test", display_name="Member Person", client=member)
    order = case.start_checkout(member, seed["tier_id"])
    case.settle_and_verify(member, order["order_ref"])
    membership_id = member.get("/api/memberships").json["items"][0]["membership"]["id"]
    member.post(f"/api/memberships/{membership_id}/cancel")

    thread = member.post("/api/threads", json_body={
        "creator_id": seed["page_id"], "body": "Hello! Loved the last post.",
    })
    if thread.status != 201:
        raise AssertionError(f"thread creation failed: {thread.status} {thread.text}")
    thread_id = thread.json["thread"]["id"]
    creator.post(f"/api/threads/{thread_id}/messages", json_body={"body": "Thank you — more soon."})
    member.post(f"/api/threads/{thread_id}/messages", json_body={"body": "Looking forward to it."})

    member.post("/api/reports", json_body={
        "target_type": "post", "target_id": members_post["id"],
        "reason_code": "spam", "details": "Checking the report flow renders.",
    })

    applicant = Client(case.app)
    clear_rate_limits(case)
    case.register("applicant@velora.test", display_name="Hopeful Creator", client=applicant)
    applicant.post("/api/creators/apply", json_body={
        "category": "music", "pitch": "I write original scores and want to share process work.",
        "desired_handle": "hopeful-scores",
    })

    # A second creator without a payout destination exercises the "checkout closed"
    # blocker, and an overpaid attempt against the first creator must be held
    # rather than unlocked.
    bare = Client(case.app)
    clear_rate_limits(case)
    bare_seed = case.seed_creator(bare, email="bare@velora.test", handle="bare-page",
                                  page_name="Bare Page", category="writing", price_cents=500)
    with case.db.transaction() as conn:
        conn.execute("DELETE FROM creator_payouts WHERE creator_id = ?", (bare_seed["page_id"],))
    held_order = case.start_checkout(member, seed["tier_id"])
    held_result = case.settle_and_verify(member, held_order["order_ref"], sats=900_000)
    if held_result.json.get("status") != "held":
        raise AssertionError(f"expected a held payment, got {held_result.status} {held_result.text}")

    admin.post("/api/admin/notes", json_body={
        "target_type": "user", "target_id": 2, "body": "Reviewed the payout address at signup.",
    })
    invitation = admin.post("/api/admin/invitations",
                            json_body={"role": "creator", "note": "Queued creator invite."})
    invitation_token = invitation.json["link"].split("token=", 1)[1]

    return {
        "admin": admin,
        "creator": creator,
        "member": member,
        "applicant": applicant,
        "anonymous": Client(case.app),
        "handles": seed,
        "post_id": members_post["id"],
        "draft_id": draft["id"],
        "thread_id": thread_id,
        "held_order_ref": held_order["order_ref"],
        "membership_id": membership_id,
        "invitation_token": invitation_token,
        "report_target": members_post["id"],
        "public_post_id": public_post["id"],
        "creator_page_id": seed["page_id"],
    }


def capture(case: _World, world) -> dict:
    admin, creator, member, applicant = (world["admin"], world["creator"], world["member"],
                                        world["applicant"])
    anonymous = world["anonymous"]
    handle = world["handles"]["handle"]

    read_paths = [
        ("anonymous", anonymous, "GET", "/api/bootstrap"),
        ("anonymous", anonymous, "GET", "/api/creators?per_page=12&sort=recent"),
        ("anonymous", anonymous, "GET", f"/api/creators/{handle}"),
        ("anonymous", anonymous, "GET", f"/api/creators/{handle}/posts"),
        ("anonymous", anonymous, "GET", f"/api/posts/{world['post_id']}"),
        ("anonymous", anonymous, "GET", f"/api/posts/{world['public_post_id']}"),
        ("anonymous", anonymous, "GET", "/api/privacy/summary"),
        ("anonymous", anonymous, "GET", "/api/payments/availability"),
        ("anonymous", anonymous, "GET", "/api/health"),
        ("member", member, "GET", "/api/auth/session"),
        ("member", member, "GET", "/api/account/sessions"),
        ("member", member, "GET", "/api/account/export"),
        ("member", member, "GET", "/api/account/privacy"),
        ("member", member, "GET", "/api/memberships"),
        ("member", member, "GET", "/api/payments/status"),
        ("member", member, "GET", f"/api/payments/orders/{world['held_order_ref']}"),
        ("member", member, "GET", "/api/feed"),
        ("member", member, "GET", "/api/threads"),
        ("member", member, "GET", f"/api/threads/{world['thread_id']}"),
        ("member", member, "GET", "/api/reports/mine"),
        ("member", member, "GET", "/api/creators/applications/mine"),
        ("creator", creator, "GET", "/api/studio/overview"),
        ("creator", creator, "GET", "/api/studio/posts"),
        ("creator", creator, "GET", f"/api/studio/posts/{world['draft_id']}"),
        ("creator", creator, "GET", f"/api/studio/posts/{world['public_post_id']}"),
        ("creator", creator, "GET", "/api/studio/posts?status=published"),
        ("creator", creator, "GET", "/api/studio/tiers"),
        ("creator", creator, "GET", "/api/studio/members"),
        ("creator", creator, "GET", "/api/studio/payout"),
        ("creator", creator, "GET", "/api/payments/quote?tier_id=%d" % world["handles"]["tier_id"]),
        ("member", member, "GET", "/api/payments/quote?tier_id=%d" % world["handles"]["tier_id"]),
        ("applicant", applicant, "GET", "/api/creators/applications/mine"),
        ("admin", admin, "GET", "/api/admin/overview"),
        ("admin", admin, "GET", "/api/admin/users"),
        ("admin", admin, "GET", "/api/admin/users/1"),
        ("admin", admin, "GET", "/api/admin/creators"),
        ("admin", admin, "GET", "/api/admin/applications"),
        ("admin", admin, "GET", "/api/admin/posts"),
        ("admin", admin, "GET", "/api/admin/tiers"),
        ("admin", admin, "GET", "/api/admin/reports"),
        ("admin", admin, "GET", "/api/admin/payments"),
        ("admin", admin, "GET", "/api/admin/ledger"),
        ("admin", admin, "GET", "/api/admin/audit"),
        ("admin", admin, "GET", "/api/admin/invitations"),
        ("member", member, "GET", "/api/bootstrap"),
        ("creator", creator, "GET", "/api/bootstrap"),
        ("admin", admin, "GET", "/api/bootstrap"),
        ("creator", creator, "GET", "/api/auth/session"),
        ("admin", admin, "GET", "/api/auth/session"),
        ("anonymous", anonymous, "GET", "/api/categories"),
        ("anonymous", anonymous, "GET", f"/api/invitations/{world['invitation_token']}"),
        ("member", member, "GET",
         f"/api/reports/target?target_type=post&target_id={world['report_target']}"),
    ]
    fixtures = {}
    by_role: dict[str, dict] = {}
    failures = []
    for role, client, method, path in read_paths:
        response = client.request(method, path)
        key = f"{method} {path}"
        if response.status >= 400:
            failures.append(f"{key} -> {response.status} {response.text[:200]}")
            continue
        try:
            body = json.loads(response.text)
        except ValueError:  # pragma: no cover - defensive
            failures.append(f"{key} -> non-JSON body")
            continue
        entry = {"role": role, "status": response.status, "json": body}
        fixtures[key] = entry
        by_role.setdefault(role, {})[key] = entry

    # Mutating endpoints are captured with request bodies the UI can also build,
    # so the render check exercises write paths too (against the live server).
    writes = [
        ("creator", creator, "POST", "/api/studio/tiers",
         {"name": "Sketch club", "price_cents": 1200, "description": "Monthly sketch pack."}),
        ("creator", creator, "PATCH", f"/api/studio/posts/{world['post_id']}",
         {"title": "Members-only process notes", "teaser": "What the last week looked like.",
          "body": "Detailed members-only notes.", "visibility": "members"}),
        ("member", member, "PUT", "/api/account/orientation",
         {"orientation": {"value": "bisexual"}, "visibility": "private"}),
        ("member", member, "PATCH", "/api/account/profile", {"bio": "Quiet art admirer."}),
        ("admin", admin, "POST", "/api/admin/notes",
         {"target_type": "user", "target_id": 2, "body": "Second note for the render check."}),
    ]
    for role, client, method, path, body in writes:
        response = client.request(method, path, json_body=body)
        key = f"{method} {path}"
        if response.status >= 400:
            failures.append(f"{key} -> {response.status} {response.text[:200]}")
            continue
        try:
            payload = json.loads(response.text)
        except ValueError:  # pragma: no cover - defensive
            failures.append(f"{key} -> non-JSON body")
            continue
        entry = {"role": role, "status": response.status, "json": payload}
        fixtures[key] = entry
        by_role.setdefault(role, {})[key] = entry

    return {"fixtures": fixtures, "by_role": by_role, "failures": failures}


def main() -> int:
    case = _World()
    case.setUp()
    try:
        world = build_world(case)
        captured = capture(case, world)
    finally:
        case.doCleanups()
        case.tearDown()

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(captured, indent=2, sort_keys=True), encoding="utf-8")
    print(f"captured {len(captured['fixtures'])} responses across "
          f"{len(captured['by_role'])} roles -> {FIXTURE_PATH.relative_to(REPO_ROOT)}")
    if captured["failures"]:
        print("endpoints that did not return success:")
        for failure in captured["failures"]:
            print("  -", failure)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
