-- 0012_multi_asset_payments.sql
-- Creators record one receiving address per coin/token they accept, and a
-- payment attempt remembers which asset the member chose. Prices stay in
-- integer USD cents; the quoted crypto amount is kept as an exact decimal
-- count of the asset's smallest unit (TEXT, because 18-decimal tokens overflow
-- a 64-bit INTEGER).

CREATE TABLE creator_wallets (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  creator_id   INTEGER NOT NULL REFERENCES creator_pages(id) ON DELETE CASCADE,
  asset        TEXT    NOT NULL,
  address      TEXT    NOT NULL,
  address_kind TEXT    NOT NULL DEFAULT 'unknown',
  saved_at     TEXT    NOT NULL,
  saved_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  UNIQUE (creator_id, asset)
);

CREATE INDEX idx_creator_wallets_creator ON creator_wallets(creator_id);

-- Earlier releases kept a single BTC address per creator. Carry it over.
INSERT INTO creator_wallets (creator_id, asset, address, address_kind, saved_at, saved_by)
SELECT creator_id, 'btc', btc_address, address_kind, saved_at, saved_by FROM creator_payouts;

DROP TABLE creator_payouts;

ALTER TABLE payment_intents ADD COLUMN asset TEXT NOT NULL DEFAULT 'btc';
ALTER TABLE payment_intents ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'BTC-CHAIN';
ALTER TABLE payment_intents ADD COLUMN asset_amount_atomic TEXT;

UPDATE payment_intents SET asset_amount_atomic = CAST(btc_invoice_sats AS TEXT)
WHERE btc_invoice_sats IS NOT NULL;

-- Settled invoices are append-only (triggers from 0011), so existing rows keep
-- their BTC columns and read back through the defaults below.
ALTER TABLE invoices ADD COLUMN asset TEXT NOT NULL DEFAULT 'btc';
ALTER TABLE invoices ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'BTC-CHAIN';
ALTER TABLE invoices ADD COLUMN asset_amount_atomic TEXT;
ALTER TABLE invoices ADD COLUMN payout_asset TEXT;

-- Wallet addresses entered while applying. Validated when submitted and copied
-- into creator_wallets if the application is approved.
ALTER TABLE creator_applications ADD COLUMN wallets_json TEXT;

-- A settled order keeps the asset it was paid in.
CREATE TRIGGER payment_intents_freeze_settled_asset BEFORE UPDATE ON payment_intents
WHEN OLD.status = 'settled' AND (
     NEW.asset <> OLD.asset
  OR NEW.payment_method <> OLD.payment_method
  OR NEW.asset_amount_atomic IS NOT OLD.asset_amount_atomic
) BEGIN
  SELECT RAISE(ABORT, 'settled payment intent is immutable');
END;
