# Implementation Plan — Multi-Entry Escort Confirmations, Vessel-Prefix Normalization, No-Draft Fallback (2026-08-01)

**Status, 2026-08-01: Steps 1, 2, 3, 5 shipped and tested (Owner-approved
priority order: 1 → 3 → 2 → 5). Step 4 (daily gap-scan job) deliberately
deferred — Owner instruction: keep the existing scheduled-job set as-is for
now, no new job added today. Step 6 is verification-only (no code).** See
`knowledge_base/04_business_rules/escort_business_rules.md` and
`knowledge_base/05_workflows/escort_source_of_truth_workflow.md` for the
as-shipped behavior; this plan doc is kept for the historical audit trail
and the still-open Step 4.

**Scope, in the Owner's own framing (translated from the original Bengali
instruction, kept alongside for traceability):**

1. A message **from an escort-client to admin's WhatsApp number** is an
   "escort-order."
2. A message **from admin's number to an escort-client's WhatsApp number**
   that contains Mother Vessel, Lighter Vessel, Escort Name, Escort Mobile,
   Date, and Shift is that order's confirmation — admin's message is the
   **final source of truth**.
3. If the system fails to create a draft for some reason, but admin still
   sends an escort-assignment/confirmation message, a new row must still be
   created in the escort-roster/escort-program database (with a duplicate
   check) — a missing draft must never mean the confirmation is silently
   lost. **Superseded by Owner refinement below (see "Decisions confirmed
   by Owner"): implemented instead as a daily gap-scan job that catches the
   missed *order* directly, rather than auto-creating at confirmation-time
   — keeps the 2026-07-19 anti-ambiguity protection fully intact.**
4. One escort order can have **multiple lighters** — multiple lighter
   vessel names, multiple destinations, multiple MT capacities — but always
   **one** mother vessel.
5. One escort confirmation can equally assign **multiple escorts** — one
   mother vessel, but as many lighter/escort pairs as there are lighters —
   and every one of them must reach the escort-roster page's backend DB,
   not just the first.
6. Vessel-name prefixes (`MV` / `M. V.` / `M.v.` / `M V` / `M v`, any
   casing/spacing) must normalize to one canonical form.
7. `Narayanganj` may be written as `N.Gonj` / `Rupshi` — **confirmed
   correct as-is**, not a bug (see Finding 3 below).
8. If a mobile number is short or malformed, the system should ask the
   admin (via a draft) whether to send a reminder for the correct number or
   record what's there — not silently guess or silently drop it.

---

## Audit findings

### Finding 1 — Escort-ORDER side (client → admin) already supports multiple lighters correctly. No fix needed here.

`modules/escort/parse_escort_message()` (`modules/escort/__init__.py:837-879`)
already returns `EscortOrder{mother_vessel: <single>, lighters: [<list>]}` via
four layered parser strategies (`_parse_mobile_grouped_lighters` →
`_parse_labeled_lighters` → `_parse_inline_lighters` →
`_parse_mv_block_lighters`, tried in order until one yields results).
`build_admin_message()` (`:971-1011`) already loops over every lighter and
emits one `"Notun escort order:"` draft block per lighter in a single
combined admin notification. `save_escort_programs()` already saves one
`wbom_escort_programs` row per lighter, with its own inline + cross-channel
dedup (`:1223` onward, `:1179-1219`). Verified against real production data
(program_ids 1026-1028 from the same inbound message earlier this session).
**No change proposed for the order-intake side.**

### Finding 2 — Escort-CONFIRMATION side (admin → client) is single-entry only. Confirmed root cause of the skipped Hridoy/Elim confirmation.

`parse_completed_draft()` (`:1031-1029`, exact field regexes at `:133-165`)
extracts every field with `.search()` — `_CD_MV_RE`, `_CD_LV_RE`,
`_CD_MASTER_MOB_RE`, `_CD_ESCORT_NAME_RE`, `_CD_ESCORT_MOB_RE`,
`_CD_DEST_RE` — never `.finditer()`. Given a confirmation with two numbered
lighter/escort blocks, it silently captures only the **first** block's
fields and drops the second entirely, no error, no log signal.
`handle_admin_escort_completion()` (`:2684-2854`) calls
`parse_completed_draft()` once and processes exactly one program match —
architecturally single-entry end to end.

Confirmed live (2026-08-01) against the real message from this incident:
with the first entry's malformed phone corrected, the parser still only
returned entry #1's fields; entry #2 (a fully valid, complete lighter/escort
pair) was silently dropped.

### Finding 3 — `Rupshi` → `Narayanganj` is a documented, confirmed-correct business rule. Not a bug.

`knowledge_base/04_business_rules/escort_business_rules.md:141` already
documents `Rupshi -> Narayanganj` as an intentional normalization, matching
`_DEST_ALIASES` (`modules/escort/__init__.py:247`) and now re-confirmed
directly by the Owner in this instruction. **No change proposed.** (I had
flagged this as a possible bug in the previous turn before finding this
documented rule — retracted.)

### Finding 4 — Vessel-prefix normalization is incomplete: handles `MV`/`MV.`, not `M.V.`/`M V`/`M.v.`/`M v`. Confirmed root cause of the "MV M.V. ELIM" double-prefix bug.

The strip regex used in the confirmation path (`modules/escort/__init__.py:1063`,
also `:625`, `:633`, `:1853`) is `re.sub(r"^MV\.?\s+", "", mv, flags=re.IGNORECASE)`
— this matches a leading `M` immediately followed by `V` (zero or one
following period), then whitespace. It does **not** match `M.V.` (period
between M and V), `M V` (space between M and V), or `M.v.`/`M v` (mixed
case with either separator). When the raw value is `"M.V. Elim"`, the strip
is a no-op, `mv.upper().startswith("MV")` then evaluates `"M.V. ELIM"` —
which does **not** start with the literal two characters `"MV"` — so the
fallback prepends a second `"MV "`, producing `"MV M.V. ELIM"`. Confirmed
live against the real confirmation message this session.

### Finding 5 — No-draft-exists fallback: KB and code actively disagree. Needs an explicit decision, not a silent pick.

- **`knowledge_base/04_business_rules/escort_business_rules.md:79-84`**
  (last verified 2026-07-05, never updated since) states: *"If all required
  confirmation fields are present: match an existing draft... **Create one
  confirmed row only when no draft exists**. Never create duplicate rows."*
- **The actual code**, since commit for Workstream 2 Stage 2
  (2026-07-19, comment at `modules/escort/__init__.py:2729-2740`)
  deliberately **removed** exactly this behavior: *"this used to call
  `_create_confirmed_from_admin()` directly here... Owner decision: no-match
  must never auto-create — it now queues for admin review instead."* That
  change fixed a real bug (a fuzzy-match miss against an *existing but
  different* draft was silently creating a second, unlinked confirmed row).

These are two Owner-attributed decisions that contradict each other, and the
KB was never updated when the second one shipped.

**RESOLVED by Owner, 2026-08-01: keep the 2026-07-19 behavior exactly as-is
— no-draft-match confirmations never auto-create a program row.** No code
change to `handle_admin_escort_completion()`'s no-match branch. In its
place, a new **daily gap-scan job** (Step 3 below) independently audits the
last 7 days for escort *orders* (client → admin messages) that never
produced a `wbom_escort_programs` row at all, and surfaces those — not
confirmations that failed to match — to the review queue with an admin
WhatsApp reminder. This is a materially safer design than confirmation-time
auto-create: it never risks attaching a confirmation to the wrong draft,
and it catches the actual failure mode (a missed order) directly instead of
inferring it from a downstream confirmation mismatch.

**Refinement — exact master-mobile match must never be "ambiguous":**
Owner instruction, 2026-08-01: if the draft's lighter master-mobile and the
confirmation's lighter master-mobile are the identical number, that alone
proves it's the same order — no fuzzy scoring needed, never routed to
review. Currently this is **not** true: `_score_pending_program_candidates()`
(`:1349-1444`) gives an exact `master_mobile` match only `+5.0`, but
`_PENDING_PROGRAM_MATCH_THRESHOLD` (`:1346`) is `5.5` — so a candidate
matching *only* on master_mobile (no program_date, weak mother/lighter
vessel similarity) currently scores *below* the threshold and is
incorrectly treated as ambiguous. Fix: in `_find_pending_program()`, check
for an exact `master_mobile` match among the scored candidates first: if
exactly one candidate has `master_mobile` in its `matched_fields`, return it
immediately, bypassing the 5.5 threshold entirely (unchanged if zero or if
more than one candidate shares that master mobile — that genuinely is
ambiguous and must still go to review).

### Finding 6 — No admin-reminder-or-accept-as-is decision flow exists for a short/malformed mobile number.

Currently a malformed `escort_mobile`/`master_mobile` (e.g. the real
10-digit `0160230063` from this incident) simply fails the phone regex and
that field comes back empty — `is_completed_escort_draft()`'s completeness
gate then fails outright (today) or, once Finding 2 is fixed, that one
lighter entry in a multi-entry confirmation would be skipped while the
others still process. There is no existing "ask admin what to do" path for
this specific case.

**Owner spec, 2026-08-01, exact wording to implement:**
Notification pushed to admin:
```
Admin REMINDER <message id>
<Name of sender/recipient> — <WhatsApp number>
<complete original message>
Do you think to review this message?
```
Admin's reply: `<message id> Type: APPROVE / EDIT / REJECT` — APPROVE
saves exactly what was parsed (including the short/malformed number as-is)
to the database; REJECT discards it (nothing saved); EDIT is followed by
the **complete corrected message** (not a partial patch), which is
re-parsed through the same confirmation pipeline as a fresh message and
that result is what gets saved.

**Design note:** this is *not* wired onto the existing generic
`APPROVE <id>` / `EDIT <id> <text>` / `REJECT <id>` commands in
`modules/admin_commands/__init__.py` (`_cmd_approve`/`_cmd_edit`/
`_cmd_reject`), because those have a different, hardcoded meaning baked in
for the generic AI-reply-drafts case — `_cmd_edit` (`:674-`) sends the
edited text **to the original message sender as a reply**, which is wrong
here (EDIT here means "here is the corrected confirmation text to re-parse
and save," not "send this text to someone"). This matches the codebase's
own existing precedent: `fazle_payment_drafts` already has its own
dedicated verb set (`APPROVED`/`DREDIT`/`DREJECT`, `:81-83`) distinct from
the generic draft verbs, for exactly this reason — different draft types
need different action semantics. This queue gets its own dedicated intent
(`escort_confirmation_review`) and its own command regexes matching the
Owner's exact `<id> Type: APPROVE|EDIT|REJECT` syntax, reusing the existing
push mechanism (`shared.draft_reply.create_draft_reply()` +
`modules.bridge_poller._notify_admin_bridge()`, shipped 2026-08-01 for the
FPE unmatched-cash queue) but its own handler functions.

---

## Implementation plan

Ordered by dependency; each step is independently testable and revertible.

### Step 1 — Vessel-prefix normalization (Finding 4)

Replace the four call sites of `re.sub(r"^MV\.?\s+", "", ...)`
(`:625, :633, :1063, :1853`) with one shared helper:

```python
_VESSEL_PREFIX_RE = re.compile(r"^\s*M\.?\s*V\.?\s+", re.IGNORECASE)

def _strip_vessel_prefix(name: str) -> str:
    """Strip a leading MV/M.V./M.v./M V/M v label, any casing/period/
    spacing combination, so it can be re-applied exactly once canonically."""
    return _VESSEL_PREFIX_RE.sub("", name or "").strip()
```
Then everywhere that currently does the strip-then-conditionally-re-add
dance, call `_strip_vessel_prefix()` first, so the "does it already start
with MV" check never sees a residual `M.V.`/`M V` that it doesn't
recognize. Lowest-risk step, single shared regex, no schema/behavior change
beyond fixing the double-prefix artifact.

### Step 2 — Multi-entry confirmation parsing (Finding 2)

Add a new function, `parse_completed_draft_entries(text) -> list[CompletedDraft]`,
alongside (not replacing) the existing `parse_completed_draft()`:
- Split the text into blocks on a numbered-line boundary (`^\s*\d+\.\s*`,
  matching the real format `"1. Lighter Vessel: ..."` / `"2. Lighter
  Vessel: ..."`) when more than one such marker is present; otherwise treat
  the whole text as one block (preserves every existing single-entry
  format/test unchanged).
- Extract Mother Vessel and the company-name/date/shift **once**, from the
  whole text (per Owner's own model: one mother vessel per confirmation) —
  reusing the existing single-value regexes for these fields.
- Extract Lighter Vessel / Master Mobile / Destination / Escort Name /
  Escort Mobile **per block**, reusing the exact same field regexes
  (`_CD_LV_RE`, `_CD_MASTER_MOB_RE`, `_CD_DEST_RE`,
  `_CD_ESCORT_NAME_RE`, `_CD_ESCORT_MOB_RE`) against each block's text
  instead of the whole message.
- Return a list of `CompletedDraft` dicts, one per block, each carrying the
  shared mother_vessel/date/shift plus its own per-block fields.

`is_completed_escort_draft()` changes its completeness check to: company
signature present, mother vessel present, **and at least one entry** with
lighter_vessel + escort_name + escort_mobile + date present (an entry
missing only its own escort_mobile is *not* a reason to reject the whole
message — see Step 4).

`handle_admin_escort_completion()` changes from "parse once, resolve one
program" to "parse into entries, loop, resolve each program
independently" — every per-entry step it already does
(`_find_pending_program` → `_update_program_confirmed` →
`_sync_roster_after_confirm` → reconcile → build final slip → notify
client) runs once per entry, unchanged in itself. One combined admin-facing
confirmation reply summarizes all entries (matched, auto-created, or queued
for review — see Step 3) instead of one program's result.

**Existing single-entry tests must keep passing unchanged** — the single-
block code path is exercised identically to today; multi-entry is
additive.

### Step 3 — Exact master-mobile match bypasses fuzzy scoring (refinement to Finding 5)

In `_find_pending_program()` (`:1447-1476`), after getting `scored` from
`_score_pending_program_candidates()`: before the threshold check, look for
candidates whose `matched_fields` includes `"master_mobile"` from an exact
match (need to also track *which* field matched exactly in the scored dict,
not just the aggregate score — a small addition to
`_score_pending_program_candidates()`'s per-candidate record). If exactly
one such candidate exists, return it immediately — it's proven, not scored.
If more than one existing draft shares the same master mobile (rare but
possible — e.g. the same master reused across two different bookings),
that genuinely is ambiguous and falls through to the existing threshold
logic unchanged.

### Step 4 — Daily gap-scan job for missed orders (replaces the old Step 3 auto-create proposal, per Owner decision)

New scheduled job, `job_escort_order_gap_scan()`, modeled on the existing
`job_stale_escort_reminder()` pattern (`modules/scheduler/__init__.py:257-291`)
and reusing the FPE engine's own `gap_scan.py` concept (comparing "messages
that look like X" against "rows that resulted from X"):

1. **Prerequisite, small additive change**: thread `message_id` through
   `handle_escort_client_message()` → `save_escort_programs()` (both
   already accept/could easily accept an optional param — `message_id` is
   already available at all 3 call sites in `message_router.py:448,650,816`,
   just not currently passed) and store it as `remarks.source_message_ref`
   alongside the `sender_phone`/`source_bridge` fields already stored
   there today. Purely additive — no schema change, `remarks` is already a
   free-form JSON text column.
2. Daily (proposed 08:00, configurable via `ESCORT_GAP_SCAN_HOUR` matching
   the `DAILY_DIGEST_HOUR` pattern), scan `wbom_whatsapp_messages` for the
   last 7 days: inbound, sender in `ESCORT_CLIENT_PHONES`, where
   `is_order_complete(message_body)` is true (reusing the existing,
   already-shipped completeness gate — no new parsing logic).
3. For each such message, check whether any `wbom_escort_programs` row's
   `remarks->>'source_message_ref'` matches that message's id. If none —
   confirmed missed order.
4. For every missed order found: insert into the review queue (reusing
   `wbom_unmatched_confirmations`, migration 052 — same table the
   confirmation-side no-match path already uses, so there's one queue an
   admin checks, not two) and push one WhatsApp reminder per missed order
   via `_notify_admin_bridge()` (same mechanism as Step 5 below), so it's
   never silent/pull-only.
5. Idempotent across days: a message already reviewed/resolved once (or
   already flagged) is not re-flagged on the next day's run — tracked the
   same way `job_stale_escort_reminder()` already dedupes, via
   `fazle_reminders_sent` (new topic `'missed-escort-order'`).

### Step 5 — Admin REMINDER / APPROVE / EDIT / REJECT flow for a short/invalid mobile (Finding 6, exact Owner spec)

When a per-entry parse (Step 2) yields a lighter/escort block with every
field present **except** a validly-formatted `master_mobile` or
`escort_mobile` (i.e. digits were present but failed BD phone validation —
distinguishable from the field being absent entirely, which stays the
existing "ignore as incomplete" case per business rules `:74-77`), create
one `fazle_draft_replies` row (`intent='escort_confirmation_review'`) via
`shared.draft_reply.create_draft_reply()`, body text exactly:

```
Admin REMINDER <draft_id>
<sender/recipient name> — <WhatsApp number>
<complete original confirmation message>
Do you think to review this message?
```

pushed immediately via `_notify_admin_bridge()` (same mechanism as Step 4).
New, **dedicated** command parser in `modules/admin_commands/__init__.py`
(not the generic `APPROVE <id>`/`EDIT <id> <text>`/`REJECT <id>`, whose
`_cmd_edit` sends the edited text *to the original sender* — wrong meaning
here; mirrors the existing `fazle_payment_drafts`-specific verb-set
precedent, `:81-83`):

- `_ESCORT_REVIEW_RE = re.compile(r"^(\d+)\s+type\s*:\s*(approve|edit|reject)(?:\s+(.+))?$", re.IGNORECASE | re.DOTALL)`
- **APPROVE** → save the entry exactly as originally parsed (short/malformed
  number included as-is) via the Step 2/Step 3 pipeline.
- **REJECT** → mark draft `rejected`, nothing saved.
- **EDIT** → the text after `Type: EDIT` must be the complete corrected
  confirmation message (not a partial patch) — re-run it through
  `parse_completed_draft_entries()` / `handle_admin_escort_completion()`
  as a fresh confirmation, save that result.

The rest of that message's entries (other lighters/escorts in the same
multi-entry confirmation) proceed normally through Step 2 regardless — one
bad phone number never blocks the others.

### Step 6 — Escort-roster frontend verification (no code change — verification only)

`_sync_roster_after_confirm(program_id)` (`:1623-1685`) already syncs one
`wbom_escort_programs` row to `escort_roster_entries` by `program_id`; once
Step 2 calls it once per matched entry, the roster page's existing
`/api/escort-roster` read path needs no changes. Manual smoke test after
Steps 1-5 ship.

---

## Decisions confirmed by Owner (2026-08-01)

1. **No-draft-exists fallback**: keep 2026-07-19 behavior — never
   auto-create from a confirmation with no matching draft. Replaced by the
   Step 4 daily gap-scan job instead (addresses missed *orders* directly,
   not confirmation-time ambiguity).
2. **Exact master-mobile match is proof, not a fuzzy signal** — Step 3.
3. **Reminder flow**: exact `Admin REMINDER <id>` / `<id> Type:
   APPROVE|EDIT|REJECT` wording, dedicated command handler — Step 5.

## Open item

`escort_business_rules.md:79-84` still documents the pre-2026-07-19
"create one confirmed row when no draft exists" rule, which is now
confirmed permanently false. Will correct this alongside the code change
(KB-first policy) rather than leave the contradiction in place — no
decision needed, just noting it'll be part of the same commit.

## Testing plan

- New unit tests for `parse_completed_draft_entries()`: the exact real
  two-lighter confirmation (both with and without the phone typo), a
  single-entry message (regression — must still work), a three-entry
  message, an entry with a missing escort_mobile alongside a complete
  entry (Step 5 path).
- New unit tests for the vessel-prefix helper: all five stated formats,
  plus regression against every existing `MV`/`MV.` test.
- New unit tests for Step 3's exact-master-mobile-match bypass: single
  match (bypasses threshold), multiple matches (still ambiguous, falls
  through), no match (unchanged fuzzy path).
- New unit tests for the Step 4 gap-scan job: a message with a
  corresponding program (`source_message_ref` present, no flag), a message
  with none (flagged once), a message already flagged yesterday (not
  re-flagged today).
- New unit tests for the Step 5 REMINDER/APPROVE/EDIT/REJECT parser and
  handlers.
- Full existing `test_escort_client_*`, `test_escort_source_of_truth*`
  suites re-run for regressions, same rigor as the 2026-08-01
  identity/company-name fix.

## Rollout order

Steps 1, 2, 3 are independent and can ship together first. Step 4 (gap
scan) depends on the small `message_id`-threading prerequisite but is
otherwise independent of Step 5. Step 6 is verification-only, done last.
