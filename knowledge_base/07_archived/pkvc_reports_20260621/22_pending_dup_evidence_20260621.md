---
title: Pending DUP Evidence Pack (Developer Evidence)
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# Pending DUP Evidence Pack (Developer Evidence)

- Date: 2026-06-21
- Scope: Evidence for DUP-03, DUP-04, DUP-06 pending management decisions
- Method: Read-only source and migration inspection

## DUP-03: Phone Normalization Duplication

### Module A: modules/phone_normalizer
- Primary function: strict Bangladesh canonicalization
- Core output: single canonical value in format 8801XXXXXXXXX or None
- Validation behavior: operator prefix validation (11-19)
- Utility outputs: display format and WhatsApp +880 format generation

### Module B: modules/number_identity
- Primary function: identity and critical-number helper utilities
- normalize_phone output: list of normalized variants [01..., 880..., +880...]
- canonical_phone behavior: delegates to app.critical_numbers.normalize_phone_880 and preserves unresolved: values
- Additional responsibilities: critical-phone checks, message hash generation, critical log appending

### IO Comparison
- Input overlap: both accept raw phone-like input
- Output difference:
  - phone_normalizer.normalize_phone -> one canonical string or None
  - number_identity.normalize_phone -> list of variants for DB matching
- Conclusion: not functionally identical; shared validation dependency exists by design.

## DUP-04: Recruitment Keyword Duplication

### Evidence
- message_router imports recruitment_eligibility from recruitment_flow
- message_router calls recruitment_eligibility(...) for candidate/new_lead/unknown routing decision
- message_router does not maintain a standalone INTAKE_KEYWORDS list
- recruitment_flow is where INTAKE_KEYWORDS and recruitment trigger logic are defined

### Technical Conclusion
- Router delegates recruitment eligibility to recruitment module.
- Based on current code evidence, this is closest to option G (no true duplicate in router keyword list).
- Recommended governance note: keep recruitment keywords authoritative in recruitment_flow only.

## DUP-06: Draft Table Duplication

### Table A: fazle_draft_replies (general/admin-review drafts)
- Created in migration 001; extended in 005
- Typical fields: source, recipient, reply_text, intent, status, meta, approved_at, admin_phone
- Used by create_draft_reply shared helper
- Used for: attendance drafts, OCR release review draft, complaint drafts, NL outbound drafts, verification-related drafts
- Approved by admin via APPROVE command path (_cmd_approve)

### Table B: fazle_payment_drafts (payment transaction drafts)
- Created in migration 002; extended in 003c/005/018
- Typical fields: draft_type, employee info, program linkage, expected/approved amount, method, status, accountant_msg
- Used for: escort payment drafts, advance drafts, payment ingest/correction/payroll related payment draft entries
- Approved/finalized by PAID/ADVANCE command path (_cmd_paid + finalize_payment)

### Which Draft Goes Where
- Escort order/admin messaging draft -> fazle_draft_replies
- Attendance draft -> fazle_draft_replies
- OCR release review draft -> fazle_draft_replies
- Escort settlement/payment draft -> fazle_payment_drafts
- Advance payment request draft -> fazle_payment_drafts
- Accountant/payment ingest-related draft -> fazle_payment_drafts

### When Both Tables Are Touched
- Multi-stage release flow can touch both tables across steps:
  1) OCR release slip creates admin review draft in fazle_draft_replies
  2) After confirmed release handling, payment draft is created in fazle_payment_drafts
- Same business case, different lifecycle stage and table responsibility.

## Proposed Decision Input to Management
- DUP-03: Mark as intentional layered design (canonical core + identity variants/logging utilities).
- DUP-04: Confirm recruitment_flow as single source for keywords and keep router as delegator.
- DUP-06: Confirm split-by-purpose architecture:
  - fazle_draft_replies for conversational/admin review drafts
  - fazle_payment_drafts for monetary approval/ledger workflow drafts
