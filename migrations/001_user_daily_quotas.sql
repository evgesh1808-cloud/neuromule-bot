-- Daily per-user and global Nano Banana FREE quotas (lazy reset by quota_date).
-- Quota key for FREE photo: free_banana_daily
-- Apply: sqlite3 data/bot.db < migrations/001_user_daily_quotas.sql
-- Or automatically via init_db / init_billing_schema.

CREATE TABLE IF NOT EXISTS user_daily_quotas (
    user_id     INTEGER NOT NULL,
    quota_key   TEXT    NOT NULL,
    quota_date  TEXT    NOT NULL,
    used_count  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, quota_key, quota_date)
);

CREATE INDEX IF NOT EXISTS idx_udq_user_key_date
    ON user_daily_quotas (user_id, quota_key, quota_date DESC);
