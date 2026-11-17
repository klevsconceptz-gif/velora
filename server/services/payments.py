"""On-chain crypto membership payments (BTCPay Server) and memberships.

The settlement pipeline
-----------------------
1. ``create_intent`` writes a *pending* payment intent with an opaque order
   reference and asks BTCPay for an invoice for the USD price, restricted to the
   one on-chain coin or token the member chose (and that the creator has a
   wallet for).
2. Access is granted only by :func:`grant_settlement`, which requires:
   a valid signed webhook **and** an independent ``GET /invoices/{id}`` from
   BTCPay, both agreeing on a settled, confirmed, fully paid, in-window invoice
   paid with the chosen payment method, whose order reference matches the intent.
3. A browser redirect is never evidence of payment. There is no code path that
   settles an invoice from a redirect, a query parameter, or a user request.
4. Settled invoices and ledger rows are append-only (enforced by SQL triggers as
   well as by this module). Replayed webhooks are idempotent.
"""

from __future__ import annotations

from .. import audit
from ..btcpay import (
    BtcPayClient,
    BtcPayError,
    BtcPayNotConfigured,
    Classification,
    NormalizedInvoice,
    classify_invoice,
    normalize_invoice,
    parse_webhook_signature,
)
from ..config import MEMBERSHIP_PERIOD_DAYS, PLATFORM_FEE_PERCENT
from ..db import future_iso, now_iso
from ..wallets import ASSETS, get_asset
from ..http import ApiError, bad_request, conflict, forbidden, not_found, unavailable
from ..security import constant_time_equal, hmac_hex, opaque_reference, sha256_hex
from ..serializers import INTENT_LABELS, invoice_public, membership_public, payment_intent_public, tier_public
from .accounts import Auth, require_active, require_verified
from .creators import tier_by_id, wallet_for, wallets_for

TERMINAL_STATUSES = ("settled", "cancelled", "archived")


# ---------------------------------------------------------------------------
# Availability and read helpers
# ---------------------------------------------------------------------------


METHOD_CACHE_SECONDS = 60


def checkout_availability(ctx) -> dict:
    """Explain, honestly, whether crypto checkout can be used right now."""
    if ctx.config.btcpay_configured:
        return {
            "available": True,
            "methods": list(ctx.config.offered_assets),
            "assets": [ASSETS[key].public() for key in ctx.config.offered_assets],
            "reason": None,
            "notice": (
                "Memberships are paid on-chain in a cryptocurrency or Tether token through the "
                "configured hosted BTCPay Server checkout. Each 30-day period is a separate payment "
                "and nothing renews automatically."
            ),
        }
    return {
        "available": False,
        "methods": [],
        "assets": [],
        "reason": "btcpay_not_configured",
        "notice": (
            "Crypto checkout is unavailable on this instance because BTCPay Server is not configured. "
            "No payment can be started, and Velora will never report a purchase as successful while "
            "checkout is unavailable."
        ),
    }


def require_checkout_available(ctx) -> None:
    if not ctx.config.btcpay_configured:
        raise unavailable(
            "Crypto checkout is unavailable because this Velora instance has no BTCPay Server "
            "configuration. Ask the operator to configure it before buying a membership. "
            "No payment has been started.",
            code="checkout_unavailable",
        )


def store_payment_methods(ctx) -> set[str]:
    """Payment-method ids enabled on the BTCPay store (cached for a minute).

    Raises :class:`BtcPayError` when BTCPay cannot be asked, so callers fail
    closed instead of offering coins the store may not accept.
    """
    cached = ctx.method_cache.get("store")
    if cached and ctx.now() - cached[0] < METHOD_CACHE_SECONDS:
        return cached[1]
    methods = BtcPayClient.from_config(ctx.config).enabled_payment_methods()
    ctx.method_cache["store"] = (ctx.now(), methods)
    return methods


def creator_methods(ctx, conn, creator_id: int) -> dict:
    """Which coins a member can use for this creator, and why others cannot.

    A coin is payable only when the creator has a wallet for it, the operator
    offers it, and the BTCPay store has the matching payment method enabled.
    """
    wallets = {w["asset"]: w for w in wallets_for(conn, creator_id)}
    enabled: set[str] | None = None
    store_error = None
    if wallets and ctx.config.btcpay_configured:
        try:
            enabled = store_payment_methods(ctx)
        except BtcPayError as exc:
            store_error = str(exc)
    options = []
    for key in wallets:
        asset = ASSETS[key]
        reason = None
        if key not in ctx.config.offered_assets:
            reason = "not_offered"
        elif enabled is None:
            reason = "store_unreachable" if store_error else "btcpay_not_configured"
        elif ctx.config.method_id(key) not in enabled:
            reason = "not_enabled_on_store"
        options.append({**asset.public(), "available": reason is None, "unavailable_reason": reason,
                        "payment_method": ctx.config.method_id(key)})
    return {"options": options, "store_error": store_error, "has_wallets": bool(wallets)}


def expire_memberships(ctx, conn=None) -> int:
    """Mark memberships whose 30-day period has ended. Access is never shortened."""
    from ..db import now_iso as _now

    if conn is not None:
        cursor = conn.execute(
            "UPDATE memberships SET status = 'expired', updated_at = ? WHERE status = 'active' AND ends_at <= ?",
            (_now(), _now()),
        )
        return cursor.rowcount or 0
    with ctx.db.transaction() as owned:
        cursor = owned.execute(
            "UPDATE memberships SET status = 'expired', updated_at = ? WHERE status = 'active' AND ends_at <= ?",
            (_now(), _now()),
        )
        return cursor.rowcount or 0


def membership_for(conn, user_id: int, creator_id: int):
    """The live membership for a pair, falling back to the most recent row."""
    row = conn.execute(
        """
        SELECT * FROM memberships
        WHERE user_id = ? AND creator_id = ? AND status = 'active' AND ends_at > ?
        ORDER BY ends_at DESC LIMIT 1
        """,
        (user_id, creator_id, now_iso()),
    ).fetchone()
    if row is not None:
        return row
    return conn.execute(
        "SELECT * FROM memberships WHERE user_id = ? AND creator_id = ? ORDER BY id DESC LIMIT 1",
        (user_id, creator_id),
    ).fetchone()


def memberships_overview(ctx, auth: Auth) -> dict:
    require_active(auth)
    expire_memberships(ctx)
    with ctx.db.connection() as conn:
        rows = conn.execute(
            """
            SELECT m.*, c.handle, c.page_name, c.status AS page_status, c.category,
                   t.name AS tier_name, t.price_cents AS tier_price_cents
            FROM memberships m
            JOIN creator_pages c ON c.id = m.creator_id
            JOIN tiers t ON t.id = m.tier_id
            WHERE m.user_id = ?
            ORDER BY (m.status = 'active' AND m.ends_at > ?) DESC, m.ends_at DESC
            """,
            (auth.user_id, now_iso()),
        ).fetchall()
        items = []
        for row in rows:
            record = dict(row)
            items.append(
                {
                    "membership": membership_public(
                        record,
                        creator={
                            "id": record["creator_id"],
                            "handle": record["handle"],
                            "page_name": record["page_name"],
                            "category": record["category"],
                            "url": f"/c/{record['handle']}",
                            "page_status": record["page_status"],
                        },
                        tier={
                            "id": record["tier_id"],
                            "name": record["tier_name"],
                            "price_cents": record["tier_price_cents"],
                        },
                    ),
                    "access": {
                        "active": record["status"] == "active" and record["ends_at"] > now_iso(),
                        "ends_at": record["ends_at"],
                        "cancel_requested": bool(record["cancel_requested_at"]),
                        "renewal": "manual",
                        "cancellation_note": (
                            "Cancelling stops a future renewal only. Access you have already paid "
                            "for continues until the period ends."
                        ),
                    },
                }
            )
    active = [item for item in items if item["access"]["active"]]
    return {
        "items": items,
        "active_count": len(active),
        "past_count": len(items) - len(active),
        "empty_state": None if items else {
            "title": "No memberships yet",
            "body": (
                "When you support a creator, your 30-day access appears here with its end date. "
                "Nothing renews automatically."
            ),
        },
    }


def cancel_membership(ctx, auth: Auth, membership_id: int) -> dict:
    require_active(auth)
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM memberships WHERE id = ? AND user_id = ?",
                           (membership_id, auth.user_id)).fetchone()
        if row is None:
            raise not_found("Membership not found.")
        membership = dict(row)
        if membership["status"] != "active" or membership["ends_at"] <= now_iso():
            raise conflict(
                "That membership has already ended, so there is nothing to cancel. "
                "Renewing is always a manual choice.",
                code="membership_ended",
            )
        if not membership["cancel_requested_at"]:
            conn.execute("UPDATE memberships SET cancel_requested_at = ?, updated_at = ? WHERE id = ?",
                         (now_iso(), now_iso(), membership_id))
            audit.record(conn, action="member.membership_cancel_requested", actor_user_id=auth.user_id,
                         actor_role=auth.role, target_type="membership", target_id=membership_id,
                         meta={"ends_at": membership["ends_at"]})
        updated = conn.execute("SELECT * FROM memberships WHERE id = ?", (membership_id,)).fetchone()
    payload = membership_public(updated)
    payload["message"] = (
        f"Renewal stopped. Your access continues until {dict(updated)['ends_at']} — the time you "
        "already paid for is not removed."
    )
    return payload


def resume_membership(ctx, auth: Auth, membership_id: int) -> dict:
    require_active(auth)
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM memberships WHERE id = ? AND user_id = ?",
                           (membership_id, auth.user_id)).fetchone()
        if row is None:
            raise not_found("Membership not found.")
        membership = dict(row)
        if membership["status"] != "active" or membership["ends_at"] <= now_iso():
            raise conflict("That membership has already ended.", code="membership_ended")
        conn.execute("UPDATE memberships SET cancel_requested_at = NULL, updated_at = ? WHERE id = ?",
                     (now_iso(), membership_id))
        audit.record(conn, action="member.membership_cancel_withdrawn", actor_user_id=auth.user_id,
                     actor_role=auth.role, target_type="membership", target_id=membership_id)
        updated = conn.execute("SELECT * FROM memberships WHERE id = ?", (membership_id,)).fetchone()
    return membership_public(updated)


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


def checkout_quote(ctx, auth: Auth, tier_id: int, asset_key: str | None = None) -> dict:
    """Everything a member needs to see before a checkout is created."""
    require_active(auth)
    availability = checkout_availability(ctx)
    with ctx.db.connection() as conn:
        tier = tier_by_id(conn, tier_id)
        if tier is None:
            raise not_found("Tier not found.")
        tier = dict(tier)
        page = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (tier["creator_id"],)).fetchone()
        if page is None:
            raise not_found("Tier not found.")
        page = dict(page)
        owner = conn.execute("SELECT * FROM users WHERE id = ?", (page["user_id"],)).fetchone()
        existing = membership_for(conn, auth.user_id, page["id"])
        methods = creator_methods(ctx, conn, page["id"])

    blockers: list[dict] = []
    if not tier["is_active"] or tier["archived_at"]:
        blockers.append({"code": "tier_inactive", "message": "That tier is not on sale right now."})
    if page["status"] != "active" or (owner and owner["status"] != "active"):
        blockers.append({"code": "page_unavailable",
                         "message": "That creator's page is not accepting new members right now."})
    if not availability["available"]:
        blockers.append({"code": availability["reason"] or "checkout_unavailable",
                         "message": availability["notice"]})
    usable = [m for m in methods["options"] if m["available"]]
    if not methods["has_wallets"]:
        blockers.append({
            "code": "payout_address_required",
            "message": (
                "This creator has not recorded a wallet address yet, so the operator cannot "
                "settle funds to them. Checkout stays closed until they add one."
            ),
        })
    elif availability["available"] and not usable:
        if methods["store_error"]:
            blockers.append({
                "code": "checkout_unavailable",
                "message": "Velora could not reach BTCPay Server to confirm which coins it accepts. "
                           "No payment has been started — please try again shortly.",
            })
        else:
            blockers.append({
                "code": "no_payment_method_available",
                "message": "None of the coins this creator accepts can be paid through this Velora "
                           "instance right now. No payment has been started.",
            })
    chosen = None
    if asset_key is not None:
        chosen = next((m for m in usable if m["key"] == asset_key), None)
        if chosen is None and methods["has_wallets"] and not any(
                b["code"] in ("no_payment_method_available", "checkout_unavailable") for b in blockers):
            blockers.append({
                "code": "asset_unavailable",
                "message": "That coin cannot be used for this creator right now. Choose another.",
            })
    if not auth.verified:
        blockers.append({"code": "email_verification_required",
                         "message": "Confirm your email address before buying a membership."})

    fee_cents = platform_fee(tier["price_cents"])
    return {
        "tier": tier_public(tier),
        "creator": {"id": page["id"], "handle": page["handle"], "page_name": page["page_name"]},
        "amount_cents": tier["price_cents"],
        "platform_fee_cents": fee_cents,
        "creator_net_cents": tier["price_cents"] - fee_cents,
        "fee_percent": PLATFORM_FEE_PERCENT,
        "period_days": MEMBERSHIP_PERIOD_DAYS,
        "can_checkout": not blockers,
        "blockers": blockers,
        "payment_options": methods["options"],
        "selected_asset": chosen["key"] if chosen else None,
        "availability": availability,
        "existing_membership": (
            {
                "id": existing["id"],
                "ends_at": existing["ends_at"],
                "status": existing["status"],
            } if existing else None
        ),
        "renewal_notice": (
            "This is a one-time on-chain crypto payment for a 30-day period. It does not auto-renew: "
            "no charge is ever scheduled, and you choose each time whether to renew."
        ),
        "sequence": [
            "You choose a coin or token the creator accepts. Velora creates a pending order and asks "
            "BTCPay for an on-chain invoice in that asset.",
            "You pay the quoted amount on the chosen network inside BTCPay's hosted checkout.",
            "BTCPay sends a signed webhook and Velora independently re-checks the invoice.",
            "Only a matching, settled, on-chain payment in the chosen asset unlocks 30 days of access.",
        ],
    }


def platform_fee(amount_cents: int) -> int:
    """Integer-exact 10% platform fee, frozen into the settled invoice row."""
    return (int(amount_cents) * PLATFORM_FEE_PERCENT) // 100


def create_intent(ctx, auth: Auth, payload: dict, request) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("checkout", auth.user_id)
    require_checkout_available(ctx)

    tier_id = payload.get("tier_id")
    if isinstance(tier_id, str) and tier_id.isdigit():
        tier_id = int(tier_id)
    if not isinstance(tier_id, int):
        raise bad_request("Choose a tier to support.", code="validation_error", field="tier_id")

    asset_key = payload.get("asset")
    if asset_key is not None and not isinstance(asset_key, str):
        raise bad_request("Choose how you want to pay.", code="validation_error", field="asset")
    if asset_key is not None and get_asset(asset_key) is None:
        raise bad_request("That coin or token is not supported.", code="validation_error", field="asset")

    quote = checkout_quote(ctx, auth, tier_id)
    usable = [m for m in quote["payment_options"] if m["available"]]
    if asset_key is None and len(usable) == 1:
        asset_key = usable[0]["key"]
    elif asset_key is None and len(usable) > 1:
        raise bad_request("Choose which coin or token you want to pay with.",
                          code="validation_error", field="asset")
    if asset_key is not None:
        quote = checkout_quote(ctx, auth, tier_id, asset_key)
    if not quote["can_checkout"]:
        blocker = quote["blockers"][0]
        code = blocker["code"]
        status = 409 if code in ("tier_inactive", "page_unavailable", "payout_address_required",
                                 "no_payment_method_available", "asset_unavailable") else 503
        if code == "email_verification_required":
            raise forbidden(blocker["message"], code=code)
        raise ApiError(status, code, blocker["message"])

    asset = ASSETS[asset_key]
    method_id = ctx.config.method_id(asset_key)
    order_ref = opaque_reference("VLR")
    expires_at = future_iso(ctx.config.btcpay_invoice_ttl_minutes * 60)
    created = now_iso()
    with ctx.db.transaction() as conn:
        intent_id = conn.execute(
            """
            INSERT INTO payment_intents (order_ref, user_id, creator_id, tier_id, amount_cents, currency,
                                         period_days, status, created_at, updated_at, expires_at,
                                         asset, payment_method)
            VALUES (?, ?, ?, ?, ?, 'USD', ?, 'pending', ?, ?, ?, ?, ?)
            """,
            (order_ref, auth.user_id, quote["creator"]["id"], tier_id, quote["amount_cents"],
             MEMBERSHIP_PERIOD_DAYS, created, created, expires_at, asset_key, method_id),
        ).lastrowid
        audit.record(conn, action="payment.intent_created", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="payment_intent", target_id=intent_id,
                     meta={"order_ref": order_ref, "amount_cents": quote["amount_cents"],
                           "creator_id": quote["creator"]["id"], "asset": asset_key})

    try:
        client = BtcPayClient.from_config(ctx.config)
        redirect_url = f"{_public_base(ctx, request)}/checkout/{order_ref}"
        raw = client.create_invoice(
            amount_cents=quote["amount_cents"],
            order_ref=order_ref,
            item_description=f"Velora — {quote['creator']['page_name']} · {quote['tier']['name']} "
                             f"({MEMBERSHIP_PERIOD_DAYS} days)",
            redirect_url=redirect_url,
            ttl_minutes=ctx.config.btcpay_invoice_ttl_minutes,
            payment_method=method_id,
        )
    except BtcPayNotConfigured:
        _fail_intent(ctx, intent_id, "BTCPay Server is not configured.")
        raise unavailable("Crypto checkout is unavailable on this instance.", code="checkout_unavailable") from None
    except BtcPayError as exc:
        _fail_intent(ctx, intent_id, str(exc))
        raise unavailable(
            "Velora could not reach BTCPay Server to create the invoice. No payment has been taken. "
            "Please try again shortly.",
            code="checkout_unavailable",
        ) from None

    normalized = normalize_invoice(raw, method_id, asset.decimals)
    if not normalized.invoice_id or not normalized.checkout_link:
        _fail_intent(ctx, intent_id, "BTCPay did not return a usable invoice.")
        raise unavailable("BTCPay returned an unusable invoice. No payment has been taken.",
                          code="checkout_unavailable")
    if str(normalized.metadata.get("orderRef") or "") != order_ref:
        _fail_intent(ctx, intent_id, "Order reference mismatch at creation.")
        raise unavailable("Velora stopped this checkout because the invoice did not match the order.",
                          code="checkout_unavailable")

    with ctx.db.transaction() as conn:
        conn.execute(
            """
            UPDATE payment_intents
            SET btcpay_invoice_id = ?, checkout_url = ?, btc_invoice_sats = ?, asset_amount_atomic = ?,
                btc_rate_usd = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (normalized.invoice_id, normalized.checkout_link,
             normalized.due_units if asset_key == "btc" else None,
             str(normalized.due_units) if normalized.due_units is not None else None,
             normalized.rate, now_iso(), intent_id),
        )
        row = conn.execute("SELECT * FROM payment_intents WHERE id = ?", (intent_id,)).fetchone()
    payload_out = payment_intent_public(row)
    payload_out["renewal_notice"] = quote["renewal_notice"]
    return payload_out


def _fail_intent(ctx, intent_id: int, error: str) -> None:
    with ctx.db.transaction() as conn:
        conn.execute(
            """
            UPDATE payment_intents SET status = 'cancelled', last_error = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (error[:300], now_iso(), intent_id),
        )


def _public_base(ctx, request) -> str:
    """Absolute base for BTCPay's redirect target (never used as proof of payment)."""
    if ctx.config.public_base_url:
        return ctx.config.public_base_url.rstrip("/")
    scheme = "https" if ctx.config.secure_cookies == "always" else "http"
    return f"{scheme}://{request.host or 'localhost:8000'}"


# ---------------------------------------------------------------------------
# Verification and settlement
# ---------------------------------------------------------------------------


def intent_for_user(conn, intent_id: int, user_id: int, *, is_admin: bool = False):
    row = conn.execute("SELECT * FROM payment_intents WHERE id = ?", (intent_id,)).fetchone()
    if row is None:
        raise not_found("Payment attempt not found.")
    intent = dict(row)
    if not is_admin and intent["user_id"] != user_id:
        raise forbidden("That payment attempt belongs to another account.", code="payment_private")
    return intent


def verify_with_btcpay(ctx, intent: dict) -> tuple[NormalizedInvoice, Classification]:
    """Independently fetch the invoice and classify it against the intent.

    Advisory only: always re-reads from BTCPay rather than trusting a webhook
    body, a redirect URL or anything a browser could hand us.
    """
    if not ctx.config.btcpay_configured:
        raise unavailable("BTCPay Server is not configured, so no payment can be verified.",
                          code="checkout_unavailable")
    if not intent.get("btcpay_invoice_id"):
        raise conflict("That order has no BTCPay invoice yet.", code="no_invoice")
    asset = ASSETS.get(intent.get("asset") or "btc") or ASSETS["btc"]
    method_id = intent.get("payment_method") or ctx.config.method_id(asset.key)
    client = BtcPayClient.from_config(ctx.config)
    raw = client.get_invoice(intent["btcpay_invoice_id"])
    normalized = normalize_invoice(raw, method_id, asset.decimals)
    return normalized, classify_invoice(normalized, _Target(intent))


class _Target:
    """Small adapter so classification sees plain attribute access."""

    def __init__(self, intent: dict):
        self.order_ref = intent["order_ref"]
        self.amount_cents = intent["amount_cents"]
        self.currency = intent.get("currency") or "USD"
        self.btcpay_invoice_id = intent.get("btcpay_invoice_id")
        self.expires_at = intent.get("expires_at")


def apply_classification(ctx, intent: dict, normalized: NormalizedInvoice, classification: Classification,
                         *, source: str) -> dict:
    """Persist the outcome of a verification. Only ``settled`` grants access."""
    if classification.grants_access:
        return grant_settlement(ctx, intent, normalized, classification, source=source)

    if classification.outcome in ("pending", "processing"):
        new_status = "processing" if classification.outcome == "processing" else intent["status"]
        if intent["status"] in ("pending", "processing") and new_status != intent["status"]:
            with ctx.db.transaction() as conn:
                conn.execute(
                    "UPDATE payment_intents SET status = ?, btc_rate_usd = COALESCE(?, btc_rate_usd), updated_at = ? "
                    "WHERE id = ? AND status IN ('pending','processing')",
                    (new_status, classification.rate, now_iso(), intent["id"]),
                )
        return {"outcome": classification.outcome, "reason": classification.reason,
                "message": classification.message, "access_granted": False}

    if classification.outcome == "expired":
        with ctx.db.transaction() as conn:
            conn.execute(
                "UPDATE payment_intents SET status = 'expired', updated_at = ? WHERE id = ? AND status IN ('pending','processing')",
                (now_iso(), intent["id"]),
            )
        return {"outcome": "expired", "reason": classification.reason,
                "message": classification.message, "access_granted": False}

    # Everything else is held for review; content stays locked.
    hold_intent(ctx, intent["id"], classification.reason, classification.message)
    return {"outcome": "held", "reason": classification.reason, "message": classification.message,
            "access_granted": False}


def hold_intent(ctx, intent_id: int, reason: str, message: str) -> None:
    with ctx.db.transaction() as conn:
        conn.execute(
            """
            UPDATE payment_intents
            SET status = 'held', hold_reason = ?, last_error = ?, updated_at = ?
            WHERE id = ? AND status != 'settled'
            """,
            (reason, message[:300], now_iso(), intent_id),
        )
        audit.record(conn, action="payment.held", target_type="payment_intent", target_id=intent_id,
                     meta={"reason": reason})


def grant_settlement(ctx, intent: dict, normalized: NormalizedInvoice, classification: Classification,
                     *, source: str) -> dict:
    """Record the settled invoice, freeze the fee, write the ledger, unlock access.

    Idempotent: replaying the same settled invoice never writes a second invoice,
    a second ledger set or a second membership extension.
    """
    order_ref = intent["order_ref"]
    invoice_id = normalized.invoice_id or intent.get("btcpay_invoice_id") or ""
    if not invoice_id:
        raise conflict("The invoice has no identifier; refusing to settle.", code="no_invoice")

    with ctx.db.transaction() as conn:
        existing = conn.execute(
            "SELECT * FROM invoices WHERE btcpay_invoice_id = ? OR order_ref = ?", (invoice_id, order_ref)
        ).fetchone()
        if existing is not None:
            audit.record(conn, action="payment.settlement_replay_ignored", target_type="invoice",
                         target_id=dict(existing)["id"], meta={"order_ref": order_ref, "source": source})
            return {
                "outcome": "settled",
                "reason": "already_settled",
                "message": "That payment was already recorded; nothing was granted twice.",
                "access_granted": False,
                "invoice": invoice_public(existing),
            }

        row = conn.execute("SELECT * FROM payment_intents WHERE id = ?", (intent["id"],)).fetchone()
        if row is None:
            raise not_found("Payment attempt not found.")
        current = dict(row)
        if current["status"] == "settled":
            return {"outcome": "settled", "reason": "already_settled",
                    "message": "That order is already settled.", "access_granted": False}

        amount_cents = int(current["amount_cents"])
        fee_cents = platform_fee(amount_cents)
        net_cents = amount_cents - fee_cents
        settled_at = now_iso()

        asset_key = current.get("asset") or "btc"
        method_id = current.get("payment_method") or ASSETS[asset_key].method_id
        wallet = wallet_for(conn, current["creator_id"], asset_key)
        payout_snapshot = dict(wallet)["address"] if wallet else None
        units = int(classification.units or normalized.settled_units or 0)

        invoice_row = conn.execute(
            """
            INSERT INTO invoices (payment_intent_id, order_ref, btcpay_invoice_id, user_id, creator_id, tier_id,
                                  amount_cents, platform_fee_cents, creator_net_cents, fee_percent,
                                  btc_amount_sats, btc_rate_usd, btc_destination, payout_address_snapshot,
                                  status, settled_at, recorded_at, verify_source, invoice_digest,
                                  asset, payment_method, asset_amount_atomic, payout_asset)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'settled', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (current["id"], order_ref, invoice_id, current["user_id"], current["creator_id"],
             current["tier_id"], amount_cents, fee_cents, net_cents, PLATFORM_FEE_PERCENT,
             units if asset_key == "btc" else 0, classification.rate or normalized.rate,
             f"btcpay:{method_id}", payout_snapshot, settled_at, settled_at, source,
             sha256_hex(f"{invoice_id}:{order_ref}:{amount_cents}:{settled_at}".encode("utf-8")),
             asset_key, method_id, str(units), asset_key if wallet else None),
        ).lastrowid

        ledger_rows = [
            ("member_payment", "member_payment_received", "debit", amount_cents,
             f"On-chain {ASSETS[asset_key].symbol} ({ASSETS[asset_key].network}) membership payment "
             f"for order {order_ref}"),
            ("platform_fee", "platform_revenue", "credit", fee_cents,
             f"Velora platform fee ({PLATFORM_FEE_PERCENT}%), frozen at settlement"),
            ("creator_earning", "creator_payable", "credit", net_cents,
             f"Creator share, released by the operator to the creator's recorded "
             f"{ASSETS[asset_key].symbol} ({ASSETS[asset_key].network}) wallet"),
        ]
        for entry_type, account, direction, amount, memo in ledger_rows:
            conn.execute(
                """
                INSERT INTO ledger_entries (invoice_id, order_ref, entry_type, account, direction,
                                            amount_cents, memo, actor_kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'system', ?)
                """,
                (invoice_row, order_ref, entry_type, account, direction, amount, memo, settled_at),
            )

        conn.execute(
            """
            UPDATE payment_intents
            SET status = 'settled', settled_at = ?, btc_invoice_sats = ?, asset_amount_atomic = ?,
                btc_rate_usd = ?, hold_reason = NULL, last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (settled_at, units if asset_key == "btc" else None, str(units),
             classification.rate or normalized.rate, settled_at, current["id"]),
        )

        membership = _extend_membership(conn, current, invoice_row)

        audit.record(conn, action="payment.settled", target_type="invoice", target_id=invoice_row,
                     meta={"order_ref": order_ref, "amount_cents": amount_cents, "fee_cents": fee_cents,
                           "creator_net_cents": net_cents, "source": source,
                           "membership_id": membership["id"]})
        invoice_row_data = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_row,)).fetchone()
        membership_row = conn.execute("SELECT * FROM memberships WHERE id = ?",
                                      (membership["id"],)).fetchone()

    return {
        "outcome": "settled",
        "reason": "verified",
        "message": "Payment settled and verified. 30 days of access granted.",
        "access_granted": True,
        "invoice": invoice_public(invoice_row_data),
        "membership": membership_public(membership_row),
        "membership_extended": membership["extended"],
    }


def _extend_membership(conn, intent: dict, invoice_id: int) -> dict:
    """Create the 30-day membership that the settled invoice pays for.

    Renewals extend from the current end date, so a member who renews early never
    loses the days they already paid for.
    """
    from ..db import parse_iso, utcnow
    from datetime import timedelta

    existing = conn.execute(
        """
        SELECT * FROM memberships WHERE user_id = ? AND creator_id = ? AND status = 'active' AND ends_at > ?
        ORDER BY ends_at DESC LIMIT 1
        """,
        (intent["user_id"], intent["creator_id"], now_iso()),
    ).fetchone()

    now = utcnow()
    start = now
    if existing is not None:
        previous = dict(existing)
        previous_end = parse_iso(previous["ends_at"])
        if previous_end and previous_end > now:
            start = previous_end
        # Retire the old row: the new invoice funds a new, continuous period.
        conn.execute("UPDATE memberships SET status = 'expired', updated_at = ? WHERE id = ?",
                     (now_iso(), previous["id"]))
    ends_at = (start + timedelta(days=MEMBERSHIP_PERIOD_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

    membership_id = conn.execute(
        """
        INSERT INTO memberships (user_id, creator_id, tier_id, invoice_id, status, started_at, ends_at,
                                 created_at, updated_at)
        VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (intent["user_id"], intent["creator_id"], intent["tier_id"], invoice_id,
         now.strftime("%Y-%m-%dT%H:%M:%SZ"), ends_at, now_iso(), now_iso()),
    ).lastrowid
    return {"id": membership_id, "extended": existing is not None, "ends_at": ends_at}


# ---------------------------------------------------------------------------
# Member-facing status
# ---------------------------------------------------------------------------


def refresh_intent(ctx, auth: Auth, intent_id: int) -> dict:
    """Poll BTCPay for an intent's real status. Never trusts the caller."""
    require_active(auth)
    ctx.gate("payment_poll", auth.user_id)
    with ctx.db.connection() as conn:
        intent = intent_for_user(conn, intent_id, auth.user_id, is_admin=auth.is_admin)

    if intent["status"] == "settled":
        payload = payment_intent_public(intent)
        payload["verification"] = {"outcome": "settled", "reason": "already_settled",
                                   "message": "This order is settled.", "access_granted": False}
        return payload
    if intent["status"] in ("cancelled", "archived"):
        payload = payment_intent_public(intent)
        payload["verification"] = {"outcome": intent["status"], "reason": intent["status"],
                                   "message": "That order is not awaiting payment.", "access_granted": False}
        return payload
    if not intent.get("btcpay_invoice_id"):
        payload = payment_intent_public(intent)
        payload["verification"] = {"outcome": "pending", "reason": "no_invoice",
                                   "message": "No invoice has been created for this order yet.",
                                   "access_granted": False}
        return payload

    try:
        normalized, classification = verify_with_btcpay(ctx, intent)
    except BtcPayError as exc:
        with ctx.db.transaction() as conn:
            conn.execute("UPDATE payment_intents SET last_error = ?, updated_at = ? WHERE id = ?",
                         (str(exc)[:300], now_iso(), intent_id))
        raise unavailable(
            "Velora could not reach BTCPay Server to check this payment. Nothing has changed: the "
            "order stays pending until it can be verified.",
            code="verification_unavailable",
        ) from None

    result = apply_classification(ctx, intent, normalized, classification, source="poll")
    with ctx.db.connection() as conn:
        fresh = conn.execute("SELECT * FROM payment_intents WHERE id = ?", (intent_id,)).fetchone()
    payload = payment_intent_public(fresh)
    payload["verification"] = result
    if result.get("membership"):
        payload["membership"] = result["membership"]
    return payload


def payment_status(ctx, auth: Auth, *, limit: int = 50) -> dict:
    require_active(auth)
    expire_memberships(ctx)
    with ctx.db.connection() as conn:
        intents = conn.execute(
            """
            SELECT i.*, c.handle, c.page_name, t.name AS tier_name
            FROM payment_intents i
            JOIN creator_pages c ON c.id = i.creator_id
            JOIN tiers t ON t.id = i.tier_id
            WHERE i.user_id = ?
            ORDER BY i.created_at DESC, i.id DESC LIMIT ?
            """,
            (auth.user_id, max(1, min(200, limit))),
        ).fetchall()
        invoices = conn.execute(
            """
            SELECT * FROM invoices WHERE user_id = ? ORDER BY settled_at DESC, id DESC LIMIT ?
            """,
            (auth.user_id, max(1, min(200, limit))),
        ).fetchall()
    return {
        "intents": [
            {
                **payment_intent_public(row),
                "creator": {"id": row["creator_id"], "handle": row["handle"], "page_name": row["page_name"]},
                "tier_name": row["tier_name"],
            }
            for row in intents
        ],
        "invoices": [invoice_public(row) for row in invoices],
        "labels": INTENT_LABELS,
        "unavailable_notice": checkout_availability(ctx)["notice"]
        if not ctx.config.btcpay_configured else None,
    }


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


class WebhookOutcome(dict):
    """A webhook result plus the HTTP status BTCPay should receive."""

    def __init__(self, http_status: int, outcome: str, detail: str, **extra):
        super().__init__(http_status=http_status, outcome=outcome, detail=detail, **extra)


def handle_webhook(ctx, *, raw_body: bytes, signature_header: str | None, delivery_header: str | None,
                   source_hash: str | None = None) -> WebhookOutcome:
    """Process a BTCPay webhook. Verifies the signature, then re-verifies the invoice.

    Fail-closed rules:
    * No webhook secret configured -> refuse, record, never grant.
    * Bad or missing signature -> refuse and record, never grant.
    * The invoice must exist locally *and* re-verify through the API.
    * Duplicate deliveries are recorded once and ignored afterwards.
    """
    ctx.gate_or_flag("webhook", source_hash or "unknown")
    if not ctx.config.btcpay_webhook_secret:
        return WebhookOutcome(
            503, "rejected_unconfigured",
            "Webhook handling requires VELORA_BTCPAY_WEBHOOK_SECRET; no events are trusted.",
            signature_valid=False,
        )

    provided = parse_webhook_signature(signature_header)
    expected = hmac_hex(ctx.config.btcpay_webhook_secret, raw_body)
    signature_valid = bool(provided) and constant_time_equal(provided, expected)
    if not signature_valid:
        _record_event(ctx, delivery_id=_delivery_id_from(raw_body, delivery_header), payload=None,
                      signature_valid=False, outcome="invalid_signature",
                      detail="Signature missing or does not match the configured webhook secret.")
        return WebhookOutcome(403, "invalid_signature",
                              "Webhook signature verification failed.", signature_valid=False)

    try:
        import json as _json

        event = _json.loads(raw_body.decode("utf-8"))
    except Exception:  # noqa: BLE001 - malformed body
        _record_event(ctx, delivery_id=_delivery_id_from(raw_body, delivery_header), payload=None,
                      signature_valid=True, outcome="malformed_body", detail="Body was not JSON.")
        return WebhookOutcome(400, "malformed_body", "Webhook body could not be parsed.",
                              signature_valid=True)
    if not isinstance(event, dict):
        return WebhookOutcome(400, "malformed_body", "Webhook body was not a JSON object.",
                              signature_valid=True)

    delivery_id = str(event.get("deliveryId") or delivery_header or "").strip() \
        or _delivery_id_from(raw_body, None)
    invoice_id = event.get("invoiceId")
    if not invoice_id and isinstance(event.get("data"), dict):
        invoice_id = event["data"].get("id")
    event_type = str(event.get("type") or event.get("eventType") or "unknown")
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    reference = metadata.get("orderRef") if metadata else None

    with ctx.db.connection() as conn:
        duplicate = conn.execute(
            "SELECT id, outcome FROM webhook_events WHERE provider = 'btcpay' AND delivery_id = ?",
            (delivery_id,),
        ).fetchone()
        if duplicate is not None:
            return WebhookOutcome(200, "duplicate",
                                  f"Delivery {delivery_id} was already processed.", signature_valid=True)
        intent_row = None
        if invoice_id:
            intent_row = conn.execute("SELECT * FROM payment_intents WHERE btcpay_invoice_id = ?",
                                      (str(invoice_id),)).fetchone()
    intent = dict(intent_row) if intent_row else None

    if intent is None:
        _record_event(ctx, delivery_id=delivery_id, payload=event, signature_valid=True,
                      outcome="unknown_invoice", invoice_id=invoice_id, order_ref=reference,
                      detail="No local order matches that invoice; nothing was changed.")
        return WebhookOutcome(200, "unknown_invoice",
                              "No local order matches that invoice; nothing was changed.",
                              signature_valid=True)

    if intent["status"] == "settled":
        _record_event(ctx, delivery_id=delivery_id, payload=event, signature_valid=True,
                      outcome="already_settled", invoice_id=invoice_id, order_ref=intent["order_ref"],
                      detail="Order already settled; replay ignored.")
        return WebhookOutcome(200, "already_settled", "Order already settled; replay ignored.",
                              signature_valid=True)

    # Independent verification: never trust the webhook body's claim.
    try:
        normalized, classification = verify_with_btcpay(ctx, intent)
    except (BtcPayError, ApiError) as exc:
        _record_event(ctx, delivery_id=delivery_id, payload=event, signature_valid=True,
                      outcome="verification_failed", invoice_id=invoice_id, order_ref=intent["order_ref"],
                      detail=str(exc)[:300])
        return WebhookOutcome(503, "verification_failed",
                              "Independent BTCPay verification failed; the event was not applied.",
                              signature_valid=True)

    result = apply_classification(ctx, intent, normalized, classification, source=f"webhook:{event_type}")

    if result["outcome"] == "settled":
        http_status = 200
    elif result["outcome"] == "held":
        http_status = 200
    elif result["outcome"] in ("pending", "processing"):
        # Ask BTCPay to retry: the invoice may not be readable as settled yet.
        http_status = 503
    else:
        http_status = 200

    _record_event(ctx, delivery_id=delivery_id, payload=event, signature_valid=True,
                  outcome=result["outcome"], invoice_id=invoice_id, order_ref=intent["order_ref"],
                  detail=f"{result['reason']}: {result['message']}"[:400])
    return WebhookOutcome(http_status, result["outcome"], result["message"],
                          signature_valid=True, access_granted=result.get("access_granted", False),
                          reason=result.get("reason"))


def _delivery_id_from(raw_body: bytes, header: str | None) -> str:
    if header:
        return str(header).strip()[:120]
    return f"body-{sha256_hex(raw_body)[:40]}"


def _record_event(ctx, *, delivery_id: str, payload: dict | None, signature_valid: bool, outcome: str,
                  invoice_id: str | None = None, order_ref: str | None = None, detail: str | None = None) -> None:
    digest = None
    if payload is not None:
        import json as _json

        digest = sha256_hex(_json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    with ctx.db.transaction() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO webhook_events (provider, delivery_id, event_type, invoice_id, order_ref,
                                                  signature_valid, outcome, detail, payload_digest, received_at)
            VALUES ('btcpay', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (delivery_id, (payload or {}).get("type") if payload else None, invoice_id, order_ref,
             1 if signature_valid else 0, outcome, detail, digest, now_iso()),
        )
    if outcome in ("invalid_signature", "rejected_unconfigured", "unknown_invoice", "malformed_body",
                   "verification_failed"):
        with ctx.db.transaction() as conn:
            audit.record(conn, action="payment.webhook_rejected", target_type="webhook_event",
                         meta={"outcome": outcome, "delivery_id": delivery_id[:64]})


def payment_intent_for_order_ref(ctx, order_ref: str) -> dict | None:
    with ctx.db.connection() as conn:
        row = conn.execute("SELECT * FROM payment_intents WHERE order_ref = ?", (order_ref,)).fetchone()
    return dict(row) if row else None


__all__ = [
    "apply_classification",
    "cancel_membership",
    "checkout_availability",
    "checkout_quote",
    "create_intent",
    "expire_memberships",
    "grant_settlement",
    "handle_webhook",
    "hold_intent",
    "membership_for",
    "memberships_overview",
    "payment_status",
    "platform_fee",
    "refresh_intent",
    "require_checkout_available",
    "resume_membership",
]
