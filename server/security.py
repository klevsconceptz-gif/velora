"""Cryptographic helpers.

Rules enforced here:

* Passwords are hashed with PBKDF2-HMAC-SHA256 and a per-password random salt.
  Plaintext passwords are never written to the database, logged or returned.
* Session, verification and invitation tokens are random 256-bit values; only
  their SHA-256 digest is stored, so a database leak does not hand over live
  sessions.
* Every comparison of secret material uses :func:`hmac.compare_digest`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

PBKDF2_ALGORITHM = "sha256"
SALT_BYTES = 16
TOKEN_BYTES = 32

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 200


class PasswordPolicyError(ValueError):
    pass


def hash_password(password: str, iterations: int, *, salt: bytes | None = None) -> str:
    """Return a self-describing hash string: ``pbkdf2_sha256$iters$salt$hash``."""
    salt = salt or secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(PBKDF2_ALGORITHM, password.encode("utf-8"), salt, iterations)
    return "pbkdf2_{alg}${iters}${salt}${hash}".format(
        alg=PBKDF2_ALGORITHM,
        iters=iterations,
        salt=base64.b64encode(salt).decode("ascii"),
        hash=base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations_raw, salt_raw, hash_raw = stored.split("$", 3)
        if not scheme.startswith("pbkdf2_"):
            return False
        algorithm = scheme.split("_", 1)[1]
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_raw)
        expected = base64.b64decode(hash_raw)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac(algorithm, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def validate_password(password: str) -> str:
    """Validate a new password. Returns the password unchanged or raises."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError("password is too long")
    return password


def new_token(nbytes: int = TOKEN_BYTES) -> str:
    return secrets.token_urlsafe(nbytes)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left), str(right))


def hmac_hex(secret: str | bytes, message: str | bytes, algorithm: str = "sha256") -> str:
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if isinstance(message, str):
        message = message.encode("utf-8")
    return hmac.new(secret, message, getattr(hashlib, algorithm)).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def opaque_reference(prefix: str, *, nbytes: int = 24) -> str:
    """A non-sequential, non-guessable public identifier (e.g. order reference)."""
    cleaned = "".join(ch for ch in prefix.upper() if ch.isalnum())
    return f"{cleaned}-{secrets.token_hex(nbytes).upper()}"


def fingerprint(secret: bytes, *parts: str | None) -> str:
    """Salted, truncated hash used for audit/rate-limit correlation.

    Never a lookup key for identity: the value cannot be reversed without the
    server secret and is never exposed to another user.
    """
    material = "|".join(part or "" for part in parts)
    return hmac_hex(secret, material)[:32]


def mask_email(email: str) -> str:
    """``ada@example.com`` -> ``a**@example.com`` for non-owner displays."""
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    keep = local[0]
    return f"{keep}{'*' * max(2, len(local) - 1)}@{domain}"
