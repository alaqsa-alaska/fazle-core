---
title: PKCA Report 05: Parser Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 05: Parser Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Parser Inventory from Production

The production system contains **12 parsers** across multiple modules, plus 3 additional extraction components in the newly discovered `escort_slip_extractor` module.

---

## Parser 1: Escort Order Parser — 20% Covered

**Module:** `modules/escort/__init__.py`
**Output:** `EscortOrder` TypedDict

**Key Patterns:**
- `_MV_LABEL_RE`: Mother vessel label extraction
- `_IMPORTER_RE`: Importer name extraction
- `_CARGO_RE`: Cargo type extraction
- `_CAPACITY_RE`: Vessel capacity extraction
- `_DATE_RE`: Date extraction

**Supported Formats:**
1. Labeled block format (`Mother Vessel: MV XYZ`)
2. Inline format (compact multi-field on one line)
3. MV-block format (multi-MV grouped sections)
4. Numbered format (lighter vessels numbered 1., 2., 3.)

**KB Coverage:** `05_workflows/escort_workflow.md` lists required fields but not the 4 parser formats or regex constants.

**Enrichment Target:** `05_workflows/escort_workflow.md` — add parser format examples.

---

## Parser 2: Lighter Block Parser — 0% Covered

**Module:** `modules/escort._parse_lighter_block`
**Output:** `LighterInfo` TypedDict

**Extracts:** Named labels — Lighter:, Master:, Mob:, Capacity:, Dest:

**KB Coverage:** Not mentioned anywhere.

**Enrichment Target:** `05_workflows/escort_workflow.md` or `06_developer_system/parser_engine.md`.

---

## Parser 3: Labeled Lighter Parser — 0% Covered

**Module:** `modules/escort._parse_labeled_lighters`
**Output:** `list[LighterInfo]`

**Extracts:** Splits text on `Lighter:` / `Lighter Vessel:` labels.

**KB Coverage:** Not mentioned anywhere.

---

## Parser 4: Inline Lighter Parser — 0% Covered

**Module:** `modules/escort._parse_inline_lighters`
**Output:** `list[LighterInfo]`

**Extracts:** Compact numbered format + mobile patterns.

**KB Coverage:** Not mentioned anywhere.

---

## Parser 5: MV Block Lighter Parser — 0% Covered

**Module:** `modules/escort._parse_mv_block_lighters`
**Output:** `list[LighterInfo]`

**Patterns:** `_MV_BLOCK_LINE_RE`, `_MASTER_LINE_RE`

**KB Coverage:** Not mentioned anywhere.

---

## Parser 6: Completed Draft Detector — 0% Covered

**Module:** `modules/escort.is_completed_escort_draft`
**Output:** `CompletedDraft` TypedDict

**Extracts:** [ESCORT NAME:], [ESCORT MOBILE:], [CD_SHIFT_RE], [CD_MV_RE], [CD_LV_RE]
**Trigger:** Admin fills in escort name/mobile in the draft response.

**KB Coverage:** `escort_workflow.md` mentions "Admin fills escort name/mobile" but not the detection patterns or CompletedDraft structure.

---

## Parser 7: Attendance Parser — 20% Covered

**Module:** `modules/attendance_parser`
**Output:** Attendance dict

**Key Patterns:**
- `_DATE_PATTERNS`: DD-MM-YYYY, YYYY-MM-DD
- `_SHIFT_RE`: D(ay) / N(ight) detection
- `_MOBILE_RE`: BD phone extraction
- `_NAME_LABEL_RE`: Named label extraction + bare-name heuristic

**KB Coverage:** `05_workflows/attendance_workflow.md` mentions date/shift/name but not the regex patterns or heuristic fallback.

**Enrichment Target:** `05_workflows/attendance_workflow.md` — add date format examples and shift detection.

---

## Parser 8: Release Confirmation Parser — 0% Covered

**Module:** `modules/escort_lifecycle.parse_release_confirmation`
**Output:** Release fields dict (triggered by [RELEASE CONFIRMED] outbound message)

**Key Patterns:**
- `_RC_DATE_RE`: Release date extraction
- `_RC_SHIFT_RE`: Shift extraction
- `_RC_POINT_RE`: Release point/location
- `_RC_DAYS_RE`: Duty days extraction
- `_RC_CONV_RE`: Conveyance amount
- `_RC_FOOD_RE`: Food amount

**KB Coverage:** Not mentioned anywhere.

**Enrichment Target:** `05_workflows/release_slip_workflow.md` — add release confirmation parsing details.

---

## Parser 9: Payment SMS Parser — 10% Covered

**Module:** `modules/payment_ingest.looks_like_payment_sms`
**Output:** Payment record

**Detects:** Accountant SMS payment notifications (bKash/Nagad confirmations).

**KB Coverage:** `05_workflows/cash_workflow.md` mentions "Transaction Parse" but not the SMS format or parser.

**Enrichment Target:** `05_workflows/cash_workflow.md` — add SMS format examples.

---

## Parser 10: Admin Cash Shorthand Parser — 0% Covered

**Module:** `modules/payment_ingest.is_admin_cash_shorthand`
**Output:** Cash entry

**Detects:** Admin shorthand for cash entries (format not documented).

**KB Coverage:** Not documented anywhere.

---

## Parser 11: RAG Tokenizer (Bilingual) — 0% Covered

**Module:** `modules/rag`
**Pattern:** `_TOKEN_RE`: `[A-Za-z0-9ঀ-৿]+` (Bangla + English)

**KB Coverage:** `rag_strategy.md` mentions "bilingual" but not the tokenizer regex or token range.

**Enrichment Target:** `06_developer_system/rag_strategy.md` — add tokenizer pattern.

---

## Parser 12: Intent Classifier (Deterministic) — 10% Covered

**Module:** `modules/intent`
**Output:** Intent string

**Behavior:** Keyword-based deterministic matching. LLM fallback when deterministic = 'unknown'. AI (Groq→GitHub→Ollama) is authoritative; deterministic is availability fallback.

**KB Coverage:** `06_developer_system/conversation_parser.md` mentions "Classify conversation intent" but not the deterministic-vs-LLM hierarchy.

---

## Parser 13: Escort Slip Extractor (NEW — NOT IN AUDIT) — 0% Covered

**Module:** `modules/escort_slip_extractor` (947 lines)
**Output:** `EscortSlipResult` TypedDict

**This is a completely new parser not covered by the previous audit.**

**Capabilities:**
- Detects document type: `printed_template_slip` | `handwritten_blank_slip` | `mixed_form` | `unknown_document`
- Template detection via 16 keywords (al-aqsa, escort slip, master mobile, etc.)
- Handwritten detection via Bangla/English field words
- Label blacklist (35+ strings that can never be field values)
- Signature detection (lighter master signed, ghat supervisor signed, company signed)
- Confidence scoring per extraction

**Output Fields:**
```
mother_vessel, lighter_vessel, master_mobile, escort_name, escort_mobile,
start_date, start_time, completion_date, completion_time, release_place,
start_shift, end_shift, signatures{}, confidence, missing_fields[], raw_ocr_text, extraction_id
```

**Required Fields:** mother_vessel, lighter_vessel, escort_name, escort_mobile, start_date, completion_date

**KB Coverage:** `ocr_engine.md` mentions OCR outputs but not EscortSlipResult structure, document type detection, or signature extraction.

**Enrichment Target:** `06_developer_system/ocr_engine.md` — add EscortSlipResult TypedDict and document type detection.

---

## Parser 14: Accountant Summary Detector (NEW) — 0% Covered

**Module:** `modules/accountant_summary`
**Output:** Boolean (is_accountant_summary) + parsed dict (date_str, label, amount)

**Detects:** Bengali accounting summary messages (company-level cash flow). Intentionally does NOT write to wbom_cash_transactions (requires employee_id NOT NULL).

**Supported Formats:**
- "7/5/26=জমা =75,000/-" (date + deposit + total)
- "7/5/26=টোটাল বাকি =51,238/-" (date + outstanding)
- "অগ্রিম জমা থাকে =23,762/-" (advance balance, no date)

**Labels Detected:** জমা, টোটাল বাকি, মোট বাকি, অগ্রিম জমা, অগ্রিম, অফিস ভাড়া, ভাড়া বাবদ, বেতন বাকি, মোট জমা, আয়, ব্যয়, লাভ, ক্ষতি (13 labels)

**KB Coverage:** None. Not documented anywhere.

**Enrichment Target:** `02_admin_knowledge/admin_payment_handling.md` — add accounting summary detection note.

---

## Parser 15: FPE Message Parser — 0% Covered

**Module:** `modules/fazle_payroll_engine/parser.py`
**Output:** `ParsedPayment` model

**Detects Message Types:** payment, balance_summary, cash_command, income_command, escort_payment, other

**KB Coverage:** None.

---

## Parser Coverage Summary

| # | Parser | Module | KB Coverage |
|---|---|---|---|
| 1 | Escort Order Parser | `escort` | 20% |
| 2 | Lighter Block Parser | `escort` | 0% |
| 3 | Labeled Lighter Parser | `escort` | 0% |
| 4 | Inline Lighter Parser | `escort` | 0% |
| 5 | MV Block Lighter Parser | `escort` | 0% |
| 6 | Completed Draft Detector | `escort` | 5% |
| 7 | Attendance Parser | `attendance_parser` | 20% |
| 8 | Release Confirmation Parser | `escort_lifecycle` | 0% |
| 9 | Payment SMS Parser | `payment_ingest` | 10% |
| 10 | Admin Cash Shorthand Parser | `payment_ingest` | 0% |
| 11 | RAG Tokenizer | `rag` | 5% |
| 12 | Intent Classifier | `intent` | 10% |
| 13 | Escort Slip Extractor | `escort_slip_extractor` | 0% |
| 14 | Accountant Summary Detector | `accountant_summary` | 0% |
| 15 | FPE Message Parser | `fazle_payroll_engine` | 0% |

**Average Parser Coverage: 5%**

**Enrichment Strategy:** Enrich existing articles first. Parser internals should go in the relevant workflow or developer article (not new dedicated parser articles).
