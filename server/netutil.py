"""Client address helpers for deployments behind a proxy.

Velora never stores a raw address: the application hashes whatever this module
returns and keeps only the hash, so rate limiting can recognise a repeat caller
without keeping an IP log. Nothing here geolocates anybody — an address is used
for abuse control and for nothing else.

``trust_proxy`` must be enabled explicitly (``VELORA_TRUST_PROXY=1``); when it is
off, forwarded headers are ignored entirely, because a client can always send
them itself.
"""

from __future__ import annotations

import ipaddress
from typing import Mapping

# Cloudflare's dedicated client-address header, checked before X-Forwarded-For.
CF_CONNECTING_IP = "cf-connecting-ip"
# The generic proxy header, whose first entry is the original client.
X_FORWARDED_FOR = "x-forwarded-for"
X_REAL_IP = "x-real-ip"
TRUE_CLIENT_IP = "true-client-ip"

MAX_HEADER_CHARS = 128


def normalize_ip(value: str | None) -> str | None:
    """Return a canonical, validated address, or ``None`` when unusable."""
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_HEADER_CHARS:
        return None
    # Bracketed IPv6, optionally with a port: ``[2001:db8::1]:443``.
    if candidate.startswith("["):
        closing = candidate.find("]")
        if closing == -1:
            return None
        candidate = candidate[1:closing]
    # Some proxies append a port to a bare IPv4 address (``203.0.113.7:443``).
    # A bare IPv6 address is never split: it legitimately contains colons.
    elif candidate.count(":") == 1:
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def forwarded_client_ip(headers: Mapping[str, str]) -> str | None:
    """Best-effort client address from proxy headers (validated, never guessed)."""
    lowered = {str(key).lower(): value for key, value in headers.items()}

    for header in (CF_CONNECTING_IP, TRUE_CLIENT_IP, X_REAL_IP):
        candidate = normalize_ip(lowered.get(header))
        if candidate:
            return candidate

    chain = lowered.get(X_FORWARDED_FOR) or ""
    for hop in chain.split(","):
        candidate = normalize_ip(hop)
        if candidate:
            return candidate
    return None


__all__ = [
    "CF_CONNECTING_IP",
    "TRUE_CLIENT_IP",
    "X_FORWARDED_FOR",
    "X_REAL_IP",
    "forwarded_client_ip",
    "normalize_ip",
]
