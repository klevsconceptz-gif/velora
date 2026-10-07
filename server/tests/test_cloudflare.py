"""Cloudflare deployment files stay consistent with the server.

* the Worker's proxy behaviour (Node unit tests, no dependencies),
* ``web/_headers`` carries the same security policy as ``server/app.py``,
* ``wrangler.jsonc`` is wired the way docs/CLOUDFLARE.md says and holds no secrets,
* test code and config files are neither uploaded to Cloudflare nor served by the origin.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from ..app import CSP_TEMPLATE, SECURITY_HEADERS
from .harness import Client, VeloraTestCase

ROOT = Path(__file__).resolve().parents[2]


def _strip_jsonc(text: str) -> str:
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _wrangler() -> dict:
    return json.loads(_strip_jsonc((ROOT / "wrangler.jsonc").read_text()))


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
    def test_config_is_wired_as_documented(self):
        config = _wrangler()
        self.assertEqual(config["main"], "cloudflare/worker.js")
        self.assertTrue((ROOT / config["main"]).is_file())
        assets = config["assets"]
        self.assertEqual(assets["directory"], "./web")
        self.assertEqual(assets["run_worker_first"], ["/api/*"])
        self.assertEqual(assets["not_found_handling"], "single-page-application")
        self.assertEqual(assets["binding"], "ASSETS")
        self.assertRegex(config["compatibility_date"], r"^\d{4}-\d{2}-\d{2}$")

    def test_the_config_holds_no_secret_and_no_placeholder_origin(self):
        text = (ROOT / "wrangler.jsonc").read_text()
        self.assertEqual(_wrangler()["vars"], {"ORIGIN_URL": ""})
        self.assertNotRegex(text, r'"EDGE_SECRET"\s*:')
        self.assertNotIn("example.com", json.dumps(_wrangler()))

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
