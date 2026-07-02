# Sprint-C0C: Canonical Schema Generation
## Financial Tables Canonical DDL — Written FROM Production, with Owner Decisions Applied

**Date:** 2026-06-28  
**Auditor:** Financial Architecture Refactoring Auditor  
**Methodology:** `pg_dump --schema-only` from production DB → apply Owner decisions → generate canonical DDL → diff against existing migrations → propose repair plan  
**Predecessors:** Sprint-C0A (Schema Certification — APPROVED), Sprint-C0B (Runtime Column Usage — APPROVED)  
**Principle:** "Migration is not Truth. Production is Truth."  
**Status:** CANONICAL SCHEMA APPROVED WITH MODIFICATIONS — Owner feedback incorporated

**Owner Modifications (LOCKED from C0C Approval):**
1. `approved_by` / `approved_at` → Do NOT physically DROP now. First declare as Deprecated. Physical DROP deferred to future sprint.
2. `source_bridge` fix → Add Acceptance Test to verify the fix prevents the crash.
3. conftest changes → Execute in small incremental steps, not all at once.
4. Create Migration Execution Order document before running any migration.

---

## Owner Decisions Applied (LOCKED from Sprint-C0B)

| # | Decision | Action Applied |
|---|----------|----------------|
| 1 | `wbom_cash_transactions.approved_by` — REMOVE | ❌ Excluded from canonical schema |
| 2 | `wbom_cash_transactions.approved_at` — REMOVE | ❌ Excluded from canonical schema |
| 3 | `fazle_payment_drafts.escort_roster_entry_id` — KEEP | ✅ Included in canonical schema |
| 4 | `wbom_cash_transactions.reference_number` — KEEP | ✅ Included in canonical schema |
| 5 | `fazle_payment_drafts.source_bridge` — FIX CODE, don't add column | ❌ Excluded from canonical schema; code fix deferred to C0D |

---

## Migration Directory Structure Discovery

**CRITICAL FINDING:** There are **three separate migration directories**, none of which is the single source of truth:

| Directory | Files | Purpose |
|-----------|-------|---------|
| `core/db/migrations/` | 001–022 | Main application migrations (wbom tables, drafts, escort, etc.) |
| `core/modules/fazle_payroll_engine/migrations/` | 001–009 | FPE engine migrations (fpe_cash_transactions, fpe_employee_ledger, etc.) |
| `core/migrations/` | 002–005 + escort schemas | Legacy/standalone migrations (indexes, constraints, escort schemas) |

**This is part of the Configuration Drift problem.** A fresh DB restore requires running migrations from ALL THREE directories in the correct order, and even then, production columns are missing (C0A finding). The canonical schema below supersedes all three.

---

## Canonical DDL: 8 Financial Tables

### Table 1: `fpe_cash_transactions` (Canonical: 19 columns)

```sql
-- Sequence
CREATE SEQUENCE public.fpe_cash_transactions_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

-- Table
CREATE TABLE public.fpe_cash_transactions (
    id bigint NOT NULL DEFAULT nextval('public.fpe_cash_transactions_id_seq'::regclass),
    txn_ref text NOT NULL,
    fpe_wa_message_id bigint,
    employee_id bigint,
    employee_name_raw text,
    amount numeric(12,2) NOT NULL,
    payout_phone text,
    payout_method text,
    txn_date date NOT NULL,
    txn_category text NOT NULL DEFAULT 'salary',
    source_message_text text,
    is_reversal boolean NOT NULL DEFAULT false,
    reversed_txn_id bigint,
    accounting_period text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by text NOT NULL DEFAULT 'fpe_engine',
    deleted_at timestamptz,
    deleted_by text,
    updated_at timestamptz DEFAULT now()
);

-- Primary Key
ALTER TABLE public.fpe_cash_transactions
    ADD CONSTRAINT fpe_cash_transactions_pkey PRIMARY KEY (id);

-- Unique Constraint
ALTER TABLE public.fpe_cash_transactions
    ADD CONSTRAINT fpe_cash_transactions_txn_ref_key UNIQUE (txn_ref);

-- Foreign Keys
ALTER TABLE public.fpe_cash_transactions
    ADD CONSTRAINT fpe_cash_transactions_employee_id_fkey
    FOREIGN KEY (employee_id) REFERENCES public.fpe_employees(id);

ALTER TABLE public.fpe_cash_transactions
    ADD CONSTRAINT fpe_cash_transactions_fpe_wa_message_id_fkey
    FOREIGN KEY (fpe_wa_message_id) REFERENCES public.fpe_wa_messages(id);

ALTER TABLE public.fpe_cash_transactions
    ADD CONSTRAINT fpe_cash_transactions_reversed_txn_id_fkey
    FOREIGN KEY (reversed_txn_id) REFERENCES public.fpe_cash_transactions(id);

-- Indexes
CREATE INDEX idx_fpe_txn_date_desc ON public.fpe_cash_transactions USING btree (txn_date DESC, id DESC);
CREATE INDEX idx_fpe_txn_emp_name_raw_trgm ON public.fpe_cash_transactions USING gin (lower(employee_name_raw) gin_trgm_ops);
CREATE INDEX idx_fpe_txn_employee ON public.fpe_cash_transactions USING btree (employee_id, txn_date);
CREATE INDEX idx_fpe_txn_not_deleted ON public.fpe_cash_transactions USING btree (id) WHERE (deleted_at IS NULL);
CREATE INDEX idx_fpe_txn_period ON public.fpe_cash_transactions USING btree (accounting_period);
CREATE INDEX idx_fpe_txn_phone ON public.fpe_cash_transactions USING btree (payout_phone);
CREATE INDEX idx_fpe_txn_raw_collapsed_trgm ON public.fpe_cash_transactions
    USING gin (regexp_replace(lower(COALESCE(employee_name_raw, '')), '[^a-z0-9]+', '', 'g') gin_trgm_ops);

-- Trigger Function
CREATE OR REPLACE FUNCTION public.fpe_txn_set_updated_at()
    RETURNS trigger LANGUAGE plpgsql AS
    $function$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $function$;

-- Trigger
CREATE TRIGGER fpe_txn_updated_at_trig
    BEFORE UPDATE ON public.fpe_cash_transactions
    FOR EACH ROW EXECUTE FUNCTION public.fpe_txn_set_updated_at();
```

**Diff from existing migration (`modules/fazle_payroll_engine/migrations/001_fpe_schema.sql`):**
- ❌ MISSING: `deleted_at`, `deleted_by`, `updated_at` columns
- ❌ MISSING: `idx_fpe_txn_not_deleted` index
- ❌ MISSING: `fpe_txn_set_updated_at()` function + trigger
- ❌ MISSING: `idx_fpe_txn_emp_name_raw_trgm`, `idx_fpe_txn_raw_collapsed_trgm` (trigram indexes — may be in 003)
- ❌ MISSING: `idx_fpe_txn_phone` index

---

### Table 2: `wbom_cash_transactions` (Canonical: 21 columns — Owner removed `approved_by` + `approved_at`)

```sql
-- Sequence
CREATE SEQUENCE public.wbom_cash_transactions_transaction_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

-- Table
CREATE TABLE public.wbom_cash_transactions (
    transaction_id integer NOT NULL DEFAULT nextval('public.wbom_cash_transactions_transaction_id_seq'::regclass),
    employee_id integer NOT NULL,
    program_id integer,
    transaction_type varchar(20) NOT NULL,
    amount numeric(10,2) NOT NULL,
    payment_method varchar(10) NOT NULL,
    payment_mobile varchar(20),
    transaction_date date NOT NULL DEFAULT CURRENT_DATE,
    transaction_time timestamptz DEFAULT now(),
    status varchar(20) DEFAULT 'Completed',
    reference_number varchar(50),
    remarks text,
    whatsapp_message_id varchar(100),
    created_by varchar(50),
    idempotency_key varchar(64),
    source varchar(30) DEFAULT 'web',
    is_reversed boolean DEFAULT false,
    reversal_of integer,
    correction_note text,
    employee_phone text,
    payment_number text
);

-- Primary Key
ALTER TABLE public.wbom_cash_transactions
    ADD CONSTRAINT wbom_cash_transactions_pkey PRIMARY KEY (transaction_id);

-- Foreign Keys
ALTER TABLE public.wbom_cash_transactions
    ADD CONSTRAINT wbom_cash_transactions_employee_id_fkey
    FOREIGN KEY (employee_id) REFERENCES public.wbom_employees(employee_id)
    ON UPDATE CASCADE ON DELETE RESTRICT;

ALTER TABLE public.wbom_cash_transactions
    ADD CONSTRAINT wbom_cash_transactions_program_id_fkey
    FOREIGN KEY (program_id) REFERENCES public.wbom_escort_programs(program_id);

ALTER TABLE public.wbom_cash_transactions
    ADD CONSTRAINT wbom_cash_transactions_reversal_of_fkey
    FOREIGN KEY (reversal_of) REFERENCES public.wbom_cash_transactions(transaction_id);

-- Indexes
CREATE INDEX idx_tx_date ON public.wbom_cash_transactions USING btree (transaction_date);
CREATE INDEX idx_tx_type_date ON public.wbom_cash_transactions USING btree (transaction_type, transaction_date);
CREATE UNIQUE INDEX idx_txn_idempotency ON public.wbom_cash_transactions USING btree (idempotency_key) WHERE (idempotency_key IS NOT NULL);
CREATE INDEX idx_wbom_transactions_date ON public.wbom_cash_transactions USING btree (transaction_date);
CREATE UNIQUE INDEX idx_wbom_transactions_dedup ON public.wbom_cash_transactions
    USING btree (employee_id, transaction_date, amount, transaction_type, payment_method)
    WHERE (status = 'Completed');
CREATE INDEX idx_wbom_transactions_employee ON public.wbom_cash_transactions USING btree (employee_id);
CREATE INDEX idx_wbom_transactions_type ON public.wbom_cash_transactions USING btree (transaction_type);
CREATE UNIQUE INDEX idx_wbom_transactions_wa_msg_dedup ON public.wbom_cash_transactions
    USING btree (whatsapp_message_id)
    WHERE (whatsapp_message_id IS NOT NULL AND whatsapp_message_id <> '');
CREATE UNIQUE INDEX idx_wbom_tx_dedup_migration ON public.wbom_cash_transactions
    USING btree (employee_id, amount, transaction_date, created_by)
    WHERE (created_by = 'migration_ops');
```

**Owner Decision Applied:** `approved_by varchar(80)` and `approved_at timestamptz` REMOVED from canonical schema.

**Diff from production:** Canonical has 21 columns; production has 23 (includes `approved_by` + `approved_at`). These 2 columns will be marked DEPRECATED in Sprint-C0D (no physical DROP yet, per Owner modification).

**Diff from conftest.py:**
- ❌ conftest has `is_reversal` (boolean) — NOT in canonical or production. REMOVE from conftest.
- ❌ conftest has `reversal_reason` (text) — NOT in canonical or production. REMOVE from conftest.
- ❌ conftest has `whatsapp_message_id` as INT — canonical is `varchar(100)`. FIX type in conftest.
- ❌ conftest missing `approved_by`, `approved_at` — but Owner decided to REMOVE these, so conftest is actually correct here (coincidentally).

---

### Table 3: `fpe_employee_ledger` (Canonical: 10 columns)

```sql
CREATE SEQUENCE public.fpe_employee_ledger_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE public.fpe_employee_ledger (
    id bigint NOT NULL DEFAULT nextval('public.fpe_employee_ledger_id_seq'::regclass),
    employee_id bigint NOT NULL,
    accounting_period text NOT NULL,
    opening_balance numeric(12,2) NOT NULL DEFAULT 0,
    total_earned numeric(12,2) NOT NULL DEFAULT 0,
    total_paid numeric(12,2) NOT NULL DEFAULT 0,
    total_advance numeric(12,2) NOT NULL DEFAULT 0,
    closing_balance numeric(12,2) NOT NULL DEFAULT 0,
    txn_count integer NOT NULL DEFAULT 0,
    last_updated timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.fpe_employee_ledger
    ADD CONSTRAINT fpe_employee_ledger_pkey PRIMARY KEY (id);

ALTER TABLE public.fpe_employee_ledger
    ADD CONSTRAINT fpe_ledger_unique UNIQUE (employee_id, accounting_period);

ALTER TABLE public.fpe_employee_ledger
    ADD CONSTRAINT fpe_employee_ledger_employee_id_fkey
    FOREIGN KEY (employee_id) REFERENCES public.fpe_employees(id);
```

**Diff from conftest.py:**
- ❌ conftest uses composite PK `(employee_id, accounting_period)` — canonical has `id` BIGSERIAL PK + unique constraint. FIX conftest.
- ❌ conftest has `employee_id` as INT — canonical is `bigint`. FIX type in conftest.

---

### Table 4: `fazle_payment_drafts` (Canonical: 38 columns — all production columns, `source_bridge` NOT included)

```sql
CREATE SEQUENCE public.fazle_payment_drafts_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE public.fazle_payment_drafts (
    id bigint NOT NULL DEFAULT nextval('public.fazle_payment_drafts_id_seq'::regclass),
    employee_mobile text,
    employee_name text,
    draft_text text,
    expected_amount numeric(12,2),
    method text,
    status text DEFAULT 'pending',
    created_at timestamptz DEFAULT now(),
    approved_at timestamptz,
    approved_amount numeric(12,2),
    payment_method text,
    admin_phone text,
    accountant_msg text,
    notes text,
    updated_at timestamptz DEFAULT now(),
    draft_type text NOT NULL DEFAULT 'escort_payment',
    employee_id integer,
    escort_program_id integer,
    duty_days numeric(6,2),
    source text DEFAULT 'bridge1',
    correction_of integer,
    correction_type text,
    correction_note text,
    corrected_by text,
    corrected_at timestamptz,
    expires_at timestamptz,
    escort_roster_entry_id integer,
    gross_amount numeric(12,2) NOT NULL DEFAULT 0,
    food_bill numeric(10,2) NOT NULL DEFAULT 0,
    conveyance numeric(10,2) NOT NULL DEFAULT 0,
    advance_deduction numeric(12,2) NOT NULL DEFAULT 0,
    payout_mobile text,
    purpose text,
    verification_summary jsonb,
    source_message text,
    conversation_summary jsonb,
    draft_created_by text DEFAULT 'ai_conversation',
    conversation_id text
);

ALTER TABLE public.fazle_payment_drafts
    ADD CONSTRAINT fazle_payment_drafts_pkey PRIMARY KEY (id);

ALTER TABLE public.fazle_payment_drafts
    ADD CONSTRAINT fazle_payment_drafts_correction_of_fkey
    FOREIGN KEY (correction_of) REFERENCES public.fazle_payment_drafts(id);

ALTER TABLE public.fazle_payment_drafts
    ADD CONSTRAINT fazle_payment_drafts_employee_fkey
    FOREIGN KEY (employee_id) REFERENCES public.wbom_employees(employee_id)
    ON UPDATE CASCADE ON DELETE SET NULL;

ALTER TABLE public.fazle_payment_drafts
    ADD CONSTRAINT fazle_payment_drafts_program_fkey
    FOREIGN KEY (escort_program_id) REFERENCES public.wbom_escort_programs(program_id)
    ON UPDATE CASCADE ON DELETE SET NULL;

-- Indexes
CREATE INDEX idx_fazle_payment_drafts_employee ON public.fazle_payment_drafts USING btree (employee_id);
CREATE INDEX idx_payment_drafts_created ON public.fazle_payment_drafts USING btree (created_at DESC);
CREATE INDEX idx_payment_drafts_phone ON public.fazle_payment_drafts USING btree (employee_mobile);
CREATE INDEX idx_payment_drafts_program_type ON public.fazle_payment_drafts USING btree (escort_program_id, draft_type);
CREATE INDEX idx_payment_drafts_status ON public.fazle_payment_drafts USING btree (status);
CREATE INDEX idx_pdraft_conversation ON public.fazle_payment_drafts
    USING btree (employee_mobile, status) WHERE (status = 'pending');
```

**Note:** `source_bridge` is NOT in the canonical schema (Owner Decision 5). Code fix required in `payment_correction/__init__.py:221`.

**Diff from conftest.py:**
- ❌ conftest has `admin_reply` — NOT in canonical or production. REMOVE from conftest.
- ❌ conftest has `source_bridge` — NOT in canonical or production. REMOVE from conftest.
- ❌ conftest has `payment_number` — NOT in canonical or production (for drafts table). REMOVE from conftest.
- ❌ conftest missing `method` column — ADD to conftest.
- ❌ conftest uses FLOAT for `gross_amount`/`food_bill`/`conveyance`/`advance_deduction` — canonical is `numeric`. FIX types in conftest.

---

### Table 5: `fpe_accounting_audit_logs` (Canonical: 9 columns)

```sql
CREATE SEQUENCE public.fpe_accounting_audit_logs_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE public.fpe_accounting_audit_logs (
    id bigint NOT NULL DEFAULT nextval('public.fpe_accounting_audit_logs_id_seq'::regclass),
    entity_type text NOT NULL,
    entity_id bigint NOT NULL,
    action text NOT NULL,
    before_state jsonb,
    after_state jsonb,
    performed_by text NOT NULL DEFAULT 'fpe_engine',
    reason text,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.fpe_accounting_audit_logs
    ADD CONSTRAINT fpe_accounting_audit_logs_pkey PRIMARY KEY (id);

CREATE INDEX idx_fpe_audit_entity ON public.fpe_accounting_audit_logs USING btree (entity_type, entity_id);
```

**Diff from conftest.py:**
- 🟢 Minor: conftest uses `VARCHAR(50)` for `entity_type`/`action` — canonical is `text`. FIX types in conftest.
- 🟢 Minor: conftest missing DEFAULT for `performed_by`. ADD default.

---

### Table 6: `fazle_payment_correction_log` (Canonical: 11 columns)

```sql
CREATE SEQUENCE public.fazle_payment_correction_log_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE public.fazle_payment_correction_log (
    id integer NOT NULL DEFAULT nextval('public.fazle_payment_correction_log_id_seq'::regclass),
    action text NOT NULL,
    payment_draft_id integer NOT NULL,
    transaction_id integer,
    counter_tx_id integer,
    original_amount double precision,
    correction_amount double precision,
    method text,
    note text,
    performed_by text,
    created_at timestamptz DEFAULT now()
);

ALTER TABLE public.fazle_payment_correction_log
    ADD CONSTRAINT fazle_payment_correction_log_pkey PRIMARY KEY (id);

ALTER TABLE public.fazle_payment_correction_log
    ADD CONSTRAINT fazle_payment_correction_log_payment_draft_id_fkey
    FOREIGN KEY (payment_draft_id) REFERENCES public.fazle_payment_drafts(id);

CREATE INDEX idx_pay_correction_log_draft ON public.fazle_payment_correction_log USING btree (payment_draft_id);
```

**Diff from conftest.py:** ✅ Aligned (minor type differences only).

---

### Table 7: `fpe_income_transactions` (Canonical: 11 columns)

```sql
CREATE SEQUENCE public.fpe_income_transactions_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE public.fpe_income_transactions (
    id bigint NOT NULL DEFAULT nextval('public.fpe_income_transactions_id_seq'::regclass),
    txn_ref text NOT NULL,
    fpe_wa_message_id bigint,
    employee_id bigint,
    employee_name_raw text,
    amount numeric(12,2) NOT NULL,
    txn_date date NOT NULL,
    accounting_period text NOT NULL,
    reported_by_phone text,
    source_message_text text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fpe_income_transactions_amount_check CHECK (amount > 0)
);

ALTER TABLE public.fpe_income_transactions
    ADD CONSTRAINT fpe_income_transactions_pkey PRIMARY KEY (id);

ALTER TABLE public.fpe_income_transactions
    ADD CONSTRAINT fpe_income_transactions_txn_ref_key UNIQUE (txn_ref);

ALTER TABLE public.fpe_income_transactions
    ADD CONSTRAINT fpe_income_transactions_employee_id_fkey
    FOREIGN KEY (employee_id) REFERENCES public.fpe_employees(id);

ALTER TABLE public.fpe_income_transactions
    ADD CONSTRAINT fpe_income_transactions_fpe_wa_message_id_fkey
    FOREIGN KEY (fpe_wa_message_id) REFERENCES public.fpe_wa_messages(id);

-- Indexes
CREATE INDEX idx_fpe_income_emp ON public.fpe_income_transactions USING btree (employee_id, txn_date DESC);
CREATE INDEX idx_fpe_income_period ON public.fpe_income_transactions USING btree (accounting_period);
CREATE INDEX idx_fpe_income_reporter ON public.fpe_income_transactions USING btree (reported_by_phone);
```

**Diff from conftest.py:** ❌ TABLE ENTIRELY MISSING from conftest. ADD complete table definition.

---

### Table 8: `wbom_staging_payments` (Canonical: 16 columns)

```sql
CREATE SEQUENCE public.wbom_staging_payments_staging_id_seq
    AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE public.wbom_staging_payments (
    staging_id integer NOT NULL DEFAULT nextval('public.wbom_staging_payments_staging_id_seq'::regclass),
    message_id integer,
    sender_number varchar(20),
    extracted_name varchar(100),
    extracted_mobile varchar(20),
    amount numeric(12,2),
    payment_method varchar(20),
    transaction_type varchar(30),
    matched_employee_id integer,
    name_match_ratio numeric(4,2),
    status varchar(20) DEFAULT 'pending',
    approved_by varchar(100),
    approved_at timestamp WITHOUT TIME ZONE,
    created_at timestamp WITHOUT TIME ZONE DEFAULT now(),
    final_transaction_id integer,
    idempotency_key varchar(64)
);

ALTER TABLE public.wbom_staging_payments
    ADD CONSTRAINT wbom_staging_payments_pkey PRIMARY KEY (staging_id);

ALTER TABLE public.wbom_staging_payments
    ADD CONSTRAINT wbom_staging_payments_final_transaction_id_fkey
    FOREIGN KEY (final_transaction_id) REFERENCES public.wbom_cash_transactions(transaction_id);

CREATE INDEX idx_staging_payments_status ON public.wbom_staging_payments USING btree (status);
```

**Diff from conftest.py:**
- ❌ conftest uses `TIMESTAMPTZ` for `approved_at`/`created_at` — canonical is `timestamp WITHOUT TIME ZONE`. FIX types.
- ❌ conftest uses `TEXT` for `sender_number`/`extracted_name`/`extracted_mobile` — canonical is `varchar`. FIX types.

---

## Proposed Migration Repair Plan (Sprint-C0D — NO EXECUTION YET)

### Migration C0D-001: Add soft-delete columns to `fpe_cash_transactions`

**Purpose:** Document the 3 phantom columns that exist in production but not in migrations.

```sql
-- Migration C0D-001: Add soft-delete and updated_at columns to fpe_cash_transactions
-- These columns already exist in production (added manually without tracked migration).
-- This migration is idempotent — safe to run on production (columns already exist).

ALTER TABLE fpe_cash_transactions
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS deleted_by TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Trigger function for auto-updating updated_at
CREATE OR REPLACE FUNCTION fpe_txn_set_updated_at()
    RETURNS trigger LANGUAGE plpgsql AS
    $function$ BEGIN NEW.updated_at = now(); RETURN NEW; END; $function$;

-- Trigger
DROP TRIGGER IF EXISTS fpe_txn_updated_at_trig ON fpe_cash_transactions;
CREATE TRIGGER fpe_txn_updated_at_trig
    BEFORE UPDATE ON fpe_cash_transactions
    FOR EACH ROW EXECUTE FUNCTION fpe_txn_set_updated_at();

-- Index for filtering non-deleted transactions
CREATE INDEX IF NOT EXISTS idx_fpe_txn_not_deleted
    ON fpe_cash_transactions USING btree (id) WHERE (deleted_at IS NULL);
```

### Migration C0D-002: Mark `approved_by` / `approved_at` as DEPRECATED (no physical DROP)

**Purpose:** Declare `approved_by` and `approved_at` as deprecated. Physical DROP deferred to future sprint.

**Owner Modification:** "এখনই Physical DROP নয়; প্রথমে Deprecated ঘোষণা।"

```sql
-- Migration C0D-002: Mark approved_by/approved_at as DEPRECATED (no physical DROP)
-- Owner Decision: Do NOT physically DROP now. Declare as Deprecated.
-- These columns have 0 rows, no active writer, not part of Business Constitution.
-- Physical DROP will be done in a future sprint after verification period.

COMMENT ON COLUMN wbom_cash_transactions.approved_by IS 'DEPRECATED — 0 rows, no active writer. Scheduled for removal in future sprint.';
COMMENT ON COLUMN wbom_cash_transactions.approved_at IS 'DEPRECATED — 0 rows, no active writer. Scheduled for removal in future sprint.';
```

### Migration C0D-003: Add missing indexes to `fpe_cash_transactions`

**Purpose:** Document indexes that exist in production but not in migrations.

```sql
-- Migration C0D-003: Add missing indexes (exist in production, not in migrations)
-- Requires pg_trgm extension

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_fpe_txn_emp_name_raw_trgm
    ON fpe_cash_transactions USING gin (lower(employee_name_raw) gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_fpe_txn_raw_collapsed_trgm
    ON fpe_cash_transactions
    USING gin (regexp_replace(lower(COALESCE(employee_name_raw, '')), '[^a-z0-9]+', '', 'g') gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_fpe_txn_phone
    ON fpe_cash_transactions USING btree (payout_phone);
```

### Migration C0D-004: Add `fpe_income_transactions` CHECK constraint

**Purpose:** Document the CHECK constraint that exists in production but may not be in migrations.

```sql
-- Migration C0D-004: Ensure income transaction amount check constraint exists
ALTER TABLE fpe_income_transactions
    DROP CONSTRAINT IF EXISTS fpe_income_transactions_amount_check,
    ADD CONSTRAINT fpe_income_transactions_amount_check CHECK (amount > 0);
```

### Code Fix C0D-005: Remove `source_bridge` from `payment_correction` INSERT

**Purpose:** Fix crash bug — `payment_correction.adjust_payment()` writes `source_bridge` to `fazle_payment_drafts` but column doesn't exist.

**File:** `core/modules/payment_correction/__init__.py`  
**Line:** 218-226  

**Current (broken):**
```python
"""INSERT INTO fazle_payment_drafts
       (draft_type, employee_id, employee_name, employee_mobile,
        escort_program_id, expected_amount, payment_method,
        status, admin_phone, source_bridge, draft_text, notes,
        correction_of, correction_type, correction_note, corrected_by, corrected_at)
   VALUES ($1, $2, $3, $4, $5, $6, $7,
           'pending', $8, $9, $10, $11,
           $12, 'adjustment', $13, $14, NOW())
   RETURNING id""",
```

**Fixed:**
```python
"""INSERT INTO fazle_payment_drafts
       (draft_type, employee_id, employee_name, employee_mobile,
        escort_program_id, expected_amount, payment_method,
        status, admin_phone, draft_text, notes,
        correction_of, correction_type, correction_note, corrected_by, corrected_at)
   VALUES ($1, $2, $3, $4, $5, $6, $7,
           'pending', $8, $9, $10,
           $11, 'adjustment', $12, $13, NOW())
   RETURNING id""",
```

**And remove the `source_bridge` parameter from the VALUES:**
- Remove `draft.get("source_bridge") or "bridge2"` (line 235)
- Renumber remaining parameters

---

## conftest.py Repair Plan (Sprint-C0D — NO EXECUTION YET)

### Changes Required

| # | Table | Action | Details |
|---|-------|--------|---------|
| 1 | `fpe_cash_transactions` | ADD columns | `deleted_at TIMESTAMPTZ`, `deleted_by TEXT`, `updated_at TIMESTAMPTZ DEFAULT NOW()` |
| 2 | `fpe_cash_transactions` | FIX types | `employee_id` → BIGINT (was INT NOT NULL, should be BIGINT nullable); `payout_method` → TEXT (was VARCHAR(20) NOT NULL); `accounting_period` → TEXT (was VARCHAR(7) NOT NULL) |
| 3 | `wbom_cash_transactions` | REMOVE columns | `is_reversal` (not in production), `reversal_reason` (not in production) |
| 4 | `wbom_cash_transactions` | FIX type | `whatsapp_message_id` → VARCHAR(100) (was INT) |
| 5 | `wbom_cash_transactions` | DO NOT ADD | `approved_by`, `approved_at` — Owner decided to REMOVE from canonical |
| 6 | `fpe_employee_ledger` | ADD column | `id BIGSERIAL PRIMARY KEY` (conftest uses composite PK) |
| 7 | `fpe_employee_ledger` | FIX type | `employee_id` → BIGINT (was INT) |
| 8 | `fazle_payment_drafts` | REMOVE columns | `admin_reply` (not in production), `source_bridge` (not in production), `payment_number` (not in production for drafts) |
| 9 | `fazle_payment_drafts` | ADD column | `method TEXT` (exists in production, missing from conftest) |
| 10 | `fazle_payment_drafts` | FIX types | `gross_amount`/`food_bill`/`conveyance`/`advance_deduction` → NUMERIC (was FLOAT) |
| 11 | `fpe_income_transactions` | ADD TABLE | Complete table definition missing from conftest |
| 12 | `fpe_accounting_audit_logs` | FIX types | `entity_type`/`action` → TEXT (was VARCHAR(50)); add DEFAULT 'fpe_engine' for `performed_by` |
| 13 | `wbom_staging_payments` | FIX types | `approved_at`/`created_at` → TIMESTAMP WITHOUT TIME ZONE (was TIMESTAMPTZ); `sender_number`/`extracted_name`/`extracted_mobile` → VARCHAR (was TEXT) |

---

## Canonical Schema Summary

| Table | Canonical Columns | Production Columns | Delta | Reason |
|-------|-------------------|-------------------|-------|--------|
| `fpe_cash_transactions` | 19 | 19 | 0 | Match (phantom columns now documented) |
| `wbom_cash_transactions` | 21 | 23 | -2 | Owner removed `approved_by` + `approved_at` |
| `fpe_employee_ledger` | 10 | 10 | 0 | Match |
| `fazle_payment_drafts` | 38 | 38 | 0 | Match (`source_bridge` never was in production) |
| `fpe_accounting_audit_logs` | 9 | 9 | 0 | Match |
| `fazle_payment_correction_log` | 11 | 11 | 0 | Match |
| `fpe_income_transactions` | 11 | 11 | 0 | Match |
| `wbom_staging_payments` | 16 | 16 | 0 | Match |
| **TOTAL** | **135** | **137** | **-2** | |

---

## Success Criteria

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Canonical DDL generated from production for all 8 tables | ✅ Complete |
| 2 | Owner decisions applied (approved_by/approved_at removed) | ✅ Applied |
| 3 | All constraints, indexes, sequences, triggers documented | ✅ Complete |
| 4 | Diff against existing migrations identified | ✅ Complete |
| 5 | Migration repair plan proposed (no execution) | ✅ 5 migrations proposed |
| 6 | conftest.py repair plan proposed (no execution) | ✅ 13 changes identified |
| 7 | Code fix for `source_bridge` crash bug proposed | ✅ Documented |

---

## Sign-off

| Role | Status |
|------|--------|
| Auditor | ✅ Canonical schema generated |
| Owner Decision | ✅ APPROVED WITH MODIFICATIONS — deprecation not DROP, acceptance test for source_bridge, incremental conftest changes, migration execution order document required |