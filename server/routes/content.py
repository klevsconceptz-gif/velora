"""Post reads, gated media delivery and the member feed."""

from __future__ import annotations

from ..http import Response, not_found
from ..routing import AUTH_OPTIONAL, AUTH_REQUIRED, route
from ..services import posts as posts_service
from .base import ok


@route("GET", "/api/posts/<int:post_id>", auth=AUTH_OPTIONAL)
def get_post(request, ctx, auth, params):
    """A post as this caller may see it.

    Locked posts return the teaser and metadata only. Nothing private is placed in
    the response, so viewing source cannot reveal members-only content.
    """
    payload = posts_service.get_post_for_viewer(ctx, auth, params["post_id"], request)
    payload["gate_notice"] = (
        "Velora checks access on the server for every request. A locked post is never sent to your "
        "browser, so there is nothing hidden in the page source."
    )
    return ok(payload)


@route("GET", "/api/posts/<int:post_id>/media/<int:media_id>", auth=AUTH_OPTIONAL)
def get_post_media(request, ctx, auth, params):
    """Serve attachment bytes only after the same server-side gate is satisfied."""
    row = posts_service.authorize_media_download(ctx, auth, params["post_id"], params["media_id"])
    data = ctx.media.read(row["storage_key"])
    return Response(
        200,
        data,
        content_type=row["content_type"],
        headers={
            "Cache-Control": "private, max-age=0, no-store",
            "Content-Disposition": "inline",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@route("GET", "/api/feed", auth=AUTH_REQUIRED)
def member_feed(request, ctx, auth, params):
    limit = request.query_int("limit", 30, minimum=1, maximum=100) or 30
    payload = posts_service.member_feed(ctx, auth, limit=limit)
    if not payload["items"]:
        payload["empty_state"] = {
            "title": "Your feed is quiet",
            "body": (
                "Posts from creators you support appear here. Discover creators to find someone worth "
                "following, or renew a membership to see their members-only posts again."
            ),
            "action": {"label": "Discover creators", "href": "#/discover"},
        }
    return ok(payload)


@route("GET", "/api/creators/<handle:handle>/posts", auth=AUTH_OPTIONAL)
def creator_posts(request, ctx, auth, params):
    from ..services import creators as creators_service

    limit = request.query_int("limit", 20, minimum=1, maximum=50) or 20
    with ctx.db.connection() as conn:
        page = creators_service.page_by_handle(conn, params["handle"].lower())
        if page is None:
            raise not_found("That creator page does not exist.")
        page = dict(page)
        if page["status"] != "active":
            return ok({"items": [], "total": 0, "available": False})
        feed = posts_service.creator_feed(ctx, conn, page, auth, limit=limit)
    feed["available"] = True
    return ok(feed)
