"""Operator CLI: first-admin provisioning, promotion rules and non-interactive safety.

The CLI is the only way to create the first administrator, so its guards matter as
much as the API's. These tests call the real command functions in-process against a
throwaway database and assert both the output and the audit trail.

`input()` is replaced by a stub that fails loudly, so any test that would otherwise
block on a prompt fails instead of hanging.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

from server import cli  # noqa: E402
from server.config import set_config  # noqa: E402
from server.tests.harness import Client, VeloraTestCase  # noqa: E402


class _NoPrompt(Exception):
    """Raised if a command asks for input when it should not."""


def no_input(prompt: str = "") -> str:  # pragma: no cover - only called on failure
    raise _NoPrompt(f"unexpected prompt: {prompt!r}")


class CliTestCase(VeloraTestCase):
    def run_cli(self, argv: list[str], *, stdin: str = "", config=None) -> tuple[int, str]:
        """Run a CLI command in-process, returning (exit code, combined output)."""
        import builtins

        buffer = io.StringIO()
        original_input = builtins.input
        builtins.input = no_input if not stdin else (lambda prompt="": next(iter([stdin.strip()])))
        set_config(config or self.config)
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                code = cli.main(argv)
        except _NoPrompt as exc:
            raise AssertionError(str(exc)) from None
        except SystemExit as exc:  # some commands exit through SystemExit
            code = int(exc.code or 0)
        finally:
            builtins.input = original_input
        return code, buffer.getvalue()

    def user_id(self, email: str) -> int:
        with self.db.connection() as conn:
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        self.assertIsNotNone(row, email)
        return row["id"]

    def audit_actions(self) -> list[str]:
        with self.db.connection() as conn:
            return [row["action"] for row in
                    conn.execute("SELECT action FROM audit_log ORDER BY id")]


class FirstAdminTests(CliTestCase):
    def test_creates_the_first_administrator_with_a_hashed_password(self):
        code, output = self.run_cli([
            "create-admin", "--email", "ops@velora.test", "--display-name", "Operator",
            "--password", "correct-horse-9", "--confirm",
        ])
        self.assertEqual(code, 0, output)
        self.assertIn("Created administrator ops@velora.test", output)
        self.assertIn("PBKDF2", output)
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = 'ops@velora.test'").fetchone()
        self.assertEqual(row["role"], "admin")
        self.assertEqual(row["status"], "active")
        self.assertEqual(row["email_verified"], 1)
        self.assertTrue(row["password_hash"].startswith("pbkdf2_"))
        self.assertNotIn("correct-horse-9", output)
        self.assertNotIn("correct-horse-9", row["password_hash"])
        self.assertIn("cli.first_admin_created", self.audit_actions())

    def test_second_administrator_is_refused_until_invited_or_promoted(self):
        self.run_cli(["create-admin", "--email", "ops@velora.test", "--display-name", "Operator",
                      "--password", "correct-horse-9", "--confirm"])
        code, output = self.run_cli([
            "create-admin", "--email", "second@velora.test", "--display-name", "Second",
            "--password", "correct-horse-9", "--confirm",
        ])
        self.assertEqual(code, 1, output)
        self.assertIn("already has an active, verified administrator", output)
        self.assertIn("invitation", output.lower())
        with self.db.connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        self.assertEqual(count, 1, "the refused command must not create an account")

    def test_grant_existing_promotes_an_active_verified_account_without_prompting(self):
        client = Client(self.app)
        self.register("existing@velora.test", display_name="Existing", client=client)
        code, output = self.run_cli([
            "create-admin", "--email", "existing@velora.test", "--grant-existing", "--confirm",
        ])
        self.assertEqual(code, 0, output)
        self.assertIn("Granted administrator role to existing@velora.test", output)
        self.assertNotIn("Display name", output, "promotion must not ask for a display name")
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = 'existing@velora.test'").fetchone()
        self.assertEqual(row["role"], "admin")
        self.assertEqual(row["display_name"], "Existing", "promotion must not rename the account")
        self.assertIn("cli.admin_role_granted", self.audit_actions())

    def test_grant_existing_refuses_missing_unverified_and_inactive_accounts(self):
        code, output = self.run_cli([
            "create-admin", "--email", "ghost@velora.test", "--grant-existing", "--confirm",
        ])
        self.assertEqual(code, 1, output)
        self.assertIn("No account exists", output)

        # Unverified account: the operator is told exactly what to do first.
        client = Client(self.app)
        client.post("/api/auth/signup", json_body={
            "display_name": "Unverified", "email": "unverified@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        })
        code, output = self.run_cli([
            "create-admin", "--email", "unverified@velora.test", "--grant-existing", "--confirm",
        ])
        self.assertEqual(code, 1, output)
        self.assertIn("has not confirmed its email address", output)
        self.assertIn("user verify-email", output)
        self.assertNotIn("admin", self.audit_actions())

        # Archived account.
        member = Client(self.app)
        self.register("archived@velora.test", display_name="Archived", client=member)
        with self.db.transaction() as conn:
            conn.execute("UPDATE users SET status = 'archived' WHERE email = 'archived@velora.test'")
        code, output = self.run_cli([
            "create-admin", "--email", "archived@velora.test", "--grant-existing", "--confirm",
        ])
        self.assertEqual(code, 1, output)
        self.assertIn("cannot be promoted", output)

    def test_existing_account_is_not_silently_converted(self):
        client = Client(self.app)
        self.register("member@velora.test", display_name="Member", client=client)
        code, output = self.run_cli([
            "create-admin", "--email", "member@velora.test", "--display-name", "Member",
            "--password", "correct-horse-9", "--confirm",
        ])
        self.assertEqual(code, 1, output)
        self.assertIn("--grant-existing", output)
        with self.db.connection() as conn:
            role = conn.execute("SELECT role FROM users WHERE email = 'member@velora.test'").fetchone()["role"]
        self.assertEqual(role, "member")

    def test_refuses_a_weak_password(self):
        code, output = self.run_cli([
            "create-admin", "--email", "ops@velora.test", "--display-name", "Operator",
            "--password", "short", "--confirm",
        ])
        self.assertEqual(code, 1, output)
        self.assertIn("Nothing was changed", output)
        with self.db.connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        self.assertEqual(count, 0)

    def test_interactive_confirmation_can_be_declined(self):
        code, output = self.run_cli(
            ["create-admin", "--email", "ops@velora.test", "--display-name", "Operator",
             "--password", "correct-horse-9"],
            stdin="no thanks",
        )
        self.assertEqual(code, 1, output)
        self.assertIn("Nothing was changed", output)
        with self.db.connection() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        self.assertEqual(count, 0)


class MaintenanceCommandTests(CliTestCase):
    def test_status_reports_configuration_and_contents(self):
        code, output = self.run_cli(["status"])
        self.assertEqual(code, 0, output)
        self.assertIn("migrations applied : 11", output)
        self.assertIn("accounts           : 0 (active verified admins: 0)", output)
        self.assertIn("btcpay configured  : True", output)
        for secret in ("test-api-key", "test-webhook-secret", "test-secret-key-not-used-outside-tests"):
            self.assertNotIn(secret, output)

    def test_admin_list_is_empty_before_provisioning(self):
        code, output = self.run_cli(["admin-list"])
        self.assertEqual(code, 1, output)
        self.assertIn("No administrators exist", output)
        self.assertIn("create-admin", output)

    def test_migrate_is_idempotent(self):
        code, output = self.run_cli(["migrate"])
        self.assertEqual(code, 0, output)
        self.assertIn("already up to date", output)

    def test_verify_email_marks_an_account_and_records_why(self):
        client = Client(self.app)
        client.post("/api/auth/signup", json_body={
            "display_name": "Out of Band", "email": "oob@velora.test",
            "password": "correct-horse-9", "password_confirm": "correct-horse-9",
            "adult_attestation": True,
        })
        code, output = self.run_cli(["user", "verify-email", "--email", "oob@velora.test"])
        self.assertEqual(code, 0, output)
        self.assertIn("Reminder", output)
        self.assertIn("self-attestation is still not age verification", output)
        with self.db.connection() as conn:
            row = conn.execute("SELECT email_verified FROM users WHERE email = 'oob@velora.test'").fetchone()
        self.assertEqual(row["email_verified"], 1)
        self.assertIn("cli.email_verified", self.audit_actions())

        code, output = self.run_cli(["user", "verify-email", "--email", "oob@velora.test"])
        self.assertEqual(code, 0, output)
        self.assertIn("already verified", output)

    def test_generate_secret_is_long_and_unique(self):
        _, first = self.run_cli(["generate-secret"])
        _, second = self.run_cli(["generate-secret"])
        first, second = first.strip(), second.strip()
        self.assertGreater(len(first), 40)
        self.assertNotEqual(first, second)

    def test_maintenance_expires_and_prunes(self):
        code, output = self.run_cli(["maintenance"])
        self.assertEqual(code, 0, output)
        self.assertIn("Expired 0 membership period(s)", output)

    def test_hash_password_outputs_a_verifiable_hash(self):
        from server.security import verify_password

        code, output = self.run_cli(["hash-password", "--password", "correct-horse-9"])
        self.assertEqual(code, 0, output)
        digest = output.strip()
        self.assertTrue(digest.startswith("pbkdf2_"))
        self.assertTrue(verify_password("correct-horse-9", digest))

    def test_no_seed_or_demo_command_exists(self):
        parser = cli.build_parser()
        commands = set()
        for action in parser._actions:  # noqa: SLF001 - argparse introspection
            if getattr(action, "choices", None) and isinstance(action.choices, dict):
                commands.update(action.choices)
        for forbidden in ("seed", "demo", "fixtures", "load-sample"):
            self.assertNotIn(forbidden, commands)
        self.assertIn("create-admin", commands)


class MigrationSafetyTests(CliTestCase):
    def test_status_does_not_crash_on_an_unmigrated_database(self):
        from server.db import Database
        from server.config import build_config

        fresh = build_config({**self.environ, "VELORA_DB_PATH": str(self.tmp / "unmigrated.db")})
        set_config(fresh)
        self.addCleanup(set_config, self.config)
        self.assertTrue(Database(fresh.db_path))
        code, output = self.run_cli(["status"], config=fresh)
        self.assertEqual(code, 0, output)
        self.assertIn("migrations pending : 0001", output)
        self.assertIn("not inspected", output)

    def test_serve_refuses_to_start_on_a_checksum_mismatch(self):
        with self.db.transaction() as conn:
            conn.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = '0002'")
        code, output = self.run_cli(
            ["create-admin", "--email", "ops@velora.test", "--display-name", "Operator",
             "--password", "correct-horse-9", "--confirm"]
        )
        self.assertEqual(code, 1, output)
        self.assertIn("changed after it was applied", output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
