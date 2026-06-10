-- Migration 017: Memory promotion tracking
-- Adds columns to llm_learning_memory so reviewed replies can be promoted to KB.

ALTER TABLE llm_learning_memory
    ADD COLUMN IF NOT EXISTS promoted_to_kb  BOOLEAN     DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS promoted_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dismissed       BOOLEAN     DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_llm_memory_pending
    ON llm_learning_memory (created_at DESC)
    WHERE promoted_to_kb = FALSE AND dismissed = FALSE;
