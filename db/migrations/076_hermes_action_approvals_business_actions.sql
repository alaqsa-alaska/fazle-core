-- Migration 076: Stage F -- hermes_action_approvals business-action fields
-- (2026-08-28)
--
-- Reuses the EXISTING propose -> approve -> execute -> verify gate
-- (hermes_action_approvals, migration 068) for Business Inbox proposed
-- actions rather than a second, parallel approval framework -- per Stage
-- F1's explicit instruction to find and reuse the authoritative existing
-- mechanism, not invent another. This table already has the full state
-- machine (status/risk/reviewed_by/executed_at/execution_result); what it
-- genuinely lacked for a BUSINESS (not code/infra) proposal is:
--
--   domain              -- which Stage E supported domain this belongs to
--                          (recruitment/escort/attendance/cash) -- a
--                          different concept from action_type (WHAT kind
--                          of action) or risk (how dangerous).
--   business_reference   -- the canonical conversation_key or business
--                          object reference (e.g. candidate phone,
--                          program_id) this proposal is about. Never a
--                          message body.
--   evidence_refs         -- small JSON array of {type, value} references
--                          into canonical/business records (message_id,
--                          candidate phone, program_id, attendance_id,
--                          txn id, ...) that justified this proposal.
--                          Deliberately NOT full message bodies -- Stage
--                          F2's explicit "do not copy huge message bodies
--                          into the action record".
--   idempotency_key        -- Stage F7: a stable identifier derived from
--                          (business object + action type + source event
--                          reference), never message text alone, so a
--                          retry/duplicate WhatsApp delivery/repeated
--                          Hermes run/service restart/duplicated
--                          cross-bridge observation can never create a
--                          second proposal for the same real-world event.
--                          Enforced by the partial unique index below
--                          (only while a proposal is still "live" --
--                          pending/approved/executed -- a rejected or
--                          expired one intentionally does NOT block a
--                          fresh proposal for the same event, matching
--                          how a human would want to re-raise something
--                          that was previously turned down).
--
-- Two new action_type values (same widen-the-CHECK-constraint pattern as
-- migrations 070/071): 'business_reply_draft' and 'business_record_review'
-- -- both Class B (draft/propose, no mutation of authoritative business
-- state) per Stage F3. Nothing here grants send/mutate authority -- the
-- actual draft TEXT still goes through the existing, separate
-- fazle_draft_replies / create_draft_reply() / approve_draft() pipeline
-- (Hermes Capability Expansion Level 1, 2026-08-10) exactly as before;
-- this table only records the proposal's own bookkeeping and evidence.
--
-- Purely additive: no default change, no existing row rewritten, no
-- existing action_type value removed, no existing column altered.

BEGIN;

ALTER TABLE hermes_action_approvals
    ADD COLUMN IF NOT EXISTS domain TEXT,
    ADD COLUMN IF NOT EXISTS business_reference TEXT,
    ADD COLUMN IF NOT EXISTS evidence_refs JSONB,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_hermes_action_approvals_idempotency_live
    ON hermes_action_approvals (idempotency_key)
    WHERE idempotency_key IS NOT NULL AND status IN ('pending', 'approved', 'executed');

CREATE INDEX IF NOT EXISTS idx_hermes_action_approvals_domain
    ON hermes_action_approvals (domain) WHERE domain IS NOT NULL;

ALTER TABLE hermes_action_approvals
    DROP CONSTRAINT hermes_action_approvals_action_type_check;

ALTER TABLE hermes_action_approvals
    ADD CONSTRAINT hermes_action_approvals_action_type_check
    CHECK (action_type IN (
        'file_mutation', 'git_commit', 'deploy', 'migration', 'restart',
        'production_write', 'opencode_dispatch',
        'dispatch_assignment', 'billing_mutation', 'escort_program_creation',
        'business_reply_draft', 'business_record_review'
    ));

COMMIT;

-- Downgrade (manual, run only after confirming zero rows use either new
-- action_type value and no row has a non-NULL domain/business_reference/
-- evidence_refs/idempotency_key):
--   BEGIN;
--   DROP INDEX IF EXISTS uq_hermes_action_approvals_idempotency_live;
--   DROP INDEX IF EXISTS idx_hermes_action_approvals_domain;
--   ALTER TABLE hermes_action_approvals DROP CONSTRAINT hermes_action_approvals_action_type_check;
--   ALTER TABLE hermes_action_approvals ADD CONSTRAINT hermes_action_approvals_action_type_check
--       CHECK (action_type IN ('file_mutation','git_commit','deploy','migration','restart','production_write','opencode_dispatch','dispatch_assignment','billing_mutation','escort_program_creation'));
--   ALTER TABLE hermes_action_approvals DROP COLUMN domain, DROP COLUMN business_reference, DROP COLUMN evidence_refs, DROP COLUMN idempotency_key;
--   COMMIT;
