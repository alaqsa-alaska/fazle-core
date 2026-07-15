-- Migration 048: chat_history (read-only shadow archive for external chat_sync service)
--
-- Purely additive. Not written to by any app code in this repo -- it exists
-- only as the write target for a standalone, out-of-repo service
-- (/opt/chat_sync/sync.py, systemd unit chat-sync.service) that reads
-- wbom_whatsapp_messages (inbound) and fazle_outbound_queue (outbound) on a
-- poll loop and copies rows here for a unified per-thread conversation view.
-- See docs/TASK_BRIEF_chat_sync_and_reply_gap_2026-07-15.md Phase 1.
--
-- Outbound is sourced from fazle_outbound_queue only, never from
-- wbom_whatsapp_messages -- the customer-reply auto-send path writes the
-- same message to both tables, and there is no shared key across them to
-- de-dupe against; admin notifications write only to fazle_outbound_queue.
-- Pulling outbound from both would double-count.
--
-- 'outbound_pending' is reserved for a possible future third source
-- (fazle_draft_replies -- replies generated but never sent) that is not
-- wired up as of this migration.

CREATE TABLE IF NOT EXISTS chat_history (
    id              BIGSERIAL PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound', 'outbound_pending')),
    sender_phone    TEXT,
    receiver_phone  TEXT,
    message_body    TEXT,
    message_type    TEXT,
    bridge          TEXT,
    purpose         TEXT,
    status          TEXT,
    source_table    TEXT NOT NULL,
    source_id       BIGINT NOT NULL,
    sent_at         TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source_table, source_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_history_thread_sent ON chat_history (thread_id, sent_at);
