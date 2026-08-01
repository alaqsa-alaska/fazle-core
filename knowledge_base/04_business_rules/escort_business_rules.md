---
title: Escort Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-08-01
runtime_index: true
---

# Escort Business Rules

## Message Direction Rules

Production escort-order parsing is direction-gated first. Parsing does not begin until the sender and recipient match one of the approved flows below.

### Client -> Bridge

Allowed bridge numbers:
- `8801880446111`
- `8801958122300`

Allowed client numbers:
- `ESCORT_CLIENT_PHONES` from `.env`

Meaning:
- The client is requesting escort service.
- This message may create a draft only.
- It is never the source of truth.

Minimum required fields:
- Mother Vessel
- Lighter Vessel
- Master Mobile

If any minimum field cannot be identified with reasonable confidence:
- Ignore the message
- Create no draft
- Create no roster side effect

> **Still accurate for the production single-message parser** (this rule
> governs real draft creation and is unchanged). As of 2026-07-19, an
> authorized-but-incomplete message is *additionally* held in the Phase B
> Conversation Memory Engine (shadow mode, non-authoritative) instead of
> being discarded with no trace — see "Multi-Message Intake (Phase B,
> Shadow Mode)" below. It still creates no draft and no roster side
> effect; only the deterministic single-message parser above may do that,
> and only while its own gate is satisfied by one message.

If the minimum fields are present:
- Parse with best effort
- Create one draft order only
- Keep status unconfirmed / waiting confirmation
- Never auto-confirm from the client message

### Bridge -> Escort Client

Allowed bridge numbers:
- `8801880446111`
- `8801958122300`

Allowed recipients:
- `ESCORT_CLIENT_PHONES` from `.env`

Meaning:
- Official escort confirmation
- This is the only source of truth for final escort assignment data

Required confirmation fields:
- Mother Vessel
- Lighter Vessel
- Escort Name
- Escort Mobile
- Start Date (Shift D/N also expected when present)

**Multi-entry confirmations (2026-08-01):** one confirmation message has
exactly one Mother Vessel, but may assign multiple escorts across multiple
numbered lighter-vessel blocks ("1. Lighter Vessel: ...", "2. Lighter
Vessel: ...") — one escort per lighter. Every numbered block is its own
independent entry: matched, updated, and synced to the roster separately.
One entry failing (see below) never blocks the others in the same message.
`modules/escort/parse_completed_draft_entries()` is the parser; a message
with no numbered blocks (the normal single-lighter case) is unaffected.

Vessel-name prefixes — `MV`, `M.V.`, `M.v.`, `M V`, `M v` (any casing,
periods, spacing) — all normalize to the same canonical form before
matching or storage.

If a required field is missing entirely for a given entry:
- Ignore that entry as an escort confirmation (unaffected — unchanged rule)
- Do not update a draft for that entry, do not create a confirmed row

**If a mobile number (Master Mobile or Escort Mobile) is present but
malformed** (digits found, but not a valid 11-digit BD number) for a given
entry — as opposed to genuinely absent — that entry is **not** silently
ignored and not silently guessed: an "Admin REMINDER" is pushed to
`ADMIN_NUMBERS` immediately with the entry's full text, and the admin
replies `<id> Type: APPROVE` (save exactly as sent), `<id> Type: REJECT`
(discard), or `<id> Type: EDIT <complete corrected message>` (re-parsed and
saved). See `05_workflows/escort_source_of_truth_workflow.md` "Enforcement"
section for the exact mechanism.

If all required fields are present for a given entry:
- Match an existing draft by client phone, master mobile, mother vessel, and start date
- An **exact match on the lighter's master mobile number alone** is treated
  as proof of the same order (not just a fuzzy-scoring signal) when the
  normal match score is inconclusive — but only as a fallback (the normal
  scoring path always runs first) and only among drafts from the last 7
  days; two or more drafts sharing that same master mobile stays genuinely
  ambiguous and is not auto-resolved.
- Update the existing row when matched; overwrite incorrect draft values
  with confirmation values
- **When no draft matches at all: queue for admin review, never
  auto-create a confirmed row from the confirmation alone** (Owner
  decision, reaffirmed 2026-08-01 — see "Source Of Truth Policy" below for
  why). A missed *order* that never produced a draft in the first place is
  instead caught by a separate daily gap-scan job, not by auto-creating
  here.
- Never create duplicate rows

## Source Of Truth Policy

Not source of truth:
- Client order text
- AI parser guess
- Draft row

Source of truth:
- Bridge -> Escort Client confirmation message

Conflict rule:
- If draft data conflicts with confirmation data, confirmation always wins

**History note (this doc was stale on this exact point from 2026-07-19 to
2026-08-01):** this doc previously said "create one confirmed row only
when no draft exists," matching the *original* design. Commit for
Workstream 2 Stage 2 (2026-07-19) deliberately removed that behavior after
finding it silently created a second, unlinked confirmed row whenever a
confirmation's fuzzy match missed an *existing-but-different* draft — a
real, confirmed bug, not a hypothetical one. This doc was never updated to
match at the time. Reconciled 2026-08-01: the no-draft-match case now
always queues for review (2026-07-19's fix, kept), and a *separate* daily
gap-scan job (not confirmation-time auto-create) catches genuinely missed
orders instead — see `05_workflows/escort_source_of_truth_workflow.md`.

## Parsing Rules

Primary signal:
- Master Mobile Number

Secondary signals:
- Mother vessel block
- Lighter vessel block
- Importer
- Cargo
- Destination
- Capacity

This is a list of signal types, not the order they appear in a message.
Destination is recognized by content (a Bangladeshi place name, full or
short-hand), not position. Capacity commonly trails the lighter vessel name.
Importer/cargo commonly sits after the mother vessel name or before the
lighter vessel name, and may be wrapped in WhatsApp `*bold*` markup — see
`../06_developer_system/escort_order_parser.md` Parser Philosophy for the
full positional notes.

Rules:
- Every valid Bangladesh mobile belongs to one lighter block
- The vessel nearest that mobile is the lighter vessel
- Destination and capacity nearest that mobile belong to that lighter
- A block containing a mobile number cannot be the mother vessel
- `MV`, `M.V.`, `Mv`, and similar labels are helper signals only
- Those labels may appear before both mother vessels and lighter vessels
- Never classify mother vessel from `MV` label alone
- `Mother Vessel:` label, when present, is the highest-priority mother-vessel source
- `Lighter Vessel:` / `Lighter Name:` label, when present, is the highest-priority lighter-vessel source
- In compact multiline formats, an `MV` line with nearby `MT/M.T` capacity and mobile belongs to lighter
- A trailing parenthesized `MV` without mobile (for example `(MV HORDA)`) is a mother-vessel candidate

Supported parsing modes:
- Block-based parsing has higher priority
- Line-based parsing remains supported as fallback

## Normalization Rules

Destination:
- `Local` and `CTG` -> `Chattogram`
- `Rupshi` -> `Narayanganj`
- `N.Bari` -> `Nagarbari`

Capacity:
- `700 m.t`
- `700 MT`
- `700mt`
- all normalize to `700 MT`

## Validation And Duplicate Prevention

Before saving any escort order row, the parser must have:
- Mother Vessel
- Lighter Vessel
- Master Mobile

If any of the three are missing:
- Discard the message
- Do not save draft data

> As of 2026-07-19: "discard" means the deterministic single-message
> parser takes no action on that one message, as always. It does not mean
> the message leaves no trace anywhere in the system — see "Multi-Message
> Intake (Phase B, Shadow Mode)" below for what else may observe it.

Duplicate-prevention match priority:
1. Master Mobile
2. Mother Vessel
3. Client Phone
4. Start Date

## Roster Synchronization Rule

`wbom_escort_programs` is the operational parent.

`escort_roster_entries` is the roster projection.

When a program becomes confirmed by source-of-truth confirmation:
- Program status becomes `confirmed`
- Roster status must not remain `draft`
- Roster status is synced to `confirmed`
- UI must show a clean final state without program/roster conflict

## Escort Client Configuration Rule

`ESCORT_CLIENT_PHONES` is the whitelist for escort-order directions.

Rules:
- Numbers are configured in `.env`
- Add and remove operations must normalize Bangladesh mobile format
- Invalid numbers are rejected
- Duplicate numbers are not re-added
- Updating the list clears the cached settings object immediately
- No service restart is required for whitelist changes

## Browser And Admin Output Rules

- Generated admin notification must include only one Automated Reply footer
- Search and export endpoints must qualify joined SQL columns with table aliases
- The roster UI must not send empty or malformed query parameters
- Optional missing fields should render as `—`, not throw runtime errors

## এসকর্ট পেমেন্ট ক্যালকুলেশন ফর্মুলা

### ডিফল্ট ডেইলি রেট
`DEFAULT_DAILY_RATE = ৳400/দিন` (PAY-01: ৳12,000 ÷ 30)

### ডেইলি রেট ক্যালকুলেশন
```
daily_rate = basic_salary ÷ 30
```
যদি `basic_salary` না থাকে বা শূন্য হয়: `daily_rate = 400` (৳400/দিন ব্যবহার হয়)

### গ্রস পেমেন্ট
```
gross_amount = round(duty_days × daily_rate, 2)
```

### নেট দেয় (ফ্লোর: শূন্য)
```
net_payable = max(gross_amount − food_bill − conveyance − advances, 0)
```

### গুরুত্বপূর্ণ নিয়ম
- নেট পেমেন্ট কখনো ০-এর নিচে যাবে না (`max(..., 0)` দিয়ে নিশ্চিত)
- খাদ্য বিল কর্তন (`food_bill`): প্রোগ্রাম টেবিল থেকে, ডিফল্ট ০
- যাতায়াত কর্তন (`conveyance`): প্রোগ্রাম টেবিল থেকে, ডিফল্ট ০
- অ্যাডভান্স কর্তন: ওই এসকর্ট প্রোগ্রাম ও চলতি মাসের `final` স্ট্যাটাস অ্যাডভান্স মাত্র
- ডিউটি দিন: রোস্টারে দিন গণনা; না থাকলে প্রোগ্রামের তারিখের পার্থক্য + 1

## Multi-Message Intake (Phase B, Shadow Mode) — added 2026-07-19

A second, entirely separate layer sits alongside the deterministic
single-message parser above: `modules/conversation_memory` (Phase B,
Stages 1-3). It does not replace or relax any rule above — every rule in
this document still governs the *only* code path that may create a real
`wbom_escort_programs` row. This layer exists to stop losing information
when a genuine order legitimately spans more than one WhatsApp message
(e.g. lighter/mobile in one message, mother vessel named in a
follow-up), which the single-message parser above has no way to see.

**Core rules:**

- **Multi-message spanning.** An authorized client's messages within a
  TTL window (`CONVERSATION_TTL_ESCORT_MINUTES`, default 30) may be
  accumulated into one "conversation thread," field by field, instead of
  each message being judged in isolation.
- **Authorized-incomplete is held, not discarded.** A message from an
  authorized escort client (`ESCORT_CLIENT_PHONES`, correct bridge
  direction) that is individually incomplete is retained in the thread
  (as a *shadow candidate*) rather than vanishing with no trace, once
  Stage 2 (`MULTI_MESSAGE_ESCORT_PARSER_ENABLED`) is on.
- **Only a combined-complete thread ever qualifies as a candidate.**
  Completeness is still exactly Mother Vessel + Lighter Vessel + Master
  Mobile (Part 6.4) — spanning messages changes *where* those three
  values may come from, never *what* is required.
- **Conversation memory grants no authority.** Accumulating messages into
  a thread never creates a draft, program, roster entry, or client/admin
  reply by itself — see "Shadow-Mode Rollout" below. The authorization
  gates above (`ESCORT_CLIENT_PHONES`, bridge direction) are re-checked
  independently inside this layer too, not assumed from the caller.
- **System-generated messages never contribute.** Any inbound text
  carrying the `_AUTOMATED_SUFFIX_ANCHOR` marker (`app/bridge.py`) or
  matching `SYSTEM_NOTIFICATION_TITLE_PREFIXES` is refused before it can
  affect a thread — the same class of incident this fixed for the
  outgoing scan (`escort_source_of_truth_workflow.md`, Root Cause 7).
- **Interleaving protection.** The same client phone can have more than
  one order in flight. Two messages with *different* lighter vessels or
  master mobiles are treated as two separate candidate threads, never
  silently merged into one order. A message that can't be confidently
  attributed to any open thread is held as `needs_review`, never guessed.
- **Conflicts are flagged, not silently overwritten.** A later message
  giving a different high-confidence value for a field already set stays
  a recorded conflict on the thread; the original value is preserved.
  Values that are the same after normalization (digit forms, phone
  formats, whitespace/case) are never treated as conflicts.
- **TTL / expiry.** A thread past its TTL is marked `expired`, not
  deleted, and a later related message opens a fresh thread rather than
  silently reopening the old one.
- **Bangla / mixed-language labels.** Stage 3
  (`BANGLA_SEMANTIC_PARSER_ENABLED`) adds label-aware extraction for
  Bangla vessel/lighter/mobile/destination/trip/capacity labels (see
  `escort_order_parser.md` for the full label list and normalization
  rules) — fixing a real gap where the old single-message parser read a
  Bangla label as literal vessel-name text.
- **Provenance and idempotency.** Every accumulated field records which
  message set it, at what confidence, and by what extraction method.
  Replaying the same `message_id` is a guaranteed no-op.

**Shadow-mode rollout:** `CONVERSATION_MEMORY_ENABLED` and
`WHATSAPP_THREAD_RECONSTRUCTION_ENABLED` are on in production
(2026-07-19 checkpoint). `MULTI_MESSAGE_ESCORT_PARSER_ENABLED` and
`BANGLA_SEMANTIC_PARSER_ENABLED` remain off pending a separate
Owner-approved checkpoint. In every combination of these flags, this
layer never writes to `wbom_escort_programs` or any other business
table — see `modules/conversation_memory/thread_reconstruction.py` and
`management_decisions.md` → "Phase B" entries for full detail.

## Cross References

- `../05_workflows/escort_workflow.md`
- `../05_workflows/escort_source_of_truth_workflow.md`
- `../06_developer_system/escort_order_parser.md`
- `../06_developer_system/escort_roster_system.md`
