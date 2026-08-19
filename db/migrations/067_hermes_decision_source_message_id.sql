-- Migration 067: hermes_decision_audit_log.source_message_id (2026-08-19)
--
-- HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md
-- (Task 1) found that process_message() has always received the bridge's
-- real message_id (modules/bridge_poller passes it through since it was
-- added for migration 052's dedup identity), but the Hermes Kernel's
-- shadow DecisionRecord/KernelEvent (migration 060) never captured it --
-- making "which message caused this decision" a phone+timestamp guess
-- instead of a deterministic lookup.
--
-- Purely additive: nullable column, no backfill (historical rows have no
-- message_id available -- this column is populated only going forward),
-- no index (add later only if lookup-by-message-id becomes a real query
-- pattern; the existing /api/hermes/decisions/lookup route filters by
-- phone/trace_id/chosen_action, not message_id). hermes_decision_audit_log
-- remains append-only per migration 060's own stated principle and is not
-- read by any production query outside the two /api/hermes/* lookup
-- routes.

ALTER TABLE hermes_decision_audit_log
    ADD COLUMN IF NOT EXISTS source_message_id VARCHAR(100);
