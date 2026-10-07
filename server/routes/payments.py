"""Payment availability, checkout, verification, memberships and the BTCPay webhook."""

from __future__ import annotations

from ..http import bad_request, forbidden, json_response, not_found, rate_limited
from ..routing import AUTH_NONE, AUTH_REQUIRED, route
from ..config import MEMBERSHIP_PERIOD_DAYS as PLATFORM_FEE_PERIOD_DAYS
from ..config import PLATFORM_FEE_PERCENT
from ..serializers import payment_intent_public
from ..services import payments as payments_service
from .base import ok


@route("GET", "/api/payments/availability", auth=AUTH_NONE)
def availability(request, ctx, auth, params):
    payload = payments_service.checkout_availability(ctx)
    payload.update(
        {
            "methods": ["btc_onchain"] if ctx.config.btcpay_configured else [],
            "period_days": PLATFORM_FEE_PERIOD_DAYS,
            "platform_fee_percent": PLATFORM_FEE_PERCENT,
            "auto_renewal": False,
            "notices": [
                "Bitcoin on-chain payments only. No cards, no Lightning, no other assets.",
                "Each 30-day period is a separate payment. Velora never schedules an automatic charge.",
                "A browser redirect is not proof of payment: access follows a verified settled invoice.",
            ],
        }
    )
    return ok(payload)


@route("GET", "/api/payments/quote", auth=AUTH_REQUIRED)
def quote(request, ctx, auth, params):
    tier_id = request.query_int("tier_id", None)
    if not tier_id:
        raise bad_request("Choose a tier to see the price.", code="validation_error", field="tier_id")
    return ok(payments_service.checkout_quote(ctx, auth, tier_id))


@route("POST", "/api/payments/intents", auth=AUTH_REQUIRED)
def create_intent(request, ctx, auth, params):
    payload = payments_service.create_intent(ctx, auth, request.json(), request)
    return json_response(payload, status=201)


@route("GET", "/api/payments/status", auth=AUTH_REQUIRED)
def payment_status(request, ctx, auth, params):
    return ok(payments_service.payment_status(ctx, auth))


@route("GET", "/api/payments/intents/<int:intent_id>", auth=AUTH_REQUIRED)
def intent_detail(request, ctx, auth, params):
    """Order status for its owner (or an administrator). Proves nothing by itself."""
    with ctx.db.connection() as conn:
        record = payments_service.intent_for_user(conn, params["intent_id"], auth.user_id,
                                                  is_admin=auth.is_admin)
    return ok(payment_intent_public(record))


@route("POST", "/api/payments/intents/<int:intent_id>/refresh", auth=AUTH_REQUIRED)
def refresh_intent(request, ctx, auth, params):
    return ok(payments_service.refresh_intent(ctx, auth, params["intent_id"]))


@route("GET", "/api/payments/orders/<str:order_ref>", auth=AUTH_REQUIRED)
def order_by_reference(request, ctx, auth, params):
    record = payments_service.payment_intent_for_order_ref(ctx, params["order_ref"])
    if record is None:
        raise not_found("Order not found.")
    if record["user_id"] != auth.user_id and not auth.is_admin:
        raise forbidden("That order belongs to another account.", code="payment_private")
    payload = payment_intent_public(record)
    payload["order_ref"] = record["order_ref"]
    return ok(payload)


@route("POST", "/api/memberships/<int:membership_id>/cancel", auth=AUTH_REQUIRED)
def cancel_membership(request, ctx, auth, params):
    return ok(payments_service.cancel_membership(ctx, auth, params["membership_id"]))


@route("POST", "/api/memberships/<int:membership_id>/resume", auth=AUTH_REQUIRED)
def resume_membership(request, ctx, auth, params):
    return ok(payments_service.resume_membership(ctx, auth, params["membership_id"]))


@route("POST", "/api/payments/btcpay/webhook", auth=AUTH_NONE, csrf=False, body_limit="webhook")
def btcpay_webhook(request, ctx, auth, params):
    """BTCPay delivery endpoint.

    Never authenticated by session: authenticity comes from the HMAC signature over
    the raw body, and every event is independently re-verified against the BTCPay
    API before any state changes.
    """
    result = payments_service.handle_webhook(
        ctx,
        raw_body=request.body,
        signature_header=request.header("btcpay-sig"),
        delivery_header=request.header("btcpay-delivery-id"),
        source_hash=ctx.client_hash(request.client_ip(ctx.config.trust_proxy)),
    )
    http_status = result.pop("http_status", 200)
    if http_status == 429:
        raise rate_limited("Too many webhook deliveries.", 60)
    return json_response(result, status=http_status)
