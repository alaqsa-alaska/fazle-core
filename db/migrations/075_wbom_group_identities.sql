-- Migration 075: wbom_group_identities
--
-- Stage D1 (2026-08-28, "Group Identity: first discover, then design").
--
-- Discovery, not invention: every WhatsApp bridge (bridge1/2/3, whatsmeow-
-- based) already maintains its own authoritative `chats` table inside its
-- SQLite store (messages.db) -- populated live by whatsmeow itself as it
-- syncs group metadata from WhatsApp (jid, name, last_message_time). This
-- IS the authoritative source Stage D1 asked us to look for first. This
-- migration adds the smallest canonical structure to mirror that bridge-
-- provided metadata into Postgres via an explicit sync (see
-- modules/group_identity.py) -- it does NOT replace the bridge's own store
-- as the source of truth, and does NOT hand-write any JID.
--
-- display_name vs business_label (kept deliberately separate, per Stage D1
-- instruction -- these must not be conflated):
--   display_name    -- the provider/WhatsApp group's own subject, exactly
--                      as synced from the bridge's chats table. Mutable
--                      (a group can be renamed on WhatsApp at any time).
--   business_label   -- OUR internal meaning (e.g. 'office_internal'),
--                      NULL by default. Never auto-set by the sync
--                      function -- only ever set by an explicit, separate
--                      action (never inferred from display_name content,
--                      role labels, or participant behavior).
--
-- Real finding during discovery (disclosed in the Stage D report, not
-- hidden): the SAME group_jid can carry a DIFFERENT display_name across
-- different bridges' local caches (bridge1 vs bridge2 disagreed on this
-- exact group's name at investigation time -- one bridge's cache was
-- stale relative to the other). group_jid is the stable identity;
-- display_name is per-source, per-observation, intentionally NOT
-- collapsed into one row per JID -- see the (group_jid, source_bridge)
-- primary key below.
CREATE TABLE IF NOT EXISTS wbom_group_identities (
    group_jid          TEXT NOT NULL,
    source_bridge       TEXT NOT NULL,
    display_name         TEXT,
    business_label        TEXT,
    first_seen_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at             TIMESTAMPTZ,
    metadata_updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (group_jid, source_bridge)
);

COMMENT ON TABLE wbom_group_identities IS
    'Stage D1: mirror of each WhatsApp bridge''s own authoritative chats.name '
    'cache for @g.us JIDs, synced explicitly via modules.group_identity, '
    'never hand-maintained. One row per (group_jid, source_bridge) because '
    'different bridges'' local name caches can genuinely disagree.';
COMMENT ON COLUMN wbom_group_identities.display_name IS
    'Provider/WhatsApp group subject as last synced from this source_bridge. '
    'NOT our internal label -- see business_label.';
COMMENT ON COLUMN wbom_group_identities.business_label IS
    'Our internal meaning for this group (e.g. office_internal). NULL by '
    'default. Only ever set by an explicit action, never auto-derived from '
    'display_name text, role labels, or participant behavior.';
COMMENT ON COLUMN wbom_group_identities.last_seen_at IS
    'The source bridge''s own last_message_time for this chat at sync time '
    '-- when the bridge last saw activity in this group, not when we synced.';
COMMENT ON COLUMN wbom_group_identities.metadata_updated_at IS
    'When our own sync last wrote/refreshed this row (distinct from '
    'last_seen_at, which is the bridge''s own activity timestamp).';

CREATE INDEX IF NOT EXISTS idx_wbom_group_identities_business_label
    ON wbom_group_identities (business_label) WHERE business_label IS NOT NULL;
