-- Migration 016: User Profiles, Memory, Role Reply Styles, Phone Normalization Log
-- Enables per-contact role context, structured memory, and phone canonicalization.

CREATE TABLE IF NOT EXISTS user_profiles (
    id                  BIGSERIAL PRIMARY KEY,
    phone_canonical     TEXT        UNIQUE NOT NULL,
    phone_raw           TEXT,
    name                TEXT,
    role                TEXT        DEFAULT 'unknown',
    -- buyer, seller, client, vip_client, employee, escort,
    -- security_guard, supervisor, manager, accountant,
    -- vendor, family, friend, candidate, unknown
    relationship_type   TEXT,
    -- regular_client, former_employee, salary_pending,
    -- job_applicant, vendor, family_member
    notes               TEXT,
    is_active           BOOLEAN     DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_profiles_role
    ON user_profiles (role)
    WHERE is_active = TRUE;


CREATE TABLE IF NOT EXISTS user_memory (
    id                  BIGSERIAL PRIMARY KEY,
    phone_canonical     TEXT        NOT NULL REFERENCES user_profiles (phone_canonical) ON DELETE CASCADE,
    memory_type         TEXT        NOT NULL,
    -- personal_info, work_info, conversation_fact,
    -- pending_matter, important_event
    content             TEXT        NOT NULL,
    confidence          REAL        DEFAULT 1.0,
    source              TEXT,       -- 'manual', 'github_models_extracted', 'admin'
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_memory_phone
    ON user_memory (phone_canonical, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_memory_type
    ON user_memory (memory_type);


CREATE TABLE IF NOT EXISTS role_reply_styles (
    id                      BIGSERIAL PRIMARY KEY,
    role                    TEXT        UNIQUE NOT NULL,
    tone                    TEXT,       -- formal, informal, professional, friendly
    language                TEXT        DEFAULT 'bengali',
    max_length              INTEGER     DEFAULT 200,
    greeting_template       TEXT,
    system_prompt_addition  TEXT,
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS phone_normalization_log (
    id                  BIGSERIAL PRIMARY KEY,
    raw_phone           TEXT,
    canonical_phone     TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_phone_norm_canonical
    ON phone_normalization_log (canonical_phone);
