-- Migration 078: Phase-2 -- Inbound WhatsApp Identity & Canonical Dedup
--
-- Owner-authoritative identity principle (this migration's reason for existing):
--   "For WhatsApp identity, the normalized WhatsApp mobile number is the
--    primary identity key. Names are supporting/display evidence only."
--
-- This migration is purely additive on wbom_whatsapp_messages:
--   * 6 nullable columns  (sender_name_snapshot / _source / _confidence,
--                          canonical_message_key, resolved_phone, resolved_at)
--   * 1 CHECK constraint on sender_name_source (NULL-tolerant, doesn't
--     retroactively validate existing rows)
--   * 2 partial indexes for the two real query paths
--     (canonical dedup on bridge platforms, Meta source_message_ref lookup)
--
-- Does NOT change semantics of any existing column, FK, trigger, UNIQUE, or
-- default. Live traffic continues unaffected because every new column is NULL
-- until the new persist_inbound_central() helper writes to it.
--
-- Idempotent: every statement uses IF NOT EXISTS / DO blocks so a re-apply
-- against an already-migrated DB is a no-op.

BEGIN;

-- 1. New columns -----------------------------------------------------------------
ALTER TABLE wbom_whatsapp_messages
    ADD COLUMN IF NOT EXISTS sender_name_snapshot   text,
    ADD COLUMN IF NOT EXISTS sender_name_source     varchar(40),
    ADD COLUMN IF NOT EXISTS sender_name_confidence smallint,
    ADD COLUMN IF NOT EXISTS canonical_message_key  text,
    ADD COLUMN IF NOT EXISTS resolved_phone         text,
    ADD COLUMN IF NOT EXISTS resolved_at            timestamptz;

-- 2. CHECK on sender_name_source (NULL-tolerant) ---------------------------------
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_wbom_messages_sender_name_source'
    ) THEN
        ALTER TABLE wbom_whatsapp_messages
            ADD CONSTRAINT ck_wbom_messages_sender_name_source CHECK (
                sender_name_source IS NULL OR sender_name_source IN (
                    'employee',
                    'wbom_contacts',
                    'identity_brain',
                    'whatsmeow_full_name',
                    'whatsmeow_push_name',
                    'whatsmeow_business_name',
                    'meta_profile',
                    'social_inbox',
                    'unknown'
                )
            );
    END IF;
END $$;

-- 3. Partial index: canonical_message_key (cross-bridge grouping) -----------------
-- Only bridge-platform rows have a canonical_message_key; partial keeps the
-- index small. Used by:
--   (a) action-layer cross-bridge dedup (process_bridge_inbound pre-check)
--   (b) forensic "show all bridges that observed this physical message"
CREATE INDEX IF NOT EXISTS idx_wbom_msg_canonical_key
    ON wbom_whatsapp_messages (canonical_message_key, platform, received_at DESC)
    WHERE canonical_message_key IS NOT NULL
      AND platform IN ('bridge1', 'bridge2', 'bridge3');

-- 4. Partial index: Meta source_message_ref --------------------------------------
-- Used by app.main._handle_meta_message()'s existing dedup SELECT (speeds any
-- future Meta correlation queries).
CREATE INDEX IF NOT EXISTS idx_wbom_msg_meta_source_ref
    ON wbom_whatsapp_messages (source_message_ref)
    WHERE platform = 'meta' AND direction = 'inbound';

COMMIT;