-- 0010_privacy_retention.sql
-- Privacy lifecycle bookkeeping for account archival, anonymisation and the
-- retention boundary between personal data and financial history.

CREATE TABLE account_actions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  action        TEXT    NOT NULL
                        CHECK (action IN ('archived','unarchived','anonymized','suspended','restored',
                                          'orientation_hidden','orientation_shared','data_export_requested')),
  reason        TEXT,
  actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  actor_kind    TEXT    NOT NULL DEFAULT 'self' CHECK (actor_kind IN ('self','admin','system')),
  retained_note TEXT,
  created_at    TEXT    NOT NULL
);

CREATE INDEX idx_account_actions_user ON account_actions(user_id, created_at);

-- Privacy preference audit for the optional, sensitive orientation field so a
-- user can see exactly when a value became publicly visible and when it stopped.
CREATE TABLE orientation_disclosures (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  value_kind   TEXT    NOT NULL CHECK (value_kind IN ('preset','self_described','prefer_not_to_say')),
  visibility   TEXT    NOT NULL CHECK (visibility IN ('private','public')),
  actor_kind   TEXT    NOT NULL DEFAULT 'self' CHECK (actor_kind IN ('self','system')),
  created_at   TEXT    NOT NULL
);

CREATE INDEX idx_orientation_disclosures_user ON orientation_disclosures(user_id, created_at);
