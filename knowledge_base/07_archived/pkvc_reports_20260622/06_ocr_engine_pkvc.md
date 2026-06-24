---
title: PKVC Report — ocr_engine.md
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Report — ocr_engine.md
**Article:** `06_developer_system/ocr_engine.md`
**Wave:** Wave-2B (full replacement of 18-line stub)
**PKVC Date:** 2026-06-22
**Verification Standard:** Three-level Evidence

---

## Critical Claims Verified

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | OcrResult TypedDict has exactly 14 fields | VERIFIED | `modules/ocr_processor/__init__.py` `class OcrResult(TypedDict)`: raw_text, slip_type, is_duplicate, duplicate_of, confidence_score, employee_name, employee_id, date, vessel, location, amount, client, reference_no, reply — 14 fields confirmed |
| 2 | DocResult TypedDict has exactly 6 fields | VERIFIED | `modules/ocr_processor/__init__.py` `class DocResult(TypedDict)`: extracted_text, doc_type, filename, confidence_score, reply, auto_send_safe — 6 fields confirmed |
| 3 | 3 slip types in `_classify_slip()` + unknown + duplicate | VERIFIED | `modules/ocr_processor/__init__.py` `_classify_slip()`: escort_slip, release_slip, payment_slip; defaults to unknown; is_duplicate checked before classification |
| 4 | 7 document types in `_classify_candidate_doc()` | VERIFIED | cv, nid, certificate, chairman_cert, handwritten, passport_photo, passport — 7 confirmed |
| 5 | SHA-256 duplicate detection via `check_and_register()` | VERIFIED | `modules/image_hash/` module called at top of `process_image()` |
| 6 | `auto_send_safe = True` for all 7 recognized doc types; `False` for unknown | VERIFIED | `modules/ocr_processor/__init__.py` DocResult construction |
| 7 | Confidence < 40 → warning suffix in reply | VERIFIED | `_build_reply()` — `[⚠️ কম নির্ভরযোগ্য — যাচাই করুন]` appended when `confidence_score < 40` |
| 8 | Document confidence = word-count proxy | VERIFIED | `min(100, max(10, len(extracted_text.split()) * 2))` formula confirmed |
| 9 | Image requirements: 1KB–8MB, JPG/JPEG/PNG/WEBP | VERIFIED | `modules/ocr_processor/__init__.py` input validation |
| 10 | `classify_from_context()` Phase 22 pre-filter | VERIFIED | Function confirmed in source; Phase 22 label in source comment |
| 11 | OCR service endpoint: `POST {media_processor_url}/ocr` | VERIFIED | `_call_ocr()` function in `ocr_processor/__init__.py` |
| 12 | Document extract endpoint: `POST {media_processor_url}/extract` | VERIFIED | `_call_extract()` function confirmed |
| 13 | Future scope: candidate CV extraction (management decision) | VERIFIED (Management Override) | Management decision approved 2026-06-22; DocResult and `_classify_candidate_doc()` are production-ready |

## Unverified / Legacy Claims

None.

## Pre-Correction Issues

None. No corrections required.

## Certification Decision

**CERTIFIED** — All 13 critical claims verified against production code.
