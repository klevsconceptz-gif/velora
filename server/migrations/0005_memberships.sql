-- 0005_memberships.sql
-- Memberships are 30-day periods bought individually. There is no automatic
-- renewal and no scheduled crypto charge; renewal is a manual, deliberate action.
--
-- Access must survive cancellation: cancelling only stops a future renewal, so a
-- cancelled membership keeps status 'active' until ends_at has passed. Paid-for
-- time is never taken away early.

CREATE TABLE memberships (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  creator_id          INTEGER NOT NULL REFERENCES creator_pages(id) ON DELETE CASCADE,
  tier_id             INTEGER NOT NULL REFERENCES tiers(id) ON DELETE CASCADE,
  invoice_id          INTEGER REFERENCES invoices(id) ON DELETE RESTRICT,
  status              TEXT    NOT NULL DEFAULT 'active'
                              CHECK (status IN ('active','expired','revoked')),
  started_at          TEXT    NOT NULL,
  ends_at             TEXT    NOT NULL,
  cancel_requested_at TEXT,
  created_at          TEXT    NOT NULL,
  updated_at          TEXT    NOT NULL,
  -- Velora never schedules a repeating crypto charge. The CHECK makes that a
  -- schema-level invariant instead of a promise in the documentation.
  auto_renew          INTEGER NOT NULL DEFAULT 0 CHECK (auto_renew = 0)
);

CREATE INDEX idx_memberships_user ON memberships(user_id, status, ends_at);
CREATE INDEX idx_memberships_creator ON memberships(creator_id, status, ends_at);
CREATE INDEX idx_memberships_tier ON memberships(tier_id);

-- At most one membership row per member/creator pair can ever be active.
CREATE UNIQUE INDEX idx_memberships_one_active
  ON memberships(user_id, creator_id) WHERE status = 'active';
