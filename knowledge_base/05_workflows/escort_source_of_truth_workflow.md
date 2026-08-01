---
title: Escort Source Of Truth Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-27
runtime_index: true
---

# Escort Source Of Truth Workflow

## Workflow Diagram

```text
Escort Client
      |
      v
Client -> Bridge
      |
      v
AI Parser
      |
      v
Draft (Not Source Of Truth)
      |
      v
Operation/Admin Review
      |
      v
Bridge -> Escort Client Confirmation
      |
      v
SOURCE OF TRUTH
      |
      v
Overwrite Draft
      |
      v
wbom_escort_programs
      |
      v
Escort Roster Projection
      |
      v
UI / Payroll / Reports
```

## Practical Meaning

- Client-origin messages are intake only.
- Drafts are operational placeholders only.
- Final assignment truth comes from the outbound bridge confirmation to the approved escort client.
- Confirmation overwrites draft mistakes instead of creating a second row.
- The roster projection must follow the final confirmed state.

## Direction Gate

Accepted draft direction:
- `ESCORT_CLIENT_PHONES -> bridge`

Accepted confirmation direction:
- `bridge -> ESCORT_CLIENT_PHONES`

Everything else:
- ignored for source-of-truth escort confirmation

## Matching Logic

Draft-to-confirmation reconciliation uses:
1. Client phone
2. Master mobile
3. Mother vessel
4. Start date

Bridge confirmation always wins when a match is found.

## Final State Requirements

After confirmation:
- Same program row is updated
- No duplicate row is created
- Destination aliases are normalized before save
- Program status is final
- Roster status is synced to the confirmed-equivalent visible state

## No-Match Behavior (updated 2026-07-19 — Workstream 2 Stage 2)

The above "no duplicate row is created" / "duplicate prevention works"
verification (2026-06-27) covered the *matched*-draft path only. Until
2026-07-19, a confirmation that failed to match any draft with high
enough confidence (`_find_pending_program()`'s scoring, threshold 5.5 —
see Matching Logic above) fell through to `_create_confirmed_from_admin()`,
creating a **second, unlinked confirmed program row** ("admin reply is
the source of truth"), followed by a best-effort post-hoc cleanup pass
(`reconcile_drafts_for_confirmation()`) — mitigation, not prevention.

**Changed 2026-07-19 (Owner decision):** no-match no longer auto-creates.
It now queues the confirmation in `wbom_unmatched_confirmations`
(migration 052) for admin review instead — no program row is created, no
draft rows are touched/reconciled, no client-facing reply is sent. An
admin must explicitly either link the confirmation to an existing draft
or confirm it really is a separate duty segment before any program row is
written. See `modules/escort/__init__.py::_enqueue_unmatched_confirmation()`
and `management_decisions.md` → "Draft-Confirmation Invariant, Workstream
2 Stage 2" for full detail.

**Stage 3 (2026-07-19):** the review actions themselves —
`POST /api/escort/unmatched-confirmations/{id}/link-existing`,
`.../confirm-new`, `.../reject`, `.../defer` (`modules/escort/routes.py`,
admin/superadmin only). `link-existing` updates the same target program
row via the same `_update_program_confirmed()` the matched-draft path
uses. `confirm-new` re-validates no confident match has appeared since
the item was queued before it will create a program — it is the only
remaining call path to `_create_confirmed_from_admin()`, never reached
automatically. No frontend UI yet — reachable only via direct API call as
of this note. See `management_decisions.md` → "... Stage 3" for the
concurrency design (non-blocking advisory locks, not row locks —
deliberately avoids the nested-connection deadlock class Phase 1I found).

## Production Verification

Live non-destructive verification completed on 2026-06-27 confirmed:
- draft creation works
- invalid client intake is ignored
- bridge confirmation overwrites corrupted draft values
- destination normalization works
- duplicate prevention works (matched-draft path only — see "No-Match
  Behavior" above for the no-match path's separate, later fix)
- roster list and export search both work
- browser search, view, edit, recalculate, config, escort-client management, and export all work

## Enforcement — Where The Gates Actually Live

Added in commit `41916b0` (2026-06-27) and unchanged since:

- **Draft creation gate** (`modules/escort/__init__.py`, `handle_escort_client_message()`):
  `_is_trusted_escort_source(source)` (bridge must be in `ESCORT_TRUSTED_SOURCES`) and
  `_is_allowed_escort_client(sender_phone)` (sender must be in `ESCORT_CLIENT_PHONES`),
  both checked before parsing; `is_minimum_escort_order(order)` (mother vessel + at
  least one lighter with lighter_vessel + master_mobile) checked before saving.
- **Confirmation gate** (`handle_admin_escort_completion()`):
  `_is_authoritative_bridge_confirmation(ctx)` — sender must be one of
  `ADMIN_NUMBERS`/`ADMIN_META_NUMBER`/`ADMIN_BRIDGE1_NUMBER`/`ADMIN_BRIDGE2_NUMBER`, and
  if a recipient is given it must be a registered `ESCORT_CLIENT_PHONES` entry.
- **Completed-draft detection gate** (`modules/escort/__init__.py`,
  `is_completed_escort_draft()`, checked by `bridge_poller._poll_bridge()`
  on every outgoing admin message *before* `handle_admin_escort_completion()`
  is even called): requires Mother Vessel + Lighter Vessel + Escort Name +
  Escort Mobile + a date, **and** a recognized company-name signature
  somewhere in the text. **2026-08-01 incident:** the company-name check
  (`_AL_AQSA_RE`) was a hardcoded regex listing only the pre-2026-07-21 name
  ("Al-Aqsa") and the 2026-07-21 rebrand name ("Al-Manaratayn") — it was
  never updated for the 2026-07-22 "Second Rebrand" to "Al-Rifa'i Integrated
  Services" (`management_decisions.md`, `Settings.company_name`), even
  though the *other* brand-name gate in this codebase (§14 of
  `whatsapp_reply_workflow.md`, a different, contact-name-based check) was
  correctly updated for it the same day. Every real, well-formed admin
  confirmation signed with the current name silently failed this gate from
  2026-07-22 onward — never confirming a program, never erroring, never
  logging why. Fixed by additionally accepting any message that mentions
  the live `Settings.company_name` (tolerant of punctuation/spacing), so a
  future rebrand only requires updating `COMPANY_NAME` in `.env`, not this
  file — see `_matches_company_name()`.
- **Draft TTL**: unconfirmed drafts (`status='draft'`) are hard-**deleted**, not
  soft-expired, after `DRAFT_TTL_HOURS` (default 24) — `expire_stale_drafts()` in
  `modules/escort_roster/db.py`, added in commit `82f2b33` (2026-07-05), run on a
  schedule by `modules/scheduler`. Full row content is written to
  `escort_roster_audit_logs` before each delete.

Session 16 (2026-07-16) audited this against an explicit non-negotiable business-rule
set and found all of the above already enforced; the only gap was 18 historical rows
predating the 2026-06-27 gate that had gone through the *old* soft-expire path and were
never retroactively swept — cleaned up one-time, see
`knowledge_base/00_governance/escort_sender_validation_audit_2026-07-16.md`. A
defensive second sweep for any future financially-inert legacy `status='expired'` row
was added to `expire_stale_drafts()` so this specific failure mode can't recur even in
an edge case.

## Phase B — Conversation Memory Engine (Stages 1-3, Shadow Mode) — 2026-07-19

Everything above this section is unchanged and still describes the
*only* path that writes `wbom_escort_programs`. Phase B adds a parallel,
strictly observational layer (`modules/conversation_memory`) upstream of
that path, with its own non-authoritative notion of "source of truth"
that must not be confused with this document's:

- **Conversation memory does not grant authority.** A message being
  authorized (`ESCORT_CLIENT_PHONES`, correct bridge direction) lets it
  *contribute fields to a shadow candidate*; it never makes that
  candidate a draft, and never bypasses the Direction Gate / Matching
  Logic / Enforcement rules above. The real source of truth remains
  exactly what this document already says: the bridge -> escort client
  confirmation message.
- **System-generated exclusion.** Re-checked independently inside
  `modules.conversation_memory.thread_reconstruction._observe()` via
  `modules.bridge_poller._is_system_generated_notification()` — the same
  detection this document's "Enforcement" section relies on elsewhere —
  rather than assuming the caller already filtered it out.
- **Existing-draft awareness is read-only.** When a shadow thread
  accumulates enough fields to look like a candidate, it checks (SELECT
  only) whether an existing `wbom_escort_programs` draft/confirmed/
  running row already matches — classified as `no_candidate`,
  `one_likely`, `several_ambiguous`, `likely_duplicate`, or
  `likely_correction` — and records that classification. It never
  modifies a draft and never reuses `_create_confirmed_from_admin()`'s
  dedup/replacement logic (that logic governs admin confirmation intake,
  a different decision with different consequences than client order
  intake).
- **Interleaving / grouping.** `modules/conversation_memory/grouping.py`
  decides which open thread (if any) a new message belongs to, using
  lighter_vessel and master_mobile as the hard identity anchors (Section
  5's Case B: different anchors from the same phone -> separate threads,
  never merged). Mother-vessel disagreement alone does not fork a new
  thread — it is flagged as an ordinary field-level conflict on the
  already-matched thread instead, since a real order's mother vessel is
  routinely supplied in a separate message from its lighter/mobile
  (Case A).
- **TTL / expiry.** Domain-configurable
  (`CONVERSATION_TTL_ESCORT_MINUTES`, default 30). A thread past its TTL
  is marked `expired` (never hard-deleted) and is never silently reopened
  by a later message — `get_open_thread(s)` filters `expires_at > NOW()`
  on every read, not just at sweep time.
- **Shadow comparison records.** Every observed message writes one row to
  `fazle_conversation_shadow_comparisons` (migration 054): the existing
  single-message parser's result side by side with this engine's
  conversation-aware result, field provenance, completion state,
  grouping decision, conflicts, and `would_create_draft` — structured
  data only, queryable for the aggregate metrics Owner review needs
  before any draft-activation decision.
- **Rollout state:** `CONVERSATION_MEMORY_ENABLED` /
  `WHATSAPP_THREAD_RECONSTRUCTION_ENABLED` on in production;
  `MULTI_MESSAGE_ESCORT_PARSER_ENABLED` / `BANGLA_SEMANTIC_PARSER_ENABLED`
  off pending a separate Owner-approved checkpoint. See
  `management_decisions.md` → "Phase B" entries and
  `modules/conversation_memory/` for full implementation detail.
