# Fazle Core — System Atlas

**Version:** 1.0
**Date:** 2026-07-09
**Purpose:** the single entry point for understanding Fazle Core — what it is, who it's for, how it's built, what state it's in right now, and where to go for detail. Read this first. Everything below links to the document that actually owns that detail — this file summarizes and indexes, it does not duplicate.

**Authority order for anything below:** live code and live database > this Atlas > the documents it links to > memory of any past conversation. If this Atlas and the code ever disagree, the code is right and this file is stale — fix the file, don't trust it blindly.

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
| "What are the unbreakable rules for financial code?" | [`BUSINESS-CONSTITUTION.md`](BUSINESS-CONSTITUTION.md) — Article VII currently governs whether new features are even allowed (see §5 below) |
| "What's the long-term roadmap and feasibility verdict?" | `~/.claude/plans/now-you-are-in-squishy-chipmunk.md` |
| "Which migration directory do I add a new schema change to?" | [`db/MIGRATION_INVENTORY.md`](db/MIGRATION_INVENTORY.md) and [`knowledge_base/06_developer_system/database_rules.md`](knowledge_base/06_developer_system/database_rules.md)'s Migration Governance section — **always `db/migrations/`, never the other two legacy paths** |
| "How do I safely restart production?" | [`docs/DEPLOYMENT_RESTART_GATE.md`](docs/DEPLOYMENT_RESTART_GATE.md) (designed, not yet mechanically enforced) |
| "What's the organizational/AI-behavior knowledge base?" | `knowledge_base/00_governance/` (governance/decisions), `knowledge_base/06_developer_system/` (developer-facing KB articles) — this is a separate, older knowledge system with its own governance culture; this Atlas is the newer, code-verified layer on top of it, not a replacement |

---

## 3. Architecture in one paragraph

FastAPI app (`app/main.py`, composition root) ingests WhatsApp messages from two self-hosted bridges (SQLite polling) and Meta's webhook (WhatsApp Cloud/Messenger/FB comments), resolves sender identity (`identity_brain`), routes by priority tier (`message_router`) into recruitment/payroll/escort/admin-command/AI-reply workflows, and persists to a single PostgreSQL database (`ai-postgres` container) with no ORM. Money moves through exactly one canonical function, `fazle_payroll_engine.accounting.create_transaction()` (enforced by `BUSINESS-CONSTITUTION.md`). AI (Ollama, local) only ever reads business data through named, SELECT-only tools (`ai_readonly_tools`) — it cannot write to production tables or send messages directly. The service runs **directly off the git working tree** with no build step — a restart loads whatever is currently on disk, reviewed or not. Full detail: `docs/DEVELOPER_GUIDE.md` §B, `docs/MODULE_MAP.md`.

---

## 4. Current operational state (as of 2026-07-09, this session)

This section is a point-in-time snapshot from the most recent consolidation work. **Re-verify against live systemctl/git status before trusting it in a future session** — it will go stale.

- **Service:** `fazle-core.service`, restarted twice this session (07:32:45 and 07:35:05 CEST, 2026-07-09) to pick up fixes below. Currently active/healthy.
- **Fixed and live:** the RBAC fail-open bug (now fails closed), the `drafts_router`/`social_auto_reply_router` unmounting regression (both restored), and the escort-roster `toordinal` date-filter crash (`modules/escort_roster/db.py` — `start_from`/`start_to` now parsed into real `date` objects before binding). All three verified live via real HTTP requests post-restart, not just unit tests.
- **Rotated:** `INTERNAL_API_KEY` (the master bypass credential) — the old leaked value is confirmed rejected, the new value confirmed working. The 2 HR-officer API keys and 2 passwords found during the credential audit were confirmed **dead** (no matching live RBAC record, never read by any code path) — cleanup hygiene only, not an active risk.
- **New finding, not yet fixed:** `modules/rag`'s vector index rebuild re-encodes the entire KB corpus (~4,400 chunks) on every restart with no persisted embedding cache, blocking both admin RAG stats **and the customer-facing RAG search path** for 10+ minutes per restart. Queued in `docs/NEXT_IMPLEMENTATION_QUEUE.md` Tier 2 — not a data-integrity risk, but a real availability gap every time this service restarts.
- **Not yet done:** committing any of this session's work (nothing has been committed or pushed all session), the migration-reconciliation work (3 legacy migration paths still need consolidating into `db/migrations/`), the dual pay-formula question in escort/payroll, and Phase 2 (dispatch/billing/GL) has not started.
- **Governance state:** `BUSINESS-CONSTITUTION.md` Article VII's feature freeze is still nominally in force pending the System Consolidation Sprint — see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` for the full audit of how well that freeze has actually held (verdict: imperfectly, several in-flight feature branches exist uncommitted alongside this consolidation work).

For the full blow-by-blow (files changed, tests run, exact validation commands), see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`'s "Current Consolidation State" and "Release Preparation State" sections — this Atlas intentionally does not repeat that detail.

---

## 5. Rules that override casual judgment

1. **`BUSINESS-CONSTITUTION.md` Article I** — exactly one function creates a financial transaction (`create_transaction()`). No exceptions, no parallel paths.
2. **`BUSINESS-CONSTITUTION.md` Article VII** — no new business features until the System Consolidation Sprint is formally closed out. Phase 2 (dispatch/billing/GL) should not start until this is genuinely satisfied, not just declared.
3. **Migration governance** — all new schema changes go in `db/migrations/`, tracked via `db/migrate.py`. Never the other two legacy migration directories, never inline DDL in a new module.
4. **AI/production boundary** — Ollama and the AI console never get direct DB write access, unrestricted SQL, or the ability to send WhatsApp messages. Any change that would blur this line needs explicit Owner sign-off, not an incremental judgment call.
5. **Working-tree deployment risk** — because there's no build step, treat every restart as "whatever is on disk goes live." Check `git status` before restarting; know what you're actually deploying.

---

## 6. How to keep this file useful

Update §4 ("Current operational state") at the end of any session that changes production behavior, fixes a bug, or restarts the service — a few sentences, with a pointer to the detailed report, not a copy of it. Don't let this file grow into a second copy of `docs/DEVELOPER_GUIDE.md` or `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` — if you're about to write more than a paragraph about something, it belongs in one of those instead, with a link added here.
