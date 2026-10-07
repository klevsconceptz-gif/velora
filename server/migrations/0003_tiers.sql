-- 0003_tiers.sql
-- Paid membership tiers. Prices are stored as integer USD cents; BTCPay quotes BTC.

CREATE TABLE tiers (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  creator_id   INTEGER NOT NULL REFERENCES creator_pages(id) ON DELETE CASCADE,
  name         TEXT    NOT NULL,
  description  TEXT,
  price_cents  INTEGER NOT NULL CHECK (price_cents > 0),
  position     INTEGER NOT NULL DEFAULT 0,
  is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at   TEXT    NOT NULL,
  updated_at   TEXT    NOT NULL,
  archived_at  TEXT
);

CREATE INDEX idx_tiers_creator ON tiers(creator_id, is_active, position);
