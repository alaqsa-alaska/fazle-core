# Next Implementation Queue — Fazle Core

**Last updated:** 2026-07-17 (end of Session 20) — reviewed against `git log` through
commit `d870cee` and `knowledge_base/00_governance/management_decisions.md` Sessions
13-20. Session 20 (this update) shipped three separate pieces of work — see "What
shipped in Session 20" below: Step 1 (a blocking-SQLite-call fix), Step 2 (the
`payroll.html` shared-fetch migration), and Step 3 (a Finance-First Link/Sync Integrity
audit + 3 approved implementation phases). All commits through `d870cee` pushed to
`origin/main` (`e458158..d870cee`, clean fast-forward, 23 commits), confirmed synced —
`git rev-parse HEAD` and `git rev-parse origin/main` match. Working tree clean except an
intentionally-untracked `db/migrations_drafts/047_*.DRAFT-DO-NOT-APPLY`, which is a
**rejected, dead** migration kept only as a record — see "Resolved/closed since last
review" below, not a pending task.

**How to use this file:** work top to bottom within a tier. Don't start Phase 2/3 items
while Tier 0/1/2 items remain open — that repeats the exact "freeze declared, freeze not
held" failure mode the consolidation effort exists to fix (see `BUSINESS-CONSTITUTION.md`
Article VII). **New this revision:** a "Known Broken / Conflicting Workflows" section and
a "URL / Route-Based Functions Needing Attention" section, below, called out separately
per request — both are pointers into the Tier 2 / Immediate-Next detail, not new items.

**Source:** `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` (10-report audit), `docs/PRODUCTION_CERTIFICATION_v1.0.md`,
`knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md`, and
`knowledge_base/00_governance/management_decisions.md` Sessions 13-18 (2026-07-15/16, the
work this revision folds in). Read those for full detail — this file is a queue, not a report.

---

## What actually shipped since the last review (2026-07-13 → 2026-07-16, Sessions 13-18)

All Owner-authorized, all committed, all documented in `management_decisions.md`:

- **S13 (2026-07-15):** social auto-reply reverted to Selective-draft policy (not blanket);
  `mark_paid()` now posts to `fpe_cash_transactions` and fails closed if unlinked;
  settlement formula prorates office-staff basic salary by attendance
  (`compute_run(prorate_basic=True)`, still dormant — flag stays off, 0 eligible employees);
  WBOM status-casing lookup fixed (`LOWER(status)`).
- **S14 (2026-07-16):** payroll "View All Transactions" double-fetch/stale-filter bug fixed;
  new `/payroll/transactions/{employee_id}` deep-link route.
- **S15 (2026-07-16):** Employees-panel edits to Salary/Designation/Joining Date now sync
  `wbom_employees` → `fpe_employees` (previously silently didn't); a pre-existing
  `joining_date` 500 (raw string into a `DATE` column) fixed alongside it.
- **S16-S18 (2026-07-16):** three-round escort-data audit. Confirmed the sender-validation
  gate and 24h hard-delete Owner asked to add **already existed** (commits `41916b0`,
  `82f2b33`) — no new validation code needed. Cleaned 31 historical rows across three
  rounds (18 unauthorized-sender + 10 legacy-expired + 3 confirmed duplicates), each
  audit-logged before deletion. Two items explicitly left open by Owner directive, not
  oversight — see "Immediate Next" below.

## What shipped in Session 19 (2026-07-17)

All Owner-authorized, all committed and live, all documented in `management_decisions.md`.
Implemented `docs/AUDIT_REPORT_2026-07-17.md`'s recommended session plan, Steps 0-8, one
at a time with a commit + restart-verify after each:

- **Step 0:** retroactive `management_decisions.md` entries for migrations 043
  (`office_assistant_role`) and 046 (`fpe_employee_edit_requests`), which had shipped
  with no governance record.
- **Step 1 — 🔴 the highest-severity live finding:** webhook path (`/webhook/mcp1`/`mcp2`)
  gained the same `_requires_legacy_workflow()` gate the SQLite poller already had —
  previously an inbound escort order over the webhook could be silently classified as a
  social message and never reach booking creation. Also fixed the P15-03 dedup guard from
  fail-open to fail-closed. **Confirmed live** via a real smoke test (escort-shaped test
  message → 0 rows in `social_inbox_events`, correctly routed to the escort pipeline
  instead; control greeting → correctly classified `social`).
- **Step 2:** `webhook/mcp1`'s always-200 behavior — Owner decision: Option A, document as
  intentional (prevents bridge retry-storms). Test renamed/updated to match.
- **Step 3 — destination-field leak, upgraded from "unconfirmed" to fixed:** root cause
  confirmed (`_CD_DEST_RE`'s `\s*` crossing a newline on a blank `Destination:` line,
  `modules/escort/__init__.py`), fixed (`\s*`→`[ \t]*`), blast radius corrected from 4 to 6
  rows (902 and 938 were previously uncounted), 6 corrupted rows backfilled to `NULL`.
- **Step 4 — discovered `chat_history` sync was already running for ~2 days,
  undocumented:** the Session 13 "blocked on sudo" note was stale — someone/something
  installed and started `chat-sync.service` the same night, outside any recorded session.
  Verified healthy (9,156+ rows synced, zero errors in its full run). Record corrected.
- **Step 5:** dual-write concurrency diagnostic (no fix) — documented all 3 writers to
  `escort_roster_entries` (a previously-undiscussed 3rd writer, `create_roster_entry()`,
  found). Still open, see below.
- **Step 6 (+6B):** hardcoded admin bridge number (`8801880446111`) replaced with settings
  references across 8 files. Found and fixed a real, separate bug along the way:
  `modules/social_auto_reply/rate_limiter.py::mark_sent()` was passing an `int` to a
  `$2::text`-cast query param, crashing on every send and silently disabling the
  DB-backed rate limiter for at least ~22 hours before the fix.
- **Step 7:** consolidated the escort-client-phone-allowlist duplication by extending
  `modules/fazle_payroll_engine/normalizer.py` (the pre-existing, Owner-directed single
  source of truth for BD phone normalization) with the missing operator-code check,
  rather than building a new file — caught mid-implementation after discovering that
  module already existed. **Caused a brief self-inflicted production outage** (deleted a
  function still cross-imported by `client_billing/profiles.py`; hotfixed same session).
  Full incident writeup in `management_decisions.md`.
- **Step 8:** fixed the blocking `sqlite3.connect()` call inside `async def _poll_bridge()`
  (`modules/bridge_poller/__init__.py`) — extracted a sync helper, dispatched via
  `asyncio.to_thread()`. Found (not fixed) a second instance of the same bug class in
  `modules/escort_roster/history_sync.py:779` — see "Technical Debt" below.

**Not done this session (deliberately, per Owner decision):** Step 9 (Groq key rotation +
DB password rotation) — deferred to its own dedicated task, not bundled into a code-fix
session.

## What shipped in Session 20 (2026-07-17)

All Owner-authorized, all committed, pushed, and live, all documented in
`management_decisions.md`. Three independent pieces of work, each with its own
per-change approval trail (exact current code + exact proposed diff shown and approved
before every change, no exceptions):

- **Step 1:** fixed the second blocking-`sqlite3.connect()` instance Session 19 Step 8
  found but didn't fix — `modules/escort_roster/history_sync.py:779`, inside `async def
  sync_from_bridge_sqlite()`. Same pattern as commit `a5d3871`: extracted a sync helper
  (`_fetch_backfill_messages()`), dispatched via `asyncio.to_thread()`. No business logic
  changed. Commit `e3c1f58`.
- **Step 2:** `payroll.html` shared-fetch migration — 12 of its 16 `fetch()` calls (2
  wrapper functions + all 10 ad-hoc calls) migrated to `shared-auth-fetch.js`, done as 6
  separate, individually-verified sub-groups (B1/B2 wrappers, C1-C4 ad-hoc calls by
  feature area). `api()`/`adminApi()` deferred to Tier 2 (dual-prefix routing + auto-
  serialize behavior need a design decision, not a mechanical swap). Commits `d76a7ff`,
  `2cab0f2`, `6feffd1`, `cc6f96d`, `b738006`, `8512acd`. See "URL / Route-Based Functions
  Needing Attention" below.
- **Step 3 — Finance-First Link/Sync Integrity audit,** requested separately after Steps
  1-2 closed: a read-only audit (ownership matrix, writer inventory, mismatch report,
  top-3 root-cause paths — full report in `management_decisions.md` S20-08) into
  unlinked/out-of-sync row creation between `wbom_employees`/`fpe_employees` breaking
  finance workflows, followed by 3 Owner-approved, narrowly-scoped fixes:
  - **Phase 1:** disabled `message_router::_seed_unknown_employee_identity()` — it
    auto-created a `wbom_employees` row (and indirectly seeded `fpe_employees`) for
    **any** sender identity_brain classified as `"unknown"` whose message merely looked
    like an attendance/escort/advance-request, with zero trust check of its own. By
    construction every such sender had already failed every trust check the system has.
    Disabled, not deleted — full rationale in the function's own docstring. Commit
    `47018f7`.
  - **Phase 2:** fixed a real search-order gap in the trusted admin→accountant finance
    path — `payment_ingest::_match_or_create_instruction_employee()` collapsed
    `employee_id_mobile`/`payout_mobile` into one search key before ever calling
    `match_employee()`, so an explicit ID-mobile miss never fell back to a payout-number
    retry, risking duplicate employee rows for someone already registered. 2-line fix;
    trusted auto-create, no-reply, and no-review-queue behavior all verified unchanged.
    Commit `38dbedd`.
  - **Phase 3:** added `modules/reports::_b_unresolved_fpe_employees()`, a read-only
    report distinguishing genuinely-unlinked `fpe_employees` rows from rows that are
    already correctly linked but carry a stale `resolution_status` label (the Phase 2
    finding) — never conflated. Confirmed live against production:
    `172 (no_wbom_match) + 81 (wbom_match_unlinked) = 253`, matching the audit's live
    finding exactly; `153` more `linked_but_mislabeled`. Served via the existing generic
    `GET /reports/{name}` dispatcher, no new route. Commit `b0c0057`.
  - **Explicitly excluded from this session, unless separately approved later:**
    `wbom_*`→`fpe_*` renames, the `wbom_employees`↔`fpe_employees` merge,
    `employee_id_phone` uniqueness changes, escort dual-write concurrency,
    `accounting.py` semantics changes, `admin_transactions` bypass cleanup, and the
    pre-existing operator-manual-entry review-queue gap found during Phase 0 (documented,
    not fixed — see "Immediate Next" below).

**Two new Technical Debt items found and filed this session, not fixed:**
`POST /admin/contact-roles` accepts a free-text `role` field with no enum/whitelist (an
admin could assign `role=accountant` to a second number without touching `.env`); a
`resolution_status` metadata-staleness note for anyone building on the new report. Both
detailed under "Technical Debt" below.

## Resolved/closed since last review (no action needed, noted so nobody re-opens them)

- **Migration `047` (bulk-fabricate designation/salary for 180 unlinked FPE employees) —
  REJECTED 2026-07-14, replaced by a reviewed tool.** Owner declined bulk-guessing
  `'Unknown'` designations; replaced with a one-at-a-time human-reviewed flow instead:
  `app/static/wbom-create-link.html` + `POST /api/admin/employees/create-and-link` (for
  the 180 with no existing WBOM row) and `app/static/wbom-link-review.html` + `POST
  /api/admin/employees/link-fpe` (for the separate 33 phone-match pairs). Both tools are
  live (commits `61dd382`, `1e1e496`). The draft file is dead — do not revive it.
- **`chat_history` migration (048) — recorded in inventory, migration 047 intentionally
  skipped/reserved.** See `db/MIGRATION_INVENTORY.md`.

---

## Immediate Next (genuinely open, ranked)

1. **[x] 🟢 RESOLVED 2026-07-18 (Phase 1I) — Escort-roster/lifecycle dual-write
   concurrency hang.** See "Known Broken / Conflicting Workflows" below for the full fix
   record. Reproduced both the check-then-act race and the actual deadlock root cause
   (nested pool-connection acquisition), fixed `sync_program_to_roster()` to be atomic
   and connection-safe, and deliberately left `escort_lifecycle`'s own writer unchanged
   after finding the two writers compute financial fields from different, incompatible
   sources. Commit `2d712f9`.
2. **[x] RESOLVED 2026-07-17 (Session 19 Step 3) — Escort `destination` field leak.** Root
   cause confirmed (`_CD_DEST_RE`'s `\s*` crossing a newline on a blank `Destination:`
   line), fixed, 6 corrupted rows (blast radius corrected from 4) backfilled to `NULL`.
   See `knowledge_base/00_governance/escort_destination_field_leak_issue_2026-07-16.md`
   (status now "CONFIRMED AND FIXED") and "Known Broken / Conflicting Workflows" below.
3. **[ ] Historical rows 703, 707, 734 — flagged for manual admin review, Owner declined to
   guess.** Raw text is genuinely ambiguous (unclaimed phone-like number near the lighter
   name in 703/707; no mother-vessel label at all in 734). See
   `escort_sender_validation_audit_phase2_2026-07-16.md` §3 review note. Needs an admin
   with domain knowledge of the actual vessel/booking, not another automated pass. Not
   touched in Session 19.
4. **[x] RESOLVED 2026-07-17 (Session 19 Step 4) — `chat_history` sync.** Turned out to
   already be installed and running since 2026-07-15 23:28:28 — the "blocked on sudo, not
   deployed" note below was stale. Verified healthy (9,156+ rows, zero errors across the
   full run). See `management_decisions.md` Session 19 Step 4 for the full correction.
5. **[x] RESOLVED 2026-07-17 (Session 20) — `payroll.html` shared-fetch migration.** See
   "URL / Route-Based Functions Needing Attention" below. 12 of `payroll.html`'s 16
   `fetch()` calls (2 wrapper functions + all 10 ad-hoc calls) migrated to
   `shared-auth-fetch.js` across 6 Owner-approved, individually-verified sub-groups (B1,
   B2, C1-C4). `api()`/`adminApi()` deferred to Tier 2 (design decision needed first, not
   a mechanical swap) and `resolveUserAccess()`/`doLogin()` permanently out of scope
   (bootstrap/session logic). Full record: `management_decisions.md` Session 20, S20-02
   through S20-07. **Remaining scope: 7 pages, not 8** — see the updated table below.
6. **[ ] `open-webui` docker-compose.yml Groq API key in plaintext** (`/home/azim/docker-compose.yml`,
   outside this repo) — still flagged, still not rotated. Same class of issue as the
   Postgres password (already rotated 2026-07-12), smaller blast radius. This is Session
   19's deferred "Step 9" — deliberately not bundled into a code-fix session; needs its
   own dedicated task (requires logging into the Groq console).
7. **[x] RESOLVED 2026-07-17 (Session 19 Step 2) — `webhook/mcp1` always-200 behavior.**
   Owner decision: Option A, document as intentional (prevents bridge retry-storms). Test
   renamed to `test_missing_sender_field_returns_200` and updated. See "URL / Route-Based
   Functions Needing Attention" below.
8. **[ ] LOW PRIORITY — `PUT /api/admin/employees/143` 500 error, single occurrence, no
   confirmed pattern.** Occurred 2026-07-16 20:48:46 — no traceback found in available log
   history; Pydantic type validation rules out a simple str/int mismatch (confirmed via a
   direct test: an int for an `Optional[str]` field raises a 422, not a 500). Cannot
   diagnose from logs alone. Next step when reproducing: check logs immediately after
   triggering the same edit action live to capture the traceback. Standalone bug, not part
   of any Tier or Phase — investigate opportunistically when log context is available, not
   a scoped project of work. Not resolved in Session 19 (Owner decision: log and defer).
   See `management_decisions.md` Session 19 for full detail.
9. **[ ] NEW (found in Session 19 Step 8) — `modules/escort_roster/history_sync.py:779`
   blocking SQLite call in async context.** Same bug class as the `bridge_poller` fix
   (commit `a5d3871`), not yet applied here. See "Technical Debt" below for full detail.
10. **[x] RESOLVED 2026-07-17 (Session 20 Step 3, Phase 1) — unknown/untrusted-sender
    auto-create of `wbom_employees`/`fpe_employees` rows.** `message_router.py::
    _seed_unknown_employee_identity()` disabled — every sender reaching it had already
    failed every trust check identity_brain has (not admin, not accountant, not
    escort-client, not an existing employee/contact), so auto-creating a row from
    nothing more than a message shaped like an attendance/escort/advance-request was
    exactly the "random/unapproved number" auto-create pattern the Owner's finance-
    integrity audit flagged. Disabled, not deleted — full rationale in the function's
    own docstring. See `management_decisions.md` Session 20 Step 3 Phase 1 (S20-09),
    commit `47018f7`.
11. **[x] RESOLVED 2026-07-17 (Session 20 Step 3, Phase 2) — trusted admin→accountant
    finance path employee-search gap.** `payment_ingest::_match_or_create_instruction_employee()`
    collapsed `employee_id_mobile`/`payout_mobile` into one search key before ever
    calling `match_employee()`, so an explicit ID-mobile miss never fell back to a
    second search against the payout number — risking a duplicate `wbom_employees`/
    `fpe_employees` row for an already-registered employee. Fixed with a 2-line
    fallback; trusted auto-create, no-reply, and no-review-queue behavior all
    preserved and verified unchanged. See `management_decisions.md` Session 20 Step 3
    Phase 2 (S20-10), commit `38dbedd`.
12. **[x] DONE 2026-07-17 (Session 20 Step 3, Phase 3) — `unresolved_fpe_employees`
    read-only report.** New builder in `modules/reports/__init__.py`, served via the
    existing `GET /reports/{name}` dispatcher (no new route). Splits `fpe_employees`
    rows needing review into `no_wbom_match`, `wbom_match_unlinked`, and
    `linked_but_mislabeled` (the last is the Phase 2 "already linked, stale label"
    finding — reported separately, not flagged as broken). Confirmed live against
    production: 172 + 81 = 253, matching Phase 2's live-audit number exactly. See
    `management_decisions.md` Session 20 Step 3 Phase 3 (S20-11), commit `b0c0057`.
    **🟢 DONE 2026-07-18 (Phase 1E):** both pages now load this report live on
    boot instead of the hardcoded `CANDIDATES` snapshot. Full record:
    `management_decisions.md` Session 24 Phase 1E. Commit `fe5b342`.
    **🟢 RESOLVED 2026-07-18 (Phase 1E.1):** real-browser certification completed
    (Playwright + Chromium, already installed). Found and fixed a genuine mobile
    horizontal-overflow defect on `wbom-link-review.html` and added a missing
    refresh control to both pages; all other checks (auth, live counts, XSS incl.
    attribute-breakout, Unicode/Bangla rendering, 409 handling, cancel-no-write)
    verified correct. New permanent suite: `tests/e2e/test_employee_review.py`
    (19 tests). Full record: `management_decisions.md` Phase 1E.1. Commit
    `03f851a`.

---

## Known Broken / Conflicting Workflows

### 1. Escort release-event dual-write — 🟢 RESOLVED 2026-07-18 (Phase 1I)

**Was:** concurrent releases could hang if `escort_lifecycle` were ever routed through
`sync_program_to_roster()` (attempted 2026-07-13, reverted). Diagnostic completed
2026-07-17, root cause not yet found.

**Fixed 2026-07-18:** built a deterministic reproduction of both bugs before touching
any code. (1) `sync_program_to_roster()`'s SELECT-then-branch was a real check-then-act
race — 4/10 concurrent calls for the same `program_id` raised a live
`UniqueViolationError`. Fixed with a single atomic `INSERT ... ON CONFLICT (program_id)
DO UPDATE`. (2) The actual "second, un-root-caused issue" that hung the 2026-07-13
attempt even after that race looked fixed: `sync_program_to_roster()` never accepted a
`conn` parameter, so it always acquired its own fresh pool connection — calling it from
inside a caller's already-held transaction (exactly what the reverted refactor did)
exhausted the pool and deadlocked every concurrent caller. Reproduced as a genuine
~25-30s hang, fixed by threading an optional `conn` through every internal call;
re-verified the same reproduction completes in ~2.6s post-fix.

**Decision: `escort_lifecycle` (Writer 1) is NOT changed and does not call
`sync_program_to_roster()`.** Found during investigation: Writer 1's financial fields
come from the real `fazle_payment_drafts` row (including `advance_deduction`, which
`sync_program_to_roster()` has no concept of at all), while `sync_program_to_roster()`
independently recomputes them — unifying the writers would have silently changed
production payment figures. Writer 1 stays canonical and unchanged for the
release-event path; the fix hardens Writer 2 for its own 3 other real callers where
both bugs were already live. Writer 3 (`create_roster_entry()`, the admin manual-entry
API) has its own separate, still-unfixed races — documented, not in scope.

New test suite: `tests/unit/test_escort_roster_concurrency.py` (6 tests, including the
literal "release + sync_program_to_roster running concurrently" scenario). Zero
regressions across the full escort/payroll test surface. Full record:
`management_decisions.md` Phase 1I. Commit `2d712f9`.

**Where:** `modules/escort_lifecycle/__init__.py` (~line 315-390, the raw `INSERT INTO
escort_roster_entries ... ON CONFLICT DO UPDATE` block) vs. `modules/escort_roster`'s own
`sync_program_to_roster()` API, which is never called from this path.

**The conflict:** two independent code paths write to the same `escort_roster_entries`
row for the same program:

```
Release event arrives (WhatsApp "[RELEASE CONFIRMED]" or admin action)
        │
        ▼
modules/escort_lifecycle.handle_admin_release_confirmation()
        │
        ├── writes escort_roster_entries directly, its OWN raw
        │   INSERT ... ON CONFLICT (program_id) DO UPDATE
        │   (single atomic statement — race-safe as-is)
        │
        └── [never calls] modules/escort_roster.sync_program_to_roster()
                (the module that's supposed to own this table)
```

Two independent, duplicated write paths into the same table is the actual defect (the
Tier-2 "cleanup" item) — today it's merely duplicated logic, not broken. It became
actively broken when someone tried to fix the duplication:

```
2026-07-13 refactor attempt:
  escort_lifecycle → (threaded conn/pay_override through) → sync_program_to_roster()
        │
        ▼
  sync_program_to_roster() does SELECT-existing-row, THEN branch
  (insert vs. update) instead of one atomic statement
        │
        ▼
  test_concurrent_release_events (10 concurrent releases, same program_id)
        │
        ▼
  HANGS — check-then-act race window between the SELECT and the
  branch decision, invisible in isolated/single-threaded tests
        │
        ▼
  Rewrote that specific race to be atomic too → test STILL hung
  (a second, un-root-caused issue underneath) → fully reverted,
  confirmed via `git checkout`, tests pass again on the original
  duplicated-but-safe code.
```

**Current state:** reverted, safe, but still duplicated (`escort_lifecycle` doesn't use
`escort_roster`'s own write API). **Do not re-attempt this refactor without first
reproducing the hang and inspecting `pg_locks`/`pg_stat_activity` live** — the 2026-07-13
attempt burned real time chasing this blind twice and stopped rather than ship a
financial/lifecycle-path deadlock.

**Update 2026-07-17 (Session 19 Step 5):** full diagnostic completed, no code changed.
A **previously-undiscussed 3rd writer** to `escort_roster_entries` was found:
`escort_roster.create_roster_entry()` (the manual admin "add roster entry" API), with its
own two separate check-then-act races (program_id collision, duplicate-entry bypass),
unrelated to the release-event path. Running `test_concurrent_release_events` against
today's (reverted, safe) code passes — expected, not new evidence, since the hang was
specific to the abandoned refactor which no longer exists in the tree. Full writeup:
`knowledge_base/00_governance/escort_dualwrite_concurrency_debug_2026-07-17.md`, including
diagnostic SQL ready for whenever someone next attempts the actual fix.

### 2. Escort `destination` field leak — ✅ RESOLVED 2026-07-17 (Session 19 Step 3)

**Where:** `modules/escort/__init__.py::_create_confirmed_from_admin()` (~line 1650-1680),
the direct-create path used when an admin's confirmation message has no matching prior
draft, and `_update_program_confirmed()` (a second consumer of the same parse output,
found during the fix).

**Root cause confirmed and fixed.** `_CD_DEST_RE` (`modules/escort/__init__.py:144-147`)
used `\s*` around the colon, which matches newlines — when an admin left `Destination:`
blank, the pattern spilled into and captured the entire next line ("Escort Name: ...")
instead of an empty value. Confirmed via a direct regex test against the real source
message (message_id 10678). Fixed: `\s*` → `[ \t]*`. Blast radius corrected from the
originally-reported 4 rows to **6** (902 and 938 were previously uncounted; 938 confirmed
the bug also affects `_update_program_confirmed()`, not just the auto-create path). All 6
corrupted rows backfilled to `NULL` (the correct value, not data loss — the admin
genuinely left the field blank).

Full write-up (updated to "ROOT CAUSE CONFIRMED AND FIXED"):
`knowledge_base/00_governance/escort_destination_field_leak_issue_2026-07-16.md`.

---

## URL / Route-Based Functions Needing Attention

### 1. `POST /webhook/mcp1` — always returns 200 — ✅ RESOLVED 2026-07-17 (Session 19 Step 2)

`app/main.py:1507`. **Owner decision: Option A — document as intentional.** The route
returns `200` even for a structurally invalid payload (missing sender field) instead of
`422`, deliberately, to avoid retry-storms from the upstream bridge client. Test renamed
`test_missing_sender_field_returns_422` → `test_missing_sender_field_returns_200`,
assertion updated to `== 200` with an intent comment. No route code change. See
`management_decisions.md` Session 19 (S19-01a) for the full decision record, including the
bridge-client-script search performed before the decision (binary located, systemd
service confirmed, but no source code present to independently confirm its retry
behavior — decided anyway, since Option A is the safe default regardless).

### 2. Frontend pages not yet on the shared auth-fetch utility — 7 pages, not 8

`app/static/shared-auth-fetch.js` (`FazleAuthFetch`) was built 2026-07-13 and proven on
`escort-roster.html`. **Session 20 (2026-07-17) migrated `payroll.html`** — 12 of its 16
`fetch()` calls (both wrapper functions, `clientBillingFetch()`/`attendanceFetch()`, plus
all 10 ad-hoc calls) now go through `FazleAuthFetch`, done as 6 separate Owner-approved,
individually-verified sub-groups (see `management_decisions.md` Session 20, S20-02 through
S20-07, commits `d76a7ff`, `2cab0f2`, `6feffd1`, `cc6f96d`, `b738006`, `8512acd`).
`payroll.html`'s `api()`/`adminApi()` wrappers are **deferred, not done** — see the Tier 2
item above; dual-prefix routing and auto-serialize behavior have no equivalent in
`shared-auth-fetch.js` and need a design decision before migration, not a mechanical swap.

Remaining pages, verified current state (`grep -c "fetch(" app/static/*.html`):

| Page | fetch() calls | Notes |
|---|---|---|
| `kb.html` | 6 | |
| `drafts.html` | 5 | |
| `dashboard.html` | 2 | Has its own auto-JSON-serialize + logout-on-403 behavior — not a pure drop-in |
| `wa_chat.html` | 1 | Has its own 429/5xx handling — not a pure drop-in |
| `open-chat.html` | 1 | |
| `wbom-create-link.html` | 1 | New since the original "5 pages" count (S15/commit `1e1e496`) |
| `wbom-link-review.html` | 1 | New since the original "5 pages" count (commit `61dd382`) |

Each page needs individual behavioral verification after migration (no browser tooling
was available in the sessions that built the utility) — not a mechanical find-replace
across all 7 at once, per the same caution already recorded for the original 5 (and
followed sub-group-by-sub-group for `payroll.html` in Session 20).

### 3. `/payroll/transactions/{employee_id}` — new route, working, just documenting it exists here

Added 2026-07-16 (S14) specifically to fix the double-fetch/no-deep-link bug on the
Employees panel's "View All Transactions" button. Uses employee **ID**, not name, to
avoid display-name collisions. Not broken — listed here only because it's the most
recent URL-surfaced change and the natural place someone reviewing "recent URL-based
functions" would look first.

---

## Technical Debt

**`sqlite3.connect()` used outside `bridge_poller` — audited 2026-07-17 during Step 8
(Session 19), all 5 now confirmed safe:**

- 🟢 **Confirmed safe** (call-site verified, not just function shape — each is a plain
  `def` helper actually dispatched via `loop.run_in_executor(None, ...)`, not merely
  written as sync and assumed safe):
  - `modules/contact_sync/__init__.py` (`_read_bridge_contacts`)
  - `modules/fazle_payroll_engine/historical_sync.py` (`_fetch_all_dm_jids_sync`,
    `_fetch_messages_sync`, `_load_lid_map_sync` — the last is called from inside
    `_fetch_messages_sync`, itself already dispatched, so nested sync-to-sync is fine)
  - `modules/fazle_payroll_engine/gap_scan.py` (`_fetch_sqlite_rows_for_diff`)
- 🟢 **RESOLVED 2026-07-17 (Session 20) — `modules/escort_roster/history_sync.py:779`,
  inside `async def sync_from_bridge_sqlite()`.** This entry previously read "genuinely
  broken, not yet applied here" — that was stale; Session 20 Step 1 already fixed it the
  same day (this entry just wasn't updated at the time). The blocking sequence was
  extracted into `_fetch_backfill_messages()`, a plain sync helper, dispatched via
  `asyncio.to_thread()` — same pattern as `bridge_poller`'s fix (commit `a5d3871`).
  Commit `e3c1f58`. Re-verified live in code at a later baseline check (HEAD `b9cd43b`) —
  do not re-open.

**🟢 RESOLVED 2026-07-17 — `POST /admin/contact-roles` free-text `role` field.** an admin
could previously assign `role=accountant` (or `admin`/`employee`/`family`) to a phone
number without touching `.env`, conflicting with `CANONICAL_BUSINESS_RULES.md`'s
`.env`-authority Structural Rule. Fixed with a closed `AssignableContactRole` enum
(`vip_client`, `client_escort_buyer`, `repeat_client`, `vendor`, `supervisor`,
`candidate`) validated at the API boundary in `modules/contact_roles/routes.py` —
`admin`/`accountant`/`employee`/`family`/`blocked` are rejected outright (422), not
cross-checked against `.env`. Pre-existing rows using a forbidden legacy value (found
live: `accountant` ×4, `employee` ×5, `family` ×8) are left untouched and remain
readable/updatable via `PUT` as long as `role` itself isn't part of the update — no
data migration, no schema change. See `management_decisions.md` for the full record
and existing-role audit.

**🟢 RESOLVED 2026-07-17 (Session 23, Phase 1C) — `fpe_employees.resolution_status`
consistency.** Was: 7 writers across 3 modules (not just the originally-cited
`fpe_bridge.py` function) set `wbom_employee_id` without also flipping
`resolution_status` from the schema default `'unresolved'` to `'resolved'`. Fixed with
an identical conditional pattern everywhere (`CASE WHEN resolution_status =
'unresolved' THEN 'resolved' ELSE resolution_status END`, set atomically with the
link) — deliberately conditional, not unconditional, to protect the separate
dedup-workflow states (`duplicate`/`canonical`/`inactive`) and the confirmed-legitimate
`resolved`+unlinked `admin_manual_create` rows. Historical backfill executed same
session: 153 affected rows corrected in one audited transaction (rollback artifact:
`knowledge_base/00_governance/phase1c_backfill_audit_20260717.json`). Live-verified:
`unresolved_fpe_employees` report's `linked_but_mislabeled_count` **153 → 0**;
`no_wbom_match`/`wbom_match_unlinked` unchanged. Full record:
`management_decisions.md` Session 23 Stage 1. Commits `55a2d79`, `c2671fb`, `20c3778`.

**`modules/contact_roles/routes.py::upsert_contact_role()` raises a raw 500 (
`NotNullViolationError`) if `name`/`sub_role`/`notes` are omitted from the POST body
— found 2026-07-17 during Phase 1B test verification.** All three are
`Optional[str] = None` on `ContactRoleBody`, but the live schema has them `NOT NULL
DEFAULT ''` and the `INSERT` passes the Python `None` straight through instead of
falling back to `''`. Never hit in practice if every real caller already supplies
these fields; surfaced only because the new test suite (`tests/unit/test_contact_roles.py`)
correctly omitted optional fields per the API's own stated contract. Cheap fix
(`COALESCE($n, '')` in the `INSERT`, or `Field(default="")` instead of `None` on the
three models), not bundled into Phase 1B since it's unrelated to the role-whitelist scope.

**🟢 RESOLVED 2026-07-18 (Phase 1D) — `fazle_contact_roles` test-schema drift.** Was:
migration 029's own `CREATE TABLE IF NOT EXISTS` for this table was already 100%
correct (matches production exactly, confirmed) — the actual cause was
`tests/conftest.py`'s own pre-migration schema stub creating the table FIRST with a
stale shape (`id SERIAL`, a `label` column that exists in no production code path,
missing `sub_role`/`language_pref`/`is_active`/`metadata`), causing migration 029's
correct definition to silently no-op every time. Fixed by correcting the stub to match
migration 029 exactly; 4 test files that seeded via the now-removed `label` column
updated to use `name` instead. No production schema change. The 16 originally-affected
tests (`test_contact_roles.py`, `test_shared_auth_deps.py`) now pass, 45/45. Full
record: `management_decisions.md` Session 24 Phase 1D. Commit `9fea840`.

**🟢 RESOLVED 2026-07-18 (Phase 1G) — `tests/workflows/test_full_workflows.py`'s 6
failures.** Was: 4 function-signature drift failures (`compute_run()`,
`save_attendance()`, `finalize_payment()`/`reverse_payment()`), 1 test whose
release-confirmation date was always chronologically before the program's effective
start regardless of "today" (not a staleness issue — the program start itself defaults
to row-insertion date, not the parsed order text), and 1 multi-turn conversation-flow
drift (advance requests are now a strict 4-turn state machine). All fixed; also
uncovered and fixed 5 separate legacy-table (`wbom_cash_transactions` →
`fpe_cash_transactions`) assertion drifts in the same file. **Confirmed real,
un-fixed production bug found in the process:** `compute_run()`'s advances query
resolves `fpe_cash_transactions.employee_id` using the WBOM employee_id, but that
column's FK target is `fpe_employees(id)` — a different ID space; `mark_paid()` in
the same file resolves this correctly elsewhere. Real advances are silently never
deducted from payroll unless the two IDs coincidentally match. Needs its own
Owner-approved proposal (payroll formula change) — not fixed, left as an
intentionally-failing, clearly-documented test
(`test_advance_deducted_in_payroll`). Full record: `management_decisions.md` Phase
1G. Commit `1489bbd`.

**🟢 RESOLVED 2026-07-18 (Phase 1H) — `tests/workflows/test_escort_payment_flow.py`
"hang."** Was hypothesized above as an incompletely-mocked external call. Actual
finding: does not reproduce. Two clean back-to-back runs completed deterministically
in under 5 minutes each (275s, 225s), identical 11 real (non-hang) failures both
times, zero lock contention (`pg_locks`/`pg_stat_activity` snapshotted mid-run), no
test approaching its 30s timeout ceiling. Most plausible root cause: the malformed
multi-line background-bash invocation documented elsewhere in this same session
(redirection + trailing commands flattened by `eval` into bogus extra pytest
arguments) — an agent-tooling artifact, not a product or test defect. The file's 11
real failures are a separate, explicitly out-of-scope backlog item (mostly the same
`wbom_cash_transactions`/`compute_run()` drift as Phase 1G, plus one deliberately
removed route and one intentionally-hardened endpoint) — not fixed here. Full
record: `management_decisions.md` Phase 1H.

**🟢 RESOLVED 2026-07-18 — `compute_run()`'s advance-deduction employee-ID bug
(the one flagged as un-fixed in Phase 1G above).** Fixed: resolves the linked,
active `fpe_employees` row via `wbom_employee_id` first (matching
`_transition()`'s existing pattern), then queries `fpe_cash_transactions` by
that id. Production audit (read-only): 194 advance transactions all-time
(~70 distinct FPE employees, ৳153,408 total); 15/15 sampled real
advance-bearing employees had `id != wbom_employee_id`. **Zero realized
financial exposure** — confirmed all 534 sampled `wbom_payroll_runs` rows
(May/June/July 2026) are 100% `status='draft'`, 0% with `total_programs>0`;
this pipeline has never paid anyone, so the bug's impact was latent only,
never historical. Production-verified against real, previously-uncomputed
data: wbom employee 151 (FPE 177, "Nazmul"), period 2026-01, correctly
returned `total_advances: 3500.0` matching two real transactions (would have
been `0.0` before the fix). Full record: `management_decisions.md`
"Payroll Advance Deduction Employee-ID Resolution Fix". Commit `c2c4256`.

**🔴 NOT RESOLVED — same bug class found in 4 sibling functions, none fixed
(explicitly out of the above fix's narrow scope; do not assume this whole
class of ID-resolution issue is closed):**
- `modules/payment_workflow/__init__.py::create_escort_payment_draft()` —
  **highest priority of these.** This is the function that computes every
  real escort payment draft's `net_payable` on release (confirmed live path,
  `CANONICAL_BUSINESS_RULES.md`) — unlike `compute_run()`'s dormant pipeline,
  this is real money moving today with the same wrong-ID-space advance
  subtraction.
- `modules/payment_workflow/__init__.py::create_advance_request_draft()` —
  same bug in a display-only "paid this month" figure shown to the admin
  approving a new advance — misleading but not money-miscalculating.
- `modules/payroll_logic/__init__.py::get_payroll_summary()` — same bug
  across 3 queries feeding customer-facing WhatsApp payment-history replies.
- `modules/admin_employees/__init__.py::get_employee()` — same bug in a
  cosmetic admin-panel transaction count.
- (`modules/social_auto_reply/employee_lookup.py`'s two similarly-shaped
  functions have the same bug but are dead code, zero callers — no live
  impact, not prioritized.)

**`escort_roster_audit_logs` relation missing from the test schema — found
2026-07-17 during Phase 1B.2 Stage A live-verification testing.** Caught and logged
non-fatally (`[audit] log_audit failed: relation "escort_roster_audit_logs" does not
exist`), doesn't block escort-order processing — one more instance of the same
migration-drift class above, not a new category.

**🟢 RESOLVED 2026-07-17 (Session 21 Phase 1B.2) — Escort-client identity now
converges across its three intended sources.** Was: found during a routing-
convergence audit requested after Phase 1B that `ESCORT_CLIENT_PHONES` was only a
downstream parser gate (not an identity source), contact-name-based detection didn't
exist, and `fazle_contact_roles.role` was the only reliable source. Fixed:
`identity_brain.detect_identity()` gained an explicit `ESCORT_CLIENT_PHONES` match
(Step 3.7) and a contact-name "escort" token match (Step 4, word-boundary matched);
a new canonical `resolve_escort_client_identity()` distinguishes verified sources
from the pre-existing keyword-only Step 6 fallback (now explicitly labeled
`unverified_message_content`, never persisted as identity); `message_router` and
`modules/escort::handle_escort_client_message()` route verified/unverified senders
differently (unverified admin drafts are labeled, `save_escort_programs()` records
`source_verified`); the webhook path (`app/main.py::_handle_bridge_event()`) now
resolves `@lid` senders via the poller's own `_load_lid_map()`, closing the
webhook/poller parity gap. Commits `7313613`, `45d23fa`, `24051fe`, `f8a1d75`. 39 new
tests, full regression sweep clean (1 pre-existing unrelated failure). Full record:
`management_decisions.md` Session 21 Phase 1B.2. **Residual, explicitly flagged, not
blocking:** the escort-order-creation path itself has not been exercised by a live
production webhook call (deliberate safety choice — see the governance entry) —
recommend confirming against the next real inbound escort order.

---

## Tier 2 (carried forward, unresolved)

- [ ] **Escort roster/lifecycle dual-write fix — ATTEMPTED 2026-07-13, REVERTED after
  finding a real concurrency hang. Diagnostic completed 2026-07-17 (Session 19 Step 5),
  still not fixed.** See "Known Broken / Conflicting Workflows" #1 above for the full
  trace and the 3-writer comparison (including a previously-undiscussed 3rd writer,
  `create_roster_entry()`). `modules/escort_lifecycle/__init__.py` (~line 315-390) still
  writes `escort_roster_entries` directly instead of calling `escort_roster`'s own
  `sync_program_to_roster()`. Worth revisiting with real concurrency-debugging time
  budgeted, not as a quick cleanup pass — diagnostic SQL ready in
  `knowledge_base/00_governance/escort_dualwrite_concurrency_debug_2026-07-17.md`.

- [ ] **NEW (Session 20, found during the payroll.html shared-fetch audit) —
  `payroll.html`'s `adminApi()` and `api()` wrappers not migrated to
  `shared-auth-fetch.js`, deferred by Owner decision.** `adminApi()`'s
  dual-prefix routing (`/users*` and `/audit*` → `/admin`, everything else →
  `/api/admin`) and auto-serialize behavior (non-string `opts.body` gets
  `JSON.stringify`'d for you) have no equivalent in `shared-auth-fetch.js` —
  needs design decision before migration, not a mechanical swap. `api()` (its
  own `/api/fpe`-prefixed wrapper, simpler but still a distinct error-handling
  shape) deferred alongside it for the same reason — not attempted this
  session. Group B (`clientBillingFetch()`, `attendanceFetch()`) and Group C
  (10 ad-hoc `fetch()` calls) are the in-progress, Owner-approved migration
  track for this page; see `management_decisions.md` Session 20 for the
  call-by-call audit and per-group authorization records.

---

## Phase 2

**Gate status (2026-07-09, see `docs/PHASE2_ENTRY_GATE.md`): Phase 2a conditional GO,
Phase 2b (billing/GL) conditional GO for the billing-only slice** (Commit D shipped
2026-07-13) — General Ledger / payroll-cost reconciliation remains its own later,
undesigned phase. Owner-approved roadmap order: dispatch first, then accounting/GL, then
AI console unification (see roadmap doc referenced in this repo's governance memory).

- [ ] Dispatch/scheduling engine (availability calendar, conflict/double-booking
  detection, ranked-assignment suggestions) — chosen by Owner to go first, before
  accounting. **Not started.** Needs an Owner scope conversation before any code —
  large, undesigned business system, not something to infer from the codebase.
- [x] Client Billing / invoicing / accounts receivable (billing-only slice) — Commit D
  (payment recording, outstanding-balance view, mark-paid, audit log, AI read-only tool,
  dashboard summary) shipped and verified live 2026-07-13.
- [ ] General Ledger / double-entry accounting — chart of accounts, debit/credit posting
  hook on `create_transaction()`, P&L/balance-sheet views, bKash/Nagad/bank-statement
  reconciliation; revenue-side GL postings (from billing) in scope alongside the
  cost-side ledger that already exists. **Not started**, needs its own Owner scope
  conversation.

## Phase 3

- [ ] AI Operating Console unified read-only tools — extend `modules/ai_readonly_tools`/
  `modules/ai_console` with safe, named, SELECT-only tools over the new dispatch and GL
  data (never raw SQL, matching the existing pattern). **Blocked on Phase 2 landing.**
- [x] RAG over curated KB — done 2026-07-11 (`knowledge_base/*.md` indexed alongside
  `resources/*.txt`, 8,878 total chunks, `kb_file:` chunks role-gated to
  `admin`/`developer`). The `knowledge_base`-exact-match-vs-`rag`-semantic-search
  coordination question remains genuinely open but non-blocking.
- [ ] Evidence-backed owner queries — single natural-language query surface in
  `open-chat.html` across payroll, escort, accounting, and dispatch, with citations.
  **Blocked on Phase 2 landing.**

---

## Full history (Sessions 1-13, Tier 0/1, and detailed "Done" log)

Everything before Session 13 (2026-07-15) — the full P0/P1/P2/Tier-0/Tier-1 detailed
"Done" log, the Priority Ranking from 2026-07-12, escort program status-casing fix,
Office Payroll Hardening build, client billing profile build, invoice numbering, RAG
rebuild-lock fix, RBAC dedup, etc. — is unchanged from the prior revision of this file and
has been moved to `docs/NEXT_IMPLEMENTATION_QUEUE_ARCHIVE_2026-07-16.md` to keep this
file scannable. Read the archive for full detail on any item referenced above by name
only; nothing in the archive is still open — every item there is either `[x]` done or the
one `[ ]` (dual-write fix) that's carried forward into this file's Tier 2 section above.
