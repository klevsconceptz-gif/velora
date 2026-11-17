"""Append-only audit trail and admin notes."""

from __future__ import annotations

import json

from .db import now_iso


def record(
    conn,
    *,
    action: str,
    actor_user_id: int | None = None,
    actor_role: str | None = None,
    target_type: str | None = None,
    target_id: int | None = None,
    meta: dict | None = None,
    client_hash: str | None = None,
) -> int:
    """Append one audit row.

    ``meta`` must contain only non-sensitive identifiers and short codes. Values
    that could carry secrets are the caller's responsibility to omit; see the
    callers in ``server/services`` for the conventions used.
    """
    payload = None
    if meta:
        payload = json.dumps(meta, separators=(",", ":"), sort_keys=True)[:2000]
    cursor = conn.execute(
        """
        INSERT INTO audit_log (actor_user_id, actor_role, action, target_type, target_id, meta, client_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (actor_user_id, actor_role, action, target_type, target_id, payload, client_hash, now_iso()),
    )
    return cursor.lastrowid


def add_note(conn, *, admin_user_id: int, target_type: str, target_id: int, body: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO admin_notes (admin_user_id, target_type, target_id, body, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (admin_user_id, target_type, target_id, body, now_iso()),
    )
    return cursor.lastrowid


def recent(conn, *, limit: int = 50, actor_user_id: int | None = None, action: str | None = None):
    clauses = []
    params: list = []
    if actor_user_id is not None:
        clauses.append("a.actor_user_id = ?")
        params.append(actor_user_id)
    if action:
        clauses.append("a.action = ?")
        params.append(action)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(500, limit)))
    return conn.execute(
        f"""
        SELECT a.*, u.display_name AS actor_name, u.email AS actor_email
        FROM audit_log a
        LEFT JOIN users u ON u.id = a.actor_user_id
        {where}
        ORDER BY a.id DESC
        LIMIT ?
        """,  # noqa: S608 - only static fragments are interpolated, values are bound
        params,
    ).fetchall()


def for_target(conn, *, target_type: str, target_id: int, limit: int = 50):
    return conn.execute(
        """
        SELECT a.*, u.display_name AS actor_name
        FROM audit_log a
        LEFT JOIN users u ON u.id = a.actor_user_id
        WHERE a.target_type = ? AND a.target_id = ?
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (target_type, target_id, max(1, min(200, limit))),
    ).fetchall()


def notes_for(conn, *, target_type: str, target_id: int, limit: int = 50):
    return conn.execute(
        """
        SELECT n.*, u.display_name AS admin_name
        FROM admin_notes n
        LEFT JOIN users u ON u.id = n.admin_user_id
        WHERE n.target_type = ? AND n.target_id = ?
        ORDER BY n.id DESC
        LIMIT ?
        """,
        (target_type, target_id, max(1, min(200, limit))),
    ).fetchall()
