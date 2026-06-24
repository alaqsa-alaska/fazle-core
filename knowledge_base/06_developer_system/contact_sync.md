---
title: Contact Sync System
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Contact Sync System
**KB Article ID:** DEV-06-CONTACT-SYNC
**Source:** `modules/contact_sync/__init__.py` (357 lines — read 2026-06-23)
**Visibility:** Developer
**Certified:** 2026-06-23 (Wave-4, W4-AUTH)

---

## Purpose

Merges WhatsApp contacts from all three bridge sources into the central `wbom_contacts` and `fazle_unified_contacts` tables. Ensures that inbound messages can look up a human-readable display name for any phone number.

---

## Three Sources

| Source | Bridge | SQLite DB Path | Bridge Number |
|---|---|---|---|
| Bridge 1 | `bridge1` | `/home/azim/whatsapp1/store/whatsapp.db` | `8801958122300` |
| Bridge 2 | `bridge2` | `/home/azim/bridges/bridge2/store/whatsapp.db` | `8801880446111` |
| Meta | Meta webhook | No SQLite — populated from inbound messages | N/A |

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

**Name selection:** `_best_name()` returns the longest non-empty name from: `full_name` → `first_name` → `push_name` → `business_name`.

---

## Sync Modes

### 1. Startup Full Sync

`sync_all_contacts()` → iterates all `BRIDGE_SOURCES` → calls `sync_bridge()` for each.

Run in executor (async wrapper around sync SQLite read):
```python
contacts = await loop.run_in_executor(None, _read_bridge_contacts, whatsapp_db, bridge_number)
```

### 2. Background Periodic Re-Sync

`start_contact_sync_loop(interval_seconds=3600)` — runs `sync_all_contacts()` every 1 hour in a background asyncio loop. Launched on application startup.

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

---

## Public API

| Function | Caller | Purpose |
|---|---|---|
| `sync_all_contacts()` | App startup | Full sync from all bridges |
| `sync_bridge(cfg)` | `sync_all_contacts()` | Sync one bridge source |
| `upsert_contact_from_message(phone, push_name, bridge)` | Message router (on each inbound) | Ensure sender is in contact DB |
| `get_display_name(phone)` | Identity brain, router | Look up best known name for a phone |
| `start_contact_sync_loop(interval_seconds)` | App startup | Launch background hourly re-sync |
| `init_tables()` | App startup | Create contact tables if missing |

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
- `automation_pipeline.md` — `start_contact_sync_loop` started on app init
- `database_rules.md` — `wbom_contacts` table ownership and schema
