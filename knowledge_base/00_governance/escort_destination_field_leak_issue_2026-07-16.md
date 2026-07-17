---
title: RESOLVED — Escort destination field leak ("Escort Name:" text)
owner: Fazle Core Admin
status: ROOT CAUSE CONFIRMED AND FIXED — 2026-07-17
last_verified: 2026-07-17
runtime_index: true
---

# Escort `destination` field leak — root cause confirmed and fixed (2026-07-17)

**Status: fixed.** Originally opened 2026-07-16 as "documented, not fixed,
reproducibility unconfirmed." Root cause was confirmed on 2026-07-17
(`docs/AUDIT_REPORT_2026-07-17.md` Section 1C) by locating the actual source
WhatsApp messages and reproducing the bug with a standalone regex test, and
the fix was applied the same day (`management_decisions.md` Session 19,
Step 3).

## Root cause

`_CD_DEST_RE` in `modules/escort/__init__.py` (used inside `parse_completed_draft()`,
consumed by both `_create_confirmed_from_admin()` — new-row insert — and
`_update_program_confirmed()` — merge into an existing historical row):

```python
_CD_DEST_RE = re.compile(
    r"destination\s*:\s*(.+?)(?=\s*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
```

`\s` matches newlines. When an admin leaves the `Destination:` line blank,
`\s*` immediately after the colon consumes the trailing space **and the
newline**, spilling onto the next line. The non-greedy `(.+?)` then has
nothing to stop it until *that* line's own end, capturing the entire next
line — in every known case, `"Escort Name: <name>"` — instead of an empty
value.

Confirmed with a direct regex test against the real message text (message_id
10678 in `wbom_whatsapp_messages`, the source of program_id 900):
```python
>>> _CD_DEST_RE.search("...Destination: \nEscort Name: Sohedul Karim\n...").group(1)
'Escort Name: Sohedul Karim'
```
This exactly reproduces the corrupted value stored in production. The
per-line label extractor tried first (`destination_label`, anchored per-line)
correctly returns `None` for a blank line — only the fallback,
whole-text-search regex has the cross-line bug.

## Evidence — blast radius corrected from 4 rows to 6

The 2026-07-16 version of this doc listed 4 rows and could not locate any
source message. Both were incomplete — the messages exist, and there are 2
more affected rows:

| program_id | mother_vessel | lighter_vessel | `destination` (before fix) | program_date | write path |
|---|---|---|---|---|---|
| 900 | MV DUBAI ECO | MV HABIBULLAH | `Escort Name: Sohedul Karim` | 2026-06-25 | `_create_confirmed_from_admin` (auto_created=true) |
| 901 | MV TRUONG MINH PROSPERITY | MV AL Morium | `Escort Name: Md Helal` | 2026-06-25 | `_create_confirmed_from_admin` (auto_created=true) |
| **902** | MV TRUONG MINH PROSPERITY | ABUL BASHAR | `Escort Name: Rejaul korim` | 2026-06-25 | `_create_confirmed_from_admin` (auto_created=true) — **previously uncounted** |
| 905 | MV DUBAI ECO | MV HABIBULLAH | `Escort Name: Ferdous` | 2026-06-26 | `_create_confirmed_from_admin` (auto_created=true) |
| 906 | MV TRUONG MINH PROSPERITY | MV AL Morium | `Escort Name: Md Rubel` | 2026-06-26 | `_create_confirmed_from_admin` (auto_created=true) |
| **938** | MV HORDA | MV Pavel-6 | `Escort Name: Anik Nath` | 2026-07-03 | `_update_program_confirmed` via `sqlite_backfill`/`bridge_confirmation` merge — **previously uncounted, not `auto_created`, different write path, same root cause** |

Source messages found in `wbom_whatsapp_messages` for 900, 901, 906, and 938
all show the identical shape — an admin confirmation message with the
`Destination:` line left blank, immediately followed by `Escort Name: <name>`
on the next line. 902's specific source message was not individually located
in the time available, but the identical corruption pattern and identical
write path (`_create_confirmed_from_admin`) make the same root cause
overwhelmingly likely.

`modules/escort_roster/history_sync.py` has a superficially similar regex
(`_DEST_RE`) with the same theoretical cross-line weakness, but it is **not**
affected in practice — its `_extract_destination()` only ever returns a
matched canonical destination keyword (from `DESTINATIONS`) or `None`; it
never returns the raw captured text, so a leaked "Escort Name: ..." value is
silently discarded rather than stored.

## Fix applied (2026-07-17)

One-line change to `_CD_DEST_RE` — replaced `\s*` with `[ \t]*` around the
colon and in the lookahead, so the pattern can never cross a newline:
```python
_CD_DEST_RE = re.compile(
    r"destination[ \t]*:[ \t]*(.+?)(?=[ \t]*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
```
Verified with 4 standalone regex tests (blank destination → empty, real
destination → correct value, blank-with-trailing-spaces → empty, and the
exact real message that produced row 938 → empty) — all pass. Since both
downstream consumers (`_create_confirmed_from_admin`, `_update_program_confirmed`)
read from the same single parse point, one fix covers both write paths.

**Not yet done:** full HTTP/live-service verification is pending a session
with sudo access to restart `fazle-core.service` (same blocker as Session 19
Steps 1-2) — the fix is correct by direct regex test and code trace, but
hasn't been exercised end-to-end through the running app yet.

## Data backfill applied (2026-07-17)

All 6 known-corrupted rows (900, 901, 902, 905, 906, 938) had `destination`
set to `NULL` (the correct value, since the admin genuinely left the field
blank — this is a correction, not data loss) via a direct, audited `UPDATE`,
each row tagged `remarks.destination_leak_fix = "fixed_2026-07-17_step3_audit"`
for traceability. Verified before (`SELECT` confirmed all 6 still corrupted)
and after (`SELECT` confirmed all 6 now `NULL` with the fix marker) the
`UPDATE`, which affected exactly 6 rows as expected.

## Non-goals, still open

- No wider blast-radius sweep was run for *other* `auto_created=true` or
  `sqlite_backfill`-sourced rows beyond the 6 already known — the original
  doc's "recommended next step #4" (grep for any other row with a
  destination value matching `Escort Name: ...`) was done as part of this
  fix (the live query in Step 3 searched the whole table by pattern, not
  just the original 4 IDs) and found exactly these 6, so this is now closed,
  not still open.
- 902's exact source WhatsApp message was not individually located this
  session — low priority, since the fix and data correction don't depend on
  it.
