---
title: PKCA Report 04: Business Rule Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 04: Business Rule Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Business Rule Coverage by Source Module

### Domain 1: Payment & Financial Rules

| Rule ID | Business Rule | Source | KB Article | Coverage |
|---|---|---|---|---|
| BR-01 | Escort payment formula: (12000/30) × duty_days - food - conv - advances | `payment_workflow` CON-01 | `payment_business_rules.md` (no formula) | ⛔ 0% |
| BR-02 | Payroll formula same as payment: (12000/30) × duty_days | `payroll` CON-02 | `salary_business_rules.md` (no formula) | ⛔ 0% |
| BR-03 | Food cost: 150 BDT/day. Exception A: release before 10AM → exclude release day. Exception B: board after 3PM → exclude boarding day | `escort_lifecycle` CON-04 | `escort_business_rules.md` (no food rule) | ⛔ 0% |
| BR-04 | Food amount recorded as escort advance and deducted in payroll | `escort_lifecycle` CON-04 | None | ⛔ 0% |
| BR-05 | Advance payment advances deducted only if tied to specific program + current payroll month | `payment_workflow` | None | ⛔ 0% |
| BR-06 | Advance range ৳500–৳1,000 is guideline not hard cap | Management decision | `payment_business_rules.md` ✅ | ✅ 100% |
| BR-07 | Payment method (bKash/Nagad/cash) confirmed every time | `payment_workflow` | `payment_business_rules.md` ✅ | ✅ 100% |
| BR-08 | Payment completion trigger: admin message reaches accountant WhatsApp | Management decision | `payment_business_rules.md` ✅ | ✅ 100% |
| BR-09 | Payment idempotency key format: `payment-draft:{id}` | `payment_workflow.finalize_payment` | None | ⛔ 0% |
| BR-10 | Payroll is idempotent on UNIQUE(employee_id, period_year, period_month) WHERE status != 'cancelled' | `payroll.compute_run` | None | ⛔ 0% |
| BR-11 | Payroll only counts programs with status='Completed' | `payroll.compute_run` | None | ⛔ 0% |
| BR-12 | Payment correction REVERSE/ADJUST is dormant — 0 callers | `payment_correction` | None | ⛔ N/A |

**Domain Coverage: 3/11 rules = 27%**

---

### Domain 2: Escort Rules

| Rule ID | Business Rule | Source | KB Article | Coverage |
|---|---|---|---|---|
| BR-13 | Escort client NEVER receives direct reply — always admin draft | `message_router` | `escort_workflow.md` (implied) | 🔴 30% |
| BR-14 | Admin must fill [ESCORT NAME] + [ESCORT MOBILE] before client confirmation | `escort` | `escort_workflow.md` ✅ | 🟡 50% |
| BR-15 | Escort order creates 1 draft per lighter vessel | `escort` | None | ⛔ 0% |
| BR-16 | Transport rates: Dhaka/Narayanganj=600, Faridpur=700, Mongla=800, Barishal/coastal=900, Khulna/Jessore=1000, default=600 | `escort_lifecycle` CON-03 | `transport_allowance.md` (no rates) | ⛔ 0% |
| BR-17 | Release date must not be in future; not >1 year old | `escort_lifecycle._validate_release_date` | None | ⛔ 0% |
| BR-18 | Duty days >90 → SUSPICIOUS warning in draft | `escort_lifecycle.build_release_draft` | None | ⛔ 0% |
| BR-19 | OCR confidence <40% → low-confidence warning in draft | `escort_lifecycle.build_release_draft` | None | ⛔ 0% |
| BR-20 | Release slip estimates are DRAFT ONLY — admin must review | `escort_lifecycle` | `release_slip.md` (implied) | 🔴 20% |
| BR-21 | Attendance backfill: one row per day from program_date to end_date, ON CONFLICT DO NOTHING | `escort_lifecycle.backfill_attendance_for_program` | None | ⛔ 0% |
| BR-22 | Required release slip fields: Escort Name, Lighter Vessel, Release Date, Release Location | `escort_lifecycle` | `release_slip.md` (partial) | 🔴 20% |
| BR-23 | Escort training: 45 days, ৳10,000–৳15,000/month | Management decision | `escort_identity.md` ✅ | ✅ 100% |
| BR-24 | Escort duty day = 24h (day + night) | Management decision | `attendance_policy.md` ✅ | ✅ 100% |

**Domain Coverage: 2.5/12 rules = 21%**

---

### Domain 3: Recruitment Rules

| Rule ID | Business Rule | Source | KB Article | Coverage |
|---|---|---|---|---|
| BR-25 | Age range 18–55 (code) / 18–45 (KB) — DISCREPANCY | `recruitment_flow._parse_age` (18–55) | `recruitment_policy.md` (18–45) | ⚠️ CONFLICT |
| BR-26 | Operational roles BLOCKED from recruitment (admin, employee, supervisor, accountant, etc.) | `recruitment_flow.OPERATIONAL_ROLES` | None | ⛔ 0% |
| BR-27 | 9 valid positions: Escort, Survey Scout, Security Guard, Security Supervisor, Assistant Supervisor, Operation Officer, Security In-Charge, Marketing Officer, Ghat Supervisor | `recruitment_flow.VALID_POSITIONS` | `recruitment_policy.md` (6 categories, different names) | 🔴 30% |
| BR-28 | Scoring: experience≥6yr=60pts, ≥3yr=40pts, ≥1yr=20pts; target position=20pts; all fields complete=20pts | `recruitment_flow._compute_score` | None | ⛔ 0% |
| BR-29 | Session TTL: 24 hours | `recruitment_flow.SESSION_TTL` | None | ⛔ 0% |
| BR-30 | No joining fee mandatory (candidate-facing) | Management decision | `joining_business_rules.md` ✅ | ✅ 100% |
| BR-31 | Form fee ৳330 non-refundable; joining fee ৳3,500; monthly installment ৳500 | Management decision | `joining_business_rules.md` ✅ | ✅ 100% |
| BR-32 | 6-month completion → ৳3,500 returned over 7 months | Management decision | `joining_business_rules.md` ✅ | ✅ 100% |

**Domain Coverage: 3.5/8 rules = 44% (Note: 1 conflict found — BR-25)**

---

### Domain 4: Attendance Rules

| Rule ID | Business Rule | Source | KB Article | Coverage |
|---|---|---|---|---|
| BR-33 | Security guard: 12h = 1 duty day | Management decision | `attendance_policy.md` ✅ | ✅ 100% |
| BR-34 | Escort: 24h (day+night) = 1 duty day | Management decision | `attendance_policy.md` ✅ | ✅ 100% |
| BR-35 | 1 unauthorized absent day → up to 2 days salary deduction | `leave_policy` | `leave_policy.md` ✅ | ✅ 100% |
| BR-36 | 3 consecutive late arrivals → 1 day salary deduction | `leave_policy` | `leave_policy.md` ✅ | ✅ 100% |
| BR-37 | wbom_attendance UNIQUE(employee_id, date) → ON CONFLICT UPDATE | `attendance` | None | ⛔ 0% |
| BR-38 | Guard attendance: self-report or supervisor/operation officer batch message | `attendance` | `attendance_business_rules.md` ✅ | ✅ 100% |

**Domain Coverage: 5/6 rules = 83%**

---

### Domain 5: Identity and Routing Rules

| Rule ID | Business Rule | Source | KB Article | Coverage |
|---|---|---|---|---|
| BR-39 | Admin identity: bootstrapped from ADMIN_NUMBERS env on first sight | `rbac.ensure_bootstrap_admins` | None | ⛔ 0% |
| BR-40 | Admin messages never fall through to AI — always command/NL/help | `message_router` | `admin_identity.md` (implied) | 🔴 20% |
| BR-41 | Family members receive personal redirect — no business workflow | `message_router` | `family_identity.md` ✅ | ✅ 100% |
| BR-42 | Client escort buyer messages always drafted to admin | `bridge_poller._is_draft_always` | None | ⛔ 0% |
| BR-43 | accountant phone silent-skip — no reply, no draft | `message_router._should_silent_skip` | None | ⛔ 0% |
| BR-44 | Role='blocked' → silent skip entire routing | `message_router._should_silent_skip` | None | ⛔ 0% |
| BR-45 | Identity resolution order: admin(200)→family(100)→accountant(95)→vip(92)→escort_buyer(90)→employee(88)→supervisor(80)→vendor(70)→candidate(50)→unknown(0) | `identity_brain._ROLE_PRIORITY` | `identity_overview.md` (list only, no numbers) | 🔴 20% |
| BR-46 | office_location intent → KB fast path, bypasses AI entirely | `message_router` | None | ⛔ 0% |
| BR-47 | Safe auto-send intents: recruitment, join, greeting, office_location, salary_query, payment_due, attendance, leave, escort_duty (9 intents) | `message_router._SAFE_AUTOSEND_INTENTS` | None | ⛔ 0% |
| BR-48 | advance_request intentionally excluded from auto-send (except employee/security_guard roles) | `message_router` | None | ⛔ 0% |

**Domain Coverage: 1.5/10 rules = 15%**

---

### Domain 6: AI and System Rules

| Rule ID | Business Rule | Source | KB Article | Coverage |
|---|---|---|---|---|
| BR-49 | Reply chain: GitHub Models → Groq → Ollama | `app/llm.py` | None | ⛔ 0% |
| BR-50 | Intent chain (classification): Groq → GitHub Models → Ollama | `app/llm.py.classify_intent_llm` | None | ⛔ 0% |
| BR-51 | OLLAMA_REPLY_DISABLED=true: Ollama never used for customer replies; still used for intent/RAG/memory | `app/config.py` | None | ⛔ 0% |
| BR-52 | RAG chunk size: 320 chars, overlap: 60 chars | `modules/rag` | None | ⛔ 0% |
| BR-53 | RAG BM25 params: k1=1.5, b=0.75 | `modules/rag` | `rag_strategy.md` (mentions BM25+semantic, no params) | ⛔ 5% |
| BR-54 | Draft quality gate: rejects empty/llm-fallback/bad-pattern/too-long (>4000 chars) drafts | `modules/draft_quality` | None | ⛔ 0% |
| BR-55 | Outbound automated replies append Bengali suffix "🤖 Automated Reply System" | `app/bridge._AUTOMATED_SUFFIX` | None | ⛔ 0% |
| BR-56 | API keys stored as SHA-256 hash | `rbac.hash_api_key` | None | ⛔ 0% |
| BR-57 | Reviewed reply memory: eligible intents only; unsafe draft types (attendance, payment, gap_action) excluded | `modules/reviewed_reply_memory` | None | ⛔ 0% |
| BR-58 | Memory extractor: LLM extracts facts from conversations → user_profiles + user_memory tables | `modules/memory_extractor` | None | ⛔ 0% |

**Domain Coverage: 0/10 rules = 0%**

---

## Business Rule Coverage Summary

| Domain | Total Rules | Covered | Coverage % |
|---|---|---|---|
| Payment & Financial | 11 | 3 | 27% |
| Escort | 12 | 2.5 | 21% |
| Recruitment | 8 | 3.5 (+1 conflict) | 44% |
| Attendance | 6 | 5 | 83% |
| Identity & Routing | 10 | 1.5 | 15% |
| AI & System | 10 | 0 | 0% |
| **TOTAL** | **57** | **15.5** | **27%** |

### Critical Conflict Found

**BR-25 / Age Range Discrepancy:**
- Production code (`recruitment_flow._parse_age`): **18–55**
- KB article (`recruitment_policy.md`): **18–45**
- Management decision required to confirm authoritative value.
