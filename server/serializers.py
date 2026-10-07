"""Output serialisers.

Two hard rules:

1. A private value never enters a public serializer. Optional orientation is only
   emitted on a public creator page when the owner explicitly opted in and the
   value is on the eligible list.
2. Locked content is described, never embedded. A members-only post that the
   viewer cannot access returns its teaser and metadata only — no body, no media
   bytes, not even media identifiers.

Member email addresses are never included in a payload consumed by a creator or
another member; only the account owner and authorized admins see them.
"""

from __future__ import annotations

from .validation import ORIENTATION_PREFER_NOT_TO_SAY, orientation_public_eligible

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def user_private(user_row) -> dict:
    """The account owner's own view. Includes the private orientation value."""
    user = dict(user_row)
    return {
        "id": user["id"],
        "display_name": user["display_name"],
        "email": user["email"],
        "role": user["role"],
        "status": user["status"],
        "email_verified": bool(user["email_verified"]),
        "email_verified_at": user["email_verified_at"],
        "adult_attested_at": user["adult_attested_at"],
        "bio": user.get("bio"),
        "created_at": user["created_at"],
        "orientation": {
            "value": _orientation_value(user),
            "self_described": user.get("orientation_self_text"),
            "visibility": user.get("orientation_visibility") or "private",
            "public_eligible": orientation_public_eligible(user.get("orientation_value")),
            "note": (
                "Sexual orientation is optional and private by default. It is never used to "
                "filter discovery and is only shown on your public creator page if you choose to "
                'share it. "Prefer not to say" is never displayed publicly.'
            ),
        },
    }


def _orientation_value(user: dict) -> str | None:
    value = user.get("orientation_value")
    if value == ORIENTATION_PREFER_NOT_TO_SAY:
        return ORIENTATION_PREFER_NOT_TO_SAY
    if value:
        return value
    return None


def user_public_identity(user_row) -> dict:
    """Anything any visitor may see about an account."""
    user = dict(user_row)
    return {
        "id": user["id"],
        "display_name": user["display_name"],
        "created_at": user["created_at"],
    }


def orientation_for_public_page(user_row) -> dict | None:
    """Optional orientation, public page only, opt-in only.

    Returns ``None`` unless the owner explicitly chose public visibility *and*
    the stored value is on the eligible list. ``prefer_not_to_say`` and free-text
    self-descriptions are never emitted here.
    """
    user = dict(user_row)
    if (user.get("orientation_visibility") or "private") != "public":
        return None
    value = user.get("orientation_value")
    if not orientation_public_eligible(value):
        return None
    return {"value": value, "label": label_for_orientation(value)}


ORIENTATION_LABELS = {
    "straight": "Straight",
    "gay": "Gay",
    "lesbian": "Lesbian",
    "bisexual": "Bisexual",
    "pansexual": "Pansexual",
    "asexual": "Asexual",
    "queer": "Queer",
    "questioning": "Questioning",
    "other": "Another identity",
    ORIENTATION_PREFER_NOT_TO_SAY: "Prefer not to say",
}


def label_for_orientation(value: str | None) -> str | None:
    if value is None:
        return None
    return ORIENTATION_LABELS.get(value, "Self-described")


# ---------------------------------------------------------------------------
# Creators and tiers
# ---------------------------------------------------------------------------


def creator_card(page_row, *, tiers: list[dict] | None = None, stats: dict | None = None,
                 owner_row=None, membership: dict | None = None) -> dict:
    page = dict(page_row)
    card = {
        "id": page["id"],
        "handle": page["handle"],
        "page_name": page["page_name"],
        "tagline": page.get("tagline"),
        "category": page["category"],
        "status": page["status"],
        "created_at": page["created_at"],
        "updated_at": page["updated_at"],
        "url": f"/c/{page['handle']}",
        "monogram": monogram(page["page_name"]),
    }
    if owner_row is not None:
        card["owner"] = user_public_identity(owner_row)
        orientation = orientation_for_public_page(owner_row)
        if orientation:
            card["owner_orientation"] = orientation
    if tiers is not None:
        card["tiers"] = tiers
        prices = [tier["price_cents"] for tier in tiers if tier.get("is_active")]
        card["lowest_price_cents"] = min(prices) if prices else None
        card["active_tier_count"] = len(prices)
    if stats is not None:
        card["stats"] = stats
    if membership is not None:
        card["viewer_membership"] = membership
    return card


def creator_page(page_row, *, owner_row, tiers: list[dict], about_visible: bool = True,
                 stats: dict | None = None, membership: dict | None = None) -> dict:
    page = dict(page_row)
    payload = creator_card(page, tiers=tiers, stats=stats, owner_row=owner_row, membership=membership)
    payload["about"] = page.get("about") if about_visible else None
    return payload


def tier_public(row) -> dict:
    tier = dict(row)
    return {
        "id": tier["id"],
        "creator_id": tier["creator_id"],
        "name": tier["name"],
        "description": tier.get("description"),
        "price_cents": tier["price_cents"],
        "price_display": format_cents(tier["price_cents"]),
        "period_days": 30,
        "is_active": bool(tier["is_active"]),
        "position": tier["position"],
        "created_at": tier["created_at"],
    }


def creator_payout(row) -> dict:
    """Payout destination view for the owner and authorized admins only."""
    payout = dict(row)
    return {
        "creator_id": payout["creator_id"],
        "btc_address": payout["btc_address"],
        "address_kind": payout["address_kind"],
        "saved_at": payout["saved_at"],
        "notice": (
            "This is an on-chain BTC receiving address you control. Saving it only records a "
            "destination — Velora does not send funds, split payments automatically, or verify "
            "wallet ownership."
        ),
    }


def creator_application(row) -> dict:
    application = dict(row)
    return {
        "id": application["id"],
        "user_id": application["user_id"],
        "category": application["category"],
        "pitch": application["pitch"],
        "desired_handle": application.get("desired_handle"),
        "status": application["status"],
        "decision_note": application.get("decision_note"),
        "reviewed_at": application.get("reviewed_at"),
        "created_at": application["created_at"],
        "updated_at": application["updated_at"],
    }


# ---------------------------------------------------------------------------
# Posts and media
# ---------------------------------------------------------------------------


def post_for_owner(row, media: list[dict] | None = None, tier_ids: list[int] | None = None) -> dict:
    post = dict(row)
    return {
        "id": post["id"],
        "creator_id": post["creator_id"],
        "title": post["title"],
        "teaser": post["teaser"],
        "body": post["body"],
        "visibility": post["visibility"],
        "status": post["status"],
        "published_at": post.get("published_at"),
        "created_at": post["created_at"],
        "updated_at": post["updated_at"],
        "admin_archived": bool(post.get("admin_archived")),
        "media": media or [],
        "tier_ids": tier_ids or [],
        "locked": False,
        "access": "owner",
    }


def post_for_viewer(row, *, locked: bool, media: list[dict] | None = None,
                    access: str = "public", tier_ids: list[int] | None = None) -> dict:
    """A post as a viewer may see it.

    When ``locked`` is true the body is omitted, media identifiers are omitted and
    only the teaser plus metadata are returned, so an unauthorized client never
    receives private content or a pointer to the private media bytes.
    """
    post = dict(row)
    payload = {
        "id": post["id"],
        "creator_id": post["creator_id"],
        "title": post["title"],
        "teaser": post["teaser"],
        "visibility": post["visibility"],
        "status": post["status"],
        "published_at": post.get("published_at"),
        "created_at": post["created_at"],
        "updated_at": post["updated_at"],
        "locked": locked,
        "access": access,
        "teaser_notice": (
            "This is a public teaser. The full post is available to members supporting this creator."
            if locked else None
        ),
    }
    if locked:
        # Deliberately absent: body, media list, media ids, media counts by id.
        payload["body"] = None
        payload["media"] = []
        payload["locked_media_count"] = None
        payload["tier_ids"] = []
    else:
        payload["body"] = post["body"]
        payload["media"] = media or []
        payload["tier_ids"] = tier_ids or []
    return payload


def media_public(row) -> dict:
    media = dict(row)
    return {
        "id": media["id"],
        "post_id": media["post_id"],
        "content_type": media["content_type"],
        "byte_size": media["byte_size"],
        "position": media["position"],
        "url": f"/api/posts/{media['post_id']}/media/{media['id']}",
        "original_name": media.get("original_name"),
    }


# ---------------------------------------------------------------------------
# Membership, payments, ledger
# ---------------------------------------------------------------------------


def membership_public(row, *, creator=None, tier=None) -> dict:
    membership = dict(row)
    payload = {
        "id": membership["id"],
        "creator_id": membership["creator_id"],
        "tier_id": membership["tier_id"],
        "status": membership["status"],
        "started_at": membership["started_at"],
        "ends_at": membership["ends_at"],
        "cancel_requested_at": membership.get("cancel_requested_at"),
        "auto_renew": False,
        "auto_renew_note": (
            "Velora never schedules an automatic charge. Renewing is a manual action you take "
            "when you choose to."
        ),
        "created_at": membership["created_at"],
    }
    if creator is not None:
        payload["creator"] = creator
    if tier is not None:
        payload["tier"] = tier
    return payload


# Statuses that must never be described as paid.
INTENT_LABELS = {
    "pending": "Awaiting payment (not paid)",
    "processing": "Payment detected, waiting for confirmations (not yet unlocked)",
    "settled": "Payment settled — access granted",
    "held": "Held for review — access not granted",
    "expired": "Checkout expired (not paid)",
    "cancelled": "Cancelled (not paid)",
    "archived": "Archived attempt (not paid)",
}


def payment_intent_public(row) -> dict:
    intent = dict(row)
    status = intent["status"]
    payload = {
        "id": intent["id"],
        "order_ref": intent["order_ref"],
        "creator_id": intent["creator_id"],
        "tier_id": intent["tier_id"],
        "amount_cents": intent["amount_cents"],
        "amount_display": format_cents(intent["amount_cents"]),
        "currency": intent["currency"],
        "period_days": intent["period_days"],
        "status": status,
        "status_label": INTENT_LABELS.get(status, status),
        "paid": status == "settled",
        "access_granted": status == "settled",
        "hold_reason": intent.get("hold_reason"),
        "btc_amount_sats": intent.get("btc_invoice_sats"),
        "btc_rate_usd": intent.get("btc_rate_usd"),
        "checkout_url": intent.get("checkout_url") if status in ("pending", "processing") else None,
        "created_at": intent["created_at"],
        "updated_at": intent["updated_at"],
        "expires_at": intent["expires_at"],
        "settled_at": intent.get("settled_at"),
        "notice": (
            "A pending invoice is not a paid membership. Access is granted only after BTCPay "
            "reports the on-chain BTC invoice settled and Velora verifies it independently."
        ),
    }
    return payload


def invoice_public(row) -> dict:
    invoice = dict(row)
    return {
        "id": invoice["id"],
        "order_ref": invoice["order_ref"],
        "creator_id": invoice["creator_id"],
        "tier_id": invoice["tier_id"],
        "amount_cents": invoice["amount_cents"],
        "amount_display": format_cents(invoice["amount_cents"]),
        "platform_fee_cents": invoice["platform_fee_cents"],
        "creator_net_cents": invoice["creator_net_cents"],
        "fee_percent": invoice["fee_percent"],
        "btc_amount_sats": invoice["btc_amount_sats"],
        "btc_rate_usd": invoice.get("btc_rate_usd"),
        "status": invoice["status"],
        "settled_at": invoice["settled_at"],
        "recorded_at": invoice["recorded_at"],
    }


def ledger_entry_public(row) -> dict:
    entry = dict(row)
    return {
        "id": entry["id"],
        "invoice_id": entry.get("invoice_id"),
        "order_ref": entry.get("order_ref"),
        "entry_type": entry["entry_type"],
        "account": entry["account"],
        "direction": entry["direction"],
        "amount_cents": entry["amount_cents"],
        "amount_display": format_cents(entry["amount_cents"]),
        "memo": entry.get("memo"),
        "actor_kind": entry["actor_kind"],
        "created_at": entry["created_at"],
    }


# ---------------------------------------------------------------------------
# Messaging and reports
# ---------------------------------------------------------------------------


def message_public(row) -> dict:
    message = dict(row)
    return {
        "id": message["id"],
        "thread_id": message["thread_id"],
        "sender_id": message["sender_id"],
        "body": message["body"],
        "created_at": message["created_at"],
        "read_at": message.get("read_at"),
        "removed": bool(message.get("removed_at")),
    }


def thread_public(row, *, counterpart: dict | None = None, unread: int = 0,
                  membership_state: str | None = None) -> dict:
    thread = dict(row)
    payload = {
        "id": thread["id"],
        "member_id": thread["member_id"],
        "creator_id": thread["creator_id"],
        "created_at": thread["created_at"],
        "last_message_at": thread["last_message_at"],
        "archived": bool(thread.get("archived_at")),
        "unread_count": unread,
    }
    if counterpart is not None:
        payload["counterpart"] = counterpart
    if membership_state is not None:
        payload["membership_state"] = membership_state
    return payload


def report_public(row, *, include_reporter: bool = False) -> dict:
    report = dict(row)
    payload = {
        "id": report["id"],
        "target_type": report["target_type"],
        "target_id": report["target_id"],
        "target_label": report.get("target_label"),
        "reason_code": report["reason_code"],
        "details": report.get("details"),
        "status": report["status"],
        "resolution_note": report.get("resolution_note"),
        "created_at": report["created_at"],
        "updated_at": report["updated_at"],
        "handled_at": report.get("handled_at"),
    }
    if include_reporter:
        payload["reporter_user_id"] = report.get("reporter_user_id")
    return payload


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------


def admin_user_row(row) -> dict:
    user = dict(row)
    return {
        "id": user["id"],
        "display_name": user["display_name"],
        "email": user["email"],
        "role": user["role"],
        "status": user["status"],
        "email_verified": bool(user["email_verified"]),
        "created_at": user["created_at"],
        "archived_at": user.get("archived_at"),
        "anonymized_at": user.get("anonymized_at"),
        "creator_page_id": user.get("creator_page_id"),
        "handle": user.get("handle"),
    }


def admin_audit_row(row) -> dict:
    entry = dict(row)
    return {
        "id": entry["id"],
        "actor_user_id": entry.get("actor_user_id"),
        "actor_name": entry.get("actor_name"),
        "actor_role": entry.get("actor_role"),
        "action": entry["action"],
        "target_type": entry.get("target_type"),
        "target_id": entry.get("target_id"),
        "meta": entry.get("meta"),
        "created_at": entry["created_at"],
    }


def admin_note_row(row) -> dict:
    note = dict(row)
    return {
        "id": note["id"],
        "admin_user_id": note.get("admin_user_id"),
        "admin_name": note.get("admin_name"),
        "target_type": note["target_type"],
        "target_id": note["target_id"],
        "body": note["body"],
        "created_at": note["created_at"],
    }


def webhook_event_row(row) -> dict:
    event = dict(row)
    return {
        "id": event["id"],
        "provider": event["provider"],
        "delivery_id": event["delivery_id"],
        "event_type": event.get("event_type"),
        "invoice_id": event.get("invoice_id"),
        "order_ref": event.get("order_ref"),
        "signature_valid": bool(event["signature_valid"]),
        "outcome": event["outcome"],
        "detail": event.get("detail"),
        "received_at": event["received_at"],
    }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def format_cents(cents: int | None) -> str:
    if cents is None:
        return "—"
    return f"${cents / 100:,.2f}"


def monogram(value: str) -> str:
    parts = [part for part in str(value).strip().split() if part]
    if not parts:
        return "V"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def paginate(items: list, *, page: int, per_page: int) -> dict:
    total = len(items)
    start = max(0, (page - 1) * per_page)
    window = items[start:start + per_page]
    return {
        "items": window,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "has_more": start + per_page < total,
    }
