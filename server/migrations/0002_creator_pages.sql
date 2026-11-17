-- 0002_creator_pages.sql
-- Public creator pages and creator applications.

CREATE TABLE creator_pages (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id        INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
  handle         TEXT    NOT NULL COLLATE NOCASE UNIQUE,
  page_name      TEXT    NOT NULL,
  tagline        TEXT,
  about          TEXT,
  category       TEXT    NOT NULL DEFAULT 'other',
  status         TEXT    NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active','paused','archived')),
  created_at     TEXT    NOT NULL,
  updated_at     TEXT    NOT NULL,
  archived_at    TEXT
);

CREATE INDEX idx_creator_pages_status_category ON creator_pages(status, category);

-- Aggregate-only page view counters. No visitor identity, no cookies, no
-- per-visitor rows: privacy-conscious analytics by design.
CREATE TABLE creator_page_views (
  creator_id  INTEGER NOT NULL REFERENCES creator_pages(id) ON DELETE CASCADE,
  day         TEXT    NOT NULL,
  view_count  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (creator_id, day)
);

CREATE TABLE creator_applications (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category     TEXT    NOT NULL,
  pitch        TEXT    NOT NULL,
  desired_handle TEXT  COLLATE NOCASE,
  status       TEXT    NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','approved','rejected','withdrawn')),
  decision_note TEXT,
  reviewed_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
  reviewed_at  TEXT,
  created_at   TEXT    NOT NULL,
  updated_at   TEXT    NOT NULL
);

CREATE INDEX idx_creator_applications_status ON creator_applications(status, created_at);
CREATE UNIQUE INDEX idx_creator_applications_open ON creator_applications(user_id)
  WHERE status = 'pending';

-- The creator payout destination. It is an on-chain BTC receiving address, not a
-- provider username. It is never returned by any public serializer.
CREATE TABLE creator_payouts (
  creator_id   INTEGER PRIMARY KEY REFERENCES creator_pages(id) ON DELETE CASCADE,
  btc_address  TEXT    NOT NULL,
  address_kind TEXT    NOT NULL DEFAULT 'unknown'
                       CHECK (address_kind IN ('p2pkh','p2sh','bech32','bech32m','unknown')),
  saved_at     TEXT    NOT NULL,
  saved_by     INTEGER REFERENCES users(id) ON DELETE SET NULL
);
