"""Minimal BTCPay Server Greenfield API client (on-chain coins and tokens).

A checkout asks BTCPay for one invoice restricted to the single on-chain payment
method the member chose (Bitcoin, Litecoin, Ethereum, Tether on Tron ... as
enabled on the operator's BTCPay store). Lightning is never requested.

Everything here fails closed:

* No configuration means no client, and callers must report checkout as
  unavailable rather than inventing a payment.
* The API key travels only in the request header to the operator-configured host.
  It is never logged, never returned to a browser and never stored in SQLite.
* Plain-HTTP base URLs are refused unless they point at localhost, so an API key
  cannot be sent over an unencrypted connection to a remote host.
* :func:`classify_invoice` treats anything it cannot positively verify as
  ``held``: partial, mismatched, late, overpaid, unverifiable or paid-with-a-
  different-method payments never unlock content automatically.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

DEFAULT_METHOD = "BTC-CHAIN"
DEFAULT_DECIMALS = 8
LOCALHOST_HOSTS = ("localhost", "127.0.0.1", "::1")

# BTCPay surfaces some settlement oddities in ``additionalStatus``.
ADDITIONAL_STATUS_HOLDS = {
    "paidover": ("overpaid", "The invoice was overpaid; it is held for review."),
    "overpaid": ("overpaid", "The invoice was overpaid; it is held for review."),
    "paidpartial": ("underpaid", "The invoice was underpaid; it is held for review."),
    "underpaid": ("underpaid", "The invoice was underpaid; it is held for review."),
    "paidlate": ("late_settlement", "The payment arrived after the checkout window closed."),
    "marked": ("manually_marked", "The invoice was manually marked and is held for review."),
    "markedstatus": ("manually_marked", "The invoice was manually marked and is held for review."),
    "invalid": ("invalid", "BTCPay marked this invoice invalid."),
}


class BtcPayError(RuntimeError):
    """A safe, non-secret-bearing BTCPay failure."""

    def __init__(self, message: str, *, kind: str = "transport", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


class BtcPayNotConfigured(BtcPayError):
    def __init__(self):
        super().__init__("BTCPay Server is not configured on this instance.", kind="not_configured")


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def to_units(value, decimals: int = DEFAULT_DECIMALS) -> int | None:
    """A coin amount as BTCPay reports it -> integer smallest units."""
    amount = _decimal(value)
    if amount is None or not amount.is_finite() or amount < 0:
        return None
    return int((amount * (Decimal(10) ** decimals)).to_integral_value())


def btc_to_sats(value) -> int | None:
    return to_units(value, 8)


def usd_to_cents(value) -> int | None:
    amount = _decimal(value)
    if amount is None:
        return None
    return int((amount * 100).to_integral_value())


@dataclass(frozen=True)
class NormalizedInvoice:
    """The fields Velora actually reasons about."""

    invoice_id: str
    status: str
    amount_cents: int | None
    currency: str
    due_units: int | None
    rate: str | None
    checkout_link: str | None
    paid_units: int
    settled_units: int
    confirmed: bool
    matching_payment_count: int
    other_method_payment_count: int
    unverifiable_payment_count: int
    latest_payment_at: str | None
    metadata: dict
    additional_status: str | None
    settled: bool
    processing: bool
    expired: bool
    invalid: bool
    payments_seen: int = 0
    method_id: str = DEFAULT_METHOD
    raw_keys: tuple[str, ...] = field(default_factory=tuple)


class BtcPayClient:
    """Thin client for the parts of the BTCPay API Velora needs."""

    def __init__(self, config, *, transport=None, timeout: int = 20):
        self.config = config
        self.base_url = (config.btcpay_url or "").rstrip("/")
        self.store_id = config.btcpay_store_id
        self.api_key = config.btcpay_api_key
        self.timeout = timeout
        self._transport = transport or self._urllib_transport

    # ---- configuration --------------------------------------------------------
    @classmethod
    def from_config(cls, config, **kwargs) -> "BtcPayClient":
        if not config.btcpay_configured:
            raise BtcPayNotConfigured()
        _assert_safe_base_url(config.btcpay_url or "")
        return cls(config, **kwargs)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.store_id and self.api_key)

    # ---- transport ------------------------------------------------------------
    def _urllib_transport(self, method: str, url: str, headers: dict, body: bytes | None):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise BtcPayError(f"Could not reach BTCPay Server ({exc.reason})", kind="transport") from None

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.configured:
            raise BtcPayNotConfigured()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"token {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        status, raw = self._transport(method, url, headers, body)
        text = raw.decode("utf-8", "replace") if raw else ""
        if status >= 400:
            raise BtcPayError(f"BTCPay Server rejected the request (HTTP {status}).",
                              kind="api_error", status=status)
        if not text:
            raise BtcPayError("BTCPay Server returned an empty response.", kind="bad_response", status=status)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            raise BtcPayError("BTCPay Server returned a response Velora could not read.",
                              kind="bad_response", status=status) from None
        if not isinstance(parsed, dict):
            raise BtcPayError("BTCPay Server returned an unexpected response shape.",
                              kind="bad_response", status=status)
        return parsed

    # ---- operations -----------------------------------------------------------
    def create_invoice(self, *, amount_cents: int, order_ref: str, item_description: str,
                       redirect_url: str, ttl_minutes: int, payment_method: str = DEFAULT_METHOD) -> dict:
        """Create an invoice priced in USD and payable only with ``payment_method``."""
        payload = {
            "amount": f"{amount_cents / 100:.2f}",
            "currency": "USD",
            "metadata": {
                "orderRef": order_ref,
                "orderId": order_ref,
                "itemDesc": item_description,
                "platform": "velora",
            },
            "checkout": {
                # One on-chain method per invoice. Lightning is never offered.
                "paymentMethods": [payment_method],
                "defaultPaymentMethod": payment_method,
                "expirationMinutes": int(ttl_minutes),
                "redirectURL": redirect_url,
                "redirectAutomatically": False,
            },
        }
        return self._request("POST", f"/api/v1/stores/{self.store_id}/invoices", payload)

    def enabled_payment_methods(self) -> set[str]:
        """Payment-method ids that are enabled on the configured store."""
        if not self.configured:
            raise BtcPayNotConfigured()
        url = f"{self.base_url}/api/v1/stores/{self.store_id}/payment-methods"
        headers = {"Authorization": f"token {self.api_key}", "Accept": "application/json"}
        status, raw = self._transport("GET", url, headers, None)
        if status >= 400:
            raise BtcPayError(f"BTCPay Server rejected the request (HTTP {status}).",
                              kind="api_error", status=status)
        try:
            parsed = json.loads(raw.decode("utf-8", "replace")) if raw else None
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, list):
            raise BtcPayError("BTCPay Server returned a response Velora could not read.",
                              kind="bad_response", status=status)
        enabled: set[str] = set()
        for entry in parsed:
            if not isinstance(entry, dict) or entry.get("enabled") is not True:
                continue
            method = entry.get("paymentMethodId") or entry.get("paymentMethod")
            if not method and entry.get("cryptoCode"):
                method = f"{entry['cryptoCode']}-CHAIN"
            if isinstance(method, str) and method:
                enabled.add(method)
        return enabled

    def get_invoice(self, invoice_id: str) -> dict:
        safe_id = urllib.parse.quote(str(invoice_id), safe="")
        return self._request("GET", f"/api/v1/stores/{self.store_id}/invoices/{safe_id}")


def _assert_safe_base_url(base_url: str) -> None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise BtcPayError("VELORA_BTCPAY_URL must be an http(s) URL.", kind="configuration")
    if parsed.scheme == "http":
        host = (parsed.hostname or "").lower()
        if host not in LOCALHOST_HOSTS:
            raise BtcPayError(
                "Refusing to send the BTCPay API key over plain HTTP to a remote host. "
                "Use https:// for remote BTCPay instances.",
                kind="configuration",
            )


def _due_units_from(raw: dict, method_id: str, decimals: int) -> int | None:
    """The amount BTCPay quoted for the chosen payment method, in smallest units."""
    methods = raw.get("paymentMethods") or raw.get("paymentMethodItems") or []
    if not isinstance(methods, list):
        return None
    for entry in methods:
        if not isinstance(entry, dict):
            continue
        method = entry.get("paymentMethod") or entry.get("method") or entry.get("paymentMethodId")
        if method != method_id:
            continue
        for key in ("due", "amount", "totalDue"):
            if entry.get(key) is not None:
                value = to_units(entry.get(key), decimals)
                if value is not None:
                    return value
    return None


def normalize_invoice(raw: dict, method_id: str = DEFAULT_METHOD,
                      decimals: int = DEFAULT_DECIMALS) -> NormalizedInvoice:
    """Map a BTCPay invoice payload onto Velora's own normalized shape.

    ``method_id`` is the payment method the member chose at checkout; a payment
    made any other way (another coin, Lightning, a manual mark) is counted as an
    *other-method* payment and the invoice is held for review.
    """
    payments = raw.get("payments") or []
    if not isinstance(payments, list):
        payments = []

    paid_units = 0
    matching_settled = 0
    confirmed = False
    matching_count = 0
    other_method = 0
    unverifiable = 0
    latest_payment_at = None

    for payment in payments:
        if not isinstance(payment, dict):
            unverifiable += 1
            continue
        method = payment.get("method") or payment.get("paymentMethod") or payment.get("paymentMethodId")
        amount = to_units(payment.get("amount") if payment.get("amount") is not None
                          else payment.get("value"), decimals) or 0
        paid_units += amount
        for key in ("settledTime", "receivedTime", "createdTime"):
            if payment.get(key):
                latest_payment_at = str(payment[key])
                break
        if method is None:
            # Without a payment method we cannot prove which asset was paid.
            unverifiable += 1
            continue
        if method == method_id:
            matching_count += 1
            if str(payment.get("status", "")).lower() == "settled":
                matching_settled += amount
                try:
                    confirmations = int(payment.get("confirmations") or 0)
                except (TypeError, ValueError):
                    confirmations = 0
                if confirmations > 0:
                    confirmed = True
        else:
            other_method += 1

    status = str(raw.get("status") or "Unknown")
    metadata = raw.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    additional = raw.get("additionalStatus")

    return NormalizedInvoice(
        invoice_id=str(raw.get("id") or ""),
        status=status,
        amount_cents=usd_to_cents(raw.get("amount")),
        currency=str(raw.get("currency") or "").upper(),
        due_units=_due_units_from(raw, method_id, decimals),
        rate=(str(raw.get("rate")) if raw.get("rate") is not None else None),
        checkout_link=raw.get("checkoutLink"),
        paid_units=paid_units,
        settled_units=matching_settled,
        confirmed=confirmed,
        matching_payment_count=matching_count,
        other_method_payment_count=other_method,
        unverifiable_payment_count=unverifiable,
        latest_payment_at=latest_payment_at,
        metadata=metadata,
        additional_status=str(additional) if additional else None,
        settled=status.lower() == "settled",
        processing=status.lower() in ("processing", "paid"),
        expired=status.lower() == "expired",
        invalid=status.lower() in ("invalid", "malformed"),
        payments_seen=len(payments),
        method_id=method_id,
        raw_keys=tuple(sorted(str(key) for key in raw.keys())),
    )


@dataclass(frozen=True)
class Classification:
    outcome: str          # settled | processing | pending | expired | invalid | held
    reason: str
    message: str
    units: int = 0
    rate: str | None = None

    @property
    def grants_access(self) -> bool:
        return self.outcome == "settled"


def _parsed(value: str | None):
    from datetime import datetime, timezone

    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def settlement_after_expiry(invoice: NormalizedInvoice, target) -> bool:
    """True when payment arrived after the intent's checkout window closed."""
    expiry = _parsed(getattr(target, "expires_at", None))
    settled_at = _parsed(invoice.latest_payment_at)
    if expiry is None or settled_at is None:
        return False
    return settled_at > expiry


def additional_status_hold(invoice: NormalizedInvoice) -> Classification | None:
    if not invoice.additional_status:
        return None
    key = "".join(ch for ch in invoice.additional_status.lower() if ch.isalnum())
    for candidate, (reason, message) in ADDITIONAL_STATUS_HOLDS.items():
        if candidate and candidate in key:
            return Classification("held", reason, message)
    return None


def classify_invoice(invoice: NormalizedInvoice, target) -> Classification:
    """Decide what a normalized invoice means for one payment intent.

    ``target`` is any object exposing ``order_ref``, ``amount_cents``,
    ``currency``, ``btcpay_invoice_id`` and ``expires_at``.
    """
    if invoice.invoice_id and target.btcpay_invoice_id and invoice.invoice_id != target.btcpay_invoice_id:
        return Classification("held", "invoice_mismatch",
                              "The invoice does not match the order that was checked out.")

    reference = str(invoice.metadata.get("orderRef") or invoice.metadata.get("orderId") or "")
    if invoice.settled or invoice.processing or invoice.invalid:
        if reference != target.order_ref:
            return Classification("held", "order_reference_mismatch",
                                 "The invoice's order reference does not match this order.")

    if invoice.currency and invoice.currency != (target.currency or "USD"):
        return Classification("held", "currency_mismatch", "The invoice currency does not match the order.")

    if invoice.invalid:
        return Classification("invalid", "invalid", "BTCPay marked this invoice invalid.")

    manual_hold = additional_status_hold(invoice)
    if manual_hold is not None:
        return manual_hold

    if invoice.settled or invoice.processing:
        if invoice.unverifiable_payment_count:
            return Classification("held", "payment_method_unverifiable",
                                  "A payment could not be confirmed as using the payment method chosen at "
                                  "checkout.")
        if invoice.matching_payment_count == 0:
            return Classification("held", "not_onchain",
                                  "The invoice was not paid with the on-chain payment method chosen at "
                                  "checkout, so it is held for review.")
        if invoice.other_method_payment_count:
            return Classification("held", "not_onchain",
                                  "Part of the invoice was paid with a different payment method than the "
                                  "one chosen at checkout, so it is held for review.")

    if invoice.amount_cents is None:
        return Classification("held", "amount_unreadable", "The invoice amount could not be read.")
    if invoice.amount_cents != target.amount_cents:
        return Classification("held", "amount_mismatch",
                              "The invoice amount does not match the price that was quoted.")

    if invoice.settled:
        if invoice.settled_units <= 0:
            return Classification("held", "no_settled_onchain_amount",
                                  "The settled on-chain amount could not be confirmed.")
        if not invoice.confirmed:
            return Classification("held", "unconfirmed_onchain_payment",
                                  "The on-chain payment has no confirmation yet.")
        # Compare against BTCPay's own quoted amount when it is exposed. A
        # partial or excess payment is held for review, never auto-unlocked.
        if invoice.due_units:
            if invoice.paid_units < invoice.due_units:
                return Classification("held", "underpaid",
                                      "The invoice was underpaid; it is held for review.")
            if invoice.paid_units > invoice.due_units:
                return Classification("held", "overpaid",
                                      "The invoice was overpaid; it is held for review.")
        if settlement_after_expiry(invoice, target):
            return Classification("held", "late_settlement",
                                  "The payment arrived after the checkout window closed.")
        return Classification("settled", "verified",
                              "Settled on-chain payment verified with BTCPay Server.",
                              units=invoice.settled_units, rate=invoice.rate)

    if invoice.processing:
        return Classification("processing", "processing",
                              "BTCPay sees a payment but it is not settled yet.", rate=invoice.rate)
    if invoice.expired:
        return Classification("expired", "expired", "The checkout window closed without payment.")
    return Classification("pending", "pending", "No payment has been seen on this invoice yet.",
                          rate=invoice.rate)


def parse_webhook_signature(header_value: str | None) -> str | None:
    """Extract the hex digest from a ``BTCPay-Sig`` header value."""
    if not header_value:
        return None
    text = header_value.strip()
    if text.lower().startswith("sha256="):
        text = text.split("=", 1)[1]
    text = text.strip()
    if len(text) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        return None
    return text.lower()
