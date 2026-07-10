# Fazle Core — System Consolidation Master Plan

**Version:** 1.0
**Date:** 2026-07-08
**Status:** DRAFT — for Owner review and sign-off
**Authority:** Prepared for Owner review; supersedes no existing governance document — operationalizes `BUSINESS-CONSTITUTION.md` Article VII and the previously-approved roadmap (`~/.claude/plans/now-you-are-in-squishy-chipmunk.md`, 2026-07-08)
**Scope:** Complete architectural, operational, and risk audit of `/home/azim/core` in support of the System Consolidation Sprint mandated by `BUSINESS-CONSTITUTION.md` Article VII §7.1-7.3

**Method note:** Every claim below carries file:line or command-output evidence gathered by read-only inspection (no production changes made in the course of this audit). Where evidence was insufficient, findings are explicitly marked **Not verified** rather than guessed. Nothing here proposes new architecture, new frameworks, or feature work — every recommendation is either a consolidation of what already exists (per BUSINESS-CONSTITUTION.md's Additive-Only Rule) or a fix to a defect already present.

---

## How to read this document

10 reports, in this order:

1. Current Architecture Map
2. Module Dependency Matrix
3. System Consolidation Plan (duplicate-logic classification)
4. Migration Governance
5. Production Readiness Audit
6. Enterprise Gap Analysis
7. Execution Order
8. Risk Register
9. Technical Debt Register
10. Master Execution Blueprint

Reports 1-5 and 8 were produced by independent, parallel, read-only research passes over the live codebase and host on 2026-07-08. Reports 6, 7, 9, and 10 are this document's own synthesis across all of them. Cross-references between reports are called out inline.

---

## Current Consolidation State After 2026-07-08 Fix Pass

**Read this section first if you are a coding agent picking this up cold.** It tells you what's already done, what's still open, and what NOT to redo.

### 1. Original condition before fixes
The 10-report audit below found: two live admin API keys + a master `INTERNAL_API_KEY` leaked in plaintext across 5 git-tracked files (confirmed still active); RBAC failing **open** (not closed) on the WhatsApp admin-command channel on any `rbac.check_permission()` exception; `drafts_router`/`social_auto_reply_router` silently dropped from `app/main.py` by in-flight uncommitted work (last committed version had them mounted); migration schema scattered across 4 ungoverned mechanisms with real cross-directory `ALTER TABLE` collisions; 27 production tables (not 24 — 3 more found mid-fix) with zero schema source of truth outside a guessed test fixture, including 2 confirmed live writers (`fazle_error_log`, `llm_conversation_log`); `AUTO_REPLY_ENABLED` documented as `false` in 3 KB articles while live/`.env`/the actual governing decision log all said `true`; and a pre-existing test-harness bug where a shared module-global `asyncpg` pool wasn't reset between tests, causing order-dependent false failures.

### 2. What was fixed (both fix passes, chronological)
**Pass 1 (2026-07-08):** (1) scrubbed leaked credentials from 5 git-tracked files, found a 3rd leaked key (`INTERNAL_API_KEY` itself) beyond the original 2 during the search; (2) fixed RBAC fail-open in `modules/admin_commands/__init__.py`, added regression tests; (3) restored `drafts_router`/`social_auto_reply_router` mounting in `app/main.py`, added HTTP-level regression tests; (4) added migration governance documentation (no schema changes); (5) generated `db/migrations/029_bootstrap_core_tables_schema_record.sql` from live `pg_dump`, found 3 more undocumented tables while validating (27 total, not 24), verified end-to-end (fresh scratch DB + idempotent re-run + rolled-back production dry-run); (6) documented the client-billing scope decision (Owner: in scope).

**Pass 2 (2026-07-09):** (1) reconciled `AUTO_REPLY_ENABLED` — confirmed the authoritative decision log (`management_decisions.md` S11-02, 2026-06-23) already resolved this in favor of `true`; the 3 KB articles saying `false` were simply never updated after that session and have now been corrected with dated correction notes, not silently rewritten; (2) produced a full commit-readiness categorization of every modified/untracked file (see `git log`-adjacent report in this session's transcript — not duplicated here to avoid drift, re-run `git status --short` and categorize fresh if this file is stale); (3) fixed the test-harness shared-pool bug in `tests/conftest.py` (test-only change, zero production code touched) — confirmed via full-suite run that "pool is closed" errors are now zero, with only pre-existing, already-documented schema-drift failures remaining.

### 3. Exact files changed (both passes combined)
Credential redaction: `DEPLOYMENT_SUMMARY.md`, `HR_OFFICER_SETUP.md`, `migrations/002_add_hr_officers.sql`, `CHAOS_TEST_PLAN.md`, `scripts/setup_hr_officers.py`. Code fixes: `modules/admin_commands/__init__.py` (RBAC — **mixed with pre-existing unrelated in-flight changes, see §6 below**), `app/main.py` (router mount — **also mixed**), `tests/conftest.py` (pool fix, clean). New/updated docs: `knowledge_base/06_developer_system/database_rules.md`, `knowledge_base/00_governance/master_execution_plan_v2.md`, `knowledge_base/06_developer_system/identity_integration.md`, `knowledge_base/06_developer_system/runtime_gateway_flags.md`, `db/MIGRATION_INVENTORY.md`, this file. New migration: `db/migrations/029_bootstrap_core_tables_schema_record.sql`. New tests: `tests/unit/test_drafts_router_mounted.py`, plus additions to `tests/unit/test_admin_commands.py`. Stale-notice banners from an earlier session: `AUDIT_REPORT.md`, `AUDIT_SUMMARY.json`, `TESTING_STATUS.md`.

### 4. What was validated
Every credential redaction re-scanned (zero matches remain). RBAC fix: 4 new tests (1 end-to-end, 3 isolated), all passing where the test-DB schema drift doesn't block them. Router mount fix: 2 new HTTP-level tests, both passing; confirmed via live schema inspection that the routes were previously served by the old in-memory process and would have vanished on restart. Migration 029: fresh scratch-DB creation (0 errors), idempotent re-application (0 errors), rolled-back dry run against real production (0 errors, nothing permanently changed). Pool fix: full `tests/unit -m unit` run, grepped for "pool is closed" — 0 occurrences (was present before the fix).

### 5. What was NOT changed
`.env` (mtime unchanged throughout both passes), production credentials/API keys (deliberately left unrotated per Owner instruction — rotation is a separate, later, explicit step), RBAC records, production database schema (only a disposable scratch DB and a rolled-back transaction were used to validate), any of the ~40 pre-existing uncommitted in-flight files (left exactly as found), `fazle-core.service` (never restarted — `ActiveEnterTimestamp` unchanged at `2026-07-05 07:18:43` across both passes).

### 6. Remaining risks
- Leaked credential **values** are still the live ones — only the git-tracked *documentation* was scrubbed; rotation is still pending and is an explicit separate step.
- `app/main.py` and `modules/admin_commands/__init__.py` each contain my fix mixed with unrelated pre-existing in-flight hunks in the same file — committing either whole-file bundles reviewed/tested work with un-reviewed work; needs `git add -p` (hunk-level staging) if separation is wanted.
- Cross-directory migration collisions (Report 4) and the dual pay-calculation-formula issue (Report 3 §7) remain unresolved.
- The test-harness's SQL statement-splitter (`tests/conftest.py::_split_sql_safe`) is fragile against `DO $$...$$` guarded blocks — produced 676 tolerated-but-harmless errors when applying migration 029 in the test schema rebuild (confirmed all 27 tables still got created correctly despite the noise); this is pre-existing behavior (the same splitter already tolerates errors on migrations 010/020/021/024 etc.), not something either fix pass introduced, but worth a future cleanup pass.

### 7. Remaining Tier 0 tasks (from Report 7)
Commit-or-explicitly-abandon the untracked migration files — now 5, not 4 (`020`, `023`, `027`, `028` from the original audit, plus my new `029`). Decide on the `app/main.py`/`admin_commands.py` hunk-splitting question above before committing either.

### 8. Next Tier 1 tasks
Working-tree deployment gate (pre-restart hook refusing a dirty `git status`); tag-based deploy design; enforce single migration path going forward (already documented, not yet enforced mechanically); add a pre-commit secret scan (gitleaks/trufflehog) so Task-1-style leaks can't recur silently.

### 9. Known test fragility
(a) The shared-pool bug is now fixed (§2/§4 above). (b) The SQL-splitter DO-block issue (§6 above) is newly documented but not fixed — low priority since it's cosmetically noisy, not functionally broken. (c) The underlying schema-drift issues (`fpe_employees.canonical_employee_id`, `fazle_draft_replies.sent_at`/`error_text` missing in the test DB) remain unresolved and pre-existing — confirmed via `git stash` isolation to predate both fix passes; tracked in Report 7 Tier 3 Task 8 (test-coverage triage), not yet started.

### 10. Production restart status
**Not restarted.** Same process since `2026-07-05 07:18:43` throughout both fix passes. Several of this session's fixes (router mounting, RBAC fail-closed) will only take effect on the *next* restart — until then, the live process runs whatever code was loaded at 07:18:43 on 2026-07-05, not the current working tree.

### 11. Git commit/push status
**Zero commits, zero pushes, across both fix passes.** Repository remains 1 commit ahead of `origin/main` (unchanged from session start). ~40 modified + ~20 untracked files are sitting in the working tree — see the Task 2 commit-readiness categorization for the breakdown (pre-existing in-flight work vs. this session's clean additions vs. the 2 mixed files).

### 12. Owner decisions still required
- **Credential rotation timing** — see §"Release Preparation State" below for a corrected risk picture: only `INTERNAL_API_KEY` is a live risk; the 2 HR-officer keys/passwords are already dead/unused.
- **`app/main.py`/`admin_commands.py` commit strategy** — accept a mixed commit, or take the `git add -p` hunk-splitting route (see the release-prep section below for exactly which hunks split cleanly and which don't).
- **Migration reconciliation execution** — Report 4's recommended strategy (port `migrations/` and `fazle_payroll_engine/migrations/` into `db/migrations/`, dedupe the `social_auto_reply` schema definition) is documented but not started; needs Owner sign-off on timing.
- **Dual pay-calculation-formula resolution** (Report 3 §7) — needs a runtime/product check, not just a static read, before Phase 2a (dispatch engine) work begins.
- ~~`AUTO_REPLY_ENABLED` current-value decision~~ — **resolved this pass, no longer open**: the Owner already decided this on 2026-06-23 (S11-02); it was a documentation staleness issue, not an undecided question.

---

## Release Preparation State After Credential/Restart Planning

**Date:** 2026-07-09. This section covers a follow-up pass that prepared (but did not execute) the next release step after the Current Consolidation State section above.

**What was inspected:**
- The 3 "leaked" API keys + 2 passwords, cross-referenced against the **live** `fazle_admins` RBAC table (not just `.env`) — not done in the prior pass, which only confirmed the values matched what was in the git-tracked docs.
- Every diff hunk in `app/main.py` (13 hunks) and `modules/admin_commands/__init__.py` (2 hunks), to determine which are this session's reviewed/tested fixes vs. pre-existing in-flight work.
- Full pre-restart validation: syntax check on all modified/new `.py` files, RBAC fail-closed tests, drafts-router-mounted tests, a fresh secret scan, a rolled-back-transaction re-validation of migration 029 against real production, and an `xfail`-count check against the documented baseline.
- The rest of the uncommitted working tree, specifically checking whether any other known-but-unfixed issue would go live on the next restart.

**Correction found — credential risk was overstated in the prior pass.** Only `INTERNAL_API_KEY` is a real, live risk (checked via direct string comparison, no DB record needed — anyone with the leaked value has full master access right now). The 2 HR-officer API keys' sha256 hashes **do not match any row** in the current `fazle_admins` table — the live `officeassistant01`/`02` accounts (created 2026-06-27) use different keys never captured under these names in `.env`. The 2 passwords are **never read by any application code path at all** (`grep` across `app/`/`modules/` returns zero hits) and the corresponding accounts have no `username`/`password_hash` set in the DB. Net effect: **1 real rotation needed, not 5** — the other 4 are dead values worth cleaning up as hygiene, not urgent security work.

**Hunk-split finding:** `modules/admin_commands/__init__.py` splits cleanly via `git add -p` (2 hunks, ~600 lines apart — the RBAC fix and an unrelated pre-existing status-string change). `app/main.py` does **not** split cleanly — the 2-line router-mount fix sits inside one large, contiguous hunk that is line-by-line interleaved with ~8 unrelated pre-existing changes (FastAPI constructor edit, log-line removal, `preflight_guard_router` addition, middleware/import removals, a rename). Manual patch editing (`git add -p`'s `e` option) could isolate it but carries real risk of a malformed patch — recommendation is to accept a mixed commit for this one file rather than attempt manual hunk surgery without explicit approval.

**Pre-restart validation: CONDITIONAL PASS, not unconditional.** Every check specific to this session's fixes passed (syntax, RBAC tests, drafts-router tests, route-table presence, secret scan, migration 029, xfail count). But the pre-restart check also surfaced that **the escort-roster `toordinal` date-parsing bug (found and analyzed in an earlier session) is still unfixed**, sitting in the same uncommitted working tree as the fixes in this pass. The currently-running process (since 2026-07-05 07:18:43) predates that bug's introduction, so it isn't live yet — but a restart today would fix the router/RBAC regressions **and simultaneously introduce the toordinal bug live for the first time**, since a restart loads the whole working tree, not just the reviewed fixes. This is an Owner-level trade-off to accept knowingly, not something to bundle in silently.

**What is safe to commit:** the 6 groups from the Task-2-style commit plan — credential redaction, RBAC fix + tests, router-mount fix + tests (`app/main.py` as a whole, accepting the mix), migration governance + schema record, documentation, and the test-harness pool fix.

**What is unsafe to commit whole-file without further care:** nothing beyond the already-noted `app/main.py` mixing — that file is safe to commit *as a whole* (it doesn't corrupt anything), just not cleanly *attributable* to this fix pass alone without accepting the mix.

**What needs Owner approval before proceeding:** rotating `INTERNAL_API_KEY` (real risk, real disruption to anything using that key); deciding whether to fix the `toordinal` bug before or accept it alongside the next restart; the mixed-commit strategy for `app/main.py`; timing for the migration-reconciliation and dual-pay-formula work already queued.

**Whether restart is recommended:** not yet, as a default — recommend fixing the `toordinal` bug first so the next restart is a clean win (fixes go live, no new regression rides along). If timeline pressure makes that impractical, restarting anyway is a legitimate call, but should be made knowingly.

**Whether credential rotation is ready:** the *plan* is ready (see the rotation plan in this session's own record — masked values, exact `.env` variable, verification commands, rollback). Execution is explicitly gated on Owner approval, per instructions, and hasn't happened.

**Exact next action for the Owner:** two independent decisions, either order — (1) approve `INTERNAL_API_KEY` rotation (low-risk, low-effort, single `.env` line + restart), and (2) decide whether the `toordinal` bug gets fixed before the next restart or accepted alongside it. Neither blocks the other.

---

# REPORT 1 — Current Architecture Map

**Scope note:** Read-only research. Every claim is backed by a file path + line number or a command run against the live host. Where evidence was insufficient, marked **Not verified**.

## 0. Method

Direct inspection of `/home/azim/core`: `app/main.py` (3,703 lines, read in full), `app/config.py` (295 lines), `app/database.py` (84 lines); all 63 `modules/*` packages scanned for size, docstrings, and import graph (two-pass grep — a naive `from modules\.` pattern misses the `from modules import X` style used in several places, e.g. `observability`, `ollama_memory`, `reviewed_reply_memory`, `rbac`); all 21 `shared/*.py` files checked for actual inbound callers, not just their own docstrings' claims; `knowledge_base/06_developer_system/database_rules.md`; live nginx config (`/etc/nginx/sites-enabled/fazle.iamazim.com.conf`, `/etc/nginx/upstreams/fazle-api.conf`) and `fazle-core.service`/`run.py`; `app/static/*.html` cross-referenced against `app.include_router()` calls.

## 1. Frontend Layer

`app/static/` — 9 HTML pages, no build step, no framework, served directly from the working tree: `dashboard.html`, `drafts.html`, `escort-roster.html`, `kb.html`, `open-chat.html`, `payroll.html`, `ux-preview.html`, `ux-preview-v2.html`, `wa_chat.html`, plus `manifest.json`, `service-worker.js`, `assets/`.

Served via `app.mount("/static", StaticFiles(...))` (`app/main.py:440`) plus individual `FileResponse` routes per page (e.g. `/drafts` → `app/main.py:1811`, `/kb` → `:1813-1816`, `/payroll.html` → `:1782`, `/escort-roster.html` → `:1804-1807`, `/wa-chat*` → `:1826-1838`).

**🔴 Verified live bug — the standalone Drafts dashboard is calling an unmounted API.**
`modules/drafts/routes.py` defines `/api/drafts/stats`, `/api/drafts`, `/api/drafts/{id}/approve|edit|delete|block` (lines 55, 71, 127, 189, 220, 243). `app/main.py:48` imports it as `drafts_router`, but the repo's only 8 `app.include_router()` calls (`main.py:441-450`) are `fpe_router`, `escort_roster_router`, `kb_upload_router`, `admin_employees_router`, `admin_transactions_router`, `contact_roles_router`, `preflight_guard_router`, `wa_chat_router` — **`drafts_router` is never mounted**. Meanwhile `app/static/drafts.html` (served at `/drafts`) calls exactly these unmounted paths (`drafts.html:302,321,341,343,430,436,443,467`). `main.py` separately implements its own, differently-shaped `/admin/drafts` endpoint (`main.py:2446-2455`) which `dashboard.html` correctly calls instead. **On this evidence, the dedicated Drafts page's approve/edit/delete/block buttons 404 in the current tree.** Recommend a live `curl /api/drafts/stats` check to confirm before treating as settled fact, since the running server was not queried during this read-only audit.

## 2. API Layer (`app/main.py`)

3,703 lines. Per its own header docstring: *"This file only handles HTTP transport, signature verification, and delivery… All message routing is delegated to modules.message_router.process_message()."* True for the WhatsApp path, but `main.py` also contains substantial inline business logic: `/admin/drafts`, `/admin/payment-drafts`, `/admin/overview`, `/backup/*`, `/reports`, `/observability/*`, RBAC admin endpoints (`main.py:2609-2638`), and the `X-Internal-Key` auth dependency itself (`main.py:200-203`).

**At least 8 separate re-implementations of the same single-shared-secret header check** exist across route modules (`admin_employees`, `admin_transactions`, `contact_roles`, `drafts`, `escort_roster`, `fazle_payroll_engine`, `kb_upload`, `preflight_guard`, `social_auto_reply`) rather than one shared dependency. Real RBAC (roles/audit) coexists on top of this in most of these routers — **two authorization mechanisms stacked on the same endpoints, not one.**

`social_auto_reply_router` (`main.py:53`) is imported but, like `drafts_router`, **never mounted** — its background worker still runs (started/stopped in `lifespan()`), but its admin control surface (`/status`, `/queue`, `/flagged`, `/pause`, `/resume`, `/retry`) is unreachable over HTTP.

**Dependency direction:** `main.py` imports ~15 `modules.*` packages directly plus, deferred inside `lifespan()`, 8 more `shared.*` modules. Only one reverse dependency exists and is explicitly documented as deliberate: `modules/scheduler/__init__.py:167` — `from app.main import _build_health  # late import (avoid circular)`.

## 3. Modules Layer

63 packages. Full inventory in **Report 2**. Layer-boundary observations:

- **Money flows through one canonical writer** (`fazle_payroll_engine.accounting.create_transaction()`) but is reached from 6 independent call sites (`admin_commands`, `admin_transactions`, `draft_approval`, `payment_ingest` ×3 internal, `payment_workflow`), each re-importing the same `.employee`/`.normalizer`/`.models`/`.payment_event` submodule quadruplet rather than going through one facade.
- **Private-symbol cross-module imports** (real layer violations): `modules/escort_roster/routes.py:625-626` imports `_resolve_escort_employee_id` directly from `modules.escort` — notable because `escort_roster/__init__.py:434` itself states *"DO NOT import from this module in existing modules (no circular deps)"* yet doesn't hold itself to the same rule reaching into `escort`. `modules/bridge_poller/__init__.py:40` imports `_is_safe_autosend_intent` directly from `modules.message_router`.
- **Layer inversion:** `shared/phone.py:29,39` imports from `modules.fazle_payroll_engine.normalizer` and `modules.number_identity` — `shared/` is meant to be the layer `modules/` depends on, not vice versa.
- **Five duplicate-logic clusters** (evidence only, resolution deferred to Report 3):
  1. **Payroll trio** — `modules/payroll`, `modules/payroll_logic`, `modules/fazle_payroll_engine` all reason about pay; `payroll/__init__.py:104` reads `fpe_cash_transactions` with its own inline SQL rather than an FPE accessor.
  2. **Queue/retry, built four times** — `shared/queue.py` + `shared/queue_arbiter.py` (meant to be canonical), `modules/outbound` (independent retry/backoff/DLQ), `modules/social_auto_reply/{send_queue,retry_queue}.py` (a fourth, independent set). `modules.outbound` and `social_auto_reply` do not import `shared.queue_arbiter`.
  3. **Circuit breakers, built four times** — `app/bridge.py`, `shared/bridge_orchestrator.py`, `shared/queue_arbiter.py`, `shared/frontend_sync.py` each implement their own failure-window/backoff logic, no shared base class.
  4. **Identity-detection trio** — `identity_brain` (current), `user_role` (legacy, still imported by `attendance_parser`), `role_classifier` (a third, `user_profiles`-based system). `identity_brain/__init__.py:527-528` itself acknowledges the overlap with "legacy UserRole."
  5. **Message-dedup, built three times** — `bridge_poller`'s own `processed_bridge_messages` table, `social_auto_reply/message_deduplicator.py`, `image_hash` — scoped to different ingestion channels, may be legitimately separate; flagged for the resolution team to judge.
- **🔴 Security-relevant finding — RBAC fails open on the WhatsApp admin-command channel.** `modules/admin_commands/__init__.py:251-280`: the guard around money-moving commands (`APPROVE`, `PAID`, `ADVANCE`, etc.) wraps `rbac.check_permission(...)` in try/except; on exception, it logs `"rbac check failed (failing open for now)"` and **falls through to execute the command anyway**. By contrast, every HTTP-route surface (`admin_employees`, `admin_transactions`, `contact_roles`, `drafts`, `escort_roster`, `fazle_payroll_engine`, `kb_upload`, `preflight_guard`, `social_auto_reply`, `wa_chat_frontend`) uses `rbac` as a hard `Depends()` gate — fail-closed by FastAPI's own exception handling. **The same permission system has materially different failure semantics on two different channels.**

## 4. Shared Components Layer (`shared/`)

21 files, 6,397 lines. Largest gap found anywhere in the codebase between documented intent and actual wiring:

| File | Lines | Documented role | Actual inbound callers (verified) |
|---|---|---|---|
| `queue.py` | 231 | Central inbound queue | `main.py`, `scheduler`, `fazle_payroll_engine/diagnostics.py`, `bridge_poller` — genuinely wired |
| `locks.py` | 279 | Distributed locks | Mostly reached *through* `draft_reply.py`, not called directly by business modules |
| `draft_reply.py` | 182 | Draft-insertion helper | 8 business modules — the most genuinely shared file here |
| `reply_policy.py` | 290 | Reply post-processing | `app/github_models.py`, `app/groq_provider.py`, `app/ollama.py`, `message_router` — real |
| `phone.py` | 103 | "Canonical" phone normalization | Only 2 outside callers; itself imports **upward** from `modules.fazle_payroll_engine.normalizer` and `modules.number_identity` — one of 3 phone-normalization entry points in the codebase, not the sole one its own docstring claims |
| `events.py` | 243 | Domain event bus | Only inbound caller: a diagnostics stats read — **no module actually publishes/subscribes business events through it** |
| `realtime.py` | 188 | "Phase 12" WS event bridge | Started from `main.py`; read for stats only |
| `frontend_sync.py` | 508 | "Phase 13C" sync monitoring | Started from `main.py`; **zero other callers** |
| `bridge_orchestrator.py` | 645 | "Phase 13D" bridge orchestration | Started from `main.py`; read-only diagnostics display by `ai_console` |
| `self_heal.py` | 524 | "Phase 13E" self-healing | Started from `main.py`; **zero other callers** |
| `runtime_gateway.py` | 686 | "Phase 13A" node registry | Started from `main.py`; read-only display by `ai_console` |
| `queue_arbiter.py` | 814 (largest in `shared/`) | "Phase 13B" global queue arbitration, meant to wrap `shared.queue` for every consumer | Started from `main.py`; **no module that actually dequeues messages imports it** |
| `write_router.py` | 301 | Docstring: *"ALL database writes for employees, payments, escort programs, and transactions must flow through this module"* | **Exactly one inbound caller in the entire repo** (a diagnostics stats read) — `routed_write()` itself is called by none of the real writers |
| `consistency.py` | 212 | Version-check helper for `write_router` | **Zero inbound callers outside `shared/`** |
| `aliases.py` | 260 | Contact/number alias resolution | **Zero inbound callers outside `shared/`** |
| `identity_map.py` | 359 | Identity mapping | **Zero inbound callers outside `shared/`** |
| `error_response.py` | 86 | Standard error envelope | **Zero inbound callers outside `shared/`** |
| `env.py` | 118 | Env-var helper | **Zero inbound callers outside `shared/`** |
| `draft.py` | 241 | `expire_stale_drafts()` | `scheduler` only |
| `state_version.py` | 122 | Version stamps | Diagnostics reads only |

**Conclusion:** of 21 files, ~7 are genuinely load-bearing; **~8 files totalling 3,000+ lines** (`write_router`, `consistency`, `queue_arbiter`, `frontend_sync`, `bridge_orchestrator`, `self_heal`, `runtime_gateway`, `aliases`, `identity_map`, `error_response`, `env`) are either inert background loops with no consumer or have zero callers at all. This "Phase 12/13 coordination layer" starts up and presumably logs/heartbeats, but does not intercept the actual write/message paths its own docstrings describe. **This is the single largest aspirational-vs-real architecture gap in the codebase.**

## 5. Database Layer

Single Postgres database via `app/database.py` (84 lines) — thin `asyncpg` pool wrapper (`min_size=2, max_size=10, command_timeout=30`), no ORM, no repository layer. Every module writes raw SQL inline. `database_rules.md:20` documents **4 separate schema-creation mechanisms** (numbered `db/migrations/`, `fazle_payroll_engine/migrations/`, standalone `migrations/`, inline Python DDL) — confirmed independently (`social_auto_reply/__init__.py:16+`, `bridge_poller/__init__.py:80` both `CREATE TABLE IF NOT EXISTS` inline). Full detail in **Report 4**. Table ownership (e.g. `fpe_cash_transactions`/`fpe_employee_ledger` "immutable ledger") is enforced by convention only, not by DB grants or code-level access control.

## 6. Background Jobs Layer

**A. `main.py`'s `lifespan()` (lines 252-389)** — ~20 startup steps, each independently try/excepted (non-fatal): DB init → feature flags → RBAC bootstrap → wa_chat tables → bridge pollers → outbound worker → scheduler → Ollama daemon → Groq validation → RAG index → FPE → social auto-reply → then 6 "Phase 12/13" shared-layer starts (realtime, runtime_gateway, queue_arbiter, frontend_sync, bridge_orchestrator, self_heal) → escort-roster cleanup → stale-draft-expiry worker. Steps 13-18 (the shared/ coordination layer) are started but not all symmetrically stopped on shutdown, and per §4 above, several have no consumers regardless.

**B. `modules/scheduler`** — APScheduler, ~12-13 registered jobs (`daily_payroll_compute` 02:00, `dlq_alert`, `health_summary` 6h, `agent_incident_summary` 6h, `stale_escort_reminder` 09:00, `payment_reconciliation` hourly, `backup_staleness_alert` 03:00, `combined_draft_cleanup` hourly, `daily_memory_review`, `rag_rebuild`, plus 3 more conditional jobs). Each writes to `fazle_scheduled_jobs` with a heartbeat.

## 7. External Services Layer

Ollama (primary reply provider, 5 task-tiered models) → Groq (fallback) → GitHub Models (2nd fallback, 3 rotating tokens) → fixed holding reply. Meta Graph API (WhatsApp Cloud/Messenger/FB comments). Two self-hosted WhatsApp bridges polled via SQLite (not webhooks) at `/home/azim/whatsapp{1,2}/store/*.db`, wrapped by `app/bridge.py`'s own `CircuitBreaker`. Qdrant (RAG vector store, optional server mode). A separate media-processor service (OCR/transcription). LocationWhere (sister app) — per `ARCHITECTURE.md:31-44`, writes **directly into `wbom_employees`** via its own Prisma `$executeRawUnsafe` call, a documented but one-sided coupling gated only from LocationWhere's side.

## 8. AI Components Layer

`ai_console` (2,958 lines) — "Phase 2C registry-driven skeleton," reads `shared.bridge_orchestrator`/`shared.runtime_gateway` for display; one undocumented reverse-import of `app.main.app` (`acceptance_runner.py:441`) — unlike `scheduler`'s equivalent, not commented as deliberate. `ai_readonly_tools` (878 lines) — hard SELECT-only constraint, separate `fazle_ai_reader` DB role, row-capped at 100; imports `admin_commands.date_parser` (a Critical-risk module) purely for date parsing. `ollama_memory` — fully isolated AI-owned memory DB, own DB role, one of the cleanest boundaries in the repo. `rag` (1,214 lines) — BM25+Qdrant hybrid retrieval. `admin_chat_lab` — explicitly "does not mutate production data." **This layer is one of the better-enforced boundaries in the system** — AI reads business data only through the narrow, view-gated `ai_readonly_tools` surface and writes only to its own isolated stores.

## 9. Knowledge Base Layer

`knowledge_base/` tree: `00_governance`, `01_employee_knowledge`, `02_admin_knowledge`, `02_admin_system`, `03_ai_identity`, `03_developer_system` (empty), `04_business_rules`, `05_workflows`, `06_developer_system`, `07_archived`. Duplicated numbering (`02_admin_knowledge` vs `02_admin_system`; `03_ai_identity` vs `03_developer_system` vs `06_developer_system`) suggests at least one un-migrated restructuring. Ingestion via `modules/kb_upload` (PDF/DOCX/TXT → `rag.build_index()`). Two independent retrieval paths both feed `message_router`: `modules.knowledge_base` (DB-first exact-intent lookup) and `modules.rag` (semantic search) — no coordination between them.

## 10. Deployment Layer

systemd unit `fazle-core.service`: `Type=simple`, `WorkingDirectory=/home/azim/core`, `ExecStart=/home/azim/.venv/bin/python run.py`, `Restart=always`/`RestartSec=5`. **No container, no build artifact — runs directly off the git working tree.** A `Dockerfile` exists at repo root but is **not referenced by the actual systemd path** (Not verified whether it's used anywhere in practice). nginx terminates TLS and rate-limits at `20r/s`, proxying to `127.0.0.1:8200` (a comment in `upstreams/fazle-api.conf` notes a prior dead upstream `8101` — evidence of at least one past port migration left as a comment rather than cleaned up). **No CI/build gate between a code edit and it running in production.**

---

# REPORT 2 — Module Dependency Matrix

**Methodology:** All 63 `modules/*` packages enumerated. Purpose from docstring; dependencies/dependents from two-pass repo-wide grep; risk level (Critical = touches the cash ledger or sends real WhatsApp; High = on the identity/routing critical path; Medium/Low otherwise); test coverage from `find tests -iname`. No KEEP/MERGE/DELETE calls made here (that's Report 3) — only "duplicate logic suspected" flags with evidence.

## Domain: Payroll / Financial (12 modules)

| Module | Purpose | Depends on | Depended on by | Risk | Duplicate logic suspected | Tests | Hidden coupling |
|---|---|---|---|---|---|---|---|
| **fazle_payroll_engine** | Canonical cash-transaction ledger, 19 files/8,668 lines | `app.database/config/ollama/bridge`; `shared.queue/events/realtime/write_router(diag)/state_version` | `main.py` + 9 modules; `shared/phone.py` | **Critical** | `.employee`/`.normalizer`/`.models`/`.payment_event` re-imported piecemeal by 5+ callers, no facade | `test_fpe_accounting.py`, `test_fpe_employee.py`, `test_fpe_parser.py`, `test_fpe_rules.py`, etc. | `shared/phone.py` reaches into its `.normalizer` (layer inversion) |
| **payroll** | Monthly payroll run state machine (`wbom_payroll_runs`) | `app.database` only | `main.py`, `admin_commands`, `scheduler` | **Critical** | Own inline SQL reads `fpe_cash_transactions` directly (`payroll/__init__.py:104`) instead of an FPE accessor | `test_payroll.py` | Direct cross-domain table read |
| **payroll_logic** | Read-only salary/duty Q&A, "never fabricates amounts" | `app.database` only | `message_router` | Medium (read-only) | 3rd pay-related module alongside `payroll`/FPE | None found | None |
| **payment** | 22-line re-export shim of `payment_workflow` | `payment_workflow` | **Zero production callers**, tests only | Low | It *is* the (deliberate) duplicate | Tests only | None |
| **payment_workflow** | Escort-payment/advance/salary drafts feeding FPE | `app.database/config`; `fazle_payroll_engine.*` | `main.py`, `admin_commands`, `employee_verification`, `escort_lifecycle`, `message_router`, `payment` | **Critical** | One of 5 independent FPE-import sites | `test_payment_workflow.py`, `test_escort_payment_flow.py` | None found |
| **payment_ingest** | Parses bKash/Nagad SMS + admin shorthand → canonical FPE txn; Owner Directive: FPE table is sole canonical cash table | `app.database`; `shared.phone`; `fazle_payroll_engine.*` | `main.py`, `admin_commands`, `message_router` | **Critical** | 2nd FPE-import site | **None found dedicated** | None |
| **payment_correction** | Reversal/adjustment via counter-rows; **self-documented DORMANT, 0 external callers** | `app.database`; `fazle_payroll_engine.accounting.reverse_transaction` | Confirmed zero production callers | **Critical** (if wired) | Author-flagged unused | Tests only, no production caller | REVERSE/ADJUST commands never wired into `admin_commands` |
| **draft_approval** | Sprint-3B draft→decision→`create_transaction()` | `app.database`; `fazle_payroll_engine.*` | `admin_commands` | **Critical** | 3rd FPE-import site | `test_sprint3b_draft_approval.py` | None |
| **admin_transactions** | CRUD + smart matching on `fpe_cash_transactions` | `app.database`; `fazle_payroll_engine.*` | `main.py` | **Critical** | 4th FPE-import site; own `X-Internal-Key` re-declaration; **direct UPDATE/soft-delete on `fpe_cash_transactions` outside `create_transaction()`** (edit line 698, soft-delete line 764) — Constitution boundary case, see Report 3 §1 | None found | None |
| **admin_employees** | CRUD on `wbom_employees`, never hard-delete | `app.database/config`; `fazle_payroll_engine.employee` | `main.py` | High | 5th FPE-import site; own auth re-declaration | None found | Soft-propagates deactivation to `fpe_employees` by phone — intentional |
| **accountant_summary** | Recognizes Bengali daily cash-summary messages, never writes | None | `message_router` | Low | None | `test_accountant_payment_pipeline.py` | None |
| **admin_commands** | WhatsApp text-command processor, 2,846 lines/8 files | `app.database/config/bridge`; `shared.draft_reply`; `fazle_payroll_engine.*`, `phone_normalizer`, `draft_approval`, `payment_workflow`, `attendance`, `rbac`, `observability` | `ai_readonly_tools`, `message_router` | **Critical** | **RBAC fail-open bug here** (Report 1 §3) | `test_admin_commands.py` etc. | Fail-open path is a hidden coupling risk on the money-command channel |

## Domain: Escort / Dispatch (7 modules)

| Module | Purpose | Risk | Duplicate logic suspected | Tests | Hidden coupling |
|---|---|---|---|---|---|
| **escort** | Core `wbom_escort_programs` state machine, 1,929 lines | **High** | None beyond private-export issue | Multiple, see Report 2 full detail | Exports `_resolve_escort_employee_id` (private) to `escort_roster` |
| **escort_lifecycle** | Closes duty loop → `create_escort_payment_draft()` | **Critical** | None found in isolation, but **direct raw `INSERT ... ON CONFLICT` into `escort_roster_entries`** bypassing `escort_roster`'s own API (Report 3 §7) | `test_escort_lifecycle.py` etc. | None beyond the roster-write bypass |
| **escort_roster** | Roster sync/recalc, 4,096 lines/6 files, self-documented "don't import from us" | High | Imports `escort`'s private `_resolve_escort_employee_id`, contradicting its own isolation rule | `test_escort_roster_routes.py` | Same private-import issue from this module's side |
| **escort_slip_extractor** | OCR field extraction from release slips | Medium | Overlaps `ocr_processor` conceptually | `test_escort_ocr_pipeline.py` | None |
| **location_adapter** | External LocationWhere adapter, own circuit-breaker | Medium | 4th independent circuit-breaker implementation | `test_location_adapter.py` | None |
| **attendance** | Structured keyword attendance ("হাজির"/"Present") | Medium | Overlaps `attendance_parser` (2 ingestion paths for the same concept) | `test_attendance.py` | None |
| **attendance_parser** | Free-text attendance extraction | Medium | Same overlap as above; still depends on legacy `user_role` rather than `identity_brain` | None found | Depends on identity-trio's legacy member |

## Domain: Messaging / Routing (19 modules)

Key entries (full 19 in underlying research): **message_router** (1,029 lines, single choke point by design, High risk, no duplicate — this is the intended hub); **bridge_poller** (1,829 lines, single file, Critical risk, real outbound sends, **thin test coverage for its size**, own 3rd independent dedup table); **outbound** (Critical, durable send queue with DLQ/circuit-breaker, does **not** route through `shared.queue_arbiter` despite the arbiter existing to prevent exactly the double-send risk it could cause); **wa_chat_frontend** (High risk, **no dedicated unit test file**); **drafts** (its router unmounted — see Report 1 §1 — duplicates the intent of `main.py`'s inline `/admin/drafts` endpoint); **social_auto_reply** (High risk, real sends, 21 files/2,323 lines — most files of any module, own 4th queue + 3rd dedup mechanism, router unmounted); **phone_normalizer** (widely depended on but lives in `modules/` not `shared/`, one of 3 phone-normalization entry points); **message_archive** (imported **only** by two recovery scripts — effectively dead in the live request path despite living alongside runtime packages).

## Domain: Identity / RBAC (6 modules)

**identity_brain** (High, the intended canonical resolver, acknowledges legacy overlap itself); **user_role** (Medium, being migrated away from, still has one live caller — `attendance_parser` — and is imported from a file already living under `archive/deprecated/`); **role_classifier** (3rd identity-adjacent system, LLM-context-building not routing); **rbac** (High, security-critical, widely depended on, no bug found *in* the module itself — the fail-open bug lives in one of its 11+ callers); **employee_verification** (Critical, gates payment drafts, no dedicated test file); **employee_conversation** (High, self-limited to drafts by hard docstring rule, "boundary enforced by convention only, not by code").

## Domain: Recruitment (4 modules)

**recruitment_flow** (Medium, state machine); **recruitment_ai** (Medium, "restricted recruitment AI reply brain," largely superseded per `conversation_layer`'s own docstring but helpers still imported); **recruitment_assistant** (Low, **confirmed zero callers anywhere in the repo** — orphaned scaffolding); **conversation_layer** (Medium, recruitment-scoped despite generic name, circular-ish pairing with `recruitment_flow` via deferred imports).

## Domain: AI / Knowledge (8 modules)

**ai_console** (Medium, undocumented reverse-import of `app.main.app`); **ai_readonly_tools** (Medium, hard-constrained read-only, but depends on `admin_commands.date_parser` — a Critical business module — for a generic utility); **ollama_memory** (Low, one of the cleanest boundaries in the repo, deliberately isolated); **rag** (Medium, widely depended on); **knowledge_base** (Medium, **two parallel retrieval paths exist for the same goal** — exact-match lookup vs. semantic `rag` search, neither calls the other, both independently feed `message_router`); **kb_upload**, **memory_extractor**, **admin_chat_lab** (all Medium/Low, no significant issues found).

## Domain: Scheduler / Ops (7 modules)

**scheduler** (High, central job runner, deliberate documented circular import with `main.py`); **backup** (High, data-loss-prevention critical, imports `scheduler._admin_number` — a private cross-module reach the author flagged with `# type: ignore`); **health_monitor** vs **operations_health** — **near-identical naming/purpose, but `operations_health` has confirmed zero callers anywhere in the repo** — likely superseded scaffolding; **observability** (Low, genuinely widely-shared utility, arguably misplaced under `modules/` rather than `shared/`); **preflight_guard** (High, the safety-control module governing other Critical modules — its own protected-path list is **hardcoded as strings**, so it would silently stop protecting a module if that module were ever renamed/moved without updating the list); **reports** (Medium, no significant issues).

## Summary counts

- **63 of 63 modules accounted for.**
- **Confirmed orphaned (zero real callers):** `operations_health`, `recruitment_assistant`, `payment` (shim), `payment_correction` (self-documented dormant), `message_archive` (recovery-scripts-only).
- **Confirmed dead HTTP surface despite being imported:** `modules/drafts` (router never mounted), `modules/social_auto_reply` (router never mounted; background worker still runs).
- **Zero dedicated unit tests found for roughly half the module inventory**, including several **Critical/High**-risk ones: `payment_ingest`, `employee_verification`, `scheduler` (e2e only), `wa_chat_frontend`, `bridge_poller` (one adjacent test only, for a 1,829-line send-capable module).
- **Layer violations found:** `shared/phone.py` depends upward on 2 `modules.*` packages; `escort_roster`↔`escort` private-symbol cross-imports in both directions; `bridge_poller`→`message_router` private-symbol import; ~8 of 21 `shared/*.py` files have no or diagnostic-only inbound callers despite "canonical path" docstring claims; WhatsApp-channel RBAC fails open while HTTP-channel RBAC fails closed.

---

# REPORT 3 — System Consolidation Plan (Duplicate-Logic Classification)

Classifications: **KEEP** (looks similar, serves distinct purpose) / **MERGE** (real duplication) / **DELETE** (dead) / **ARCHIVE** (historical, safe to move out) / **DEPRECATE** (working, phase out for a named replacement) / **INVESTIGATE** (needs a human/runtime check, not resolvable from static reading).

## 1. Payment-related modules — Constitution compliance check

- `modules/payment` — confirmed pure 22-line re-export shim. **ARCHIVE/DEPRECATE** — no logic of its own; keep only if external callers still exist (grep found none in production code).
- `payment_workflow.finalize_payment()`, `payment_ingest/fpe_bridge.py:132`, `wbom_fpe_sync.py:175`, `admin_commands/nl_advance_record.py:234` — all correctly build a `TransactionCreateRequest` and call `create_transaction()`. **KEEP** — compliant, single canonical path confirmed across all of them.
- **`modules/payment_correction`** — self-declares in its own header: *"MODULE STATUS: DORMANT... External callers: 0 (grep confirmed)... DO NOT DELETE without explicit confirmation from Azim first."* Independently re-verified: zero hits outside its own directory. **ARCHIVE** — confirmed dead, but the file itself vetoes deletion; move out of active tree only.
- **🟡 Constitution boundary case:** `modules/admin_transactions/__init__.py:698` (`UPDATE fpe_cash_transactions SET {...}`, transaction edit) and `:764` (soft-delete) are **direct writes to `fpe_cash_transactions` that bypass `create_transaction()`**. The ledger side is safe (`_adjust_ledger()` still calls `_upsert_ledger()` internally), and Article I §1.2 textually prohibits `INSERT` not `UPDATE`, so this isn't a clean textual violation — but it bypasses the "no bypass paths" spirit and Article II §2.1's requirement that any new path be Owner-approved and documented. **INVESTIGATE** — needs an explicit Owner ruling on whether post-creation admin edit/soft-delete counts as a "path to cash ledger" requiring Constitution documentation.
- No other module does raw `INSERT`/`UPDATE` on `fpe_cash_transactions`/`fpe_employee_ledger` outside the canonical function. **No other Constitution violation found.**

## 2. Recruitment/conversation AI modules

- `conversation_layer/__init__.py:31` states it *"Replaces modules.recruitment_ai.generate_recruitment_reply in the live router."* Verified: that function has no live-router caller (superseded), but `recruitment_ai.looks_like_recruitment_followup` is still imported at `recruitment_flow/__init__.py:345`. **DEPRECATE** — core LLM-reply function is dead/superseded, but the module can't be deleted outright since a helper is still a live import.
- `modules/recruitment_assistant` — grepped repo-wide including tests: **zero references anywhere**. **DELETE** candidate — pure orphan stub.
- `modules/employee_conversation` — confirmed genuinely distinct domain (existing employees' payment requests, hard-coded to never call `create_transaction()`). **KEEP** — not recruitment-adjacent despite naming pattern.

## 3. Identity/role modules

- `identity_brain` — the live unified resolver used by `message_router`, `rag`, `bridge_poller`, `main.py`. **KEEP**.
- `contact_roles/routes.py` — CRUD for `fazle_contact_roles`, the exact seed-rule table `identity_brain` reads first. **KEEP** — feeds one of identity_brain's inputs, not competing logic.
- `role_classifier.get_user_context()` — different data source (`user_profiles`/`user_memory`) and different consumer (LLM prompt styling) than `identity_brain`. **KEEP**.
- **`modules/user_role`** — `detect_role()`, its actual role-detection function, has **zero callers anywhere**. Its only live consumer, `attendance_parser`, imports `normalize_phone`, which is itself just a re-export of `shared.phone.normalize_phone`. **DELETE/ARCHIVE** the role-detection logic; repoint the one live import directly at `shared.phone`.

## 4. Messaging/queue infrastructure

- `modules/outbound` (durable, DLQ/circuit-breaker) and `social_auto_reply/{send_queue,retry_queue,rate_limiter,message_deduplicator}.py` are **two independently-built durable send pipelines** with independent idempotency, retry, and rate-limiting. `outbound/__init__.py:90` imports raw transport functions from `social_auto_reply.send_queue` for `meta`/`messenger`/`facebook_comment` channels, but grepping every `outbound.enqueue()` call site shows **all but one use `source_bridge="bridge1"/"bridge2"`** — the bulk of social-platform sends flow through `social_auto_reply`'s own `sweep_once()`, never touching `modules.outbound`. **MERGE** — confirmed real infra duplication; `modules.outbound`'s social-channel branch is essentially unexercised in practice.
- `shared/queue.py` + `shared/queue_arbiter.py` solve a **different problem** (inbound multi-worker coordination) than the outbound send-queue duplication above. **KEEP** — distinct problem domain, not part of the outbound duplication.

## 5. AI/console tooling overlap

Traced the actual call graph: `admin_chat_lab` → `ai_console` orchestrator/registry/model_router → `ai_readonly_tools` concrete queries, with `ollama_memory` for conversational state — **one coherent pipeline**, not duplicate implementations. `knowledge_base` (deterministic FAQ lookup) and `rag` (BM25 retrieval) serve the recruitment/reply-generation domain, separate from the admin-console pipeline. **KEEP across the board** — an honest non-finding; despite similar "AI tooling" naming, no two of these implement the same concept twice.

## 6. RBAC/auth consistency

The pattern `key == settings.internal_api_key` (else `rbac.get_admin_by_api_key()` + active-status check) is reimplemented **identically in at least 10 separate files**: `admin_transactions`, `wa_chat_frontend`, `drafts/routes.py`, `preflight_guard/routes.py`, `social_auto_reply/routes.py`, `admin_employees` (×2), `contact_roles/routes.py`, `kb_upload/routes.py`, `escort_roster/routes.py` (×2), `fazle_payroll_engine/routes.py`. Every copy routes through the same two authorities — **no bypass or weaker check found**. **MERGE** — genuine, extensive copy-paste (presumably to dodge circular imports with `app.main`), not a security bypass.

## 7. Escort modules

Pipeline confirmed: `escort` → `escort_slip_extractor` → `escort_lifecycle` (reuses `payment_workflow`'s canonical draft function, not a separate write path) → `escort_roster` (derived roster, synced via `sync_program_to_roster()`).

- **🟡 Two independent write paths to `escort_roster_entries`.** `escort/__init__.py:1330-1346` correctly calls `escort_roster.db.sync_program_to_roster()`. But `escort_lifecycle/__init__.py:315-343` does **its own raw `INSERT INTO escort_roster_entries ... ON CONFLICT (program_id) DO UPDATE`** at duty-completion time, bypassing `escort_roster`'s own functions entirely. **MERGE** — should call `escort_roster`'s API instead of reimplementing the upsert inline.
- **🟡 Two different pay-calculation formulas feed the same table.** `escort_lifecycle`'s upsert pulls figures from `payment_workflow.create_escort_payment_draft`'s formula (`basic_salary/30 × duty_days − food − conveyance − advances`). `escort_roster/calculations.py:81-90` defines an **independent** `calculate_pay()` using a flat `shift_rate=200`/shift default. **INVESTIGATE** — can't determine from static reading whether these ever fire against the same `program_id` with different results; needs a runtime/product check.
- `escort_roster/history_sync.py:875` writes to `wbom_escort_programs` — but confirmed a guarded, admin-triggered historical backfill with dedup, not a competing live write path. **KEEP**.

## 8. Migrations (table-collision check, CREATE-only scope — see Report 4 for the full ALTER-inclusive audit)

Zero table names found `CREATE`'d in more than one of the three migration directories. (**Note:** Report 4's independent, deeper audit — which also checked `ALTER TABLE` statements, not just `CREATE` — found several genuine cross-directory collisions at the ALTER level, e.g. `fpe_cash_transactions`, `escort_roster_entries`. The two findings are consistent, not contradictory: this pass was explicitly scoped to `CREATE TABLE` only.)

## 9. Dashboard/frontend duplication

Confirmed **zero shared JS files** exist in `app/static/` and **zero `<script src=...>` tags** in any of the 6 dashboard HTML files — every page independently implements its own fetch-wrapper + `X-Internal-Key` header injection (`dashboard.html:312-314`, `payroll.html:1483,1493` — two separate wrappers in the same file, `escort-roster.html:702-703,1257`, `drafts.html` — inline literal repeated per call site, no wrapper function at all in some spots, `wa_chat.html:460-461`, `open-chat.html:1381`). **MERGE** — confirmed real, extensive copy-paste; no shared frontend utility script exists anywhere in the tree.

## 10. Phone-number normalization

`PHONE_NORMALIZER_CONVENTION.md` documents a deliberate 3-tier design (`phone_normalizer` canonical 13-digit, `number_identity` 3-variant helper, `fazle_payroll_engine.normalizer` an explicitly documented 11-digit FPE-only exception) and explicitly says *"Do not create a new normalizer module."* Every other `normalize_phone`-named function found (`contact_sync`, `shared/identity_map`, `app/critical_numbers`, `user_role`) is a **thin delegating wrapper, not a reimplementation**. **KEEP overall** — a genuinely well-disciplined design; the apparent proliferation is wrapper/re-export sprawl, not duplicated logic. Two minor items: `shared/phone.py:8` self-declares itself "canonical," contradicting the convention doc's naming of `phone_normalizer` as canonical (a documentation inconsistency, not a runtime bug); `attendance_parser/__init__.py:186-191` manually builds a 2-variant fallback instead of using `number_identity`'s prescribed 3-variant helper (minor convention deviation, not urgent).

## Summary table

| # | Area | Classification | Key evidence |
|---|---|---|---|
| 1 | `modules/payment` shim | ARCHIVE | 22-line re-export, no callers |
| 1 | `payment_correction` | ARCHIVE (do-not-delete) | Self-declared dormant |
| 1 | `admin_transactions` edit/delete | INVESTIGATE | Direct UPDATE outside `create_transaction()` |
| 1 | payment_workflow/ingest/nl_advance_record | KEEP | All route through canonical function |
| 2 | `recruitment_ai` | DEPRECATE | Superseded, one helper still imported |
| 2 | `recruitment_assistant` | DELETE | Zero callers repo-wide |
| 2 | `employee_conversation` | KEEP | Distinct domain |
| 3 | `user_role` | DELETE/ARCHIVE | `detect_role()` unused |
| 3 | `identity_brain`/`contact_roles`/`role_classifier` | KEEP | Distinct sources/consumers |
| 4 | `outbound` vs `social_auto_reply` send infra | MERGE | Parallel queue/retry/dedup/rate-limit |
| 4 | `shared/queue.py`+`queue_arbiter` | KEEP | Different problem (inbound arbitration) |
| 5 | ai_console/ai_readonly_tools/admin_chat_lab/ollama_memory/knowledge_base/rag | KEEP | One coherent pipeline + 2 unrelated utilities |
| 6 | RBAC local auth-check copies | MERGE | ~10 files |
| 7 | `escort_lifecycle` direct roster write | MERGE | Bypasses `escort_roster.db` API |
| 7 | Dual pay-calc formulas | INVESTIGATE | Needs runtime/product check |
| 8 | Migration table collisions (CREATE-only) | KEEP (none at CREATE level) | See Report 4 for ALTER-level collisions |
| 9 | Dashboard fetch-wrapper duplication | MERGE | 6 HTML files, no shared JS |
| 10 | Phone normalization | KEEP | All wrappers delegate correctly |

---

# REPORT 4 — Migration Governance

## 1. Full inventory (verified directly)

- **`/home/azim/core/migrations/`** — 6 files: `002_add_hr_officers.sql`, `003_add_fpe_indexes.sql`, `004_add_accounting_period_constraint.sql`, `005_add_admin_login_auth.sql`, `escort_history_schema.sql`, `escort_roster_schema.sql`. No `001`. **No automated execution path at all** — `DEPLOYMENT_SUMMARY.md:46`/`HR_OFFICER_SETUP.md:28,31` instruct manual `psql -f migrations/002_add_hr_officers.sql`.
- **`/home/azim/core/db/migrations/`** — **35 files** (`find | wc -l`): `001, 002, 003, 003b, 003c, 004–008, 010, 011, 012×2, 013–019, 020×2, 021, 022, 023×5 (023_c0d_001‑004 + 023_model_router_tables), 024–028`.
- **`/home/azim/core/modules/fazle_payroll_engine/migrations/`** — 14 files, `001`–`014`, clean, no gaps/duplicates.
- **4th path — inline Python DDL**, all `CREATE TABLE IF NOT EXISTS`, re-executed at every process startup: `modules/contact_sync/__init__.py` (3 tables), `modules/social_auto_reply/__init__.py` (8 tables), `modules/bridge_poller/__init__.py` (4 tables), `modules/wa_chat_frontend/__init__.py` (2 tables).
- **5th path (test-only but load-bearing for assumed schema):** `tests/conftest.py` independently `CREATE TABLE IF NOT EXISTS`'s **24 tables** — see §7.
- **Orphan scripts:** `db/merge_preview.sql`, `db/new_tables.sql` (see §6), plus one-off data scripts `scripts/backfill_skipped_failed_to_review.sql`, `scripts/seed_recruitment_kb.sql`.

## 2. Duplicate/missing numbers

`db/migrations/` confirmed duplicates at `012` (`ai_readonly_views.sql` / `queue_arbiter.sql`) and `020` (`escort_roster_employee_fk.sql` / `preflight_guard_tables.sql`), missing `009`, and `023` fragmented into 5 files. No runtime collision results because `db/migrate.py` keys `schema_migrations` on full filename, not numeric prefix, applying files in alphabetical glob order. `migrations/` and `fazle_payroll_engine/migrations/` are each internally clean.

## 3. Cross-directory / inline-DDL collisions (the key finding)

A table-extraction script across all sources found genuine, non-commented collisions:

- **`fpe_cash_transactions`** — created in `fazle_payroll_engine/migrations/001`, altered 19× by `.../010_fpe_cash_transactions_canonical.sql`, **also altered by `migrations/004_add_accounting_period_constraint.sql:6`** and **`db/migrations/023_c0d_001_fpe_soft_delete_columns.sql:20`** — one table touched by all three governed directories.
- **`fpe_income_transactions`** — same 3-way split (`fazle_payroll_engine/migrations/006,014`, `migrations/004:14`, `db/migrations/023_c0d_004:11,14`).
- **`fazle_contact_aliases`** — created independently in `db/migrations/006_critical_contact_zero_loss.sql:29` **and** `modules/contact_sync/__init__.py:132` — verified byte-for-byte identical today, but two separate hand-maintained sources of truth with nothing enforcing sync.
- **All 8 `social_auto_reply` tables** — identical `CREATE TABLE IF NOT EXISTS` in **both** `db/migrations/013_social_auto_reply.sql` **and** `social_auto_reply/__init__.py:17-122` (executed every startup). Confirmed in sync today; **largest duplicated-schema-definition surface in the repo**, zero drift detection.
- **`escort_roster_entries`** — created `migrations/escort_roster_schema.sql:42`, altered same-directory by `escort_history_schema.sql:58`, **and** by `db/migrations/018_atomic_release_and_outbound.sql:11` and `020_escort_roster_employee_fk.sql:8` — already internally flagged in `database_rules.md:93`.
- **`wbom_escort_programs`** — altered by `db/migrations/003c,018` **and** `fazle_payroll_engine/migrations/009_stabilization.sql:25`.
- **`fazle_payment_drafts`** — created `db/migrations/002_align_tables.sql:53`, altered 11 more times in `db/migrations/`, **and** by `fazle_payroll_engine/migrations/008,009`, **and** re-created by orphan `db/new_tables.sql:51`.
- **`fazle_knowledge_base`, `fazle_recruitment_sessions`** — each `CREATE TABLE`'d **twice within `db/migrations/` itself**, plus a third `CREATE TABLE` in orphan `db/new_tables.sql`.

## 4. Migration runner — tracks only ONE of the four mechanisms

`db/migrate.py`'s `MIGRATIONS_DIR` resolves **only** to `db/migrations/`, and `schema_migrations` (the only tracking table anywhere — confirmed via repo-wide grep) is created/read/written only there. `migrations/` is applied manually via psql, untracked. `fazle_payroll_engine/migrations/` is **re-run in full, statement-by-statement, on every app startup** (`fazle_payroll_engine/__init__.py:25-38`), with **per-statement exceptions swallowed as warnings** — a partially-failed `ALTER` is silently skipped, not blocking startup. The 4 inline-DDL modules follow the same untracked, idempotent-by-convention, re-run-every-boot pattern. **Net: 1 of 4 mechanisms is tracked; the other 3 rely on manual psql or blind re-execution at every boot.**

## 5. Unsafe migrations scan

- **`db/migrations/003c_schema_align.sql`** — 8× `ALTER TABLE ... DROP CONSTRAINT <name>` with **no `IF EXISTS`** guard, plus 2 hard `RENAME COLUMN` statements with no guard at all — the single non-idempotent migration in a codebase where every other file explicitly documents itself as safe to re-run.
- **`db/migrations/003b_recruitment_sessions_fix.sql:7`** — `DROP TABLE IF EXISTS fazle_recruitment_sessions` (guarded, but a full drop-and-recreate).
- No `TRUNCATE`, `ALTER COLUMN ... TYPE`, or `DROP COLUMN` found anywhere in live (non-commented) code.

## 6. Orphaned files

- **`db/merge_preview.sql`** — pure read-only inspection script, explicitly self-labeled "Nothing is dropped." Contains two never-executed `ALTER TABLE` suggestions in comments. Harmless — `migrate.py`'s glob never sees it (it's in `db/`, not `db/migrations/`).
- **`db/new_tables.sql`** — a standalone "run once, safe to re-run" bootstrap script that `CREATE TABLE IF NOT EXISTS`'s 3 tables **also** created by numbered migrations, then does a large `INSERT ... ON CONFLICT (key) DO UPDATE` KB seed. **Risk: if re-run today, the `ON CONFLICT DO UPDATE` will silently overwrite any `fazle_knowledge_base` rows edited since**, reverting to this file's old canned replies.

## 7. `database_rules.md`'s "84 verified production tables" — does NOT reconcile

- **Undercount**: 6 more `db/migrations/` files landed since the 2026-07-02 doc, introducing ≥7 tables never mentioned in any of the 10 documented domains (`assistant_release_gates`, `assistant_preflight_reports`, `assistant_model_decisions`, `assistant_model_eval_samples`, `assistant_feature_flags`, `assistant_ext_location_audit`, `escort_roster_change_requests`). **4 of the 6 newer files are currently untracked in git** — their production-application status is unknowable from version control alone.
- **A much larger gap: 24 production tables have zero schema source of truth except `tests/conftest.py`.** Full list: `wbom_employees, wbom_contacts, wbom_whatsapp_messages, wbom_escort_programs, wbom_attendance, wbom_cases, wbom_employee_requests, wbom_cash_transactions, wbom_payroll_runs, wbom_payroll_run_items, wbom_payroll_approval_log, wbom_salary_records, wbom_staging_payments, fazle_admins, fazle_admin_roles, fazle_admin_audit, fazle_roles, fazle_contact_roles, fazle_outbound_queue, fazle_service_heartbeats, fazle_db_backups, fazle_report_cache, fazle_error_log, llm_conversation_log`. **Two of these — `fazle_error_log` and `llm_conversation_log` — are confirmed live, actively-written production tables** (`app/error_log.py`, `app/ollama.py`, `app/main.py`, `app/github_models.py`, `app/groq_provider.py`) with genuinely zero schema definition anywhere outside the test file. **A clean environment built strictly from this repo's migrations would crash on first error-log write or first LLM-call log.**

## Recommended canonical migration strategy

1. **Designate `db/migrations/` + `db/migrate.py` as the single source of truth** — it's the only mechanism with real tracking, transactional apply, and dry-run support. Port the other two directories in as **new, never-before-used numbers** (e.g. `029`+), each annotated as reconciliation-only, not re-derivation. **Do not renumber or dedupe 012/020/009** — production may already have applied them under their exact current filenames (the PK), and blind renumbering risks re-running or orphaning history.
2. **Treat the 4 inline-DDL modules as a deliberate, documented idempotent-bootstrap pattern**, not a restructuring target — they're genuinely idempotent and arguably intentional self-healing for a small operator. Add one new `db/migrations/0NN_*.sql` per module as the "paper record" (so `schema_migrations` becomes a complete inventory) without necessarily stopping the runtime re-creation. Specifically **dedupe the `social_auto_reply` duplication** — pick one of the two identical definitions as the single source and have the other reference it.
3. **Retroactively document the 24 conftest-only tables — the most urgent item.** Write one new `db/migrations/0NN_bootstrap_core_tables.sql` using `\d <table>` against the **actual production DB** (not `conftest.py`'s guess, which is already known to disagree with production per the C0D audit) as the column source. This closes the "can't rebuild this system from source" gap.
4. **Fix numbering without renaming anything shipped**: leave the missing `009` gap documented, not backfilled; document that filename (not number) is the uniqueness key going forward; **commit the 4 currently-untracked files immediately** or explicitly record their production-application status; enforce that all new schema changes, from any domain, land only as new `db/migrations/0NN_*.sql` files from this point forward.

---

# REPORT 5 — Production Readiness Audit

## Scorecard

| Area | Risk | One line |
|---|---|---|
| Release process | **Critical** | Service runs the working tree directly; no build/artifact/CI gate before code is live |
| Secrets management | **Critical** | Two live production API keys + a plaintext password committed to git-tracked docs, unrotated |
| Configuration / feature flags | High | `.env` is still the primary lever; the app can rewrite its own `.env` at runtime, untracked |
| Rate limiting | High | No rate limiting on the public `/webhook/meta` ingress that triggers paid LLM calls |
| Rollback | High | Procedure is detailed and specific, but no evidence a rollback drill has ever been executed |
| CI pipeline | Medium | Two workflows exist; one has a `working-directory` path that looks structurally broken |
| Testing | Medium | Suite is green net of 22 formally-waived xfails, but two same-day reports disagree on exact numbers |
| Logging / monitoring / alerting | Medium | Full Prometheus+Grafana+Loki stack is running (better than docs claim) but app logs aren't shipped to it |
| Idempotency / transaction safety | Medium | DB-enforced uniqueness is real; the app-level race path raises unhandled, not idempotent-return |
| Service restart / graceful shutdown | Low | `Restart=always` with an explicit, ordered lifespan shutdown sequence |
| Health checks | Low | Three tiers, correctly tiered by auth, checking real dependencies not just process liveness |
| Backup / recovery / DR | Low | 18 consecutive daily dumps on disk through today; staleness alert wired to a human |
| RBAC | Low | Spot check across 3 modules shows consistent key/permission enforcement |

## 01 — Release process (Critical)

`fazle-core.service`: `WorkingDirectory=/home/azim/core`, `ExecStart=/home/azim/.venv/bin/python run.py`, `Restart=always`. No Docker, no build, no packaging. **Live evidence captured during this audit (2026-07-08 21:10 CEST):** `git status --short` returned **56 modified/untracked files**, including `app/main.py`, `app/config.py`, `modules/escort/__init__.py`, 4 FPE modules, plus 4 new untracked modules/migrations. File mtimes on modified core files are **2026-07-06**; the running process (PID 2417318) has been active since **2026-07-05 07:18:43** — the in-memory process predates these on-disk edits. **Under `Restart=always`, any crash or manual restart right now loads this uncommitted, unreviewed, untested state verbatim.**

## 02 — Testing (Medium)

`TESTING_STATUS.md` explicitly marked stale by its own header. Current numbers live in `reports/release/`: `failure_triage_protocol_2026-07-05.md`'s final validation run reports **561 passed, 1 skipped, 22 xfailed, 0 failed**; `go_no_go_decision_package_2026-07-05.md` §3, same day, reports **553 passed, 1 skipped, 26 xfailed, 0 failed**. The two counts do not reconcile from the documents alone (**Not verified** which is authoritative — re-running the suite was out of scope for a read-only audit against a live production DB). 22 of the passing xfails are formal, issue-tagged waivers with an owner sign-off line, not silently swept failures.

## 03 — Rollback (High)

`RECOVERY.md` §5 gives two concrete, actionable paths (git-tag checkout or tar-snapshot restore, each followed by `make smoke`). But `RECOVERY.md`/`BACKUP.md` are both dated "Last updated: 2026-05-07" — two months stale against the 2026-07-02 employee-unification migration; §7's validation checklist still queries `wbom_employees` exclusively, not `fpe_employees` (the codebase's own "single canonical table" as of that commit). `PRE_CHAOS_CHECKLIST.md` §G "Rollback Readiness" — all 5 checkboxes unchecked, sign-off blank. No evidence a pre-chaos git tag currently exists.

## 04 — CI pipeline (Medium)

`.github/workflows/ci.yml` — syntax + offline tests only, no DB, 9 DB-dependent test files explicitly `--ignore`d. `.github/workflows/ci-test.yml` — a much larger, well-designed 5-job pipeline (lint, unit+coverage, integration+DB+workflow against real Postgres/Redis containers, resilience, Playwright E2E). **Structural concern:** every job in `ci-test.yml` sets `working-directory: core/`, but `git rev-parse --show-toplevel` returns `/home/azim/core` itself — there is no nested `core/` folder in this repo. If the GitHub remote has the same flat layout, every step after checkout would `cd` into a directory that doesn't exist and fail immediately. **Not verified** — no `gh` CLI/network access to check Actions run history from this host.

## 05 — Service restart & graceful shutdown (Low)

`Restart=always`/`RestartSec=5`; `lifespan()` (`main.py:251`) has a real ordered teardown after `yield` — stale-draft worker → scheduler → outbound queue drain → self-heal/orchestrator/sync/arbiter/gateway deregistration → FPE → social auto-reply → Ollama daemon, each independently try/excepted. `ExecStartPre=-/home/azim/shared/scripts/generate_runtime_services_env.sh` (leading `-` = non-fatal), confirmed present and executable. **Sound.**

## 06 — Configuration, environment variables & feature flags (High)

`.env` correctly gitignored, mode 600, never committed. `modules/preflight_guard` DB-backed feature flags are real and load at startup — but **coexist with, not replace**, `.env`. `app/config.py:284` `update_repo_env_value()` **opens `.env` directly, regex-replaces/appends a key, writes it back, clears the settings cache — the running application can rewrite its own production config file**, no audit log, no git commit, no diff. `AUTO_REPLY_ENABLED` — the single most consequential flag in the system — lives in this file. "Config change audit trail" today is informal timestamped `.env` copies, not a diff/changelog.

## 07 — Health checks (Low)

`/health` (unauthenticated, 503 on critical), `/health/deep` and `/health/c1b` (both correctly `Depends(require_api_key)`-gated). `_build_health()` probes Postgres, Ollama, Qdrant, outbound queue, both bridge pollers' heartbeats, bridge SQLite freshness, disk, memory, and (deep-only) LLM provider + full bridge status. `/health/c1b` is a named business-invariant check (FPE integrity, WBOM freeze state, RAG count), not a generic infra probe. **Well-designed, correctly tiered.**

## 08 — Logging, monitoring & alerting (Medium)

`ARCHITECTURE.md:134` states Prometheus/Loki "Not configured" — **wrong as of this audit**. Confirmed live via `docker ps -a`: a full, healthy Prometheus+Grafana+Loki+Promtail+node-exporter+cadvisor stack, running "Up 2 weeks." Prometheus's scrape config confirms an active `fazle-core` job hitting `172.20.0.1:8200/metrics` every 30s; `GET /metrics` is real (a request-timing/count middleware feeds it on every call). **But** Promtail's only scrape target is `/var/log/*log` — it does **not** ingest `/home/azim/core/logs/fazle-core*.log`, where the app's own logs actually go. **Loki is running but not ingesting this app's logs.** No Alertmanager container found. What actually reaches a human today: hand-built, per-job WhatsApp alerts from the scheduler (e.g. backup staleness), not general-purpose alerting off the metrics Prometheus already has.

## 09 — Backup, recovery & DR (Low)

`ls /home/azim/backups/fazle/` shows **18 consecutive daily dumps from 2026-06-07 through 2026-07-08 (today)**, growing in size release over release — a real, unattended job, not stale artifacts. Scheduler wiring confirmed (`daily_db_backup` job → `modules.backup.job_daily_db_backup`). Staleness is self-checked and alerts a human. `RECOVERY.md` gives a specific, non-generic restore procedure. Caveat carried from §03: its verification queries reference `wbom_employees`, not necessarily the current canonical table. **Strongest area in the audit** apart from that doc-staleness caveat.

## 10 — Secrets management (Critical)

`.env` permissions/gitignore are correct — that's not the problem. **The problem:** `DEPLOYMENT_SUMMARY.md` and `HR_OFFICER_SETUP.md`, both git-tracked (added commit `1ca93f9`, 2026-06-26), contain **two currently-valid plaintext API keys** (`HROFFICER_01_APIKEY`, `HROFFICER_02_APIKEY`), confirmed byte-identical to the live values in `.env` today, plus two plaintext passwords (`Office1234`, `Assistant1234`) for the same accounts, unrotated since 2026-06-26. These authenticate as real admin accounts with payroll/income-entry permissions against the live RBAC system today. No `.env` file itself was ever committed — the leak is narrower than "secrets in history," but the two files that do leak real credentials sit in the working tree and remote **today**. **Treat as an active incident.**

## 11 — RBAC / security regression check (Low)

Spot check across `escort_roster/routes.py`, `fazle_payroll_engine/routes.py`, and `main.py` (98 matches for `Depends(require_api_key)`/RBAC calls) shows consistent enforcement. No regression found.

## 12 — Rate limiting (High)

Repo-wide grep for rate-limiting returns hits only in `social_auto_reply`'s own send-rate limiter and the two outbound AI-provider clients — **nothing on the inbound HTTP layer**. `/webhook/meta` and both bridge webhooks have no request-rate limiting or concurrency cap. `/webhook/meta` does have real HMAC signature verification (not wide open to arbitrary injection), but a valid-signature flood, or a flood via the bridge endpoints (which trust `_verify_bridge_webhook` rather than a public secret), is unthrottled — and each inbound message can trigger paid LLM calls plus OCR/transcription, making this a cost/availability surface as much as a security one.

## 13 — Idempotency, transaction safety & data integrity (Medium)

`accounting.py:35-50` — idempotency via `txn_ref = sha256(...)`, checked with `SELECT` before insert; a match returns the existing row. **Confirmed at the schema level:** `txn_ref TEXT UNIQUE NOT NULL` (FPE migration 001:89) — the database itself cannot end up with two rows sharing a `txn_ref` regardless of application-level races. **Gap:** no `SELECT ... FOR UPDATE` and no try/except around the `INSERT ... RETURNING id` to catch a concurrent unique-violation and convert it into the graceful idempotent-hit response — a genuine race would surface as an unhandled exception rather than a graceful success. Narrow window, low likelihood, but the fix is small relative to the guarantee it would close. Same pattern in the reversal and income-transaction paths. **Data integrity is protected; the caller experience under a genuine race is not what the design intends.**

---

# REPORT 6 — Enterprise Gap Analysis

Only capabilities confirmed genuinely missing (via exhaustive grep and cross-referencing across all four research streams) are listed. Anything already present, even partially, is explicitly excluded per the brief.

## Confirmed missing

1. **Dispatch / Scheduling Engine.** Zero hits repo-wide for `conflict|overlap|double.?book|assign.*guard|duty roster|shift schedule`. No availability calendar, no conflict/double-booking detection, no decision-support for who to assign — the entire "escort roster" is a message-parsing → admin-typed-assignment → pay-calculation pipeline layered on manual admin decisions. **This is the core operational logistics of the business the company sells, and it does not exist as a system capability.** (Already the centerpiece of Phase 2a in the previously-approved roadmap.)

2. **Double-entry / bank-reconciled General Ledger.** Zero hits for `double.entry|debit|credit|chart of account|balance sheet|trial balance|general ledger|bank statement|bank reconciliation`. What exists (`fpe_cash_transactions` + `fpe_employee_ledger`) is a solid, immutable, audited **single-entry** cash-tracking ledger — genuinely production-grade for "who was paid what," but it cannot produce a P&L or balance sheet, and reconciliation (`fazle_payroll_engine/reconcile.py`) only checks **internal** consistency (parser-detected amount vs. ledger vs. review queue), never against a real bKash/Nagad/bank statement. (Already Phase 2b in the previously-approved roadmap.)

3. **Client billing / invoicing / accounts-receivable (revenue side).** **New finding, not previously flagged in any prior session or research pass.** Every payroll/payment module found across the entire 63-module inventory (`fazle_payroll_engine`, `payment_workflow`, `payment_ingest`, `admin_transactions`, `payroll`, `payroll_logic`) is concerned exclusively with **paying employees/guards** (the cost side). No module, table, or workflow was found for **billing the company's own corporate clients** for escort/guard services rendered — no invoice generation, no accounts-receivable tracking, no client payment-status dashboard.
   **Owner decision (2026-07-08): in scope.** This is now a confirmed part of Phase 2b (Accounting/GL), not an open question — see the updated Phase 2b entry in Report 10. Nothing has been built yet; no tables or code exist for this as of this decision. Required capabilities per the Owner's decision: client billing, invoice generation, accounts receivable, client payment tracking, client outstanding-balance tracking, a link between escort/guard service delivery and the resulting client invoice, and future GL postings for revenue, receivable, collection, adjustment, discount, and bad debt as needed.

4. **Centralized alerting / notification policy engine.** Partial gap. Prometheus+Grafana+Loki are running (Report 5 §08) and Prometheus is actively scraping real metrics, but there is no Alertmanager and no configured alert rules — the only alerts that reach a human today are hand-built, per-scheduler-job WhatsApp messages (backup staleness, health summary, DLQ backlog), each independently coded rather than driven by a shared policy engine. Not a from-scratch build — mostly a matter of wiring existing infrastructure (add Alertmanager, define rules against metrics already being collected).

## Explicitly NOT gaps (already present, sufficient for current scale)

- **Workflow/BPM engine** — the codebase uses per-domain state machines (draft lifecycle, payroll runs, escort programs) rather than a generic workflow engine. For a business at this scale, this is the right level of abstraction; introducing a generic BPM engine would be premature complexity, not a gap to fill.
- **Analytics / BI** — `modules/reports` (pure-SQL report builders) plus the in-progress AI Operating Console (`ai_console`/`ai_readonly_tools`, already ~70% built) cover this need at a scale-appropriate level; the previously-approved roadmap's Phase 3 already extends this rather than building new BI infrastructure.
- **Document management** — `kb_upload` (PDF/DOCX/TXT ingestion) covers knowledge-base content; escort slip images are processed via OCR (`escort_slip_extractor`). **Not verified** whether original slip images are retained with a formal retention/versioning policy — worth a quick check, but not large enough to warrant a new "document management module."

## Open question for the Owner (not a gap — a scope question)

- **Regulatory/tax compliance** (VAT, statutory payroll deductions, etc. under Bangladeshi law) — **Not verified** whether this is handled manually outside the system or needs to be a Fazle Core capability. No module addresses it either way. Flag for an explicit Owner decision, not an assumed gap.

---

# REPORT 7 — Execution Order

This operationalizes **Phase 1 (Consolidation & Freeze Enforcement)** from the previously-approved roadmap into a concrete, dependency-ordered task list. Phases 2-4 remain as scoped in that roadmap (dispatch engine → GL/accounting → AI console unification) and are not re-broken-down here until Phase 1's findings are actioned, since several Phase 1 items (e.g. the billing/AR gap in Report 6, the RBAC fail-open bug) could change Phase 2's shape.

## Tier 0 — Immediate, no dependencies, start today (can run in parallel with everything else)

| Task | Prerequisites | Risk | Complexity | Est. duration | Testing required | Rollback | Success criteria |
|---|---|---|---|---|---|---|---|
| **0a. Rotate leaked HR-officer API keys + passwords; scrub plaintext from `DEPLOYMENT_SUMMARY.md`/`HR_OFFICER_SETUP.md`** | None | High if delayed | Low | Hours | Confirm old key/password no longer authenticates; confirm new ones do | Old keys already compromised — no rollback needed, only forward fix | New key hashes differ from the leaked ones in `fazle_admins`; committed docs no longer contain a working credential |
| **0b. Verify and fix the unmounted `drafts_router`/`social_auto_reply_router`** | None | Medium (live customer-facing bug if confirmed) | Low (one-line `include_router` fix, or explicit removal if intentionally retired) | Hours | `curl /api/drafts/stats` before and after; confirm `drafts.html` buttons work end-to-end | Revert the one-line change | Drafts page approve/edit/delete/block work over HTTP; decision recorded on whether social_auto_reply's control endpoints should be mounted |
| **0c. Resolve `AUTO_REPLY_ENABLED` KB-vs-`.env` discrepancy** | None (already scoped in a prior session's P0 proposal) | Medium (safety-relevant config drift) | Low | Hours | Confirm `.env`, `management_decisions.md`, and the 4 other KB articles all state the same value | Config flip, trivially reversible | One authoritative value, all KB articles updated to match, decision logged as a new `management_decisions.md` entry |
| **0d. Fix admin_commands RBAC fail-open bug** (`admin_commands/__init__.py:251-280`) | None | High (money-command channel) | Low (change except-branch to deny, not allow) | Hours-1 day | Add a test that simulates `rbac.check_permission` raising and asserts the command is rejected, not executed | Trivial code revert | WhatsApp channel now fails closed identically to the HTTP channel |
| **0e. Commit or explicitly abandon the 4 untracked migration files** (`020_preflight_guard_tables.sql`, `023_model_router_tables.sql`, `027_assistant_feature_flags.sql`, `028_assistant_ext_location_audit.sql`) | None | High (risk of loss via accidental `git clean`) | Low | Hours | `git status --short` shows zero untracked files under any `migrations/` path | N/A — this is itself the safety net | Files committed with clear messages, or a documented decision to discard them |

## Tier 1 — Release-safety gate (prerequisite for everything below)

| Task | Prerequisites | Risk | Complexity | Est. duration | Testing required | Rollback | Success criteria |
|---|---|---|---|---|---|---|---|
| **1. Working-tree deployment gate** — pre-restart hook refusing to start if `git status --short` is non-empty; medium-term, tag-based deploy instead of live working tree | Tier 0 items landed (else the gate blocks on their own uncommitted state) | High impact if skipped | Medium | 1 sprint | Deliberately dirty the tree and confirm the service refuses to restart; confirm normal tagged restarts still work | Remove the hook | `git status --short` empty at all times going forward; `git rev-parse HEAD` matches a `/version` endpoint |

## Tier 2 — Structural consolidation (Article VII §7.2 checklist, made executable)

| Task | Prerequisites | Risk | Complexity | Est. duration | Testing required | Rollback | Success criteria |
|---|---|---|---|---|---|---|---|
| **2. Migration consolidation** — collapse 3 directories + inline DDL into governed `db/migrations/`; backport the 24 conftest-only tables (Report 4's recommended strategy) | Tier 1 gate in place | High (schema-critical) | High | 2-3 sprints | Fresh-environment rebuild from migrations alone must succeed without `conftest.py`; diff against a real `pg_dump --schema-only` | New migrations are additive-only; nothing destructive to revert | `schema_migrations` is a complete inventory; a clean environment can be rebuilt from this repo alone |
| **3. Duplicate-logic resolution batch** (Report 3's MERGE items): `outbound`/`social_auto_reply` queue consolidation; extract the ~10 copy-pasted RBAC auth-check functions into one shared dependency; `escort_lifecycle` → call `escort_roster`'s API instead of its own raw upsert; extract shared frontend fetch/auth-header JS across the 6 dashboard pages | Task 2 substantially done (schema stability) | Medium | Medium-High | 2 sprints | Full regression suite; specifically re-test escort-roster pay figures before/after the `escort_lifecycle` fix, since two formulas currently feed the same table (Report 3 §7 INVESTIGATE item — resolve this first) | Each merge is independently revertible (isolated commits) | Duplicate implementations reduced to one canonical each, with the same or better test coverage |
| **4. Dead-code cleanup** — archive/delete confirmed-orphaned modules (`operations_health`, `recruitment_assistant`, `payment` shim, `message_archive`); move `payment_correction` out of the active tree per its own do-not-delete note | Task 3 (avoid deleting something mid-merge) | Low | Low | 1 sprint | Full regression suite; confirm zero import errors after removal | Git history retains everything | Zero orphaned modules remain in the active `modules/` tree |
| **5. Documentation alignment** — fix `RECOVERY.md`/`BACKUP.md`'s `wbom_employees`→`fpe_employees` references; correct `ARCHITECTURE.md`'s monitoring-stack claim; reconcile `AUTO_REPLY_ENABLED` across all KB articles (if not already done in Tier 0); namespace the 3 parallel "Phase" vocabularies (KB Phase 1-6, release Phase 1-5/FG1-FG5, hardening hardening-report Phase 1-5) | None blocking | Low | Low-Medium | 1 sprint | None beyond doc review | N/A | No doc contradicts verified live state; "Phase N" always appears with a namespace prefix going forward |
| **6. RBAC alignment** — verify every WhatsApp command and HTTP route maps to the correct role per `rbac.COMMAND_ROLE`; confirm the admin-command channel now fails closed (Tier 0d) is consistent everywhere | Task 0d done | Low (audit found this largely solid already) | Low | 1 sprint | Attempt each privileged command/route with a too-low-role account, confirm denial | N/A | No under- or over-privileged command/route found |
| **7. Frontend / dashboard / admin panel / transaction UI / pending queue / audit UI review** — the Constitution's own remaining checklist items, now that the Drafts page's backend is fixed (Tier 0b) and shared JS exists (Task 3) | Tasks 0b, 3 | Low-Medium | Medium | 1-2 sprints | Manual click-through of every admin page against live-shaped test data | N/A | Every dashboard page's displayed data matches its backing API; Constitution §7.2 checklist fully checked off |

## Tier 3 — Verification and closure

| Task | Prerequisites | Risk | Complexity | Est. duration | Testing required | Rollback | Success criteria |
|---|---|---|---|---|---|---|---|
| **8. Test-coverage triage** — reconcile the 561-vs-553 discrepancy (re-run and treat one report as canonical going forward); add tests for untested Critical/High modules (`payment_ingest`, `employee_verification`, `scheduler`, `wa_chat_frontend`, `bridge_poller`) | Tasks 2-4 (stable ground to test against) | Medium | Medium-High | 2 sprints | New tests must actually exercise the money/dispatch-adjacent paths, not just import-smoke-test | N/A | One authoritative test-count report; the 5 named Critical/High modules each have a dedicated test file |
| **9. CI pipeline fix** — resolve/confirm the `ci-test.yml` `working-directory: core/` issue; confirm which of the two workflow files is the real gate | None blocking, but should land before Tier 1's tag-based deploy matures | Low-Medium | Low | Half a sprint | A green run in GitHub Actions on a real PR | N/A | CI actually runs and gates merges |
| **10. Rate limiting on public webhook ingress** | None blocking | Medium (cost/availability exposure) | Low-Medium | Half a sprint | Load-test the webhook endpoint, confirm throttling before LLM-provider spend spikes | Remove the limiter config | `/webhook/meta` and bridge webhooks reject/throttle a flood without blocking legitimate traffic |
| **11. Monitoring completion** — add a Promtail scrape target for the app's own log files; define Grafana/Prometheus alert rules for health-check failures, error-rate spikes, backup staleness | None blocking | Low-Medium | Low-Medium | 1 sprint | Kill the app deliberately in a test window, confirm an alert fires through Grafana/Prometheus | N/A | An intentional outage triggers an alert via the monitoring stack, not only via the hand-built WhatsApp job |
| **12. Full pre-production acceptance re-run** | Tasks 1-11 substantially complete | — | — | — | Re-run the acceptance suite that returned "FAIL"/"BLOCKS RELEASE" on 2026-07-06 | N/A | A non-FAIL verdict, or every remaining failure has an explicit, dated, Owner-approved waiver |
| **13. Formal Owner sign-off on Phase 1 completion** | Task 12 | — | — | — | — | N/A | Constitution Article VII §7.3's 4 conditions all met; Constitution reviewed/updated; Owner approval recorded; Phase 2 (dispatch engine) authorized to begin |

---

# REPORT 8 — Risk Register (Consolidation Effort)

## Register at a glance

| # | Risk | Prob. | Impact | Owner |
|---|---|---|---|---|
| 1 | Broken freeze policy (Article VII violated in practice) | High | High | Owner / Release process |
| 2 | Working-tree deployment (no edit→prod gate) | High | Critical | Owner / Release process |
| 3 | Feature creep despite the freeze | High | Medium | Owner |
| 4 | Secrets leaked in git-tracked docs | High (already occurred) | Critical | Whoever owns secrets/infra |
| 5 | Release without full certification | Medium | High | Owner / Release process |
| 6 | Architecture drift (parallel frameworks) | Medium | Medium | Owner / Whoever owns architecture |
| 7 | Knowledge divergence (KB vs. running code) | High (already occurring, twice) | Medium | KB governance owner |
| 8 | Schema divergence | Medium | High | Whoever owns migrations |
| 9 | Migration corruption / multi-stream migrations | Medium | High | Whoever owns migrations |
| 10 | Duplicate logic across modules | Medium | Medium | Whoever owns consolidation |
| 11 | AI hallucination / unsupervised writes | Low | High | Whoever owns `ai_console` |
| 12 | Monitoring blind spot | Medium | Medium | Whoever owns ops/observability |
| 13 | Unrate-limited public webhook ingress | Medium | Medium | Owner / Release process |
| 14 | Payroll idempotency race (unhandled exception path) | Low | Low | Whoever owns `fazle_payroll_engine` |

## 01 — Broken freeze policy

`BUSINESS-CONSTITUTION.md` itself only entered git in commit `be1ed3c`, **2026-07-02**, titled *"feat: employee unification — fpe_employees as single canonical table"* — the freeze document arrived bundled inside a feature commit that restructured the employee schema. 16 commits landed between 2026-06-28 (the freeze date) and today, including `feat: employee unification`, `feat(escort-roster): production hardening`, `feat(admin): add user activate flow` — these read as new/changed behavior, not the 9 consolidation tasks §7.2 authorizes. **Mitigation:** require every commit touching `modules/`/`app/` to declare which §7.2 line item it serves; retroactively classify the 16 commits with an explicit Owner ruling on each. **Verification:** monthly `git log --since=2026-06-28 --name-only` review, zero un-mapped commits = policy holding.

## 02 — Working-tree deployment

Caught live, not just architecturally possible (see Report 5 §01 for full evidence). **Mitigation:** pre-restart hook refusing dirty trees (Report 7 Tier 1); medium-term, tag-based deploy. **Verification:** `git rev-parse HEAD` vs. a `/version` endpoint always matching; `git status --short` always empty.

## 03 — Feature creep despite the freeze

9 local branches beyond `main`, including `feature/employee-unification-20260702` (last commit 2026-07-03), `feature/escort-roster-hardening-20260703` (last commit 2026-07-04), `wip/employee-unification-20260704` (last commit 2026-07-04, "save uncommitted employee unification migrations 025-034") — all post-freeze. **Mitigation:** explicitly except or close every open feature branch by name. **Verification:** `git branch -a --sort=-committerdate` reviewed at each checkpoint, every branch logged with a one-line disposition.

## 04 — Secrets leaked in git-tracked documentation

Already occurred, not hypothetical — full detail in Report 5 §10. **Mitigation:** rotate immediately (Report 7 Tier 0a), independent of the rest of the timeline; add a pre-commit secret scan (gitleaks/trufflehog); decide with Owner sign-off whether to rewrite git history. **Verification:** confirm rotation via hash comparison in `fazle_admins.api_key_hash`; re-run a secret scan against the full repo.

## 05 — Release without full certification

`pre_production_acceptance_report.json` (2026-07-06, the only/latest file in its directory) returned `"verdict": "FAIL"`, `"status": "BLOCKS RELEASE"`. The go/no-go package's "NOT READY FOR GO" (Security, Ops) is followed by a "GO WITH CONDITIONS" recommendation — but **all four stakeholder sign-off lines are blank**, and the actual go/no-go meeting is scheduled for **2026-07-19** — no decision has actually been made yet as of this audit (2026-07-08). **Mitigation:** resolve the contradiction on paper before that meeting — either re-run the failing suites to a non-FAIL verdict, or have leads explicitly co-sign the conditions. **Verification:** post-meeting, confirm a signed decision record with all 4 lines filled, and a post-meeting acceptance run showing PASS or named waivers.

## 06 — Architecture drift (parallel frameworks)

Confirmed 3 independent "Phase" vocabularies in flight simultaneously: KB's own Phase 1-6 execution plan, a separate release-engineering Phase 1-5/FG1-FG5 process, and a 3rd hardening-workstream phase-numbering scheme (`reports/phase1`-`phase5`, T017-T023 test IDs). The KB governance model itself changed frameworks within 24 hours (v1.0 freeze → v2.0 "Phase 6" on 2026-06-23). **Mitigation:** namespace the three vocabularies; assign a single owner authorized to open a new "Phase" of any kind. **Verification:** grep for un-namespaced "Phase N," trending to zero.

## 07 — Knowledge divergence

Confirmed live, twice, independently, in this audit: `AUTO_REPLY_ENABLED` is `true` in `.env` (matching one KB decision log) but **still described as `false`/draft-mode in at least four other KB articles**; `ARCHITECTURE.md` states the monitoring stack is "Not configured" when it's confirmed running and actively scraping. **Mitigation:** correct the specific wrong articles against the actual decision log; add a scripted check diffing safety-critical `.env` flags against KB claims. **Verification:** the scripted check running as a scheduled job or CI step, not a manual audit every few weeks.

## 08 — Schema divergence

`wbom_employees` is still referenced in 29 Python files, `fpe_employees` (the codebase's own "canonical" table since 2026-07-02) in 14 — both live simultaneously. `AUDIT_SUMMARY.json` (explicitly stale-flagged) reports 208 tables against the KB's current 84 — a likely-legitimate but unconfirmed consolidation delta. **Mitigation:** deferred to Report 4's migration strategy; additionally, update `RECOVERY.md`'s verification SQL to include `fpe_employees`. **Verification:** see Report 4.

## 09 — Migration corruption / multi-stream migrations

Directly observed: migrations live in 4 separate mechanisms with independent numbering (Report 4); 4 files are currently untracked in git, at risk of loss. **Mitigation:** commit or explicitly abandon the 4 untracked files (Report 7 Tier 0e) before any accidental `git clean`. **Verification:** see Report 4; `git status --short` shows zero untracked files under any `migrations/` path going forward.

## 10 — Duplicate logic across modules

See Report 3 in full. Circumstantial corroboration: the `wbom_employees`/`fpe_employees` coexistence and the 4-mechanism migration split are exactly the structural conditions duplicate logic tends to accumulate under.

## 11 — AI hallucination / unsupervised writes (lower risk than the framing suggests)

`ai_console/adapters.py` defines explicit `prohibited_side_effects` per adapter class (business writes, unrestricted SQL, WhatsApp sends, destructive deletes all named and prohibited); the live orchestrator path instantiates only `Readonly*`/`AIMemoryAdapter` classes. **Not fully verified**: did not exhaustively trace every call site to confirm no other path ever hands a write-capable adapter to an LLM-driven flow — but the one confirmed AI-adjacent write path (admin approving a drafted reply) is explicitly human-gated. **Mitigation:** add a runtime assertion (not just a naming convention) that the orchestrator's adapters have no business-write capability. **Verification:** a CI test instantiating the live orchestrator and asserting, by introspection, an empty intersection between adapter capabilities and any write operation.

## 12 — Monitoring blind spot

See Report 5 §08. **Mitigation:** Promtail scrape target for app logs; minimal Alertmanager rule set routed to the existing WhatsApp admin path. **Verification:** deliberate test-window outage, confirm an alert fires through the stack, not only via the hand-built job.

## 13 — Unrate-limited public webhook ingress

See Report 5 §12. **Mitigation:** per-sender + global rate limit at the FastAPI middleware or nginx layer (already behind nginx per `AUDIT_SUMMARY.json`'s `web_server` field), independent of the freeze — closer to an operational-safety fix than a feature. **Verification:** load-test confirming throttling before LLM spend/DB load spikes.

## 14 — Payroll idempotency race path

See Report 5 §13. Data-safe (DB `UNIQUE` constraint holds); the gap is only the unhandled-exception caller experience under a genuine race. **Mitigation:** wrap the 3 `INSERT ... RETURNING id` call sites in a try/except for the unique-violation, re-fetching on conflict. **Verification:** a concurrency test firing two identical create-transaction requests simultaneously, asserting both return the same transaction id with a 2xx status.

---

# REPORT 9 — Technical Debt Register

Categorized; each item scored **Priority** (Critical/High/Medium/Low), with **Fix cost** (rough complexity) and **Risk if ignored**.

## Architecture

| Item | Reason | Business impact | Priority | Fix cost | Risk if ignored |
|---|---|---|---|---|---|
| `main.py` 3,703-line monolith mixing transport + inline business routes | Grew by accretion; own README admits it | Slower onboarding, higher change-risk per edit | Medium | High (large, careful extraction) | Continues to accrete; every new route adds to the same file |
| 8+ duplicated `X-Internal-Key` auth-check implementations | Copy-pasted, likely to dodge circular imports | Drift risk — a future auth-logic fix might only land in some copies | Medium | Low-Medium (extract shared dependency) | A security fix applied to one copy silently doesn't apply to the other 7+ |
| "Phase 12/13" `shared/` coordination layer (6 files, ~3,000 lines) started but largely unconsumed | Built ahead of actual wiring | False confidence ("we have self-healing/arbitration") when not actually intercepting real paths | Medium | Medium (wire in properly, or remove) | Continues to run as inert overhead; anyone debugging assumes it's doing something it isn't |
| `write_router.py`'s docstring mandate ("ALL writes must flow through this") contradicted by having exactly 1 diagnostic-only caller | Aspirational architecture never adopted | Misleading documentation for future engineers/AI agents | High | Medium (either enforce or correct the docstring) | An AI agent reading the docstring literally could assume a safety guarantee that doesn't exist |
| `shared/phone.py` layer inversion (depends upward on 2 `modules.*` packages) | Grew organically | Low direct impact, but breaks the intended dependency direction | Low | Low | Minor; mostly a documentation/discipline issue |
| Private-symbol cross-imports (`escort_roster`↔`escort`, `bridge_poller`→`message_router`) | Convenience during rapid iteration | Fragile — a rename in the "private" function silently breaks the caller | Low-Medium | Low (promote to public API or add explicit accessor) | Refactoring the "owning" module could break a caller with no compiler/type-checker warning (no static typing enforced) |

## Database

| Item | Reason | Business impact | Priority | Fix cost | Risk if ignored |
|---|---|---|---|---|---|
| 3 migration directories + inline DDL, with real cross-directory `ALTER` collisions on `fpe_cash_transactions`, `fpe_income_transactions`, `escort_roster_entries`, `wbom_escort_programs`, `fazle_payment_drafts` | Multiple initiatives (FPE, general, social) grew their own schema paths independently | Schema drift risk; hard to answer "what does production actually look like" from source alone | **Critical** | High | Silent divergence between environments; a future migration could conflict with one already applied via a different mechanism |
| 24 tables with zero schema source of truth outside a test fixture, including 2 confirmed live writers (`fazle_error_log`, `llm_conversation_log`) | Historical inline-DDL/manual-creation predates migration discipline | A clean environment cannot be rebuilt from this repo | **Critical** | Medium (one bootstrap migration from real `\d` output) | Continued impossibility of disaster recovery to a truly clean environment; `conftest.py`'s guessed schema already known to disagree with production |
| `db/new_tables.sql` orphan script with `ON CONFLICT DO UPDATE` KB seed | Superseded by numbered migrations but never deleted | If accidentally re-run, silently reverts admin-edited KB content | Medium | Low (delete or clearly quarantine) | Data-loss risk on accidental re-run |
| `database_rules.md`'s 84-table count stale (≥7 undocumented newer tables, 4 uncommitted) | Doc not updated since 2026-07-02, work continued after | Anyone trusting this doc undercounts real schema surface | Medium | Low (doc update) | Compounds every time a new table lands without a doc update |
| Duplicate `012`/`020` migration numbers, missing `009` | Ad hoc numbering under time pressure | Low direct risk (filename, not number, is the actual key) but confusing for humans/AI agents | Low | Low (documentation only) | Continued confusion about "canonical" numbering; risk of someone "fixing" it destructively |

## Backend

| Item | Reason | Business impact | Priority | Fix cost | Risk if ignored |
|---|---|---|---|---|---|
| RBAC fails open on WhatsApp admin-command channel | Defensive `except` written to avoid blocking legitimate commands on transient errors, over-corrected to bypass authorization entirely | A DB hiccup during an `rbac` check lets a financial approval command through with **no permission check** | **Critical** | Low | Genuine security/financial-control gap on the money-command channel |
| `drafts_router`/`social_auto_reply_router` imported but never mounted | Likely an incomplete refactor or an intentional-but-undocumented retirement | Drafts dashboard page likely non-functional in production right now; social-auto-reply has no HTTP pause/resume control | **High** | Low (one line, or explicit removal) | Live, customer/operator-facing dysfunction persists unnoticed |
| Payroll idempotency check-then-insert has no row lock / exception handling for the race path | DB `UNIQUE` constraint was trusted as sufficient, app-level graceful-return wasn't added | A genuine concurrent duplicate-approval race surfaces as an unhandled error, not a graceful idempotent response | Medium | Low (try/except around 3 call sites) | Rare, narrow, but a real rough edge on the money ledger |
| Two independent pay-calculation formulas feeding `escort_roster_entries` (`payment_workflow`'s vs. `escort_roster/calculations.py`'s) | Grew from two different modules solving adjacent problems without coordination | Possible silent pay-figure discrepancy depending on which path fires last for a given program | **High** (needs runtime verification — Report 3 §7 INVESTIGATE) | Medium (once resolved, requires picking one formula) | Could already be producing inconsistent numbers for closed programs; needs Tier 2 Task 3 resolution before further escort-roster work |
| `escort_lifecycle` bypasses `escort_roster`'s own API for a direct raw upsert into `escort_roster_entries` | Convenience during Batch 13 implementation | Two code paths writing the same table increases drift risk (contributes to the item above) | Medium-High | Low-Medium (call the existing API instead) | Continued risk of the two pay-formula paths diverging further |
| `outbound` vs. `social_auto_reply` duplicate send-queue infrastructure | Built independently for different channels without a shared base | Maintenance burden ×2 for retry/backoff/DLQ/rate-limit logic; `outbound`'s social branch is effectively dead code in practice | Medium | Medium-High | Bug fixes to one queue implementation won't apply to the other |
| Confirmed-orphaned modules (`operations_health`, `recruitment_assistant`, `payment` shim, `message_archive`, `payment_correction`) | Superseded or never wired in | Minimal direct impact — dead code — but inflates the module count anyone auditing this system has to reason about | Low | Low (archive/delete) | Continues to cost reviewer/AI-agent attention on every future audit |

## Frontend

| Item | Reason | Business impact | Priority | Fix cost | Risk if ignored |
|---|---|---|---|---|---|
| Zero shared JS across 6 dashboard HTML pages; each hand-rolls its own fetch/auth-header wrapper | No build tooling ever introduced for the frontend | A future auth-header change (e.g. token rotation) requires editing 6+ places by hand | Medium | Medium (extract one `common.js`) | Continued risk of one page silently missing a security-relevant fetch-layer fix applied to the others |
| `drafts.html` calling an unmounted backend (see Backend section) | Same root cause as the backend item above | Directly customer/operator-facing | **High** | Shared with the backend fix | Same as above |

## Infrastructure / Operations

| Item | Reason | Business impact | Priority | Fix cost | Risk if ignored |
|---|---|---|---|---|---|
| No CI/CD gate between edit and production; service runs directly off working tree | No deploy pipeline was ever built, small-team/AI-agent iteration speed prioritized | Any uncommitted or bad edit is one crash/restart away from being live | **Critical** | Medium (pre-restart gate is a small first step) | Continues to be the single largest release-safety gap (Risk 02 in Report 8) |
| Secrets committed to git-tracked docs | Convenience during 2026-06-26 HR-officer onboarding | Active credential exposure | **Critical** | Low (rotate + scrub) | Anyone with repo read access has working admin credentials right now |
| `ci-test.yml`'s `working-directory: core/` likely broken (no such nested folder exists) | Possibly written for a different repo layout, never validated after a restructure | The more thorough of the two CI pipelines may not be running at all | High | Low (path fix, once confirmed via Actions history) | False confidence that "CI covers this" when it may not be executing |
| No rate limiting on public webhook ingress | Not yet prioritized against a real incident | Cost/availability exposure (unthrottled paid-LLM-triggering traffic) | High | Low-Medium | One bad day (bug, bot, or bill) is unmitigated |
| Promtail not scraping app's own logs despite full Loki stack running | Stack was stood up generically, app-specific wiring never completed | Log-based incident investigation currently requires SSH+`tail`, not the dashboard stack already paid for in ops effort | Medium | Low (one scrape-config addition) | Continues to under-utilize existing infrastructure |
| No Alertmanager / alert rules despite Prometheus running | Same as above | Metrics are collected but nothing proactively pages a human off them | Medium | Low-Medium | Issues are caught only by the narrower, hand-built WhatsApp alerts |
| `RECOVERY.md`/`BACKUP.md` stale (reference `wbom_employees` only) | Not updated after the 2026-07-02 employee-unification migration | A real restore drill today would validate against a partially-outdated schema assumption | Medium | Low (doc update) | A real disaster-recovery event could be executed against subtly wrong verification queries |
| Rollback procedure never drilled (chaos checklist unchecked) | Chaos-testing program exists on paper, evidently not executed to completion | Unknown whether the documented rollback actually works under real conditions | Medium-High | Medium (run the drill once) | First real rollback attempt could be the first time it's ever tested |

## AI

| Item | Reason | Business impact | Priority | Fix cost | Risk if ignored |
|---|---|---|---|---|---|
| `ai_console`'s reverse-import of `app.main.app`, undocumented as deliberate (unlike `scheduler`'s equivalent) | Possibly a necessary pattern, possibly an oversight | Low direct impact, but ambiguous intent | Low | Low (add a comment, or refactor if accidental) | Minor; mostly a maintainability note |
| `ai_readonly_tools` depends on `admin_commands.date_parser` (a Critical business module) for a generic date-parsing utility | Convenience reuse | A change to the money-command module could inadvertently affect an AI-facing read tool | Low | Low (extract `date_parser` to a neutral shared utility) | Minor coupling risk, easy to fix |

## Knowledge Base

| Item | Reason | Business impact | Priority | Fix cost | Risk if ignored |
|---|---|---|---|---|---|
| `AUTO_REPLY_ENABLED` discrepancy across 4+ KB articles vs. live `.env` value | KB not updated when the flag changed | Anyone reading the more numerous "false" articles would misunderstand live production behavior | Medium | Low (doc correction) | Repeats the exact "knowledge divergence" failure mode already caught twice this audit |
| Duplicated/renumbered KB directory structure (`02_admin_knowledge` vs `02_admin_system`, 3 different `03_*`/`06_*` prefixes) | An un-migrated restructuring left old and new numbering coexisting | Confusing navigation for humans and AI agents alike | Low-Medium | Low-Medium (one-time rename/cleanup pass) | Continues to compound with every new KB article addition |
| `knowledge_base` (exact-match) and `rag` (semantic) retrieval paths run independently with no coordination | Built at different times for different needs, never unified | Possible inconsistent answers depending on which path a given query happens to hit first | Medium | Medium (define a clear precedence/fallback contract between them) | Low urgency today, but exactly the kind of thing Phase 3 (AI console unification) needs resolved before building on top of it |

## Testing

| Item | Reason | Business impact | Priority | Fix cost | Risk if ignored |
|---|---|---|---|---|---|
| ~Half of all modules have zero dedicated tests, including Critical/High-risk ones (`payment_ingest`, `employee_verification`, `scheduler`, `wa_chat_frontend`, `bridge_poller`) | Test-writing hasn't kept pace with feature velocity | Regressions in money/dispatch-adjacent code could ship undetected | **High** | High (writing real tests for 30+ modules is substantial work) | Continued blind spots on exactly the modules where a bug matters most |
| Two same-day reports disagree on exact pass/fail/xfail counts (561 vs. 553 passed) | Two separate report-generation runs, not reconciled | Minor — doesn't change the "0 failed" headline, but undermines confidence in "the" number | Medium | Low (re-run once, treat as canonical) | Continued ambiguity about which document is authoritative |
| 22-26 real regressions converted to `xfail` waivers rather than fixed | Deliberate, owner-signed-off deferral to "next sprint" — a legitimate call, not a cover-up | Deferred work has a real cost if "next sprint" keeps slipping | Medium | Varies per item | If never revisited, `xfail` becomes a permanent hiding place for known bugs |

---

# REPORT 10 — Master Execution Blueprint

## How this blueprint relates to prior planning

This blueprint does not replace the previously-approved roadmap (`~/.claude/plans/now-you-are-in-squishy-chipmunk.md`, approved 2026-07-08): **Phase 1 below is that roadmap's "Consolidation & Freeze Enforcement" phase, now fully detailed with evidence** (Reports 1-9 above). Phases 2-4 remain as previously scoped (dispatch/scheduling → accounting/GL → AI console unification), sequenced solo + AI-agent, one track at a time — with one addition from this audit's Report 6: **Owner decision (2026-07-08): the client-billing/invoicing/accounts-receivable gap IS in scope for Fazle Core.** Phase 2b (accounting/GL) must therefore cover both the cost side (existing payroll ledger) and the revenue side (client billing) — see the updated Phase 2b entry below. No tables or code have been created for this yet; this is a scope decision only, recorded so Phase 2b isn't planned around costs alone.

## Phase 1 — Consolidation & Freeze Enforcement

**Objective:** Make `BUSINESS-CONSTITUTION.md` Article VII true in practice, not just in writing — close the release-safety, migration-governance, and duplicate-logic gaps found in Reports 1-5, 8, 9 before any new business feature (including Phase 2's dispatch engine) is authorized.

**Deliverables:** All of Report 7's Tier 0-3 tasks. In order: secrets rotated + live bug fixes (drafts router, RBAC fail-open, AUTO_REPLY_ENABLED) → working-tree deployment gate → migration consolidation → duplicate-logic merges → dead-code cleanup → documentation alignment → RBAC alignment → frontend/dashboard/UI review → test-coverage triage → CI fix → rate limiting → monitoring completion → full pre-production acceptance re-run.

**Validation:** Report 7 Tier 3, Task 12 — a re-run of the pre-production acceptance suite must return a non-FAIL verdict, or every remaining failure carries an explicit, dated, Owner-approved waiver (not a silent `xfail`).

**Rollback:** Every Phase 1 task is either additive (new migrations, new tests, new docs) or independently revertible in isolation (each merge/cleanup as its own commit) — no task in this phase requires a phase-level rollback plan of its own.

**Success metrics:** Zero untracked files under any migration path; one migration source of truth; `git status --short` empty at rest; secrets rotated and no longer in git-tracked docs; drafts dashboard functional; WhatsApp and HTTP RBAC channels both fail closed; test suite has one authoritative, reconciled pass/fail count; pre-production acceptance passes or has fully-owned waivers.

**Definition of Done:** Report 7 Tier 3 Task 13 — Constitution Article VII §7.3's four conditions (all consolidation tasks complete, all tests pass, Constitution reviewed/updated, Owner approval obtained) are met and recorded.

**Go/No-Go criteria for Phase 2:** Do not begin Phase 2 (dispatch engine) until Phase 1's Definition of Done is signed off. Building new business logic on top of the schema/duplicate-logic instability found in Reports 3-4 would inherit that instability and risk repeating exactly the "freeze declared, freeze not held" pattern found in Report 8 Risk 01.

**Estimated timeline:** ~3-4 two-week sprints (per the previously-approved roadmap), though Report 7's Tier 0 items (secrets, drafts router, RBAC fail-open, AUTO_REPLY_ENABLED, untracked migrations) should be actioned within days, independent of the sprint cadence — they are small, independent, and some are active incidents.

**Critical path:** Tier 0 (parallel, immediate) → Tier 1 (deployment gate) → Tier 2 (migration consolidation must substantially precede the duplicate-logic merges, since several merges touch tables involved in the migration collisions) → Tier 3 (verification). The single longest-pole item is migration consolidation (Report 7 Task 2, "High" complexity, 2-3 sprints) — everything in Tier 2/3 that touches the database is safer after it lands.

**Milestones:** (1) All Tier 0 items closed — target: within 1 week of Owner sign-off on this plan. (2) Deployment gate live — target: end of sprint 1. (3) Migration consolidation complete, clean-environment rebuild proven — target: end of sprint 2-3. (4) Duplicate-logic merges + dead-code cleanup complete — target: end of sprint 3-4. (5) Full re-certification passes — target: end of sprint 4.

**Completion checklist:**
- [ ] Secrets rotated, scrubbed from tracked docs, pre-commit secret scan added
- [ ] Drafts dashboard confirmed functional (or explicitly retired)
- [ ] RBAC fails closed on both WhatsApp and HTTP channels
- [ ] `AUTO_REPLY_ENABLED` reconciled across `.env` and all KB articles
- [ ] 4 untracked migration files committed or explicitly abandoned
- [ ] Working-tree deployment gate live
- [ ] Single migration source of truth; 24 conftest-only tables backported into a real migration
- [ ] `outbound`/`social_auto_reply` queues consolidated; RBAC auth-check duplication merged; `escort_lifecycle`/`escort_roster` write-path unified; shared frontend JS extracted
- [ ] Confirmed-orphaned modules archived/deleted
- [ ] `RECOVERY.md`/`BACKUP.md`/`ARCHITECTURE.md`/KB docs corrected against live state; 3 "Phase" vocabularies namespaced
- [ ] Test-count discrepancy reconciled; the 5 named Critical/High untested modules have dedicated tests
- [ ] CI pipeline confirmed running (both workflows, or the working one identified and the broken one fixed/retired)
- [ ] Rate limiting live on public webhook ingress
- [ ] Promtail ingesting app logs; baseline Alertmanager rules live
- [ ] Pre-production acceptance suite passes (or fully-waived)
- [ ] Owner sign-off recorded per Constitution Article VII §7.3

## Phases 2-4 (unchanged from the previously-approved roadmap; referenced here for continuity)

- **Phase 2a — Dispatch/Scheduling Engine** (~3 sprints): availability calendar, conflict/double-booking detection, ranked-assignment suggestions plugged into the existing admin-typed WhatsApp assignment flow. Report 3 §7's dual-pay-formula item was investigated 2026-07-09 (see Phase 1.5 below) — dispatch reads guard availability/program data, not the payroll ledger, so it does not depend on that finding closing first; conditional GO per `docs/PHASE2_ENTRY_GATE.md`.
- **Phase 2b — Accounting/GL layer** (~3 sprints, scope expanded 2026-07-08): chart-of-accounts + debit/credit posting hook on `create_transaction()`; P&L/balance-sheet views; bKash/Nagad/bank-statement import + reconciliation; **and, per Owner decision 2026-07-08, the revenue side is explicitly in scope** — client billing, invoice generation, accounts receivable, client payment/outstanding-balance tracking, a link between escort/guard service delivery and the resulting client invoice, and GL postings for revenue/receivable/collection/adjustment/discount/bad-debt as needed. This is a real scope expansion versus the original roadmap (which only covered the cost side) — expect Phase 2b's duration estimate to grow once this is broken into its own execution plan; ~3 sprints was sized for costs-only.
- **Phase 3 — Unify into the AI Operating Console** (~2-3 sprints): runtime-ingest the curated `knowledge_base/` tree into RAG; extend `ai_readonly_tools`/`ai_console` with safe read-only tools over the new dispatch/GL data; single NL query surface in `open-chat.html`. **Before starting:** resolve Report 9's `knowledge_base`-vs-`rag` coordination gap — the console's answer quality depends on these two retrieval paths having a defined precedence.

## Phase 1.5 — Production Certification (2026-07-09)

A dedicated certification pass ran after this plan's fix passes and the `v1.0-consolidation-baseline` tag. Full evidence-based detail lives in **`docs/PRODUCTION_CERTIFICATION_v1.0.md`** and the checklist in **`docs/PHASE2_ENTRY_GATE.md`** — not duplicated here. Headline result: **NOT CERTIFIED FOR PHASE 2 (conditional, narrow gap)**. The previous pass's specific fixes (RBAC, routers, toordinal, credential rotation) hold up under fresh testing. Two items are larger than previously documented and need an Owner decision rather than more engineering: the migration/schema governance gap (~150 tables / ~88 columns of undocumented prod-vs-migration drift, versus the 27-table gap closed previously) and the escort-duty payroll pay-formula situation (Report 3 §7's "dual pay-formula INVESTIGATE" item is now resolved as an investigation — there are three formulas, not two, and production evidence shows none of them currently gates a real payment). Neither blocks Phase 2a (dispatch); both block Phase 2b (billing/GL) per the entry gate.

## Phase 1.6 — Canonical Business Rules (2026-07-09)

Following the certification pass's finding that the largest remaining risk is "Business Knowledge Drift" (naming/formula/status inconsistency, not code defects), a dedicated audit produced **`knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md`** — now the second-highest-priority document in the repo, immediately below `BUSINESS-CONSTITUTION.md`. It surveys 21+ business functions and documents (not silently resolves) every naming/formula/status conflict found: the escort-duty pay-formula situation from Phase 1.5 in full detail, a second concrete finding that the "Client" entity has no single canonical table (blocks Phase 2b's billing work independent of the payroll question), a real casing/vocabulary drift on `wbom_escort_programs.status`, and confirmation that `wbom_candidates`/`wbom_clients`/`fazle_clients`/`wbom_billing_records` are each real, named instances of the larger undocumented-migration gap. Not duplicated here — read that document directly.

## Overall Go/No-Go for the full program

Feasibility verdict (unchanged from the previously-approved roadmap, now backed by this audit's evidence): **Yes** — achievable. Everything found in this audit is a consolidation or correctness fix on top of already-substantial, genuinely mature infrastructure (payroll ledger, RBAC, outbound queue, RAG, dashboards, AI console scaffolding) — not evidence that the goal is unreachable. The risk this audit adds the most new evidence for is the same one already named in the prior roadmap: **convergence, not feasibility.** Nothing found here should change the "Yes" verdict; several findings (the live drafts-router bug, the RBAC fail-open bug, the leaked secrets) are reasons to execute Phase 1 with urgency, not reasons to reconsider the goal.

## Phase 1.7 — Client Domain Canonicalization (2026-07-09)

Following Phase 1.6's finding that Client was the least-resolved business function, a dedicated pass produced **`docs/CANONICAL_CLIENT_DOMAIN.md`** (discovery + architecture) and **`docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md`** (phased Phase 2A/2B/2C roadmap) — not duplicated here. Headline finding: the Client foundation is better than previously thought (`wbom_contacts` is already the de facto canonical table with a working sync pipeline; `wbom_billing_records` already exists in production with a complete, correct schema, just 0 rows and never wired up) but three uncoordinated client-classification mechanisms coexist with no single source of truth. Phase 2A (a small, low-risk reconciliation) is the recommended next implementation task, ahead of any Phase 2B billing code.
