-- Batch 19 auth upgrade: add username/password login support for admin accounts.
ALTER TABLE fazle_admins
    ADD COLUMN IF NOT EXISTS username TEXT,
    ADD COLUMN IF NOT EXISTS password_hash TEXT,
    ADD COLUMN IF NOT EXISTS login_token_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_fazle_admins_username
    ON fazle_admins (LOWER(username));

CREATE INDEX IF NOT EXISTS idx_fazle_admins_login_token_hash
    ON fazle_admins (login_token_hash);
