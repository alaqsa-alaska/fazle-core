---
title: Daily Cash Transaction Source Of Truth
owner: Fazle Core Admin
status: active
last_verified: 2026-06-26
runtime_index: true
source_modules: modules/payment_ingest, modules/payment_workflow, modules/accountant_summary
---

# Daily Cash Transaction Source Of Truth

This is the canonical rule set for Admin and Accountant conversations, daily cash payments, employee advance/loan requests, escort release payment flow, security guard/ghat tallyman daily payments, and employee mobile based traceability.

## Core Entities

- Employee/Escort/Security Guard/Ghat Tallyman: asks for advance or payment.
- Admin: approves payment and sends the instruction to the accountant.
- Accountant: receives final payment instruction or sends company-level summaries.

## Tables And Ownership

- `fazle_payment_drafts`: temporary admin-review drafts for employee-origin requests. Pending drafts expire after the configured TTL.
- `wbom_cash_transactions`: final employee-level transaction ledger in the core app.
- FPE tables may mirror or process financial data, but the operational rule is the same: final ledger rows require final admin/accountant authority.

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
- Do not write `wbom_cash_transactions`.
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
- If employee does not exist, create a minimal active employee using `employee_mobile` as the lookup key.
- Insert a final row in `wbom_cash_transactions`.
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
- Do not write `wbom_cash_transactions`.
- To record an individual employee advance/payment, use an employee-level final instruction.

## Non-Negotiable Rules

- Employee request is not a final transaction.
- AI must not create a final transaction from employee request text.
- Admin -> Accountant instruction is the final transaction trigger.
- Final ledger must be searchable by employee name, employee mobile, payout mobile, and employee id.
- Transaction history is traceable through `employee_id`, `employee_mobile`, `payment_mobile`, `payment_number`, `source`, `remarks`, `whatsapp_message_id`, and timestamps where present.
- Existing DB columns must be used; do not change the DB schema for this workflow.
