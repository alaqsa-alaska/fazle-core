---
title: Payment Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Payment Workflow

## Flow
Payment Request -> Identity Verification -> Duty/Slip Verification -> Method/Number Confirmation -> Draft/Admin Message -> Accountant WhatsApp -> Payment Complete -> Ledger Update.

## Completion Rule
Payment is considered complete when admin's payment message reaches accountant WhatsApp.

## Critical Distinction

Employee/Escort request and Admin instruction are different workflows:

| Item | Employee/Escort Request | Admin -> Accountant Instruction |
|---|---|---|
| Intent | Wants advance/payment | Confirms payment instruction |
| Verification | Required | Not required after admin handoff |
| Intermediate state | `fazle_payment_drafts.pending` | None |
| Final ledger write | No | Yes, direct `wbom_cash_transactions` |
| AI role | Conversation + draft support | No decision; code parser executes |
| Employee create | No | Yes, if lookup mobile is missing |

AI must never create a final transaction from an employee request. Only an Admin -> Accountant instruction may create the final cash transaction.

## Required Data
Employee ID or name, phone/payment number, method, amount, purpose, admin sender, accountant recipient, timestamp.

## Cross References
- ../04_business_rules/payment_business_rules.md
- cash_workflow.md

---

## Payment Draft State Machine

### Purpose
Employee-origin payment and advance requests create a draft in `fazle_payment_drafts`. The draft follows a defined lifecycle. Admin -> Accountant payment instructions bypass drafts and create final transactions directly.

### States

```
pending → sent (admin PAID or ADVANCE command → finalize_payment() → accountant notified)
        → rejected (admin REJECT command)
        → expired (24-hour TTL; combined_draft_cleanup job runs hourly)
```

| State | Trigger | Next Action |
|---|---|---|
| `pending` | Created by payment_workflow | Admin reviews and approves or rejects |
| `sent` | Admin runs PAID/ADVANCE | Accountant notified via bridge1; ledger updated |
| `rejected` | Admin runs REJECT | Requester optionally notified |
| `expired` | 24h TTL passed | No further action; admin must re-request if needed |

**Business Rule:** A payment draft never auto-sends. It always requires explicit admin action (PAID or ADVANCE command).

**Source Module:** `modules/payment_workflow`, `modules/admin_commands`, `modules/scheduler`
**PKCA Report:** 09_state_machine_coverage_report.md (SM-03)
**Management Authority:** Production evidence; documented 2026-06-22

---

## Employee Payment Verification (5-Step)

### Purpose
New employees requesting payment method setup go through a 5-step identity verification session before payment is processed.

### Verification Steps

| Step | Session State | Employee Action | System Action |
|---|---|---|---|
| 1 | `STEP_SELFIE` (`pending_selfie`) | Employee sends selfie image | System stores image; advances session |
| 2 | `STEP_SLIP` (`pending_slip`) | Employee sends duty slip or release slip | System validates slip presence; advances session |
| 3 | `STEP_METHOD` (`pending_payment_method`) | Employee confirms bKash/Nagad/cash + number | System stores payment method |
| 4 | `STEP_DONE` (`verified`) | Session complete | Admin can now process payment |
| — | `rejected` | Identity mismatch detected | Session closed; admin notified |

**Business Rule:** Verification session is stored in `fazle_draft_replies` with `intent='verification'`. Identity mismatch (selfie vs employee records) triggers rejection and admin notification.

**Source Module:** `modules/employee_verification`
**Source Function:** `run_verification_step()`, `STEP_SELFIE`, `STEP_SLIP`, `STEP_METHOD`, `STEP_DONE`
**PKCA Report:** 09_state_machine_coverage_report.md (SM-06)

---

## PAID and ADVANCE Commands

| Command | Syntax | Required Role |
|---|---|---|
| PAID | `PAID <draft_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant |
| ADVANCE | `ADVANCE <draft_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant |
| REJECT | `REJECT <draft_id>` | operator |

**Example:**
```
PAID 165 12000 bkash ref=TX12345
ADVANCE 166 1000 nagad
```

**Source Module:** `modules/admin_commands`
**Source Function:** `_cmd_paid()`, `_cmd_advance()`
**PKCA Report:** 12_command_coverage_report.md

---

## Payment Reconciliation

**Business Rule:** Every hour, the `payment_reconciliation` scheduler job re-attempts to match unmatched staging payments in `wbom_staging_payments` against employee records.

- Matching method: mobile phone tail (last 11 digits)
- Tries both `extracted_mobile` and `sender_number`
- Result logged to `fazle_reconciliation_log`
- Max 50 unmatched records per run

**Source Module:** `modules/scheduler`
**Source Function:** `job_payment_reconciliation()`
**PKCA Report:** 07_scheduler_coverage_report.md
