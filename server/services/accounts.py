"""Accounts: signup, sessions, email verification, invitations, privacy controls.

Design notes
------------
* Signup collects exactly four things: display name, email, password, and an
  explicit 18+ self-attestation. No phone number, no billing address, no
  government ID, no location.
* The 18+ checkbox records a self-attestation. It is not identity or age
  verification and no code or copy describes it as verified age.
* Password hashing and token handling live in :mod:`server.security`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import audit
from ..db import now_iso
from ..http import ApiError, bad_request, conflict, forbidden, not_found, unauthorized, unavailable
from ..security import (
    constant_time_equal,
    fingerprint,
    hash_password,
    mask_email,
    new_token,
    token_digest,
    verify_password,
)
from ..serializers import user_private
from ..validation import (
    ORIENTATION_PREFER_NOT_TO_SAY,
    ValidationError,
    clean_bool,
    clean_display_name,
    clean_email,
    clean_orientation,
    clean_text,
    orientation_kind,
    orientation_public_eligible,
    validate_new_password,
    MAX_BIO,
)

SESSION_COOKIE = "velora_session"
CSRF_HEADER = "x-velora-csrf"
ROLE_ORDER = {"member": 0, "creator": 1, "admin": 2}


@dataclass
class Auth:
    user: dict
    session: dict
    csrf_token: str

    @property
    def user_id(self) -> int:
        return int(self.user["id"])

    @property
    def role(self) -> str:
        return self.user["role"]

    @property
    def is_admin(self) -> bool:
        return self.user["role"] == "admin"

    @property
    def verified(self) -> bool:
        return bool(self.user["email_verified"])

    @property
    def is_active(self) -> bool:
        return self.user["status"] == "active"

    def public(self) -> dict:
        return {
            "authenticated": True,
            "user": user_private(self.user),
            "csrf_token": self.csrf_token,
            "can_act": self.verified and self.is_active,
            "capabilities": capabilities(self.user),
        }


def capabilities(user_row) -> dict:
    user = dict(user_row)
    role = user["role"]
    verified = bool(user["email_verified"])
    active = user["status"] == "active"
    return {
        "verified": verified,
        "active": active,
        "member": active and verified,
        "creator": active and verified and role in ("creator", "admin"),
        "admin": active and verified and role == "admin",
        "orientation_shareable": True,
    }


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


def resolve_auth(ctx, request) -> Auth | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    digest = token_digest(raw)
    with ctx.db.connection() as conn:
        row = conn.execute(
            """
            SELECT s.*, u.id AS u_id
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.revoked_at IS NULL
            """,
            (digest,),
        ).fetchone()
        if row is None:
            return None
        session = dict(row)
        if session["expires_at"] <= now_iso():
            conn.execute("UPDATE sessions SET revoked_at = ? WHERE id = ?", (now_iso(), session["id"]))
            return None
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        if user_row is None:
            return None
        user = dict(user_row)
        if user["status"] in ("suspended", "archived", "anonymized"):
            # Suspended/deactivated accounts keep no live sessions.
            conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                         (now_iso(), user["id"]))
            return None
        last_seen = session.get("last_seen_at") or ""
        if last_seen[:16] != now_iso()[:16]:
            conn.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now_iso(), session["id"]))
    return Auth(user=user, session=session, csrf_token=session["csrf_token"])


def require_auth(ctx, request) -> Auth:
    auth = resolve_auth(ctx, request)
    if auth is None:
        raise unauthorized()
    return auth


def require_verified(auth: Auth) -> Auth:
    if not auth.verified:
        error = forbidden(
            "Confirm your email address to use this part of Velora. "
            "You can resend the confirmation email from your account page.",
            code="email_verification_required",
        )
        raise error
    return auth


def require_admin(auth: Auth) -> Auth:
    if not auth.is_admin:
        raise forbidden("Administrator access is required.", code="admin_required")
    require_verified(auth)
    return auth


def require_active(auth: Auth) -> Auth:
    if not auth.is_active:
        raise forbidden("This account is not active.", code="account_inactive")
    return auth


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def create_session(conn, ctx, user_id: int, request) -> tuple[str, dict]:
    raw_token = new_token()
    csrf = new_token(24)
    ip = request.client_ip(ctx.config.trust_proxy)
    client_hash = fingerprint(ctx.config.secret_key, "ip", ip or "unknown")
    expires = _future(ctx.config.session_ttl_seconds)
    row = {
        "token_hash": token_digest(raw_token),
        "user_id": user_id,
        "csrf_token": csrf,
        "client_fingerprint": client_hash,
        "user_agent": request.user_agent(),
        "created_at": now_iso(),
        "last_seen_at": now_iso(),
        "expires_at": expires,
    }
    conn.execute(
        """
        INSERT INTO sessions (token_hash, user_id, csrf_token, client_fingerprint, user_agent,
                              created_at, last_seen_at, expires_at)
        VALUES (:token_hash, :user_id, :csrf_token, :client_fingerprint, :user_agent,
                :created_at, :last_seen_at, :expires_at)
        """,
        row,
    )
    return raw_token, row


def _future(seconds: int) -> str:
    from ..db import future_iso

    return future_iso(seconds)


def revoke_session(ctx, raw_token: str) -> None:
    if not raw_token:
        return
    with ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (now_iso(), token_digest(raw_token)),
        )


def revoke_all_sessions(ctx, user_id: int, *, except_session_id: int | None = None) -> int:
    with ctx.db.transaction() as conn:
        if except_session_id is None:
            cursor = conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (now_iso(), user_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL AND id != ?",
                (now_iso(), user_id, except_session_id),
            )
        return cursor.rowcount or 0


def user_sessions(conn, user_id: int):
    return conn.execute(
        """
        SELECT id, created_at, last_seen_at, expires_at, user_agent, client_fingerprint
        FROM sessions
        WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?
        ORDER BY last_seen_at DESC
        LIMIT 25
        """,
        (user_id, now_iso()),
    ).fetchall()


# ---------------------------------------------------------------------------
# Signup, login, logout
# ---------------------------------------------------------------------------


def signup(ctx, payload: dict, request) -> dict:
    identifier = request.client_ip(ctx.config.trust_proxy)
    ctx.gate("signup", identifier)

    try:
        display_name = clean_display_name(payload.get("display_name"))
        email = clean_email(payload.get("email"))
        password = validate_new_password(payload.get("password"), confirm=payload.get("password_confirm"))
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    # The 18+ checkbox is a self-attestation, not verification.
    adult = clean_bool(payload.get("adult_attestation"), field="adult_attestation", default=False)
    if not adult:
        raise bad_request(
            "Confirm that you are 18 or older to create an account.",
            code="adult_attestation_required",
            field="adult_attestation",
        )

    client_hash = ctx.client_hash(identifier)
    with ctx.db.transaction() as conn:
        existing = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if existing is not None:
            audit.record(
                conn,
                action="auth.signup_duplicate_email",
                target_type="user",
                target_id=existing["id"],
                meta={"masked": mask_email(email)},
                client_hash=client_hash,
            )
            raise conflict(
                "Velora could not create an account with those details. "
                "If you already have an account, sign in — or resend the confirmation email.",
                code="signup_unavailable",
                field="email",
            )

        created = now_iso()
        user_id = conn.execute(
            """
            INSERT INTO users (email, password_hash, display_name, role, email_verified,
                               adult_attested_at, orientation_visibility, status, created_at, updated_at)
            VALUES (?, ?, ?, 'member', 0, ?, 'private', 'active', ?, ?)
            """,
            (email, hash_password(password, ctx.config.pbkdf2_iterations), display_name, created, created, created),
        ).lastrowid
        audit.record(
            conn,
            action="auth.signup",
            actor_user_id=user_id,
            actor_role="member",
            target_type="user",
            target_id=user_id,
            meta={"adult_attestation": "self_attested", "email_verified": False},
            client_hash=client_hash,
        )
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    # Verification mail is a separate step: when email is not configured Velora
    # says so instead of pretending a message went out.
    verification = issue_verification(ctx, user, request)
    return {"user_id": user_id, "verification": verification}


def issue_verification(ctx, user: dict, request) -> dict:
    """Create a single-use verification token and try to send the email."""
    from ..emaillib import SendResult

    token = new_token()
    expires = _future(ctx.config.verification_ttl_seconds)
    with ctx.db.transaction() as conn:
        conn.execute(
            "UPDATE email_tokens SET used_at = ? WHERE user_id = ? AND purpose = 'verify_email' AND used_at IS NULL",
            (now_iso(), user["id"]),
        )
        conn.execute(
            """
            INSERT INTO email_tokens (user_id, purpose, email, token_hash, created_at, expires_at)
            VALUES (?, 'verify_email', ?, ?, ?, ?)
            """,
            (user["id"], user["email"], token_digest(token), now_iso(), expires),
        )

    if not ctx.email.configured:
        return {
            "sent": False,
            "reason": "unavailable",
            "message": (
                "Email delivery is not configured on this instance, so Velora cannot send the "
                "confirmation link. Ask the operator to configure email delivery."
            ),
            "expires_at": expires,
        }

    link = f"{_absolute_base(ctx, request)}/verify?token={token}"
    result: SendResult = ctx.email.send_verification(user["email"], user["display_name"], link,
                                                     ctx.config.verification_ttl_seconds)
    with ctx.db.transaction() as conn:
        audit.record(
            conn,
            action="auth.verification_sent" if result.ok else "auth.verification_send_failed",
            actor_user_id=user["id"],
            actor_role=user["role"],
            target_type="user",
            target_id=user["id"],
            meta={"transport": result.transport},
        )
    payload = {
        "sent": result.ok,
        "expires_at": expires,
        "masked_email": mask_email(user["email"]),
    }
    if not result.ok:
        payload["reason"] = "delivery_failed"
        payload["message"] = "Velora could not deliver the confirmation email. Try again shortly."
    if ctx.config.email_transport == "file" and result.stored_path:
        payload["development_path"] = result.stored_path
    return payload


def _absolute_base(ctx, request) -> str:
    if ctx.config.public_base_url:
        return ctx.config.public_base_url.rstrip("/")
    scheme = "https" if ctx.config.secure_cookies == "always" else "http"
    host = request.host or f"localhost:8000"
    return f"{scheme}://{host}"


def login(ctx, payload: dict, request) -> dict:
    ip = request.client_ip(ctx.config.trust_proxy)
    ctx.gate("login", ip)

    raw_email = payload.get("email")
    raw_password = payload.get("password")
    if not isinstance(raw_email, str) or not isinstance(raw_password, str) or not raw_email or not raw_password:
        raise bad_request("Enter your email address and password.", code="validation_error")

    try:
        email = clean_email(raw_email)
    except ValidationError:
        raise unauthorized("Email or password is incorrect.", code="invalid_credentials") from None

    client_hash = ctx.client_hash(ip)
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        user = dict(row) if row else None
        if user is None:
            # Spend comparable time so a missing account cannot be timed.
            verify_password(raw_password, _DUMMY_HASH)
            ctx.gate_or_flag("login_failed", f"{ip}:{email}", conn=conn)
            audit.record(conn, action="auth.login_failed", meta={"reason": "unknown_account"},
                         client_hash=client_hash)
            raise unauthorized("Email or password is incorrect.", code="invalid_credentials")

        if not verify_password(raw_password, user["password_hash"]):
            ctx.gate_or_flag("login_failed", f"{ip}:{email}", conn=conn)
            audit.record(conn, action="auth.login_failed", actor_user_id=user["id"],
                         target_type="user", target_id=user["id"], meta={"reason": "bad_password"},
                         client_hash=client_hash)
            raise unauthorized("Email or password is incorrect.", code="invalid_credentials")

        if user["status"] in ("suspended", "archived", "anonymized"):
            audit.record(conn, action="auth.login_blocked", actor_user_id=user["id"], target_type="user",
                         target_id=user["id"], meta={"status": user["status"]}, client_hash=client_hash)
            raise forbidden(
                "This account is not active. "
                + ("It was deactivated. An administrator can restore it." if user["status"] == "archived" else
                   "Contact an administrator if you believe this is a mistake."),
                code="account_" + user["status"],
            )

        token, _session = create_session(conn, ctx, user["id"], request)
        conn.execute("UPDATE users SET updated_at = ? WHERE id = ?", (now_iso(), user["id"]))
        audit.record(conn, action="auth.login", actor_user_id=user["id"], actor_role=user["role"],
                     target_type="user", target_id=user["id"], client_hash=client_hash)

    auth = None
    fresh = dict(user)
    return {"session_token": token, "user": user_private(fresh), "verified": bool(fresh["email_verified"])}


_DUMMY_HASH = hash_password("velora-dummy-password", 40_000)


def logout(ctx, auth: Auth, request) -> None:
    with ctx.db.transaction() as conn:
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE id = ?", (now_iso(), auth.session["id"]))
        audit.record(conn, action="auth.logout", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="session", target_id=auth.session["id"])


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------


def verify_email(ctx, token: str, request) -> dict:
    if not isinstance(token, str) or not token.strip():
        raise bad_request("This confirmation link is missing its token.", code="invalid_token")
    ctx.gate("verify_attempt", request.client_ip(ctx.config.trust_proxy))
    digest = token_digest(token.strip())
    with ctx.db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM email_tokens WHERE token_hash = ? AND purpose = 'verify_email'",
            (digest,),
        ).fetchone()
        if row is None:
            raise bad_request(
                "This confirmation link is not valid. Request a new one from your account page.",
                code="invalid_token",
            )
        record = dict(row)
        if record["used_at"]:
            raise bad_request(
                "This confirmation link has already been used. You can request a new one.",
                code="token_used",
            )
        if record["expires_at"] <= now_iso():
            raise bad_request(
                "This confirmation link has expired. Request a new one from your account page.",
                code="token_expired",
            )
        conn.execute("UPDATE email_tokens SET used_at = ? WHERE id = ?", (now_iso(), record["id"]))
        conn.execute(
            "UPDATE users SET email_verified = 1, email_verified_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), record["user_id"]),
        )
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (record["user_id"],)).fetchone())
        audit.record(conn, action="auth.email_verified", actor_user_id=user["id"], actor_role=user["role"],
                     target_type="user", target_id=user["id"])
    return {"verified": True, "email": user["email"], "display_name": user["display_name"]}


def resend_verification(ctx, payload: dict, request) -> dict:
    ip = request.client_ip(ctx.config.trust_proxy)
    ctx.gate("resend_verification", ip)
    try:
        email = clean_email(payload.get("email"))
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    # Every outcome returns the same shape, so asking for a resend can never be
    # used to find out whether an address has an account on this instance.
    response = {"accepted": True, "sent": False, "masked_email": mask_email(email)}
    unavailable_notice = {
        "unavailable": "email_not_configured",
        "notice": (
            "Email delivery is not configured on this Velora instance, so no confirmation link "
            "can be sent. An operator has to configure email delivery first."
        ),
    }

    with ctx.db.connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        return {**response, **unavailable_notice} if not ctx.email.configured else response

    user = dict(row)
    if user["email_verified"] or user["status"] != "active":
        return {**response, **unavailable_notice} if not ctx.email.configured else response
    if not ctx.email.configured:
        return {**response, **unavailable_notice}

    result = issue_verification(ctx, user, request)
    return {"accepted": True, "masked_email": mask_email(email), **result}


# ---------------------------------------------------------------------------
# Invitations (administrator onboarding)
# ---------------------------------------------------------------------------


def create_invitation(ctx, admin_auth: Auth, payload: dict, request) -> dict:
    require_admin(admin_auth)
    ctx.gate("admin_mutation", admin_auth.user_id)

    role = (payload.get("role") or "admin").strip().lower() if isinstance(payload.get("role"), str) else "admin"
    if role not in ("admin", "creator"):
        raise bad_request("Invitations can grant administrator or creator access.", code="validation_error",
                          field="role")
    email = None
    if payload.get("email"):
        try:
            email = clean_email(payload.get("email"))
        except ValidationError as exc:
            raise bad_request(exc.message, code="validation_error", field=exc.field) from None
    try:
        note = clean_text(payload.get("note"), field="note", max_length=300, allow_newlines=False) or None
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    token = new_token()
    expires = _future(ctx.config.invitation_ttl_seconds)
    with ctx.db.transaction() as conn:
        invitation_id = conn.execute(
            """
            INSERT INTO admin_invitations (email, role, token_hash, invited_by, note, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (email, role, token_digest(token), admin_auth.user_id, note, now_iso(), expires),
        ).lastrowid
        audit.record(conn, action="admin.invitation_created", actor_user_id=admin_auth.user_id,
                     actor_role="admin", target_type="admin_invitation", target_id=invitation_id,
                     meta={"role": role, "email_bound": bool(email)}, client_hash=ctx.client_hash(
                         request.client_ip(ctx.config.trust_proxy)))

    link = f"{_absolute_base(ctx, request)}/invite?token={token}"
    delivery = {"sent": False, "reason": "no_recipient"}
    if email and ctx.email.configured:
        result = ctx.email.send_invitation(email, role, link, ctx.config.invitation_ttl_seconds,
                                           admin_auth.user["display_name"], note)
        delivery = {"sent": result.ok, "transport": result.transport}
        if ctx.config.email_transport == "file" and result.stored_path:
            delivery["development_path"] = result.stored_path
    elif not ctx.email.configured:
        delivery = {
            "sent": False,
            "reason": "unavailable",
            "message": "Email delivery is not configured; share the invitation link directly.",
        }

    payload_out = {
        "invitation_id": invitation_id,
        "role": role,
        "expires_at": expires,
        "email_bound": bool(email),
        "delivery": delivery,
    }
    # A generic (email-unbound) invitation link must be shared securely by the admin.
    if not email:
        payload_out["link"] = link
    return payload_out


def preview_invitation(ctx, token: str) -> dict:
    if not token:
        raise bad_request("This invitation link is incomplete.", code="invalid_token")
    with ctx.db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM admin_invitations WHERE token_hash = ?", (token_digest(token.strip()),)
        ).fetchone()
    if row is None:
        raise not_found("This invitation is not valid.", code="invalid_invitation")
    invitation = dict(row)
    state = invitation_state(invitation)
    if state != "usable":
        raise ApiError(410, f"invitation_{state}", _invitation_message(state))
    return {
        "role": invitation["role"],
        "expires_at": invitation["expires_at"],
        "email_bound": bool(invitation["email"]),
        "masked_email": mask_email(invitation["email"]) if invitation["email"] else None,
        "note": invitation.get("note"),
        "requires_account": True,
    }


def invitation_state(invitation: dict) -> str:
    if invitation.get("revoked_at"):
        return "revoked"
    if invitation.get("used_at"):
        return "used"
    if (invitation.get("expires_at") or "") <= now_iso():
        return "expired"
    return "usable"


def _invitation_message(state: str) -> str:
    return {
        "used": "This invitation has already been accepted.",
        "expired": "This invitation has expired. Ask an administrator for a new one.",
        "revoked": "This invitation was revoked.",
    }.get(state, "This invitation is not usable.")


def accept_invitation(ctx, payload: dict, request) -> dict:
    ip = request.client_ip(ctx.config.trust_proxy)
    ctx.gate("invitation_accept", ip)
    token = payload.get("token")
    if not isinstance(token, str) or not token.strip():
        raise bad_request("This invitation link is incomplete.", code="invalid_token")

    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM admin_invitations WHERE token_hash = ?",
                           (token_digest(token.strip()),)).fetchone()
        if row is None:
            raise not_found("This invitation is not valid.", code="invalid_invitation")
        invitation = dict(row)
        state = invitation_state(invitation)
        if state != "usable":
            raise ApiError(410, f"invitation_{state}", _invitation_message(state))

        email = invitation["email"]
        session_raw = request.cookies.get(SESSION_COOKIE)
        existing_user = None
        if session_raw:
            session_row = conn.execute(
                "SELECT user_id FROM sessions WHERE token_hash = ? AND revoked_at IS NULL",
                (token_digest(session_raw),),
            ).fetchone()
            if session_row:
                existing_user = dict(conn.execute("SELECT * FROM users WHERE id = ?",
                                                  (session_row["user_id"],)).fetchone() or {})

        if existing_user:
            if email and existing_user["email"].lower() != email.lower():
                raise forbidden(
                    "This invitation was issued to a different email address.",
                    code="invitation_email_mismatch",
                )
            user = existing_user
            if not user["email_verified"]:
                raise forbidden(
                    "Confirm your email address before accepting an invitation.",
                    code="email_verification_required",
                )
            if user["status"] != "active":
                raise forbidden("This account is not active.", code="account_inactive")
            granted_role = _stronger_role(user["role"], invitation["role"])
            conn.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                         (granted_role, now_iso(), user["id"]))
            user["role"] = granted_role
        else:
            if not email:
                raise bad_request(
                    "This invitation is not bound to an email address. Sign in or sign up first, then "
                    "open the invitation link again.",
                    code="invitation_needs_account",
                )
            password = validate_new_password(payload.get("password"), confirm=payload.get("password_confirm"))
            display_name = clean_display_name(payload.get("display_name"))
            adult = clean_bool(payload.get("adult_attestation"), field="adult_attestation", default=False)
            if not adult:
                raise bad_request("Confirm that you are 18 or older to create an account.",
                                  code="adult_attestation_required", field="adult_attestation")
            clash = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if clash is not None:
                raise conflict(
                    "An account already exists for that email address. Sign in and open the invitation "
                    "link again.",
                    code="account_exists",
                    field="email",
                )
            created = now_iso()
            user_id = conn.execute(
                """
                INSERT INTO users (email, password_hash, display_name, role, email_verified,
                                   adult_attested_at, orientation_visibility, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, 'private', 'active', ?, ?)
                """,
                (email, hash_password(password, ctx.config.pbkdf2_iterations), display_name,
                 invitation["role"], created, created, created),
            ).lastrowid
            user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

        conn.execute("UPDATE admin_invitations SET used_at = ?, used_by = ? WHERE id = ?",
                     (now_iso(), user["id"], invitation["id"]))
        audit.record(conn, action="admin.invitation_accepted", actor_user_id=user["id"],
                     actor_role=user["role"], target_type="admin_invitation", target_id=invitation["id"],
                     meta={"role": invitation["role"], "new_account": not existing_user},
                     client_hash=ctx.client_hash(ip))

    verification = None
    if not user["email_verified"]:
        verification = issue_verification(ctx, user, request)
    return {"user": user_private(user), "role": user["role"], "verification": verification}


def _stronger_role(current: str, invited: str) -> str:
    return invited if ROLE_ORDER.get(invited, 0) > ROLE_ORDER.get(current, 0) else current


def revoke_invitation(ctx, admin_auth: Auth, invitation_id: int, request) -> dict:
    require_admin(admin_auth)
    ctx.gate("admin_mutation", admin_auth.user_id)
    with ctx.db.transaction() as conn:
        row = conn.execute("SELECT * FROM admin_invitations WHERE id = ?", (invitation_id,)).fetchone()
        if row is None:
            raise not_found("Invitation not found.")
        if dict(row)["used_at"]:
            raise conflict("That invitation was already accepted and cannot be revoked.",
                           code="invitation_used")
        conn.execute("UPDATE admin_invitations SET revoked_at = ? WHERE id = ?", (now_iso(), invitation_id))
        audit.record(conn, action="admin.invitation_revoked", actor_user_id=admin_auth.user_id,
                     actor_role="admin", target_type="admin_invitation", target_id=invitation_id,
                     client_hash=ctx.client_hash(request.client_ip(ctx.config.trust_proxy)))
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Profile and privacy
# ---------------------------------------------------------------------------


def update_profile(ctx, auth: Auth, payload: dict) -> dict:
    require_active(auth)
    fields: dict[str, object] = {}
    if "display_name" in payload:
        try:
            fields["display_name"] = clean_display_name(payload.get("display_name"))
        except ValidationError as exc:
            raise bad_request(exc.message, code="validation_error", field=exc.field) from None
    if "bio" in payload:
        try:
            fields["bio"] = clean_text(payload.get("bio") or "", field="bio", max_length=MAX_BIO) or None
        except ValidationError as exc:
            raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    if not fields:
        raise bad_request("There is nothing to update.", code="validation_error")

    with ctx.db.transaction() as conn:
        assignments = ", ".join(f"{column} = ?" for column in fields)
        conn.execute(
            f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?",  # noqa: S608 - static column names
            (*fields.values(), now_iso(), auth.user_id),
        )
        audit.record(conn, action="account.profile_updated", actor_user_id=auth.user_id,
                     actor_role=auth.role, target_type="user", target_id=auth.user_id,
                     meta={"fields": sorted(fields.keys())})
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (auth.user_id,)).fetchone())
    return user_private(user)


def set_orientation(ctx, auth: Auth, payload: dict) -> dict:
    require_active(auth)
    try:
        orientation = clean_orientation(payload.get("orientation"))
        requested_visibility = (payload.get("visibility") or "private")
        if not isinstance(requested_visibility, str) or requested_visibility.lower() not in ("private", "public"):
            raise ValidationError("Choose whether to keep this private or share it.", "visibility")
        visibility = requested_visibility.lower()
    except ValidationError as exc:
        raise bad_request(exc.message, code="validation_error", field=exc.field) from None

    notes: list[str] = []
    if visibility == "public":
        value = orientation["value"]
        if value == ORIENTATION_PREFER_NOT_TO_SAY:
            visibility = "private"
            notes.append('"Prefer not to say" is never displayed publicly, so this stayed private.')
        elif orientation["self_described"]:
            visibility = "private"
            notes.append(
                "Your own words are kept private. Choose one of the listed options if you want to "
                "share something publicly."
            )
        elif value is None:
            visibility = "private"
            notes.append("There is nothing to share yet, so this stayed private.")
        elif not orientation_public_eligible(value):
            visibility = "private"
            notes.append("That choice cannot be shared publicly, so this stayed private.")

    kind = orientation_kind(orientation)
    with ctx.db.transaction() as conn:
        conn.execute(
            """
            UPDATE users
            SET orientation_value = ?, orientation_self_text = ?, orientation_visibility = ?, updated_at = ?
            WHERE id = ?
            """,
            (orientation["value"], orientation["self_described"], visibility, now_iso(), auth.user_id),
        )
        conn.execute(
            """
            INSERT INTO orientation_disclosures (user_id, value_kind, visibility, actor_kind, created_at)
            VALUES (?, ?, ?, 'self', ?)
            """,
            (auth.user_id, kind, visibility, now_iso()),
        )
        audit.record(conn, action="account.orientation_visibility_changed", actor_user_id=auth.user_id,
                     actor_role=auth.role, target_type="user", target_id=auth.user_id,
                     meta={"kind": kind, "visibility": visibility})
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (auth.user_id,)).fetchone())
    return {"user": user_private(user), "notes": notes}


def export_personal_data(ctx, auth: Auth) -> dict:
    """A copy of the personal data Velora holds for this account."""
    with ctx.db.connection() as conn:
        user = dict(conn.execute("SELECT * FROM users WHERE id = ?", (auth.user_id,)).fetchone())
        sessions = [dict(row) for row in conn.execute(
            "SELECT created_at, last_seen_at, expires_at, user_agent FROM sessions WHERE user_id = ?",
            (auth.user_id,))]
        memberships = [dict(row) for row in conn.execute(
            "SELECT id, creator_id, tier_id, status, started_at, ends_at, cancel_requested_at "
            "FROM memberships WHERE user_id = ?", (auth.user_id,))]
        intents = [dict(row) for row in conn.execute(
            "SELECT order_ref, creator_id, tier_id, amount_cents, asset, asset_amount_atomic, status, "
            "created_at, settled_at "
            "FROM payment_intents WHERE user_id = ?", (auth.user_id,))]
        invoices = [dict(row) for row in conn.execute(
            "SELECT order_ref, amount_cents, creator_net_cents, platform_fee_cents, btc_amount_sats, "
            "asset, asset_amount_atomic, settled_at FROM invoices WHERE user_id = ?", (auth.user_id,))]
        disclosures = [dict(row) for row in conn.execute(
            "SELECT value_kind, visibility, created_at FROM orientation_disclosures WHERE user_id = ?",
            (auth.user_id,))]
        actions = [dict(row) for row in conn.execute(
            "SELECT action, reason, actor_kind, created_at FROM account_actions WHERE user_id = ?",
            (auth.user_id,))]

    return {
        "generated_at": now_iso(),
        "notice": (
            "This export contains your account data. Financial records are kept in a form that "
            "preserves the integrity of settled payments and are shown here for transparency."
        ),
        "account": {
            "email": user["email"],
            "display_name": user["display_name"],
            "role": user["role"],
            "created_at": user["created_at"],
            "email_verified": bool(user["email_verified"]),
            "adult_attestation_recorded_at": user["adult_attested_at"],
            "bio": user.get("bio"),
            "orientation": {
                "value": user.get("orientation_value"),
                "self_described": user.get("orientation_self_text"),
                "visibility": user.get("orientation_visibility"),
            },
        },
        "sessions": sessions,
        "memberships": memberships,
        "payment_attempts": intents,
        "settled_invoices": invoices,
        "orientation_disclosures": disclosures,
        "account_actions": actions,
    }


def archive_account(ctx, auth: Auth, payload: dict, *, actor_kind: str = "self",
                    reason: str | None = None) -> dict:
    """Deactivate an account: no sign-in, sessions revoked, creator page hidden.

    Financial and ledger history is retained; nothing is cascade-deleted.
    """
    if actor_kind == "self":
        require_active(auth)
        password = payload.get("password")
        if not isinstance(password, str) or not verify_password(password, auth.user["password_hash"]):
            raise bad_request("Enter your current password to deactivate your account.",
                              code="password_required", field="password")
    with ctx.db.transaction() as conn:
        conn.execute("UPDATE users SET status = 'archived', archived_at = ?, updated_at = ? WHERE id = ?",
                     (now_iso(), now_iso(), auth.user_id))
        conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                     (now_iso(), auth.user_id))
        conn.execute("UPDATE creator_pages SET status = 'paused', updated_at = ? WHERE user_id = ?",
                     (now_iso(), auth.user_id))
        conn.execute(
            "INSERT INTO account_actions (user_id, action, reason, actor_user_id, actor_kind, retained_note, created_at) "
            "VALUES (?, 'archived', ?, ?, ?, ?, ?)",
            (auth.user_id, reason, auth.user_id, actor_kind,
             "Settled payments, ledger entries and memberships are retained for financial integrity.",
             now_iso()),
        )
        audit.record(conn, action="account.archived", actor_user_id=auth.user_id, actor_role=auth.role,
                     target_type="user", target_id=auth.user_id, meta={"actor": actor_kind, "reason": reason})
    return {"archived": True, "sessions_revoked": True}


def restore_account(conn, actor: Auth, user_id: int, reason: str | None = None) -> dict:
    conn.execute("UPDATE users SET status = 'active', archived_at = NULL, updated_at = ? WHERE id = ?",
                 (now_iso(), user_id))
    conn.execute("UPDATE creator_pages SET status = 'active', updated_at = ? WHERE user_id = ? AND status = 'paused'",
                 (now_iso(), user_id))
    conn.execute(
        "INSERT INTO account_actions (user_id, action, reason, actor_user_id, actor_kind, created_at) "
        "VALUES (?, 'restored', ?, ?, 'admin', ?)",
        (user_id, reason, actor.user_id, now_iso()),
    )
    audit.record(conn, action="account.restored", actor_user_id=actor.user_id, actor_role=actor.role,
                 target_type="user", target_id=user_id, meta={"reason": reason})
    return {"restored": True}


def anonymize_account(conn, *, user_id: int, actor_user_id: int | None, actor_kind: str,
                      reason: str | None = None) -> dict:
    """Irreversibly strip personal data while keeping financial history intact.

    Email is replaced with a non-routable placeholder, the password becomes
    unusable, and optional sensitive fields are cleared. Memberships, settled
    invoices and ledger rows keep pointing at the (now anonymous) account row, so
    append-only financial history is never deleted or rewritten.
    """
    placeholder_email = f"deleted+user{user_id}@velora.invalid"
    conn.execute(
        """
        UPDATE users
        SET email = ?, display_name = 'Deleted member', password_hash = ?, bio = NULL,
            orientation_value = NULL, orientation_self_text = NULL, orientation_visibility = 'private',
            status = 'anonymized', anonymized_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (placeholder_email, hash_password(new_token(32), 40_000), now_iso(), now_iso(), user_id),
    )
    conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                 (now_iso(), user_id))
    conn.execute("UPDATE creator_pages SET status = 'archived', archived_at = ?, updated_at = ? WHERE user_id = ?",
                 (now_iso(), now_iso(), user_id))
    conn.execute("UPDATE messages SET body = '[removed]', removed_at = ?, removed_by = ? WHERE sender_id = ?",
                 (now_iso(), actor_user_id, user_id))
    conn.execute("UPDATE email_tokens SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
                 (now_iso(), user_id))
    conn.execute(
        "INSERT INTO account_actions (user_id, action, reason, actor_user_id, actor_kind, retained_note, created_at) "
        "VALUES (?, 'anonymized', ?, ?, ?, ?, ?)",
        (user_id, reason, actor_user_id, actor_kind,
         "Settled payments, ledger entries and settlement records are retained in anonymised form.",
         now_iso()),
    )
    audit.record(conn, action="account.anonymized", actor_user_id=actor_user_id,
                 actor_role=actor_kind, target_type="user", target_id=user_id, meta={"reason": reason})
    return {"anonymized": True, "placeholder_email": placeholder_email}


def orientation_disclosure_history(conn, user_id: int, limit: int = 50):
    return conn.execute(
        """
        SELECT value_kind, visibility, actor_kind, created_at
        FROM orientation_disclosures WHERE user_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (user_id, max(1, min(200, limit))),
    ).fetchall()


def find_user_by_email(conn, email: str):
    return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()


def mask_user_email(email: str) -> str:
    return mask_email(email)


__all__ = [
    "Auth",
    "SESSION_COOKIE",
    "CSRF_HEADER",
    "accept_invitation",
    "anonymize_account",
    "archive_account",
    "capabilities",
    "create_invitation",
    "create_session",
    "export_personal_data",
    "find_user_by_email",
    "invitation_state",
    "issue_verification",
    "login",
    "logout",
    "preview_invitation",
    "require_active",
    "require_admin",
    "require_auth",
    "require_verified",
    "resend_verification",
    "resolve_auth",
    "restore_account",
    "revoke_all_sessions",
    "revoke_invitation",
    "revoke_session",
    "set_orientation",
    "signup",
    "update_profile",
    "user_sessions",
    "verify_email",
    "constant_time_equal",
]
