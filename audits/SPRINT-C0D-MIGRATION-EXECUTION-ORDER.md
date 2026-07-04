# Sprint-C0D: Migration Execution Order
## Required order for all migration files to achieve Canonical Schema alignment

**Date:** 2026-06-28  
**Prerequisite:** Sprint-C0C Canonical Schema APPROVED  
**Principle:** "Migration is not Truth. Production is Truth."  
**Owner Requirement:** "Migration Execution Order ডকুমেন্ট তৈরি করুন।"  

---

## Existing Migration Inventory

### Directory 1: `core/db/migrations/` (Main application)

| Order | File | Tables Affected |
|-------|------|-----------------|
| 001 | `001_safe_mode_and_escort_extractor.sql` | escort extractor config |
| 002 | `002_align_tables.sql` | wbom tables alignment |
| 003 | `003_kb_schema_fix_and_recruitment.sql` | KB, recruitment |
| 003b | `003b_recruitment_sessions_fix.sql` | recruitment sessions |
| 003c | `003c_schema_align.sql` | schema alignment |
| 004 | `004_identity_brain.sql` | identity brain |
| 005 | `005_unified_draft_approval.sql` | draft approval |
| 006 | `006_critical_contact_zero_loss.sql` | contacts |
| 007 | `007_reviewed_reply_memory.sql` | reply memory |
| 008 | `008_payment_correction.sql` | payment correction, fazle_payment_drafts |
| 010 | `010_state_version.sql` | state version |
| 011 | `011_runtime_nodes.sql` | runtime nodes |
| 012 | `012_ai_readonly_views.sql` | AI readonly views |
| 012q | `012_queue_arbiter.sql` | queue arbiter |
| 013 | `013_social_auto_reply.sql` | social auto reply |
| 014 | `014_career_hr_dataset.sql` | career HR |
| 015 | `015_llm_learning_memory.sql` | LLM memory |
| 016 | `016_user_profiles_memory.sql` | user profiles |
| 017 | `017_memory_promotion.sql` | memory promotion |
| 018 | `018_atomic_release_and_outbound.sql` | atomic release |
| 019 | `019_central_message_fields.sql` | central message fields |
| 020 | `020_escort_roster_employee_fk.sql` | escort roster FK |
| 021 | `021_sprint3a_employee_conversation_drafts.sql` | fazle_payment_drafts (Sprint-3A columns) |
| 022 | `022_sprint3b_draft_approval.sql` | fazle_payment_drafts (Sprint-3B columns) |

### Directory 2: `core/modules/fazle_payroll_engine/migrations/` (FPE engine)

| Order | File | Tables Affected |
|-------|------|-----------------|
| FPE-001 | `001_fpe_schema.sql` | fpe_cash_transactions, fpe_employee_ledger |
| FPE-002 | `002_fpe_normalization.sql` | fpe normalization |
| FPE-003 | `003_fpe_search_indexes.sql` | fpe search indexes |
| FPE-004 | `004_fpe_zero_loss.sql` | fpe zero loss |
| FPE-005 | `005_review_audit_logs.sql` | fpe_accounting_audit_logs |
| FPE-006 | `006_fpe_income.sql` | fpe_income_transactions |
| FPE-007 | `007_fpe_diagnostics.sql` | fpe diagnostics |
| FPE-008 | `008_unification.sql` | unification (escort_roster_entry_id) |
| FPE-009 | `009_stabilization.sql` | stabilization |

### Directory 3: `core/migrations/` (Legacy/standalone)

| Order | File | Tables Affected |
|-------|------|-----------------|
| LEG-002 | `002_add_hr_officers.sql` | HR officers |
| LEG-003 | `003_add_fpe_indexes.sql` | fpe indexes |
| LEG-004 | `004_add_accounting_period_constraint.sql` | accounting period |
| LEG-005 | `005_add_admin_login_auth.sql` | admin login |
| LEG-EH | `escort_history_schema.sql` | escort history |
| LEG-ER | `escort_roster_schema.sql` | escort roster |

---

## New C0D Migration Execution Order

All new C0D migrations go into `core/db/migrations/` with prefix `023_` (continuing the main sequence).

### Execution Order (Strict — must run in this sequence)

| Step | Migration File | Action | Idempotent? | Risk |
|------|---------------|--------|-------------|------|
| 1 | `023_c0d_001_fpe_soft_delete_columns.sql` | Add `deleted_at`, `deleted_by`, `updated_at` to `fpe_cash_transactions` + trigger + index | ✅ Yes (IF NOT EXISTS) | LOW — columns already exist in production |
| 2 | `023_c0d_002_deprecate_approved_columns.sql` | Add COMMENT marking `approved_by`/`approved_at` as DEPRECATED | ✅ Yes (COMMENT is idempotent) | LOW — no schema change |
| 3 | `023_c0d_003_fpe_missing_indexes.sql` | Add trigram + phone indexes to `fpe_cash_transactions` | ✅ Yes (IF NOT EXISTS) | LOW — indexes already exist in production |
| 4 | `023_c0d_004_income_check_constraint.sql` | Ensure CHECK constraint on `fpe_income_transactions.amount` | ✅ Yes (DROP IF EXISTS + ADD) | LOW |

### Code Fix (not a migration — applied to source code)

| Step | File | Action | Risk |
|------|------|--------|------|
| 5 | `core/modules/payment_correction/__init__.py` | Remove `source_bridge` from INSERT statement | MEDIUM — changes INSERT column list and parameter numbering |

### Acceptance Test (not a migration — new test file)

| Step | File | Action | Risk |
|------|------|--------|------|
| 6 | `core/tests/unit/test_c0d_source_bridge_fix.py` | Test that `adjust_payment()` does not reference `source_bridge` column | LOW |

### conftest.py Repair (incremental — 5 steps)

| Step | Table(s) | Action | Risk |
|------|----------|--------|------|
| 7 | `fpe_cash_transactions` | Add 3 columns, fix 3 types | MEDIUM |
| 8 | `wbom_cash_transactions` | Remove 2 phantom columns, fix 1 type | MEDIUM |
| 9 | `fpe_employee_ledger` | Add `id` column, fix `employee_id` type | MEDIUM |
| 10 | `fazle_payment_drafts` | Remove 3 phantom columns, add `method`, fix 4 types | MEDIUM |
| 11 | `fpe_income_transactions` (new table) + `fpe_accounting_audit_logs` + `wbom_staging_payments` | Add income table, fix audit types, fix staging types | MEDIUM |

### Regression

| Step | Action | Success Criteria |
|------|--------|-----------------|
| 12 | Run full test suite | All 110 tests must pass |

---

## Safety Rules

1. **Production Freeze:** No ALTER TABLE on production during C0D. Migrations are created as files only.
2. **Idempotency:** All migrations use `IF NOT EXISTS` / `IF EXISTS` — safe to run on production (which already has these columns).
3. **conftest First:** conftest changes are applied and tested BEFORE any production migration runs.
4. **Incremental Testing:** After each conftest step, run the test suite to catch regressions early.
5. **No Data Loss:** No DROP COLUMN, no DELETE, no TRUNCATE. Only ADD COLUMN, COMMENT, CREATE INDEX.
6. **Rollback Plan:** Each migration has a commented rollback statement.

---

## Rollback Plan

| Migration | Rollback |
|-----------|----------|
| C0D-001 | `ALTER TABLE fpe_cash_transactions DROP COLUMN IF EXISTS deleted_at, DROP COLUMN IF EXISTS deleted_by, DROP COLUMN IF EXISTS updated_at; DROP TRIGGER IF EXISTS fpe_txn_updated_at_trig ON fpe_cash_transactions;` |
| C0D-002 | `COMMENT ON COLUMN wbom_cash_transactions.approved_by IS NULL; COMMENT ON COLUMN wbom_cash_transactions.approved_at IS NULL;` |
| C0D-003 | `DROP INDEX IF EXISTS idx_fpe_txn_emp_name_raw_trgm; DROP INDEX IF EXISTS idx_fpe_txn_raw_collapsed_trgm; DROP INDEX IF EXISTS idx_fpe_txn_phone;` |
| C0D-004 | `ALTER TABLE fpe_income_transactions DROP CONSTRAINT IF EXISTS fpe_income_transactions_amount_check;` |
| Code Fix | Git revert the `payment_correction/__init__.py` change |
| conftest | Git revert the `conftest.py` changes |