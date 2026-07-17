# Next Implementation Queue — Fazle Core

**Last updated:** 2026-07-17 — reviewed against `git log` through commit `3582821`
(Session 18, 2026-07-16) and `knowledge_base/00_governance/management_decisions.md`
Sessions 13-18. Everything through Session 18 is committed on `main` (66 commits ahead
of `origin/main`, working tree clean except an intentionally-untracked
`db/migrations_drafts/047_*.DRAFT-DO-NOT-APPLY`, which is a **rejected, dead** migration
kept only as a record — see "Resolved/closed since last review" below, not a pending task).

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

1. **Escort-roster/lifecycle dual-write concurrency hang — unresolved, needs dedicated
   concurrency-debugging time.** See "Known Broken / Conflicting Workflows" below for the
   full call-path trace. Not a quick pickup — budget real time for `pg_locks`/
   `pg_stat_activity` inspection during a live hang, not another blind refactor attempt.
2. **Escort `destination` field leak (S/N 900/901/905/906) — open, undiagnosed, unfixed.**
   `knowledge_base/00_governance/escort_destination_field_leak_issue_2026-07-16.md`. Next
   step is a *scoped investigation* (find the 4 source admin messages in a wider search —
   they weren't in `wbom_whatsapp_messages`'s narrow window checked so far — then trace
   `_create_confirmed_from_admin()`'s field mapping around `modules/escort/__init__.py:1650-1680`),
   not a code change yet. KB-first proposal required before touching the parser.
3. **Historical rows 703, 707, 734 — flagged for manual admin review, Owner declined to
   guess.** Raw text is genuinely ambiguous (unclaimed phone-like number near the lighter
   name in 703/707; no mother-vessel label at all in 734). See
   `escort_sender_validation_audit_phase2_2026-07-16.md` §3 review note. Needs an admin
   with domain knowledge of the actual vessel/booking, not another automated pass.
4. **`chat_history` sync (Phase 1 of `docs/TASK_BRIEF_chat_sync_and_reply_gap_2026-07-15.md`)
   — code finished and validated, still not deployed.** Staged at `~/chat_sync_staging/`
   (`sync.py` + a systemd unit file, not installed). Blocked on `/opt` write access
   requiring `sudo`, not available in-session as of 2026-07-15. Needs a session with sudo
   to actually install and start the service.
5. **Frontend shared-fetch migration — see "URL / Route-Based Functions Needing Attention"
   below.** Scope grew from the original "5 pages" estimate to 8 (two new admin pages
   shipped since that assessment also need it).
6. **`open-webui` docker-compose.yml Groq API key in plaintext** (`/home/azim/docker-compose.yml`,
   outside this repo) — still flagged, still not rotated. Same class of issue as the
   Postgres password (already rotated 2026-07-12), smaller blast radius.
7. **`webhook/mcp1` always-200 behavior — needs a product decision, not an inference.**
   See "URL / Route-Based Functions Needing Attention" below.
8. **LOW PRIORITY — `PUT /api/admin/employees/143` 500 error, single occurrence, no
   confirmed pattern.** Occurred 2026-07-16 20:48:46 — no traceback found in available log
   history; Pydantic type validation rules out a simple str/int mismatch (confirmed via a
   direct test: an int for an `Optional[str]` field raises a 422, not a 500). Cannot
   diagnose from logs alone. Next step when reproducing: check logs immediately after
   triggering the same edit action live to capture the traceback. Standalone bug, not part
   of any Tier or Phase — investigate opportunistically when log context is available, not
   a scoped project of work. See `management_decisions.md` Session 19 for full detail.

---

## Known Broken / Conflicting Workflows

### 1. Escort release-event dual-write — concurrent releases can hang (UNFIXED, reverted)

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

### 2. Escort `destination` field leak — parser/field-mapping conflict, unconfirmed scope

**Where:** `modules/escort/__init__.py::_create_confirmed_from_admin()` (~line 1650-1680),
the direct-create path used when an admin's confirmation message has no matching prior
draft.

```
Admin sends a direct confirmation message (no prior draft exists)
        │
        ▼
modules/escort/__init__.py::_create_confirmed_from_admin() (~1517-1662)
        │
        ├── parses fields from raw message text
        │
        ├── escort-name field  ──┐
        │                        ├─ cross paths somewhere in here
        └── destination field  ──┘  (exact line not yet found)
        │
        ▼
Result: `destination` column stores "Escort Name: <name>" instead of
a place name — confirmed on 4 rows (900, 901, 905, 906), all
`auto_created=true`, all in the 2026-06-25–06-27 window.
```

Unlike the S/N 777 defect (positively confirmed fixed by replaying the original message
through current code), the 4 source WhatsApp messages could not be located in
`wbom_whatsapp_messages` — reproducibility against current code is **open, not ruled
out, not confirmed**. Next step is finding the source messages (try `fazle_messages` /
`processed_bridge_messages`, or accept the retention window has passed), not editing the
parser blind. Full write-up: `knowledge_base/00_governance/escort_destination_field_leak_issue_2026-07-16.md`.

---

## URL / Route-Based Functions Needing Attention

### 1. `POST /webhook/mcp1` — always returns 200, needs a product decision

`app/main.py:1507`. `test_missing_sender_field_returns_422` documents that this route
returns `200` even for a structurally invalid payload (missing sender field) instead of
`422`. Plausibly deliberate ("always ack the inbound webhook so the upstream bridge
doesn't retry-storm"), but nobody has actually decided that — it's an inference either
way. **Needs an explicit Owner/product decision recorded in `management_decisions.md`**,
then either document the always-200 behavior as intentional or add real validation.

### 2. Frontend pages not yet on the shared auth-fetch utility — 8 pages, not 5

`app/static/shared-auth-fetch.js` (`FazleAuthFetch`) was built 2026-07-13 and proven on
`escort-roster.html` only. At the time, 5 other pages were flagged as the natural
follow-up. Verified current state (`grep -c "fetch(" app/static/*.html`): **8 pages**
still call the API through their own independently-reimplemented fetch/auth code, because
2 admin pages shipped after that original count:

| Page | fetch() calls | Notes |
|---|---|---|
| `payroll.html` | 16 | Largest surface, has real behavioral differences to reconcile before a blind swap |
| `kb.html` | 6 | |
| `drafts.html` | 5 | |
| `dashboard.html` | 2 | Has its own auto-JSON-serialize + logout-on-403 behavior — not a pure drop-in |
| `wa_chat.html` | 1 | Has its own 429/5xx handling — not a pure drop-in |
| `open-chat.html` | 1 | |
| `wbom-create-link.html` | 1 | New since the original "5 pages" count (S15/commit `1e1e496`) |
| `wbom-link-review.html` | 1 | New since the original "5 pages" count (commit `61dd382`) |

Each page needs individual behavioral verification after migration (no browser tooling
was available in the sessions that built the utility) — not a mechanical find-replace
across all 8 at once, per the same caution already recorded for the original 5.

### 3. `/payroll/transactions/{employee_id}` — new route, working, just documenting it exists here

Added 2026-07-16 (S14) specifically to fix the double-fetch/no-deep-link bug on the
Employees panel's "View All Transactions" button. Uses employee **ID**, not name, to
avoid display-name collisions. Not broken — listed here only because it's the most
recent URL-surfaced change and the natural place someone reviewing "recent URL-based
functions" would look first.

---

## Technical Debt

**`sqlite3.connect()` used outside `bridge_poller` — audited 2026-07-17 during Step 8
(Session 19), 4 of 5 confirmed safe, 1 confirmed NOT safe (same bug class as the
`bridge_poller` fix, just not yet applied here):**

- 🟢 **Confirmed safe** (call-site verified, not just function shape — each is a plain
  `def` helper actually dispatched via `loop.run_in_executor(None, ...)`, not merely
  written as sync and assumed safe):
  - `modules/contact_sync/__init__.py` (`_read_bridge_contacts`)
  - `modules/fazle_payroll_engine/historical_sync.py` (`_fetch_all_dm_jids_sync`,
    `_fetch_messages_sync`, `_load_lid_map_sync` — the last is called from inside
    `_fetch_messages_sync`, itself already dispatched, so nested sync-to-sync is fine)
  - `modules/fazle_payroll_engine/gap_scan.py` (`_fetch_sqlite_rows_for_diff`)
- 🔴 **`modules/escort_roster/history_sync.py:779`, inside `async def
  sync_from_bridge_sqlite()` — genuinely broken, not just "worth a future audit."** The
  `sqlite3.connect()`/`execute()`/close sequence sits directly inside the async function
  body with zero executor/`to_thread()` dispatch — the exact same bug class fixed in
  `bridge_poller/__init__.py` by Step 8 (commit `a5d3871`), just not yet applied here.
  Lower urgency than the `bridge_poller` case was, since this function backs the
  admin-triggered `/backfill-sqlite` route (`escort_roster/routes.py:536`) rather than the
  continuous inbound-message poll loop — but while it runs, it blocks the entire app's
  event loop for every other concurrent request, not just its own caller. Worth its own
  small fix (same pattern: extract a sync helper, dispatch via `asyncio.to_thread()`),
  not bundled into Step 8 since that step was scoped to `bridge_poller` only.

---

## Tier 2 (carried forward, unresolved)

- [ ] **Escort roster/lifecycle dual-write fix — ATTEMPTED 2026-07-13, REVERTED after
  finding a real concurrency hang.** See "Known Broken / Conflicting Workflows" #1 above
  for the full trace. `modules/escort_lifecycle/__init__.py` (~line 315-390) still writes
  `escort_roster_entries` directly instead of calling `escort_roster`'s own
  `sync_program_to_roster()`. Worth revisiting with real concurrency-debugging time
  budgeted, not as a quick cleanup pass.

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
