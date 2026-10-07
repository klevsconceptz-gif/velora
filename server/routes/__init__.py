"""Route registry.

Adding a route means decorating a handler in one of these modules with
``@route(...)``. The authorization decision travels with the route declaration
so it is visible in review rather than buried in handler bodies.
"""

from __future__ import annotations

from ..routing import Route, collect
from . import accounts, admin, content, creators, messaging, payments

_MODULES = (accounts, creators, content, payments, messaging, admin)


def build_routes() -> list[Route]:
    routes: list[Route] = []
    for module in _MODULES:
        routes.extend(collect(module))
    return routes


ROUTES: list[Route] = build_routes()

__all__ = ["ROUTES", "Route", "build_routes"]
