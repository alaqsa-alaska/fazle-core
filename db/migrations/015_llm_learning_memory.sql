-- Migration 015: LLM Learning Memory
-- Stores every GitHub Models (and Ollama) generated reply so Ollama can
-- learn from high-quality examples over time.

CREATE TABLE IF NOT EXISTS llm_learning_memory (
    id              BIGSERIAL PRIMARY KEY,
    provider        TEXT        NOT NULL,           -- 'github_models' | 'ollama'
    model           TEXT        NOT NULL,           -- 'openai/gpt-4o-mini' | 'qwen2.5:3b'
    trigger_text    TEXT        NOT NULL,           -- inbound message that triggered the reply
    intent          TEXT,                           -- classified intent
    role            TEXT,                           -- contact role at time of reply
    source          TEXT,                           -- bridge1 | bridge2 | meta
    context_used    TEXT,                           -- KB/RAG/DB context provided to model
    reply_text      TEXT        NOT NULL,           -- generated reply
    is_fallback     BOOLEAN     DEFAULT false,      -- true if this was a fallback response
    draft_id        BIGINT,                         -- FK to fazle_draft_replies.id (if saved as draft)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_llm_memory_provider
    ON llm_learning_memory (provider, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_llm_memory_intent
    ON llm_learning_memory (intent, role);

CREATE INDEX IF NOT EXISTS idx_llm_memory_draft
    ON llm_learning_memory (draft_id)
    WHERE draft_id IS NOT NULL;
