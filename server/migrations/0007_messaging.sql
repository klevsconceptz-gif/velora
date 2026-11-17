-- 0007_messaging.sql
-- Direct messages, subject to the project's membership/contact rules:
-- a member may open a thread with a creator they currently support, and creators
-- may reply inside an existing thread. Bodies are plain text, never HTML.

CREATE TABLE threads (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  member_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  creator_id      INTEGER NOT NULL REFERENCES creator_pages(id) ON DELETE CASCADE,
  created_at      TEXT    NOT NULL,
  last_message_at TEXT    NOT NULL,
  archived_at     TEXT,
  UNIQUE (member_id, creator_id)
);

CREATE INDEX idx_threads_member ON threads(member_id, last_message_at);
CREATE INDEX idx_threads_creator ON threads(creator_id, last_message_at);

CREATE TABLE messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id   INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
  sender_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body        TEXT    NOT NULL,
  created_at  TEXT    NOT NULL,
  read_at     TEXT,
  removed_at  TEXT,
  removed_by  INTEGER REFERENCES users(id) ON DELETE SET NULL
);

CREATE INDEX idx_messages_thread ON messages(thread_id, created_at);
