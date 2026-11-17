"""Static file serving for the vanilla-JS frontend.

The frontend is a dependency-free single-page app: ``web/index.html`` plus
first-party ``/assets`` JavaScript and CSS. No CDN, no bundler, no build step.

Path safety: the resolved path must stay inside the web root, and only files
inside the root are served. Anything that looks like an API path never reaches
this module.
"""

from __future__ import annotations

from pathlib import Path

from .http import Request, Response, not_found

WEB_ROOT = (Path(__file__).resolve().parent.parent / "web").resolve()

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}

# Immutable-ish caching for fingerprinted assets, no-store for HTML so a deployed
# frontend is never served stale.
CACHE_ASSETS = "public, max-age=300, must-revalidate"
CACHE_HTML = "no-store"


def resolve(web_root: Path, request_path: str) -> Path | None:
    """Map a URL path onto a file inside ``web_root`` (or ``None``)."""
    relative = request_path.lstrip("/")
    if not relative:
        relative = "index.html"
    # Only the public site is served: no dot-files, no underscore config files
    # (such as Cloudflare's _headers), no test code and no source files.
    segments = [part for part in relative.split("/") if part]
    if any(part.startswith((".", "_")) or part == "tests" or part == "__pycache__" for part in segments):
        return None
    candidate = (web_root / relative).resolve()
    try:
        candidate.relative_to(web_root)
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    if not candidate.is_file() or candidate.suffix.lower() not in CONTENT_TYPES:
        return None
    return candidate


def serve_static(request: Request, config) -> Response:
    web_root = Path(getattr(config, "web_root", WEB_ROOT)).resolve()

    if request.path.startswith("/assets/"):
        target = resolve(web_root, request.path)
        if target is None:
            return _error(request, 404, "That asset does not exist.")
        return _file_response(target, cache=CACHE_ASSETS, allow_index_fallback=False)

    if request.path in ("/", "/index.html"):
        return _file_response(web_root / "index.html", cache=CACHE_HTML, fallback_html=True)

    # Single-page app routes (#/...) are client-side, but a direct hit on a
    # friendly path such as /c/handle should still return the shell.
    if request.method in ("GET", "HEAD") and not request.path.startswith("/api/"):
        if "." not in request.path.rsplit("/", 1)[-1]:
            try:
                return _file_response(web_root / "index.html", cache=CACHE_HTML, fallback_html=True)
            except Exception:  # noqa: BLE001
                return _error(request, 404, "Not found.")
        target = resolve(web_root, request.path)
        if target is None:
            return _error(request, 404, "Not found.")
        return _file_response(target, cache=CACHE_ASSETS)

    return _error(request, 404, "Not found.")


def _file_response(path: Path, *, cache: str, allow_index_fallback: bool = False,
                   fallback_html: bool = False) -> Response:
    if not path.is_file():
        if fallback_html:
            raise FileNotFoundError(path)
        raise FileNotFoundError(path)
    body = path.read_bytes()
    content_type = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    headers = {
        "Cache-Control": cache,
        "X-Content-Type-Options": "nosniff",
    }
    if fallback_html:
        headers["Vary"] = "Cookie"
    return Response(200, body, content_type=content_type, headers=headers)


def _error(request: Request, status: int, message: str) -> Response:
    if request.path.startswith("/assets/"):
        return Response(status, message, content_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
    return Response(
        status,
        _shell_message(message),
        content_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


def _shell_message(message: str) -> str:
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Velora</title><link rel=\"stylesheet\" href=\"/assets/styles.css\"></head>"
        "<body class=\"page-not-found\"><main class=\"shell\">"
        f"<h1>{message}</h1>"
        "<p><a href=\"/\">Back to Velora</a></p>"
        "</main></body></html>"
    )


__all__ = ["CONTENT_TYPES", "WEB_ROOT", "resolve", "serve_static", "not_found"]
