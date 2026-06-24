---
title: PKMA Report 11 — Workflow Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 11 — Workflow Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the maturity of each operational workflow documented in the Knowledge Base. A workflow is considered mature when its full lifecycle (triggers, processing steps, state transitions, admin commands, edge cases) is documented, production-verified, and management-approved.

---

## Workflow Inventory

The platform has 6 primary workflows, all in `05_workflows/`:

1. Attendance Workflow
2. Escort Workflow
3. Payment Workflow (advance + salary advance)
4. Salary / Payroll Workflow
5. Recruitment Workflow
6. Release Slip Workflow

---

## Workflow 01 — Attendance

**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `attendance_workflow.md` |
| Trigger | Employee sends attendance message via WhatsApp |
| Parser | Date formats (DD-MM-YYYY, YYYY-MM-DD), shift D/N, mobile number, name heuristic |
| State Machine | pending → approved / rejected / expired |
| Storage | `wbom_attendance` (ON CONFLICT UPDATE — upsert semantics) |
| Duplicate Detection | ON CONFLICT UPDATE ensures idempotency |
| Admin Command | APPROVE (multi-ID, Bangla digits supported, HK-38/39) |
| Edge Cases | Bangla digit normalization; multi-record approval in single command |
| Management Decision | DUP-05 (separation of attendance vs attendance_parser modules) |
| Missing | Expired draft cleanup details; who can reject (role requirement) |
| Production Verified | Yes (Wave-1) |

**Path to Level 4:** Document expired state cleanup + rejection role → PKVC → Level 4.

---

## Workflow 02 — Escort Program

**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `escort_workflow.md` |
| Trigger | Admin creates escort order; employee confirmed when order locked |
| Order Parser | 4 formats: labeled block, inline compact, MV-block, numbered list |
| State Machine | draft → confirmed → Assigned → Running → Completed / Cancelled |
| Admin Command | ESCORTCONFIRM with syntax and example documented |
| Stale Alert | 30-day rule, ESCORT_STALE_DAYS env, reminder scheduler job |
| Management Decisions | CON-01, CON-02, CON-03, CON-04 (financial rules for escort) |
| Missing | Escort order parser regex patterns not in KB |
| Production Verified | Yes (Wave-1) |

**Path to Level 4:** Document parser regex in `parser_engine.md` → PKVC → Level 4.

---

## Workflow 03 — Payment (Advance)

**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `payment_workflow.md` |
| Trigger | Employee sends advance request (18 keywords or 5 short-form phrases) |
| Draft Creation | AI parses request → creates payment draft in pending state |
| State Machine | pending → sent / rejected / expired (24h TTL) |
| Employee Verification | 5-step: STEP_SELFIE → STEP_SLIP → STEP_METHOD → STEP_DONE / rejected |
| Admin Commands | PAID (mark as paid), ADVANCE (authorize advance) with syntax |
| Payment Reconciliation | Hourly job (payment_reconciliation); mobile-tail-11 matching |
| Management Decisions | CON-01, CON-02 (payment formula); HK-43 (advance keywords) |
| Missing | Edge cases: what happens after STEP_METHOD but before STEP_DONE |
| Production Verified | Yes (Wave-1) |

**Path to Level 4:** Document verification edge cases → PKVC → Level 4.

---

## Workflow 04 — Salary / Payroll

**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `salary_workflow.md` |
| Trigger | Daily auto-compute at 02:00 OR admin runs PAYROLL START |
| State Machine | draft → reviewed → approved → locked → paid; cancelled from any non-paid |
| ALLOWED_TRANSITIONS | All 5 transitions documented with source function |
| Idempotency | UNIQUE constraint prevents duplicate payroll runs |
| Audit | wbom_payroll_approval_log records every state change |
| Admin Commands | 7 PAYROLL commands: START, REVIEW, APPROVE, LOCK, MARK-PAID, CANCEL, STATUS |
| Management Decision | CON-01 (formula 12000/30×days) |
| DUP Conflict | DUP-03 (salary display format) — PENDING |
| Missing | Bulk payroll scenarios; partial month override; correction-after-lock scenario |
| Production Verified | Yes (Wave-1) |

**Note:** DUP-03 prevents Level 4 until resolved.
**Path to Level 4:** Resolve DUP-03 → document missing scenarios → PKVC → Level 4.

---

## Workflow 05 — Recruitment

**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| KB Article | `recruitment_workflow.md` |
| Trigger | Candidate contacts via WhatsApp/social; intent classified as recruitment |
| Session State Machine | 7-step: new → step:name → step:age → step:area → step:position → step:experience → step:phone → scored / expired |
| Session TTL | 24h (HK-33) |
| Age Validation | 18–55 (BR-25 resolved) |
| Scoring | Experience (60/40/20/0), position (20), completeness (20); max 100 |
| Role Gate | Recruiting-blocked roles cannot enter recruitment flow (HK-05) |
| AI Brain | 4 deterministic categories; Bangla fallback message; reads resources/ops/recruitment_source_of_truth.txt |
| Storage | fazle_recruitment_sessions |
| Management Decisions | BR-25, HK-33, HK-34, HK-36 |
| DUP Conflict | DUP-06 (FAQ wording) — PENDING |
| Missing | Resumed session behavior; concurrent session handling |
| Production Verified | Yes (Wave-1) |

**Path to Level 4:** Resolve DUP-06 → document session edge cases → PKVC → Level 4.

---

## Workflow 06 — Release Slip

**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `release_slip_workflow.md` |
| Trigger | Admin uploads image OR sends text release confirmation |
| OCR Path | JPG/JPEG/PNG/WEBP; 1KB–8MB; 4 document type detection |
| Text Parser | 6 regex fields: _RC_DATE_RE, _RC_SHIFT_RE, _RC_POINT_RE, _RC_DAYS_RE, _RC_CONV_RE, _RC_FOOD_RE |
| Validation | No future release date; no >1yr past; duty_days >90 = SUSPICIOUS |
| Duty Day Calculation | release_date - boarding_date (not calendar days; exact formula in `_calc_duty_days()`) |
| Output | EscortSlipResult (6 of 18 fields documented) |
| Management Decision | None specifically for release slip workflow |
| Missing | 12 additional EscortSlipResult fields; label blacklist; signature detection; full management ratification |
| Production Verified | Yes (Wave-1, partial) |

**Gap to Level 3:** No management decision specifically for release slip processing rules.
**Path to Level 3:** Management ratification of validation rules → Level 3; then PKVC → Level 4.

---

## Workflow Maturity Summary

| Workflow | Level | Missing for Next Level |
|---|---|---|
| Attendance | 3 | PKVC post-Wave-1 |
| Escort | 3 | Parser regex + PKVC |
| Payment | 3 | Verification edge cases + PKVC |
| Payroll | 3 | Resolve DUP-03 + PKVC |
| Recruitment | 3 | Resolve DUP-06 + PKVC |
| Release Slip | 2 | Management decision + full TypedDict + PKVC |

**Workflow Domain Average: 2.83 / 3.0 for Level 3 domains (approaching Level 4)**

---

## Workflow Coverage After Wave-1

| Workflow | Pre-Wave-1 | Post-Wave-1 | State Machine | Commands | Edge Cases |
|---|---|---|---|---|---|
| Attendance | 40% | ~75% | Yes | Yes | Partial |
| Escort | 21% | ~65% | Yes | Yes | Partial |
| Payment | 25% | ~65% | Yes | Yes | Partial |
| Payroll | ~30% | ~70% | Yes | Yes | Partial |
| Recruitment | 44% | ~85% | Yes | Yes | Partial |
| Release Slip | ~15% | ~55% | N/A (validation rules) | N/A | Partial |
| **Average** | **29%** | **69%** | — | — | — |

---

## Workflow Domain Verdict

**Domain Maturity: Level 3 (Management Approved)**

5 of 6 workflows are at Level 3. Release Slip at Level 2 is the only gap. The overall Workflow domain is Level 3 because the 5 major operational workflows all have management approval for their key decisions.

**Minimum for Level 4:** Run PKVC post-Wave-1; resolve DUP-03 and DUP-06; document release slip management authority.

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
