---
title: Contact Sync System
owner: Fazle Core Admin
status: active
last_verified: 2026-07-22
runtime_index: true
---

# Contact Sync System
**KB Article ID:** DEV-06-CONTACT-SYNC
**Source:** `modules/contact_sync/__init__.py` (re-verified 2026-07-22, several corrections below)
**Visibility:** Developer
**Certified:** 2026-06-23 (Wave-4, W4-AUTH); corrected 2026-07-22

---

## Purpose

Merges WhatsApp contacts from all three bridge sources into the central `wbom_contacts` and `fazle_unified_contacts` tables. Ensures that inbound messages can look up a human-readable display name for any phone number.

---

## Three Sources

| Source | Bridge | SQLite DB Path | Bridge Number |
|---|---|---|---|
| Bridge 1 | `bridge1` | `_settings.bridge1_whatsapp_db_path` (`/home/azim/whatsapp1/store/whatsapp.db`) | `8801958122300` |
| Bridge 2 | `bridge2` | `_settings.bridge2_whatsapp_db_path` (`/home/azim/whatsapp2/store/whatsapp.db`) | `8801880446111` |
| Meta | Meta webhook | No SQLite — populated from inbound messages | N/A |

**Corrected 2026-07-22:** `BRIDGE_SOURCES` previously hardcoded bridge2's path as
`/home/azim/bridges/bridge2/store/whatsapp.db` — that file does not exist. The real,
actively-updated file was (and is) wherever `app/config.py`'s `bridge2_whatsapp_db_path`
setting points. bridge2 contact sync had been silently reading zero contacts (caught by
`_read_bridge_contacts`'s broad `except`, only logged) until this was fixed. `BRIDGE_SOURCES`
now reads both paths from settings — the same source of truth `bridge_poller` itself uses —
instead of a second, independently-drifting hardcoded copy.

**Bridge numbers are skipped during sync** (a bridge never stores itself as a contact).

---

## Database Tables

### `wbom_contacts` — Master Contact Table

Primary contact lookup. One row per `(whatsapp_number, platform)`.

Key columns: `whatsapp_number` (canonical 8801XXXXXXXXX), `display_name`, `platform` (`"whatsapp"`), `last_seen`, `updated_at`.

**Upsert rule:** `display_name` is only updated if the new name is longer than the existing name (longer = more complete).

### `fazle_unified_contacts` — Dedup / Normalize Layer

One row per normalized phone (PRIMARY KEY on `phone`). Used for fast display name lookup by `get_display_name()`.

Columns: `phone`, `display_name`, `source_bridge`, `first_seen`, `last_updated`.

**Same "longer name wins" upsert rule** as `wbom_contacts`.

### `fazle_contact_aliases` — All Known Names

Stores every name variant ever seen for a phone. PK = `(phone, alias_name)`.

Columns: `phone`, `alias_name`, `source_bridge`, `first_seen`, `last_seen`.

Written by `_upsert_alias()` on every sync and on every inbound message with a push name.

### `fazle_contact_sync_log` — Per-Bridge Sync Metadata

One row per bridge. Tracks `synced_at` and `contacts_upserted` count. PK = `bridge`.

### `fazle_contact_presence` — Per-(Phone, Bridge) Absence Tracking (new 2026-07-22)

Migration `056_contact_sync_presence_tracking.sql`. PK = `(phone, bridge)`. Tracks
`consecutive_absences` and `last_present_at`. Deliberately keyed per bridge, not just per
phone: a contact present in bridge1's phone book but not bridge2's is normal (two different
phones' address books), not evidence of deletion — a single phone-keyed column couldn't
represent that. Powers the soft-delete-then-hard-delete policy below.

### `fazle_contact_hard_delete_log` — Hard-Delete Audit Trail (new 2026-07-22)

Migration `056_contact_sync_presence_tracking.sql`. Written *before* every hard-delete
(irreversible by definition) with the contact's last known `display_name`/`relation` and
the absence-streak that triggered it, so a mistaken deletion is fully reconstructable.

---

## Phone Normalization

`contact_sync.normalize_phone()` wraps `modules.phone_normalizer.normalize_phone()` with JID handling:

1. Strip JID suffix: `phone@s.whatsapp.net` → `phone`; `phone:device@lid` → `phone`
2. Call `phone_normalizer.normalize_phone()` → canonical `8801XXXXXXXXX`
3. LID JIDs (`@lid`) → `None` (skipped entirely)

---

## SQLite Read — WhatsApp Contact DB Schema

Bridge SQLite table: `whatsmeow_contacts`

Columns read: `their_jid`, `first_name`, `full_name`, `push_name`, `business_name`

Filter: `WHERE their_jid LIKE '%@s.whatsapp.net'` (excludes groups and LID entries)

**Name selection — corrected 2026-07-22 (canonical-authority Owner decision):** `_best_name()`
previously returned the *longest* non-empty name across all four fields, treating
WhatsApp's own `push_name` as equally authoritative as the admin's own phone-book entry.
It now returns **priority-first, not longest-first**: `full_name` → `first_name` →
`push_name` → `business_name`. Verified empirically against live bridge2 data that these
fields genuinely differ and mean different things: `full_name`/`first_name` are populated
from the phone's own synced address book (what *we* call this person — e.g. saved as
"Escort Saimun"); `push_name` is what the other person set as their own WhatsApp profile
name (e.g. "Saimun"); `business_name` is a WhatsApp Business API field. The phone-book
fields are canonical for both routing and display; the WhatsApp-side fields are only a
fallback for a number the phone hasn't saved a name for at all — never an override of one
that exists. `modules/route_b/__init__.py`'s own separate `whatsmeow_contacts` reader had
the same bug (built with `push_name` first) and was fixed the same day to delegate to this
same `_best_name()` function rather than re-implement the priority independently.

---

## Sync Modes

### 1. Startup Full Sync

`sync_all_contacts()` → iterates all `BRIDGE_SOURCES` → calls `sync_bridge()` for each.

Run in executor (async wrapper around sync SQLite read):
```python
contacts = await loop.run_in_executor(None, _read_bridge_contacts, whatsapp_db, bridge_number)
```

### 2. Daily Scheduled Sync (corrected 2026-07-22 — this was never actually live before)

**Live mechanism:** `job_daily_contact_sync()`, registered in `modules.scheduler` as
`daily_contact_sync`, `CronTrigger(hour=CONTACT_SYNC_HOUR, minute=0)` — default 04:00
Asia/Dhaka, tunable via the `CONTACT_SYNC_HOUR` env var (matches the existing
`os.getenv()` convention other scheduler jobs use, e.g. `PAYROLL_AUTO_COMPUTE_HOUR`).
This is the actual "at least once a day" mechanism.

**Correction:** this article previously described `start_contact_sync_loop(interval_seconds=3600)`
as "launched on application startup" — that was never true. Grepping the whole codebase
confirms it has zero callers anywhere; the only prior invocation of `sync_all_contacts()`
was the admin-triggered manual "sync now" button (`POST /api/wa/contacts/sync`,
`modules/wa_chat_frontend`). Before 2026-07-22, contact sync only ever ran when an admin
clicked that button — there was no automatic sync at all. `start_contact_sync_loop()` is
left in the module, undeleted, explicitly marked superseded in its own docstring, per repo
convention (flag dead code, don't silently remove it) — it is not the live mechanism.

### 3. Per-Message Upsert

`upsert_contact_from_message(phone, push_name, bridge_number)` — called on every inbound message. Insert-only, doesn't overwrite a better existing name.

Write target: both `fazle_unified_contacts` and `wbom_contacts`, plus `_upsert_alias()` if push_name is non-empty.

---

## Name Merge Strategy

Consistent across all write paths:

```sql
SET display_name = CASE
    WHEN length(EXCLUDED.display_name) > length(existing.display_name)
    THEN EXCLUDED.display_name
    ELSE existing.display_name
END
```

**Implication:** A shorter push_name from a new message will never overwrite a longer saved name. Contact book names (full_name from SQLite) are typically longer and win over push names.

**Note:** this DB-level "longer wins" merge rule is unchanged and still applies across
syncs. It's a separate, still-valid safety net from the 2026-07-22 source-priority fix
above — that fix controls which *raw field* wins when reading fresh data from one bridge
sync; this rule controls whether a *new* sync's chosen name is allowed to overwrite what's
already stored from a *previous* sync. Both apply, in that order.

---

## Deletion Detection & Soft-Delete-Then-Hard-Delete Policy (new 2026-07-22)

**Owner policy:** a phone missing from a single sync is not reliable evidence the contact
was actually deleted from the phone — the phone could have been offline, the bridge export
could have failed, or the SQLite read could have been incomplete. So deletion is staged:

1. **First consecutive miss** (this bridge's `fazle_contact_presence.consecutive_absences`
   goes 0 → 1): **soft-delete only** — `wbom_contacts.is_active = false`,
   `marked_for_delete_at = NOW()` (both pre-existing columns, no schema change needed for
   this part). Fully reversible: if the contact reappears in a later sync, `_mark_present()`
   resets `is_active = true` and clears `marked_for_delete_at` automatically.
2. **Nth consecutive miss** (`consecutive_absences >= CONTACT_SYNC_HARD_DELETE_AFTER`,
   default `3`, env-tunable): **hard-delete** — the `wbom_contacts` row is actually removed,
   after writing a row to `fazle_contact_hard_delete_log` first (never skipped).
3. **`wbom_contacts.is_protected = true` contacts are never touched by either step**, at
   any absence count — this pre-existing column is exactly the "don't ever auto-delete
   this one" escape hatch and is honored as such.

Implementation: `_mark_present()`, `_handle_missing_contacts()`, `_hard_delete_contact()`
in `modules/contact_sync/__init__.py`. Each `sync_bridge()` call now: upserts every
currently-read contact, resets/reactivates presence for all of them, then diffs the
bridge's previously-tracked phone set against the current one and acts on whatever's
missing. Verified live 2026-07-22: first full run against both bridges populated 3,821
`fazle_contact_presence` rows (1,991 bridge1 + 1,830 bridge2, matching `contacts_upserted`
exactly); a second run with no real-world contact-book changes correctly found zero new
absences.

---

## Public API

| Function | Caller | Purpose |
|---|---|---|
| `sync_all_contacts()` | `job_daily_contact_sync()`, admin manual-sync API | Full sync from all bridges, including deletion detection |
| `sync_bridge(cfg)` | `sync_all_contacts()` | Sync one bridge source; returns `{upserted, soft_deleted, hard_deleted}` (was a bare `int` before 2026-07-22) |
| `job_daily_contact_sync()` | `modules.scheduler` (`daily_contact_sync`, 04:00 Asia/Dhaka default) | The live daily sync entry point |
| `upsert_contact_from_message(phone, push_name, bridge)` | Message router (on each inbound) | Ensure sender is in contact DB |
| `get_display_name(phone)` | Identity brain, router | Look up best known name for a phone |
| `init_tables()` | Not yet wired to app startup — call manually or via the scheduler job's first run | Create contact tables (including the two new 2026-07-22 tables) if missing |
| `start_contact_sync_loop(interval_seconds)` | **Not called anywhere** — superseded by the scheduler job, kept only as documented dead code | (historical) |

---

## `get_display_name(phone)` — Display Name Lookup

```python
async def get_display_name(phone: str) -> str
```

Normalizes phone → queries `fazle_unified_contacts.display_name`. Returns `""` on miss or error (never raises).

Used by identity brain and message router to attach human-readable names to sender records.

---

## Security Note

- SQLite files are opened read-only (`mode=ro` via URI): `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
- Contact names from WhatsApp may contain arbitrary Unicode — they are stored as-is, not sanitized
- Bridge phone numbers (`8801958122300`, `8801880446111`) are hardcoded and skipped during sync

---

## Cross-References

- `phone_normalizer.md` — canonical 8801XXXXXXXXX format used as PK in contact tables
- `identity_brain.md` — uses `get_display_name()` to attach name to sender context
- `automation_pipeline.md` — corrected 2026-07-22: `start_contact_sync_loop` was never
  started on app init; `job_daily_contact_sync` (via `modules.scheduler`) is the real
  mechanism now
- `database_rules.md` — `wbom_contacts` table ownership and schema
- `../00_governance/management_decisions.md` — 2026-07-22 entry, full record of this
  article's corrections plus the related `fazle_contact_roles` duplicate-phone cleanup
- `db/migrations/056_contact_sync_presence_tracking.sql`
