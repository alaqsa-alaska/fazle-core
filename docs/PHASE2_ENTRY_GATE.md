# Phase 2 Entry Gate

**Purpose:** the single checklist that decides whether Phase 2 (Dispatch → Client Billing → General Ledger) may begin. Sourced from `docs/PRODUCTION_CERTIFICATION_v1.0.md` (2026-07-09) — read that document for the evidence behind each line here; this file is the gate, not the report.

**Rule:** re-run this checklist before Phase 2a and again before Phase 2b — dispatch and billing/GL have different prerequisites (see GO/NO-GO below), so passing the gate once does not mean both sub-phases are cleared.

---

## Mandatory checks

| # | Check | Status as of 2026-07-09 | Required for |
|---|---|---|---|
| 1 | RBAC fail-closed verified live | ✅ PASS (27/27 + 19/19 tests) | 2a + 2b |
| 2 | No known leaked/live-risk credentials | ✅ PASS (`INTERNAL_API_KEY` rotated, confirmed) | 2a + 2b |
| 3 | Working tree matches a tagged, reviewed commit before any restart | ✅ PASS (`v1.0-consolidation-baseline`, clean tree) | 2a + 2b |
| 4 | Test suite: 0 setup/fixture errors | ✅ PASS (was 27, now 0) | 2a + 2b |
| 5 | Test suite: all real failures classified with root cause | ✅ PASS (29/29 classified in certification report) | 2a + 2b |
| 6 | Test suite: all real failures either fixed or explicitly Owner-accepted | ⚠️ PARTIAL — 4 fixed this session, 29 classified but not all fixed (see report) | 2a + 2b (soft — see notes) |
| 7 | Migration governance: production schema fully reproducible from tracked migrations | ❌ **FAIL** — ~150 tables / ~88 columns undocumented drift | 2b (hard) — 2a soft |
| 8 | Payroll cost-ledger linkage trustworthy (`program_id` populated, one enforced pay formula) | ❌ **FAIL** — `program_id` NULL on all 3,671 transactions; 3 unenforced formulas | 2b (hard) — not required for 2a |
| 9 | RAG availability gap does not block customer-facing replies during business hours | ⚠️ PARTIAL — confirmed daily 15-20 min block at 18:00; not fixed, only documented | 2a + 2b (soft — see notes) |
| 10 | Owner approval to proceed, recorded in writing | ⏳ Pending this document's review | 2a + 2b |
| 11 | Production health at time of Phase 2 kickoff (service active, no unresolved P0) | ✅ PASS as of last restart this session | 2a + 2b |

**Hard** = must be closed before that sub-phase starts, no exceptions. **Soft** = Owner may explicitly accept the risk and proceed; must be a recorded decision, not silence.

---

## Required documentation

- `SYSTEM_ATLAS.md` — current operational state section, updated each session that changes production behavior. ✅ current.
- `docs/PRODUCTION_CERTIFICATION_v1.0.md` — this gate's evidence source. ✅ exists.
- `docs/NEXT_IMPLEMENTATION_QUEUE.md` — Tier 0/1/2 items reflecting current, not stale, priority. ✅ updated this session.
- `BUSINESS-CONSTITUTION.md` Article VII — freeze conditions checked against reality, not just declared satisfied.

## Required test status

- 0 setup/fixture errors (met).
- All classified failures either fixed or have a named Owner-accepted-debt entry in `docs/NEXT_IMPLEMENTATION_QUEUE.md` (partially met — 29 classified, 4 fixed, 25 queued not yet individually Owner-accepted).
- e2e suite run separately from the async suite, both green or with named accepted debt (not yet re-run in isolation this session — **Not verified**, run `pytest tests/e2e` on its own before relying on its current pass/fail state).

## Required security status

- RBAC fail-closed live (met).
- No credential with confirmed live risk unrotated (met).
- Webhook signature enforcement status explicitly reconfirmed by Owner as intentional, not just historically logged (open — see certification report's Security Status).

## Required migration status

- Every table/column that live application code reads or writes has a migration file that would reproduce it from scratch (**not met** — the ~150/~88 gap).
- No new schema changes land outside `db/migrations/` (governance rule in place, holding for new work; does not retroactively fix the existing gap).

## Required restart status

- Restart procedure is known and was exercised successfully this session (met).
- RAG rebuild does not block customer-facing replies during business hours (**not met** — daily 18:00 recurrence confirmed).

## Required Owner approvals

- [ ] Payroll pay-formula/control-gap decision (which formula is canonical; how the `PAID` command should validate against it)
- [ ] Schema Reconciliation Sprint scoped and scheduled, or explicit written risk acceptance to proceed without it
- [ ] RAG daily-rebuild fix scheduled, or explicit written risk acceptance of the current daily customer-facing gap
- [ ] Confirmation that Phase 2a (dispatch) may proceed independently of items above (it has no dependency on either), if Owner wants to keep moving rather than wait

## Required production health

- Service active, health checks green, no unresolved P0 at time of kickoff — met as of this session's final restart and validation.

---

## GO / NO-GO

### Phase 2a (Dispatch/Scheduling)

**Conditional GO.** Dispatch reads guard availability and escort program data — it has no dependency on the payroll-formula gap (#8) or the migration-drift gap (#7, soft for 2a). The RAG gap (#9) and the 25 not-yet-fixed test failures (#6) are real but don't block dispatch specifically; they should be tracked, not used to stall Phase 2a. Proceed once Owner approval (#10) is recorded and RAG risk (#9) is either scheduled for a fix or explicitly accepted.

### Phase 2b split, 2026-07-09 — Owner decision narrowed the scope

The Owner formally decoupled billing from GL/payroll-cost reconciliation (`docs/PHASE2B_READINESS_REPORT.md` Decision 1): **Phase 2B is now billing-only** (invoicing, AR, contact-level payment tracking, triggered only on program confirmed/completed final-client-slip state) — General Ledger and payroll-cost linkage move to a later, separate phase.

#### Phase 2B (Client Billing — billing-only, no GL)

**Split status, updated 2026-07-09 (post re-audit):**
- **Schema/foundation (Migration 032, client tier/status):** GO — applied and verified.
- **Invoice-number generator:** GO — implemented, tested (schema check, scratch-DB concurrency + rollback safety, permanent unit tests), not yet applied to production, no blocker.
- **Bill creation, payment recording, outstanding-balance view, admin API, readonly tools, dashboard, audit logging:** **NO GO — new hard blocker found.** A fresh, evidence-based re-audit (`docs/PHASE2B_READINESS_REPORT.md` Re-Audit section) found no client-facing rate/price field exists anywhere in this schema — every candidate field is the guard's cost, not the client's charge. #8 (payroll cost-ledger trust) no longer blocks this narrowed billing-only scope, as previously noted, but this new finding does: building bill creation without a real `service_charge` source would either produce a meaningless bill or recreate the exact unvalidated-free-text-amount risk already flagged in the Payroll certification finding. Proceed only once the Owner has decided what determines `service_charge` (`docs/PHASE2B_READINESS_REPORT.md` New Owner Decisions Required #1).

#### Phase 2c (General Ledger / payroll-cost reconciliation) — later, separate phase

**NO GO** until #8 (payroll cost-ledger trust) is closed or the Owner explicitly, in writing, accepts the risk of posting revenue-side ledger entries against an unenforced cost ledger. This is now its own phase, not bundled with billing — building GL on an unenforced cost ledger repeats the exact failure mode Article VII's freeze exists to prevent.
