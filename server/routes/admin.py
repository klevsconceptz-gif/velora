"""Administrator console endpoints.

Every handler is declared ``auth=AUTH_ADMIN``, which the central pipeline turns
into ``require_admin`` — verified, active, role ``admin``. Handlers add their own
per-service checks on top so a mistake in one place cannot expose data.
"""

from __future__ import annotations

from ..db import now_iso
from ..http import bad_request, json_response
from ..routing import AUTH_ADMIN, route
from ..serializers import creator_application
from ..services import accounts as accounts_service
from ..services import admin as admin_service
from ..services import creators as creators_service
from ..services import posts as posts_service
from ..services.payments import handle_webhook  # noqa: F401 - re-exported for tests
from .base import ok


@route("GET", "/api/admin/overview", auth=AUTH_ADMIN)
def overview(request, ctx, auth, params):
    with ctx.db.connection() as conn:
        counts = {
            "users": _count(conn, "SELECT COUNT(*) AS c FROM users"),
            "creators": _count(conn, "SELECT COUNT(*) AS c FROM creator_pages"),
            "active_memberships": _count(
                conn, "SELECT COUNT(*) AS c FROM memberships WHERE status='active' AND ends_at > ?",
                (now_iso(),)),
            "open_reports": _count(conn, "SELECT COUNT(*) AS c FROM reports WHERE status IN ('open','reviewing')"),
            "pending_applications": _count(
                conn, "SELECT COUNT(*) AS c FROM creator_applications WHERE status='pending'"),
            "held_payments": _count(
                conn, "SELECT COUNT(*) AS c FROM payment_intents WHERE status='held'"),
            "admins": _count(
                conn, "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND status='active' "
                      "AND email_verified=1"),
            "settled_cents": _count(conn, "SELECT COALESCE(SUM(amount_cents),0) AS c FROM invoices"),
            "fee_cents": _count(conn, "SELECT COALESCE(SUM(platform_fee_cents),0) AS c FROM invoices"),
            "ledger_entries": _count(conn, "SELECT COUNT(*) AS c FROM ledger_entries"),
        }
        recent_audit = [
            dict(row)
            for row in conn.execute(
                """
                SELECT a.id, a.action, a.target_type, a.target_id, a.created_at, u.display_name AS actor_name
                FROM audit_log a LEFT JOIN users u ON u.id = a.actor_user_id
                ORDER BY a.id DESC LIMIT 12
                """
            )
        ]
    with ctx.db.connection() as conn:
        ledger_totals = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN direction='debit' THEN amount_cents END),0) AS debits, "
            "COALESCE(SUM(CASE WHEN direction='credit' THEN amount_cents END),0) AS credits FROM ledger_entries"
        ).fetchone()
    return ok(
        {
            "counts": counts,
            "ledger_balanced": int(ledger_totals["debits"]) == int(ledger_totals["credits"]),
            "recent_audit": recent_audit,
            "invariants": [
                "Settled invoices and ledger entries are append-only.",
                "Administrators cannot mark an invoice paid or fabricate a settlement.",
                "The last active, verified administrator cannot be removed.",
                "Administrators cannot promote or demote their own account.",
            ],
            "environment": ctx.config.environment,
            "payment_configured": ctx.config.btcpay_configured,
            "email_configured": ctx.config.email_configured,
        }
    )


def _count(conn, sql: str, params=()) -> int:
    return int(conn.execute(sql, params).fetchone()["c"])


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@route("GET", "/api/admin/users", auth=AUTH_ADMIN)
def users(request, ctx, auth, params):
    return ok(
        admin_service.list_users(
            ctx, auth,
            query=request.query_one("q"),
            role=request.query_one("role"),
            status=request.query_one("status"),
            page=request.query_int("page", 1, minimum=1) or 1,
            per_page=request.query_int("per_page", 25, minimum=1, maximum=100) or 25,
        )
    )


@route("GET", "/api/admin/users/<int:user_id>", auth=AUTH_ADMIN)
def user_detail(request, ctx, auth, params):
    return ok(admin_service.user_detail(ctx, auth, params["user_id"]))


@route("POST", "/api/admin/users/<int:user_id>/promote", auth=AUTH_ADMIN)
def promote(request, ctx, auth, params):
    return ok(admin_service.promote_user(ctx, auth, params["user_id"], request.json()))


@route("POST", "/api/admin/users/<int:user_id>/demote", auth=AUTH_ADMIN)
def demote(request, ctx, auth, params):
    return ok(admin_service.demote_user(ctx, auth, params["user_id"], request.json()))


@route("POST", "/api/admin/users/<int:user_id>/status", auth=AUTH_ADMIN)
def set_status(request, ctx, auth, params):
    return ok(admin_service.set_user_status(ctx, auth, params["user_id"], request.json()))


@route("POST", "/api/admin/users/<int:user_id>/anonymize", auth=AUTH_ADMIN)
def anonymize(request, ctx, auth, params):
    return ok(admin_service.anonymize_user(ctx, auth, params["user_id"], request.json()))


# ---------------------------------------------------------------------------
# Creators, applications, posts, tiers
# ---------------------------------------------------------------------------


@route("GET", "/api/admin/creators", auth=AUTH_ADMIN)
def creators_list(request, ctx, auth, params):
    return ok(admin_service.list_creators(ctx, auth, query=request.query_one("q"),
                                         status=request.query_one("status")))


@route("POST", "/api/admin/creators/<int:creator_id>/status", auth=AUTH_ADMIN)
def creator_status(request, ctx, auth, params):
    return ok(admin_service.set_creator_status(ctx, auth, params["creator_id"], request.json()))


@route("GET", "/api/admin/creators/<int:creator_id>/payout", auth=AUTH_ADMIN)
def creator_payout_address(request, ctx, auth, params):
    """Payout addresses are visible to the creator and authorized admins only."""
    return ok({"payout": creators_service.payout_for_actor(ctx, auth, params["creator_id"])})


@route("GET", "/api/admin/applications", auth=AUTH_ADMIN)
def applications(request, ctx, auth, params):
    status = request.query_one("status", "pending")
    with ctx.db.connection() as conn:
        rows = creators_service.applications_for(conn, None if status == "all" else status)
    return ok(
        {
            "items": [
                {
                    **{k: row[k] for k in row.keys() if k not in ("email", "wallets_json")},
                    "wallet_assets": creator_application(row)["wallet_assets"],
                    "applicant_email": row["email"],
                    "applicant_status": row["user_status"],
                }
                for row in rows
            ]
        }
    )


@route("POST", "/api/admin/applications/<int:application_id>/review", auth=AUTH_ADMIN)
def review_application(request, ctx, auth, params):
    return ok(creators_service.review_application(ctx, auth, params["application_id"], request.json()))


@route("GET", "/api/admin/posts", auth=AUTH_ADMIN)
def posts_list(request, ctx, auth, params):
    return ok(
        admin_service.list_posts(
            ctx, auth,
            query=request.query_one("q"),
            creator_id=request.query_int("creator_id", None),
            visibility=request.query_one("visibility"),
        )
    )


@route("POST", "/api/admin/posts/<int:post_id>/archive", auth=AUTH_ADMIN)
def archive_post(request, ctx, auth, params):
    payload = request.json()
    return ok(posts_service.admin_archive_post(ctx, auth, params["post_id"], payload.get("note")))


@route("POST", "/api/admin/posts/<int:post_id>/restore", auth=AUTH_ADMIN)
def restore_post(request, ctx, auth, params):
    return ok(posts_service.admin_restore_post(ctx, auth, params["post_id"]))


@route("GET", "/api/admin/tiers", auth=AUTH_ADMIN)
def tiers_list(request, ctx, auth, params):
    return ok(admin_service.list_tiers(ctx, auth, creator_id=request.query_int("creator_id", None)))


@route("POST", "/api/admin/tiers/<int:tier_id>/archive", auth=AUTH_ADMIN)
def archive_tier(request, ctx, auth, params):
    return ok(admin_service.archive_tier(ctx, auth, params["tier_id"], request.json()))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@route("GET", "/api/admin/reports", auth=AUTH_ADMIN)
def reports_list(request, ctx, auth, params):
    return ok(admin_service.list_reports(ctx, auth, status=request.query_one("status"),
                                         target_type=request.query_one("target_type")))


@route("POST", "/api/admin/reports/<int:report_id>", auth=AUTH_ADMIN)
def update_report(request, ctx, auth, params):
    from ..services import social as social_service

    return ok(social_service.admin_update_report(ctx, auth, params["report_id"], request.json()))


# ---------------------------------------------------------------------------
# Payments, ledger, audit, notes, invitations
# ---------------------------------------------------------------------------


@route("GET", "/api/admin/payments", auth=AUTH_ADMIN)
def payments(request, ctx, auth, params):
    return ok(admin_service.list_payments(ctx, auth, status=request.query_one("status"),
                                          query=request.query_one("q")))


@route("POST", "/api/admin/payments/<int:intent_id>/archive", auth=AUTH_ADMIN)
def archive_payment(request, ctx, auth, params):
    return ok(admin_service.archive_payment_attempt(ctx, auth, params["intent_id"], request.json()))


@route("GET", "/api/admin/ledger", auth=AUTH_ADMIN)
def ledger(request, ctx, auth, params):
    return ok(admin_service.ledger(ctx, auth, account=request.query_one("account"),
                                   entry_type=request.query_one("entry_type")))


@route("GET", "/api/admin/audit", auth=AUTH_ADMIN)
def audit_feed(request, ctx, auth, params):
    return ok(admin_service.audit_feed(ctx, auth, actor_user_id=request.query_int("actor_user_id", None),
                                       action=request.query_one("action"),
                                       limit=request.query_int("limit", 100, minimum=1, maximum=500) or 100))


@route("GET", "/api/admin/notes", auth=AUTH_ADMIN)
def notes(request, ctx, auth, params):
    target_type = request.query_one("target_type") or "user"
    target_id = request.query_int("target_id", None)
    if not target_id:
        raise bad_request("Provide target_id.", code="validation_error", field="target_id")
    return ok(admin_service.admin_notes(ctx, auth, target_type, target_id))


@route("POST", "/api/admin/notes", auth=AUTH_ADMIN)
def add_note(request, ctx, auth, params):
    payload = request.json()
    target_type = payload.get("target_type")
    target_id = payload.get("target_id")
    if not isinstance(target_type, str) or not isinstance(target_id, int):
        raise bad_request("Provide target_type and target_id.", code="validation_error", field="target_id")
    return json_response(
        admin_service.add_note(ctx, auth, target_type, target_id, payload.get("body")), status=201
    )


@route("GET", "/api/admin/invitations", auth=AUTH_ADMIN)
def invitations(request, ctx, auth, params):
    return ok(admin_service.list_invitations(ctx, auth))


@route("POST", "/api/admin/invitations", auth=AUTH_ADMIN)
def create_invitation(request, ctx, auth, params):
    return json_response(accounts_service.create_invitation(ctx, auth, request.json(), request),
                         status=201)


@route("POST", "/api/admin/invitations/<int:invitation_id>/revoke", auth=AUTH_ADMIN)
def revoke_invitation(request, ctx, auth, params):
    return ok(accounts_service.revoke_invitation(ctx, auth, params["invitation_id"], request))


@route("GET", "/api/admin/payments/events", auth=AUTH_ADMIN)
def webhook_events(request, ctx, auth, params):
    with ctx.db.connection() as conn:
        rows = conn.execute("SELECT * FROM webhook_events ORDER BY id DESC LIMIT 200").fetchall()
    from ..serializers import webhook_event_row

    return ok({"items": [webhook_event_row(row) for row in rows]})
