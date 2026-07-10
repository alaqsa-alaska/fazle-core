# Fazle Core — System Atlas

**Version:** 1.0
**Date:** 2026-07-09
**Purpose:** the single entry point for understanding Fazle Core — what it is, who it's for, how it's built, what state it's in right now, and where to go for detail. Read this first. Everything below links to the document that actually owns that detail — this file summarizes and indexes, it does not duplicate.

**Authority order for anything below:** live code and live database > `BUSINESS-CONSTITUTION.md` (immutable governance) > `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md` (the single source of truth for current business rules/naming — see below) > this Atlas > the documents it links to > memory of any past conversation. If this Atlas and the code ever disagree, the code is right and this file is stale — fix the file, don't trust it blindly.

---

## 1. What Fazle Core is (business context)

Fazle Core is the production WhatsApp-first operations backend for **Al-Aqsa Security & Logistics Services Ltd**, a security-guard and vessel-escort company. It runs recruitment, employee/guard payroll, escort (vessel security) service intake and duty tracking, client and employee WhatsApp communication, and an AI console over that data — for one company, not a generic platform.

**Who uses it:** the Owner/Admin (runs the business via WhatsApp commands + web dashboards), HR/office assistants (restricted payroll access), the accountant, employees/guards, recruitment candidates, and corporate escort/vessel clients.

**The long-term goal** (owner-approved, 2026-07-08): turn this into the *complete* operating system for the business — payroll, dispatch, client billing, and full accounting, queryable through one AI console. **Verdict: achievable** — see `~/.claude/plans/now-you-are-in-squishy-chipmunk.md` for the full feasibility analysis and roadmap. The gap between "where it is" and "that goal" is the Phase 1/2/3 roadmap referenced throughout this Atlas.

**Full narrative:** [`README.md`](README.md) §"Product Overview".

---

## 2. Where to find things (document map)

| Question | Answer lives in |
|---|---|
| "What is this app, who's it for, how do I run it?" | [`README.md`](README.md) |
| "How does the runtime/infra work — servers, ports, external services?" | [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) §B |
| "What does each frontend page do, what APIs/search fields does it use?" | [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) §C |
| "What does module X do, what tables does it touch, how risky is it?" | [`docs/MODULE_MAP.md`](docs/MODULE_MAP.md) |
| "How does money/payroll actually work end to end?" | [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) §E, plus [`BUSINESS-CONSTITUTION.md`](BUSINESS-CONSTITUTION.md) for the governing rules |
| "How does an employee get created, and why are there two employee tables?" | [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) §F |
| "How does an escort order become a paid duty?" | [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) §G |
| "How does message routing / AI / RAG work, what can the AI touch?" | [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) §H |
| "What's broken, what's already fixed, what's next?" | [`docs/NEXT_IMPLEMENTATION_QUEUE.md`](docs/NEXT_IMPLEMENTATION_QUEUE.md) (the actionable queue) and [`SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`](SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md) (the full 10-report audit + evidence) |
| "Is this system actually certified/ready for Phase 2?" | [`docs/PRODUCTION_CERTIFICATION_v1.0.md`](docs/PRODUCTION_CERTIFICATION_v1.0.md) (evidence-based Phase 1.5 certification) and [`docs/PHASE2_ENTRY_GATE.md`](docs/PHASE2_ENTRY_GATE.md) (the GO/NO-GO checklist itself) |
| "What does `employee_mobile` vs `payout_phone` vs `canonical_employee_id` actually mean — which name is canonical?" / "Which of 3 pay formulas is correct?" / "What are the canonical status values for X?" | [`knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md`](knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md) — the single source of truth for business rules, naming, and known conflicts (second-highest-priority document after the Constitution) |
| "What is 'a client' in this system — which table, which fields, is billing ready?" | [`docs/CANONICAL_CLIENT_DOMAIN.md`](docs/CANONICAL_CLIENT_DOMAIN.md) (discovery + architecture) and [`docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md`](docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md) (phased fix, Phase 2A/2B/2C) |
| "What are the unbreakable rules for financial code?" | [`BUSINESS-CONSTITUTION.md`](BUSINESS-CONSTITUTION.md) — Article VII currently governs whether new features are even allowed (see §5 below) |
| "What's the long-term roadmap and feasibility verdict?" | `~/.claude/plans/now-you-are-in-squishy-chipmunk.md` |
| "Which migration directory do I add a new schema change to?" | [`db/MIGRATION_INVENTORY.md`](db/MIGRATION_INVENTORY.md) and [`knowledge_base/06_developer_system/database_rules.md`](knowledge_base/06_developer_system/database_rules.md)'s Migration Governance section — **always `db/migrations/`, never the other two legacy paths** |
| "How do I safely restart production?" | [`docs/DEPLOYMENT_RESTART_GATE.md`](docs/DEPLOYMENT_RESTART_GATE.md) (designed, not yet mechanically enforced) |
| "What's the organizational/AI-behavior knowledge base?" | `knowledge_base/00_governance/` (governance/decisions), `knowledge_base/06_developer_system/` (developer-facing KB articles) — this is a separate, older knowledge system with its own governance culture; this Atlas is the newer, code-verified layer on top of it, not a replacement |

---

## 3. Architecture in one paragraph

FastAPI app (`app/main.py`, composition root) ingests WhatsApp messages from two self-hosted bridges (SQLite polling) and Meta's webhook (WhatsApp Cloud/Messenger/FB comments), resolves sender identity (`identity_brain`), routes by priority tier (`message_router`) into recruitment/payroll/escort/admin-command/AI-reply workflows, and persists to a single PostgreSQL database (`ai-postgres` container) with no ORM. Money moves through exactly one canonical function, `fazle_payroll_engine.accounting.create_transaction()` (enforced by `BUSINESS-CONSTITUTION.md`). AI (Ollama, local) only ever reads business data through named, SELECT-only tools (`ai_readonly_tools`) — it cannot write to production tables or send messages directly. The service runs **directly off the git working tree** with no build step — a restart loads whatever is currently on disk, reviewed or not. Full detail: `docs/DEVELOPER_GUIDE.md` §B, `docs/MODULE_MAP.md`.

---

## 4. Current operational state (as of 2026-07-09, Phase 1.6/1.7 pass)

This section is a point-in-time snapshot from the most recent consolidation work. **Re-verify against live systemctl/git status before trusting it in a future session** — it will go stale.

**Phase 1.5 (Production Certification) result: NOT CERTIFIED FOR PHASE 2 — conditional, narrow gap.** Full evidence in `docs/PRODUCTION_CERTIFICATION_v1.0.md`; the gate itself is `docs/PHASE2_ENTRY_GATE.md`. Short version: the specific bugs fixed in the previous pass (RBAC, routers, toordinal) are genuinely fixed and re-verified. Two larger, newly-evidenced gaps remain open and need an Owner decision, not more engineering guessing: (1) production's schema has ~150 tables and ~88 columns with zero corresponding migration file anywhere in the repo — larger than previously documented; (2) escort-duty payroll has three separate pay formulas (flat 400/day monthly rollup, shift-based roster reconciliation, `basic_salary/30` payment-draft) and production evidence shows **none of them currently gates a real payment** — real money moves via a free-typed admin amount with no cross-check. Neither is an active incident. Dispatch (Phase 2a) doesn't depend on either and has a conditional GO; Billing/GL (Phase 2b) does depend on both and is NO GO until they close or the Owner explicitly accepts the risk.

Also fixed this pass, evidence-based (not guesswork): the test suite looked catastrophically broken (376 setup errors) but was actually two narrow, unrelated causes — e2e/Playwright tests corrupting the shared event loop when run in the same process as the async DB suite, and a comment-handling bug in the test harness's own SQL splitter that silently dropped real `ALTER TABLE` statements. Both root-caused and the splitter fixed; e2e must simply never share a process with the rest of the suite. Real failures dropped from 40 to 29 after 4 small, safe, test-only/migration-only fixes; the remaining 29 are individually classified in the certification report, mostly stale tests that predate an intentional 2026-06-19 safety guard on escort-release finalization.

Also newly found: the RAG index-rebuild availability gap (previously documented as restart-only) actually recurs **every day at 18:00** via a scheduler cron job, not just on restart — escalated from Tier 2 to Tier 1 in the implementation queue.

**Phase 2B (Client Billing Activation), 2026-07-09:** Migration 032 (client tier/status model) applied to production. A fresh re-audit found the **single largest blocker in the whole client/billing effort**: no client-facing rate/price field exists anywhere in this schema (only guard-cost fields do) — bill creation cannot proceed until the Owner decides what determines `service_charge`. The one decision-independent piece — a concurrency-safe, gap-free `AQS-YYYY-NNNNNN` invoice-number generator (`modules/billing/invoice_numbering.py` + `db/migrations/033_invoice_number_counters.sql`) — was implemented and tested (schema check, scratch-DB concurrency/rollback tests, permanent unit tests), but **not applied to production** yet. Full detail: `docs/PHASE2B_READINESS_REPORT.md`.

**Phase 1.6/1.7 (Canonical Business Rules + Client Domain Canonicalization), same day:** produced `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md` (naming/formula/status audit across 21+ business functions) and `docs/CANONICAL_CLIENT_DOMAIN.md` + `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` (the deepest single finding: `wbom_billing_records` already exists in production with a correct, ready schema — 0 rows, never wired to any code — and three uncoordinated client-classification mechanisms coexist with no single source of truth). Architecture and discovery only — no migrations applied, no code changed.

- **Service:** `fazle-core.service`, restarted twice this session (07:32:45 and 07:35:05 CEST, 2026-07-09) to pick up fixes below. Currently active/healthy.
- **Fixed and live:** the RBAC fail-open bug (now fails closed), the `drafts_router`/`social_auto_reply_router` unmounting regression (both restored), and the escort-roster `toordinal` date-filter crash (`modules/escort_roster/db.py` — `start_from`/`start_to` now parsed into real `date` objects before binding). All three verified live via real HTTP requests post-restart, not just unit tests.
- **Rotated:** `INTERNAL_API_KEY` (the master bypass credential) — the old leaked value is confirmed rejected, the new value confirmed working. The 2 HR-officer API keys and 2 passwords found during the credential audit were confirmed **dead** (no matching live RBAC record, never read by any code path) — cleanup hygiene only, not an active risk.
- **Observed, self-resolved, now queued:** `modules/rag`'s vector index rebuild re-encoded the entire KB corpus (~4,400 chunks) after each restart with no persisted embedding cache, blocking both admin RAG stats and the customer-facing RAG search path for roughly 15-20 minutes. Confirmed via CPU monitoring it was actively computing (not deadlocked) and did fully recover — `/rag/stats` and `/rag/search` both verified working again post-recovery. Queued in `docs/NEXT_IMPLEMENTATION_QUEUE.md` Tier 2 as a real availability gap on every restart, not a data-integrity risk.
- **Committed and tagged:** all of this session's consolidation work landed in 7 commits, tagged `v1.0-consolidation-baseline` (working tree is clean as of this tag). The separate, pre-existing REL-2026-07-W1 in-flight feature work (location_adapter, preflight_guard, model_router, escort/payroll/identity_brain/message_router changes) was committed immediately after, in its own commit, explicitly labeled as out-of-scope of the baseline tag — it was already live in production before this sprint and committing it just makes source control match deployed reality; it has not been reviewed as part of this consolidation. **Nothing has been pushed to `origin/main`** — that's a separate, explicit decision for later.
- **Not yet done:** the migration-reconciliation work (2 legacy migration paths still need consolidating into `db/migrations/`), the dual pay-formula question in escort/payroll, and Phase 2 (dispatch/billing/GL) has not started.
- **Governance state:** `BUSINESS-CONSTITUTION.md` Article VII's feature freeze is now anchored at the `v1.0-consolidation-baseline` tag — the REL-2026-07-W1 commit sitting on top of it is the acknowledged, named exception that should be reviewed on its own merits before Phase 2 begins, not silently treated as part of the frozen baseline.

For the full blow-by-blow (files changed, tests run, exact validation commands), see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`'s "Current Consolidation State" and "Release Preparation State" sections — this Atlas intentionally does not repeat that detail.

**Office Payroll Hardening (2026-07-10/11):** the dual pay-formula question above is now resolved by Owner decision — `modules/payment_workflow::create_escort_payment_draft` (the real, live escort-payment path) is fixed to use the shift-based day-rate (matching `escort_roster/calculations.py`, not `basic_salary/30`) with conveyance added, not subtracted. A separate, new office-staff monthly-salary pipeline was built (`modules/payroll/eligibility.py`, `modules/payroll/dry_run.py`, `modules/attendance/{crud,routes}.py`, migration 040) — manual + admin-approved-supervisor-message attendance only, escort attendance never counts. **Confirmed: 0 of 188 employees are currently eligible** (184 are Escort, the rest fail salary/active/placeholder checks) — correct and dormant, not broken. `job_daily_payroll` is now guarded behind `settings.office_payroll_authoritative_enabled` (default False) and creates no payroll rows until Owner sign-off; `compute_run`'s own internal formula still needs rewriting to the new calendar-days formula before that flag can safely flip. Full detail: `docs/NEXT_IMPLEMENTATION_QUEUE.md`, `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md` §Payroll/§Attendance.

---

## 5. Rules that override casual judgment

1. **`BUSINESS-CONSTITUTION.md` Article I** — exactly one function creates a financial transaction (`create_transaction()`). No exceptions, no parallel paths.
2. **`BUSINESS-CONSTITUTION.md` Article VII** — no new business features until the System Consolidation Sprint is formally closed out. Phase 2 (dispatch/billing/GL) should not start until this is genuinely satisfied, not just declared.
3. **Migration governance** — all new schema changes go in `db/migrations/`, tracked via `db/migrate.py`. Never the other two legacy migration directories, never inline DDL in a new module.
4. **AI/production boundary** — Ollama and the AI console never get direct DB write access, unrestricted SQL, or the ability to send WhatsApp messages. Any change that would blur this line needs explicit Owner sign-off, not an incremental judgment call.
5. **Working-tree deployment risk** — because there's no build step, treat every restart as "whatever is on disk goes live." Check `git status` before restarting; know what you're actually deploying.
6. **`knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md`** — no module, migration, or AI agent may invent a new name for an existing entity, a new status value, or a new formula for an existing business calculation without first checking (and if needed, updating) that document. Business policy may change any time at the Owner's discretion; business structure (names, tables, IDs, statuses) may not silently diverge across modules.

---

## 6. How to keep this file useful

Update §4 ("Current operational state") at the end of any session that changes production behavior, fixes a bug, or restarts the service — a few sentences, with a pointer to the detailed report, not a copy of it. Don't let this file grow into a second copy of `docs/DEVELOPER_GUIDE.md` or `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` — if you're about to write more than a paragraph about something, it belongs in one of those instead, with a link added here.
