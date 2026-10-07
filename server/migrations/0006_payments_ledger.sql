-- 0006_payments_ledger.sql
-- BTC-only, on-chain, hosted BTCPay Server checkout.
-- A browser redirect is never proof of payment: access is granted only after a
-- signed webhook plus an independent BTCPay verification of a matching, settled
-- BTC invoice for our opaque order reference.

CREATE TABLE payment_intents (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  order_ref         TEXT    NOT NULL UNIQUE,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  creator_id        INTEGER NOT NULL REFERENCES creator_pages(id) ON DELETE CASCADE,
  tier_id           INTEGER NOT NULL REFERENCES tiers(id) ON DELETE CASCADE,
  amount_cents      INTEGER NOT NULL CHECK (amount_cents > 0),
  currency          TEXT    NOT NULL DEFAULT 'USD',
  period_days       INTEGER NOT NULL DEFAULT 30,
  status            TEXT    NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','processing','settled','held','expired','cancelled','archived')),
  hold_reason       TEXT,
  btcpay_invoice_id TEXT,
  btc_invoice_sats  INTEGER,
  btc_rate_usd      TEXT,
  checkout_url      TEXT,
  last_error        TEXT,
  created_at        TEXT    NOT NULL,
  updated_at        TEXT    NOT NULL,
  expires_at        TEXT    NOT NULL,
  settled_at        TEXT,
  archived_at       TEXT
);

CREATE INDEX idx_payment_intents_user ON payment_intents(user_id, created_at);
CREATE INDEX idx_payment_intents_creator ON payment_intents(creator_id, created_at);
CREATE INDEX idx_payment_intents_status ON payment_intents(status, created_at);
CREATE INDEX idx_payment_intents_invoice ON payment_intents(btcpay_invoice_id);

-- Settled invoice records. Written once, never mutated, never deleted.
CREATE TABLE invoices (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  payment_intent_id        INTEGER NOT NULL REFERENCES payment_intents(id) ON DELETE RESTRICT,
  order_ref                TEXT    NOT NULL UNIQUE,
  btcpay_invoice_id        TEXT    NOT NULL UNIQUE,
  user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  creator_id               INTEGER NOT NULL REFERENCES creator_pages(id) ON DELETE RESTRICT,
  tier_id                  INTEGER NOT NULL REFERENCES tiers(id) ON DELETE RESTRICT,
  amount_cents             INTEGER NOT NULL,
  platform_fee_cents       INTEGER NOT NULL,
  creator_net_cents        INTEGER NOT NULL,
  fee_percent              INTEGER NOT NULL,
  btc_amount_sats          INTEGER NOT NULL,
  btc_rate_usd             TEXT,
  btc_destination          TEXT,
  payout_address_snapshot  TEXT,
  status                   TEXT    NOT NULL DEFAULT 'settled' CHECK (status IN ('settled')),
  settled_at               TEXT    NOT NULL,
  recorded_at              TEXT    NOT NULL,
  verify_source            TEXT    NOT NULL DEFAULT 'btcpay_api',
  invoice_digest           TEXT,
  CHECK (amount_cents = platform_fee_cents + creator_net_cents)
);

CREATE INDEX idx_invoices_creator ON invoices(creator_id, settled_at);
CREATE INDEX idx_invoices_user ON invoices(user_id, settled_at);

-- Append-only double-entry style ledger. Amounts are integer USD cents.
CREATE TABLE ledger_entries (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  invoice_id   INTEGER REFERENCES invoices(id) ON DELETE RESTRICT,
  order_ref    TEXT,
  entry_type   TEXT    NOT NULL
                       CHECK (entry_type IN ('member_payment','platform_fee','creator_earning','adjustment_note')),
  account      TEXT    NOT NULL,
  direction    TEXT    NOT NULL CHECK (direction IN ('debit','credit')),
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  memo         TEXT,
  actor_kind   TEXT    NOT NULL DEFAULT 'system' CHECK (actor_kind IN ('system','admin')),
  actor_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  created_at   TEXT    NOT NULL
);

CREATE INDEX idx_ledger_invoice ON ledger_entries(invoice_id);
CREATE INDEX idx_ledger_account ON ledger_entries(account, created_at);

-- Webhook deliveries are recorded for idempotency and audit. Replays of the same
-- delivery id are ignored; the same invoice can never be granted twice.
CREATE TABLE webhook_events (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  provider          TEXT    NOT NULL DEFAULT 'btcpay',
  delivery_id       TEXT    NOT NULL,
  event_type        TEXT,
  invoice_id        TEXT,
  order_ref         TEXT,
  signature_valid   INTEGER NOT NULL DEFAULT 0 CHECK (signature_valid IN (0,1)),
  outcome           TEXT    NOT NULL,
  detail            TEXT,
  payload_digest    TEXT,
  received_at       TEXT    NOT NULL,
  UNIQUE (provider, delivery_id)
);

CREATE INDEX idx_webhook_events_invoice ON webhook_events(invoice_id);
