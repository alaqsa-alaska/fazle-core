# Proposal — Silent-Failure Gaps in Admin Notification / Instruction Parsing (2026-08-01)

**Status: DRAFT PROPOSAL — no code, schema, or config changed.** Produced per
this repo's KB-First policy (read KB → read production → gap analysis →
formal proposal → **stop for management approval**). Follows the 6-part
structure that policy requires for every implementation proposal.

**Origin:** live read-only investigation (2026-08-01) into three reported
symptoms — (1) an escort-order message didn't produce a draft the admin ever
saw, (2) escort-client/admin conversations weren't landing new confirmed
rows in `escort_roster_entries`, (3) an admin→accountant conversation didn't
produce a new `fpe_cash_transactions` row. Full findings already reported to
Owner in chat; this document is the formal proposal for fixing them.

**Cross-cutting root cause:** all three symptoms come from the same class of
bug — a step that silently does nothing observable when its input doesn't
match what it expected, with no push notification, no log-visible alert, and
(for drafts) a 24-hour auto-delete that erases the only trace. The service,
DB, and all three WhatsApp bridges are otherwise healthy; this is not an
outage, it's a silent-failure-mode gap in the review/instruction layer.
Gap A and Gap B share one root cause (the admin review queue is pull-only
and unreviewed); Gap C is a separate, unrelated parser gap.

---

## GAP A — Escort-order / generic drafts never pushed to admin

### A1. Current production behavior

`fazle_draft_replies` rows are created correctly by two different code
paths, but only one of them ever sends anything:

- **Verified/complete escort orders** — `modules.escort.handle_escort_client_message()`
  (`modules/escort/__init__.py:2159-2240`) returns an `admin_note` dict that
  `message_router.process_message()`'s caller in `bridge_poller` forwards to
  `_notify_admin_bridge()` (`modules/bridge_poller/__init__.py:1744-1771`),
  which enqueues onto `fazle_outbound_queue` (purpose `internal-notification`).
  **Confirmed working** — verified live: several `internal-notification` rows
  sent successfully to `8801880446111` within hours of this audit.
- **Everything else that gets drafted** (incomplete orders — missing escort
  name/mobile, unmatched senders, generic AI-reply suppressions, financial-
  intent forced drafts, safety-block drafts, etc.) goes through
  `bridge_poller._save_draft()` (`modules/bridge_poller/__init__.py:1416-1516`)
  or `app/main.py:_save_draft()` (`app/main.py:2211-2272`). **Neither
  function calls any send/enqueue function.** They only `INSERT INTO
  fazle_draft_replies`.
- The only two ways a pending draft ever becomes visible to a human are (a)
  the admin texting `STATUS`/`DRAFTS` (`modules/admin_commands/__init__.py`,
  `modules/message_router/__init__.py:554`) or (b) the web dashboard
  (`GET /api/drafts`, `modules/drafts/routes.py:66-80`). Both are pull-only.
- `job_combined_draft_cleanup` (`app/main.py:393-455`, hourly) and
  `job_draft_ttl_cleanup` (`app/main.py:656-668`) hard-`DELETE` pending
  drafts after `DRAFT_TTL_HOURS` (currently `24`, confirmed live in the
  running process). A draft nobody pulled within 24h is gone with no
  audit trail beyond the delete-count log line.
- **Empirical confirmation this lane is dead in practice:** `SELECT`
  against production `fazle_draft_replies` shows **zero rows with
  `status='sent'` (i.e. admin-approved) since 2026-06-09** — the entire
  approve/edit/reject flow has gone unused for ~7 weeks even though drafts
  keep being created daily. 21 drafts were pending, unreviewed, at time of
  audit; the oldest reviewable one was from the previous day.

### A2. Knowledge Base reference

`knowledge_base/05_workflows/whatsapp_reply_workflow.md` §14 ("Escort
exception"), lines ~394-399:

> "...a draft (not a final row) is created **and sent to the admin number
> for review** — same as any other escort order."

This is the only place the KB describes destination behavior for these
drafts, and it says "sent," not "queued for the admin to look up." The KB
does not separately document a pull-only/no-push design for the generic
`_save_draft()` catch-all path — there is no KB article describing the
current pull-only behavior as intentional.

### A3. Gap analysis

| | KB says | Production does |
|---|---|---|
| Verified/complete escort order | draft created and sent to admin | ✅ matches — sent via outbound queue |
| Incomplete order / generic draft | (undocumented — implied "same as any other escort order" by proximity) | ❌ created, never sent; pull-only; auto-deleted after 24h |

The gap is not "the code is broken" — every function does exactly what it's
written to do, and no exception is thrown anywhere in this path (checked
`logs/fazle-core-error.log`, no relevant tracebacks). The gap is a **missing
push step** for the majority of drafts, undocumented as a deliberate design
choice, combined with an auto-delete that destroys the evidence. The
practical effect matches the reported symptom exactly: an escort order with
one blank field is exactly as invisible to the admin as if it had never
been sent at all.

### A4. Proposed implementation

Reuse the existing, already-working push pattern instead of inventing a new
one — this repo already has two proven templates:

1. **`modules.reports.job_daily_admin_digest()`** (`modules/reports/__init__.py:533-558`,
   registered `modules/scheduler/__init__.py:567-568`, scheduled via
   `CronTrigger(hour=DAILY_DIGEST_HOUR)` at `modules/scheduler/__init__.py:707-713`)
   — proven daily summary push to `ADMIN_NUMBERS` via `outbound.enqueue()`.
2. **`job_stale_escort_reminder()`** (`modules/scheduler/__init__.py:257-291`)
   — proven per-item reminder pattern: one message per row, deduped via
   `fazle_reminders_sent` (`ON CONFLICT (reminder_key) DO NOTHING`), sent via
   `outbound.enqueue()`.

Proposed change, in two parts:

- **A4a — Immediate per-draft push (real-time), for `_save_draft()` and
  `app/main.py:_save_draft()` only when `draft_only=True`/enriched (i.e. the
  5-part admin-review format).** After the `INSERT`, call
  `modules.bridge_poller._notify_admin_bridge()` with the same draft text,
  `purpose="draft-review-pending"`, `admin_phone=settings.admin_bridge2_number
  or settings.admin_bridge1_number`. This is the smallest possible change —
  one new call site, reusing an existing, already-tested send function — no
  new table, no new job.
- **A4b — Safety net: a new scheduled job, `job_draft_review_reminder()`,
  cloned from `job_stale_escort_reminder()`'s structure**, running every 30
  minutes (`IntervalTrigger(minutes=30)`), that finds
  `fazle_draft_replies` rows with `status='pending'` older than e.g. 2 hours
  and not yet reminded (new `fazle_reminders_sent` topic
  `'draft-pending'`), and sends **one batched summary message** ("N drafts
  waiting, oldest from HH:MM, reply DRAFTS to review") rather than one
  message per draft, to avoid spamming admin's WhatsApp on a busy day. This
  catches anything A4a might miss (e.g. a future new draft-creation call
  site someone forgets to wire up) and gives a second chance before the 24h
  TTL deletes evidence.
- **Do not change `DRAFT_TTL_HOURS` or the cleanup jobs themselves** — once
  push notification exists, the 24h expiry is reasonable and out of scope.

### A5. Risk assessment

- **Low technical risk.** Both proposed call sites reuse existing,
  already-production-tested send functions (`_notify_admin_bridge`,
  `outbound.enqueue`) — no new bridge integration, no new outbound path.
- **Real operational risk: notification volume.** `_save_draft()` currently
  fires often (financial-intent gate, loop-detect, length/structure guard,
  AI_SAFE_MODE, DRAFT_ALWAYS gate, safety blocks — at least 7 distinct call
  sites in `bridge_poller/__init__.py` alone). A naive "push every draft
  immediately" would flood admin's WhatsApp. A4a should be scoped tightly —
  recommend limiting the immediate push to the **enriched (5-part,
  `sender_name`/`sender_message` populated) drafts only**, which is already
  the subset the Owner specifically asked for a 5-part format on
  (2026-07-27 spec, see commit `97fb353`) — and leave the rest to the
  batched A4b reminder.
- **Idempotency risk if done wrong.** Must reuse the existing
  `idempotency_key` mechanism in `outbound.enqueue()` (as `_notify_admin_bridge`
  already does via its hour-bucketed digest) to avoid duplicate sends on
  retry/restart.
- **No schema change required** for A4a. A4b needs one new
  `fazle_reminders_sent` topic value only (`'draft-pending'`) — no migration,
  that table already supports arbitrary topic strings.

### A6. Rollback strategy

- A4a: single new function call, guarded by a settings flag
  (`DRAFT_PUSH_NOTIFICATION_ENABLED`, default `true` once approved) — set to
  `false` to instantly revert to current (silent) behavior without a
  redeploy.
- A4b: new job is additive and independently toggleable
  (`register_job`/`add_job` pattern already supports disabling one job
  without touching others) — remove its `add_job()` registration to disable;
  no data is deleted or migrated, `fazle_reminders_sent` rows are harmless
  if the job is later removed.
- Both changes are reversible with a `git revert` of a single, small commit
  each — no data migration involved either direction.

---

## GAP B — Escort roster stuck at `draft`, not reaching `confirmed`

### B1. Current production behavior

`wbom_escort_programs`/`escort_roster_entries` write correctly at intake:
new client orders land as `status='draft'` immediately (confirmed live —
two new draft rows appeared during the audit itself, `program_id` 1029/1030).
The `draft → confirmed` transition happens only via
`_is_authoritative_bridge_confirmation()` (`modules/escort/__init__.py:2243-2265`)
matching a field confirmation reply from the escort/master, or via an admin
approving the pending draft. Production data shows the **last `confirmed`
roster entry is from 2026-07-27** — a 5-day gap as of the audit date, despite
new draft orders continuing to arrive daily.

### B2. Knowledge Base reference

`knowledge_base/05_workflows/escort_workflow.md` and
`knowledge_base/05_workflows/escort_source_of_truth_workflow.md` document
the two-stage draft→confirmed model itself (not re-quoted in full here —
no KB/production mismatch on the *mechanism*). The mismatch is upstream: KB
§14 of `whatsapp_reply_workflow.md` (Gap A) documents that admin review
drafts get sent for review; if that push never happens, nothing downstream
of it — including this confirmation step, when it depends on an admin
approving a draft rather than a field-confirmation reply — can complete
either.

### B3. Gap analysis

This is **not a second, independent bug** — it is the observable downstream
effect of Gap A. Draft escort programs wait on either (a) the escort/master
sending a field-confirmation message, which is a separate, working mechanism
untouched by this proposal, or (b) an admin reviewing and approving a
pending `fazle_draft_replies` row, which per Gap A is not happening (0
reviewed since 2026-07-27, 0 approved since 2026-06-09). Fixing Gap A's push
notification is expected to directly reduce this backlog, since the admin
will actually see there's something to review.

### B4. Proposed implementation

**No separate code change proposed for Gap B beyond Gap A.** Recommend:
ship Gap A (A4a+A4b) first, then re-measure the `draft`→`confirmed` gap
after ~1 week of live data before deciding whether a second, independent
fix is needed here. If the backlog persists after Gap A ships (i.e. admin
*is* seeing drafts but still not confirming escort orders specifically),
that would point to a different, UI/workflow-friction problem, not a
notification gap — worth a fresh, narrower investigation at that point
rather than guessing at a fix now.

### B5. Risk assessment

No new risk — this section proposes no code change, only a measurement
checkpoint.

### B6. Rollback strategy

N/A — no change proposed.

---

## GAP C — Admin→accountant cash instruction: strict-parser silent failure

### C1. Current production behavior

`modules.payment_ingest.ingest_admin_cash_entry()` (`modules/payment_ingest/__init__.py:509-563`)
is the only path that writes a `fpe_cash_transactions` row directly from a
WhatsApp message text with `source_channel='admin-accountant-instruction'`.
It only runs when:
- `message_router.process_message()` resolves the **sender's role to
  `accountant`** (`modules/message_router/__init__.py:592`) — not `admin`,
  despite the function's name and docstring; and
- the message text matches `_SHORTHAND_RE`
  (`modules/payment_ingest/__init__.py:424-431`), an exact single-line
  pattern: `[ID: <mobile>] <Name> <mobile>(N|B|Nagad|Bikash|Bkash|Cash|C|Rocket|R|BANK) <amount>/-`.

If the role resolves to `accountant` but the text does **not** match
`_SHORTHAND_RE`, execution falls through
(`modules/message_router/__init__.py:613-618`) to `kb_get_reply()` then
`ai.generate_reply()` — a generic conversational AI reply. **No log
warning, no draft, no admin alert is produced on a parse miss.** Verified
live: the accountant contact (`8801844836824`, "Mamun Vai") sends only daily
balance-summary messages (e.g. `31/7/26=টোটাল বাকি =27,435/-`), which
correctly do **not** match the shorthand (by design — KB confirms these are
informational) and never write a transaction. No message from this contact
in the entire recent history matches `_SHORTHAND_RE`. Separately, the
`fpe_cash_transactions` rows that *did* land on the audit date came from a
different sender/role (an escort self-reported payout format that happens
to superficially resemble the shorthand) via a different code path, not
via `ingest_admin_cash_entry()` at all.

### C2. Knowledge Base reference

`knowledge_base/05_workflows/cash_workflow.md`, lines 12-21:

> "Admin Payment Message -> Accountant Receipt -> Transaction Parse ->
> Duplicate Check -> Cash Ledger Entry -> Employee Balance Update ->
> Payroll/Advance Sync... **Admin -> Accountant instruction path: admin
> sends a payment instruction to accountant -> message is treated as final
> payment proof -> parser writes `fpe_cash_transactions` directly via
> `create_transaction()`. No additional verification or draft is required.
> Admin's message reaching the accountant is the completion trigger.**"

Line 49 separately confirms company-summary messages are informational-only
by design — that part matches production exactly.

### C3. Gap analysis

The KB describes "a payment instruction" in prose, with no format
constraint stated. Production requires an **exact, rigid single-line
regex** with no tolerance for natural phrasing, extra words, multi-line
messages, or minor formatting drift (e.g. missing the trailing `/-`, using
"taka" instead of the amount-then-slash convention, writing the method
spelled out differently than the 8 accepted tokens). There is no documented
fallback, no partial-match draft, and no failure signal anywhere — the
sender (accountant) and the intended beneficiary of the write (admin) both
have zero indication the instruction wasn't understood. This is the exact
mechanism that would produce the reported symptom: an admin→accountant
conversation that, in the admin's own words, simply didn't process, with no
error to explain why.

### C4. Proposed implementation

Two independent, additive changes (does not touch the existing successful
path at all):

- **C4a — Parse-miss visibility.** In `message_router.py`'s accountant
  branch (`modules/message_router/__init__.py:591-619`), when role is
  `accountant` and the text looks payment-shaped (contains a BD mobile
  number **and** a bare number followed by `/-` or "taka"/"tk", a much
  looser heuristic than `_SHORTHAND_RE`) but `is_admin_cash_shorthand()`
  returns `False`, create a `fazle_draft_replies` row (reusing
  `shared.draft_reply.create_draft_reply()`, `intent='cash_instruction_unparsed'`)
  instead of silently falling through to a generic AI reply — flagged for
  admin review with the original text preserved verbatim, and (once Gap A
  ships) covered by the same push notification. This gives a human a
  chance to catch and manually correct a near-miss instruction before money
  movement depends on it being remembered correctly from memory.
- **C4b — Format tolerance widening (optional, needs Owner sign-off on
  exact grammar).** Loosen `_SHORTHAND_RE` to tolerate the most common real
  variations observed in accountant/admin traffic — multi-space runs
  (already handled), the amount word "taka"/"tk" as an alternative to `/-`,
  and letting `ID:` prefix be genuinely optional (it already is,
  `(?:ID\s*[:\-]?\s*...)?`). **This requires Owner review of exact accepted
  formats before implementation** — widening a financial-instruction parser
  carries real risk of false-positive matches (e.g. an unrelated message
  that happens to contain a phone number and a number-with-slash), so this
  sub-item is proposed for discussion, not for immediate implementation.

### C5. Risk assessment

- **C4a is low risk** — strictly additive (a new draft row on a path that
  currently does nothing), does not change behavior for any message that
  already matches `_SHORTHAND_RE` successfully, cannot cause a duplicate
  transaction (it never calls `create_transaction()`/`ingest_admin_cash_entry()`).
  Main risk is over-triggering (any accountant message with a number in it
  creates a review draft) — mitigated by the heuristic requiring both a
  phone-number-shaped token and an amount-shaped token together, and by
  being purely a draft (reviewable/dismissible), not a data write.
- **C4b is higher risk** — this is a financial-instruction parser; a
  loosened regex that over-matches could create an **incorrect** final
  ledger entry (no draft step, no verification, per the KB's own
  "no additional verification... required" line) rather than just failing
  silently. This must not ship without explicit Owner-approved test cases
  covering both "should match" and "should NOT match" examples.

### C6. Rollback strategy

- C4a: single new conditional branch, trivially revertable via `git
  revert`; produces only new rows in an existing table (`fazle_draft_replies`)
  that are already subject to the existing 24h TTL cleanup — no schema
  change, no risk of orphaned state if reverted.
- C4b: **not proposed for implementation in this pass** — if approved
  later, ship behind its own settings flag so the stricter regex can be
  restored instantly if it over-matches in production.

---

## Combined Implementation Plan

Ordered by dependency and risk, each step gets its own commit + restart-
verify per this repo's standing convention (`scripts/restart_gate.sh`), and
each is independently revertible per the rollback sections above.

| Step | Change | Depends on | Risk | New settings flag |
|---|---|---|---|---|
| 1 | A4a — immediate push for enriched (5-part) drafts only | none | low | `DRAFT_PUSH_NOTIFICATION_ENABLED` |
| 2 | A4b — batched 30-min "N drafts pending" reminder job | Step 1 (shares send path) | low | reuse `INTERNAL_NOTIFICATIONS_ENABLED`, new `DRAFT_REMINDER_INTERVAL_MINUTES` |
| 3 | C4a — parse-miss draft for accountant-role near-miss cash instructions | none (independent of 1-2) | low | `CASH_INSTRUCTION_PARSE_MISS_DRAFT_ENABLED` |
| 4 | **Checkpoint, not code:** re-measure Gap B (`draft`→`confirmed` backlog) after ~1 week of Steps 1-2 live | Steps 1-2 | none | — |
| 5 | C4b — regex tolerance widening | Owner-approved test-case list from C4b discussion | medium | ship behind its own flag |

Steps 1-3 can be implemented and shipped independently of each other (no
shared code paths that would create merge conflicts) and are each small
enough to review in isolation. Step 4 is a decision checkpoint, not
implementation work. Step 5 is explicitly gated on a separate Owner
conversation about accepted message formats and is not part of this
approval request.

### Verification plan (per step)

- Steps 1-3: unit test for the new branch/function (matching this repo's
  `tests/unit/test_admin_draft_review_workflow.py` pattern already
  established for the 5-part draft format), then a live smoke test — send
  a real test message matching the target shape, confirm (a) the DB row
  appears as before, (b) the new push/draft-row also appears, (c) no
  duplicate/second transaction is created for the already-working paths.
- Step 5 (if later approved): requires an explicit before/after test-case
  table (should-match / should-NOT-match examples) signed off by Owner
  before any regex change ships, given the direct-to-ledger, no-draft
  nature of that specific parser.

---

## Stop — awaiting management approval

Per KB-First policy, no production code will be written until this is
approved. Specifically requesting a decision on:

1. **Steps 1-2 (Gap A push notification)** — approve/reject/amend.
2. **Step 3 (Gap C parse-miss draft)** — approve/reject/amend.
3. **Step 5 (Gap C regex widening)** — this needs a separate follow-up
   conversation about exact accepted message formats before it's even
   ready for a yes/no; flagging it now so it isn't lost, not asking for
   sign-off on it yet.
4. Confirm whether Gap B (§B4) should simply wait on Gap A's effect as
   proposed, or whether the Owner wants a parallel investigation started
   now regardless.
