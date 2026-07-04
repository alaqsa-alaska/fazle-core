---
title: Daily Cash Transaction Source Of Truth
owner: Fazle Core Admin
status: active
last_verified: 2026-07-01
runtime_index: true
source_modules: modules/payment_ingest, modules/payment_workflow, modules/accountant_summary, modules/fazle_payroll_engine
---

# Daily Cash Transaction Source Of Truth

**Owner Directive (2026-06-29):** `fpe_cash_transactions` is the ONLY canonical
cash transaction table. `wbom_cash_transactions` is a legacy archive — no new
rows are written to it. All cash/payment transactions (WhatsApp, manual,
operator, employee draft, escort, correction/reversal, frontend) MUST end up in
`fpe_cash_transactions` via `create_transaction()`.

This is the canonical rule set for Admin and Accountant conversations, daily cash payments, employee advance/loan requests, escort release payment flow, security guard/ghat tallyman daily payments, and employee mobile based traceability.

## Core Entities

- Employee/Escort/Security Guard/Ghat Tallyman: asks for advance or payment.
- Admin: approves payment and sends the instruction to the accountant.
- Accountant: receives final payment instruction or sends company-level summaries.

## Tables And Ownership

- `fazle_payment_drafts`: temporary admin-review drafts for employee-origin requests. Pending drafts expire after the configured TTL.
- `fpe_cash_transactions`: **the ONLY canonical final employee-level transaction ledger.** All cash payments end up here via `create_transaction()`. This table has full approval lifecycle (`transaction_status`, `approval_status`), reversal support (`is_reversal`, `reversed_txn_id`), source tracking (`source`, `source_channel`, `source_message_id`), soft-delete (`deleted_at`, `deleted_by`), and ledger integration (`_upsert_ledger()`).
- `wbom_cash_transactions`: **legacy archive only.** No new rows are written. Historical data has been migrated to `fpe_cash_transactions` via `wbom_fpe_sync.py` with `legacy_wbom_transaction_id` preserving the original WBOM id for traceability.
- `fpe_employee_ledger`: running per-employee per-month totals, updated atomically by `_upsert_ledger()` whenever a `fpe_cash_transactions` row is created or reversed.

No DB schema change is required for this workflow. Existing columns must be used.

## Workflow A: Employee Request

Employee asks for advance/payment by text or voice+number.

Triggers include:
- অ্যাডভান্স চাই, অ্যাডভান্স দরকার, অগ্রিম চাই, advance চাই, টাকা পাঠান
- জরুরি টাকা, emergency money
- অসুস্থ, ডাক্তার, হাসপাতাল, চিকিৎসা
- পারিবারিক সমস্যা, পরিবারে সমস্যা, বাড়িতে সমস্যা
- voice message plus a mobile number

The system verifies:
- name and registered mobile
- vessel/duty location
- duty start date and D/N shift where relevant
- current vessel/ghat/release status
- supervisor/ghat information where relevant
- duty slip/release slip/selfie where relevant
- bKash/Nagad/Cash method and number

Result:
- Create `fazle_payment_drafts.status='pending'`.
- Notify admin.
- Do not write `fpe_cash_transactions`.
- Do not auto-send to accountant.

## Workflow B: Admin -> Accountant Final Instruction

Admin's payment instruction to accountant is final payment proof.

Required components:
- employee/payable name
- payout mobile number plus payment method marker
- amount

Preferred optional component:
- `ID:` employee-id mobile before the name

Formats:

```text
ID: 01795122311 Manik Mea 01789123456(B) 5000/-
Manik Mea 01789123456(N) 200/-
```

Rules:
- `ID:` mobile is the employee lookup key.
- Without `ID:`, payout mobile is the employee lookup key.
- `(B)` = bkash, `(N)` = nagad, `(C)` = cash, `(Bank)` = bank.
- If employee does not exist, create a minimal active employee in `fpe_employees` via `match_or_create_employee()`, using `employee_id_phone` or `primary_phone` as the lookup key.
- Insert a final row in `fpe_cash_transactions` via `create_transaction()`.
- Use WhatsApp message identity/timestamp for duplicate protection when available.
- Same employee, same method, same amount can appear multiple times in one day when the messages are distinct.

## Workflow C: Accountant Company Summary

Accountant summaries are informational and are not employee-level payments.

Examples:

```text
7/5/26=জমা =75,000/-
4/5/26=টোটাল বাকি =51,238/-
অগ্রিম জমা থাকে =23,762/-
```

Rules:
- Acknowledge the summary.
- Do not write `fpe_cash_transactions`.
- To record an individual employee advance/payment, use an employee-level final instruction.

## Non-Negotiable Rules

- Employee request is not a final transaction.
- AI must not create a final transaction from employee request text.
- Admin -> Accountant instruction is the final transaction trigger.
- `fpe_cash_transactions` is the ONLY canonical cash transaction table (Owner Directive 2026-06-29).
- `wbom_cash_transactions` is legacy archive only — never write new rows to it.
- Final ledger must be searchable by employee name, employee mobile, payout mobile, and employee id.
- Transaction history is traceable through `employee_id`, `employee_id_phone`, `employee_phone`, `payout_phone`, `txn_ref`, `source`, `source_message_id`, `source_message_text`, and timestamps where present.
- Existing DB columns must be used; do not change the DB schema for this workflow.
- All new transactions must go through `create_transaction()` in `accounting.py` to ensure ledger consistency.
