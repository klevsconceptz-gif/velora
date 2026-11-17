"""Shared helpers for route handlers."""

from __future__ import annotations

from ..http import Request, Response, json_response, serialize_cookie
from ..services.accounts import SESSION_COOKIE


def secure_cookies(config, request: Request) -> bool:
    mode = config.secure_cookies
    if mode == "always":
        return True
    if mode == "never":
        return False
    forwarded = (request.header("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded:
        return forwarded == "https"
    return request.environ.get("wsgi.url_scheme", "http") == "https"


def session_cookie(config, request: Request, token: str, *, max_age: int | None = None) -> str:
    return serialize_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age if max_age is not None else config.session_ttl_seconds,
        secure=secure_cookies(config, request),
        http_only=True,
        same_site="Lax",
    )


def cleared_session_cookie(config, request: Request) -> str:
    return serialize_cookie(
        SESSION_COOKIE,
        "",
        max_age=0,
        secure=secure_cookies(config, request),
        http_only=True,
        same_site="Lax",
    )


def ok(payload: dict | None = None, status: int = 200, *, cookies: list[str] | None = None) -> Response:
    return json_response(payload if payload is not None else {"ok": True}, status=status, cookies=cookies)


def created(payload: dict) -> Response:
    return json_response(payload, status=201)


__all__ = ["cleared_session_cookie", "created", "ok", "secure_cookies", "session_cookie"]
