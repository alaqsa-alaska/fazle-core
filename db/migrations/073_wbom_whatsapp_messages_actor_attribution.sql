-- 073_wbom_whatsapp_messages_actor_attribution.sql
-- Stage B1 (2026-08-28, canonical-history baseline plan): minimum
-- backward-compatible attribution fields for wbom_whatsapp_messages.
--
-- Existing writer inventory (every current writer of this table, and what
-- actor_type each can reliably know without inference from message text):
--
--   modules.bridge_poller._save_raw_core_copy()   -- WhatsApp bridge1/2/3
--     genuine inbound (is_from_me=0). actor_type='external_user'. One
--     known, disclosed exception: a Hermes admin-relay reply sent via
--     bridge1 can land as a genuine is_from_me=0 inbound row on bridge2
--     (the cross-bridge echo case modules.message_router's own echo guard
--     already recognizes and silently ignores at the ROUTING layer) --
--     this raw-copy save happens earlier than that guard, so a small,
--     known fraction of "external_user" inbound rows are actually an
--     echo of Fazle Core's own earlier reply, not a new external message.
--     Not fixed in this migration -- disclosed, not silently assumed
--     away; closing it would require duplicating the echo guard's own
--     signature check at persistence time, out of this stage's scope.
--
--   modules.bridge_poller._save_message(), 2 call sites (customer-reply
--     and the internal "admin-hermes-reply" AI-console cascade -- NOT the
--     real external Hermes agent, see modules.admin_directives.router's
--     own 2026-08-04 docstring correction of this exact naming
--     confusion) -- fazle-core's own automated reply generation.
--     actor_type='automation'.
--
--   app.main._save_message() (a second, separate function of the same
--     name writing to the same table) -- Meta channels (messenger,
--     instagram, fb_comment, meta/whatsapp-business). Inbound:
--     actor_type='external_user'. Outbound: fazle-core's own auto-reply
--     cascade, actor_type='automation'. This IS a genuine, pre-existing
--     shared cross-channel canonical table for WhatsApp+Meta -- a
--     correction to this session's earlier read-only audit, which only
--     checked modules.social_auto_reply's own separate
--     social_inbox_events/social_reply_queue tables and missed this
--     older, still-live parallel path.
--
--   modules.recruitment_recovery_sweep._reconcile_confirmed_sends() --
--     reuses bridge_poller._save_message to backfill confirmed-sent
--     ('recovery_sweep_reply', 'hermes_admin_send' purposes only --
--     NOT 'hermes_relay_reply', the real Hermes relay's purpose, which
--     this reconciliation never covered) rows missing a canonical
--     record. actor_type='automation' (reconciling automation-generated
--     sends).
--
--   modules.message_archive.save_message() -- used only by two offline,
--     one-off disaster-recovery scripts (recover_from_backup.py,
--     recover_critical_numbers.py), not live runtime traffic. Out of
--     live-attribution scope; not touched here.
--
-- New writers this stage adds (B2, B3): _deliver_hermes_reply()
-- (actor_type='hermes') and the Bridge2 self-chat outgoing scanner
-- (actor_type='human_device') -- both additive, no schema impact beyond
-- what this migration already adds.
--
-- Deliberately NOT reusing identity_role -- that column describes the
-- counterparty/business identity (who Fazle Core resolved the sender/
-- recipient to be), a completely different concept from who/what
-- generated THIS row's own content.
BEGIN;

ALTER TABLE public.wbom_whatsapp_messages
    ADD COLUMN IF NOT EXISTS actor_type text,
    ADD COLUMN IF NOT EXISTS actor_id text;

COMMENT ON COLUMN public.wbom_whatsapp_messages.actor_type IS
    'Who/what produced this row''s content (never inferred from message '
    'text): external_user, human_device, human_operator, hermes, '
    'automation, system. NULL where the writing code path cannot state '
    'this with certainty -- left NULL rather than guessed.';
COMMENT ON COLUMN public.wbom_whatsapp_messages.actor_id IS
    'Stable identifier for the actor when one is reliably known at write '
    'time (e.g. the admin phone for human_device, "hermes-runner" for '
    'hermes) -- optional, NULL when no accurate stable value exists.';

-- Not a hard CHECK constraint: some existing/legacy rows and some future
-- writers may legitimately need NULL (unknown actor) rather than a forced
-- guess, and a new actor_type value should never require a migration to
-- add. Documented, not enforced.

COMMIT;
