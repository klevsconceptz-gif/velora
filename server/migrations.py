"""Forward-only SQL migrations.

Each ``server/migrations/NNNN_name.sql`` file is applied exactly once, inside a
transaction, and its SHA-256 checksum is recorded. Editing an already-applied
migration is detected on the next run instead of silently drifting.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .db import now_iso

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  checksum   TEXT NOT NULL,
  applied_at TEXT NOT NULL
)
"""


class MigrationError(RuntimeError):
    pass


def discover(migrations_dir: Path | None = None) -> list[tuple[str, Path]]:
    directory = Path(migrations_dir or MIGRATIONS_DIR)
    if not directory.exists():
        raise MigrationError(f"migrations directory not found: {directory}")
    files = []
    for path in sorted(directory.glob("*.sql")):
        version = path.name.split("_", 1)[0]
        if not version.isdigit():
            raise MigrationError(f"migration filename must start with a number: {path.name}")
        files.append((version, path))
    versions = [v for v, _ in files]
    if len(set(versions)) != len(versions):
        raise MigrationError("duplicate migration version numbers")
    return files


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied(conn) -> dict[str, dict]:
    conn.execute(SCHEMA_TABLE)
    rows = conn.execute("SELECT version, name, checksum, applied_at FROM schema_migrations").fetchall()
    return {row["version"]: dict(row) for row in rows}


def pending(conn, migrations_dir: Path | None = None) -> list[tuple[str, Path]]:
    done = applied(conn)
    return [(v, p) for v, p in discover(migrations_dir) if v not in done]


def verify_checksums(conn, migrations_dir: Path | None = None) -> list[str]:
    """Return a list of human-readable problems with already-applied migrations."""
    done = applied(conn)
    problems = []
    for version, path in discover(migrations_dir):
        record = done.get(version)
        if record and record["checksum"] != checksum(path):
            problems.append(f"migration {version} ({path.name}) changed after it was applied")
    return problems


def apply_all(db, migrations_dir: Path | None = None, log=print) -> list[str]:
    """Apply every pending migration. Returns the versions applied in this run."""
    applied_now: list[str] = []
    with db.connection() as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        problems = verify_checksums(conn, migrations_dir)
        if problems:
            raise MigrationError("; ".join(problems))
        for version, path in pending(conn, migrations_dir):
            sql = path.read_text(encoding="utf-8")
            log(f"applying migration {path.name}")
            # sqlite3's executescript issues an implicit COMMIT first, so the
            # transaction is opened *inside* the script text to keep a migration
            # atomic (several migrations create triggers that contain semicolons,
            # which rules out naive statement splitting).
            try:
                conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nCOMMIT;")
            except sqlite3.Error as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise MigrationError(f"migration {path.name} failed: {exc}") from None
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                (version, path.name, checksum(path), now_iso()),
            )
            applied_now.append(version)
    return applied_now


def status(db, migrations_dir: Path | None = None) -> dict:
    with db.connection() as conn:
        done = applied(conn)
        files = discover(migrations_dir)
        return {
            "applied": sorted(done.keys()),
            "total": len(files),
            "pending": [v for v, _ in files if v not in done],
            "problems": verify_checksums(conn, migrations_dir),
        }
