-- Migration 042: Remove blocked-number concept entirely (Owner decision, 2026-07-11)
--
-- The owner has permanently removed the "blocked number" concept from this
-- system's business logic. There is no scenario in which a phone number
-- should be blocked at the pipeline level -- a sender is either a known
-- role or unknown; unknown senders simply do not trigger business actions.
--
-- This reverses migration 041 (fpe_identity_anomalies), which existed only
-- to track conflicts between a stale `blocked` row and a configured admin
-- identity. Since `blocked` no longer exists as a role, that conflict can
-- never occur again, so the table has no further purpose.
--
-- fazle_contact_roles itself is NOT dropped -- confirmed via direct query
-- before writing this migration that it holds 47 rows across 9 roles
-- (family, repeat_client, client_escort_buyer, vendor, supervisor,
-- employee, accountant, vip_client, blocked) and is the core seed-role
-- table identity_brain.detect_identity() uses for real business
-- classification. Only the 2 rows with role='blocked' are removed
-- (8801864127930, 8801958122303 -- both source='admin_block').
--
-- Safe to apply: fpe_identity_anomalies was created in migration 041 (this
-- same day) with the anomaly-recording code being fully reverted alongside
-- this migration; no other table references it. The 2 deleted
-- fazle_contact_roles rows are re-creatable manually if ever needed, but
-- per this decision they must not be re-created without new owner
-- instruction.

DELETE FROM fazle_contact_roles WHERE role = 'blocked';

DROP TABLE IF EXISTS fpe_identity_anomalies;
