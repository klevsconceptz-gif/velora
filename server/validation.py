"""Input validation and text sanitisation.

All text that reaches the database passes through :func:`clean_text`, which
strips control characters (including null and bidi overrides), normalises line
endings and enforces a length budget. The frontend renders text with
``textContent`` only, so stored strings can never become markup.
"""

from __future__ import annotations

import re
import unicodedata

from .security import MIN_PASSWORD_LENGTH, PasswordPolicyError, validate_password

MAX_DISPLAY_NAME = 60
MAX_BIO = 600
MAX_TAGLINE = 120
MAX_ABOUT = 2000
MAX_POST_TITLE = 140
MAX_TEASER = 280
MAX_POST_BODY = 20000
MAX_TIER_NAME = 60
MAX_TIER_DESCRIPTION = 400
MAX_MESSAGE = 4000
MAX_REPORT_DETAILS = 1000
MAX_NOTE = 2000
MAX_PITCH = 1200
MAX_DESCRIPTION_GENERAL = 300

HANDLE_PATTERN = re.compile(r"^[a-z][a-z0-9](?:[a-z0-9_-]{1,28})[a-z0-9]$")
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253})\.[A-Za-z]{2,24}$")

# Reserved so studio, admin and product routes can never be shadowed by a page.
RESERVED_HANDLES = frozenset(
    """
    about account accounts admin api app assets auth btcpay checkout creators creator dashboard
    discover explore faq feed health help home legal login logout me member members message messages
    new nope notifications oauth password payments payment privacy profile reports reset search
    security settings signin signout signup static studio support terms tier tiers tos user users
    velora verify wallet webhook webhooks
    """.split()
)

CATEGORIES = (
    ("art", "Art & illustration"),
    ("music", "Music & audio"),
    ("writing", "Writing"),
    ("photography", "Photography"),
    ("film", "Film & video"),
    ("fitness", "Fitness & wellness"),
    ("gaming", "Gaming"),
    ("education", "Education & tutorials"),
    ("food", "Food & drink"),
    ("lifestyle", "Lifestyle"),
    ("technology", "Technology"),
    ("other", "Something else"),
)
CATEGORY_KEYS = tuple(key for key, _ in CATEGORIES)

TIER_VISIBILITIES = ("public", "members")

# Optional, sensitive, private by default. Never used as a discovery filter.
ORIENTATION_PRESETS = (
    "straight",
    "gay",
    "lesbian",
    "bisexual",
    "pansexual",
    "asexual",
    "queer",
    "questioning",
    "other",
)
ORIENTATION_PREFER_NOT_TO_SAY = "prefer_not_to_say"
ORIENTATION_SELF_DESCRIBED = "self_described"

# "prefer not to say" is never shown publicly. Free-text self-descriptions are
# kept private-only so a sensitive detail a member typed can never end up on a
# public creator page.
ORIENTATION_PUBLIC_ELIGIBLE = frozenset(ORIENTATION_PRESETS)

REPORT_REASONS = (
    "spam",
    "harassment",
    "impersonation",
    "illegal_content",
    "non_consensual_content",
    "minor_safety",
    "payment_issue",
    "privacy",
    "other",
)

REPORT_TARGETS = ("user", "creator", "post", "message", "tier")

REPORT_STATUSES = ("open", "reviewing", "resolved", "dismissed")

MAX_PRICE_CENTS = 2_000_000  # $20,000 per 30-day period


class ValidationError(ValueError):
    def __init__(self, message: str, field: str):
        super().__init__(message)
        self.message = message
        self.field = field


def _fail(message: str, field: str):
    raise ValidationError(message, field)


def clean_text(
    value,
    *,
    field: str,
    required: bool = False,
    min_length: int = 0,
    max_length: int = 200,
    allow_newlines: bool = True,
    label: str | None = None,
) -> str:
    name = label or field.replace("_", " ")
    if value is None:
        value = ""
    if not isinstance(value, str):
        _fail(f"{name} must be text.", field)
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Drop C0/C1 controls, zero-width and bidi override characters.
    cleaned_chars = []
    for char in text:
        if char in ("\n", "\t"):
            if allow_newlines:
                cleaned_chars.append("\n" if char == "\t" else char)
            continue
        category = unicodedata.category(char)
        if category in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        cleaned_chars.append(char)
    text = "".join(cleaned_chars)
    if not allow_newlines:
        text = text.replace("\n", " ")
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if required and len(text) < max(1, min_length):
        _fail(f"{name} is required.", field)
    if len(text) < min_length:
        _fail(f"{name} must be at least {min_length} characters.", field)
    if len(text) > max_length:
        _fail(f"{name} must be {max_length} characters or fewer.", field)
    return text


def clean_email(value, *, field: str = "email") -> str:
    if not isinstance(value, str):
        _fail("Enter your email address.", field)
    email = unicodedata.normalize("NFKC", value).strip()
    if len(email) > 254 or "@" not in email:
        _fail("Enter a valid email address.", field)
    local, _, domain = email.rpartition("@")
    if not local or not domain:
        _fail("Enter a valid email address.", field)
    ascii_email = f"{local}@{domain}"
    if not EMAIL_PATTERN.match(ascii_email):
        _fail("Enter a valid email address.", field)
    return ascii_email.lower()


def clean_display_name(value, *, field: str = "display_name") -> str:
    name = clean_text(value, field=field, required=True, min_length=2, max_length=MAX_DISPLAY_NAME,
                      allow_newlines=False, label="Display name")
    if not any(char.isalnum() for char in name):
        _fail("Display name must contain at least one letter or number.", field)
    return name


def clean_handle(value, *, field: str = "handle") -> str:
    if not isinstance(value, str):
        _fail("Choose a handle for your page.", field)
    handle = value.strip().lower()
    if len(handle) < 3 or len(handle) > 30:
        _fail("Handles are 3-30 characters long.", field)
    if not HANDLE_PATTERN.match(handle):
        _fail("Use lowercase letters, numbers, hyphens or underscores; start and end with a letter or number.", field)
    if handle in RESERVED_HANDLES:
        _fail("That handle is reserved. Try another one.", field)
    if "--" in handle or "__" in handle:
        _fail("Avoid repeated hyphens or underscores.", field)
    return handle


def clean_slug_list(value, *, field: str, allowed: tuple[str, ...], required: bool = False) -> list[str]:
    if value is None:
        value = []
    if not isinstance(value, list):
        _fail(f"{field} must be a list.", field)
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            _fail(f"{field} must be a list of identifiers.", field)
        candidate = item.strip().lower()
        if candidate not in allowed:
            _fail(f"{field} contains an unknown value.", field)
        if candidate not in cleaned:
            cleaned.append(candidate)
    if required and not cleaned:
        _fail(f"Choose at least one option for {field}.", field)
    return cleaned


def clean_category(value, *, field: str = "category") -> str:
    if not isinstance(value, str) or value.strip().lower() not in CATEGORY_KEYS:
        _fail("Choose a category from the list.", field)
    return value.strip().lower()


def price_to_cents(value, *, field: str = "price") -> int:
    """Accept ``"12"``, ``"12.5"``, ``12.5`` or ``1250`` cents and return cents."""
    if isinstance(value, bool):
        _fail("Enter a price in US dollars.", field)
    if isinstance(value, int):
        cents = value
    elif isinstance(value, float):
        cents = int(round(value * 100))
    elif isinstance(value, str):
        raw = value.strip().replace("$", "").replace(",", "")
        if not raw:
            _fail("Enter a monthly price.", field)
        if not re.match(r"^\d{1,7}(\.\d{1,2})?$", raw):
            _fail("Enter a price like 9 or 9.99, in US dollars.", field)
        if "." in raw:
            whole, _, fraction = raw.partition(".")
            cents = int(whole) * 100 + int(fraction.ljust(2, "0"))
        else:
            cents = int(raw) * 100
    else:
        _fail("Enter a price in US dollars.", field)
    if cents < 100:
        _fail("The lowest price is $1.00.", field)
    if cents > MAX_PRICE_CENTS:
        _fail("That price is above Velora's limit of $20,000.", field)
    return cents


def clean_bool(value, *, field: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, int):
        return bool(value)
    _fail(f"{field} must be true or false.", field)


def clean_orientation(value, *, field: str = "orientation") -> dict:
    """Normalise the optional orientation payload.

    Returns ``{"value": str|None, "self_described": str|None}`` where exactly one
    of the two may be set. ``None`` for both clears the field.
    """
    if value is None:
        return {"value": None, "self_described": None}
    if isinstance(value, str):
        value = {"value": value}
    if not isinstance(value, dict):
        _fail("Orientation must be one of the listed options or your own words.", field)

    preset = value.get("value")
    self_described = value.get("self_described")
    if preset is not None:
        if not isinstance(preset, str):
            _fail("Choose one of the listed options.", field)
        preset = preset.strip().lower()
        if preset not in ORIENTATION_PRESETS and preset != ORIENTATION_PREFER_NOT_TO_SAY:
            _fail("Choose one of the listed options.", field)
    if self_described is not None:
        self_described = clean_text(
            self_described,
            field=field,
            required=False,
            min_length=2,
            max_length=MAX_DESCRIPTION_GENERAL,
            allow_newlines=False,
            label="Your own words",
        )
        if not self_described:
            self_described = None
    if preset and preset != ORIENTATION_PREFER_NOT_TO_SAY:
        # A named option wins over the free-text field.
        self_described = None
    if preset is None and self_described is None:
        return {"value": None, "self_described": None}
    return {"value": preset, "self_described": self_described}


def orientation_kind(record: dict) -> str:
    """Classify a stored orientation for the disclosure audit trail."""
    if record.get("value") == ORIENTATION_PREFER_NOT_TO_SAY:
        return ORIENTATION_PREFER_NOT_TO_SAY
    if record.get("self_described"):
        return ORIENTATION_SELF_DESCRIBED
    if record.get("value"):
        return "preset"
    return "unset"


def orientation_public_eligible(value: str | None) -> bool:
    return value in ORIENTATION_PUBLIC_ELIGIBLE


def clean_report_reason(value, *, field: str = "reason_code") -> str:
    if not isinstance(value, str) or value.strip().lower() not in REPORT_REASONS:
        _fail("Choose a reason for the report.", field)
    return value.strip().lower()


def clean_report_status(value, *, field: str = "status") -> str:
    if not isinstance(value, str) or value.strip().lower() not in REPORT_STATUSES:
        _fail("Choose a valid report status.", field)
    return value.strip().lower()


def clean_report_target(value, *, field: str = "target_type") -> str:
    if not isinstance(value, str) or value.strip().lower() not in REPORT_TARGETS:
        _fail("Choose what you are reporting.", field)
    return value.strip().lower()


def clean_btc_address(value, *, field: str = "btc_address") -> dict:
    """Validate an on-chain BTC receiving address conservatively.

    This performs a *format* check only (length, alphabet, bech32 mixed-case
    rule). Velora makes no claim that the address belongs to the person who typed
    it, and saving one never moves funds.
    """
    if not isinstance(value, str):
        _fail("Enter a Bitcoin receiving address.", field)
    address = value.strip()
    if len(address) < 14 or len(address) > 100:
        _fail("That does not look like a Bitcoin address.", field)
    if any(char.isspace() for char in address):
        _fail("Bitcoin addresses cannot contain spaces.", field)

    if address.lower().startswith("bc1") or address.upper().startswith("BC1"):
        if address != address.lower() and address != address.upper():
            _fail("bech32 addresses must not mix upper and lower case.", field)
        lowered = address.lower()
        if not re.match(r"^bc1[023456789acdefghjklmnpqrstuvwxyz]+$", lowered):
            _fail("That does not look like a valid Bitcoin address.", field)
        if len(lowered) < 14 or len(lowered) > 90:
            _fail("That Bech32 address has an unexpected length.", field)
        kind = "bech32m" if len(lowered) >= 62 else "bech32"
        return {"address": lowered, "kind": kind}

    if re.match(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$", address):
        return {"address": address, "kind": "p2pkh" if address[0] == "1" else "p2sh"}

    _fail(
        "Use an on-chain mainnet Bitcoin address starting with 1, 3 or bc1.",
        field,
    )


def validate_new_password(value, *, confirm=None, field: str = "password") -> str:
    if not isinstance(value, str):
        _fail("Choose a password.", field)
    try:
        validate_password(value)
    except PasswordPolicyError as exc:
        _fail(str(exc), field)
    if confirm is not None and value != confirm:
        _fail("Those passwords do not match.", f"{field}_confirm")
    if value.strip() == "":
        _fail("Choose a password.", field)
    return value


def password_requirements() -> dict:
    return {"min_length": MIN_PASSWORD_LENGTH, "max_length": 200}
