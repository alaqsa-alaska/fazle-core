---
title: PKMA Report 13 — Parser Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 13 — Parser Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the maturity of every parser in the Fazle AI Platform. A parser is mature when its trigger conditions, supported input formats, extracted fields, validation rules, and failure paths are documented, production-verified, and management-approved.

---

## Parser Inventory (15 parsers identified by PKCA)

---

## Parser 01 — Escort Order Parser

**Source:** `modules/escort.parse_escort_order()`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/escort_workflow.md` (parser formats section — Wave-1) |
| Supported Formats | 4 documented: labeled block, inline compact, MV-block, numbered |
| Extracted Fields | mother_vessel, lighter_vessel, master_mobile, destination, product, capacity, start_date, shift |
| Regex Constants | `_MV_LABEL_RE`, `_IMPORTER_RE`, `_CARGO_RE`, `_CAPACITY_RE`, `_DATE_RE` — NOT in KB |
| Failure Path | Missing required fields → manual review draft |
| Production Verified | Yes — formats documented in Wave-1 |
| Management Decision | None formal for parser formats |

**Gap to Level 3:** No management decision ratifying the 4 supported formats as the authoritative set.
**Risk:** Medium — unrecognized formats silently fall to manual review; admins need to know why.

---

## Parser 02 — Lighter Block Parser

**Source:** `modules/escort._parse_lighter_block()`
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| KB Article | None |
| Extracts | Lighter:, Master:, Mob:, Capacity:, Dest: (named labels) |
| Production Verified | No |

**Risk:** Low — internal sub-parser; affects escort order parsing accuracy.

---

## Parser 03 — Labeled Lighter Parser

**Source:** `modules/escort._parse_labeled_lighters()`
**Maturity: Level 0 (Unknown)**

**Risk:** Low — internal sub-parser.

---

## Parser 04 — Inline Lighter Parser

**Source:** `modules/escort._parse_inline_lighters()`
**Maturity: Level 0 (Unknown)**

**Risk:** Low — internal sub-parser.

---

## Parser 05 — MV Block Lighter Parser

**Source:** `modules/escort._parse_mv_block_lighters()`
**Maturity: Level 0 (Unknown)**

**Risk:** Low — internal sub-parser.

---

## Parser 06 — Completed Draft Detector

**Source:** `modules/escort.is_completed_escort_draft()`
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/escort_workflow.md` (ESCORTCONFIRM section mentions admin fills escort name/mobile) |
| Detection Patterns | `_CD_ESCORT_NAME_RE`, `_CD_ESCORT_MOB_RE`, `_CD_SHIFT_RE`, `_CD_MV_RE`, `_CD_LV_RE` — NOT in KB |
| Production Verified | No |

**Risk:** Medium — admin workflow depends on this; wrong detection causes missed confirmations.

---

## Parser 07 — Attendance Parser

**Source:** `modules/attendance_parser`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/attendance_workflow.md` (attendance parser section — Wave-1) |
| Date Formats | DD-MM-YYYY, YYYY-MM-DD — documented |
| Shift Detection | D/Day or N/Night — documented |
| Mobile Pattern | BD phone — documented |
| Name Heuristic | Named label OR bare-name fallback — documented |
| Regex Constants | `_DATE_PATTERNS`, `_SHIFT_RE`, `_MOBILE_RE`, `_NAME_LABEL_RE` — names referenced in PKCA; not in KB |
| Production Verified | Yes (Wave-1) |
| Management Decision | None formal |

**Risk:** Low — most common parser; well-documented behavior.

---

## Parser 08 — Release Confirmation Parser

**Source:** `modules/escort_lifecycle.parse_release_confirmation()`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/release_slip_workflow.md` (6 field table — Wave-1) |
| Extracted Fields | 6 documented: date, shift, point, days, conveyance, food |
| Regex Constants | `_RC_DATE_RE`, `_RC_SHIFT_RE`, `_RC_POINT_RE`, `_RC_DAYS_RE`, `_RC_CONV_RE`, `_RC_FOOD_RE` — referenced only |
| Trigger | `[RELEASE CONFIRMED]` in admin message |
| Production Verified | Yes (Wave-1) |
| Management Decision | None formal for release confirmation parsing |

**Risk:** Medium — financial parser directly used to calculate escort payment amounts.

---

## Parser 09 — Payment SMS Parser

**Source:** `modules/payment_ingest.looks_like_payment_sms()`
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/cash_workflow.md` — mentions "Transaction Parse" only |
| Coverage | 10% (PKCA baseline) — not enriched in Wave-1 |
| Extracted Fields | bKash/Nagad SMS format — NOT documented in KB |
| Production Verified | No |

**Risk:** High — this parser gates whether a payment SMS becomes a transaction record. Misparse = lost payment.

---

## Parser 10 — Admin Cash Shorthand Parser

**Source:** `modules/payment_ingest.is_admin_cash_shorthand()`
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| KB Article | None |
| Coverage | 0% |
| Production Verified | No |

**Risk:** High — admin shorthand for cash entries is undocumented; any admin error format goes undetected.

---

## Parser 11 — RAG Tokenizer (Bilingual)

**Source:** `modules/rag._TOKEN_RE`
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| KB Article | `06_developer_system/rag_strategy.md` — mentions "bilingual" |
| Tokenizer Pattern | `[A-Za-z0-9ঀ-৿]+` — NOT in KB |
| Stop Words | 80+ Bangla + English — mentioned but not listed |
| Coverage | 5% (PKCA baseline) |
| Production Verified | No (Wave-1 did not enrich rag_strategy.md — P2 item) |

**Risk:** Medium — RAG quality depends on tokenizer behavior; tokenizer not documented.

---

## Parser 12 — Intent Classifier (Deterministic)

**Source:** `modules/intent`
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| KB Article | `06_developer_system/conversation_parser.md` — mentions classification |
| LLM vs Deterministic | Deterministic-first, LLM fallback if unknown — documented in ai_response_rules.md (partial) |
| Keyword List | NOT in KB |
| Coverage | 10% (PKCA baseline) — not enriched Wave-1 |
| Production Verified | No |

**Risk:** Medium — intent drives the entire routing decision tree.

---

## Parser 13 — Escort Slip Extractor

**Source:** `modules/escort_slip_extractor` (947 lines)
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| KB Article | `05_workflows/release_slip_workflow.md` (OCR section — Wave-1) |
| Document Types | 4 documented: printed_template_slip, handwritten_blank_slip, mixed_form, unknown_document |
| Required Fields | 6 documented: mother_vessel, lighter_vessel, escort_name, escort_mobile, start_date, completion_date |
| Total Output Fields | 18 total — 12 not in KB |
| Label Blacklist | 35+ strings — NOT in KB |
| Signature Detection | 3 signature types — NOT in KB |
| Image Requirements | Size (1KB–8MB), formats (JPG/JPEG/PNG/WEBP) — documented |
| Production Verified | Yes (Wave-1, partial) |
| Management Decision | Management declared OCR as strategic (PKMA program directive) |

**Gap to Level 3:** 12 of 18 EscortSlipResult fields undocumented; no formal management decision for specific OCR field requirements.
**Risk:** High — 947-line critical module; 12 output fields unknown to KB.

---

## Parser 14 — Accountant Summary Detector

**Source:** `modules/accountant_summary`
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| KB Article | None |
| Supported Labels | 13 Bangla accounting labels (জমা, টোটাল বাকি, etc.) — NOT in KB |
| Key Behavior | Does NOT write to wbom_cash_transactions (requires employee_id NOT NULL) |
| Production Verified | No |

**Risk:** High — admin accountant messages are silently parsed (or not); failure invisible.

---

## Parser 15 — FPE Message Parser

**Source:** `modules/fazle_payroll_engine/parser.py`
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| KB Article | None |
| MessageType | payment, balance_summary, cash_command, income_command, escort_payment, other |
| Output | ParsedPayment model |
| Production Verified | No |

**Risk:** Critical — FPE financial message parsing has zero documentation; errors are invisible.

---

## Parser Maturity Summary

| Parser | Level | Risk |
|---|---|---|
| P-01: Escort Order | 2 | Medium |
| P-02: Lighter Block | 0 | Low |
| P-03: Labeled Lighter | 0 | Low |
| P-04: Inline Lighter | 0 | Low |
| P-05: MV Block Lighter | 0 | Low |
| P-06: Completed Draft Detector | 1 | Medium |
| P-07: Attendance | 2 | Low |
| P-08: Release Confirmation | 2 | Medium |
| P-09: Payment SMS | 1 | High |
| P-10: Admin Cash Shorthand | 0 | High |
| P-11: RAG Tokenizer | 1 | Medium |
| P-12: Intent Classifier | 1 | Medium |
| P-13: Escort Slip Extractor | 2 | High |
| P-14: Accountant Summary | 0 | High |
| P-15: FPE Message Parser | 0 | Critical |

**Parser Domain Average: 1.0 / 5.0 (Level 1)**
**Level 0 count: 7 of 15**
**Critical risk parsers: 1 (FPE)**
**High risk parsers: 3 (Payment SMS, Admin Cash, Escort Slip)**

**Parser Domain Verdict: Level 1 — DOCUMENTED (partial)**
The attendance and release slip parsers have Wave-1 documentation. Core sub-parsers for escort (parsers 2–5), financial parsers (9, 10, 14), and FPE parser (15) remain at Level 0.

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
