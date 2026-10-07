"""Posts, image attachments, and the server-side content gate.

Every read path in this module decides visibility on the server. The frontend
receives a locked post as *teaser + metadata only*: no body, no media ids, no
media bytes. Media bytes are only ever written to a response by
:func:`authorize_media_download`, which re-checks the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import audit
from ..db import now_iso
from ..http import ApiError, bad_request, forbidden, not_found
from ..serializers import media_public, post_for_owner, post_for_viewer
from ..validation import (
    MAX_POST_BODY,
    MAX_POST_TITLE,
    MAX_TEASER,
    ValidationError,
    clean_bool,
    clean_text,
)
from .accounts import Auth, require_active, require_admin, require_verified
from .creators import require_page, tiers_for


@dataclass(frozen=True)
class Entitlement:
    """Whether a viewer may read a creator's members-only posts."""

    active: bool = False
    tier_ids: frozenset[int] = field(default_factory=frozenset)
    membership: dict | None = None
    ends_at: str | None = None

    def covers(self, post_tier_ids: list[int]) -> bool:
        if not self.active:
            return False
        if not post_tier_ids:
            # A members-only post without an explicit tier list is unlocked by any
            # active membership of that creator.
            return True
        return bool(self.tier_ids.intersection(post_tier_ids))


def entitlement_for(conn, viewer_user_id: int | None, creator_id: int) -> Entitlement:
    if viewer_user_id is None:
        return Entitlement()
    row = conn.execute(
        """
        SELECT * FROM memberships
        WHERE user_id = ? AND creator_id = ? AND status = 'active' AND ends_at > ?
        ORDER BY ends_at DESC LIMIT 1
        """,
        (viewer_user_id, creator_id, now_iso()),
    ).fetchone()
    if row is None:
        return Entitlement()
    membership = dict(row)
    # The entitlement covers the tier the member paid for. A post may restrict
    # itself to one or more tiers; the check happens in Entitlement.covers.
    return Entitlement(
        active=True,
        tier_ids=frozenset([membership["tier_id"]]),
        membership=membership,
        ends_at=membership["ends_at"],
    )


def post_tier_ids(conn, post_id: int) -> list[int]:
    rows = conn.execute("SELECT tier_id FROM post_tiers WHERE post_id = ? ORDER BY tier_id", (post_id,)).fetchall()
    return [row["tier_id"] for row in rows]


def post_media_rows(conn, post_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM post_media WHERE post_id = ? ORDER BY position, id", (post_id,)).fetchall()
    return [dict(row) for row in rows]


def decide_access(post: dict, entitlement: Entitlement, *, viewer_user_id: int | None,
                  owner_user_id: int | None, is_admin: bool = False) -> tuple[bool, str]:
    """Return ``(allowed, access_label)``. This is the single source of truth."""
    if post.get("admin_archived"):
        return (False, "admin_archived")
    if post["status"] != "published":
        if viewer_user_id is not None and owner_user_id == viewer_user_id:
            return (True, "owner_draft")
        return (False, "unpublished")
    if viewer_user_id is not None and owner_user_id == viewer_user_id:
        return (True, "owner")
    if post["visibility"] == "public":
        return (True, "public")
    if is_admin:
        return (True, "admin")
    if entitlement.active and entitlement.covers(post_tier_ids_of(post)):
        return (True, "member")
    return (False, "locked")


def post_tier_ids_of(post: dict) -> list[int]:
    """Tier ids carried on an assembled post dict (see :func:`assemble_posts`)."""
    return list(post.get("tier_ids") or [])


def assemble_posts(conn, rows) -> list[dict]:
    """Attach tier and media metadata once per post to avoid N+1 queries."""
    posts = []
    for row in rows:
        post = dict(row)
        post["tier_ids"] = post_tier_ids(conn, post["id"])
        post["media_rows"] = post_media_rows(conn, post["id"])
        posts.append(post)
    return posts


def serialize_for_viewer(post: dict, *, allowed: bool, access: str) -> dict:
    media = [media_public(row) for row in post.get("media_rows", [])] if allowed else []
    return post_for_viewer(
        post,
        locked=not allowed,
        media=media,
        access=access,
        tier_ids=[tier_id for tier_id in post.get("tier_ids", [])] if allowed else [],
    )


# ---------------------------------------------------------------------------
# Creator studio operations
# ---------------------------------------------------------------------------


def _validate_post_payload(payload: dict, *, partial: bool) -> dict:
    fields: dict[str, object] = {}
    try:
        if not partial or "title" in payload:
            fields["title"] = clean_text(payload.get("title"), field="title", required=True,
                                         max_length=MAX_POST_TITLE, allow_newlines=False, label="Title")
        if not partial or "teaser" in payload:
            fields["teaser"] = clean_text(payload.get("teaser") or "", field="teaser",
                                          max_length=MAX_TEASER, label="Teaser")
        if not partial or "body" in payload:
            fields["body"] = clean_text(payload.get("body") or "", field="body", max_length=MAX_POST_BODY)
        if not partial or "visibility" in payload:
            visibility = payload.get("visibility") or "members"
            if visibility not in ("public", "members"):
                raise ValidationError("Choose whether this post is public or members-only.", "visibility")
            fields["visibility"] = visibility
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None
    return fields


def _validate_tier_selection(conn, creator_id: int, raw) -> list[int]:
    """Tier ids must belong to this creator's own tiers."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise bad_request("tier_ids must be a list of tier ids.", code="validation_error", field="tier_ids")
    owned = {tier["id"] for tier in tiers_for(conn, creator_id, include_inactive=True)}
    selection: list[int] = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            raise bad_request("tier_ids must contain whole numbers.", code="validation_error",
                              field="tier_ids") from None
        if value not in owned:
            raise bad_request("Choose tiers from your own page.", code="validation_error", field="tier_ids")
        if value not in selection:
            selection.append(value)
    return selection


def create_post(ctx, auth: Auth, payload: dict) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    fields = _validate_post_payload(payload, partial=False)
    wants_publish = clean_bool(payload.get("publish"), field="publish", default=False)

    if fields["visibility"] == "members" and len(fields["teaser"]) < 10:
        raise bad_request(
            "Members-only posts need a short public teaser so visitors understand what they are "
            "missing. Add at least a sentence.",
            code="validation_error",
            field="teaser",
        )
    if not fields["body"]:
        raise bad_request("Add the post body before saving.", code="validation_error", field="body")

    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        tier_ids = _validate_tier_selection(conn, page["id"], payload.get("tier_ids"))
        created = now_iso()
        post_id = conn.execute(
            """
            INSERT INTO posts (creator_id, title, teaser, body, visibility, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (page["id"], fields["title"], fields["teaser"], fields["body"], fields["visibility"],
             "published" if wants_publish else "draft", created, created),
        ).lastrowid
        if wants_publish:
            conn.execute("UPDATE posts SET published_at = ? WHERE id = ?", (created, post_id))
        for tier_id in tier_ids:
            conn.execute("INSERT OR IGNORE INTO post_tiers (post_id, tier_id) VALUES (?, ?)",
                         (post_id, tier_id))
        audit.record(conn, action="creator.post_created", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="post", target_id=post_id,
                     meta={"visibility": fields["visibility"], "published": wants_publish})
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        post = assemble_posts(conn, [row])[0]
    return post_for_owner(post, media=[media_public(m) for m in post["media_rows"]],
                          tier_ids=post["tier_ids"])


def update_post(ctx, auth: Auth, post_id: int, payload: dict) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    fields = _validate_post_payload(payload, partial=True)

    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        row = conn.execute("SELECT * FROM posts WHERE id = ? AND creator_id = ?",
                           (post_id, page["id"])).fetchone()
        if row is None:
            raise not_found("Post not found.")
        post = dict(row)
        visibility = fields.get("visibility", post["visibility"])
        teaser = fields.get("teaser", post["teaser"])
        if visibility == "members" and len(teaser) < 10:
            raise bad_request(
                "Members-only posts need a short public teaser. Add at least a sentence.",
                code="validation_error",
                field="teaser",
            )
        if "tier_ids" in payload:
            tier_ids = _validate_tier_selection(conn, page["id"], payload.get("tier_ids"))
            conn.execute("DELETE FROM post_tiers WHERE post_id = ?", (post_id,))
            for tier_id in tier_ids:
                conn.execute("INSERT OR IGNORE INTO post_tiers (post_id, tier_id) VALUES (?, ?)",
                             (post_id, tier_id))
        if fields:
            assignments = ", ".join(f"{column} = ?" for column in fields)
            conn.execute(
                f"UPDATE posts SET {assignments}, updated_at = ? WHERE id = ?",  # noqa: S608
                (*fields.values(), now_iso(), post_id),
            )
        audit.record(conn, action="creator.post_updated", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="post", target_id=post_id, meta={"fields": sorted(fields.keys())})
        updated = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        post = assemble_posts(conn, [updated])[0]
    return post_for_owner(post, media=[media_public(m) for m in post["media_rows"]],
                          tier_ids=post["tier_ids"])


def publish_post(ctx, auth: Auth, post_id: int, publish: bool = True) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        row = conn.execute("SELECT * FROM posts WHERE id = ? AND creator_id = ?",
                           (post_id, page["id"])).fetchone()
        if row is None:
            raise not_found("Post not found.")
        post = dict(row)
        if publish:
            if not post["body"]:
                raise bad_request("Add the post body before publishing.",
                                  code="validation_error", field="body")
            if post["visibility"] == "members" and len(post["teaser"] or "") < 10:
                raise bad_request("Members-only posts need a public teaser before publishing.",
                                  code="validation_error", field="teaser")
            conn.execute("UPDATE posts SET status = 'published', published_at = ?, updated_at = ? WHERE id = ?",
                         (post.get("published_at") or now_iso(), now_iso(), post_id))
        else:
            conn.execute("UPDATE posts SET status = 'draft', updated_at = ? WHERE id = ?", (now_iso(), post_id))
        audit.record(conn, action="creator.post_published" if publish else "creator.post_unpublished",
                     actor_user_id=auth.user_id, actor_role=auth.role, target_type="post", target_id=post_id)
        updated = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        post = assemble_posts(conn, [updated])[0]
    return post_for_owner(post, media=[media_public(m) for m in post["media_rows"]],
                          tier_ids=post["tier_ids"])


def archive_post(ctx, auth: Auth, post_id: int) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        row = conn.execute("SELECT * FROM posts WHERE id = ? AND creator_id = ?",
                           (post_id, page["id"])).fetchone()
        if row is None:
            raise not_found("Post not found.")
        conn.execute(
            "UPDATE posts SET status = 'archived', archived_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), post_id),
        )
        audit.record(conn, action="creator.post_archived", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="post", target_id=post_id)
    return {"archived": True, "id": post_id}


def studio_posts(ctx, auth: Auth, *, status: str | None = None) -> dict:
    require_verified(auth)
    with ctx.db.connection() as conn:
        page = require_page(conn, auth)
        params: list = [page["id"]]
        clause = ""
        if status in ("draft", "published", "archived"):
            clause = "AND status = ?"
            params.append(status)
        rows = conn.execute(
            f"""
            SELECT p.*,
              (SELECT COUNT(*) FROM post_media m WHERE m.post_id = p.id) AS media_count
            FROM posts p WHERE p.creator_id = ? {clause}
            ORDER BY COALESCE(p.published_at, p.updated_at) DESC, p.id DESC
            LIMIT 200
            """,  # noqa: S608
            params,
        ).fetchall()
        posts = assemble_posts(conn, rows)
    items = []
    for post in posts:
        payload = post_for_owner(post, media=[media_public(m) for m in post["media_rows"]],
                                 tier_ids=post["tier_ids"])
        payload["media_count"] = post.get("media_count", len(post["media_rows"]))
        items.append(payload)
    return {"items": items, "page": page["handle"]}


def owner_post(ctx, auth: Auth, post_id: int) -> dict:
    """A single post for its creator, including drafts and admin-archive state."""
    require_verified(auth)
    with ctx.db.connection() as conn:
        page = require_page(conn, auth)
        row = conn.execute("SELECT * FROM posts WHERE id = ? AND creator_id = ?",
                           (post_id, page["id"])).fetchone()
        if row is None:
            raise not_found("Post not found.")
        post = assemble_posts(conn, [row])[0]
    payload = post_for_owner(post, media=[media_public(m) for m in post["media_rows"]],
                             tier_ids=post["tier_ids"])
    payload["admin_archive_note"] = post.get("admin_archive_note")
    return payload


def authorize_owner_media(ctx, auth: Auth, media_id: int):
    """Media row for a creator's own attachment (drafts included)."""
    require_verified(auth)
    with ctx.db.connection() as conn:
        page = require_page(conn, auth)
        row = conn.execute(
            """
            SELECT m.* FROM post_media m JOIN posts p ON p.id = m.post_id
            WHERE m.id = ? AND p.creator_id = ?
            """,
            (media_id, page["id"]),
        ).fetchone()
    return dict(row) if row else None


def create_post_media(ctx, auth: Auth, post_id: int, data: bytes, filename: str | None) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("media_upload", auth.user_id)
    from ..media import MediaError

    try:
        content_type = ctx.media.validate_upload(data, filename)
    except MediaError as exc:
        raise bad_request(str(exc), code="invalid_media", field="file") from None

    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        post = conn.execute("SELECT * FROM posts WHERE id = ? AND creator_id = ?",
                            (post_id, page["id"])).fetchone()
        if post is None:
            raise not_found("Post not found.")
        position_row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS p FROM post_media WHERE post_id = ?", (post_id,)
        ).fetchone()
        stored = ctx.media.save(data, content_type)
        safe_name = _safe_original_name(filename)
        media_id = conn.execute(
            """
            INSERT INTO post_media (post_id, storage_key, original_name, content_type, byte_size, sha256, position, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (post_id, stored.storage_key, safe_name, stored.content_type, stored.byte_size, stored.sha256,
             int(position_row["p"]) + 1, now_iso()),
        ).lastrowid
        conn.execute("UPDATE posts SET updated_at = ? WHERE id = ?", (now_iso(), post_id))
        audit.record(conn, action="creator.media_attached", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="post", target_id=post_id,
                     meta={"content_type": stored.content_type, "bytes": stored.byte_size})
        row = conn.execute("SELECT * FROM post_media WHERE id = ?", (media_id,)).fetchone()
    return media_public(row)


def _safe_original_name(filename: str | None) -> str | None:
    if not filename:
        return None
    cleaned = "".join(ch for ch in str(filename) if ch.isprintable() and ch not in "/\\").strip()
    return cleaned[:120] or None


def delete_post_media(ctx, auth: Auth, post_id: int, media_id: int) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        row = conn.execute(
            """
            SELECT m.* FROM post_media m JOIN posts p ON p.id = m.post_id
            WHERE m.id = ? AND m.post_id = ? AND p.creator_id = ?
            """,
            (media_id, post_id, page["id"]),
        ).fetchone()
        if row is None:
            raise not_found("Attachment not found.")
        storage_key = dict(row)["storage_key"]
        conn.execute("DELETE FROM post_media WHERE id = ?", (media_id,))
        audit.record(conn, action="creator.media_removed", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="post", target_id=post_id, meta={"media_id": media_id})
    ctx.media.delete(storage_key)
    return {"deleted": True, "id": media_id}


# ---------------------------------------------------------------------------
# Authorized reads
# ---------------------------------------------------------------------------


def authorize_post_read(ctx, conn, post_row, viewer: Auth | None) -> tuple[dict, bool, str]:
    """Resolve access for one post row. Returns ``(post, allowed, access)``."""
    page = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (post_row["creator_id"],)).fetchone()
    if page is None:
        raise not_found("Post not found.")
    page = dict(page)
    owner = conn.execute("SELECT id, status FROM users WHERE id = ?", (page["user_id"],)).fetchone()
    owner_user_id = page["user_id"]
    owner_active = bool(owner) and owner["status"] == "active"

    entitlement = entitlement_for(conn, viewer.user_id if viewer else None, page["id"])
    post = assemble_posts(conn, [post_row])[0]
    allowed, access = decide_access(
        post,
        entitlement,
        viewer_user_id=viewer.user_id if viewer else None,
        owner_user_id=owner_user_id,
        is_admin=bool(viewer and viewer.is_admin),
    )
    if not owner_active and not (viewer and (viewer.is_admin or viewer.user_id == owner_user_id)):
        allowed, access = False, "creator_inactive"
    if page["status"] != "active" and not (viewer and (viewer.is_admin or viewer.user_id == owner_user_id)):
        allowed, access = False, "page_paused"
    return post, allowed, access


def get_post_for_viewer(ctx, auth: Auth | None, post_id: int, request=None) -> dict:
    with ctx.db.connection() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            raise not_found("Post not found.")
        post, allowed, access = authorize_post_read(ctx, conn, row, auth)
    if access == "admin" and auth is not None:
        with ctx.db.transaction() as conn:
            audit.record(conn, action="admin.post_viewed", actor_user_id=auth.user_id, actor_role="admin",
                         target_type="post", target_id=post_id, meta={"visibility": post["visibility"]})
    if not allowed and access in ("unpublished", "admin_archived"):
        raise not_found("Post not found.")
    return serialize_for_viewer(post, allowed=allowed, access=access)


def authorize_media_download(ctx, auth: Auth | None, post_id: int, media_id: int):
    """Return ``(row, content_type)`` or raise. Never leaks bytes for locked posts."""
    with ctx.db.connection() as conn:
        post_row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if post_row is None:
            raise not_found("Media not found.")
        media_row = conn.execute("SELECT * FROM post_media WHERE id = ? AND post_id = ?",
                                 (media_id, post_id)).fetchone()
        if media_row is None:
            raise not_found("Media not found.")
        _post, allowed, access = authorize_post_read(ctx, conn, post_row, auth)
    if not allowed:
        if access == "locked":
            raise ApiError(402, "membership_required",
                           "This attachment is part of a members-only post. Support this creator to unlock it.")
        raise not_found("Media not found.")
    return dict(media_row)


def creator_feed(ctx, conn, page: dict, viewer: Auth | None, limit: int = 12) -> dict:
    rows = conn.execute(
        """
        SELECT * FROM posts
        WHERE creator_id = ? AND status = 'published' AND admin_archived = 0
        ORDER BY COALESCE(published_at, created_at) DESC, id DESC
        LIMIT ?
        """,
        (page["id"], max(1, min(50, limit))),
    ).fetchall()
    entitlement = entitlement_for(conn, viewer.user_id if viewer else None, page["id"])
    items = []
    for post in assemble_posts(conn, rows):
        allowed, access = decide_access(
            post, entitlement,
            viewer_user_id=viewer.user_id if viewer else None,
            owner_user_id=page["user_id"],
            is_admin=bool(viewer and viewer.is_admin),
        )
        items.append(serialize_for_viewer(post, allowed=allowed, access=access))
    return {"items": items, "total": len(items)}


def member_feed(ctx, auth: Auth, *, limit: int = 30, before: str | None = None) -> dict:
    """The entitled feed: posts from the creators this member currently supports."""
    require_active(auth)
    with ctx.db.connection() as conn:
        rows = conn.execute(
            """
            SELECT p.*, c.handle, c.page_name
            FROM posts p
            JOIN creator_pages c ON c.id = p.creator_id
            JOIN memberships m ON m.creator_id = p.creator_id
            WHERE m.user_id = ? AND m.status = 'active' AND m.ends_at > ?
              AND p.status = 'published' AND p.admin_archived = 0
              AND c.status = 'active'
            ORDER BY COALESCE(p.published_at, p.created_at) DESC, p.id DESC
            LIMIT ?
            """,
            (auth.user_id, now_iso(), max(1, min(100, limit))),
        ).fetchall()
        memberships = conn.execute(
            """
            SELECT m.*, c.handle, c.page_name FROM memberships m
            JOIN creator_pages c ON c.id = m.creator_id
            WHERE m.user_id = ? AND m.status = 'active' AND m.ends_at > ?
            """,
            (auth.user_id, now_iso()),
        ).fetchall()
        entitled_by_creator = {row["creator_id"]: dict(row) for row in memberships}

        items = []
        for post in assemble_posts(conn, rows):
            entitlement = Entitlement(active=True, tier_ids=frozenset([entitled_by_creator[post["creator_id"]]["tier_id"]]),
                                      membership=entitled_by_creator.get(post["creator_id"]))
            allowed, access = decide_access(
                post, entitlement,
                viewer_user_id=auth.user_id,
                owner_user_id=_owner_id(conn, post["creator_id"]),
                is_admin=auth.is_admin,
            )
            payload = serialize_for_viewer(post, allowed=allowed, access=access)
            payload["creator"] = {
                "id": post["creator_id"],
                "handle": post["handle"],
                "page_name": post["page_name"],
            }
            items.append(payload)

    memberships_payload = [
        {
            "creator_id": row["creator_id"],
            "handle": row["handle"],
            "page_name": row["page_name"],
            "tier_id": row["tier_id"],
            "ends_at": row["ends_at"],
            "cancel_requested_at": row["cancel_requested_at"],
        }
        for row in memberships
    ]
    return {
        "items": items,
        "memberships": memberships_payload,
        "locked_count": sum(1 for item in items if item["locked"]),
        "empty_reason": None if memberships_payload else "no_memberships",
    }


def _owner_id(conn, creator_id: int) -> int | None:
    row = conn.execute("SELECT user_id FROM creator_pages WHERE id = ?", (creator_id,)).fetchone()
    return int(row["user_id"]) if row else None


def admin_archive_post(ctx, admin: Auth, post_id: int, note: str | None) -> dict:
    """Moderation archive. Content stops being served; nothing is deleted."""
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            raise not_found("Post not found.")
        conn.execute(
            "UPDATE posts SET admin_archived = 1, admin_archive_note = ?, updated_at = ? WHERE id = ?",
            (note, now_iso(), post_id),
        )
        audit.record(conn, action="admin.post_archived", actor_user_id=admin.user_id, actor_role="admin",
                     target_type="post", target_id=post_id, meta={"note_present": bool(note)})
    return {"archived": True, "id": post_id}


def admin_restore_post(ctx, admin: Auth, post_id: int) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        if row is None:
            raise not_found("Post not found.")
        conn.execute(
            "UPDATE posts SET admin_archived = 0, admin_archive_note = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), post_id),
        )
        audit.record(conn, action="admin.post_restored", actor_user_id=admin.user_id, actor_role="admin",
                     target_type="post", target_id=post_id)
    return {"restored": True, "id": post_id}


def require_creator_or_admin(auth: Auth) -> Auth:
    """Creators and administrators may use creator-side endpoints."""
    if not (auth.role in ("creator", "admin")):
        raise forbidden("Creator access is required.", code="creator_required")
    return auth
