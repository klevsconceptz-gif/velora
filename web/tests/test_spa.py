"""Frontend checks: served shell, asset hygiene and the API contract.

The browser cannot be trusted to enforce anything, but a broken link between the
frontend and the API is a real defect. These tests read the shipped JavaScript,
so a renamed endpoint or a route that no longer exists fails here instead of in a
user's browser.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import path setup
    sys.path.insert(0, str(REPO_ROOT))

from server.routes import ROUTES  # noqa: E402
from server.tests.harness import Client, VeloraTestCase  # noqa: E402

WEB_ROOT = REPO_ROOT / "web"
ASSETS = WEB_ROOT / "assets"
VIEW_FILES = {
    "views-public": ASSETS / "views-public.js",
    "views-account": ASSETS / "views-account.js",
    "views-studio": ASSETS / "views-studio.js",
    "views-admin": ASSETS / "views-admin.js",
}

EXPORT_RE = re.compile(r"^export\s+(?:async\s+)?function\s+(\w+)", re.MULTILINE)
EXPORT_LIST_RE = re.compile(r"^export\s*\{([^}]*)\}", re.MULTILINE)
IMPORT_NS_RE = re.compile(r"^import\s+\*\s+as\s+(\w+)\s+from\s+'\./([\w-]+)\.js'", re.MULTILINE)
ROUTE_ENTRY_RE = re.compile(
    r"\{\s*name:\s*'(?P<name>[\w-]+)',\s*pattern:\s*'(?P<pattern>[^']*)',\s*view:\s*(?P<view>\w+)\.(?P<fn>\w+)",
    re.MULTILINE,
)
API_CALL_RE = re.compile(r"api\(\s*(?P<quote>`|')(?P<path>/api/[^`']*)(?P=quote)")


def exported_names(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    names = set(EXPORT_RE.findall(source))
    for block in EXPORT_LIST_RE.findall(source):
        for entry in block.split(","):
            name = entry.strip().split(" as ")[-1].strip()
            if name:
                names.add(name)
    return names


def iter_api_calls(source: str):
    """Yield ``(method, path_template, line_number)`` for every ``api(...)`` call."""
    for match in API_CALL_RE.finditer(source):
        path = match.group("path")
        tail = source[match.end():match.end() + 220]
        method_match = re.search(r"method:\s*'(?P<method>[A-Z]+)'", tail)
        method = method_match.group("method") if method_match else "GET"
        line = source.count("\n", 0, match.start()) + 1
        yield method, path, line


def candidate_paths(template: str) -> list[str]:
    """Concrete paths for a template such as ``/api/threads/${threadId}``.

    Handles both simple placeholders and calls that append an optional query
    string after a nested template (``/api/studio/posts${status ? ...}``).
    """
    candidates = []
    prefix = re.split(r"\$\{|`|\?", template, maxsplit=1)[0]
    if prefix:
        candidates.append(prefix.rstrip("/") or "/")
    path_only = template.split("?", 1)[0]
    if "`" not in path_only:
        for replacement in ("1", "sample-handle", "VLR-ABC123"):
            candidates.append(re.sub(r"\$\{[^}]*\}", replacement, path_only))
    return candidates


def negation_aware_offsets(source: str, needle: str) -> list[int]:
    """Offsets of ``needle`` that are *not* prefixed by a negating phrase.

    Velora deliberately writes copy such as "no cards, no Lightning" and
    "nothing renews automatically". Those sentences must not trip the guard that
    looks for unsupported features or misleading claims.
    """
    lowered = source.lower()
    offsets = []
    start = 0
    while True:
        index = lowered.find(needle, start)
        if index < 0:
            break
        window = lowered[max(0, index - 40):index]
        negated = any(marker in window for marker in
                      ("no ", "not ", "never ", "nothing ", "without ", "cannot ", "can't ",
                       "does not ", "do not ", "neither ", "nor ", "false:"))
        if not negated:
            offsets.append(index)
        start = index + len(needle)
    return offsets


class ShellServingTests(VeloraTestCase):
    def test_shell_is_served_with_its_mount_points(self):
        anonymous = Client(self.app)
        response = anonymous.get("/")
        self.assertEqual(response.status, 200, response.text)
        self.assertIn("text/html", response.header("content-type", ""))
        body = response.text
        for needle in ('id="app"', 'id="site-header-inner"', 'id="site-footer"',
                       'href="assets/styles.css"', 'src="assets/app.js"',
                       'type="module"', 'class="skip-link"', 'lang="en"',
                       'name="viewport"', 'tabindex="-1"'):
            self.assertIn(needle, body, needle)
        self.assertEqual(response.header("cache-control"), "no-store")
        self.assertNotIn("http://", body.replace("http://www.w3.org/2000/svg", ""))

    def test_assets_are_served_with_correct_types_and_caching(self):
        anonymous = Client(self.app)
        expectations = {
            "/assets/app.js": "text/javascript",
            "/assets/ui.js": "text/javascript",
            "/assets/api.js": "text/javascript",
            "/assets/views-public.js": "text/javascript",
            "/assets/views-account.js": "text/javascript",
            "/assets/views-studio.js": "text/javascript",
            "/assets/views-admin.js": "text/javascript",
            "/assets/styles.css": "text/css",
        }
        for path, content_type in expectations.items():
            response = anonymous.get(path)
            self.assertEqual(response.status, 200, path)
            self.assertIn(content_type, response.header("content-type", ""), path)
            self.assertIn("max-age", response.header("cache-control", ""), path)
            self.assertGreater(len(response.body), 200, path)

    def test_missing_assets_and_traversal_are_refused(self):
        anonymous = Client(self.app)
        self.assertEqual(anonymous.get("/assets/nope.js").status, 404)
        self.assertIn(anonymous.get("/assets/%2e%2e/server/config.py").status, (400, 404))
        self.assertEqual(anonymous.get("/assets/../server/config.py").status, 404)

    def test_security_headers_are_present(self):
        anonymous = Client(self.app)
        response = anonymous.get("/")
        self.assertEqual(response.header("x-frame-options"), "DENY")
        self.assertEqual(response.header("x-content-type-options"), "nosniff")
        csp = response.header("content-security-policy", "")
        for directive in ("default-src 'self'", "script-src 'self'", "object-src 'none'",
                          "frame-ancestors 'none'", "base-uri 'none'"):
            self.assertIn(directive, csp)


class AssetHygieneTests(unittest.TestCase):
    """Static reading of the shipped frontend: no CDNs, no secrets, no HTML injection."""

    def setUp(self):
        self.files = {
            path.relative_to(WEB_ROOT).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(WEB_ROOT.rglob("*"))
            if path.is_file() and path.suffix in (".js", ".css", ".html")
        }
        self.assertTrue(self.files, "expected frontend files to read")

    def test_no_third_party_network_dependencies(self):
        banned = ("cdn.", "unpkg", "jsdelivr", "cdnjs", "googleapis", "gstatic",
                  "bootstrapcdn", "jquery", "react", "vue.js", "angular")
        for name, source in self.files.items():
            lowered = source.lower()
            for needle in banned:
                self.assertNotIn(needle, lowered, f"{name} references {needle}")

    def test_no_remote_urls_except_document_namespaces(self):
        for name, source in self.files.items():
            stripped = source.replace("http://www.w3.org/2000/svg", "")
            for match in re.findall(r"https?://[^\s'\"<>)]+", stripped):
                self.fail(f"{name} references a remote URL: {match}")

    def test_no_localhost_or_hardcoded_hosts(self):
        for name, source in self.files.items():
            self.assertNotIn("localhost", source, name)
            self.assertNotIn("127.0.0.1", source, name)
            self.assertNotIn("0.0.0.0", source, name)

    def test_no_html_injection_helpers(self):
        patterns = (r"\.innerHTML\s*=", r"\.outerHTML\s*=", r"insertAdjacentHTML\(",
                    r"document\.write\(", r"\beval\(", r"new Function\(")
        for name, source in self.files.items():
            for pattern in patterns:
                self.assertIsNone(re.search(pattern, source), f"{name} matches {pattern}")

    def test_no_secrets_or_environment_names_leak_into_the_frontend(self):
        for name, source in self.files.items():
            self.assertIsNone(re.search(r"VELORA_[A-Z_]+", source), name)
            for needle in ("password_hash", "token_hash", "api_key", "webhook_secret",
                           "BEGIN PRIVATE KEY", "mnemonic", "seed phrase of"):
                self.assertNotIn(needle, source, f"{name} mentions {needle}")

    def test_no_location_collection_or_geolocation_gate(self):
        for name, source in self.files.items():
            for needle in ("geolocation", "country allowlist", "vpn detection", "ip lookup",
                           "navigator.language"):
                self.assertEqual(negation_aware_offsets(source, needle), [],
                                 f"{name} uses {needle} outside a negation")

    def test_no_unsupported_payment_providers(self):
        for name, source in self.files.items():
            for needle in ("stripe", "braintree", "paypal", "checkout.com", "adyen",
                           "lightning", "coinbase commerce", "credit card number"):
                self.assertEqual(negation_aware_offsets(source, needle), [],
                                 f"{name} references {needle} outside a negation")

    def test_age_attestation_copy_is_honest(self):
        combined = "\n".join(self.files.values())
        self.assertIn("self-attestation", combined)
        for misleading in ("verified age", "age verified", "we verify your age",
                           "identity verified", "age check passed"):
            self.assertNotIn(misleading, combined.lower())

    def test_no_automatic_renewal_language(self):
        combined = "\n".join(self.files.values())
        self.assertIn("nothing renews automatically", combined.lower())
        for misleading in ("renews automatically", "auto-renew", "automatic charge", "recurring"):
            self.assertEqual(negation_aware_offsets(combined, misleading), [],
                             f"misleading renewal wording: {misleading}")

    def test_accessibility_basics_are_in_the_stylesheet(self):
        css = self.files["assets/styles.css"]
        self.assertIn(":focus-visible", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("color-scheme", css)

    def test_every_view_module_renders_a_page_heading(self):
        for module, path in VIEW_FILES.items():
            source = path.read_text(encoding="utf-8")
            page_functions = re.findall(r"^export async function (\w*Page)\(", source, re.MULTILINE)
            self.assertTrue(page_functions, module)
            self.assertIn("el('h1'", source, f"{module} must render a page heading")
            if module == "views-public":
                # These pages build their own markup, so each one needs its own h1.
                self.assertGreaterEqual(source.count("el('h1'"), len(page_functions), module)
            else:
                # These modules share a shell/section helper that renders the h1.
                self.assertIn("el('h1'", source, module)
                self.assertGreaterEqual(source.count("el('h1'"), 1, module)


class RouteTableTests(unittest.TestCase):
    def setUp(self):
        self.app_source = (ASSETS / "app.js").read_text(encoding="utf-8")
        self.namespaces = {alias: module for alias, module in IMPORT_NS_RE.findall(self.app_source)
                           if module.startswith("views-")}

    def test_view_modules_are_imported(self):
        self.assertEqual(sorted(self.namespaces),
                         ["accountViews", "adminViews", "publicViews", "studioViews"])

    def test_every_route_points_at_an_exported_view(self):
        entries = list(ROUTE_ENTRY_RE.finditer(self.app_source))
        self.assertGreaterEqual(len(entries), 30)
        exports = {module: exported_names(path) for module, path in VIEW_FILES.items()}
        for entry in entries:
            module = self.namespaces[entry.group("view")]
            self.assertIn(module, exports, entry.group("view"))
            self.assertIn(entry.group("fn"), exports[module],
                          f"{entry.group('name')} -> {module}.{entry.group('fn')} is not exported")

    def test_route_patterns_are_unique_and_well_formed(self):
        patterns = [entry.group("pattern") for entry in ROUTE_ENTRY_RE.finditer(self.app_source)]
        self.assertEqual(len(patterns), len(set(patterns)), "duplicate route pattern")
        for pattern in patterns:
            self.assertTrue(pattern.startswith("/"), pattern)
            self.assertNotIn("..", pattern)

    def test_client_routes_cover_the_named_screens(self):
        patterns = set(ROUTE_ENTRY_RE.findall(self.app_source))
        names = {entry.group("name") for entry in ROUTE_ENTRY_RE.finditer(self.app_source)}
        for expected in ("home", "discover", "creator", "post", "privacy", "safety", "signup",
                         "login", "verify", "invite", "apply", "account", "orientations",
                         "memberships", "feed", "messages", "reports", "checkout", "studio",
                         "studioPosts", "studioTiers", "studioMembers", "studioPayout", "admin",
                         "adminUsers", "adminUser", "adminCreators", "adminApplications",
                         "adminPosts", "adminTiers", "adminReports", "adminPayments",
                         "adminLedger", "adminAudit", "adminInvitations"):
            self.assertIn(expected, names, expected)
        self.assertTrue(patterns)


class ApiContractTests(unittest.TestCase):
    """Every ``api(...)`` call in the frontend must match a real server route."""

    def test_api_calls_resolve_to_server_routes(self):
        failures = []
        total = 0
        for module, path in VIEW_FILES.items():
            source = path.read_text(encoding="utf-8")
            for method, template, line in iter_api_calls(source):
                total += 1
                if not any(
                    route.match(method, candidate) is not None
                    for candidate in candidate_paths(template)
                    for route in ROUTES
                ):
                    failures.append(f"{module}.js:{line} {method} {template}")
        self.assertGreater(total, 60, "expected the frontend to call a broad API surface")
        self.assertEqual(failures, [], "frontend calls without a matching server route:\n" +
                         "\n".join(failures))

    def test_mutating_calls_declare_their_method(self):
        for module, path in VIEW_FILES.items():
            source = path.read_text(encoding="utf-8")
            for method, template, line in iter_api_calls(source):
                if method != "GET":
                    self.assertRegex(method, r"^(POST|PATCH|PUT|DELETE)$",
                                     f"{module}.js:{line} {method} {template}")

    def test_server_routes_are_not_called_with_query_placeholders(self):
        for module, path in VIEW_FILES.items():
            source = path.read_text(encoding="utf-8")
            for _method, template, line in iter_api_calls(source):
                self.assertNotIn("<", template, f"{module}.js:{line}: {template}")


class PrivacySurfaceTests(unittest.TestCase):
    def test_privacy_page_reads_the_public_summary(self):
        source = (ASSETS / "views-public.js").read_text(encoding="utf-8")
        account = (ASSETS / "views-account.js").read_text(encoding="utf-8")
        self.assertIn("/api/privacy/summary", source)
        self.assertIn("prefer_not_to_say", account)

    def test_discovery_never_sends_an_orientation_filter(self):
        source = (ASSETS / "views-public.js").read_text(encoding="utf-8")
        discover = source[source.index("export async function discover"):]
        query_block = discover[discover.index("async function load()"):discover.index("let debounce")]
        self.assertNotIn("orientation", query_block)
        self.assertIn("search.set('category'", query_block)
        self.assertIn("search.set('sort'", query_block)

    def test_locked_content_renders_a_teaser_only(self):
        source = (ASSETS / "views-public.js").read_text(encoding="utf-8")
        card = source[source.index("export function postCard"):source.index("export async function postPage")]
        self.assertIn("post.locked", card)
        self.assertIn("locked-panel", card)
        # The locked branch explains the server-side gate and never prints the body.
        locked_branch = card[card.index("if (post.locked)"):card.index("} else {")]
        self.assertNotIn("post.body", locked_branch)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
