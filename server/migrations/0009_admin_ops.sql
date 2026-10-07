-- 0009_admin_ops.sql
-- Administration: one-time invitations, audit trail, internal notes, rate limits.
-- There is no seeded admin, no default password and no public admin self-signup.

CREATE TABLE admin_invitations (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  email       TEXT    COLLATE NOCASE,
  role        TEXT    NOT NULL DEFAULT 'admin' CHECK (role IN ('admin','creator')),
  token_hash  TEXT    NOT NULL UNIQUE,
  invited_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  note        TEXT,
  created_at  TEXT    NOT NULL,
  expires_at  TEXT    NOT NULL,
  used_at     TEXT,
  used_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
  revoked_at  TEXT
);

CREATE INDEX idx_admin_invitations_email ON admin_invitations(email, used_at);

CREATE TABLE audit_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  actor_role    TEXT,
  action        TEXT    NOT NULL,
  target_type   TEXT,
  target_id     INTEGER,
  meta          TEXT,
  client_hash   TEXT,
  created_at    TEXT    NOT NULL
);

CREATE INDEX idx_audit_actor ON audit_log(actor_user_id, created_at);
CREATE INDEX idx_audit_action ON audit_log(action, created_at);

CREATE TABLE admin_notes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  target_type   TEXT    NOT NULL,
  target_id     INTEGER NOT NULL,
  body          TEXT    NOT NULL,
  created_at    TEXT    NOT NULL
);

CREATE INDEX idx_admin_notes_target ON admin_notes(target_type, target_id, created_at);

CREATE TABLE rate_limits (
  bucket        TEXT    NOT NULL,
  window_start  INTEGER NOT NULL,
  hit_count     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (bucket, window_start)
);
