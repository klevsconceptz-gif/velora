"""Discovery, public creator pages and the creator studio."""

from __future__ import annotations

from ..http import bad_request, json_response, not_found
from ..routing import AUTH_NONE, AUTH_OPTIONAL, AUTH_REQUIRED, route
from ..serializers import tier_public
from ..services import creators as creators_service
from ..services import posts as posts_service
from .base import ok


# ---------------------------------------------------------------------------
# Public discovery
# ---------------------------------------------------------------------------


@route("GET", "/api/creators", auth=AUTH_OPTIONAL)
def list_creators(request, ctx, auth, params):
    with ctx.db.connection() as conn:
        result = creators_service.discover(
            conn,
            query=request.query_one("q"),
            category=request.query_one("category"),
            sort=request.query_one("sort") or "recent",
            page=request.query_int("page", 1, minimum=1) or 1,
            per_page=request.query_int("per_page", 12, minimum=1, maximum=48) or 12,
        )
    result["disclaimer"] = (
        "Discovery never filters on sexual orientation. Paused pages are hidden from this list."
    )
    return ok(result)


@route("GET", "/api/categories", auth=AUTH_NONE)
def categories(request, ctx, auth, params):
    return ok({"items": creators_service.categories()})


@route("GET", "/api/creators/<handle:handle>", auth=AUTH_OPTIONAL)
def public_page(request, ctx, auth, params):
    handle = params["handle"].lower()
    # Celebrity-style rate limit: simple counting, keyed by a salted client hash.
    ctx.gate_or_flag("page_view", request.client_ip(ctx.config.trust_proxy))
    payload = creators_service.public_page(ctx, handle, auth)
    if payload.get("available"):
        creators_service.record_page_view(ctx, payload["id"])
    return ok(payload)


# ---------------------------------------------------------------------------
# Studio
# ---------------------------------------------------------------------------


@route("GET", "/api/studio/overview", auth=AUTH_REQUIRED)
def studio_overview(request, ctx, auth, params):
    return ok(creators_service.studio_overview(ctx, auth))


@route("PATCH", "/api/studio/page", auth=AUTH_REQUIRED)
def update_page(request, ctx, auth, params):
    return ok({"page": creators_service.update_page(ctx, auth, request.json())})


@route("GET", "/api/studio/tiers", auth=AUTH_REQUIRED)
def studio_tiers(request, ctx, auth, params):
    with ctx.db.connection() as conn:
        page = creators_service.require_page(conn, auth)
        tiers = creators_service.tiers_for(conn, page["id"], include_inactive=True)
        members = {
            row["tier_id"]: row["c"]
            for row in conn.execute(
                """
                SELECT tier_id, COUNT(*) AS c FROM memberships
                WHERE creator_id = ? AND status = 'active' AND ends_at > ?
                GROUP BY tier_id
                """,
                (page["id"], _now()),
            )
        }
    items = []
    for tier in tiers:
        item = tier_public(tier)
        item["active_members"] = int(members.get(tier["id"], 0))
        items.append(item)
    return ok({"items": items})


def _now() -> str:
    from ..db import now_iso

    return now_iso()


@route("POST", "/api/studio/tiers", auth=AUTH_REQUIRED)
def create_tier(request, ctx, auth, params):
    return json_response(creators_service.upsert_tier(ctx, auth, request.json()), status=201)


@route("PATCH", "/api/studio/tiers/<int:tier_id>", auth=AUTH_REQUIRED)
def update_tier(request, ctx, auth, params):
    return ok(creators_service.upsert_tier(ctx, auth, request.json(), tier_id=params["tier_id"]))


@route("POST", "/api/studio/tiers/<int:tier_id>/active", auth=AUTH_REQUIRED)
def set_tier_active(request, ctx, auth, params):
    payload = request.json()
    active = payload.get("is_active")
    if not isinstance(active, bool):
        raise bad_request("Provide is_active true or false.", code="validation_error", field="is_active")
    return ok(creators_service.set_tier_active(ctx, auth, params["tier_id"], active))


@route("GET", "/api/studio/posts", auth=AUTH_REQUIRED)
def studio_posts(request, ctx, auth, params):
    return ok(posts_service.studio_posts(ctx, auth, status=request.query_one("status")))


@route("GET", "/api/studio/posts/<int:post_id>", auth=AUTH_REQUIRED)
def studio_post_detail(request, ctx, auth, params):
    return ok(posts_service.owner_post(ctx, auth, params["post_id"]))


@route("POST", "/api/studio/posts", auth=AUTH_REQUIRED)
def create_post(request, ctx, auth, params):
    return json_response(posts_service.create_post(ctx, auth, request.json()), status=201)


@route("PATCH", "/api/studio/posts/<int:post_id>", auth=AUTH_REQUIRED)
def update_post(request, ctx, auth, params):
    return ok(posts_service.update_post(ctx, auth, params["post_id"], request.json()))


@route("POST", "/api/studio/posts/<int:post_id>/publish", auth=AUTH_REQUIRED)
def publish_post(request, ctx, auth, params):
    return ok(posts_service.publish_post(ctx, auth, params["post_id"], publish=True))


@route("POST", "/api/studio/posts/<int:post_id>/unpublish", auth=AUTH_REQUIRED)
def unpublish_post(request, ctx, auth, params):
    return ok(posts_service.publish_post(ctx, auth, params["post_id"], publish=False))


@route("POST", "/api/studio/posts/<int:post_id>/archive", auth=AUTH_REQUIRED)
def archive_post(request, ctx, auth, params):
    return ok(posts_service.archive_post(ctx, auth, params["post_id"]))


@route("POST", "/api/studio/posts/<int:post_id>/media", auth=AUTH_REQUIRED, body_limit="upload")
def upload_media(request, ctx, auth, params):
    data = request.body
    if not data:
        raise bad_request("Choose an image to attach.", code="validation_error", field="file")
    filename = request.query_one("filename")
    return json_response(
        posts_service.create_post_media(ctx, auth, params["post_id"], data, filename), status=201
    )


@route("DELETE", "/api/studio/posts/<int:post_id>/media/<int:media_id>", auth=AUTH_REQUIRED)
def delete_media(request, ctx, auth, params):
    return ok(posts_service.delete_post_media(ctx, auth, params["post_id"], params["media_id"]))


@route("GET", "/api/studio/members", auth=AUTH_REQUIRED)
def studio_members(request, ctx, auth, params):
    return ok(creators_service.creator_members(ctx, auth))


@route("GET", "/api/studio/payout", auth=AUTH_REQUIRED)
def get_payout(request, ctx, auth, params):
    with ctx.db.connection() as conn:
        page = creators_service.require_page(conn, auth)
    return ok({"payout": creators_service.payout_for_actor(ctx, auth, page["id"])})


@route("PUT", "/api/studio/payout", auth=AUTH_REQUIRED)
def save_payout(request, ctx, auth, params):
    """Original single-address call; records the creator's Bitcoin wallet."""
    return ok({"payout": creators_service.save_payout_address(ctx, auth, request.json())})


@route("GET", "/api/studio/wallets", auth=AUTH_REQUIRED)
def list_wallets(request, ctx, auth, params):
    with ctx.db.connection() as conn:
        page = creators_service.require_page(conn, auth)
    return ok({"payout": creators_service.payout_for_actor(ctx, auth, page["id"])})


@route("PUT", "/api/studio/wallets/<str:asset>", auth=AUTH_REQUIRED)
def save_wallet(request, ctx, auth, params):
    body = request.json()
    return ok({"payout": creators_service.save_wallet(ctx, auth, params["asset"], body.get("address"))})


@route("DELETE", "/api/studio/wallets/<str:asset>", auth=AUTH_REQUIRED)
def remove_wallet(request, ctx, auth, params):
    return ok({"payout": creators_service.remove_wallet(ctx, auth, params["asset"])})


@route("GET", "/api/studio/media/<int:media_id>", auth=AUTH_REQUIRED)
def studio_media_preview(request, ctx, auth, params):
    """Owner-only preview of an attachment, including drafts."""
    from ..http import Response

    row = posts_service.authorize_owner_media(ctx, auth, params["media_id"])
    if row is None:
        raise not_found("Media not found.")
    data = ctx.media.read(row["storage_key"])
    return Response(
        200,
        data,
        content_type=row["content_type"],
        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"},
    )


@route("PATCH", "/api/studio/posts/<int:post_id>/tiers", auth=AUTH_REQUIRED)
def set_post_tiers(request, ctx, auth, params):
    payload = dict(request.json())
    payload["tier_ids"] = payload.get("tier_ids", [])
    return ok(posts_service.update_post(ctx, auth, params["post_id"], {"tier_ids": payload["tier_ids"]}))



