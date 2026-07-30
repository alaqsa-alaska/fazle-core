-- Migration 059: Admin directive/action memory.
--
-- Owner-authorized (Unified Admin AI Engine, Phase 2 plan, approved
-- 2026-07-30): a narrow, explicit memory of admin-authored instructions
-- ("remember this", "note about this number"), separate from
-- modules.ollama_memory (which is documented as the AI's own isolated
-- self-memory -- ai_memory_facts/ai_memory_questions/ai_memory_tasks --
-- and must not be repurposed for admin-authored content).
--
-- Written/read by modules.admin_directives (both the WhatsApp REMEMBER/
-- FORGET commands and the web /admin/directives endpoints call the same
-- two functions -- one write path, two entry points, matching the
-- existing modules.attendance.finalize_attendance_draft() pattern).
--
-- category/status/created_by_channel use DB-level CHECK constraints,
-- following CANONICAL_BUSINESS_RULES.md's Approval section recommendation
-- that escort_roster_change_requests' CHECK-constraint pattern (migration
-- 026) is "the model to follow for any new approval-style table" -- every
-- other approval/status column in this system lacks one and has drifted.
--
-- Purely additive: no ALTER on any existing table, no FK into any Critical
-- (cash-ledger/employee-identity) table -- subject_key is an informational
-- free-text reference (e.g. a phone number), not a foreign key, so this
-- migration can never fail due to another table's state.

CREATE TABLE IF NOT EXISTS fazle_admin_directives (
    id                  SERIAL      PRIMARY KEY,
    directive_text      TEXT        NOT NULL,
    category            TEXT        NOT NULL DEFAULT 'general'
                         CHECK (category IN ('general', 'autoreply_policy', 'contact_note', 'escalation')),
    subject_type        TEXT,                         -- e.g. 'phone', 'employee'; NULL = global directive
    subject_key         TEXT,                         -- e.g. the phone number this note is about; NULL = global
    created_by_phone    TEXT        NOT NULL,
    created_by_channel  TEXT        NOT NULL
                         CHECK (created_by_channel IN ('whatsapp', 'web')),
    status              TEXT        NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'revoked')),
    source_ref          TEXT,                         -- free text, e.g. related draft id
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at          TIMESTAMPTZ,
    revoked_by_phone    TEXT
);

CREATE INDEX IF NOT EXISTS idx_admin_directives_subject
    ON fazle_admin_directives (subject_type, subject_key) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_admin_directives_status
    ON fazle_admin_directives (status);

CREATE INDEX IF NOT EXISTS idx_admin_directives_created_by
    ON fazle_admin_directives (created_by_phone);
