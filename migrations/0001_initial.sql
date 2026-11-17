PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  username_key TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  email_key TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
  password_salt TEXT,
  password_hash TEXT,
  password_iterations INTEGER NOT NULL DEFAULT 120000,
  wallet_available REAL NOT NULL DEFAULT 0,
  wallet_invested REAL NOT NULL DEFAULT 0,
  wallet_earnings REAL NOT NULL DEFAULT 0,
  wallet_deposits REAL NOT NULL DEFAULT 0,
  wallet_withdrawals REAL NOT NULL DEFAULT 0,
  wallet_transfers_in REAL NOT NULL DEFAULT 0,
  wallet_transfers_out REAL NOT NULL DEFAULT 0,
  wallet_reserved REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_state (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  data_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK(role IN ('user','admin')),
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS payment_methods (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  detail TEXT NOT NULL,
  processing_time TEXT NOT NULL DEFAULT '',
  fee TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  audience TEXT NOT NULL DEFAULT 'all',
  level TEXT NOT NULL DEFAULT 'Update',
  author TEXT NOT NULL DEFAULT 'Admin',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  amount REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'Pending',
  method TEXT NOT NULL DEFAULT '',
  reference TEXT NOT NULL DEFAULT '',
  holder TEXT NOT NULL DEFAULT '',
  details TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  review_note TEXT
);
CREATE INDEX IF NOT EXISTS requests_status_created_idx ON requests(status, created_at);

CREATE TABLE IF NOT EXISTS transfers (
  id TEXT PRIMARY KEY,
  sender_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  recipient_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  amount REAL NOT NULL CHECK(amount > 0),
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sender TEXT NOT NULL CHECK(sender IN ('user','admin')),
  body TEXT NOT NULL,
  read_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_user_created_idx ON messages(user_id, created_at);

CREATE TABLE IF NOT EXISTS ledger (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_user_created_idx ON ledger(user_id, created_at);

INSERT OR IGNORE INTO payment_methods(id,name,detail,processing_time,fee,created_at)
VALUES
  ('method-ach','ACH transfer','Account details are shared after you create a deposit request.','1–3 business days','No platform fee',datetime('now')),
  ('method-wire','Wire transfer','Request wire instructions from the payments desk.','Same business day','Bank fees may apply',datetime('now')),
  ('method-card','Debit card','Card deposits are reviewed before your wallet is updated.','Usually within 1 business day','Processing may apply',datetime('now'));

INSERT OR IGNORE INTO announcements(id,title,body,audience,level,author,created_at)
VALUES ('welcome-note','A clearer way to build toward what matters','Welcome to Goal system. Set a goal, choose a pace that feels sustainable and check in whenever you need a fresh perspective.','all','Update','Admin',datetime('now'));
