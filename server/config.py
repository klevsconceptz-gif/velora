"""Runtime configuration.

Everything sensitive comes from the environment. Nothing sensitive has a
default: if a payment or email setting is missing the matching feature reports
itself as unavailable instead of pretending to work.
"""

from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .wallets import parse_asset_list, parse_method_overrides

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = REPO_ROOT / "var" / "velora.db"
DEFAULT_MEDIA_ROOT = REPO_ROOT / "var" / "media"
DEFAULT_SECRET_FILE = REPO_ROOT / "var" / "dev_secret.key"

ENVIRONMENTS = ("development", "test", "production")

PLATFORM_FEE_PERCENT = 10
MEMBERSHIP_PERIOD_DAYS = 30


def _env(environ: dict | None, key: str, default: str | None = None) -> str | None:
    source = environ if environ is not None else os.environ
    value = source.get(key)
    if value is None or value == "":
        return default
    return value


def _env_bool(environ: dict | None, key: str, default: bool = False) -> bool:
    raw = _env(environ, key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(environ: dict | None, key: str, default: int) -> int:
    raw = _env(environ, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_list(environ: dict | None, key: str) -> tuple[str, ...]:
    raw = _env(environ, key)
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class RateLimitRule:
    """A token/window allowance for one action."""

    limit: int
    window_seconds: int

    def describe(self) -> str:
        return f"{self.limit} per {self.window_seconds}s"


# Deliberately conservative. Tuned so a real person is never blocked but scripted
# abuse is throttled hard.
DEFAULT_RATE_LIMITS: dict[str, RateLimitRule] = {
    "signup": RateLimitRule(5, 3600),
    "login": RateLimitRule(10, 900),
    "login_failed": RateLimitRule(5, 900),
    "password_reset": RateLimitRule(5, 3600),
    "resend_verification": RateLimitRule(5, 3600),
    "verify_attempt": RateLimitRule(20, 3600),
    "invitation_accept": RateLimitRule(10, 3600),
    "checkout": RateLimitRule(12, 3600),
    "payment_poll": RateLimitRule(60, 900),
    "message_send": RateLimitRule(30, 900),
    "thread_create": RateLimitRule(10, 900),
    "report_create": RateLimitRule(10, 3600),
    "media_upload": RateLimitRule(40, 3600),
    "webhook": RateLimitRule(600, 300),
    "admin_mutation": RateLimitRule(120, 900),
    "page_view": RateLimitRule(120, 3600),
    "studio_write": RateLimitRule(120, 900),
}


@dataclass(frozen=True)
class Config:
    environment: str
    db_path: str
    media_root: str
    secret_key: bytes
    public_base_url: str | None
    allowed_origins: tuple[str, ...]
    allowed_origin_suffixes: tuple[str, ...]
    frame_ancestors: str
    trust_proxy: bool
    secure_cookies: str  # auto | always | never
    session_ttl_seconds: int
    verification_ttl_seconds: int
    invitation_ttl_seconds: int
    pbkdf2_iterations: int
    max_body_bytes: int
    max_upload_bytes: int
    allowed_image_types: tuple[str, ...]
    # Email
    email_transport: str  # smtp | file | none
    email_from: str
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_starttls: bool
    email_outbox_path: str | None
    # Payments
    btcpay_url: str | None
    btcpay_store_id: str | None
    btcpay_api_key: str | None
    btcpay_webhook_secret: str | None
    btcpay_invoice_ttl_minutes: int
    btcpay_allow_localhost: bool
    rate_limits: dict[str, RateLimitRule] = field(default_factory=lambda: dict(DEFAULT_RATE_LIMITS))
    # Which catalog assets this instance offers at checkout (None = the whole
    # catalog) and any per-asset BTCPay payment-method id overrides.
    payment_methods: tuple[str, ...] | None = None
    payment_method_ids: dict[str, str] = field(default_factory=dict)

    # ---- feature availability -------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def email_configured(self) -> bool:
        if self.email_transport == "smtp":
            return bool(self.smtp_host and self.email_from)
        if self.email_transport == "file":
            return bool(self.email_outbox_path and self.email_from)
        return False

    @property
    def btcpay_configured(self) -> bool:
        return bool(self.btcpay_url and self.btcpay_store_id and self.btcpay_api_key)

    @property
    def offered_assets(self) -> tuple[str, ...]:
        """Catalog asset keys the operator allows, in catalog order."""
        from .wallets import ASSET_KEYS

        if self.payment_methods is None:
            return ASSET_KEYS
        return tuple(key for key in ASSET_KEYS if key in self.payment_methods)

    def method_id(self, asset_key: str) -> str:
        """The BTCPay payment-method id used for an asset."""
        from .wallets import ASSETS

        return self.payment_method_ids.get(asset_key) or ASSETS[asset_key].method_id

    @property
    def btcpay_webhooks_configured(self) -> bool:
        return bool(self.btcpay_configured and self.btcpay_webhook_secret)

    def rate_limit(self, name: str) -> RateLimitRule:
        return self.rate_limits.get(name, RateLimitRule(60, 3600))

    def public_features(self) -> dict:
        """Feature flags safe to expose to the browser.

        Booleans only — never the configured host, store id or key material.
        """
        return {
            "environment": self.environment,
            "email_configured": self.email_configured,
            "btcpay_configured": self.btcpay_configured,
            "btcpay_webhook_configured": self.btcpay_webhooks_configured,
            "platform_fee_percent": PLATFORM_FEE_PERCENT,
            "membership_period_days": MEMBERSHIP_PERIOD_DAYS,
            "payment_methods": list(self.offered_assets) if self.btcpay_configured else [],
            "max_upload_bytes": self.max_upload_bytes,
            "allowed_image_types": list(self.allowed_image_types),
            "framing": "restricted" if self.frame_ancestors == "'none'" else "permitted",
        }


def _load_secret(environ: dict | None, environment: str) -> bytes:
    provided = _env(environ, "VELORA_SECRET_KEY")
    if provided:
        return provided.encode("utf-8")
    if environment == "production":
        raise RuntimeError(
            "VELORA_SECRET_KEY must be set when VELORA_ENV=production. "
            "Generate one with: python -m server.cli generate-secret"
        )
    # Development/test convenience: a persistent, private local key file so that
    # sessions survive restarts. Never used in production.
    path = Path(_env(environ, "VELORA_DEV_SECRET_FILE", str(DEFAULT_SECRET_FILE)))
    if path.exists():
        return path.read_bytes().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48).encode("ascii")
    path.write_bytes(value)
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover - platform dependent
        pass
    return value


def build_config(environ: dict | None = None) -> Config:
    environment = (_env(environ, "VELORA_ENV", "development") or "development").lower()
    if environment not in ENVIRONMENTS:
        raise RuntimeError(f"VELORA_ENV must be one of {ENVIRONMENTS}")

    transport = (_env(environ, "VELORA_EMAIL_TRANSPORT", "none") or "none").lower()
    if transport not in ("smtp", "file", "none"):
        raise RuntimeError("VELORA_EMAIL_TRANSPORT must be smtp, file or none")
    if environment == "production" and transport == "file":
        raise RuntimeError("VELORA_EMAIL_TRANSPORT=file is a local development helper")

    # Clickjacking protection. Production refuses to be framed at all; a
    # development instance allows framing so it can be embedded in an editor or
    # preview pane entirely on the operator's own machine.
    default_ancestors = "*" if environment == "development" else "'none'"
    frame_ancestors = (_env(environ, "VELORA_FRAME_ANCESTORS", default_ancestors)
                       or default_ancestors).strip()

    btcpay_url = _env(environ, "VELORA_BTCPAY_URL")
    if btcpay_url:
        btcpay_url = btcpay_url.rstrip("/")

    image_types = _env_list(environ, "VELORA_ALLOWED_IMAGE_TYPES") or (
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    )

    return Config(
        environment=environment,
        db_path=_env(environ, "VELORA_DB_PATH", str(DEFAULT_DB_PATH)) or str(DEFAULT_DB_PATH),
        media_root=_env(environ, "VELORA_MEDIA_ROOT", str(DEFAULT_MEDIA_ROOT)) or str(DEFAULT_MEDIA_ROOT),
        secret_key=_load_secret(environ, environment),
        public_base_url=(_env(environ, "VELORA_PUBLIC_BASE_URL") or None),
        allowed_origins=_env_list(environ, "VELORA_ALLOWED_ORIGINS"),
        allowed_origin_suffixes=_env_list(environ, "VELORA_ALLOWED_ORIGIN_SUFFIXES"),
        frame_ancestors=frame_ancestors,
        trust_proxy=_env_bool(environ, "VELORA_TRUST_PROXY", False),
        secure_cookies=(_env(environ, "VELORA_SECURE_COOKIES", "auto") or "auto").lower(),
        session_ttl_seconds=_env_int(environ, "VELORA_SESSION_TTL_SECONDS", 60 * 60 * 24 * 14),
        verification_ttl_seconds=_env_int(environ, "VELORA_VERIFICATION_TTL_SECONDS", 60 * 60 * 24),
        invitation_ttl_seconds=_env_int(environ, "VELORA_INVITATION_TTL_SECONDS", 60 * 60 * 72),
        pbkdf2_iterations=_env_int(environ, "VELORA_PBKDF2_ITERATIONS", 240_000),
        max_body_bytes=_env_int(environ, "VELORA_MAX_BODY_BYTES", 256 * 1024),
        max_upload_bytes=_env_int(environ, "VELORA_MAX_UPLOAD_BYTES", 8 * 1024 * 1024),
        allowed_image_types=tuple(image_types),
        email_transport=transport,
        email_from=_env(environ, "VELORA_EMAIL_FROM", "Velora <no-reply@localhost>") or "",
        smtp_host=_env(environ, "VELORA_SMTP_HOST") or None,
        smtp_port=_env_int(environ, "VELORA_SMTP_PORT", 587),
        smtp_username=_env(environ, "VELORA_SMTP_USERNAME") or None,
        smtp_password=_env(environ, "VELORA_SMTP_PASSWORD") or None,
        smtp_starttls=_env_bool(environ, "VELORA_SMTP_STARTTLS", True),
        email_outbox_path=_env(environ, "VELORA_EMAIL_OUTBOX") or None,
        btcpay_url=btcpay_url,
        btcpay_store_id=_env(environ, "VELORA_BTCPAY_STORE_ID") or None,
        btcpay_api_key=_env(environ, "VELORA_BTCPAY_API_KEY") or None,
        btcpay_webhook_secret=_env(environ, "VELORA_BTCPAY_WEBHOOK_SECRET") or None,
        btcpay_invoice_ttl_minutes=_env_int(environ, "VELORA_BTCPAY_INVOICE_TTL_MINUTES", 60),
        btcpay_allow_localhost=_env_bool(environ, "VELORA_BTCPAY_ALLOW_LOCALHOST", False),
        payment_methods=parse_asset_list(_env(environ, "VELORA_PAYMENT_METHODS")),
        payment_method_ids=parse_method_overrides(_env(environ, "VELORA_PAYMENT_METHOD_IDS")),
    )


_config: Config | None = None


def get_config(refresh: bool = False) -> Config:
    """Process-wide configuration, built once from the environment."""
    global _config
    if _config is None or refresh:
        _config = build_config()
    return _config


def set_config(config: Config) -> None:
    """Install an explicit configuration (used by tests and the CLI)."""
    global _config
    _config = config
