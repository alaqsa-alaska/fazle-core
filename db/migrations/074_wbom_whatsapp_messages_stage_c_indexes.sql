-- 074_wbom_whatsapp_messages_stage_c_indexes.sql
-- Stage C (2026-08-28, filtered conversation retrieval): two supporting
-- indexes for the new query dimensions Stage B's actor_type and the
-- long-existing workflow_triggered add to /api/whatsapp/messages/lookup's
-- filter set. Same partial-index-on-NOT-NULL pattern this table's own
-- idx_wbom_msg_intent and idx_wbom_messages_identity already use --
-- neither column is populated on every row, so a full index would waste
-- space on the NULL majority for no query benefit.
--
-- No new columns, no data change -- purely a read-performance addition
-- for the C3 filtered-retrieval endpoint.
BEGIN;

CREATE INDEX IF NOT EXISTS idx_wbom_msg_actor_type
    ON public.wbom_whatsapp_messages USING btree (actor_type)
    WHERE actor_type IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_wbom_msg_workflow_triggered
    ON public.wbom_whatsapp_messages USING btree (workflow_triggered)
    WHERE workflow_triggered IS NOT NULL;

COMMIT;
