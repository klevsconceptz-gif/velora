"""Operator CLI: ``python -m server.cli <command>``.

Commands
--------
``serve``          migrate if needed, then run the HTTP server.
``migrate``        apply pending migrations.
``status``         show configuration and migration state (never prints secrets).
``create-admin``   provision the first administrator (explicit confirmation).
``admin list``     list current administrators.
``user verify-email``  mark an account's email verified (audited, for operators).
``hash-password``  print a password hash for manual recovery work.
``maintenance``    expire ended memberships and prune old rate-limit counters.
``generate-secret`` print a fresh session secret.

There is deliberately no ``seed`` command: Velora ships with an empty database
and no demo accounts, pages, posts or payments.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from . import __version__, audit
from .config import build_config, get_config
from .db import Database, now_iso
from .migrations import MigrationError, apply_all, status as migration_status
from .security import hash_password, validate_password
from .validation import clean_display_name, clean_email

MIN_PASSWORD_HINT = "At least 8 characters. Velora stores only a PBKDF2 hash."


def _prompt_password(prompt: str = "Password: ") -> str:
    first = getpass.getpass(prompt)
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords did not match. Nothing was changed.")
    try:
        validate_password(first)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"{exc}. Nothing was changed.") from None
    return first


def _prompt(label: str) -> str:
    """Read a line, failing politely when there is no terminal to read from."""
    try:
        return input(label).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nNo input available. Nothing was changed.")
        raise SystemExit(1) from None


def _read_password_stdin() -> str:
    data = sys.stdin.readline().strip()
    if not data:
        raise SystemExit("Expected a password on stdin. Nothing was changed.")
    try:
        validate_password(data)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"{exc}. Nothing was changed.") from None
    return data


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_migrate(args) -> int:
    config = get_config()
    db = Database(config.db_path)
    applied = apply_all(db, log=lambda message: print(message))
    if applied:
        print(f"Applied {len(applied)} migration(s) to {config.db_path}")
    else:
        print(f"Database already up to date ({config.db_path})")
    state = migration_status(db)
    if state["problems"]:
        for problem in state["problems"]:
            print(f"WARNING: {problem}")
        return 1
    return 0


def cmd_status(args) -> int:
    config = get_config()
    db = Database(config.db_path)
    state = migration_status(db)
    print(f"Velora {__version__}")
    print(f"  environment        : {config.environment}")
    print(f"  database           : {config.db_path}")
    print(f"  migrations applied : {len(state['applied'])}")
    print(f"  migrations pending : {', '.join(state['pending']) or 'none'}")
    print(f"  media root         : {config.media_root}")
    print(f"  email configured   : {config.email_configured} (transport: {config.email_transport})")
    print(f"  btcpay configured  : {config.btcpay_configured}")
    print(f"  btcpay webhooks    : {config.btcpay_webhooks_configured}")
    print(f"  public base url    : {config.public_base_url or '(derived from the request host)'}")
    print(f"  secure cookies     : {config.secure_cookies}")
    if state["pending"]:
        # Never crash on a database that has not been migrated yet: `status` is the
        # command an operator runs first.
        print("  contents           : not inspected (run: python -m server.cli migrate)")
    else:
        with db.connection() as conn:
            users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            admins = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND status='active' "
                "AND email_verified=1"
            ).fetchone()["c"]
            pages = conn.execute("SELECT COUNT(*) AS c FROM creator_pages").fetchone()["c"]
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
        print(f"  accounts           : {users} (active verified admins: {admins})")
        print(f"  creator pages      : {pages}")
        print(f"  settled invoices   : {invoices}")
    if state["problems"]:
        for problem in state["problems"]:
            print(f"WARNING: {problem}")
    return 0


def cmd_create_admin(args) -> int:
    """Provision the first administrator.

    Requirements baked in here: no default credentials, explicit confirmation,
    email verification before the role is granted, and a single active verified
    administrator is enough — further administrators must come from a one-time
    invitation or an audited promotion inside the console.
    """
    config = get_config()
    db = Database(config.db_path)
    apply_all(db, log=lambda message: print(message))

    with db.connection() as conn:
        active_admins = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE role='admin' AND status='active' AND email_verified=1"
        ).fetchone()["c"]
        existing = None
        if args.email:
            try:
                candidate_email = clean_email(args.email)
            except Exception as exc:  # noqa: BLE001
                print(f"Invalid email: {exc}")
                return 1
            existing = conn.execute("SELECT * FROM users WHERE email = ?", (candidate_email,)).fetchone()

    if active_admins:
        print(
            "Velora already has an active, verified administrator.\n"
            "Add further administrators with a one-time invitation from the admin console "
            "(Admin → Invitations) or an audited promotion of a verified account."
        )
        return 1

    email = args.email or input("Email address: ").strip()
    try:
        email = clean_email(email)
    except Exception as exc:  # noqa: BLE001
        print(f"Invalid email: {exc}")
        return 1
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    user = dict(row) if row else None

    if user is None and args.grant_existing:
        print(f"No account exists for {email}; --grant-existing only promotes an existing account.")
        return 1

    # A display name is only needed when an account is being created. Promoting an
    # existing account must never block on a prompt: it is usually non-interactive,
    # and the account already has a name.
    display_name = None
    if user is None:
        display_name = args.display_name or _prompt("Display name: ")
        try:
            display_name = clean_display_name(display_name)
        except Exception as exc:  # noqa: BLE001
            print(f"Invalid display name: {exc}")
            return 1

    if user is not None:
        if not args.grant_existing:
            print(
                f"An account already exists for {email}. Re-run with --grant-existing to promote it."
            )
            return 1
        if user["status"] != "active":
            print(f"That account is '{user['status']}' and cannot be promoted.")
            return 1
        if not user["email_verified"]:
            print(
                "That account has not confirmed its email address. Verify it first with:\n"
                f"  python -m server.cli user verify-email --email {email}"
            )
            return 1

    if not args.confirm:
        print(
            "\nThis grants full administrator access: reading member emails, moderating content, and\n"
            "viewing the payment ledger (append-only, so nothing can be rewritten).\n"
            f"Target : {email} ({'existing account' if user else 'new account'})\n"
            'Type "CREATE ADMIN" to continue: '
        )
        if _prompt("").strip() != "CREATE ADMIN":
            print("Cancelled. Nothing was changed.")
            return 1

    password = None
    if user is None:
        if args.password_stdin:
            password = _read_password_stdin()
        elif args.password:
            password = args.password
            try:
                validate_password(password)
            except Exception as exc:  # noqa: BLE001
                print(f"{exc}. Nothing was changed.")
                return 1
        else:
            print(f"Choose a password for {email}. {MIN_PASSWORD_HINT}")
            password = _prompt_password()

    created = now_iso()
    with db.transaction() as conn:
        if user is None:
            user_id = conn.execute(
                """
                INSERT INTO users (email, password_hash, display_name, role, email_verified,
                                   email_verified_at, adult_attested_at, orientation_visibility,
                                   status, created_at, updated_at)
                VALUES (?, ?, ?, 'admin', 1, ?, ?, 'private', 'active', ?, ?)
                """,
                (email, hash_password(password, config.pbkdf2_iterations), display_name, created,
                 created, created, created),
            ).lastrowid
            action = "cli.first_admin_created"
            detail = "created and verified locally by the operator"
        else:
            user_id = user["id"]
            conn.execute("UPDATE users SET role = 'admin', updated_at = ? WHERE id = ?",
                         (now_iso(), user_id))
            action = "cli.admin_role_granted"
            detail = f"promoted from role {user['role']}"
        audit.record(
            conn,
            action=action,
            actor_user_id=user_id,
            actor_role="admin",
            target_type="user",
            target_id=user_id,
            meta={"source": "cli", "detail": detail, "confirmed": True},
        )

    if user is None:
        print(f"Created administrator {email} (id {user_id}).")
        print("The address is marked verified because the operator provisioned it locally; "
              "confirm ownership out of band before sharing the login.")
    else:
        print(f"Granted administrator role to {email} (id {user_id}).")
    print("Sign in at /#/login. Velora stores only a PBKDF2 hash of that password.")
    return 0


def cmd_admin_list(args) -> int:
    config = get_config()
    db = Database(config.db_path)
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, email, display_name, status, email_verified, created_at
            FROM users WHERE role = 'admin' ORDER BY id
            """
        ).fetchall()
    if not rows:
        print("No administrators exist. Run: python -m server.cli create-admin")
        return 1
    for row in rows:
        verified = "verified" if row["email_verified"] else "unverified"
        print(f"#{row['id']:<4} {row['email']:<40} {row['status']:<10} {verified:<10} {row['created_at']}")
    return 0


def cmd_verify_email(args) -> int:
    config = get_config()
    db = Database(config.db_path)
    try:
        email = clean_email(args.email)
    except Exception as exc:  # noqa: BLE001
        print(f"Invalid email: {exc}")
        return 1
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            print("No account with that email address.")
            return 1
        user = dict(row)
        if user["email_verified"]:
            print(f"{email} is already verified.")
            return 0
        conn.execute(
            "UPDATE users SET email_verified = 1, email_verified_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), user["id"]),
        )
        audit.record(conn, action="cli.email_verified", actor_user_id=user["id"], actor_role="admin",
                     target_type="user", target_id=user["id"],
                     meta={"source": "cli", "note": "operator confirmed ownership out of band"})
    print(
        f"Marked {email} as verified.\n"
        "Reminder: only do this when you have confirmed the address out of band. The 18+ "
        "self-attestation is still not age verification."
    )
    return 0


def cmd_hash_password(args) -> int:
    config = get_config()
    password = args.password or getpass.getpass("Password to hash: ")
    try:
        validate_password(password)
    except Exception as exc:  # noqa: BLE001
        print(f"{exc}")
        return 1
    print(hash_password(password, config.pbkdf2_iterations))
    return 0


def cmd_maintenance(args) -> int:
    from .services.payments import expire_memberships
    from .services.context import build_context

    ctx = build_context()
    expired = expire_memberships(ctx)
    pruned = ctx.limiter.prune()
    print(f"Expired {expired} membership period(s); pruned {pruned} rate-limit counter row(s).")
    return 0


def cmd_generate_secret(args) -> int:
    import secrets

    print(secrets.token_urlsafe(48))
    return 0


def cmd_serve(args) -> int:
    config = get_config()
    if args.host:
        os.environ["VELORA_HOST"] = args.host
    db = Database(config.db_path)
    apply_all(db, log=lambda message: print(message))

    from .app import create_app

    app = create_app(config)
    host = args.host
    port = args.port

    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, make_server

    class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    printing = args.quiet is False
    with make_server(host, port, app, server_class=ThreadingWSGIServer) as server:
        print(f"Velora {__version__} listening on http://{host}:{port}  (env: {config.environment})")
        if not config.email_configured:
            print("  email delivery: NOT configured — verification email cannot be sent")
        if not config.btcpay_configured:
            print("  BTC checkout  : NOT configured — checkout reports itself unavailable")
        if printing:
            print("  database      :", config.db_path)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.cli",
        description="Velora operator CLI. There is no demo seed command by design.",
    )
    parser.add_argument("--version", action="version", version=f"Velora {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP server (migrates first)")
    serve.add_argument("--host", default=os.environ.get("VELORA_HOST", "0.0.0.0"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("VELORA_PORT", "8000")))
    serve.add_argument("--quiet", action="store_true")
    serve.set_defaults(func=cmd_serve)

    migrate = sub.add_parser("migrate", help="apply pending migrations")
    migrate.set_defaults(func=cmd_migrate)

    status = sub.add_parser("status", help="show configuration and migration state")
    status.set_defaults(func=cmd_status)

    admin = sub.add_parser("create-admin", help="provision the first administrator")
    admin.add_argument("--email")
    admin.add_argument("--display-name")
    admin.add_argument("--password", help="not recommended: appears in shell history")
    admin.add_argument("--password-stdin", action="store_true")
    admin.add_argument("--grant-existing", action="store_true",
                       help="promote an existing verified account instead of creating one")
    admin.add_argument("--confirm", action="store_true", help="skip the interactive confirmation prompt")
    admin.set_defaults(func=cmd_create_admin)

    admin_list = sub.add_parser("admin-list", help="list administrators")
    admin_list.set_defaults(func=cmd_admin_list)

    verify = sub.add_parser("user", help="account maintenance")
    verify_sub = verify.add_subparsers(dest="user_command", required=True)
    verify_email = verify_sub.add_parser("verify-email", help="mark an account's email verified (audited)")
    verify_email.add_argument("--email", required=True)
    verify_email.set_defaults(func=cmd_verify_email)

    hash_cmd = sub.add_parser("hash-password", help="print a PBKDF2 hash for a password")
    hash_cmd.add_argument("--password")
    hash_cmd.set_defaults(func=cmd_hash_password)

    maintenance = sub.add_parser("maintenance", help="expire memberships and prune rate limits")
    maintenance.set_defaults(func=cmd_maintenance)

    secret = sub.add_parser("generate-secret", help="print a fresh VELORA_SECRET_KEY")
    secret.set_defaults(func=cmd_generate_secret)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve" and not os.environ.get("VELORA_ENV"):
        os.environ.setdefault("VELORA_ENV", "development")
    try:
        return args.func(args)
    except MigrationError as exc:
        # A schema problem is an operator decision, not a stack trace: a released
        # migration file was edited after it was applied, or a migration failed.
        print(f"Database is not in a safe state: {exc}")
        print("Nothing was changed. Restore the released migration file, or restore a backup "
              "if the schema was modified by hand.")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
