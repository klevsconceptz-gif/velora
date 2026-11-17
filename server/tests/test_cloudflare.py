"""Cloudflare deployment files and the archived Worker remain testable.

* the Pages deployment config points at the generated Goal system bundle and D1,
* the Pages Functions module and D1 migration are present,
* the archived Worker's proxy behaviour remains covered by Node unit tests,
* the earlier server frontend headers and static hygiene remain covered.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
import unittest
from pathlib import Path

from ..app import CSP_TEMPLATE, SECURITY_HEADERS
from .harness import Client, VeloraTestCase

ROOT = Path(__file__).resolve().parents[2]


def _wrangler() -> dict:
    return tomllib.loads((ROOT / "wrangler.toml").read_text())


def _headers_rules() -> dict[str, dict[str, str]]:
    rules: dict[str, dict[str, str]] = {}
    current = None
    for line in (ROOT / "web" / "_headers").read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            current = rules.setdefault(line.strip(), {})
        else:
            name, _, value = line.strip().partition(":")
            current[name.strip()] = value.strip()
    return rules


@unittest.skipUnless(shutil.which("node"), "node is required for the Worker tests")
class WorkerUnitTests(unittest.TestCase):
    def test_worker_node_suite_passes(self):
        result = subprocess.run(
            ["node", "--test", str(ROOT / "cloudflare" / "tests" / "worker.test.mjs")],
            capture_output=True, text=True, timeout=120, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1500:])
        self.assertRegex(result.stdout, r"# fail 0")
        self.assertNotRegex(result.stdout, r"# pass 0\b")


class HeadersParityTests(unittest.TestCase):
    def test_static_headers_match_the_server_policy(self):
        everything = _headers_rules()["/*"]
        self.assertEqual(everything["Content-Security-Policy"],
                         CSP_TEMPLATE.format(frame_ancestors="'none'"))
        for name, value in SECURITY_HEADERS.items():
            self.assertEqual(everything.get(name), value, name)
        self.assertEqual(everything["X-Frame-Options"], "DENY")
        self.assertIn("max-age=", everything["Strict-Transport-Security"])

    def test_html_is_never_cached_and_assets_are_short_lived(self):
        rules = _headers_rules()
        self.assertEqual(rules["/"]["Cache-Control"], "no-store")
        self.assertEqual(rules["/index.html"]["Cache-Control"], "no-store")
        self.assertIn("must-revalidate", rules["/assets/*"]["Cache-Control"])


class WranglerConfigTests(unittest.TestCase):
    def test_pages_config_points_at_goal_system_assets_and_d1_functions(self):
        config = _wrangler()
        self.assertEqual(config["name"], "goal-system")
        self.assertEqual(config["pages_build_output_dir"], "deploy/cloudflare-pages/site")
        self.assertRegex(config["compatibility_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(len(config["d1_databases"]), 1)
        database = config["d1_databases"][0]
        self.assertEqual(database["binding"], "DB")
        self.assertEqual(database["database_name"], "goal-system-db")
        self.assertEqual(database["migrations_dir"], "migrations")
        self.assertTrue((ROOT / "functions" / "api" / "[[path]].js").is_file())
        self.assertTrue((ROOT / "migrations" / "0001_initial.sql").is_file())

    def test_the_pages_config_contains_no_secrets(self):
        text = (ROOT / "wrangler.toml").read_text()
        self.assertNotIn("ADMIN_PASSWORD", text)
        self.assertNotIn("EDGE_SECRET", text)
        self.assertEqual(_wrangler()["d1_databases"][0]["database_id"],
                         "00000000-0000-0000-0000-000000000000")

    def test_dev_secrets_are_ignored_by_git_and_docker(self):
        self.assertIn(".dev.vars", (ROOT / ".gitignore").read_text())
        self.assertIn(".wrangler/", (ROOT / ".gitignore").read_text())
        self.assertIn("cloudflare/", (ROOT / ".dockerignore").read_text())

    def test_tests_and_source_are_not_uploaded_as_assets(self):
        ignore = (ROOT / "web" / ".assetsignore").read_text()
        for entry in ("tests/", "serve.py", "*.py"):
            self.assertIn(entry, ignore)


class OriginStaticHygieneTests(VeloraTestCase):
    def test_only_the_public_site_is_served(self):
        client = Client(self.app)
        for path in ("/serve.py", "/tests/fixtures/api.json", "/tests/test_spa.py",
                     "/.assetsignore", "/assets/.hidden.js"):
            self.assertEqual(client.get(path).status, 404, path)
        # No file extension: it falls back to the app shell, never the file itself.
        body = client.get("/_headers").text
        self.assertNotIn("X-Frame-Options", body)
        self.assertIn("<title>", body)
        self.assertEqual(client.get("/assets/app.js").status, 200)
        self.assertEqual(client.get("/").status, 200)
        self.assertEqual(client.get("/c/someone").status, 200)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
