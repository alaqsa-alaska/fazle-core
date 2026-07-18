-- Migration 051: Dispatch Phase 2A.3 Stage 2 -- widen
-- escort_roster_change_requests.action CHECK constraint
--
-- Discovered while implementing Stage 2 (not anticipated in the Decision
-- Report): escort_roster_change_requests has its OWN database-level CHECK
-- constraint on `action` (ARRAY['edit','delete']), separate from and in
-- addition to the application-level whitelist in
-- modules.escort_roster.db.create_roster_change_request(). Widening only
-- the Python whitelist was insufficient -- the INSERT itself is rejected
-- at the database level.
--
-- Adds exactly one new value needed for Stage 2's same-date correction
-- guard: 'manual_review_same_date_escort_change', used when a same-date,
-- same-vessel-pair admin message names a different escort than a program
-- that already has running/completed (attendance-gated) evidence, or when
-- the vessel match itself is low-confidence (empty lighter_vessel on
-- either side) -- Phase 2A.3 Decisions 3 and 4. The request sits
-- 'pending' for admin review; decide_roster_change_request()'s approval
-- dispatch does not yet implement this action type (that is deferred to
-- the Stage 6 review UI) -- widening this constraint only unblocks
-- *creating* the review record, not auto-approving it.
--
-- Purely additive: no default change, no existing row rewritten.

BEGIN;

ALTER TABLE escort_roster_change_requests
    DROP CONSTRAINT escort_roster_change_requests_action_check;

ALTER TABLE escort_roster_change_requests
    ADD CONSTRAINT escort_roster_change_requests_action_check
    CHECK (action IN ('edit', 'delete', 'manual_review_same_date_escort_change'));

COMMIT;

-- Downgrade (manual, run only after confirming zero rows use the new
-- action value):
--   BEGIN;
--   ALTER TABLE escort_roster_change_requests DROP CONSTRAINT escort_roster_change_requests_action_check;
--   ALTER TABLE escort_roster_change_requests ADD CONSTRAINT escort_roster_change_requests_action_check
--       CHECK (action IN ('edit', 'delete'));
--   COMMIT;
