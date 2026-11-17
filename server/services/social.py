"""Direct messages and reports.

Messaging rules (deliberately narrow, to keep the inbox safe and unambiguous):

* A member may open a thread with a creator **only** while they hold an active
  membership of that creator's page. This stops unsupporting accounts from
  cold-messaging creators.
* A creator may reply inside any thread on their own page.
* When a membership ends, the member keeps read access to the history they were
  part of but cannot send new messages until they support the creator again.
* Bodies are stored as plain text, length-bounded, and rendered with
  ``textContent`` in the browser.
"""

from __future__ import annotations

from .. import audit
from ..db import now_iso
from ..http import bad_request, conflict, forbidden, not_found
from ..serializers import message_public, report_public, thread_public
from ..validation import (
    MAX_MESSAGE,
    MAX_REPORT_DETAILS,
    ValidationError,
    clean_report_reason,
    clean_report_status,
    clean_report_target,
    clean_text,
)
from .accounts import Auth, require_active, require_admin, require_verified
from .creators import page_for_user
from .payments import expire_memberships

MIN_MESSAGE = 1


# ---------------------------------------------------------------------------
# Threads and messages
# ---------------------------------------------------------------------------


def _thread_row(conn, thread_id: int):
    return conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()


def _active_membership(conn, user_id: int, creator_id: int):
    row = conn.execute(
        """
        SELECT * FROM memberships
        WHERE user_id = ? AND creator_id = ? AND status = 'active' AND ends_at > ?
        ORDER BY ends_at DESC LIMIT 1
        """,
        (user_id, creator_id, now_iso()),
    ).fetchone()
    return dict(row) if row else None


def start_thread(ctx, auth: Auth, payload: dict) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("thread_create", auth.user_id)

    creator_id = payload.get("creator_id")
    if isinstance(creator_id, str) and creator_id.isdigit():
        creator_id = int(creator_id)
    if not isinstance(creator_id, int):
        raise bad_request("Choose a creator to message.", code="validation_error", field="creator_id")
    try:
        body = clean_text(payload.get("body"), field="body", required=True, min_length=MIN_MESSAGE,
                          max_length=MAX_MESSAGE, label="Message")
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    expire_memberships(ctx)
    with ctx.db.transaction() as conn:
        page = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (creator_id,)).fetchone()
        if page is None:
            raise not_found("Creator not found.")
        page = dict(page)
        if page["user_id"] == auth.user_id:
            raise conflict("You cannot start a thread with your own page.", code="self_thread")
        membership = _active_membership(conn, auth.user_id, creator_id)
        if membership is None:
            raise forbidden(
                "Direct messages are open to members who currently support this creator. "
                "Support a tier to start a conversation.",
                code="membership_required",
            )
        existing = conn.execute("SELECT * FROM threads WHERE member_id = ? AND creator_id = ?",
                                (auth.user_id, creator_id)).fetchone()
        if existing is not None:
            thread_id = dict(existing)["id"]
            created = False
        else:
            created_at = now_iso()
            thread_id = conn.execute(
                "INSERT INTO threads (member_id, creator_id, created_at, last_message_at) VALUES (?, ?, ?, ?)",
                (auth.user_id, creator_id, created_at, created_at),
            ).lastrowid
            created = True
        message_id = conn.execute(
            "INSERT INTO messages (thread_id, sender_id, body, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, auth.user_id, body, now_iso()),
        ).lastrowid
        conn.execute("UPDATE threads SET last_message_at = ?, archived_at = NULL WHERE id = ?",
                     (now_iso(), thread_id))
        audit.record(conn, action="message.thread_started" if created else "message.sent",
                     actor_user_id=auth.user_id, actor_role=auth.role, target_type="thread",
                     target_id=thread_id, meta={"creator_id": creator_id})
        thread = _thread_row(conn, thread_id)
        counterpart = _counterpart(conn, thread, auth)
        message = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        total = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE thread_id = ?",
                             (thread_id,)).fetchone()["c"]

    return {
        "thread": thread_public(thread, counterpart=counterpart),
        "message": message_public(message),
        "message_count": int(total),
        "created": created,
    }


def send_message(ctx, auth: Auth, thread_id: int, payload: dict) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("message_send", auth.user_id)
    try:
        body = clean_text(payload.get("body"), field="body", required=True, min_length=MIN_MESSAGE,
                          max_length=MAX_MESSAGE, label="Message")
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    expire_memberships(ctx)
    with ctx.db.transaction() as conn:
        thread = _thread_row(conn, thread_id)
        if thread is None:
            raise not_found("Conversation not found.")
        thread = dict(thread)
        page = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (thread["creator_id"],)).fetchone()
        if page is None:
            raise not_found("Conversation not found.")
        page = dict(page)
        is_creator = page["user_id"] == auth.user_id
        is_member = thread["member_id"] == auth.user_id
        if not (is_creator or is_member or auth.is_admin):
            raise forbidden("That conversation belongs to other accounts.", code="thread_private")
        if is_member:
            membership = _active_membership(conn, auth.user_id, thread["creator_id"])
            if membership is None:
                raise forbidden(
                    "Your membership of this creator has ended, so the conversation is read-only. "
                    "Messages you already received stay available; renew to write again.",
                    code="membership_required",
                )
        message_id = conn.execute(
            "INSERT INTO messages (thread_id, sender_id, body, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, auth.user_id, body, now_iso()),
        ).lastrowid
        conn.execute("UPDATE threads SET last_message_at = ? WHERE id = ?", (now_iso(), thread_id))
        audit.record(conn, action="message.sent", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="thread", target_id=thread_id)
        message = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return message_public(message)


def list_threads(ctx, auth: Auth) -> dict:
    require_active(auth)
    expire_memberships(ctx)
    with ctx.db.connection() as conn:
        page = page_for_user(conn, auth.user_id)
        threads = conn.execute(
            """
            SELECT * FROM threads
            WHERE member_id = ? OR creator_id = ?
            ORDER BY last_message_at DESC, id DESC
            LIMIT 100
            """,
            (auth.user_id, page["id"] if page else -1),
        ).fetchall()

        items = []
        for row in threads:
            thread = dict(row)
            unread = conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE thread_id = ? AND sender_id != ? AND read_at IS NULL",
                (thread["id"], auth.user_id),
            ).fetchone()["c"]
            last = conn.execute(
                "SELECT * FROM messages WHERE thread_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (thread["id"],),
            ).fetchone()
            counterpart = _counterpart(conn, thread, auth)
            membership_state = None
            if page and thread["creator_id"] == page["id"]:
                membership_state = "creator_view"
            else:
                membership_state = "active" if _active_membership(conn, thread["member_id"],
                                                                  thread["creator_id"]) else "ended"
            payload = thread_public(thread, counterpart=counterpart, unread=int(unread),
                                    membership_state=membership_state)
            if last is not None:
                snippet = dict(last)["body"]
                payload["preview"] = (snippet[:120] + "…") if len(snippet) > 120 else snippet
                payload["last_message_at"] = dict(last)["created_at"]
            else:
                payload["preview"] = None
            payload["can_send"] = bool(page and thread["creator_id"] == page["id"]) or membership_state == "active"
            items.append(payload)

    return {
        "items": items,
        "empty_state": None if items else {
            "title": "No conversations yet",
            "body": (
                "Members can start a conversation with a creator they support. Creators see those "
                "messages here and can reply."
            ),
        },
    }


def get_thread(ctx, auth: Auth, thread_id: int, *, mark_read: bool = True) -> dict:
    require_active(auth)
    with ctx.db.transaction() as conn:
        thread = _thread_row(conn, thread_id)
        if thread is None:
            raise not_found("Conversation not found.")
        thread = dict(thread)
        page = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (thread["creator_id"],)).fetchone()
        page = dict(page) if page else None
        is_creator = bool(page and page["user_id"] == auth.user_id)
        is_member = thread["member_id"] == auth.user_id
        if not (is_creator or is_member or auth.is_admin):
            raise forbidden("That conversation belongs to other accounts.", code="thread_private")

        messages = conn.execute(
            "SELECT * FROM messages WHERE thread_id = ? ORDER BY created_at ASC, id ASC LIMIT 500",
            (thread_id,),
        ).fetchall()
        if mark_read and messages:
            conn.execute(
                "UPDATE messages SET read_at = ? WHERE thread_id = ? AND sender_id != ? AND read_at IS NULL",
                (now_iso(), thread_id, auth.user_id),
            )
        counterpart = _counterpart(conn, thread, auth)
        membership = _active_membership(conn, thread["member_id"], thread["creator_id"])

    can_send = is_creator or bool(membership)
    return {
        "thread": thread_public(thread, counterpart=counterpart,
                                membership_state="active" if membership else "ended"),
        "messages": [message_public(row) for row in messages],
        "can_send": can_send,
        "read_only_reason": (
            None if can_send else
            "This creator's membership period has ended, so the conversation is read-only. "
            "Renew to write again — nothing you received is removed."
        ),
        "member_is_creator_view": is_creator,
    }


def _counterpart(conn, thread: dict, auth: Auth) -> dict:
    """Identity of the other party, without leaking email addresses."""
    page = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (thread["creator_id"],)).fetchone()
    page = dict(page) if page else None
    if page and page["user_id"] == auth.user_id:
        member = conn.execute("SELECT id, display_name FROM users WHERE id = ?", (thread["member_id"],)).fetchone()
        return {
            "kind": "member",
            "id": dict(member)["id"] if member else None,
            "display_name": dict(member)["display_name"] if member else "Member",
            "handle": None,
            "note": "Member email addresses are never shared with creators.",
        }
    member = conn.execute("SELECT id, display_name FROM users WHERE id = ?", (thread["member_id"],)).fetchone()
    return {
        "kind": "creator",
        "id": page["id"] if page else None,
        "display_name": page["page_name"] if page else "Creator",
        "handle": page["handle"] if page else None,
        "member_display_name": dict(member)["display_name"] if member else None,
    }


def archive_thread(ctx, auth: Auth, thread_id: int, archived: bool = True) -> dict:
    require_active(auth)
    with ctx.db.transaction() as conn:
        thread = _thread_row(conn, thread_id)
        if thread is None:
            raise not_found("Conversation not found.")
        thread = dict(thread)
        page = conn.execute("SELECT * FROM creator_pages WHERE id = ?", (thread["creator_id"],)).fetchone()
        allowed = thread["member_id"] == auth.user_id or (page and dict(page)["user_id"] == auth.user_id)
        if not allowed and not auth.is_admin:
            raise forbidden("That conversation belongs to other accounts.", code="thread_private")
        conn.execute("UPDATE threads SET archived_at = ? WHERE id = ?",
                     (now_iso() if archived else None, thread_id))
        audit.record(conn, action="message.thread_archived" if archived else "message.thread_unarchived",
                     actor_user_id=auth.user_id, actor_role=auth.role, target_type="thread",
                     target_id=thread_id)
    return {"archived": archived, "id": thread_id}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def create_report(ctx, auth: Auth, payload: dict) -> dict:
    require_active(auth)
    require_verified(auth)
    ctx.gate("report_create", auth.user_id)
    try:
        target_type = clean_report_target(payload.get("target_type"))
        reason = clean_report_reason(payload.get("reason_code"))
        details = clean_text(payload.get("details") or "", field="details", max_length=MAX_REPORT_DETAILS) or None
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    target_id = payload.get("target_id")
    if isinstance(target_id, str) and target_id.isdigit():
        target_id = int(target_id)
    if not isinstance(target_id, int):
        raise bad_request("Choose what you are reporting.", code="validation_error", field="target_id")

    with ctx.db.transaction() as conn:
        label = _derive_target_label(conn, target_type, target_id)
        if label is None:
            raise not_found("That content could not be found, so nothing was reported.")
        report_id = conn.execute(
            """
            INSERT INTO reports (reporter_user_id, target_type, target_id, target_label, reason_code, details,
                                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (auth.user_id, target_type, target_id, label, reason, details, now_iso(), now_iso()),
        ).lastrowid
        audit.record(conn, action="report.created", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="report", target_id=report_id,
                     meta={"target_type": target_type, "target_id": target_id, "reason": reason})
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    payload_out = report_public(row)
    payload_out["message"] = (
        "Thanks — the report was sent to the moderation queue. Reports are reviewed in order and the "
        "status updates here and in your reports list."
    )
    return payload_out


def _derive_target_label(conn, target_type: str, target_id: int) -> str | None:
    """Server-derived label so a report cannot be used to smuggle arbitrary text."""
    if target_type == "post":
        row = conn.execute("SELECT title FROM posts WHERE id = ?", (target_id,)).fetchone()
        return f"Post: {dict(row)['title'][:80]}" if row else None
    if target_type == "creator":
        row = conn.execute("SELECT page_name, handle FROM creator_pages WHERE id = ?", (target_id,)).fetchone()
        return f"Creator page: {dict(row)['page_name']} (/{dict(row)['handle']})" if row else None
    if target_type == "tier":
        row = conn.execute("SELECT name FROM tiers WHERE id = ?", (target_id,)).fetchone()
        return f"Tier: {dict(row)['name'][:60]}" if row else None
    if target_type == "user":
        row = conn.execute("SELECT display_name FROM users WHERE id = ?", (target_id,)).fetchone()
        return f"Account: {dict(row)['display_name'][:60]}" if row else None
    if target_type == "message":
        row = conn.execute("SELECT id, thread_id FROM messages WHERE id = ?", (target_id,)).fetchone()
        if not row:
            return None
        record = dict(row)
        return f"Message #{record['id']} in conversation #{record['thread_id']}"
    return None


def my_reports(ctx, auth: Auth) -> dict:
    require_active(auth)
    with ctx.db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM reports WHERE reporter_user_id = ? ORDER BY created_at DESC LIMIT 100",
            (auth.user_id,),
        ).fetchall()
    return {
        "items": [report_public(row) for row in rows],
        "statuses": {
            "open": "Received — waiting for a moderator",
            "reviewing": "Being reviewed",
            "resolved": "Resolved — action taken",
            "dismissed": "Reviewed — no action needed",
        },
        "empty_state": None if rows else {
            "title": "No reports filed",
            "body": "Reports you send about content or accounts appear here with their status.",
        },
    }


def report_target_preview(ctx, auth: Auth, target_type: str, target_id: int) -> dict:
    """Safe preview shown on the report form: no private data, no owner emails."""
    try:
        clean_report_target(target_type)
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None
    with ctx.db.connection() as conn:
        label = _derive_target_label(conn, target_type, int(target_id))
    if label is None:
        raise not_found("That content could not be found.")
    return {"target_type": target_type, "target_id": target_id, "label": label}


def admin_update_report(ctx, admin: Auth, report_id: int, payload: dict) -> dict:
    require_admin(admin)
    ctx.gate("admin_mutation", admin.user_id)
    try:
        status = clean_report_status(payload.get("status"))
        note = clean_text(payload.get("resolution_note") or "", field="resolution_note",
                          max_length=1000) or None
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if row is None:
            raise not_found("Report not found.")
        previous = dict(row)["status"]
        handled = status in ("resolved", "dismissed")
        conn.execute(
            """
            UPDATE reports SET status = ?, resolution_note = COALESCE(?, resolution_note),
                   handled_by = ?, handled_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, note, admin.user_id if handled else None, now_iso() if handled else None, now_iso(),
             report_id),
        )
        audit.record(conn, action="admin.report_updated", actor_user_id=admin.user_id, actor_role="admin",
                     target_type="report", target_id=report_id,
                     meta={"from": previous, "to": status, "note_present": bool(note)})
        updated = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    return report_public(updated, include_reporter=True)
