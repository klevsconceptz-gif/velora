"""Request and response primitives.

Velora ships its own tiny WSGI plumbing so the project stays on the Python
standard library, per the project's "no external CDN / no heavyweight
dependency" constraint.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

_HEADER_UNSAFE = re.compile(r"[^A-Za-z0-9_\-]")


class ApiError(Exception):
    """An error safe to serialise to the client."""

    def __init__(self, status: int, code: str, message: str, *, field: str | None = None, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.details = details

    def to_payload(self) -> dict:
        payload = {"error": {"code": self.code, "message": self.message}}
        if self.field:
            payload["error"]["field"] = self.field
        if self.details:
            payload["error"]["details"] = self.details
        return payload


def bad_request(message: str, *, code: str = "invalid_request", field: str | None = None) -> ApiError:
    return ApiError(400, code, message, field=field)


def unauthorized(message: str = "Sign in to continue.", *, code: str = "unauthenticated") -> ApiError:
    return ApiError(401, code, message)


def forbidden(message: str = "You do not have access to this resource.", *, code: str = "forbidden") -> ApiError:
    return ApiError(403, code, message)


def not_found(message: str = "Not found.", *, code: str = "not_found") -> ApiError:
    return ApiError(404, code, message)


def conflict(message: str, *, code: str = "conflict", field: str | None = None) -> ApiError:
    return ApiError(409, code, message, field=field)


def unprocessable(message: str, *, code: str = "unprocessable", field: str | None = None) -> ApiError:
    return ApiError(422, code, message, field=field)


def rate_limited(message: str, retry_after: int) -> ApiError:
    error = ApiError(429, "rate_limited", message)
    error.details = {"retry_after_seconds": retry_after}
    return error


def unavailable(message: str, *, code: str = "unavailable") -> ApiError:
    return ApiError(503, code, message)


MAX_UNREAD_BODY = 64 * 1024 * 1024
# When a body is too large we still read (and discard) up to this much before
# answering, so the client can finish writing and actually read the 413 instead
# of seeing a connection reset. Beyond this the socket is closed early on purpose.
MAX_DRAIN_BODY = 32 * 1024 * 1024


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes
    environ: dict = field(default_factory=dict)
    remote_addr: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    json_body: dict | None = None
    state: dict = field(default_factory=dict)
    _body_loaded: bool = False

    def declared_length(self) -> int:
        try:
            return max(0, int(self.headers.get("content-length") or 0))
        except ValueError:
            return 0

    def load_body(self, limit: int | None) -> None:
        """Read the request body once, enforcing the caller's size budget."""
        if self._body_loaded:
            return
        self._body_loaded = True
        length = self.declared_length()
        over_limit = bool(limit and length > limit)
        if over_limit or length > MAX_UNREAD_BODY:
            # Drain a bounded amount first: closing the socket mid-upload turns a
            # clear "that image is too large" message into a network error in the
            # browser, because the client is still writing when the response lands.
            self._drain(min(length, MAX_DRAIN_BODY))
            raise ApiError(413, "payload_too_large",
                           "That request is larger than Velora accepts.")
        if not length:
            self.body = b""
            return
        self.body = self._read_body(self.environ["wsgi.input"], length)

    def _drain(self, count: int, chunk: int = 64 * 1024) -> int:
        """Read and discard up to ``count`` bytes of the request body."""
        if count <= 0:
            return 0
        stream = self.environ.get("wsgi.input")
        if stream is None:
            return 0
        remaining = count
        drained = 0
        while remaining > 0:
            block = self._read_body(stream, min(chunk, remaining))
            if not block:
                break
            drained += len(block)
            remaining -= len(block)
        return drained

    @staticmethod
    def _read_body(stream, count: int) -> bytes:
        """Read from the request stream, turning a stall into a clean 408."""
        try:
            return stream.read(count)
        except (TimeoutError, OSError) as exc:  # socket timeout or client abort
            raise ApiError(
                408, "request_timeout",
                "The upload stopped before it finished. Nothing was saved — please try again.",
            ) from exc

    # ---- input helpers --------------------------------------------------------
    def header(self, name: str, default: str | None = None) -> str | None:
        return self.headers.get(name.lower(), default)

    def query_one(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        if not values:
            return default
        return values[0]

    def query_int(self, name: str, default: int | None = None, *, minimum=None, maximum=None) -> int | None:
        raw = self.query_one(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            raise bad_request(f"{name} must be a whole number.", field=name) from None
        if minimum is not None and value < minimum:
            value = minimum
        if maximum is not None and value > maximum:
            value = maximum
        return value

    def query_bool(self, name: str, default: bool = False) -> bool:
        raw = self.query_one(name)
        if raw is None:
            return default
        return raw.lower() in ("1", "true", "yes", "on")

    def json(self) -> dict:
        if self.json_body is not None:
            return self.json_body
        if not self.body:
            self.json_body = {}
            return self.json_body
        try:
            parsed = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise bad_request("Request body must be valid JSON.") from None
        if not isinstance(parsed, dict):
            raise bad_request("Request body must be a JSON object.")
        self.json_body = parsed
        return self.json_body

    def form(self) -> dict[str, str]:
        parsed = parse_qs(self.body.decode("utf-8"), keep_blank_values=True)
        return {key: values[0] for key, values in parsed.items()}

    @property
    def origin(self) -> str | None:
        return self.header("origin")

    @property
    def referer_origin(self) -> str | None:
        referer = self.header("referer")
        if not referer:
            return None
        parsed = urlparse(referer)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def host(self) -> str | None:
        return self.header("host")

    def client_ip(self, trust_proxy: bool) -> str | None:
        """The caller's address, or ``None`` when it cannot be trusted.

        Forwarded headers are only read when the operator opted in with
        ``VELORA_TRUST_PROXY``; Cloudflare's ``CF-Connecting-IP`` is preferred
        over ``X-Forwarded-For``, and every candidate is validated as an IP
        address so junk never reaches the rate limiter or the audit log.
        """
        if trust_proxy:
            from .netutil import forwarded_client_ip

            forwarded = forwarded_client_ip(self.headers)
            if forwarded:
                return forwarded
        return self.remote_addr

    def user_agent(self) -> str:
        return (self.header("user-agent") or "")[:300]

    def is_safe_method(self) -> bool:
        return self.method.upper() in SAFE_METHODS


def build_request(environ: dict, *, max_body_bytes: int | None = None,
                  read_body: bool = True) -> Request:
    method = (environ.get("REQUEST_METHOD") or "GET").upper()
    raw_path = environ.get("PATH_INFO") or "/"
    path = raw_path if raw_path.startswith("/") else "/" + raw_path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    query = parse_qs(environ.get("QUERY_STRING") or "", keep_blank_values=True)

    headers: dict[str, str] = {}
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            headers[key[5:].replace("_", "-").lower()] = value
    if environ.get("CONTENT_TYPE"):
        headers["content-type"] = environ["CONTENT_TYPE"]
    if environ.get("CONTENT_LENGTH"):
        headers["content-length"] = environ["CONTENT_LENGTH"]

    cookies: dict[str, str] = {}
    raw_cookies = environ.get("HTTP_COOKIE")
    if raw_cookies:
        jar = SimpleCookie()
        try:
            jar.load(raw_cookies)
        except Exception:  # pragma: no cover - malformed cookie header
            jar = SimpleCookie()
        cookies = {key: morsel.value for key, morsel in jar.items()}

    request = Request(
        method=method,
        path=path,
        query=query,
        headers=headers,
        body=b"",
        environ=environ,
        remote_addr=environ.get("REMOTE_ADDR"),
        cookies=cookies,
    )
    if read_body:
        request.load_body(max_body_bytes)
    return request


def normalize_header_value(value: str) -> str:
    return _HEADER_UNSAFE.sub("", str(value))


class Response:
    def __init__(
        self,
        status: int = 200,
        body: bytes | str = b"",
        *,
        content_type: str = JSON_CONTENT_TYPE,
        headers: dict[str, str] | None = None,
        cookies: list[str] | None = None,
    ):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = dict(headers or {})
        self.cookies = list(cookies or [])

    def wsgi(self, start_response):
        headers = [("Content-Type", self.content_type), ("Content-Length", str(len(self.body)))]
        for key, value in self.headers.items():
            headers.append((normalize_header_value(key), str(value)))
        for cookie in self.cookies:
            headers.append(("Set-Cookie", cookie))
        start_response(f"{self.status} {http_reason(self.status)}", headers)
        return [self.body]


_STATUS_REASONS = {
    200: "OK",
    201: "Created",
    204: "No Content",
    302: "Found",
    304: "Not Modified",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    413: "Payload Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


def http_reason(status: int) -> str:
    return _STATUS_REASONS.get(status, "OK")


def json_response(payload, status: int = 200, **kwargs) -> Response:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return Response(status, body, **kwargs)


def error_response(error: ApiError, *, headers: dict | None = None) -> Response:
    response = json_response(error.to_payload(), status=error.status)
    if error.status == 429:
        # Tell well-behaved clients (and the browser's devtools) when to retry.
        retry_after = (error.details or {}).get("retry_after_seconds") if error.details else None
        if retry_after:
            response.headers["Retry-After"] = str(int(retry_after))
    if headers:
        response.headers.update(headers)
    return response


def serialize_cookie(
    name: str,
    value: str,
    *,
    max_age: int | None = None,
    path: str = "/",
    secure: bool = True,
    http_only: bool = True,
    same_site: str = "Lax",
) -> str:
    parts = [f"{normalize_header_value(name)}={value}", f"Path={path}"]
    if max_age is not None:
        parts.append(f"Max-Age={int(max_age)}")
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    if same_site:
        parts.append(f"SameSite={same_site}")
    return "; ".join(parts)
