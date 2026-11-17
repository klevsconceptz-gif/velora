"""Supported payment assets and receiving-address validation.

Velora records a *destination* for each asset a creator accepts. Nothing here
moves funds, proves wallet ownership or talks to a blockchain: it is a format
check strong enough to catch typos and wrong-network pastes (checksums are
verified wherever the address format has one) and to refuse seed phrases and
private keys.

Every asset belongs to exactly one network. Tether (USDT) and USD Coin exist on
several networks and an address from one network is not safe to use on another,
so each token/network pair is its own entry (``usdt_trc20``, ``usdt_erc20`` ...).

The catalog is data: adding a coin means adding one :class:`Asset` row with a
``family`` that already has a validator, or writing a new validator below.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal

from .validation import ValidationError

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Asset:
    key: str            # stable Velora identifier, used in URLs and the database
    symbol: str         # what members see ("USDT")
    name: str           # "Tether"
    network: str        # "Tron (TRC-20)"
    decimals: int       # smallest unit = 10**-decimals
    method_id: str      # default BTCPay payment-method id (operator can override)
    family: str         # address validator family
    params: dict = field(default_factory=dict)
    example: str = ""
    warning: str = ""
    stablecoin: bool = False

    @property
    def label(self) -> str:
        return f"{self.symbol} · {self.network}" if self.network != self.name else self.symbol

    def public(self) -> dict:
        return {
            "key": self.key,
            "symbol": self.symbol,
            "name": self.name,
            "network": self.network,
            "label": self.label,
            "decimals": self.decimals,
            "stablecoin": self.stablecoin,
            "example": self.example,
            "warning": self.warning,
        }


_TOKEN_WARNING = (
    "Use an address that can receive this token on exactly this network. "
    "Sending the same token over a different network can lose the funds."
)

CATALOG: tuple[Asset, ...] = (
    Asset("btc", "BTC", "Bitcoin", "Bitcoin", 8, "BTC-CHAIN", "bitcoin",
          {"hrp": "bc", "p2pkh": (0x00,), "p2sh": (0x05,)},
          example="bc1… or 1… or 3…"),
    Asset("ltc", "LTC", "Litecoin", "Litecoin", 8, "LTC-CHAIN", "bitcoin",
          {"hrp": "ltc", "p2pkh": (0x30,), "p2sh": (0x32, 0x05)},
          example="ltc1… or L… or M…"),
    Asset("bch", "BCH", "Bitcoin Cash", "Bitcoin Cash", 8, "BCH-CHAIN", "cashaddr",
          {"prefix": "bitcoincash", "p2pkh": (0x00,), "p2sh": (0x05,)},
          example="bitcoincash:q… or 1…"),
    Asset("doge", "DOGE", "Dogecoin", "Dogecoin", 8, "DOGE-CHAIN", "base58check",
          {"p2pkh": (0x1E,), "p2sh": (0x16,)},
          example="D…"),
    Asset("xmr", "XMR", "Monero", "Monero", 12, "XMR-CHAIN", "monero",
          example="4… or 8… (95 characters)"),
    Asset("eth", "ETH", "Ethereum", "Ethereum", 18, "ETH-CHAIN", "evm",
          example="0x… (42 characters)"),
    Asset("bnb", "BNB", "BNB", "BNB Smart Chain", 18, "BNB-CHAIN", "evm",
          example="0x… (42 characters)"),
    Asset("trx", "TRX", "Tron", "Tron", 6, "TRX-CHAIN", "tron",
          example="T… (34 characters)"),
    Asset("sol", "SOL", "Solana", "Solana", 9, "SOL-CHAIN", "solana",
          example="32–44 base58 characters"),
    Asset("xrp", "XRP", "XRP", "XRP Ledger", 6, "XRP-CHAIN", "ripple",
          example="r…"),
    # Tether: one entry per network, because the address must match the network.
    Asset("usdt_trc20", "USDT", "Tether", "Tron (TRC-20)", 6, "USDT_TRC20-CHAIN", "tron",
          example="T… (34 characters)", warning=_TOKEN_WARNING, stablecoin=True),
    Asset("usdt_erc20", "USDT", "Tether", "Ethereum (ERC-20)", 6, "USDT_ERC20-CHAIN", "evm",
          example="0x… (42 characters)", warning=_TOKEN_WARNING, stablecoin=True),
    Asset("usdt_bep20", "USDT", "Tether", "BNB Smart Chain (BEP-20)", 18, "USDT_BEP20-CHAIN", "evm",
          example="0x… (42 characters)", warning=_TOKEN_WARNING, stablecoin=True),
    Asset("usdt_sol", "USDT", "Tether", "Solana (SPL)", 6, "USDT_SOL-CHAIN", "solana",
          example="32–44 base58 characters", warning=_TOKEN_WARNING, stablecoin=True),
    Asset("usdc_erc20", "USDC", "USD Coin", "Ethereum (ERC-20)", 6, "USDC_ERC20-CHAIN", "evm",
          example="0x… (42 characters)", warning=_TOKEN_WARNING, stablecoin=True),
)

ASSETS: dict[str, Asset] = {asset.key: asset for asset in CATALOG}
ASSET_KEYS: tuple[str, ...] = tuple(ASSETS)
DEFAULT_ASSET = "btc"


def get_asset(key) -> Asset | None:
    return ASSETS.get(key) if isinstance(key, str) else None


def parse_method_overrides(raw: str | None) -> dict[str, str]:
    """``usdt_trc20=USDT-TRON,eth=ETH-CHAIN`` -> {key: BTCPay method id}."""
    overrides: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        key, _, method = part.partition("=")
        key, method = key.strip().lower(), method.strip()
        if key not in ASSETS:
            raise RuntimeError(f"VELORA_PAYMENT_METHOD_IDS names an unknown asset: {key!r}")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{2,64}", method):
            raise RuntimeError(f"VELORA_PAYMENT_METHOD_IDS has an invalid method id for {key}")
        overrides[key] = method
    return overrides


def parse_asset_list(raw: str | None) -> tuple[str, ...] | None:
    """``btc,eth`` -> keys. ``None`` (unset) means every catalog asset."""
    if raw is None or not raw.strip():
        return None
    keys: list[str] = []
    for part in raw.split(","):
        key = part.strip().lower()
        if not key:
            continue
        if key not in ASSETS:
            raise RuntimeError(f"VELORA_PAYMENT_METHODS names an unknown asset: {key!r}")
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def to_atomic(value, decimals: int) -> int | None:
    """Decimal coin amount (as BTCPay reports it) -> integer smallest units."""
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except Exception:  # noqa: BLE001 - InvalidOperation and friends
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return int((amount * (Decimal(10) ** decimals)).to_integral_value())


def format_atomic(units, decimals: int) -> str | None:
    """Integer smallest units -> exact decimal string, trailing zeros trimmed."""
    if units is None or units == "":
        return None
    try:
        value = int(units)
    except (TypeError, ValueError):
        return None
    text = f"{Decimal(value).scaleb(-decimals):.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# ---------------------------------------------------------------------------
# Encoding helpers (stdlib only)
# ---------------------------------------------------------------------------

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_RIPPLE = "rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def b58decode(text: str, alphabet: str = _B58) -> bytes | None:
    number = 0
    for char in text:
        index = alphabet.find(char)
        if index < 0:
            return None
        number = number * 58 + index
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    pad = len(text) - len(text.lstrip(alphabet[0]))
    return b"\x00" * pad + body


def b58encode(data: bytes, alphabet: str = _B58) -> str:
    number = int.from_bytes(data, "big")
    out = ""
    while number:
        number, rem = divmod(number, 58)
        out = alphabet[rem] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return alphabet[0] * pad + out


def _double_sha(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def b58check_decode(text: str, alphabet: str = _B58) -> bytes | None:
    """Decode and verify a Base58Check string; returns version byte + payload."""
    raw = b58decode(text, alphabet)
    if raw is None or len(raw) < 5:
        return None
    body, checksum = raw[:-4], raw[-4:]
    return body if _double_sha(body)[:4] == checksum else None


def b58check_encode(body: bytes, alphabet: str = _B58) -> str:
    return b58encode(body + _double_sha(body)[:4], alphabet)


def _bech32_polymod(values) -> int:
    generator = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if (top >> i) & 1 else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def bech32_decode(text: str):
    """Return ``(hrp, data, spec)`` or ``None``. ``spec`` is bech32 or bech32m."""
    if any(ord(c) < 33 or ord(c) > 126 for c in text):
        return None
    if text.lower() != text and text.upper() != text:
        return None
    text = text.lower()
    pos = text.rfind("1")
    if pos < 1 or pos + 7 > len(text) or len(text) > 90:
        return None
    if any(c not in _BECH32 for c in text[pos + 1:]):
        return None
    hrp = text[:pos]
    data = [_BECH32.find(c) for c in text[pos + 1:]]
    constant = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if constant == 1:
        return hrp, data[:-6], "bech32"
    if constant == 0x2BC830A3:
        return hrp, data[:-6], "bech32m"
    return None


def _convert_bits(data, from_bits: int, to_bits: int, pad: bool) -> list[int] | None:
    acc = bits = 0
    out: list[int] = []
    max_value = (1 << to_bits) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            return None
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & max_value)
    if pad:
        if bits:
            out.append((acc << (to_bits - bits)) & max_value)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & max_value):
        return None
    return out


def _cashaddr_polymod(values) -> int:
    chk = 1
    for value in values:
        top = chk >> 35
        chk = ((chk & 0x07FFFFFFFF) << 5) ^ value
        for bit, generator in enumerate((0x98F2BC8E61, 0x79B76D99E2, 0xF33E5FB3C4, 0xAE2EABE2A8, 0x1E4F43E470)):
            if (top >> bit) & 1:
                chk ^= generator
    return chk ^ 1


# Keccak-256 (the pre-NIST padding Ethereum uses) for EIP-55 checksums.
_KECCAK_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_KECCAK_ROT = (
    (0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56), (27, 20, 39, 8, 14),
)
_M64 = (1 << 64) - 1


def _rol(value: int, shift: int) -> int:
    shift %= 64
    return ((value << shift) | (value >> (64 - shift))) & _M64 if shift else value


def keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0)
    padded[-1] |= 0x80
    state = [[0] * 5 for _ in range(5)]
    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for i in range(rate // 8):
            state[i % 5][i // 5] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        for rnd in range(24):
            c = [state[x][0] ^ state[x][1] ^ state[x][2] ^ state[x][3] ^ state[x][4] for x in range(5)]
            d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    state[x][y] ^= d[x]
            b = [[0] * 5 for _ in range(5)]
            for x in range(5):
                for y in range(5):
                    b[y][(2 * x + 3 * y) % 5] = _rol(state[x][y], _KECCAK_ROT[x][y])
            for x in range(5):
                for y in range(5):
                    state[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
            state[0][0] ^= _KECCAK_RC[rnd]
    out = b"".join(state[i % 5][i // 5].to_bytes(8, "little") for i in range(rate // 8))
    return out[:32]


def eip55_checksum(address_hex: str) -> str:
    lowered = address_hex.lower()
    digest = keccak256(lowered.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if int(digest[i], 16) >= 8 else c for i, c in enumerate(lowered))


# ---------------------------------------------------------------------------
# Validators: each returns {"address": normalized, "kind": label} or raises.
# ---------------------------------------------------------------------------


class _Bad(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _bitcoin_like(address: str, asset: Asset) -> dict:
    params = asset.params
    hrp = params.get("hrp")
    if hrp and address.lower().startswith(hrp + "1"):
        decoded = bech32_decode(address)
        if decoded is None or decoded[0] != hrp:
            raise _Bad(f"That {asset.name} address has a bad checksum. Check it for typos.")
        _, data, spec = decoded
        if not data:
            raise _Bad(f"That {asset.name} address is incomplete.")
        version = data[0]
        program = _convert_bits(data[1:], 5, 8, False)
        if version > 16 or program is None or not 2 <= len(program) <= 40:
            raise _Bad(f"That {asset.name} address is not a valid segwit address.")
        if version == 0 and (spec != "bech32" or len(program) not in (20, 32)):
            raise _Bad(f"That {asset.name} address is not a valid segwit address.")
        if version != 0 and spec != "bech32m":
            raise _Bad(f"That {asset.name} address is not a valid segwit address.")
        return {"address": address.lower(), "kind": "bech32" if version == 0 else "bech32m"}
    body = b58check_decode(address)
    if body is not None and len(body) == 21:
        if body[0] in params.get("p2pkh", ()):
            return {"address": address, "kind": "p2pkh"}
        if body[0] in params.get("p2sh", ()):
            return {"address": address, "kind": "p2sh"}
    raise _Bad(f"That is not a valid {asset.name} address for this network (check the first characters and "
               "for typos).")


def _base58check(address: str, asset: Asset) -> dict:
    body = b58check_decode(address)
    if body is not None and len(body) == 21:
        if body[0] in asset.params.get("p2pkh", ()):
            return {"address": address, "kind": "p2pkh"}
        if body[0] in asset.params.get("p2sh", ()):
            return {"address": address, "kind": "p2sh"}
    raise _Bad(f"That is not a valid {asset.name} address (check the first characters and for typos).")


def _cashaddr(address: str, asset: Asset) -> dict:
    prefix = asset.params["prefix"]
    if address[:1] in "13":
        body = b58check_decode(address)
        if body is not None and len(body) == 21 and body[0] in (*asset.params["p2pkh"], *asset.params["p2sh"]):
            return {"address": address, "kind": "legacy"}
        raise _Bad(f"That is not a valid {asset.name} address (check for typos).")
    if address.lower() != address and address.upper() != address:
        raise _Bad("CashAddr addresses must not mix upper and lower case.")
    lowered = address.lower()
    if ":" in lowered:
        given, _, payload = lowered.partition(":")
        if given != prefix:
            raise _Bad(f"That is not a {asset.name} address.")
    else:
        payload = lowered
    if not payload or any(c not in _BECH32 for c in payload):
        raise _Bad(f"That is not a valid {asset.name} address (check for typos).")
    values = [_BECH32.find(c) for c in payload]
    if _cashaddr_polymod([ord(c) & 31 for c in prefix] + [0] + values) != 0:
        raise _Bad(f"That {asset.name} address has a bad checksum. Check it for typos.")
    return {"address": f"{prefix}:{payload}", "kind": "cashaddr"}


def _evm(address: str, asset: Asset) -> dict:
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", address):
        raise _Bad(f"{asset.network} addresses start with 0x followed by 40 hex characters.")
    digits = address[2:]
    if digits != digits.lower() and digits != digits.upper():
        if eip55_checksum(digits) != address:
            raise _Bad("That address's mixed-case checksum does not match. Check it for typos.")
    if int(digits, 16) == 0:
        raise _Bad("The all-zero address cannot receive funds.")
    return {"address": eip55_checksum(digits), "kind": "evm"}


def _tron(address: str, asset: Asset) -> dict:
    body = b58check_decode(address)
    if body is None or len(body) != 21 or body[0] != 0x41 or len(address) != 34:
        raise _Bad("Tron addresses start with T and are 34 characters long. Check it for typos.")
    return {"address": address, "kind": "tron"}


def _solana(address: str, asset: Asset) -> dict:
    raw = b58decode(address) if 32 <= len(address) <= 44 else None
    if raw is None or len(raw) != 32:
        raise _Bad("Solana addresses are 32–44 base58 characters. Check it for typos.")
    return {"address": address, "kind": "solana"}


def _ripple(address: str, asset: Asset) -> dict:
    body = b58check_decode(address, _B58_RIPPLE)
    if body is None or len(body) != 21 or body[0] != 0x00 or not address.startswith("r"):
        raise _Bad("XRP Ledger addresses start with r. Check it for typos.")
    return {"address": address, "kind": "classic"}


def _monero(address: str, asset: Asset) -> dict:
    if not re.fullmatch(r"[48][1-9A-HJ-NP-Za-km-z]{94}", address):
        raise _Bad("Monero addresses are 95 characters and start with 4 or 8.")
    return {"address": address, "kind": "standard"}


_VALIDATORS = {
    "bitcoin": _bitcoin_like,
    "base58check": _base58check,
    "cashaddr": _cashaddr,
    "evm": _evm,
    "tron": _tron,
    "solana": _solana,
    "ripple": _ripple,
    "monero": _monero,
}

_SEED_WORDS = re.compile(r"^[a-z]+(\s+[a-z]+){11,}$")


def validate_address(asset_key, value, *, field: str = "address") -> dict:
    """Validate ``value`` as a receiving address for ``asset_key``."""
    asset = get_asset(asset_key)
    if asset is None:
        raise ValidationError("Choose a supported coin or token.", "asset")
    if not isinstance(value, str):
        raise ValidationError(f"Enter a {asset.symbol} receiving address.", field)
    address = value.strip()
    if not address:
        raise ValidationError(f"Enter a {asset.symbol} receiving address.", field)
    if _SEED_WORDS.match(address.lower()):
        raise ValidationError(
            "That looks like a recovery phrase. Never share one — enter a receiving address instead.", field)
    if len(address) < 14 or len(address) > 120:
        raise ValidationError(f"That does not look like a {asset.symbol} address.", field)
    if any(char.isspace() for char in address):
        raise ValidationError("Addresses cannot contain spaces.", field)
    try:
        return _VALIDATORS[asset.family](address, asset)
    except _Bad as exc:
        raise ValidationError(exc.message, field) from None
