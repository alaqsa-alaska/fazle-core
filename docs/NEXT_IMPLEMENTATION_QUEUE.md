# Next Implementation Queue — Fazle Core

**Last updated:** 2026-07-09
**Source:** `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` (10-report audit + two fix passes). Read that document's "Current Consolidation State" section for full detail on anything below — this file is a queue, not a report.

**How to use this file:** work top to bottom within a tier. Don't start Phase 2/3 items while Tier 0/1/2 items remain open — that repeats the exact "freeze declared, freeze not held" failure mode the consolidation effort exists to fix (see `BUSINESS-CONSTITUTION.md` Article VII).

---

## Done

- [x] Leaked git-tracked credentials scrubbed (`DEPLOYMENT_SUMMARY.md`, `HR_OFFICER_SETUP.md`, `migrations/002_add_hr_officers.sql`, `CHAOS_TEST_PLAN.md`, `scripts/setup_hr_officers.py`) — found a 3rd leaked key (`INTERNAL_API_KEY` itself) beyond the original 2 during the search
- [x] RBAC fail-open fixed in `modules/admin_commands/__init__.py` (now fails closed on `rbac.check_permission()` exception)
- [x] `drafts_router`/`social_auto_reply_router` remounted in `app/main.py` (were silently dropped by in-flight uncommitted work; last committed version had them mounted)
- [x] Migration governance documentation added (`knowledge_base/06_developer_system/database_rules.md`, `db/MIGRATION_INVENTORY.md`) — declares `db/migrations/`+`db/migrate.py` canonical going forward, no files renamed/renumbered
- [x] 27-table schema-bootstrap migration added (`db/migrations/029_bootstrap_core_tables_schema_record.sql`) — built from live `pg_dump`, not guessed from `tests/conftest.py`; found 3 more undocumented tables while validating (was 24, now 27)
- [x] Client Billing System scope decision documented (Owner: in scope for Phase 2b — invoicing, AR, client payment tracking, revenue GL postings)
- [x] `AUTO_REPLY_ENABLED` KB-vs-`.env` reconciliation — confirmed this was never actually an open question (Owner decided `true` on 2026-06-23 per `management_decisions.md` S11-02); 3 stale KB articles corrected with dated notes
- [x] Commit-readiness file categorization completed (see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`'s current-state section §3)
- [x] Test-harness shared-`asyncpg`-pool bug fixed (`tests/conftest.py`, test-only change) — confirmed via full-suite run, 0 "pool is closed" errors remaining
- [x] Credential rotation plan prepared (not executed) — see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`'s "Release Preparation State" section. **Correction found while preparing it: only `INTERNAL_API_KEY` is a live risk** — the 2 HR-officer API keys' hashes don't match any current `fazle_admins` row, and the 2 passwords are never read by any code path at all. Only 1 real rotation needed, not 5.
- [x] Commit-grouping plan with hunk-level analysis done — `admin_commands/__init__.py` cleanly splittable via `git add -p`; `app/main.py`'s fix is line-interleaved with unrelated pre-existing changes in one contiguous hunk and is **not** cleanly splittable without manual patch surgery
- [x] Pre-restart validation run — syntax/tests/secret-scan/migration/xfail all PASS; found the still-open `toordinal` bug (below) as a restart-timing consideration
- [x] Deployment/restart gate designed (not implemented) — `docs/DEPLOYMENT_RESTART_GATE.md`

## Immediate Next

- [ ] **Rotate `INTERNAL_API_KEY`** (the only credential with real live risk — see correction above). Do not execute without Owner go-ahead; plan is in `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`.
- [ ] **Fix the escort-roster `toordinal` date-parsing bug** (`modules/escort_roster/db.py`, `start_from`/`start_to` passed as raw strings into a date column) **before the next restart** — it's still sitting unfixed in the same uncommitted working tree as the RBAC/router fixes, so a restart today would fix those two regressions while simultaneously introducing this one live for the first time. Not yet code-fixed as of 2026-07-09.
- [ ] **Commit-grouping decision** — accept a mixed `app/main.py` commit (recommended, manual hunk-splitting carries real risk) or request manual patch surgery explicitly
- [ ] Hygiene: remove the 2 dead `HROFFICER_*_APIKEY`/`_PASSWORD` `.env` values (no security urgency, just clutter — see rotation-plan correction above)
- [ ] Commit-or-explicitly-abandon the 5 untracked migration files (`020`, `023`, `027`, `028` from the original audit, plus the new `029`)

## Tier 1

- [ ] Implement the deployment restart gate per `docs/DEPLOYMENT_RESTART_GATE.md` (design exists; not yet built or wired in)
- [ ] Working-tree cleanliness check before restart (same gate, or a separate CI/pre-restart step)
- [ ] No-new-xfail-count check (block a release if `xfail` count in `tests/unit` grows without an explicit, dated waiver)
- [ ] Migration path enforcement (mechanically block new `.sql` files outside `db/migrations/`, e.g. via a pre-commit hook)
- [ ] Secret scan before commit (gitleaks/trufflehog) — would have caught Task 1's leak automatically

## Tier 2

- [ ] **RAG restart-blocking rebuild** (found 2026-07-09 while validating a restart) — `modules/rag/__init__.py`'s `build_index()` holds `_IDX.lock` for the entire Qdrant vector rebuild, which re-encodes the full ~4,400-chunk KB corpus with a CPU-only sentence-transformer on every restart (no persisted embedding cache). Observed taking 10+ minutes at this corpus size, during which **both** `/rag/stats`/`/admin/overview` (admin-facing) **and** `rag.search()` (the customer-facing RAG reply path via `message_router`) block on the lock. Not a deadlock — confirmed via CPU monitoring that it's actively computing and does complete — but a real availability gap on every restart as the KB grows. Fix: persist embeddings (e.g. rely on Qdrant's own stored vectors instead of re-encoding from scratch when the corpus hasn't changed) or move the encode step off the lock/off the event loop's critical path.
- [ ] Escort roster/lifecycle dual-write fix — `escort_lifecycle` writes `escort_roster_entries` directly instead of calling `escort_roster`'s own sync API (`modules/escort_lifecycle/__init__.py` ~line 315-343)
- [ ] Dual pay-formula reconciliation — `payment_workflow`'s formula vs. `escort_roster/calculations.py`'s `calculate_pay()` feed the same table; needs a runtime/product check before Phase 2a starts, not just a static read
- [ ] Cross-directory migration collision cleanup — `fpe_cash_transactions`, `fpe_income_transactions`, `escort_roster_entries`, `wbom_escort_programs`, `fazle_payment_drafts` are each touched by more than one of the 3 governed migration directories
- [ ] Frontend shared fetch/auth utility — 6 dashboard HTML pages each hand-roll their own `X-Internal-Key` fetch wrapper; no shared JS file exists anywhere in `app/static/`
- [ ] RBAC auth-dependency deduplication — the same `_require_api_key`-shaped check is copy-pasted in ~10 route files instead of one shared dependency

## Phase 2

- [ ] Dispatch/scheduling engine (availability calendar, conflict/double-booking detection, ranked-assignment suggestions) — chosen by Owner to go first, before accounting
- [ ] Client Billing / invoicing / accounts receivable — Owner-decided scope addition to Phase 2b (2026-07-08): invoice generation, AR tracking, client payment/outstanding-balance tracking, link from completed escort/guard service to the resulting invoice
- [ ] General Ledger / double-entry accounting — chart of accounts, debit/credit posting hook on `create_transaction()`, P&L/balance-sheet views, bKash/Nagad/bank-statement reconciliation; revenue-side GL postings (from the billing item above) in scope alongside the cost-side ledger that already exists

## Phase 3

- [ ] AI Operating Console unified read-only tools — extend `modules/ai_readonly_tools`/`modules/ai_console` with safe, named, SELECT-only tools over the new dispatch and GL data (never raw SQL, matching the existing pattern)
- [ ] RAG over curated KB — runtime-ingest the actual curated `knowledge_base/` tree into RAG (today it only indexes `resources/*.txt`); resolve the `knowledge_base`-exact-match-vs-`rag`-semantic-search coordination gap first
- [ ] Evidence-backed owner queries — single natural-language query surface in `open-chat.html` across payroll, escort, accounting, and dispatch, with citations
