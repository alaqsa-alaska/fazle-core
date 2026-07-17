---
title: Escort roster/lifecycle dual-write — concurrency diagnostic (2026-07-17)
owner: Fazle Core Admin
status: diagnostic complete, fix still not attempted
last_verified: 2026-07-17
runtime_index: true
---

# Escort dual-write concurrency diagnostic (Session 19, Step 5)

**Scope of this doc: diagnosis and evidence-gathering only.** No code changed in
`modules/escort_lifecycle/__init__.py` or `modules/escort_roster/db.py` during
this session. See `docs/AUDIT_REPORT_2026-07-17.md` §1B-i and §2 Step 1 for the
background — a 2026-07-13 attempt to unify the dual-write into
`escort_roster`'s own API caused a real, reproducible concurrency hang under
`test_concurrent_release_events` and was fully reverted. This doc documents
the three writers precisely and records what running the existing test
against *today's* (reverted, safe) code actually shows.

## Three writers to `escort_roster_entries`, compared

| Writer | Location | SQL pattern | Atomic? | Trigger |
|---|---|---|---|---|
| **1. `escort_lifecycle`'s direct write** | `modules/escort_lifecycle/__init__.py:359-387` | Single statement: `INSERT INTO escort_roster_entries (...) SELECT ... FROM wbom_escort_programs p JOIN wbom_employees e ... JOIN fazle_payment_drafts d ... WHERE p.program_id=$1 AND d.id=$4 ON CONFLICT (program_id) DO UPDATE SET ...` | **Yes** — one statement, no prior SELECT-then-branch in application code (the SELECT is a subquery inside the same INSERT statement, so Postgres evaluates and writes atomically). This is the reason the pre-2026-07-13 code (and today's reverted code) is race-safe. | Release-event confirmation (`handle_admin_release_confirmation()` → `close_program()`-adjacent flow). |
| **2. `escort_roster.sync_program_to_roster()`** | `modules/escort_roster/db.py:198-...` | `existing = await fetch_one("SELECT id, calc_version FROM escort_roster_entries WHERE program_id = $1", ...)` (line 258) then branches: `if existing: UPDATE ... else: INSERT ...`. | **No** — classic check-then-act. Two concurrent calls for the same `program_id` can both see `existing = None`, both attempt the `INSERT` branch, and race on the table's own `program_id` uniqueness (if any) — or, if the first `INSERT` commits between the second call's SELECT and its own INSERT attempt, the second could hit a unique-constraint violation rather than gracefully upserting. **This is the function the reverted 2026-07-13 refactor tried to route `escort_lifecycle` through**, which is what surfaced this exact race under `test_concurrent_release_events`'s 10-concurrent-call burst. | Called from several places: `modules/escort/__init__.py:1363,1373`, `modules/escort_roster/history_sync.py:726,903`, `modules/escort_roster/db.py:394,409` (internal), `modules/escort_roster/routes.py:730` (admin API). Never currently called from `escort_lifecycle` (that's the whole point of the still-open Tier-2 item). |
| **3. `escort_roster.create_roster_entry()`** | `modules/escort_roster/db.py:797-...` | Two separate check-then-act sequences before its own INSERT: (a) `max_pid = await fetch_val("SELECT GREATEST(...) + 1")` (line 803) then uses `program_id = max_pid` directly — two concurrent calls could compute the identical next ID before either commits; (b) `dup = await _find_duplicate_roster_entry(...)` (line 813) then raises `ValueError` if found, else proceeds to `INSERT` (line 855) — two concurrent calls for the same vessel/lighter/date/escort could both pass the duplicate check before either commits, resulting in two rows unless a DB-level unique constraint backs this up (not confirmed either way this session — out of scope for this diagnostic). | **No** — two independent check-then-act races, of a different shape than Writer 2's (ID collision + duplicate-bypass, not update-vs-insert). | Admin-facing "manually add a roster entry" UI/API (`modules/escort_roster/routes.py:615`), not part of the release-event flow at all — a **previously undiscussed third writer**, found during the 2026-07-17 audit (`docs/AUDIT_REPORT_2026-07-17.md` §1B-i). |

**Implication for any future fix:** Writer 1 is safe today only because it never
goes through Writer 2. The 2026-07-13 attempt tried to make Writer 1 route
through Writer 2 (to eliminate the duplication) and hit Writer 2's own
pre-existing check-then-act race, which had never been exercised at that
concurrency level before because nothing called `sync_program_to_roster()`
under contention for the *same* `program_id` the way 10 simultaneous release
confirmations for one program would. **Writer 3 was not part of the
2026-07-13 refactor attempt at all** and has its own, separate race
conditions that any future consolidation effort needs to account for
independently — unifying writers 1 and 2 doesn't touch writer 3's ID-collision
or duplicate-bypass risk.

## Test run against current (reverted, safe) code

```
$ pytest tests/workflows/test_escort_payment_flow.py::TestRaceConditions::test_concurrent_release_events -x -v -s
collected 1 item
tests/workflows/test_escort_payment_flow.py .
1 passed in 2.72s
```

**Result: PASSED, no hang.** This is expected, not a new finding — this test
currently exercises **Writer 1 only** (the atomic, already-safe, already-shipped
code), because `escort_lifecycle` still doesn't call `sync_program_to_roster()`.
The 2026-07-13 hang was specific to a version of the code that no longer
exists (the reverted refactor that made `escort_lifecycle` call Writer 2). A
passing test here confirms today's shipped code remains safe under this
specific concurrency test — a reasonable sanity check — but it does **not**
provide new evidence about whether a *future* consolidation attempt would
reproduce the hang, because reproducing that would require re-introducing
(even temporarily, for testing) the SELECT-then-branch unification pattern
into `escort_lifecycle` — which this diagnosis-only step is explicitly not
authorized to do (per the task's own scope: "No refactor. No code change to
the escort-lifecycle or escort-roster write paths").

No `pg_locks`/`pg_stat_activity` snapshot was captured, because there was no
hang window to observe — the diagnostic SQL from `docs/AUDIT_REPORT_2026-07-17.md`
Part 2 Step 1 remains ready to use the next time someone actually attempts
the consolidation refactor and needs to debug a live hang.

**Environment note:** the test DB (resolved by `tests/conftest.py` to
`172.20.0.6:5432/fazle_test` by default, overridable via `TEST_DATABASE_URL`)
was unreachable via a raw TCP check from this session's shell
(`/dev/tcp/172.20.0.6/5432` → connection refused) but *was* reachable when
`pytest` itself opened the connection with the correct `TEST_DB_PASSWORD` set
— likely a DNS/hostname resolution difference between a raw IP probe and
however the actual container network name resolves for this process. Set
`TEST_DB_PASSWORD` (same value as the production `DATABASE_URL_TEMPLATE`'s
password, per the 2026-07-12 rotation notes — no separate test-DB credential
exists) before running any test in this repo that touches the DB.

## Recommended next step for an actual fix session (not done here)

1. Temporarily reintroduce a minimal version of the 2026-07-13 refactor
   (`escort_lifecycle` calling `sync_program_to_roster()`) in a throwaway
   branch, specifically to reproduce the hang with today's tooling access
   (DB now confirmed reachable, unlike whatever blocked the original 2026-07-13
   session's follow-up debugging).
2. While the test hangs, run the diagnostic SQL from
   `docs/AUDIT_REPORT_2026-07-17.md` Part 2 Step 1 against the test DB to
   capture a real `pg_locks`/`pg_stat_activity` snapshot.
3. Only then design the actual fix — likely making `sync_program_to_roster()`
   itself atomic (a single `INSERT ... ON CONFLICT (program_id) DO UPDATE`,
   matching Writer 1's pattern) rather than its current SELECT-then-branch,
   which would make it safe for Writer 1 to finally call it.
4. Separately, decide whether Writer 3's ID-collision and duplicate-bypass
   races need fixing in the same pass or as their own follow-up — they're a
   different bug class (manual-entry admin tool, not the release-event path)
   and lower urgency, but now documented so they aren't lost.
