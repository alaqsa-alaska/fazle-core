# Sprint-C1: Duplicate Code Audit Report

**Status:** AUDIT COMPLETE — Awaiting Owner Approval before Implementation  
**Date:** 2026-06-28  
**Auditor:** Software Auditor (not Code Writer)  
**SOP Phase:** Audit → Root Cause → Implementation Plan → **Owner Approval** → Implementation → Regression → Owner Acceptance → Production Approval  

---

## Executive Summary

Scanned 150+ Python files across `modules/`, `shared/`, `app/`. Found **18 duplicate code clusters** across 5 dimensions (functions, logic, SQL, parsers, validations). Classified by severity:

| Severity | Count | Description |
|----------|-------|-------------|
| 🔴 CRITICAL | 4 | Constitution §1 violations — parallel financial write paths bypassing `create_transaction()` |
| 🟠 HIGH | 8 | Duplicate functions with same logic in multiple files |
| 🟡 MEDIUM | 6 | Duplicate SQL patterns / query duplication |

**Constitutional Rule:** "One Logic → One Function"  
**Current Reality:** One logic exists in 2–7 places depending on the function.

---

## 🔴 CRITICAL — Constitution §1 Violations (Parallel Financial Logic)

### C1-001: `nl_advance_record.py` — Direct INSERT bypassing canonical path

**File:** [`modules/admin_commands/nl_advance_record.py:186`](modules/admin_commands/nl_advance_record.py:186)  
**Violation:** Direct `INSERT INTO wbom_cash_transactions` for advance recording.  
**Root Cause:** Written before Sprint-3B canonical transaction existed. Never refactored.  
**Impact:** Advances recorded here do NOT appear in `fpe_accounting_audit_logs`, do NOT get `txn_ref`, bypass idempotency framework.  
**Constitution Path:** This is Path-1 (Admin → Accountant WhatsApp → Canonical Transaction) but implemented as a shortcut bypass.  

### C1-002: `payment_ingest/__init__.py` — Direct INSERT bypassing canonical path

**File:** [`modules/payment_ingest/__init__.py:510`](modules/payment_ingest/__init__.py:510)  
**Violation:** Direct `INSERT INTO wbom_cash_transactions` for admin→accountant instruction processing.  
**Root Cause:** Pre-Sprint-3B code. Has its own idempotency_key logic but does not call `create_transaction()`.  
**Impact:** No `fpe_accounting_audit_logs` entry, no `txn_ref`, no ledger entry via canonical path.  

### C1-003: `payment_workflow/finalize_payment()` — Direct INSERT bypassing canonical path

**File:** [`modules/payment_workflow/__init__.py:335`](modules/payment_workflow/__init__.py:335)  
**Violation:** `finalize_payment()` directly inserts into `wbom_cash_transactions` instead of calling `create_transaction()`.  
**Root Cause:** This is the pre-Sprint-3B approval path. Sprint-3B's `draft_approval.create_canonical_transaction()` was built as the correct path, but `finalize_payment()` is **still actively called** by:
  - `admin_commands.__init__.py:772` (PAID command)
  - `payment_ingest/__init__.py:355`
  - `modules/payment/__init__.py` (re-export)
  - `app/main.py:38` (import)
**Impact:** Two competing paths to the same table. Sprint-3B uses `create_transaction()`, old PAID command uses `finalize_payment()` direct insert. Financial data inconsistency.  

### C1-004: `payment_correction/__init__.py` — Reversal INSERT bypassing canonical path

**File:** [`modules/payment_correction/__init__.py:95`](modules/payment_correction/__init__.py:95)  
**Violation:** Reversal counter-transaction directly inserted into `wbom_cash_transactions` instead of calling `reverse_transaction()` from `accounting.py`.  
**Root Cause:** Written independently. `accounting.reverse_transaction()` already exists and does the same thing canonically.  
**Impact:** Reversal transactions don't get canonical audit trail.  

### C1-005: `admin_transactions/__init__.py` — Parallel FPE transaction system

**File:** [`modules/admin_transactions/__init__.py:565`](modules/admin_transactions/__init__.py:565)  
**Violation:** `add_admin_transaction()` inserts into `fpe_cash_transactions` (different table) with its own audit log insertion and its own ledger adjustment (`_adjust_ledger`). This is a **parallel financial system** alongside `wbom_cash_transactions`.  
**Root Cause:** FPE engine has its own transaction table (`fpe_cash_transactions`) separate from legacy `wbom_cash_transactions`. Two transaction tables, two audit log insertion patterns, two ledger adjustment paths.  
**Impact:** Unclear which table is the source of truth. Constitution §1 says "single canonical transaction function" — currently there are two tables and two insertion paths.  
**Note:** This may be intentional (FPE migration in progress) — needs Owner clarification on whether `fpe_cash_transactions` is the future canonical table or a parallel system.  

---

## 🟠 HIGH — Duplicate Functions (Same Logic, Multiple Locations)

### C1-006: `normalize_phone` — 7 implementations

| # | File | Output Format | Delegates? |
|---|------|---------------|------------|
| 1 | [`shared/phone.py:70`](shared/phone.py:70) | `01XXXXXXXXXXX` (11-digit) | Yes → number_identity |
| 2 | [`shared/identity_map.py:62`](shared/identity_map.py:62) | `01XXXXXXXXXXX` (11-digit) | Yes → number_identity |
| 3 | [`modules/phone_normalizer/__init__.py:13`](modules/phone_normalizer/__init__.py:13) | `8801XXXXXXXXX` (13-digit) | **Source of truth** |
| 4 | [`modules/contact_sync/__init__.py:57`](modules/contact_sync/__init__.py:57) | `8801XXXXXXXXX` | Yes → phone_normalizer |
| 5 | [`modules/number_identity/__init__.py:18`](modules/number_identity/__init__.py:18) | `list[str]` (all variants) | Yes → phone_normalizer |
| 6 | [`modules/user_role/__init__.py:58`](modules/user_role/__init__.py:58) | `01XXXXXXXXXXX` | Independent (own logic) |
| 7 | [`modules/rbac/__init__.py:77`](modules/rbac/__init__.py:77) | `8801XXXXXXXXX` or raw | Yes → phone_normalizer |

**Root Cause:** `phone_normalizer` is the canonical source, but `user_role` has its own independent implementation. `shared/phone.py` and `shared/identity_map.py` are thin wrappers (acceptable).  
**Fix:** `user_role.normalize_phone` should delegate to `phone_normalizer` or `shared/phone.py`.  

### C1-007: `_is_valid_human_name` — 2 identical copies

| # | File | Note |
|---|------|------|
| 1 | [`modules/fazle_payroll_engine/parser.py:429`](modules/fazle_payroll_engine/parser.py:429) | Original |
| 2 | [`modules/fazle_payroll_engine/employee.py:34`](modules/fazle_payroll_engine/employee.py:34) | Copy with comment: "Mirror of parser._is_valid_human_name (kept local to avoid import cycle)" |

**Root Cause:** Import cycle avoidance. Both files are in the same package (`fazle_payroll_engine`).  
**Fix:** Move to `fazle_payroll_engine/models.py` or a shared utils module within the package.  

### C1-008: `_phone_variants` — 3 different implementations

| # | File | Logic |
|---|------|-------|
| 1 | [`modules/employee_conversation/__init__.py:255`](modules/employee_conversation/__init__.py:255) | Manual string manipulation (880→01, 01→880, +880→880) |
| 2 | [`modules/message_router/__init__.py:109`](modules/message_router/__init__.py:109) | Delegates to `get_phone_variants()` |
| 3 | [`modules/admin_commands/nl_router.py:53`](modules/admin_commands/nl_router.py:53) | SQL LIKE patterns (canonical, local, +canonical) |

**Root Cause:** Three different needs (DB lookup, SQL LIKE, variant list) solved independently.  
**Fix:** Consolidate to one `phone_variants()` function in `shared/phone.py` with output format options.  

### C1-009: `is_admin_command` — 2 completely different implementations

| # | File | Logic |
|---|------|-------|
| 1 | [`modules/admin_commands/__init__.py:168`](modules/admin_commands/__init__.py:168) | Regex-based: checks 20+ command patterns (APPROVE, REJECT, PAID, etc.) |
| 2 | [`modules/intent/__init__.py:161`](modules/intent/__init__.py:161) | Pattern-based: checks 5 generic patterns (id:, send to:, w/a no., release, mv) |

**Root Cause:** `admin_commands.is_admin_command` is the authoritative command detector. `intent.is_admin_command` is a heuristic pre-check for intent classification. Different purposes but same name = confusion.  
**Fix:** Rename `intent.is_admin_command` → `intent._looks_like_admin_text` or merge into one function with a `strict` parameter.  

### C1-010: `_require_api_key` — 4 copies

| # | File |
|---|------|
| 1 | [`modules/drafts/routes.py:27`](modules/drafts/routes.py:27) |
| 2 | [`modules/admin_transactions/__init__.py:45`](modules/admin_transactions/__init__.py:45) |
| 3 | [`modules/social_auto_reply/routes.py:16`](modules/social_auto_reply/routes.py:16) |
| 4 | [`modules/wa_chat_frontend/__init__.py:58`](modules/wa_chat_frontend/__init__.py:58) |

**Root Cause:** Each route module defines its own API key dependency.  
**Fix:** Move to `shared/auth.py` or `app/auth.py`, import everywhere.  

### C1-011: Session management — `_create_session` / `_advance_session` / `_close_session`

| Function | employee_conversation | employee_verification |
|----------|----------------------|---------------------|
| `_create_session` | ❌ (uses `_create_conversation_session`) | [`__init__.py:91`](modules/employee_verification/__init__.py:91) |
| `_advance_session` | [`__init__.py:409`](modules/employee_conversation/__init__.py:409) | [`__init__.py:123`](modules/employee_verification/__init__.py:123) |
| `_close_session` | [`__init__.py:424`](modules/employee_conversation/__init__.py:424) | [`__init__.py:136`](modules/employee_verification/__init__.py:136) |

**Root Cause:** Two conversation systems (employee_conversation for payment requests, employee_verification for slip/advance verification) with parallel session lifecycle code.  
**Fix:** Extract to `shared/session.py` with table-name parameter.  

### C1-012: Draft approve/edit/reject — 2 copies (reply drafts, not payment drafts)

| Function | drafts/routes.py | wa_chat_frontend |
|----------|------------------|-----------------|
| `approve_draft` | [`routes.py:128`](modules/drafts/routes.py:128) | [`__init__.py:570`](modules/wa_chat_frontend/__init__.py:570) |
| `edit_draft` | [`routes.py:190`](modules/drafts/routes.py:190) | [`__init__.py:558`](modules/wa_chat_frontend/__init__.py:558) |
| `reject_draft` | ❌ | [`__init__.py:601`](modules/wa_chat_frontend/__init__.py:601) |
| `list_drafts` | [`routes.py:72`](modules/drafts/routes.py:72) | [`__init__.py:504`](modules/wa_chat_frontend/__init__.py:504) |

**Note:** These operate on `fazle_draft_replies` (WhatsApp reply drafts), NOT `fazle_payment_drafts` (Sprint-3B payment drafts). Different table, different purpose — but same approve/edit/reject logic duplicated.  
**Fix:** Extract shared draft-reply operations to `shared/draft_reply.py` (already exists but may not have these operations).  

### C1-013: `expire_stale_drafts` — 2 implementations

| # | File | Table | TTL |
|---|------|-------|-----|
| 1 | [`shared/draft.py:41`](shared/draft.py:41) | `fazle_payment_drafts` | 24h |
| 2 | [`modules/escort_roster/db.py:857`](modules/escort_roster/db.py:857) | Unknown (escort-specific) | 48h |

**Root Cause:** Different draft tables, different TTL requirements. May be legitimate.  
**Fix:** Verify if these operate on the same table. If yes, consolidate. If no, document the distinction.  

---

## 🟡 MEDIUM — Duplicate SQL Patterns

### C1-014: `SELECT * FROM fpe_employees WHERE id = $1` — 6+ occurrences

**Files:** `fazle_payroll_engine/routes.py` (lines 433, 1122, 1162, 1205, 2566, 2711), `employee.py:322,410`, `normalization.py:106`  
**Fix:** Add `get_fpe_employee_by_id()` to `fazle_payroll_engine/employee.py`.  

### C1-015: `SELECT employee_id FROM wbom_employees WHERE employee_mobile = $1` — 5+ occurrences

**Files:** `admin_commands/__init__.py:867,998,1003`, `admin_employees/__init__.py:126,241`, `attendance_parser:190`, `escort/__init__.py:1174`, `message_router:755`  
**Fix:** Add `get_employee_by_phone()` to a shared employee lookup module.  

### C1-016: `SELECT * FROM fazle_payment_drafts WHERE id = $1` — 3+ occurrences

**Files:** `admin_commands/__init__.py:764`, `payment_correction/__init__.py:55,178`, `draft_approval/__init__.py:477`  
**Fix:** `draft_approval.retrieve_draft()` already exists — other modules should use it.  

### C1-017: `INSERT INTO fpe_accounting_audit_logs` — 5 occurrences

**Files:** `accounting.py:79,158` (canonical), `admin_transactions/__init__.py:611,729,795` (parallel)  
**Root Cause:** `admin_transactions` has its own audit insertion instead of calling a shared audit function.  
**Fix:** Extract `_write_audit_log()` to `fazle_payroll_engine/accounting.py` and call from both.  

### C1-018: `_lookup_employee` / `_lookup_contact` — duplicated

**Files:** `identity_brain/__init__.py:191,274` vs `user_role/__init__.py:201,221`  
**Fix:** Consolidate to one employee/contact lookup in `identity_brain` (canonical) and have `user_role` import it.  

---

## Root Cause Analysis

| Root Cause | Affected Items | Pattern |
|------------|---------------|---------|
| **Pre-Sprint-3B code never refactored** | C1-001, C1-002, C1-003, C1-004 | Canonical path was built but old paths were never decommissioned |
| **Import cycle avoidance** | C1-007 | Functions copied instead of refactored to shared module |
| **Independent module development** | C1-006, C1-008, C1-009, C1-010, C1-011 | Each module solved its own problem without checking for existing solutions |
| **Two draft systems** | C1-012 | `fazle_draft_replies` (reply drafts) vs `fazle_payment_drafts` (payment drafts) — same operations, different tables |
| **Two transaction tables** | C1-005, C1-017 | `wbom_cash_transactions` (legacy) vs `fpe_cash_transactions` (FPE engine) — unclear which is canonical |
| **No shared utility layer** | C1-014, C1-015, C1-016 | Common queries repeated instead of wrapped in functions |

---

## Implementation Plan (Proposed — Requires Owner Approval)

### Phase 1: Constitutional Fixes (C1-001 through C1-005) — PRIORITY

These are Constitution §1 violations. Each direct `INSERT INTO wbom_cash_transactions` must be refactored to call `create_transaction()` or `reverse_transaction()` from `accounting.py`.

| Item | Action | Risk |
|------|--------|------|
| C1-001 `nl_advance_record` | Route through `create_transaction()` with `txn_type='advance'` | Low — admin NL command, testable |
| C1-002 `payment_ingest` | Route through `create_transaction()` | Medium — has own idempotency, need to preserve |
| C1-003 `finalize_payment` | Refactor to call `create_transaction()`, OR deprecate in favor of `draft_approval.create_canonical_transaction()` | **High — actively called by PAID command** |
| C1-004 `payment_correction` | Route reversal through `reverse_transaction()` | Low — correction is rare path |
| C1-005 `admin_transactions` | **Owner decision needed**: Is `fpe_cash_transactions` the future canonical table? If yes, `create_transaction()` needs to target it. If no, `admin_transactions` should use `wbom_cash_transactions`. | **Blocked on Owner input** |

### Phase 2: Function Consolidation (C1-006 through C1-013)

| Item | Action | Risk |
|------|--------|------|
| C1-006 `normalize_phone` | Make `user_role` delegate to `phone_normalizer` | Low |
| C1-007 `_is_valid_human_name` | Move to `fazle_payroll_engine/models.py` | Low |
| C1-008 `_phone_variants` | Consolidate to `shared/phone.py` with format parameter | Low |
| C1-009 `is_admin_command` | Rename `intent.is_admin_command` → `_looks_like_admin_text` | Low |
| C1-010 `_require_api_key` | Move to `shared/auth.py` | Low |
| C1-011 session management | Extract to `shared/session.py` | Medium |
| C1-012 draft reply ops | Extract to `shared/draft_reply.py` | Medium |
| C1-013 `expire_stale_drafts` | Verify tables, document or consolidate | Low |

### Phase 3: SQL Pattern Consolidation (C1-014 through C1-018)

| Item | Action | Risk |
|------|--------|------|
| C1-014 | Add `get_fpe_employee_by_id()` | Low |
| C1-015 | Add `get_employee_by_phone()` | Low |
| C1-016 | Use existing `draft_approval.retrieve_draft()` | Low |
| C1-017 | Extract `_write_audit_log()` | Low |
| C1-018 | Consolidate lookup to `identity_brain` | Low |

---

## Open Questions for Owner

1. **C1-005:** Is `fpe_cash_transactions` intended to replace `wbom_cash_transactions` as the canonical transaction table? Or are they parallel systems that should coexist?
2. **C1-003:** Should `finalize_payment()` be deprecated entirely in favor of `draft_approval.create_canonical_transaction()`? The PAID command currently calls `finalize_payment()` — should it be redirected to the Sprint-3B path?
3. **C1-012:** Are `fazle_draft_replies` (WhatsApp reply drafts) and `fazle_payment_drafts` (payment drafts) both still needed, or should reply drafts be migrated to the payment draft system?

---

## Regression Strategy

After implementation:
1. Run full test suite (110 tests) — must remain 0 regressions
2. Add specific tests for each refactored path verifying:
   - Transaction appears in `wbom_cash_transactions` (or `fpe_cash_transactions`)
   - Audit log entry created in `fpe_accounting_audit_logs`
   - `txn_ref` generated
   - Idempotency preserved
3. Verify Constitution §1 compliance: `grep -rn "INSERT INTO wbom_cash_transactions" --include="*.py" modules/ | grep -v accounting.py` must return **zero results**

---

## Sign-off

| Role | Status |
|------|--------|
| Auditor | ✅ Complete |
| Owner Approval | ⏳ Pending |
| Implementation | ⏳ Blocked on Owner Approval |