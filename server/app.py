"""WSGI application: request pipeline, security policy, error handling.

Cross-cutting protections applied to every request:

* Session cookies are ``HttpOnly`` and ``SameSite=Lax``; they switch to
  ``Secure`` on HTTPS (or always, when configured).
* State-changing requests that carry a session cookie must present the CSRF token
  issued with that session *and* (when an ``Origin``/``Referer`` is present) come
  from an allowed origin. Cross-site form posts and XHR therefore cannot mutate
  anything.
* No permissive CORS headers are emitted; the API is same-origin by default.
* Responses carry ``X-Content-Type-Options``, ``Referrer-Policy``,
  ``X-Frame-Options`` and a restrictive ``Content-Security-Policy`` with no
  inline scripts, so the single-page frontend runs from first-party files only.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from urllib.parse import urlparse

from .config import Config, get_config
from .http import ApiError, Request, Response, build_request, error_response, json_response
from .routing import (
    AUTH_ADMIN,
    AUTH_CREATOR,
    AUTH_NONE,
    AUTH_OPTIONAL,
    AUTH_REQUIRED,
    AUTH_VERIFIED,
    Route,
)
from .services import context as context_module
from .services.accounts import CSRF_HEADER, SESSION_COOKIE, resolve_auth

MAX_JSON_ERROR_CHARS = 400

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "form-action 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors {frame_ancestors}"
)


def security_headers(config=None) -> dict:
    """Headers applied to every response.

    Clickjacking: production sends ``frame-ancestors 'none'`` *and* the legacy
    ``X-Frame-Options: DENY``. A development instance may permit framing
    (``VELORA_FRAME_ANCESTORS``) so the app can sit inside an editor or preview
    pane; when framing is permitted the legacy header is dropped, because
    ``X-Frame-Options: DENY`` would override ``frame-ancestors`` in older
    browsers and defeat the setting.
    """
    ancestors = getattr(config, "frame_ancestors", None) or "'none'"
    headers = dict(SECURITY_HEADERS)
    headers["Content-Security-Policy"] = CSP_TEMPLATE.format(frame_ancestors=ancestors)
    if ancestors.strip() == "'none'":
        headers["X-Frame-Options"] = "DENY"
    return headers


def all_routes() -> list[Route]:
    from .routes import ROUTES

    return ROUTES


class Velora:
    def __init__(self, config: Config | None = None, *, context=None):
        self.config = config or get_config()
        self.ctx = context or context_module.build_context(self.config)
        self.routes = all_routes()
        self.security_headers = security_headers(self.config)

    # ---- WSGI -----------------------------------------------------------------
    def __call__(self, environ, start_response):
        try:
            response = self.handle(environ)
        except ApiError as error:
            response = error_response(error, headers=dict(self.security_headers))
        except Exception:  # noqa: BLE001 - last-resort safety net
            traceback.print_exc()
            response = json_response(
                {"error": {"code": "internal_error",
                           "message": "Something went wrong on Velora's side. Nothing was changed."}},
                status=500,
                headers={"Cache-Control": "no-store"},
            )
            for key, value in self.security_headers.items():
                response.headers.setdefault(key, value)
        return response.wsgi(start_response)

    # ---- pipeline -------------------------------------------------------------
    def handle(self, environ) -> Response:
        try:
            # The body is read after routing so a route can set its own budget
            # (for example a larger one for image uploads).
            request = build_request(environ, read_body=False)
        except ApiError as error:
            return error_response(error, headers=dict(self.security_headers))

        if request.path.startswith("/api/"):
            return self.handle_api(request)
        return self.handle_static(request)

    def handle_api(self, request: Request) -> Response:
        match = None
        method_mismatch = False
        for candidate in self.routes:
            if candidate.regex.match(request.path):
                if candidate.method == request.method:
                    match = candidate
                    break
                if candidate.method in ("GET", "POST") and candidate.method != "HEAD":
                    method_mismatch = True

        if match is None:
            error = ApiError(
                405 if method_mismatch else 404,
                "method_not_allowed" if method_mismatch else "not_found",
                "That API route does not exist." if not method_mismatch else "That method is not allowed here.",
            )
            return self._finish(request, error_response(error))

        params = match.match(request.method, request.path) or {}

        auth = None
        try:
            request.load_body(self._body_limit(match))
            self._enforce_origin_and_csrf(request, match)
            auth = self._resolve_auth(request)
            self._authorize(request, match, auth)
            response = match.handler(request, self.ctx, auth, params)
            if response is None:
                response = json_response({"ok": True})
            elif isinstance(response, dict):
                response = json_response(response)
        except ApiError as error:
            response = error_response(error)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            response = json_response(
                {"error": {"code": "internal_error",
                           "message": "Something went wrong on Velora's side. Nothing was changed."}},
                status=500,
            )

        return self._finish(request, response)

    def _body_limit(self, match: Route) -> int:
        """Route-specific body budget (uploads get their own, larger allowance)."""
        if match.body_limit == "upload":
            return self.config.max_upload_bytes + 8192
        if isinstance(match.body_limit, int):
            return match.body_limit
        return self.config.max_body_bytes

    def _resolve_auth(self, request: Request):
        method = request.environ.get("REQUEST_METHOD", request.method).upper()
        if method in ("GET", "HEAD", "OPTIONS"):
            cached = request.state.get("auth")
            if cached is not None:
                return cached
        if not request.cookies.get(SESSION_COOKIE):
            return None
        return resolve_auth(self.ctx, request)

    def _authorize(self, request: Request, match: Route, auth) -> None:
        from .http import forbidden, unauthorized
        from .services import accounts as accounts_service

        if match.auth == AUTH_NONE:
            return
        if match.auth == AUTH_OPTIONAL:
            return
        if auth is None:
            raise unauthorized()
        if match.auth == AUTH_REQUIRED:
            return
        if match.auth == AUTH_VERIFIED:
            accounts_service.require_verified(auth)
            return
        if match.auth == AUTH_CREATOR:
            accounts_service.require_verified(auth)
            if auth.role not in ("creator", "admin"):
                raise forbidden("Creator access is required for that action.", code="creator_required")
            return
        if match.auth == AUTH_ADMIN:
            accounts_service.require_admin(auth)
            return
        raise forbidden()

    def _enforce_origin_and_csrf(self, request: Request, match: Route) -> None:
        """Reject foreign-origin mutations, then check the anti-forgery token.

        The origin check runs for *every* state-changing request, including the
        session-less ones (signup, login, the BTCPay webhook): a browser always
        sends Origin on a cross-site POST, and a script that sends none is not a
        cross-site request. CSRF token validation is skipped for the handful of
        routes marked ``csrf=False``, which cannot use a session token.
        """
        if request.is_safe_method():
            return
        origin = request.origin or request.referer_origin
        if origin and not self._origin_allowed(request, origin):
            raise ApiError(403, "origin_rejected",
                           "Velora only accepts requests that come from its own pages.")
        if not match.csrf:
            return
        has_session = bool(request.cookies.get(SESSION_COOKIE))
        if not has_session:
            return
        token = request.header(CSRF_HEADER)
        if not token:
            raise ApiError(403, "csrf_required",
                           "This request needs Velora's anti-forgery token. Reload the page and try again.")
        session = self._resolve_auth(request)
        if session is None:
            raise ApiError(401, "unauthenticated", "Your session has expired. Sign in again.")
        from .security import constant_time_equal

        if not constant_time_equal(token, session.csrf_token):
            raise ApiError(403, "csrf_invalid",
                           "Velora's anti-forgery check failed. Reload the page and try again.")

    def _origin_allowed(self, request: Request, origin: str) -> bool:
        """Is ``origin`` one of this instance's own origins?

        A same-origin request is recognised from the Host header (or, behind a
        reverse proxy, X-Forwarded-Host). Anything else has to be listed in
        ``VELORA_ALLOWED_ORIGINS`` or match ``VELORA_ALLOWED_ORIGIN_SUFFIXES``.
        Foreign origins are refused even when a session and a valid CSRF token
        are present, so a stolen token is not enough on its own.
        """
        parsed = urlparse(origin)
        if not parsed.scheme or not parsed.netloc:
            return False
        host = parsed.netloc.lower()
        hostname = (parsed.hostname or "").lower()

        request_hosts = {
            value.strip().lower()
            for value in (
                request.host or "",
                (request.header("x-forwarded-host") or "").split(",")[0].strip().lower(),
            )
            if value.strip()
        }
        if host in request_hosts:
            return True
        # A proxy may add its own port to the forwarded host; compare host names then.
        for candidate in request_hosts:
            if hostname and hostname == candidate.split(":")[0]:
                return True

        if self.config.allowed_origins:
            allowed = {value.lower().rstrip("/") for value in self.config.allowed_origins}
            if origin.lower().rstrip("/") in allowed or f"{parsed.scheme}://{host}" in allowed:
                return True
            for entry in allowed:
                if urlparse(entry).netloc.lower() == host:
                    return True

        for suffix in self.config.allowed_origin_suffixes:
            cleaned = suffix.lower().lstrip("*.").strip(".")
            if cleaned and (hostname == cleaned or hostname.endswith(f".{cleaned}")):
                return True

        return False

    def _finish(self, request: Request, response: Response) -> Response:
        for key, value in self.security_headers.items():
            response.headers.setdefault(key, value)
        if request.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if self._cookie_secure(request):
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    def _cookie_secure(self, request: Request) -> bool:
        mode = self.config.secure_cookies
        if mode == "always":
            return True
        if mode == "never":
            return False
        forwarded = (request.header("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if forwarded:
            return forwarded == "https"
        return request.environ.get("wsgi.url_scheme", "http") == "https"

    # ---- static ---------------------------------------------------------------
    def handle_static(self, request: Request) -> Response:
        from .static import serve_static

        return self._finish(request, serve_static(request, self.config))


def create_app(config: Config | None = None) -> Velora:
    return Velora(config)


def main() -> None:  # pragma: no cover - convenience for `python -m server.app`
    from .cli import main as cli_main

    cli_main(["serve", *__import__("sys").argv[1:]])


def _json_body_preview(body: bytes) -> str:  # pragma: no cover - debugging helper
    try:
        parsed = json.loads(body.decode("utf-8"))
        return json.dumps(parsed)[:MAX_JSON_ERROR_CHARS]
    except Exception:  # noqa: BLE001
        return "<unreadable>"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent
