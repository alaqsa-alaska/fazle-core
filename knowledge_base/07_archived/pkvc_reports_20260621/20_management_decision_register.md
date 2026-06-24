---
title: PKVC Report 20: Management Decision Register
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Report 20: Management Decision Register

- Date: 2026-06-21
- Scope: Decision status to close certification blockers

## Resolved Decisions

### MDR-01: Escort Payment Formula Authority
- Status: Resolved
- Approved: 12000 / 30 * duty_days
- Priority: Critical

### MDR-02: Payroll/Payment Formula Unification
- Status: Resolved
- Approved: Same formula in Payroll and Payment modules
- Priority: Critical

### MDR-03: Transport Allowance Authority
- Status: Resolved
- Approved: Mongla 800, full rate table confirmed
- Priority: Critical

### MDR-04: Food Cost Settlement Policy
- Status: Resolved
- Approved: 150/day with approved time exceptions; recorded as escort advance for payroll deduction
- Priority: High

### MDR-05: Hidden Rule Governance
- Status: Partially Resolved
- Approved: several hidden-rule decisions finalized (HK-01, HK-03, HK-04, HK-09, HK-13, HK-19, HK-24, HK-33, HK-34, HK-44)
- Remaining: KB traceability and implementation synchronization
- Priority: High

## Pending Decisions

### MDR-P01: DUP-03 Phone Normalization Duplication
- Status: Pending developer evidence
- Required: behavioral diff and IO contract comparison between phone_normalizer and number_identity
- Priority: High

### MDR-P02: DUP-04 Recruitment Keyword Duplication Governance
- Status: Pending final option selection after code evidence
- Required: final architecture choice (keep/document vs consolidate)
- Priority: High

### MDR-P03: DUP-06 Draft Table Duplication Governance
- Status: Pending schema and flow evidence sign-off
- Required: explicit split-of-responsibility approval for fazle_draft_replies vs fazle_payment_drafts
- Priority: High

### MDR-P04: Certification Closure Policy
- Status: Pending
- Required: formal sign-off workflow, accountable owners, and evidence package definition
- Priority: High

## Register Conclusion
Core financial/transport conflicts are now resolved by management. PKVC still requires completion of pending duplicate-governance decisions and full documentation alignment before CERTIFIED status.
