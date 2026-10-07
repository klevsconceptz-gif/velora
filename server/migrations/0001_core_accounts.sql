-- 0001_core_accounts.sql
-- Accounts, sessions and one-time email tokens.
-- Data minimisation: no phone number, no billing address, no government ID,
-- no location data. Sexual orientation is optional and private by default.

CREATE TABLE users (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  email                  TEXT    NOT NULL COLLATE NOCASE UNIQUE,
  password_hash          TEXT    NOT NULL,
  display_name           TEXT    NOT NULL,
  role                   TEXT    NOT NULL DEFAULT 'member'
                                 CHECK (role IN ('member','creator','admin')),
  email_verified         INTEGER NOT NULL DEFAULT 0 CHECK (email_verified IN (0,1)),
  email_verified_at      TEXT,
  -- 18+ self-attestation. This records that the user ticked the box; it is NOT
  -- identity or age verification and must never be described as verified age.
  adult_attested_at      TEXT    NOT NULL,
  -- Optional, private by default. Never used for discovery filtering.
  orientation_value      TEXT,
  orientation_self_text  TEXT,
  orientation_visibility TEXT    NOT NULL DEFAULT 'private'
                                 CHECK (orientation_visibility IN ('private','public')),
  bio                    TEXT,
  status                 TEXT    NOT NULL DEFAULT 'active'
                                 CHECK (status IN ('active','suspended','archived','anonymized')),
  created_at             TEXT    NOT NULL,
  updated_at             TEXT    NOT NULL,
  archived_at            TEXT,
  anonymized_at          TEXT
);

CREATE INDEX idx_users_role_status ON users(role, status);
CREATE INDEX idx_users_email ON users(email);

CREATE TABLE sessions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash         TEXT    NOT NULL UNIQUE,
  user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf_token         TEXT    NOT NULL,
  -- Salted hash of network metadata, used only to let a user recognise their own
  -- sessions. Never stored raw and never exposed to anyone else.
  client_fingerprint TEXT,
  user_agent         TEXT,
  created_at         TEXT    NOT NULL,
  last_seen_at       TEXT    NOT NULL,
  expires_at         TEXT    NOT NULL,
  revoked_at         TEXT
);

CREATE INDEX idx_sessions_user ON sessions(user_id, expires_at);
CREATE INDEX idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE email_tokens (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
  purpose     TEXT    NOT NULL CHECK (purpose IN ('verify_email','invitation','password_reset')),
  email       TEXT    COLLATE NOCASE,
  token_hash  TEXT    NOT NULL UNIQUE,
  payload     TEXT,
  created_at  TEXT    NOT NULL,
  expires_at  TEXT    NOT NULL,
  used_at     TEXT,
  created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_email_tokens_lookup ON email_tokens(purpose, token_hash);
CREATE INDEX idx_email_tokens_user ON email_tokens(user_id, purpose);
