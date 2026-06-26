---
title: Payment Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Payment Business Rules

## Source Of Truth
- Admin manually sends all payment messages to accountant WhatsApp.
- If admin message reaches accountant, payment is considered complete.
- Ledger/database updates immediately after accountant handoff.
- Future system-mediated approval may exist, but accountant handoff remains completion trigger.
- Employee/Escort payment or advance requests are never final transactions. They create drafts only.
- Accountant company summaries are informational and do not create employee cash transactions.
- The final employee-level ledger is keyed by employee lookup mobile (`employee_mobile`) and written to existing cash transaction tables only; no DB schema change is required.

## Admin -> Accountant Final Instruction

Required components:
- employee/payable name
- payout mobile number with method marker
- amount

Optional but preferred:
- `ID:` employee-id mobile before the name

Examples:

```text
ID: 01795122311 Manik Mea 01789123456(B) 5000/-
Manik Mea 01789123456(N) 200/-
```

Rules:
- `ID:` mobile is the employee lookup key.
- Without `ID:`, payout mobile becomes the employee lookup key.
- `(B)` = bKash, `(N)` = Nagad, `(C)` = Cash, `(Bank)` = Bank Transfer.
- If employee is not found, create a minimal active employee using existing `wbom_employees` columns.
- Write exactly one final transaction row for a distinct WhatsApp instruction.
- Same employee/same amount can appear multiple times in one day if the WhatsApp message time/identity is different.

## Verification Rules
- Confirm identity.
- Confirm bKash/Nagad/cash method and number every time.
- Require duty selfie, duty slip, or release slip if needed.
- Apply duplicate prevention.

## Advance Rule
৳500-৳1,000 is a normal guideline, not a hard cap. Admin/management may approve exceptions.

## Cross References
- ../02_admin_knowledge/admin_payment_handling.md
- ../05_workflows/payment_workflow.md

---

## Advance Trigger Keywords

### Purpose
When an inbound message contains any of the following 18 phrases, the system detects it as an advance request and forces the response to draft (never auto-sends).

### Trigger Phrases (18 total)

**Standard advance requests:**
- অ্যাডভান্স চাই
- অ্যাডভান্স দরকার
- অ্যাডভান্স লাগবে
- অগ্রিম চাই
- advance চাই
- অগ্রিম টাকা দরকার

**Emergency / urgency variants:**
- জরুরি টাকা
- জরুরি অর্থ
- ইমার্জেন্সি টাকা
- emergency money

**Medical / personal crisis:**
- অসুস্থ
- ডাক্তার
- হাসপাতাল
- চিকিৎসা
- চিকিৎসার জন্য

**Family emergency:**
- পারিবারিক সমস্যা
- পরিবারে সমস্যা
- বাড়িতে সমস্যা

**Business Rule:** All 18 phrases force the response to admin draft regardless of the auto-send intent gate. Advance requests always require admin approval.

**Source Module:** `modules/payment_workflow`
**Source Function:** `ADVANCE_KEYWORDS`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-43)
**Management Authority:** Production evidence; documented 2026-06-22

---

## Advance Request Force-Draft Phrases (Short Form)

In addition to the 18 advance keywords, the bridge_poller separately checks these 5 short-form Bangla phrases in any message:

- অ্যাডভান্স চাই
- অ্যাডভান্স দরকার
- অ্যাডভান্স লাগবে
- অগ্রিম চাই
- advance চাই

**Business Rule:** These 5 phrases trigger the `_ADVANCE_REQUEST_PHRASES` force-draft check at the bridge_poller level, before any intent classification. They act as an early gate.

**Source Module:** `app/bridge_poller`
**Source Function:** `_ADVANCE_REQUEST_PHRASES`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-11)

---

## Payment Draft Lifecycle

### Purpose
Document the lifecycle of a payment/advance draft and when it expires.

### Draft Expiry Rules

| Draft Type | Expiry | Cleanup Job |
|---|---|---|
| Payment draft (`fazle_payment_drafts`) | 24 hours after creation | `combined_draft_cleanup` (runs hourly) |
| General draft (`fazle_draft_replies`) | Configurable via `draft_ttl_hours` | `draft_ttl_cleanup` (runs every 30 min) |

**Business Rule:** A payment draft that has not been approved or rejected within 24 hours is automatically expired by the scheduler. Admin must re-request the payment if the draft expires.

**Validation:** Expired drafts are marked with `status='expired'`, not deleted. The record remains for audit purposes.

**Source Module:** `modules/scheduler`, `modules/outbound`
**Source Function:** `job_combined_draft_cleanup()`, `expire_stale_drafts()`
**PKCA Report:** 07_scheduler_coverage_report.md, 09_state_machine_coverage_report.md

---

## Payment Draft States

| State | Meaning |
|---|---|
| `pending` | Draft created, awaiting admin action |
| `sent` | Admin ran PAID/ADVANCE command; payment finalized; accountant notified |
| `rejected` | Admin ran REJECT command |
| `expired` | 24-hour TTL passed without admin action |

**Source Module:** `modules/payment_workflow`
**PKCA Report:** 09_state_machine_coverage_report.md (SM-03)

---

## Management-Approved Rate Schedule

**Authority:** `management_decisions.md` — these rates are policy. Where code differs, the gap is documented below; do not resolve silently.
**Management Authority:** All rates approved 2026-06-22.

### PAY-01 — Escort Duty Daily Rate

**Decision:** Escort payment formula = `12,000 ÷ 30 × duty_days` = **৳400/day**

| Constant | Module | Value |
|---|---|---|
| `DEFAULT_DAILY_RATE` | `modules/payment_workflow/__init__.py` | `400` |
| `DEFAULT_PER_PROGRAM_RATE` | `modules/payroll/__init__.py` | `400.0` |

Both constants were updated as part of CR-05 conflict resolution (2026-06-23). The formula applies to all escort duty payment calculations.

**Gross formula:** `duty_days × (basic_salary / 30)`, fallback to `duty_days × DEFAULT_DAILY_RATE` if employee has no basic_salary set.

---

### PAY-02 — Formula Unification

**Decision:** Payroll and escort payment must use the same daily rate formula. No separate calculation between escort path and payroll batch path.

**Implementation status:** Achieved via CR-05 — both `payment_workflow.DEFAULT_DAILY_RATE` and `payroll.DEFAULT_PER_PROGRAM_RATE` set to 400. Values are identical.

---

### PAY-03 — Mongla Transport Rate

**Decision:** Mongla transport allowance = **৳800 per assignment** (fixed, not variable)

**Production code status — gap exists:** `modules/escort_lifecycle/__init__.py` `_TRANSPORT_RATES` table groups Mongla with Faridpur at **৳700**. The code comment says "management-approved rates" but was updated 2026-05-29, before this PAY-03 decision (2026-06-22).

| Location | Management Decision | Code Value | Status |
|---|---|---|---|
| Mongla | ৳800 | ৳700 (grouped with Faridpur) | **GAP — code must be updated** |

**Required code change (NOT yet authorized under GOV-03):** Mongla must be split out of the Faridpur/Mongla group and set to ৳800 per assignment. Separate production authorization required.

Additional note: the `escort_calculation_config` DB table may hold different rates again (code comment: "e.g. Mongla ৳1500 vs code ৳1000"). That table is not currently used for draft calculation — code uses `_TRANSPORT_RATES` hardcoded table only.

---

### PAY-04 — Food Cost Rate

**Decision:** Food cost = **৳150/day**

**Production code:** `modules/escort_lifecycle/__init__.py` — `food_est = (duty_days * 150)` — **confirmed matching** the management decision.

The ৳150/day food estimate appears in payment draft generation as a pre-confirmation estimate. The actual food bill is parsed from the `food_bill` field in the `[RELEASE CONFIRMED]` admin message and stored in `wbom_escort_programs.food_bill`.

---

### CR-05 — Daily Rate Conflict Resolution

**Conflict (Three-Way):** Three values existed for the escort daily rate:
1. ৳800/day — hardcoded in an older code path
2. ৳1,200/day — hardcoded in another code path
3. ৳400/day (`12,000 ÷ 30`) — PAY-01 management formula

**Resolution (2026-06-23):** PAY-01 formula confirmed. Both constants unified to 400:
- `payroll.DEFAULT_PER_PROGRAM_RATE = 400.0`
- `payment_workflow.DEFAULT_DAILY_RATE = 400`

**Source files updated:** `modules/payroll/__init__.py`, `modules/payment_workflow/__init__.py`

---

### Full Rate Reference

| Rate | Management Decision | Code Status |
|---|---|---|
| Escort daily rate | ৳400/day (12,000 ÷ 30) | ✅ Matches — both constants = 400 (CR-05) |
| Formula unification | Single rate for escort + payroll | ✅ Achieved — same value 400 in both modules |
| Mongla transport | ৳800 per assignment | ⚠️ Gap — code has ৳700 (grouped with Faridpur) |
| Food cost | ৳150/day | ✅ Matches — `duty_days × 150` in escort_lifecycle |

**All transport rates in production code** (`_TRANSPORT_RATES`, `modules/escort_lifecycle/__init__.py`, last updated 2026-05-29):

| Destination Group | Code Rate | Management Override |
|---|---|---|
| Dhaka / Narayanganj area (13 keywords) | ৳600 | None |
| Faridpur / Mongla | ৳700 | PAY-03: Mongla → ৳800 (gap) |
| Barishal / coastal / river routes | ৳900 | None |
| Noapara / Jessore / Khulna | ৳1,000 | None |
| Default (unmatched location) | ৳600 | None |

**Wave:** Wave-4, W4-AUTH (2026-06-23)
