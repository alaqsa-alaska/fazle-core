---
title: Task Brief — Chat History Sync (Phase 1) then Reply-Gap Fix (Phase 2)
owner: Fazle Core Admin
status: not started
created: 2026-07-15
---

# Task Brief: Chat History Sync, then Reply-Gap Fix

## Order of execution — strict, do not deviate

1. **Phase 1 (build now):** Chat History Sync.
2. **Hard stop.** Report Phase 1 verification results back before touching anything in Phase 2.
3. **Phase 2 (later, only after Phase 1 is confirmed running):** Reply-gap investigation and fix.

Do not combine the two phases in one session, one commit, or one restart. They are
independent in risk profile: Phase 1 touches nothing in the running app; Phase 2
touches live message-routing logic on a production-critical path.

## Repo context for whichever agent picks this up

- Repo: `/home/azim/core` ("fazle-core"). Read `.claude/CLAUDE.md` first — it has the
  non-negotiable rules (no schema/business-logic change beyond what's scoped here, no
  casual restarts, KB-first for any behavior change, docs stay in sync with code).
- Before writing any code for either phase: read the relevant
  `knowledge_base/06_developer_system/` and `knowledge_base/04_business_rules/` articles
  and the current production code, then compare — this repo's own process requires a
  gap analysis before implementation, not code-first exploration.
- Never restart `fazle-core.service` except via `scripts/restart_gate.sh --restart`.
- Only commit when explicitly asked; run tests before claiming anything works.

---

## Phase 1 — Chat History Sync

**Goal:** capture every inbound and outbound WhatsApp message (bridge1/bridge2/meta) into
one queryable table on this VPS, without modifying any existing table, code path,
process, or running service. Purely additive and read-only against current production
tables.

### Verified facts to build from (checked directly against the live DB on 2026-07-15 — re-verify if time has passed)

- `wbom_whatsapp_messages`: PK is `message_id` (not `id`). Relevant columns:
  `sender_number`, `receiver_number`, `message_body`, `message_type`, `platform`,
  `direction` (`'inbound'`/`'outbound'`), `received_at` (not `created_at`).
- `fazle_outbound_queue`: PK is `id`. Relevant columns: `recipient`, `body`,
  `source_bridge`, `purpose`, `status` (`pending`/`sending`/`sent`/`failed`/`dlq`),
  `sent_at` (nullable), `created_at`.
- **Do not pull outbound messages from both tables.** Verified: the customer-reply
  auto-send path writes the same message to *both* `wbom_whatsapp_messages`
  (`direction='outbound'`) *and* `fazle_outbound_queue` at send time. Admin
  notifications/forwards (`_notify_admin_bridge`) write *only* to
  `fazle_outbound_queue`. There is no shared key across the two tables to de-dupe
  against. **Use `fazle_outbound_queue` as the sole outbound source; use
  `wbom_whatsapp_messages` for inbound only.**
- `fpe_wa_messages` is a separate, independent full copy of the same bridge traffic
  (kept by the payroll-engine subsystem for its own accounting audit trail) — it is
  not a distinct set of messages. Do not use it as a chat_history source; it would
  duplicate inbound rows already covered by `wbom_whatsapp_messages`.
- Neither `wbom_whatsapp_messages` nor `fazle_outbound_queue` is subject to any
  retention/purge job (checked production crontab) — both grow forever, so a polling
  cursor sync will not miss historical data as long as cursors are tracked correctly.

### 1a. New table — new migration file only, no existing table touched

Add `db/migrations/0NN_chat_history.sql`. Do **not** guess the next number — check
`knowledge_base/06_developer_system/database_rules.md` § Migration Governance and the
live `schema_migrations` table / `db/migrate.py` for the actual next unused integer.

```sql
CREATE TABLE chat_history (
    id              BIGSERIAL PRIMARY KEY,
    thread_id       TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound', 'outbound_pending')),
    sender_phone    TEXT,
    receiver_phone  TEXT,
    message_body    TEXT,
    message_type    TEXT,
    bridge          TEXT,          -- bridge1 / bridge2 / meta
    purpose         TEXT,          -- outbound only: customer-reply / internal-notification / escort-review-not-order / ...
    status          TEXT,          -- outbound only: sent / failed / dlq
    source_table    TEXT NOT NULL,
    source_id       BIGINT NOT NULL,
    sent_at         TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source_table, source_id)
);
CREATE INDEX ON chat_history (thread_id, sent_at);
```

`outbound_pending` is reserved for an optional future third source
(`fazle_draft_replies` — replies generated but never sent). Do not wire that source up
in Phase 1 unless separately asked; the column value just needs to exist so it isn't a
schema change later.

### 1b. Standalone sync script — outside the app, not imported by it

`/opt/chat_sync/sync.py`, deliberately decoupled from this repo per the Owner's own
instruction.

Requirements:
- Poll interval configurable via env var (default 15–30s), not hardcoded.
- Two independent, cursor-tracked read loops, each keyed on `MAX(source_id) WHERE
  source_table = '<table>'` in `chat_history`:
  - **Inbound:** `SELECT message_id, sender_number, receiver_number, message_body,
    message_type, platform, received_at FROM wbom_whatsapp_messages WHERE direction =
    'inbound' AND message_id > :last_id ORDER BY message_id ASC LIMIT 1000`
  - **Outbound:** `SELECT id, recipient, body, source_bridge, purpose, status,
    COALESCE(sent_at, created_at) AS sent_at FROM fazle_outbound_queue WHERE status IN
    ('sent','failed','dlq') AND id > :last_id ORDER BY id ASC LIMIT 1000`
- `thread_id` = the customer's phone number in both directions (inbound:
  `sender_number`; outbound: `recipient`).
- Resolve "our own number" per row from `platform` (wbom) / `source_bridge` (queue),
  mapped through `.env`'s `BRIDGE1_NUMBER` / `BRIDGE2_NUMBER` / `ADMIN_META_NUMBER` —
  do not hardcode a single placeholder string for it.
- `ON CONFLICT (source_table, source_id) DO NOTHING` on insert — idempotent, safe to
  re-run or restart without duplicating.
- Read `DATABASE_URL` from environment only; never hardcode credentials in the script.
- Log a one-line summary per cycle only when it actually synced >0 rows.

### 1c. Systemd unit

`/etc/systemd/system/chat-sync.service` — standalone. `After=postgresql.service` only.
No dependency on `fazle-core.service`. `Restart=always`.

### 1d. Verification checklist before declaring Phase 1 done

- [ ] Migration applied cleanly and recorded in `schema_migrations`.
- [ ] Script run manually once; rows land in `chat_history` with correct thread
      grouping for a real recent conversation — spot-check against
      `wbom_whatsapp_messages` / `fazle_outbound_queue` directly.
- [ ] Zero changes to any existing table or file in this repo other than the one new
      migration file — confirm via `git status`/`git diff`.
- [ ] `fazle-core.service` was **not** restarted for this phase — `chat-sync.service`
      is fully independent and must never require it.
- [ ] `systemctl enable --now chat-sync` done; `systemctl status chat-sync` active;
      log shows successful sync cycles with no errors.

**Stop here. Report results. Do not start Phase 2 in the same pass.**

---

## Phase 2 — Reply-gap fix (only after Phase 1 is confirmed working)

### Background (findings from 2026-07-15 — re-verify current state before acting, it may have changed)

Since roughly 2026-06-15/06-23, most inbound customer messages to bridge1/bridge2/meta
have received no automated reply:

- `wbom_whatsapp_messages` outbound rows stop dead at 2026-06-15/06-23 while inbound
  continues to today.
- `fazle_outbound_queue`'s `purpose='customer-reply:*'` rows: only 9 since 2026-06-15,
  last one 2026-06-23, all "recruitment" purpose — everything else sent since then is
  internal/admin traffic.
- The social-engine's own sent log (`social_sent_log`): 2 rows total, ever, both from
  May — despite `social_inbox_events` receiving 1,077+ rows continuing to today.
- `SOCIAL_AUTO_REPLY_SINGLE_ENGINE=true` diverts most bridge1/bridge2 traffic into
  `ingest_social_event()` (`modules/bridge_poller._requires_legacy_workflow()` gates
  the escape hatch back to the legacy router) — but the social daemon's send worker
  has been running with `enabled=False` this whole period, so diverted messages are
  ingested and then nothing sends.

### 2a. Investigate first — read-only, KB-first, do not write code yet

- Re-confirm current state hasn't changed (re-check `social_sent_log`,
  `social_inbox_events`, `fazle_outbound_queue` purpose breakdown, and the social
  daemon's `enabled=` flag in recent logs — do not trust the numbers above as still
  current without re-checking).
- Determine *why* the social daemon is disabled: check
  `knowledge_base/00_governance/management_decisions.md`, recent commit history, and
  `.env` history for whether this was a deliberate Owner decision (there is precedent
  in this project for exactly this kind of "was it deliberate or drift" question —
  see the `AUTO_REPLY_ENABLED` and `AI_CONSOLE_CHAT_ENABLED` history) or an
  unintentional regression.
- Produce the standard proposal this repo requires before any production change:
  current behavior → KB reference → gap analysis → proposed implementation → risk
  assessment → rollback strategy. **Stop for Owner approval before writing code.**

### 2b. Hard requirement for the fix — draft-to-admin, never direct auto-send

Whatever the root-cause fix turns out to be, once replies are working again:

- **No message may be auto-sent directly to a customer.** Every generated reply must
  be created as a draft via the existing mechanism (`create_draft_reply()` /
  `fazle_draft_replies`, already used by `modules/bridge_poller._save_draft()`) and
  routed to the admin number(s) (`ADMIN_NUMBERS` / `ADMIN_META_NUMBER` /
  `ADMIN_BRIDGE1_NUMBER` / `ADMIN_BRIDGE2_NUMBER` from `.env`) for manual approval
  before anything is sent. Reuse the existing draft mechanism — do not invent a new
  one.
- This must apply consistently to every role/number already treated specially in
  `.env` and in code: `ESCORT_CLIENT_PHONES`, `DRAFT_ALWAYS_PHONES`,
  `DRAFT_ALWAYS_ROLES` (currently `accountant,client_escort_buyer,vip_client,
  repeat_client`), `DRAFT_ALWAYS_NAMES`, `ACCOUNTANT_PHONE`. Verify each still gets
  exactly the treatment already defined for it in
  `modules/bridge_poller._is_draft_always()` and `modules/message_router` — confirm
  none of them get accidentally bypassed by whatever change re-enables replies.
- **Open question — get this confirmed explicitly by the Owner before implementing,
  do not assume either way:** should *every* re-enabled reply become a draft (i.e.
  retire genuine auto-send entirely, for all senders), or only replies for senders
  that already match the existing `DRAFT_ALWAYS_*` config (with other senders still
  getting real auto-send once the underlying pipeline bug is fixed)? The instruction
  behind this brief reads as "instead of auto-reply, always create a draft, delivered
  to the admin number" — but confirm this is meant as a blanket policy change and not
  just "don't break the existing draft-always list" before scoping the code change,
  since the two readings have very different blast radius.

### 2c. Verification

- Unit tests for any gating-logic change, run before restart — follow the precedent
  already set in this repo (`tests/unit/test_bridge_poller_legacy_gate.py`,
  `tests/unit/test_escort_module.py` additions from the escort-order-drop fix on
  2026-07-15).
- `scripts/restart_gate.sh --restart` + `/health` check, same procedure as the
  escort-order fix.
- Update the relevant KB doc(s) describing reply/draft behavior in the same change.
