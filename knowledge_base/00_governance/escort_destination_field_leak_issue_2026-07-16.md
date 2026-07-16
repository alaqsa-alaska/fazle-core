---
title: KNOWN ISSUE — Escort destination field contains leaked "Escort Name:" text
owner: Fazle Core Admin
status: open — documented, not fixed
last_verified: 2026-07-16
runtime_index: true
---

# Known Issue — `destination` field leak on admin-direct-created escort programs

**Status: open, documented only. No fix attempted. No KB-first proposal written yet —
this doc exists to hold the evidence until someone decides to pick it up.**

## Where it was found

Surfaced incidentally while reviewing the duplicate-pair report in
[escort_sender_validation_audit_phase2_2026-07-16.md](escort_sender_validation_audit_phase2_2026-07-16.md)
§4 — not something that was specifically searched for, so the 4 known instances below
should be treated as a lower bound, not a full inventory.

## Evidence

| S/N | mother_vessel | lighter_vessel | `destination` (as stored) | program_date | assignment_time |
|---|---|---|---|---|---|
| 900 | MV DUBAI ECO | MV HABIBULLAH | `Escort Name: Sohedul Karim` | 2026-06-25 | 2026-06-25 12:01:22 |
| 901 | MV TRUONG MINH PROSPERITY | MV AL Morium | `Escort Name: Md Helal` | 2026-06-25 | 2026-06-25 12:01:22 |
| 905 | MV DUBAI ECO | MV HABIBULLAH | `Escort Name: Ferdous` | 2026-06-26 | 2026-06-27 05:51:44 |
| 906 | MV TRUONG MINH PROSPERITY | MV AL Morium | `Escort Name: Md Rubel` | 2026-06-26 | 2026-06-27 06:00:29 |

All 4 have `remarks.auto_created = true` — i.e. all went through
`_create_confirmed_from_admin()` in `modules/escort/__init__.py:1517-1662` (the
direct-create path used when an admin's confirmation message has no matching prior
draft to update). The `destination` column, which should hold a place name, instead
holds the literal text `"Escort Name: <name>"` — the escort-name field label and value
appear to have been captured into the wrong column.

## What I checked, and the limit of what I could confirm

I attempted the same reproduce-against-current-parser test used for S/N 777 (see the
Phase 2 doc §2): recover the original admin message from `wbom_whatsapp_messages` and
replay it through the current parsing code in isolation. **I could not locate a
matching row** in `wbom_whatsapp_messages` for any of these 4 in their assignment-time
windows, so I could not run that test. This means:

- **Reproducibility against current code is unconfirmed — not ruled out, not
  confirmed.** Unlike S/N 777 (where I could positively demonstrate the bug is gone),
  this one is genuinely open.
- All 4 known instances have `assignment_time` in the 2026-06-25 – 2026-06-27 window,
  i.e. at or just before the 2026-06-27 hardening commit (`41916b0`) covered in Session
  16 — so it's plausible (not proven) this predates that fix too, same as most of the
  rest of this audit's findings. It's also possible it's unrelated to that commit
  entirely, since that commit's changes were about sender validation and empty-vessel
  guards, not about `_create_confirmed_from_admin()`'s field mapping specifically.

## Recommended next step

Not a fix — a scoped investigation, before any code change:
1. Search a wider time window / different message-log table for the 4 originating admin
   messages (the `wbom_whatsapp_messages` search here was narrow; the messages may be
   logged elsewhere, e.g. `fazle_messages` or `processed_bridge_messages`, or may
   predate this table's retention).
2. Once a real source message is in hand, trace `_create_confirmed_from_admin()`'s
   field-by-field mapping (`modules/escort/__init__.py` around line 1650-1680) against
   it to find exactly where `destination` and the escort-name text cross paths.
3. Only then — KB-first proposal (current behavior / KB reference / gap analysis /
   proposed fix / risk / rollback) before touching the parser, per this repo's standing
   policy.
4. Separately: grep for how many *other* `auto_created=true` rows have a `destination`
   value that looks like `"Escort Name: ..."` rather than a place name, to size the
   blast radius before deciding whether historical data needs correcting too.

## Non-goals for this doc

This is a record of a finding, not an authorization to fix anything. No sign-off has
been requested or given for a code change here.
