-- Migration 062: candidate conversation bridge message identity.
-- Nullable columns preserve all legacy rows and callers without message IDs.

ALTER TABLE IF EXISTS wbom_candidate_conversations
    ADD COLUMN IF NOT EXISTS source_bridge TEXT,
    ADD COLUMN IF NOT EXISTS message_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_conversations_bridge_message
    ON wbom_candidate_conversations (source_bridge, message_id)
    WHERE message_id IS NOT NULL;

-- Manual rollback after disabling S5 writes:
-- DROP INDEX IF EXISTS uq_candidate_conversations_bridge_message;
-- ALTER TABLE wbom_candidate_conversations
--     DROP COLUMN IF EXISTS message_id,
--     DROP COLUMN IF EXISTS source_bridge;