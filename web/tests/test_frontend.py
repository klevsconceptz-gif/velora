"""Frontend checks that need a JavaScript engine.

Two layers beyond ``test_spa.py``:

* ``test_every_shipped_module_parses`` — every shipped module is valid ES syntax.
* ``RenderCheckTests`` — the fixtures are captured from the real API, then Node
  renders every SPA route against a DOM stub, so a view that references a field
  the API no longer returns fails here instead of in a browser.

Both are skipped when Node is unavailable; the fixture assertions still run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

WEB_TESTS = Path(__file__).resolve().parent
ASSETS = REPO_ROOT / "web" / "assets"
FIXTURES = WEB_TESTS / "fixtures" / "api.json"

NODE = shutil.which("node")
TIMEOUT = 300


def run(command: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True,
                          timeout=TIMEOUT, env=env)


class JavaScriptSyntaxTests(unittest.TestCase):
    def test_asset_list_is_intact(self):
        modules = sorted(path.name for path in ASSETS.glob("*.js"))
        self.assertEqual(
            modules,
            ["api.js", "app.js", "ui.js", "views-account.js", "views-admin.js",
             "views-public.js", "views-studio.js"],
        )

    @unittest.skipIf(NODE is None, "node is not installed")
    def test_every_shipped_module_parses(self):
        with tempfile.TemporaryDirectory() as folder:
            for source in sorted(ASSETS.glob("*.js")):
                target = Path(folder) / f"{source.stem}.mjs"
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
                result = run([NODE, "--check", str(target)])
                self.assertEqual(result.returncode, 0,
                                 f"{source.name} is not valid JavaScript:\n{result.stderr}")
        # The syntax copy must not leave stray files in the assets folder.
        self.assertEqual(sorted(path.name for path in ASSETS.glob("*.mjs")), [])


class FixtureTests(unittest.TestCase):
    """The captured payloads must be real responses, not hand-written mocks."""

    def test_fixtures_are_committed_and_complete(self):
        self.assertTrue(FIXTURES.is_file(), "run: python3 web/tests/capture_fixtures.py")
        payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
        self.assertEqual(payload["failures"], [])
        self.assertGreaterEqual(len(payload["fixtures"]), 40)
        self.assertEqual(sorted(payload["by_role"]),
                         ["admin", "anonymous", "applicant", "creator", "member"])
        for entry in payload["fixtures"].values():
            self.assertLess(entry["status"], 400)
            self.assertIsInstance(entry["json"], dict)

    def test_fixtures_cover_every_role_and_hide_secrets(self):
        raw = FIXTURES.read_text(encoding="utf-8")
        payload = json.loads(raw)
        for role in ("anonymous", "member", "creator", "admin"):
            self.assertTrue(payload["by_role"].get(role), f"no fixtures captured for {role}")
        for secret in ("test-api-key", "test-webhook-secret", "password_hash", "session_token",
                       "velora_session", "PRIVATE KEY", "mnemonic", "correct-horse-9",
                       "test-secret-key-not-used-outside-tests"):
            self.assertNotIn(secret, raw, f"fixture file contains {secret}")


@unittest.skipIf(NODE is None, "node is not installed")
class RenderCheckTests(unittest.TestCase):
    def test_capture_then_render_every_route(self):
        """Capture into a temporary file: running tests must not rewrite the repo.

        The captured payloads carry real timestamps, so writing them over the
        committed fixtures would leave a dirty working tree on every run.
        """
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(folder) / "api.json"
            environment = {**os.environ, "VELORA_FIXTURES": str(fixture)}
            capture = run([sys.executable, "web/tests/capture_fixtures.py",
                           "--output", str(fixture)], env=environment)
            self.assertEqual(capture.returncode, 0,
                             f"fixture capture failed:\n{capture.stdout}\n{capture.stderr}")
            self.assertIn("captured", capture.stdout)
            self.assertTrue(fixture.is_file(), "capture wrote no fixture file")

            render = run([NODE, "web/tests/render.mjs"], env=environment)
        self.assertEqual(render.returncode, 0,
                         f"frontend render check failed:\n{render.stdout}\n{render.stderr}")
        self.assertIn("frontend render check passed", render.stdout)
        # Every route must have produced a page heading.
        for line in render.stdout.splitlines():
            if line.strip().startswith(("anonymous", "member", "creator", "admin")):
                self.assertNotIn("h1=—", line, line)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
