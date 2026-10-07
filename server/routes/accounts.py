"""Account, session, verification, invitation and privacy endpoints."""

from __future__ import annotations

from ..wallets import ASSET_KEYS, ASSETS
from ..http import bad_request, json_response
from ..routing import AUTH_NONE, AUTH_OPTIONAL, AUTH_REQUIRED, route
from ..serializers import creator_application, user_private
from ..services import accounts as accounts_service
from ..services import creators as creators_service
from ..services.payments import memberships_overview
from ..validation import (
    ORIENTATION_PRESETS,
    ORIENTATION_PREFER_NOT_TO_SAY,
    ORIENTATION_PUBLIC_ELIGIBLE,
)
from .base import cleared_session_cookie, ok, session_cookie


@route("GET", "/api/bootstrap", auth=AUTH_OPTIONAL)
def bootstrap(request, ctx, auth, params):
    """Everything the single-page app needs on load, in one request."""
    from ..media import EXTENSIONS  # noqa: F401 - ensures storage module is importable

    with ctx.db.connection() as conn:
        creator_page = None
        application = None
        if auth is not None:
            page_row = creators_service.page_for_user(conn, auth.user_id)
            if page_row:
                creator_page = creators_service._owner_page_payload(page_row)
            application_row = conn.execute(
                "SELECT * FROM creator_applications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (auth.user_id,),
            ).fetchone()
            if application_row:
                application = creator_application(application_row)

    payload = {
        "features": ctx.config.public_features(),
        "email_status": ctx.email.status(),
        "checkout": {
            "available": ctx.config.btcpay_configured,
            "methods": list(ctx.config.offered_assets) if ctx.config.btcpay_configured else [],
            "reason": None if ctx.config.btcpay_configured else "btcpay_not_configured",
        },
        # Every coin and token a creator can record a wallet for.
        "payment_assets": [ASSETS[key].public() for key in ASSET_KEYS],
        "categories": creators_service.categories(),
        "orientation_options": {
            "presets": [{"value": value, "label": OPTION_LABELS.get(value, value)} for value in ORIENTATION_PRESETS],
            "prefer_not_to_say": {"value": ORIENTATION_PREFER_NOT_TO_SAY,
                                  "label": OPTION_LABELS[ORIENTATION_PREFER_NOT_TO_SAY]},
            "public_eligible": sorted(ORIENTATION_PUBLIC_ELIGIBLE),
            "sensitivity": (
                "Sexual orientation is optional and private by default. It is never used to filter "
                "discovery, search, analytics or public listings. You can keep it private and still "
                "have a complete profile. If you choose to share an eligible option, it appears only "
                "on your own creator page, and you can change your mind at any time."
            ),
        },
        "report_reasons": [
            {"value": "spam", "label": "Spam or unwanted promotion"},
            {"value": "harassment", "label": "Harassment or threats"},
            {"value": "impersonation", "label": "Impersonation"},
            {"value": "illegal_content", "label": "Illegal content"},
            {"value": "non_consensual_content", "label": "Shared without consent"},
            {"value": "minor_safety", "label": "Possible minor safety issue"},
            {"value": "payment_issue", "label": "Payment problem"},
            {"value": "privacy", "label": "Privacy concern"},
            {"value": "other", "label": "Something else"},
        ],
        "password_requirements": {"min_length": 8},
        "session": auth.public() if auth else {"authenticated": False},
        "creator_page": creator_page,
        "application": application,
    }
    return ok(payload)


OPTION_LABELS = {
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


@route("POST", "/api/auth/signup", auth=AUTH_NONE, csrf=False)
def signup(request, ctx, auth, params):
    result = accounts_service.signup(ctx, request.json(), request)
    return json_response(
        {
            "created": True,
            "user_id": result["user_id"],
            "verification": result["verification"],
            "next_step": (
                "Check your email for the confirmation link, then sign in."
                if result["verification"].get("sent") else
                "Your account exists, but this instance cannot send email yet. An operator must "
                "configure email delivery before you can confirm and use membership features."
            ),
            "attestation_notice": (
                "The 18+ checkbox is a self-attestation. Velora does not verify age or identity."
            ),
        },
        status=201,
    )


@route("POST", "/api/auth/verify-email", auth=AUTH_NONE, csrf=False)
def verify_email(request, ctx, auth, params):
    payload = request.json()
    token = payload.get("token") or request.query_one("token")
    result = accounts_service.verify_email(ctx, token, request)
    return ok(result)


@route("POST", "/api/auth/resend-verification", auth=AUTH_NONE, csrf=False)
def resend_verification(request, ctx, auth, params):
    result = accounts_service.resend_verification(ctx, request.json(), request)
    return ok(result)


@route("POST", "/api/auth/login", auth=AUTH_NONE, csrf=False)
def login(request, ctx, auth, params):
    result = accounts_service.login(ctx, request.json(), request)
    cookie = session_cookie(ctx.config, request, result["session_token"])
    user = result["user"]
    return json_response(
        {
            "authenticated": True,
            "user": user,
            "verification_required": not user["email_verified"],
            "notice": None if user["email_verified"] else (
                "Your email is not confirmed yet. Confirm it to buy memberships, message creators, "
                "or publish."
            ),
        },
        cookies=[cookie],
    )


@route("POST", "/api/auth/logout", auth=AUTH_REQUIRED)
def logout(request, ctx, auth, params):
    accounts_service.logout(ctx, auth, request)
    return json_response({"authenticated": False}, cookies=[cleared_session_cookie(ctx.config, request)])


@route("GET", "/api/auth/session", auth=AUTH_OPTIONAL)
def session_detail(request, ctx, auth, params):
    if auth is None:
        return ok({"authenticated": False})
    return ok(auth.public())


@route("GET", "/api/account/sessions", auth=AUTH_REQUIRED)
def list_sessions(request, ctx, auth, params):
    with ctx.db.connection() as conn:
        rows = accounts_service.user_sessions(conn, auth.user_id)
    return ok(
        {
            "items": [
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "last_seen_at": row["last_seen_at"],
                    "expires_at": row["expires_at"],
                    "user_agent": row["user_agent"],
                    "current": row["id"] == auth.session["id"],
                }
                for row in rows
            ],
            "notice": (
                "Velora stores only a keyed hash of the network address each session came from, never "
                "the raw address."
            ),
        }
    )


@route("POST", "/api/account/sessions/revoke-others", auth=AUTH_REQUIRED)
def revoke_other_sessions(request, ctx, auth, params):
    revoked = accounts_service.revoke_all_sessions(ctx, auth.user_id,
                                                  except_session_id=auth.session["id"])
    return ok({"revoked": revoked})


@route("PATCH", "/api/account/profile", auth=AUTH_REQUIRED)
def update_profile(request, ctx, auth, params):
    return ok({"user": accounts_service.update_profile(ctx, auth, request.json())})


@route("PUT", "/api/account/orientation", auth=AUTH_REQUIRED)
def update_orientation(request, ctx, auth, params):
    return ok(accounts_service.set_orientation(ctx, auth, request.json()))


@route("GET", "/api/account/privacy", auth=AUTH_REQUIRED)
def privacy_overview(request, ctx, auth, params):
    with ctx.db.connection() as conn:
        disclosures = accounts_service.orientation_disclosure_history(conn, auth.user_id)
        actions = conn.execute(
            "SELECT action, reason, actor_kind, created_at FROM account_actions WHERE user_id = ? "
            "ORDER BY id DESC LIMIT 25",
            (auth.user_id,),
        ).fetchall()
    return ok(
        {
            "user": user_private(auth.user),
            "orientation_disclosures": [dict(row) for row in disclosures],
            "account_actions": [dict(row) for row in actions],
            "explainers": {
                "orientation": (
                    "Sexual orientation is optional, private by default and never used for discovery "
                    "filters, search or analytics. 'Prefer not to say' and your own words are never "
                    "displayed publicly."
                ),
                "email": (
                    "Your email address is used for sign-in, verification and account notices. It is "
                    "never shown to creators, members or visitors."
                ),
                "retention": (
                    "Deactivating hides your page and signs you out. Deleting replaces your personal "
                    "details with anonymous placeholders; settled payment records are kept in "
                    "anonymised form because financial history is append-only."
                ),
                "data_minimisation": (
                    "Velora never asks for a phone number, billing address, government ID or wallet "
                    "seed phrase."
                ),
            },
        }
    )


@route("GET", "/api/account/export", auth=AUTH_REQUIRED)
def export_data(request, ctx, auth, params):
    return ok(accounts_service.export_personal_data(ctx, auth))


@route("POST", "/api/account/deactivate", auth=AUTH_REQUIRED)
def deactivate(request, ctx, auth, params):
    result = accounts_service.archive_account(ctx, auth, request.json(), actor_kind="self")
    return json_response({**result, "message": "Your account is deactivated and your page is hidden."},
                         cookies=[cleared_session_cookie(ctx.config, request)])


@route("POST", "/api/account/delete", auth=AUTH_REQUIRED)
def delete_account(request, ctx, auth, params):
    payload = request.json()
    password = payload.get("password")
    confirm = payload.get("confirm")
    if confirm != "delete my account":
        raise bad_request('Type "delete my account" to confirm.', code="validation_error", field="confirm")
    from ..security import verify_password

    if not isinstance(password, str) or not verify_password(password, auth.user["password_hash"]):
        raise bad_request("Enter your current password to delete your account.",
                          code="password_required", field="password")
    with ctx.db.transaction() as conn:
        result = accounts_service.anonymize_account(
            conn, user_id=auth.user_id, actor_user_id=auth.user_id, actor_kind="self",
            reason="self-service deletion request",
        )
    return json_response(
        {
            **result,
            "message": (
                "Your personal details were replaced with anonymous placeholders. Membership and "
                "payment records are retained in anonymised form, as required for financial integrity."
            ),
        },
        cookies=[cleared_session_cookie(ctx.config, request)],
    )


@route("GET", "/api/invitations/<str:token>", auth=AUTH_OPTIONAL)
def invitation_preview(request, ctx, auth, params):
    return ok(accounts_service.preview_invitation(ctx, params["token"]))


@route("POST", "/api/invitations/accept", auth=AUTH_OPTIONAL, csrf=False)
def invitation_accept(request, ctx, auth, params):
    result = accounts_service.accept_invitation(ctx, request.json(), request)
    return ok(result)


@route("GET", "/api/creators/applications/mine", auth=AUTH_REQUIRED)
def my_application(request, ctx, auth, params):
    return ok({"application": creators_service.my_application(ctx, auth)})


@route("POST", "/api/creators/apply", auth=AUTH_REQUIRED)
def apply_creator(request, ctx, auth, params):
    return json_response(creators_service.apply_as_creator(ctx, auth, request.json()), status=201)


@route("GET", "/api/memberships", auth=AUTH_REQUIRED)
def list_memberships(request, ctx, auth, params):
    return ok(memberships_overview(ctx, auth))


@route("GET", "/api/health", auth=AUTH_NONE)
def health(request, ctx, auth, params):
    from ..migrations import status as migration_status

    try:
        state = migration_status(ctx.db)
        db_ok = not state["pending"] and not state["problems"]
    except Exception:  # noqa: BLE001
        db_ok = False
        state = {"pending": ["unknown"], "problems": ["database unavailable"]}
    return json_response(
        {
            "ok": db_ok,
            "database": "ready" if db_ok else "needs_migrations",
            "migrations_applied": len(state.get("applied", [])),
            "email_configured": ctx.config.email_configured,
            "btcpay_configured": ctx.config.btcpay_configured,
        },
        status=200 if db_ok else 503,
    )


@route("GET", "/api/privacy/summary", auth=AUTH_NONE)
def privacy_summary(request, ctx, auth, params):
    """Public, plain-language privacy statement (also mirrored in the UI)."""
    return ok(
        {
            "collected": [
                "Display name, email address and a password hash.",
                "An 18+ self-attestation timestamp (not identity or age verification).",
                "Optional, private-by-default sexual orientation — never used for discovery filters.",
                "Creator page details, posts, tiers and membership records you create.",
                "Aggregate creator page view counts with no visitor identity.",
            ],
            "not_collected": [
                "No phone number, billing address or government ID.",
                "No wallet seed phrases, private keys or card details.",
                "No location data and no IP-geolocation gate.",
            ],
            "payments": (
                "Memberships are paid on-chain in a cryptocurrency or Tether token through the operator's "
                "hosted BTCPay Server. "
                "Velora stores the settled invoice, the frozen platform fee and an append-only ledger; "
                "BTCPay holds the checkout interaction."
            ),
            "rights": [
                "See and export your account data at any time.",
                "Turn optional orientation sharing off, which removes it from your public page.",
                "Deactivate your account so it can no longer sign in.",
                "Delete your personal details; financial history is retained in anonymised form.",
            ],
            "demo_data": "This instance ships with an empty database: no demo accounts, posts or payments.",
        }
    )
