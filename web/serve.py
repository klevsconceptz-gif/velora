#!/usr/bin/env python3
"""Local development entry point for the whole Velora app.

The API server already serves this directory (``web/``) as its static root, so a
single process delivers the JSON API, the SPA shell and the assets. This wrapper
exists so a developer can start everything from inside ``web/`` without knowing
the module layout.

    python3 web/serve.py                # http://0.0.0.0:8000
    python3 web/serve.py --port 8080
    python3 web/serve.py --no-migrate   # start even when migrations are pending

It adds no dependencies and starts no second server: it simply delegates to
``python -m server.cli serve``, which migrates first and refuses to pretend that
email or Bitcoin checkout are configured when they are not.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEB_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the Velora app locally (API + SPA).")
    parser.add_argument("--host", default=os.environ.get("VELORA_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VELORA_PORT", "8000")))
    parser.add_argument("--quiet", action="store_true", help="do not print the database path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.environ.setdefault("VELORA_ENV", "development")

    from server.cli import main as cli_main

    print(f"Velora development server · {REPO_ROOT}")
    print("  SPA   : /")
    print("  API   : /api/*")
    print("  health: /api/health")
    if not os.environ.get("VELORA_DB_PATH"):
        print("  database: ./var/velora.db (override with VELORA_DB_PATH)")
    if os.environ.get("VELORA_ENV", "development") == "development":
        print("  note  : development mode — email uses the local outbox and framing "
              "is permitted for preview panes. Production refuses both.")
    cli_args = ["serve", "--host", args.host, "--port", str(args.port)]
    if args.quiet:
        cli_args.append("--quiet")
    return cli_main(cli_args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
