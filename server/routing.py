"""Route table primitives.

A route is a method, a path pattern with ``<name>`` / ``<int:name>`` parameters,
a handler, and an authorization policy. The policy is enforced centrally in
:mod:`server.app` so a new endpoint cannot accidentally ship without an access
check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PARAM = re.compile(r"<(?:(\w+):)?(\w+)>")

CONVERTERS = {
    "str": r"[^/]+",
    "int": r"\d+",
    "handle": r"[A-Za-z0-9_-]{1,40}",
    "slug": r"[A-Za-z0-9_-]{1,60}",
}

# Authorization policies
AUTH_NONE = "none"          # public
AUTH_OPTIONAL = "optional"  # public, but personalise when signed in
AUTH_REQUIRED = "required"  # signed-in account
AUTH_VERIFIED = "verified"  # signed-in and email-confirmed
AUTH_CREATOR = "creator"    # verified creator or administrator
AUTH_ADMIN = "admin"        # verified administrator


@dataclass
class Route:
    method: str
    path: str
    handler: object
    auth: str = AUTH_NONE
    csrf: bool = True
    name: str = ""
    body_limit: int | None = None  # bytes; None means use the global limit
    regex: re.Pattern = field(default=None, repr=False)  # type: ignore[assignment]
    converters: dict = field(default_factory=dict, repr=False)

    def compile(self) -> "Route":
        converters: dict[str, str] = {}

        def replace(match: re.Match) -> str:
            kind = match.group(1) or "str"
            name = match.group(2)
            converters[name] = kind
            return f"(?P<{name}>{CONVERTERS.get(kind, CONVERTERS['str'])})"

        pattern = _PARAM.sub(replace, self.path)
        self.regex = re.compile(f"^{pattern}$")
        self.converters = converters
        if not self.name:
            self.name = f"{self.method} {self.path}"
        return self

    def match(self, method: str, path: str) -> dict | None:
        if method != self.method:
            return None
        found = self.regex.match(path)
        if not found:
            return None
        params = {}
        for key, value in found.groupdict().items():
            if self.converters.get(key) == "int":
                params[key] = int(value)
            else:
                params[key] = value
        return params


def route(method: str, path: str, **options) -> callable:
    def decorator(handler):
        handler._velora_route = Route(method=method.upper(), path=path, handler=handler, **options).compile()
        return handler

    return decorator


def collect(module) -> list[Route]:
    routes = []
    for value in vars(module).values():
        candidate = getattr(value, "_velora_route", None)
        if isinstance(candidate, Route):
            routes.append(candidate)
    return routes
