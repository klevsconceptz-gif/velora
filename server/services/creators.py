"""Creator pages, applications, tiers, wallet addresses and studio analytics."""

from __future__ import annotations

import json
import re

from .. import audit
from ..db import now_iso
from ..http import bad_request, conflict, forbidden, not_found
from ..security import mask_email
from ..serializers import (
    creator_application,
    creator_card,
    creator_page,
    tier_public,
)
from ..validation import (
    CATEGORIES,
    CATEGORY_KEYS,
    MAX_ABOUT,
    MAX_PITCH,
    MAX_TAGLINE,
    MAX_TIER_DESCRIPTION,
    MAX_TIER_NAME,
    ValidationError,
    clean_category,
    clean_handle,
    clean_text,
    price_to_cents,
)
from ..wallets import ASSETS, CATALOG, get_asset, validate_address
from .accounts import Auth, require_active, require_admin, require_verified

MIN_PITCH = 40


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def page_by_handle(conn, handle: str):
    return conn.execute("SELECT * FROM creator_pages WHERE handle = ?", (handle.strip().lower(),)).fetchone()


def page_for_user(conn, user_id: int):
    return conn.execute("SELECT * FROM creator_pages WHERE user_id = ?", (user_id,)).fetchone()


def require_page(conn, auth: Auth):
    page = page_for_user(conn, auth.user_id)
    if page is None:
        raise forbidden(
            "You need a creator page first. Apply from your account page to get started.",
            code="creator_page_required",
        )
    return page


def tiers_for(conn, creator_id: int, *, include_inactive: bool = False) -> list[dict]:
    clause = "" if include_inactive else "AND is_active = 1 AND archived_at IS NULL"
    rows = conn.execute(
        f"""
        SELECT * FROM tiers
        WHERE creator_id = ? {clause}
        ORDER BY position ASC, price_cents ASC, id ASC
        """,  # noqa: S608 - static fragment only
        (creator_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def tier_by_id(conn, tier_id: int):
    return conn.execute("SELECT * FROM tiers WHERE id = ?", (tier_id,)).fetchone()


def creator_stats(conn, creator_id: int) -> dict:
    active_members = conn.execute(
        "SELECT COUNT(*) AS c FROM memberships WHERE creator_id = ? AND status = 'active' AND ends_at > ?",
        (creator_id, now_iso()),
    ).fetchone()["c"]
    total_members = conn.execute(
        "SELECT COUNT(*) AS c FROM memberships WHERE creator_id = ?", (creator_id,)
    ).fetchone()["c"]
    published = conn.execute(
        "SELECT COUNT(*) AS c FROM posts WHERE creator_id = ? AND status = 'published'",
        (creator_id,),
    ).fetchone()["c"]
    views = conn.execute(
        "SELECT COALESCE(SUM(view_count), 0) AS c FROM creator_page_views WHERE creator_id = ?",
        (creator_id,),
    ).fetchone()["c"]
    return {
        "active_members": active_members,
        "total_members": total_members,
        "published_posts": published,
        "page_views": views,
    }


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------


def apply_as_creator(ctx, auth: Auth, payload: dict) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)

    try:
        category = clean_category(payload.get("category"))
        pitch = clean_text(payload.get("pitch"), field="pitch", required=True, min_length=MIN_PITCH,
                           max_length=MAX_PITCH, label="Your pitch")
        desired = payload.get("desired_handle")
        handle = clean_handle(desired, field="desired_handle") if desired else None
        wallets = clean_wallet_map(payload.get("wallets"))
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    with ctx.db.transaction() as conn:
        if page_for_user(conn, auth.user_id) is not None:
            raise conflict("You already have a creator page.", code="already_creator")
        pending = conn.execute(
            "SELECT * FROM creator_applications WHERE user_id = ? AND status = 'pending'",
            (auth.user_id,),
        ).fetchone()
        if pending is not None:
            raise conflict("You already have an application under review.", code="application_pending")
        if handle and page_by_handle(conn, handle) is not None:
            raise bad_request("That handle is already taken.", code="validation_error", field="desired_handle")
        created = now_iso()
        application_id = conn.execute(
            """
            INSERT INTO creator_applications (user_id, category, pitch, desired_handle, status, created_at,
                                              updated_at, wallets_json)
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (auth.user_id, category, pitch, handle, created, created,
             json.dumps(wallets, sort_keys=True) if wallets else None),
        ).lastrowid
        audit.record(conn, action="creator.application_submitted", actor_user_id=auth.user_id,
                     actor_role=auth.role, target_type="creator_application", target_id=application_id,
                     meta={"category": category, "wallet_assets": sorted(wallets)})
        row = conn.execute("SELECT * FROM creator_applications WHERE id = ?", (application_id,)).fetchone()
    return creator_application(row)


def my_application(ctx, auth: Auth) -> dict | None:
    with ctx.db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM creator_applications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (auth.user_id,),
        ).fetchone()
    return creator_application(row) if row else None


def _derive_handle(conn, display_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (display_name or "creator").lower()).strip("-")
    if not base or not base[0].isalpha():
        base = f"creator-{base}" if base else "creator"
    base = base[:24]
    candidate = base
    suffix = 1
    while page_by_handle(conn, candidate) is not None:
        suffix += 1
        candidate = f"{base[:24]}-{suffix}"
    return candidate


def create_page_for_user(conn, *, user_id: int, handle: str, page_name: str, category: str,
                         tagline: str | None = None, about: str | None = None) -> int:
    created = now_iso()
    page_id = conn.execute(
        """
        INSERT INTO creator_pages (user_id, handle, page_name, tagline, about, category, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (user_id, handle, page_name, tagline, about, category, created, created),
    ).lastrowid
    conn.execute(
        "UPDATE users SET role = CASE WHEN role = 'member' THEN 'creator' ELSE role END, updated_at = ? WHERE id = ?",
        (now_iso(), user_id),
    )
    return page_id


def review_application(ctx, admin: Auth, application_id: int, payload: dict) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    decision = (payload.get("decision") or "").strip().lower()
    if decision not in ("approve", "reject"):
        raise bad_request("Decision must be approve or reject.", code="validation_error", field="decision")
    try:
        note = clean_text(payload.get("note"), field="note", max_length=500) or None
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM creator_applications WHERE id = ?", (application_id,)).fetchone()
        if row is None:
            raise not_found("Application not found.")
        application = dict(row)
        if application["status"] != "pending":
            raise conflict("That application was already reviewed.", code="already_reviewed")

        status = "approved" if decision == "approve" else "rejected"
        handle = None
        if decision == "approve":
            candidate = payload.get("handle") or application.get("desired_handle")
            if candidate:
                try:
                    handle = clean_handle(candidate)
                except ValidationError as exc:
                    raise bad_request(exc.message, code="validation_error", field=exc.field) from None
                if page_by_handle(conn, handle) is not None:
                    raise conflict("That handle is already taken.", code="handle_taken", field="handle")
            else:
                user_row = conn.execute("SELECT * FROM users WHERE id = ?", (application["user_id"],)).fetchone()
                handle = _derive_handle(conn, dict(user_row)["display_name"])

        conn.execute(
            "UPDATE creator_applications SET status = ?, decision_note = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ? "
            "WHERE id = ?",
            (status, note, admin.user_id, now_iso(), now_iso(), application_id),
        )
        if decision == "approve":
            user_row = conn.execute("SELECT * FROM users WHERE id = ?", (application["user_id"],)).fetchone()
            user = dict(user_row)
            existing_page = page_for_user(conn, user["id"])
            if existing_page is None:
                page_id = create_page_for_user(
                    conn,
                    user_id=user["id"],
                    handle=handle,
                    page_name=user["display_name"],
                    category=application["category"],
                )
            else:
                page_id = existing_page["id"]
                handle = existing_page["handle"]
            # Wallets entered with the application become the creator's wallets.
            for asset_key, address in _application_wallets(application).items():
                parsed = validate_address(asset_key, address)
                _store_wallet(conn, page_id, asset_key, parsed, user["id"])
        audit.record(conn, action=f"admin.application_{status}", actor_user_id=admin.user_id,
                     actor_role="admin", target_type="creator_application", target_id=application_id,
                     meta={"handle": handle, "note_present": bool(note)})
        updated = conn.execute("SELECT * FROM creator_applications WHERE id = ?", (application_id,)).fetchone()
    result = creator_application(updated)
    result["handle"] = handle
    return result


def applications_for(conn, status: str | None = "pending"):
    if status:
        return conn.execute(
            """
            SELECT a.*, u.display_name, u.email, u.status AS user_status, u.email_verified
            FROM creator_applications a JOIN users u ON u.id = a.user_id
            WHERE a.status = ? ORDER BY a.created_at ASC LIMIT 200
            """,
            (status,),
        ).fetchall()
    return conn.execute(
        """
        SELECT a.*, u.display_name, u.email, u.status AS user_status, u.email_verified
        FROM creator_applications a JOIN users u ON u.id = a.user_id
        ORDER BY a.created_at DESC LIMIT 200
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# Page management
# ---------------------------------------------------------------------------


def update_page(ctx, auth: Auth, payload: dict) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    try:
        fields: dict[str, object] = {}
        if "page_name" in payload:
            fields["page_name"] = clean_text(payload.get("page_name"), field="page_name", required=True,
                                            min_length=2, max_length=60, allow_newlines=False,
                                            label="Page name")
        if "tagline" in payload:
            fields["tagline"] = clean_text(payload.get("tagline") or "", field="tagline",
                                          max_length=MAX_TAGLINE, allow_newlines=False) or None
        if "about" in payload:
            fields["about"] = clean_text(payload.get("about") or "", field="about", max_length=MAX_ABOUT) or None
        if "category" in payload:
            fields["category"] = clean_category(payload.get("category"))
        if "status" in payload:
            status = payload.get("status")
            if status not in ("active", "paused"):
                raise ValidationError("A page can be active or paused.", "status")
            fields["status"] = status
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    if not fields:
        raise bad_request("There is nothing to update.", code="validation_error")

    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        assignments = ", ".join(f"{column} = ?" for column in fields)
        conn.execute(
            f"UPDATE creator_pages SET {assignments}, updated_at = ? WHERE id = ?",  # noqa: S608
            (*fields.values(), now_iso(), page["id"]),
        )
        audit.record(conn, action="creator.page_updated", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="creator_page", target_id=page["id"], meta={"fields": sorted(fields.keys())})
        updated = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (page["id"],)).fetchone()
    return _owner_page_payload(updated)


def _owner_page_payload(page_row) -> dict:
    page = dict(page_row)
    return {
        "id": page["id"],
        "handle": page["handle"],
        "page_name": page["page_name"],
        "tagline": page.get("tagline"),
        "about": page.get("about"),
        "category": page["category"],
        "status": page["status"],
        "created_at": page["created_at"],
        "updated_at": page["updated_at"],
        "url": f"/c/{page['handle']}",
    }


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------


def upsert_tier(ctx, auth: Auth, payload: dict, tier_id: int | None = None) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    try:
        name = clean_text(payload.get("name"), field="name", required=True, max_length=MAX_TIER_NAME,
                          allow_newlines=False, label="Tier name")
        description = clean_text(payload.get("description") or "", field="description",
                                 max_length=MAX_TIER_DESCRIPTION) or None
        price_cents = price_to_cents(payload.get("price", payload.get("price_cents")))
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        if tier_id is None:
            position_row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) AS p FROM tiers WHERE creator_id = ?", (page["id"],)
            ).fetchone()
            position = int(position_row["p"]) + 1
            created = now_iso()
            tier_id = conn.execute(
                """
                INSERT INTO tiers (creator_id, name, description, price_cents, position, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (page["id"], name, description, price_cents, position, created, created),
            ).lastrowid
            action = "creator.tier_created"
        else:
            existing = conn.execute("SELECT * FROM tiers WHERE id = ? AND creator_id = ?",
                                    (tier_id, page["id"])).fetchone()
            if existing is None:
                raise not_found("Tier not found.")
            conn.execute(
                "UPDATE tiers SET name = ?, description = ?, price_cents = ?, updated_at = ? WHERE id = ?",
                (name, description, price_cents, now_iso(), tier_id),
            )
            action = "creator.tier_updated"
        audit.record(conn, action=action, actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="tier", target_id=tier_id, meta={"price_cents": price_cents})
        row = conn.execute("SELECT * FROM tiers WHERE id = ?", (tier_id,)).fetchone()
    return tier_public(row)


def set_tier_active(ctx, auth: Auth, tier_id: int, active: bool) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        tier = conn.execute("SELECT * FROM tiers WHERE id = ? AND creator_id = ?",
                            (tier_id, page["id"])).fetchone()
        if tier is None:
            raise not_found("Tier not found.")
        conn.execute(
            "UPDATE tiers SET is_active = ?, archived_at = ?, updated_at = ? WHERE id = ?",
            (1 if active else 0, None if active else now_iso(), now_iso(), tier_id),
        )
        audit.record(conn, action="creator.tier_reactivated" if active else "creator.tier_paused",
                     actor_user_id=auth.user_id, actor_role=auth.role, target_type="tier", target_id=tier_id)
        updated = conn.execute("SELECT * FROM tiers WHERE id = ?", (tier_id,)).fetchone()
    payload = tier_public(updated)
    if not active:
        payload["notice"] = (
            "Archiving a tier stops new purchases. Existing members keep the access they already paid for."
        )
    return payload


# ---------------------------------------------------------------------------
# Wallet addresses
# ---------------------------------------------------------------------------

WALLET_NOTICE = (
    "Each address is a receiving destination you control for that coin or token. Saving one only "
    "records it — Velora does not send funds, split payments automatically, or verify wallet "
    "ownership. Never enter a recovery phrase or private key."
)


def clean_wallet_map(value) -> dict[str, str]:
    """Validate an optional ``{asset: address}`` object (used when applying)."""
    if value in (None, "", {}):
        return {}
    if not isinstance(value, dict):
        raise ValidationError("Wallet addresses must be sent as a list of coin and address pairs.", "wallets")
    if len(value) > len(CATALOG):
        raise ValidationError("Too many wallet addresses.", "wallets")
    cleaned: dict[str, str] = {}
    for asset_key, address in value.items():
        if address in (None, ""):
            continue
        if get_asset(asset_key) is None:
            raise ValidationError("Choose a supported coin or token.", "wallets")
        parsed = validate_address(asset_key, address, field=f"wallets.{asset_key}")
        cleaned[asset_key] = parsed["address"]
    return cleaned


def _application_wallets(application: dict) -> dict[str, str]:
    raw = application.get("wallets_json")
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except ValueError:
        return {}
    return {k: v for k, v in loaded.items() if k in ASSETS and isinstance(v, str)} if isinstance(loaded, dict) else {}


def _store_wallet(conn, creator_id: int, asset_key: str, parsed: dict, user_id: int | None) -> None:
    conn.execute(
        """
        INSERT INTO creator_wallets (creator_id, asset, address, address_kind, saved_at, saved_by)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(creator_id, asset) DO UPDATE SET
          address = excluded.address,
          address_kind = excluded.address_kind,
          saved_at = excluded.saved_at,
          saved_by = excluded.saved_by
        """,
        (creator_id, asset_key, parsed["address"], parsed["kind"], now_iso(), user_id),
    )


def wallets_for(conn, creator_id: int) -> list[dict]:
    """The creator's wallets in catalog order."""
    rows = conn.execute("SELECT * FROM creator_wallets WHERE creator_id = ?", (creator_id,)).fetchall()
    by_asset = {row["asset"]: dict(row) for row in rows}
    return [by_asset[asset.key] for asset in CATALOG if asset.key in by_asset]


def wallet_for(conn, creator_id: int, asset_key: str):
    return conn.execute("SELECT * FROM creator_wallets WHERE creator_id = ? AND asset = ?",
                        (creator_id, asset_key)).fetchone()


def wallets_view(creator_id: int, wallets: list[dict]) -> dict:
    """Owner/admin view of a creator's wallets.

    ``btc_address`` / ``address_kind`` / ``saved_at`` keep the original
    single-address response shape working for older clients.
    """
    items = []
    for wallet in wallets:
        asset = ASSETS.get(wallet["asset"])
        if asset is None:
            continue
        items.append({
            "asset": asset.public(),
            "address": wallet["address"],
            "address_kind": wallet["address_kind"],
            "saved_at": wallet["saved_at"],
        })
    btc = next((w for w in wallets if w["asset"] == "btc"), None)
    return {
        "creator_id": creator_id,
        "wallets": items,
        "supported_assets": [asset.public() for asset in CATALOG],
        "btc_address": btc["address"] if btc else None,
        "address_kind": btc["address_kind"] if btc else None,
        "saved_at": btc["saved_at"] if btc else None,
        "notice": (
            WALLET_NOTICE if items else
            "No wallet address is on file. Add one for each coin or token you want to accept so members "
            "can check out and the operator can review payouts. Velora never sends funds automatically."
        ),
    }


def save_wallet(ctx, auth: Auth, asset_key, address, *, field: str = "address") -> dict:
    """Record (or replace) the receiving address for one asset."""
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    try:
        parsed = validate_address(asset_key, address, field=field)
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        _store_wallet(conn, page["id"], asset_key, parsed, auth.user_id)
        audit.record(conn, action="creator.wallet_saved", actor_user_id=auth.user_id,
                     actor_role=auth.role, target_type="creator_page", target_id=page["id"],
                     meta={"asset": asset_key, "address_kind": parsed["kind"]})
        wallets = wallets_for(conn, page["id"])
    return wallets_view(page["id"], wallets)


def remove_wallet(ctx, auth: Auth, asset_key) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("studio_write", auth.user_id)
    if get_asset(asset_key) is None:
        raise not_found("That coin or token is not supported.")
    with ctx.db.transaction() as conn:
        page = require_page(conn, auth)
        removed = conn.execute("DELETE FROM creator_wallets WHERE creator_id = ? AND asset = ?",
                               (page["id"], asset_key)).rowcount
        if not removed:
            raise not_found("No wallet is saved for that coin or token.")
        audit.record(conn, action="creator.wallet_removed", actor_user_id=auth.user_id,
                     actor_role=auth.role, target_type="creator_page", target_id=page["id"],
                     meta={"asset": asset_key})
        wallets = wallets_for(conn, page["id"])
    return wallets_view(page["id"], wallets)


def save_payout_address(ctx, auth: Auth, payload: dict) -> dict:
    """Original single-address call: records the creator's Bitcoin wallet."""
    return save_wallet(ctx, auth, "btc", payload.get("btc_address"), field="btc_address")


def payout_for_actor(ctx, actor: Auth, creator_id: int) -> dict:
    """Wallet read guarded by ownership or administrator role."""
    with ctx.db.connection() as conn:
        page = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (creator_id,)).fetchone()
        if page is None:
            raise not_found("Creator page not found.")
        page = dict(page)
        if not actor.is_admin and page["user_id"] != actor.user_id:
            raise forbidden(
                "Wallet addresses are private to the creator and administrators.",
                code="payout_private",
            )
        wallets = wallets_for(conn, creator_id)
    return wallets_view(creator_id, wallets)


# ---------------------------------------------------------------------------
# Studio
# ---------------------------------------------------------------------------


def studio_overview(ctx, auth: Auth) -> dict:
    require_verified(auth)
    with ctx.db.connection() as conn:
        page = require_page(conn, auth)
        page = dict(page)
        tiers = tiers_for(conn, page["id"], include_inactive=True)
        stats = creator_stats(conn, page["id"])

        earnings = conn.execute(
            """
            SELECT
              COALESCE(SUM(amount_cents), 0)       AS gross_cents,
              COALESCE(SUM(platform_fee_cents), 0) AS fee_cents,
              COALESCE(SUM(creator_net_cents), 0)  AS net_cents,
              COUNT(*)                             AS invoice_count
            FROM invoices WHERE creator_id = ?
            """,
            (page["id"],),
        ).fetchone()
        recent_30 = conn.execute(
            """
            SELECT COALESCE(SUM(creator_net_cents), 0) AS net_cents, COUNT(*) AS invoice_count
            FROM invoices WHERE creator_id = ? AND settled_at > ?
            """,
            (page["id"], _days_ago(30)),
        ).fetchone()

        tier_members = {
            row["tier_id"]: row["c"]
            for row in conn.execute(
                """
                SELECT tier_id, COUNT(*) AS c FROM memberships
                WHERE creator_id = ? AND status = 'active' AND ends_at > ?
                GROUP BY tier_id
                """,
                (page["id"], now_iso()),
            )
        }
        pending = conn.execute(
            """
            SELECT COUNT(*) AS c FROM payment_intents
            WHERE creator_id = ? AND status IN ('pending','processing','held')
            """,
            (page["id"],),
        ).fetchone()["c"]
        wallet_rows = wallets_for(conn, page["id"])
        recent_posts = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, title, status, visibility, published_at, updated_at
                FROM posts WHERE creator_id = ? ORDER BY updated_at DESC LIMIT 5
                """,
                (page["id"],),
            )
        ]

    tiers_payload = []
    for tier in tiers:
        item = tier_public(tier)
        item["active_members"] = int(tier_members.get(tier["id"], 0))
        tiers_payload.append(item)

    return {
        "page": _owner_page_payload(page),
        "stats": {
            **stats,
            "gross_cents": int(earnings["gross_cents"]),
            "platform_fee_cents": int(earnings["fee_cents"]),
            "net_cents": int(earnings["net_cents"]),
            "settled_invoice_count": int(earnings["invoice_count"]),
            "net_last_30_days_cents": int(recent_30["net_cents"]),
            "invoices_last_30_days": int(recent_30["invoice_count"]),
            "open_payment_attempts": int(pending),
        },
        "tiers": tiers_payload,
        "recent_posts": recent_posts,
        "payout": wallets_view(page["id"], wallet_rows),
    }


def _days_ago(days: int) -> str:
    from ..db import future_iso

    return future_iso(-days * 86400)


def creator_members(ctx, auth: Auth, *, limit: int = 200) -> dict:
    """Members of the authenticated creator's page.

    Member email addresses are deliberately absent: creators see display names,
    tier, and membership window only.
    """
    require_verified(auth)
    with ctx.db.connection() as conn:
        page = require_page(conn, auth)
        rows = conn.execute(
            """
            SELECT m.id, m.status, m.started_at, m.ends_at, m.cancel_requested_at,
                   u.display_name AS member_name, u.id AS member_id,
                   t.name AS tier_name, t.id AS tier_id
            FROM memberships m
            JOIN users u ON u.id = m.user_id
            JOIN tiers t ON t.id = m.tier_id
            WHERE m.creator_id = ?
            ORDER BY (m.status = 'active' AND m.ends_at > ?) DESC, m.created_at DESC
            LIMIT ?
            """,
            (page["id"], now_iso(), max(1, min(500, limit))),
        ).fetchall()
    return {
        "items": [
            {
                "membership_id": row["id"],
                "member_id": row["member_id"],
                "member_name": row["member_name"],
                "tier_id": row["tier_id"],
                "tier_name": row["tier_name"],
                "status": row["status"],
                "active": row["status"] == "active" and row["ends_at"] > now_iso(),
                "started_at": row["started_at"],
                "ends_at": row["ends_at"],
                "cancel_requested_at": row["cancel_requested_at"],
            }
            for row in rows
        ],
        "privacy_note": "Member email addresses are never shared with creators.",
    }


# ---------------------------------------------------------------------------
# Public discovery
# ---------------------------------------------------------------------------


def categories() -> list[dict]:
    return [{"key": key, "label": label} for key, label in CATEGORIES]


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def discover(conn, *, query: str | None = None, category: str | None = None,
             sort: str = "recent", page: int = 1, per_page: int = 12) -> dict:
    clauses = ["p.status = 'active'", "u.status = 'active'"]
    params: list = []
    if category and category in CATEGORY_KEYS:
        clauses.append("p.category = ?")
        params.append(category)
    if query:
        text = clean_text(query, field="q", max_length=60, allow_newlines=False)
        if text:
            pattern = _like_pattern(text)
            clauses.append(
                "(p.page_name LIKE ? ESCAPE '\\' OR p.handle LIKE ? ESCAPE '\\' "
                "OR COALESCE(p.tagline, '') LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern, pattern])

    where = " AND ".join(clauses)
    order = {
        "recent": "p.created_at DESC, p.id DESC",
        "members": "active_members DESC, p.created_at DESC",
        "price": "lowest_price DESC, p.created_at DESC",
        "name": "p.page_name COLLATE NOCASE ASC",
    }.get(sort, "p.created_at DESC, p.id DESC")

    base_sql = f"""
        SELECT p.*, u.display_name AS owner_name, u.created_at AS owner_created_at,
               u.orientation_value, u.orientation_visibility,
               (SELECT COUNT(*) FROM memberships m
                 WHERE m.creator_id = p.id AND m.status = 'active' AND m.ends_at > ?) AS active_members,
               (SELECT COUNT(*) FROM posts po
                 WHERE po.creator_id = p.id AND po.status = 'published' AND po.admin_archived = 0) AS published_posts,
               (SELECT MIN(price_cents) FROM tiers t
                 WHERE t.creator_id = p.id AND t.is_active = 1) AS lowest_price
        FROM creator_pages p JOIN users u ON u.id = p.user_id
        WHERE {where}
        ORDER BY {order}
    """  # noqa: S608 - clauses are static, values are bound
    rows = conn.execute(base_sql, [now_iso(), *params]).fetchall()

    total = len(rows)
    per_page = max(1, min(48, per_page))
    page = max(1, page)
    start = (page - 1) * per_page
    window = rows[start:start + per_page]

    items = []
    for row in window:
        record = dict(row)
        owner = {
            "id": None,
            "display_name": record["owner_name"],
            "created_at": record["owner_created_at"],
            "orientation_value": record.get("orientation_value"),
            "orientation_visibility": record.get("orientation_visibility"),
        }
        tiers = tiers_for(conn, record["id"])[:3]
        items.append(
            creator_card(
                record,
                tiers=[tier_public(tier) for tier in tiers],
                stats={
                    "active_members": int(record["active_members"] or 0),
                    "published_posts": int(record["published_posts"] or 0),
                },
                owner_row=owner,
            )
        )
    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "has_more": start + per_page < total,
        "query": query or "",
        "category": category or "",
        "sort": sort,
    }


def public_page(ctx, handle: str, viewer: Auth | None) -> dict:
    from .posts import creator_feed  # local import avoids a circular import

    with ctx.db.connection() as conn:
        page = page_by_handle(conn, handle)
        if page is None:
            raise not_found("That creator page does not exist.")
        page = dict(page)
        owner_row = conn.execute("SELECT * FROM users WHERE id = ?", (page["user_id"],)).fetchone()
        if owner_row is None:
            raise not_found("That creator page does not exist.")
        owner = dict(owner_row)

        viewer_membership = None
        if viewer is not None:
            from .payments import membership_for

            membership = membership_for(conn, viewer.user_id, page["id"])
            if membership:
                membership = dict(membership)
                viewer_membership = {
                    "id": membership["id"],
                    "tier_id": membership["tier_id"],
                    "status": membership["status"],
                    "ends_at": membership["ends_at"],
                    "active": membership["status"] == "active" and membership["ends_at"] > now_iso(),
                    "cancel_requested_at": membership.get("cancel_requested_at"),
                }

        available = page["status"] == "active" and owner["status"] == "active"
        tiers = tiers_for(conn, page["id"]) if available else []
        feed = creator_feed(ctx, conn, page, viewer, limit=12) if available else {"items": [], "total": 0}
        stats = creator_stats(conn, page["id"]) if available else None
        view_count = conn.execute(
            "SELECT COALESCE(SUM(view_count), 0) AS c FROM creator_page_views WHERE creator_id = ?",
            (page["id"],),
        ).fetchone()["c"]

    payload = creator_page(
        page,
        owner_row=owner if available else {"id": None, "display_name": page["page_name"],
                                           "created_at": page["created_at"]},
        tiers=[tier_public(tier) for tier in tiers],
        about_visible=available,
        stats={
            "active_members": int(stats["active_members"]) if stats else 0,
            "published_posts": int(stats["published_posts"]) if stats else 0,
            "page_views": int(view_count or 0),
        },
        membership=viewer_membership,
    )
    payload["available"] = available
    payload["unavailable_notice"] = (
        None if available else "This creator paused their page. Their posts and tiers are hidden for now."
    )
    payload["posts"] = feed["items"]
    payload["post_count"] = feed.get("total", 0)
    payload["disclosure"] = (
        "This page shows only what the creator chose to make public. "
        "Sexual orientation is never used to filter discovery, and it only appears here if the "
        "creator opted in."
    )
    return payload


def record_page_view(ctx, creator_id: int) -> None:
    """Daily aggregate counter only — no identity, no cookies, no per-visitor rows."""
    from ..db import utcnow

    day = utcnow().strftime("%Y-%m-%d")
    with ctx.db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO creator_page_views (creator_id, day, view_count) VALUES (?, ?, 1)
            ON CONFLICT(creator_id, day) DO UPDATE SET view_count = view_count + 1
            """,
            (creator_id, day),
        )


def mask(member_email: str) -> str:  # pragma: no cover - helper used by admin views
    return mask_email(member_email)
