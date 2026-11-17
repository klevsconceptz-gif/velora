-- 0004_posts_media.sql
-- Posts, tier gating and image attachments.
-- A members-only post stores a short public teaser separately from the gated body
-- so locked posts never leak private content to an unauthorised client.

CREATE TABLE posts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  creator_id    INTEGER NOT NULL REFERENCES creator_pages(id) ON DELETE CASCADE,
  title         TEXT    NOT NULL,
  teaser        TEXT    NOT NULL DEFAULT '',
  body          TEXT    NOT NULL DEFAULT '',
  visibility    TEXT    NOT NULL DEFAULT 'members'
                        CHECK (visibility IN ('public','members')),
  status        TEXT    NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','published','archived')),
  published_at  TEXT,
  created_at    TEXT    NOT NULL,
  updated_at    TEXT    NOT NULL,
  archived_at   TEXT,
  admin_archived INTEGER NOT NULL DEFAULT 0 CHECK (admin_archived IN (0,1)),
  admin_archive_note TEXT
);

CREATE INDEX idx_posts_creator_status ON posts(creator_id, status, published_at);
CREATE INDEX idx_posts_visibility ON posts(visibility, status);

-- Which tiers unlock a post. An empty set means "any active tier of this creator".
CREATE TABLE post_tiers (
  post_id  INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  tier_id  INTEGER NOT NULL REFERENCES tiers(id) ON DELETE CASCADE,
  PRIMARY KEY (post_id, tier_id)
);

CREATE TABLE post_media (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id       INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
  storage_key   TEXT    NOT NULL UNIQUE,
  original_name TEXT,
  content_type  TEXT    NOT NULL,
  byte_size     INTEGER NOT NULL,
  sha256        TEXT    NOT NULL,
  position      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL
);

CREATE INDEX idx_post_media_post ON post_media(post_id, position);
