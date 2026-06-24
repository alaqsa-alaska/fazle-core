---
title: Reviewed Reply Memory
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Reviewed Reply Memory
**KB Article ID:** DEV-06-REVIEWED-REPLY-MEMORY
**Source:** `modules/reviewed_reply_memory/__init__.py` (365 lines — read 2026-06-23)
**Visibility:** Developer / Admin only — do NOT set `safe_for_customer=True`
**Certified:** 2026-06-23 (Wave-4, W4-AUTH)

---

## Purpose

When an admin edits or approves a draft reply, the corrected text is stored in `fazle_reviewed_replies`. Future messages from senders with the same intent + role (+ optionally phone) retrieve the cached reply and bypass the LLM entirely. This is Step 14 in the message routing chain — after KB miss, before AI fallback.

**Effect:** Repeated queries get consistent, admin-approved answers without LLM variance or cost.

---

## Kill-Switch

```
REVIEWED_REPLY_MEMORY_ENABLED=true  (current production value)
```

When `false`: `lookup_reviewed_reply()` returns `None` immediately — module is completely bypassed. Step 14 is skipped; routing falls through to Step 15 (AI).

Source: `_feature_enabled()` reads `get_settings().reviewed_reply_memory_enabled`.

---

## Position in Routing Chain

```
Step 13 — KB lookup (kb_get_reply)
    └── miss → Step 14 — reviewed_reply_memory.lookup_reviewed_reply()
                  └── hit  → return cached reply (no LLM call)
                  └── miss → Step 15 — AI fallback (RAG + LLM)
```

---

## How a Reviewed Reply Is Created

`create_or_update_from_edit()` is called automatically when an admin edits a pending draft.

**Blocking conditions (reply NOT stored):**
1. Feature disabled (`REVIEWED_REPLY_MEMORY_ENABLED=false`)
2. Reply fails `check_draft_quality()` quality gate
3. Reply has unsafe content (`_has_unsafe_content()` — see below)
4. Draft has no `intent` field
5. Draft type is in `_UNSAFE_DRAFT_TYPES` (see below)

**On eligible edit:** Upserts to `fazle_reviewed_replies` with `status='active'`.

---

## `fazle_reviewed_replies` Table

Key columns written by this module:

| Column | Type | Notes |
|---|---|---|
| `id` | bigserial PK | |
| `source_draft_id` | int | FK to the source draft in `fazle_draft_replies` |
| `source` | text | Bridge source (bridge1, bridge2, meta) |
| `intent` | text | Normalized intent string (lowercase) |
| `draft_type` | text | Default: `"generic"` |
| `role` | text | Sender role at time of edit |
| `recipient_phone` | text | Full canonical phone |
| `last10_phone` | text | Last 10 digits of phone — used for matching |
| `language` | text | Language of the reply |
| `normalized_trigger_text` | text | Lowercased, whitespace-collapsed trigger message (≤ 1000 chars) |
| `match_scope` | text | `"intent_role_phone"` or `"intent_role"` |
| `reply_text` | text | Admin-approved reply text |
| `status` | text | `"active"` or `"disabled"` |
| `created_by` | text | Admin phone who made the edit |
| `usage_count` | int | Incremented on each cache hit |
| `last_used_at` | timestamptz | Updated on each cache hit |
| `priority` | int | Lower = higher priority (used in ORDER BY) |
| `meta` | jsonb | `{source_reason, original_reply_text, created_from_status, guard_version}` |

---

## Lookup — 3-Attempt Fallback Cascade

`lookup_reviewed_reply()` tries 3 progressively looser match scopes, stopping at first hit:

| Attempt | Match Keys | Scope Name |
|---|---|---|
| 1 (most specific) | intent + role + draft_type + last10_phone | `intent_role_phone` |
| 2 | intent + role + draft_type (drop phone) | `intent_role` |
| 3 (least specific) | intent + role (drop draft_type and phone) | `intent_role` |

**Tie-breaking within a scope:** `ORDER BY priority ASC, usage_count DESC, updated_at DESC`

**On hit:** `usage_count` and `last_used_at` are incremented (unless `touch=False`).

---

## Unsafe Draft Types — Never Stored or Matched

```python
_UNSAFE_DRAFT_TYPES = frozenset({"attendance", "payment", "gap_action"})
```

Reviewed replies are never created for attendance confirmations, payment records, or gap-action messages. These require admin judgment on every instance.

---

## Unsafe Content Guard — Never Stored

Two safety checks on the reply text before storage:

**`_UNSAFE_REPLY_PREFIXES`** (startswith match, case-insensitive):
```
"approve ", "reject ", "paid ", "advance ",
"escortconfirm ", "payroll ", "release ", "backup "
```

**`_UNSAFE_REPLY_SUBSTRINGS`** (substring match, case-insensitive):
```
"api_key", "password", "token=", "secret", ".env",
"sudo ", "systemctl", "/home/azim"
```

If either check fires, the reply is blocked from storage — prevents admin command text or credential leakage from entering the cache.

---

## Return Value of `lookup_reviewed_reply()`

On hit: returns full `fazle_reviewed_replies` row as a dict (all columns).

Message router at Step 14 reads:
```python
_reviewed["reply_text"]    # the cached reply text
_reviewed["match_scope"]   # "intent_role_phone" or "intent_role"
_reviewed["id"]            # for logging
```

On miss or feature disabled: returns `None`.

---

## Admin Operations

| Function | Purpose |
|---|---|
| `lookup_reviewed_reply(sender_phone, intent, role, ...)` | Step 14 cache lookup (main read path) |
| `create_or_update_from_edit(draft_row, new_text, admin_phone, ...)` | Called on admin draft edit (main write path) |
| `list_reviewed_replies(limit, status, intent, role, phone)` | List all stored reviewed replies (admin UI) |
| `disable_reviewed_reply(id, reason)` | Soft-disable a specific reviewed reply |
| `reactivate_reviewed_reply(id)` | Re-enable a disabled reviewed reply |
| `build_draft_meta(reviewed_row, base_meta)` | Attach reviewed reply metadata to a draft meta dict |

---

## Observability

Metric increments (via `modules.observability.inc()`):

| Metric | Labels | When |
|---|---|---|
| `reviewed_reply_lookup_total` | `result=hit\|miss\|blocked` | Every lookup |
| `reviewed_reply_used_total` | `scope=intent_role_phone\|intent_role` | On cache hit |
| `reviewed_reply_created_total` | — | On new reviewed reply insertion |
| `reviewed_reply_updated_total` | — | On update of existing reviewed reply |
| `reviewed_reply_disabled_total` | — | On disable |

---

## Cross-References

- `workflow_engine.md` — Step 14 position in 15-step routing chain
- `runtime_gateway_flags.md` — `REVIEWED_REPLY_MEMORY_ENABLED` kill-switch
- `automation_pipeline.md` — draft quality gate (`check_draft_quality`)
