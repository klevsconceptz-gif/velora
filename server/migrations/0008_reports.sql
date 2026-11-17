-- 0008_reports.sql
-- Reports on content and accounts, with a clear lifecycle owned by admins.

CREATE TABLE reports (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  reporter_user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
  target_type         TEXT    NOT NULL
                              CHECK (target_type IN ('user','creator','post','message','tier')),
  target_id           INTEGER NOT NULL,
  target_label        TEXT,
  reason_code         TEXT    NOT NULL
                              CHECK (reason_code IN ('spam','harassment','impersonation','illegal_content',
                                                     'non_consensual_content','minor_safety','payment_issue',
                                                     'privacy','other')),
  details             TEXT,
  status              TEXT    NOT NULL DEFAULT 'open'
                              CHECK (status IN ('open','reviewing','resolved','dismissed')),
  resolution_note     TEXT,
  handled_by          INTEGER REFERENCES users(id) ON DELETE SET NULL,
  handled_at          TEXT,
  created_at          TEXT    NOT NULL,
  updated_at          TEXT    NOT NULL
);

CREATE INDEX idx_reports_status ON reports(status, created_at);
CREATE INDEX idx_reports_target ON reports(target_type, target_id);
