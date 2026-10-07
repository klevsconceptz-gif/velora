"""Administrator console operations.

Every function in this module starts by requiring an authorized administrator.
The console deliberately cannot:

* mark an invoice paid, fabricate a settlement, or edit a settled amount;
* delete or rewrite ledger history, settled invoices or recorded webhooks;
* change a payment intent's frozen amounts once settled;
* promote the acting administrator, or leave the platform without an administrator.

Administrators *can* add internal notes and archive unresolved payment attempts
that never produced an invoice.
"""

from __future__ import annotations

from .. import audit
from ..db import now_iso
from ..http import bad_request, conflict, forbidden, not_found
from ..serializers import (
    admin_audit_row,
    admin_note_row,
    admin_user_row,
    creator_application,
    creator_card,
    invoice_public,
    ledger_entry_public,
    payment_intent_public,
    report_public,
    tier_public,
    webhook_event_row,
)
from ..validation import MAX_NOTE, ValidationError, clean_text
from .accounts import Auth, anonymize_account, require_admin, restore_account

ADMIN_ROLES = ("member", "creator", "admin")


def add_note(ctx, admin: Auth, target_type: str, target_id: int, body: str | None) -> dict:
    """Internal notes are the *only* write administrators can add to a record."""
    if not body:
        raise bad_request("Write a short note first.", code="validation_error", field="note")
    try:
        cleaned = clean_text(body, field="note", required=True, max_length=MAX_NOTE, label="Note")
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    with ctx.db.transaction() as conn:
        note_id = audit.add_note(conn, admin_user_id=admin.user_id, target_type=target_type,
                                 target_id=target_id, body=cleaned)
        audit.record(conn, action="admin.note_added", actor_user_id=admin.user_id, actor_role="admin",
                     target_type=target_type, target_id=target_id)
    return {"id": note_id, "target_type": target_type, "target_id": target_id, "body": cleaned}


def _admin_count(conn) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'admin' AND status = 'active' AND email_verified = 1"
    ).fetchone()["c"])


# ---------------------------------------------------------------------------
# Customers and creators
# ---------------------------------------------------------------------------


def list_users(ctx, admin: Auth, *, query: str | None = None, role: str | None = None,
               status: str | None = None, page: int = 1, per_page: int = 25) -> dict:
    require_admin(admin)
    clauses, params = [], []
    if query:
        text = clean_text(query, field="q", max_length=60, allow_newlines=False).lower()
        if text:
            clauses.append("(LOWER(u.email) LIKE ? OR LOWER(u.display_name) LIKE ?)")
            params.extend([f"%{text}%", f"%{text}%"])
    if role in ADMIN_ROLES:
        clauses.append("u.role = ?")
        params.append(role)
    if status in ("active", "suspended", "archived", "anonymized"):
        clauses.append("u.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    per_page = max(1, min(100, per_page))
    page = max(1, page)
    with ctx.db.connection() as conn:
        total = int(conn.execute(f"SELECT COUNT(*) AS c FROM users u {where}", params).fetchone()["c"])  # noqa: S608
        rows = conn.execute(
            f"""
            SELECT u.*, c.id AS creator_page_id, c.handle, c.status AS page_status,
              (SELECT COUNT(*) FROM memberships m WHERE m.user_id = u.id AND m.status='active' AND m.ends_at > ?)
                AS active_memberships,
              (SELECT COALESCE(SUM(i.amount_cents),0) FROM invoices i WHERE i.user_id = u.id) AS settled_cents
            FROM users u LEFT JOIN creator_pages c ON c.user_id = u.id
            {where}
            ORDER BY u.created_at DESC, u.id DESC
            LIMIT ? OFFSET ?
            """,  # noqa: S608
            [now_iso(), *params, per_page, (page - 1) * per_page],
        ).fetchall()
        admin_count = _admin_count(conn)
    items = []
    for row in rows:
        record = dict(row)
        item = admin_user_row(record)
        item["active_memberships"] = int(record.get("active_memberships") or 0)
        item["settled_cents"] = int(record.get("settled_cents") or 0)
        item["page_status"] = record.get("page_status")
        items.append(item)
    return {
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "admin_count": admin_count,
    }


def user_detail(ctx, admin: Auth, user_id: int) -> dict:
    require_admin(admin)
    with ctx.db.connection() as conn:
        row = conn.execute(
            """
            SELECT u.*, c.id AS creator_page_id, c.handle, c.status AS page_status
            FROM users u LEFT JOIN creator_pages c ON c.user_id = u.id WHERE u.id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            raise not_found("Account not found.")
        record = dict(row)
        memberships = conn.execute(
            """
            SELECT m.*, c.handle, t.name AS tier_name FROM memberships m
            JOIN creator_pages c ON c.id = m.creator_id JOIN tiers t ON t.id = m.tier_id
            WHERE m.user_id = ? ORDER BY m.created_at DESC LIMIT 50
            """,
            (user_id,),
        ).fetchall()
        intents = conn.execute(
            "SELECT * FROM payment_intents WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
            (user_id,),
        ).fetchall()
        invoices = conn.execute(
            "SELECT * FROM invoices WHERE user_id = ? ORDER BY settled_at DESC LIMIT 50", (user_id,)
        ).fetchall()
        reports_against = conn.execute(
            "SELECT * FROM reports WHERE (target_type = 'user' AND target_id = ?) "
            "OR (target_type = 'creator' AND target_id = ?) ORDER BY created_at DESC LIMIT 25",
            (user_id, record.get("creator_page_id") or -1),
        ).fetchall()
        notes = audit.notes_for(conn, target_type="user", target_id=user_id, limit=25)
        history = audit.for_target(conn, target_type="user", target_id=user_id, limit=25)
        account_actions = conn.execute(
            "SELECT * FROM account_actions WHERE user_id = ? ORDER BY id DESC LIMIT 25", (user_id,)
        ).fetchall()
        applications = conn.execute(
            "SELECT * FROM creator_applications WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,)
        ).fetchall()

    item = admin_user_row(record)
    item["bio"] = record.get("bio")
    item["email_verified_at"] = record.get("email_verified_at")
    item["adult_attested_at"] = record.get("adult_attested_at")
    item["orientation_set"] = bool(record.get("orientation_value") or record.get("orientation_self_text"))
    return {
        # Email is shown here because this route is administrator-only.
        "user": item,
        "memberships": [dict(m) for m in memberships],
        "payment_intents": [payment_intent_public(i) for i in intents],
        "invoices": [invoice_public(i) for i in invoices],
        "reports": [report_public(r, include_reporter=True) for r in reports_against],
        "notes": [admin_note_row(n) for n in notes],
        "audit": [admin_audit_row(a) for a in history],
        "account_actions": [dict(a) for a in account_actions],
        "applications": [creator_application(a) for a in applications],
        "privacy_notice": (
            "Administrators see account emails for support and safety work. Optional orientation values "
            "are not shown here: only whether one is set."
        ),
    }


def promote_user(ctx, admin: Auth, user_id: int, payload: dict) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    role = (payload.get("role") or "").strip().lower()
    if role not in ("creator", "admin"):
        raise bad_request("Promote to creator or administrator.", code="validation_error", field="role")
    if user_id == admin.user_id:
        raise forbidden(
            "Administrators cannot promote their own account. Ask another administrator to do it.",
            code="self_promotion_blocked",
        )
    reason = clean_text(payload.get("reason") or "", field="reason", max_length=300) or None

    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise not_found("Account not found.")
        user = dict(row)
        if user["status"] != "active":
            raise conflict("Only an active account can be promoted.", code="account_inactive")
        if not user["email_verified"]:
            raise conflict(
                "This account must confirm its email address before receiving a role.",
                code="email_verification_required",
            )
        if user["role"] == role:
            raise conflict(f"Account is already a {role}.", code="no_change")
        conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, now_iso(), user_id))
        audit.record(conn, action="admin.user_promoted", actor_user_id=admin.user_id, actor_role="admin",
                     target_type="user", target_id=user_id,
                     meta={"from": user["role"], "to": role, "reason": reason})
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return admin_user_row(updated)


def demote_user(ctx, admin: Auth, user_id: int, payload: dict) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    target_role = (payload.get("role") or "member").strip().lower()
    if target_role not in ("member", "creator"):
        raise bad_request("Demote to member or creator.", code="validation_error", field="role")
    if user_id == admin.user_id:
        raise forbidden(
            "Administrators cannot change their own role. Ask another administrator.",
            code="self_demotion_blocked",
        )
    reason = clean_text(payload.get("reason") or "", field="reason", max_length=300) or None

    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise not_found("Account not found.")
        user = dict(row)
        if user["role"] == "admin" and _admin_count(conn) <= 1:
            raise conflict(
                "Velora must keep at least one active, verified administrator.",
                code="last_administrator",
            )
        conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                     (target_role, now_iso(), user_id))
        audit.record(conn, action="admin.user_demoted", actor_user_id=admin.user_id, actor_role="admin",
                     target_type="user", target_id=user_id,
                     meta={"from": user["role"], "to": target_role, "reason": reason})
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return admin_user_row(updated)


def set_user_status(ctx, admin: Auth, user_id: int, payload: dict) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    status = (payload.get("status") or "").strip().lower()
    if status not in ("active", "suspended", "archived"):
        raise bad_request("Choose active, suspended or archived.", code="validation_error", field="status")
    if user_id == admin.user_id:
        raise forbidden("Administrators cannot change their own account status.",
                        code="self_status_change_blocked")
    reason = clean_text(payload.get("reason") or "", field="reason", max_length=300) or None

    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise not_found("Account not found.")
        user = dict(row)
        if user["role"] == "admin" and status != "active" and _admin_count(conn) <= 1:
            raise conflict("Velora must keep at least one active, verified administrator.",
                           code="last_administrator")
        if status == "active":
            return restore_account(conn, admin, user_id, reason)
        conn.execute(
            "UPDATE users SET status = ?, archived_at = CASE WHEN ? = 'archived' THEN ? ELSE archived_at END, "
            "updated_at = ? WHERE id = ?",
            (status, status, now_iso(), now_iso(), user_id),
        )
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                     (now_iso(), user_id))
        if status == "archived":
            conn.execute("UPDATE creator_pages SET status = 'paused', updated_at = ? WHERE user_id = ?",
                         (now_iso(), user_id))
        audit.record(conn, action=f"admin.user_{status}", actor_user_id=admin.user_id, actor_role="admin",
                     target_type="user", target_id=user_id, meta={"reason": reason})
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return admin_user_row(updated)


def anonymize_user(ctx, admin: Auth, user_id: int, payload: dict) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    if user_id == admin.user_id:
        raise forbidden("Administrators cannot anonymize their own account.", code="self_anonymize_blocked")
    reason = clean_text(payload.get("reason") or "", field="reason", max_length=300) or None
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise not_found("Account not found.")
        user = dict(row)
        if user["role"] == "admin" and _admin_count(conn) <= 1:
            raise conflict("Velora must keep at least one active, verified administrator.",
                           code="last_administrator")
        result = anonymize_account(conn, user_id=user_id, actor_user_id=admin.user_id, actor_kind="admin",
                                   reason=reason)
    return result


# ---------------------------------------------------------------------------
# Creator pages, posts, tiers
# ---------------------------------------------------------------------------


def list_creators(ctx, admin: Auth, *, query: str | None = None, status: str | None = None) -> dict:
    require_admin(admin)
    clauses, params = [], []
    if query:
        text = clean_text(query, field="q", max_length=60, allow_newlines=False).lower()
        clauses.append("(LOWER(c.page_name) LIKE ? OR LOWER(c.handle) LIKE ?)")
        params.extend([f"%{text}%", f"%{text}%"])
    if status in ("active", "paused", "archived"):
        clauses.append("c.status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with ctx.db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT c.*, u.display_name, u.email, u.status AS owner_status,
              (SELECT COUNT(*) FROM tiers t WHERE t.creator_id = c.id AND t.is_active = 1) AS active_tiers,
              (SELECT COUNT(*) FROM posts p WHERE p.creator_id = c.id AND p.status = 'published') AS published_posts,
              (SELECT COUNT(*) FROM memberships m WHERE m.creator_id = c.id AND m.status='active' AND m.ends_at > ?)
                AS active_members,
              (SELECT COALESCE(SUM(i.amount_cents),0) FROM invoices i WHERE i.creator_id = c.id) AS gross_cents,
              (SELECT COUNT(*) FROM creator_wallets cp WHERE cp.creator_id = c.id) AS payout_on_file
            FROM creator_pages c JOIN users u ON u.id = c.user_id
            {where}
            ORDER BY c.created_at DESC LIMIT 200
            """,  # noqa: S608
            [now_iso(), *params],
        ).fetchall()
    return {
        "items": [
            {
                **creator_card(row, owner_row={"display_name": row["display_name"],
                                               "created_at": row["created_at"], "id": None,
                                               "orientation_value": None,
                                               "orientation_visibility": "private"}),
                "owner_email": row["email"],
                "owner_status": row["owner_status"],
                "active_tiers": int(row["active_tiers"]),
                "published_posts": int(row["published_posts"]),
                "active_members": int(row["active_members"]),
                "gross_cents": int(row["gross_cents"]),
                "payout_address_on_file": bool(row["payout_on_file"]),
            }
            for row in rows
        ]
    }


def set_creator_status(ctx, admin: Auth, creator_id: int, payload: dict) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    status = (payload.get("status") or "").strip().lower()
    if status not in ("active", "paused", "archived"):
        raise bad_request("Choose active, paused or archived.", code="validation_error", field="status")
    reason = clean_text(payload.get("reason") or "", field="reason", max_length=300) or None
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (creator_id,)).fetchone()
        if row is None:
            raise not_found("Creator page not found.")
        conn.execute(
            "UPDATE creator_pages SET status = ?, archived_at = ?, updated_at = ? WHERE id = ?",
            (status, now_iso() if status == "archived" else None, now_iso(), creator_id),
        )
        audit.record(conn, action="admin.creator_status_changed", actor_user_id=admin.user_id,
                     actor_role="admin", target_type="creator_page", target_id=creator_id,
                     meta={"to": status, "reason": reason})
        updated = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (creator_id,)).fetchone()
    return {"id": creator_id, "status": dict(updated)["status"]}


def list_posts(ctx, admin: Auth, *, query: str | None = None, creator_id: int | None = None,
               visibility: str | None = None) -> dict:
    require_admin(admin)
    clauses, params = [], []
    if query:
        text = clean_text(query, field="q", max_length=80, allow_newlines=False).lower()
        clauses.append("LOWER(p.title) LIKE ?")
        params.append(f"%{text}%")
    if creator_id:
        clauses.append("p.creator_id = ?")
        params.append(int(creator_id))
    if visibility in ("public", "members"):
        clauses.append("p.visibility = ?")
        params.append(visibility)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with ctx.db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.creator_id, p.title, p.status, p.visibility, p.published_at, p.created_at,
                   p.updated_at, p.admin_archived, p.admin_archive_note,
                   c.handle, c.page_name,
                   (SELECT COUNT(*) FROM post_media m WHERE m.post_id = p.id) AS media_count
            FROM posts p JOIN creator_pages c ON c.id = p.creator_id
            {where}
            ORDER BY p.created_at DESC LIMIT 200
            """,  # noqa: S608
            params,
        ).fetchall()
    return {
        "items": [
            {
                **dict(row),
                "admin_archived": bool(row["admin_archived"]),
                "url": f"/c/{row['handle']}/post/{row['id']}",
            }
            for row in rows
        ]
    }


def list_tiers(ctx, admin: Auth, *, creator_id: int | None = None) -> dict:
    require_admin(admin)
    params: list = []
    clause = ""
    if creator_id:
        clause = "WHERE t.creator_id = ?"
        params.append(int(creator_id))
    with ctx.db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT t.*, c.handle, c.page_name,
              (SELECT COUNT(*) FROM memberships m WHERE m.tier_id = t.id AND m.status='active' AND m.ends_at > ?)
                AS active_members
            FROM tiers t JOIN creator_pages c ON c.id = t.creator_id
            {clause}
            ORDER BY t.creator_id, t.position LIMIT 300
            """,  # noqa: S608
            [now_iso(), *params],
        ).fetchall()
    items = []
    for row in rows:
        item = tier_public(row)
        item["handle"] = row["handle"]
        item["page_name"] = row["page_name"]
        item["active_members"] = int(row["active_members"])
        items.append(item)
    return {"items": items}


def archive_tier(ctx, admin: Auth, tier_id: int, payload: dict) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    reason = clean_text(payload.get("reason") or "", field="reason", max_length=300) or None
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM tiers WHERE id = ?", (tier_id,)).fetchone()
        if row is None:
            raise not_found("Tier not found.")
        if not dict(row)["is_active"]:
            raise conflict("That tier is already archived.", code="no_change")
        conn.execute("UPDATE tiers SET is_active = 0, archived_at = ?, updated_at = ? WHERE id = ?",
                     (now_iso(), now_iso(), tier_id))
        audit.record(conn, action="admin.tier_archived", actor_user_id=admin.user_id, actor_role="admin",
                     target_type="tier", target_id=tier_id, meta={"reason": reason})
        updated = conn.execute("SELECT * FROM tiers WHERE id = ?", (tier_id,)).fetchone()
    payload_out = tier_public(updated)
    payload_out["notice"] = (
        "Archiving a tier stops new purchases. Existing members keep the access they already paid for."
    )
    return payload_out


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def list_reports(ctx, admin: Auth, *, status: str | None = None, target_type: str | None = None) -> dict:
    require_admin(admin)
    clauses, params = [], []
    if status in ("open", "reviewing", "resolved", "dismissed"):
        clauses.append("r.status = ?")
        params.append(status)
    if target_type in ("user", "creator", "post", "message", "tier"):
        clauses.append("r.target_type = ?")
        params.append(target_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with ctx.db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT r.*, u.display_name AS reporter_name, u.email AS reporter_email,
                   a.display_name AS handler_name
            FROM reports r
            LEFT JOIN users u ON u.id = r.reporter_user_id
            LEFT JOIN users a ON a.id = r.handled_by
            {where}
            ORDER BY (r.status = 'open') DESC, r.created_at DESC LIMIT 200
            """,  # noqa: S608
            params,
        ).fetchall()
        counts = {
            row["status"]: row["c"]
            for row in conn.execute("SELECT status, COUNT(*) AS c FROM reports GROUP BY status").fetchall()
        }
    return {
        "items": [
            {
                **report_public(row, include_reporter=True),
                "reporter_name": row["reporter_name"],
                "handler_name": row["handler_name"],
                "target_url": _target_url(ctx, row["target_type"], row["target_id"]),
            }
            for row in rows
        ],
        "counts": counts,
    }


def _target_url(ctx, target_type: str, target_id: int) -> str | None:
    with ctx.db.connection() as conn:
        if target_type == "post":
            row = conn.execute(
                "SELECT p.id, c.handle FROM posts p JOIN creator_pages c ON c.id = p.creator_id WHERE p.id = ?",
                (target_id,),
            ).fetchone()
            return f"/c/{row['handle']}/post/{row['id']}" if row else None
        if target_type == "creator":
            row = conn.execute("SELECT handle FROM creator_pages WHERE id = ?", (target_id,)).fetchone()
            return f"/c/{row['handle']}" if row else None
        if target_type in ("user", "message", "tier"):
            return None
    return None


# ---------------------------------------------------------------------------
# Payments and the ledger
# ---------------------------------------------------------------------------


def list_payments(ctx, admin: Auth, *, status: str | None = None, query: str | None = None) -> dict:
    require_admin(admin)
    clauses, params = [], []
    if status in ("pending", "processing", "settled", "held", "expired", "cancelled", "archived"):
        clauses.append("i.status = ?")
        params.append(status)
    if query:
        text = clean_text(query, field="q", max_length=80, allow_newlines=False)
        clauses.append("(i.order_ref LIKE ? OR i.btcpay_invoice_id LIKE ?)")
        params.extend([f"%{text}%", f"%{text}%"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with ctx.db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT i.*, u.display_name AS member_name, u.email AS member_email,
                   c.handle, c.page_name, t.name AS tier_name,
                   inv.id AS invoice_row_id, inv.settled_at AS invoice_settled_at
            FROM payment_intents i
            JOIN users u ON u.id = i.user_id
            JOIN creator_pages c ON c.id = i.creator_id
            JOIN tiers t ON t.id = i.tier_id
            LEFT JOIN invoices inv ON inv.payment_intent_id = i.id
            {where}
            ORDER BY i.created_at DESC LIMIT 200
            """,  # noqa: S608
            params,
        ).fetchall()
        webhooks = conn.execute(
            "SELECT * FROM webhook_events ORDER BY id DESC LIMIT 50"
        ).fetchall()
        summaries = {
            "settled_cents": int(conn.execute(
                "SELECT COALESCE(SUM(amount_cents),0) AS c FROM invoices").fetchone()["c"]),
            "fee_cents": int(conn.execute(
                "SELECT COALESCE(SUM(platform_fee_cents),0) AS c FROM invoices").fetchone()["c"]),
            "creator_net_cents": int(conn.execute(
                "SELECT COALESCE(SUM(creator_net_cents),0) AS c FROM invoices").fetchone()["c"]),
            "held_count": int(conn.execute(
                "SELECT COUNT(*) AS c FROM payment_intents WHERE status = 'held'").fetchone()["c"]),
            "pending_count": int(conn.execute(
                "SELECT COUNT(*) AS c FROM payment_intents WHERE status IN ('pending','processing')"
            ).fetchone()["c"]),
        }
    items = []
    for row in rows:
        item = payment_intent_public(row)
        item["member_name"] = row["member_name"]
        item["member_email"] = row["member_email"]
        item["creator"] = {"id": row["creator_id"], "handle": row["handle"], "page_name": row["page_name"]}
        item["tier_name"] = row["tier_name"]
        item["invoice_row_id"] = row["invoice_row_id"]
        items.append(item)
    return {
        "items": items,
        "summaries": summaries,
        "webhooks": [webhook_event_row(w) for w in webhooks],
        "immutability_notice": (
            "Settled invoices, their amounts, the platform fee and the ledger are append-only. "
            "Administrators can add internal notes and archive unresolved attempts, but cannot mark an "
            "invoice paid or change settled history."
        ),
    }


def archive_payment_attempt(ctx, admin: Auth, intent_id: int, payload: dict) -> dict:
    """Archive an unresolved attempt that has no settled invoice. Never touches money history."""
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    reason = clean_text(payload.get("reason") or "", field="reason", max_length=300) or None
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM payment_intents WHERE id = ?", (intent_id,)).fetchone()
        if row is None:
            raise not_found("Payment attempt not found.")
        intent = dict(row)
        settled = conn.execute("SELECT id FROM invoices WHERE payment_intent_id = ?", (intent_id,)).fetchone()
        if intent["status"] == "settled" or settled is not None:
            raise conflict(
                "That payment is settled. Settled payment history is append-only and cannot be archived.",
                code="settled_payment_immutable",
            )
        conn.execute(
            "UPDATE payment_intents SET status = 'archived', archived_at = ?, last_error = COALESCE(last_error, ?), "
            "updated_at = ? WHERE id = ?",
            (now_iso(), reason, now_iso(), intent_id),
        )
        audit.record(conn, action="admin.payment_attempt_archived", actor_user_id=admin.user_id,
                     actor_role="admin", target_type="payment_intent", target_id=intent_id,
                     meta={"reason": reason, "previous_status": intent["status"]})
        updated = conn.execute("SELECT * FROM payment_intents WHERE id = ?", (intent_id,)).fetchone()
    return payment_intent_public(updated)


def ledger(ctx, admin: Auth, *, account: str | None = None, entry_type: str | None = None) -> dict:
    require_admin(admin)
    clauses, params = [], []
    if account:
        clauses.append("l.account = ?")
        params.append(clean_text(account, field="account", max_length=60, allow_newlines=False))
    if entry_type in ("member_payment", "platform_fee", "creator_earning", "adjustment_note"):
        clauses.append("l.entry_type = ?")
        params.append(entry_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with ctx.db.connection() as conn:
        rows = conn.execute(
            f"""
            SELECT l.*, i.order_ref AS invoice_order_ref, c.handle
            FROM ledger_entries l
            LEFT JOIN invoices i ON i.id = l.invoice_id
            LEFT JOIN creator_pages c ON c.id = i.creator_id
            {where}
            ORDER BY l.id DESC LIMIT 300
            """,  # noqa: S608
            params,
        ).fetchall()
        totals = conn.execute(
            """
            SELECT account, direction, COALESCE(SUM(amount_cents),0) AS total, COUNT(*) AS entries
            FROM ledger_entries GROUP BY account, direction ORDER BY account
            """
        ).fetchall()
        debits = sum(int(row["total"]) for row in totals if row["direction"] == "debit")
        credits = sum(int(row["total"]) for row in totals if row["direction"] == "credit")
    return {
        "items": [
            {**ledger_entry_public(row),
             "order_ref": dict(row).get("invoice_order_ref") or dict(row).get("order_ref"),
             "handle": dict(row).get("handle")}
            for row in rows
        ],
        "totals": [dict(row) for row in totals],
        "balanced": debits == credits,
        "debit_cents": debits,
        "credit_cents": credits,
        "notice": (
            "The ledger is append-only. Entries are written once, at settlement, and are never edited "
            "or deleted — not even by an administrator."
        ),
    }


def audit_feed(ctx, admin: Auth, *, actor_user_id: int | None = None, action: str | None = None,
               limit: int = 100) -> dict:
    require_admin(admin)
    with ctx.db.connection() as conn:
        rows = audit.recent(conn, limit=limit, actor_user_id=actor_user_id,
                            action=action if action else None)
    return {"items": [admin_audit_row(row) for row in rows]}


def list_invitations(ctx, admin: Auth) -> dict:
    require_admin(admin)
    with ctx.db.connection() as conn:
        rows = conn.execute(
            """
            SELECT i.*, u.display_name AS invited_by_name, a.display_name AS used_by_name
            FROM admin_invitations i
            LEFT JOIN users u ON u.id = i.invited_by
            LEFT JOIN users a ON a.id = i.used_by
            ORDER BY i.created_at DESC LIMIT 100
            """
        ).fetchall()
    from .accounts import invitation_state

    return {
        "items": [
            {
                "id": row["id"],
                "role": row["role"],
                "email_bound": bool(row["email"]),
                "masked_email": _mask(row["email"]) if row["email"] else None,
                "state": invitation_state(dict(row)),
                "invited_by": row["invited_by_name"],
                "used_by": row["used_by_name"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            }
            for row in rows
        ]
    }


def _mask(email: str | None) -> str | None:
    from ..security import mask_email

    return mask_email(email) if email else None


def admin_notes(ctx, admin: Auth, target_type: str, target_id: int) -> dict:
    require_admin(admin)
    with ctx.db.connection() as conn:
        notes = audit.notes_for(conn, target_type=target_type, target_id=target_id, limit=100)
    return {"items": [admin_note_row(note) for note in notes]}


__all__ = [
    "ADMIN_ROLES",
    "add_note",
    "REQUIRED_ADMIN_ERROR",
    "anonymize_user",
    "archive_payment_attempt",
    "archive_tier",
    "audit_feed",
    "demote_user",
    "ledger",
    "list_creators",
    "list_invitations",
    "list_payments",
    "list_posts",
    "list_reports",
    "list_tiers",
    "list_users",
    "promote_user",
    "set_creator_status",
    "set_user_status",
    "user_detail",
]
