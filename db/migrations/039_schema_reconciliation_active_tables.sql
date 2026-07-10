-- Migration 039: Schema Reconciliation Sprint, Part 1 (Owner-approved, 2026-07-10)
--
-- Backfills the ~7 tables identified by the schema-governance investigation
-- (2026-07-10) as genuinely LIVE and actively written by production code,
-- but never created by any migration file anywhere. Scope, per Owner
-- instruction: preserve the production schema EXACTLY as it is today (no
-- redesign, no renamed columns, no new constraints beyond what already
-- exists) -- every definition below is copied verbatim from
-- `pg_dump --schema-only` against production, not reconstructed from code
-- reading. All statements are idempotent (CREATE ... IF NOT EXISTS / guarded
-- DO blocks) and are a no-op in production, where every one of these
-- tables already exists exactly as defined here. Their purpose is to let a
-- from-scratch environment (a fresh DB, or the test harness) build the same
-- schema production actually runs on.
--
-- Tables and their real, live write-sites (confirmed via code search before
-- writing this migration):
--   fpe_transaction_repairs   -- tools/repair_fpe_transactions.py, tools/review_repairs.py
--                                (manual payroll-correction audit trail; 285 rows)
--   fazle_reconciliation_log  -- the hourly job that auto-matches unmatched
--                                payments to employees
--   fazle_reminders_sent      -- dedup marker so scheduled reminder jobs don't
--                                re-notify the owner about the same stale item
--   fazle_scheduled_jobs      -- modules/scheduler's own job-run bookkeeping
--   fazle_report_runs         -- modules/reports's own report-run audit log
--   fazle_unified_contacts    -- modules/contact_sync's contact dedup/normalize
--                                layer (also written alongside wbom_contacts on
--                                every sync -- see the governance report's
--                                "active duplication" finding; NOT resolved by
--                                this migration, just documented into the
--                                governed path as it exists today)
--   fazle_contact_sync_log    -- modules/contact_sync's own sync-run log

-- ── fpe_transaction_repairs ───────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS fpe_transaction_repairs_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE IF NOT EXISTS fpe_transaction_repairs (
    id                  BIGINT NOT NULL DEFAULT nextval('fpe_transaction_repairs_id_seq') PRIMARY KEY,
    transaction_id      BIGINT NOT NULL,
    old_employee_id     BIGINT,
    new_employee_id     BIGINT,
    old_employee_name   TEXT,
    new_employee_name   TEXT,
    repair_reason       TEXT,
    match_method        TEXT,
    reversal_txn_id     BIGINT,
    new_txn_id          BIGINT,
    review_needed       BOOLEAN NOT NULL DEFAULT false,
    review_note         TEXT,
    dry_run             BOOLEAN NOT NULL DEFAULT false,
    repaired_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    repaired_by         TEXT NOT NULL DEFAULT 'repair_tool'
);

ALTER SEQUENCE fpe_transaction_repairs_id_seq OWNED BY fpe_transaction_repairs.id;

COMMENT ON TABLE fpe_transaction_repairs IS
    'Audit trail for every transaction re-assignment made by repair_fpe_transactions.py';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fpe_transaction_repairs_transaction_id_fkey') THEN
        ALTER TABLE fpe_transaction_repairs
            ADD CONSTRAINT fpe_transaction_repairs_transaction_id_fkey
            FOREIGN KEY (transaction_id) REFERENCES fpe_cash_transactions(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fpe_transaction_repairs_reversal_txn_id_fkey') THEN
        ALTER TABLE fpe_transaction_repairs
            ADD CONSTRAINT fpe_transaction_repairs_reversal_txn_id_fkey
            FOREIGN KEY (reversal_txn_id) REFERENCES fpe_cash_transactions(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fpe_transaction_repairs_new_txn_id_fkey') THEN
        ALTER TABLE fpe_transaction_repairs
            ADD CONSTRAINT fpe_transaction_repairs_new_txn_id_fkey
            FOREIGN KEY (new_txn_id) REFERENCES fpe_cash_transactions(id);
    END IF;
END $$;

-- ── fazle_reconciliation_log ──────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS fazle_reconciliation_log_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE IF NOT EXISTS fazle_reconciliation_log (
    id                  BIGINT NOT NULL DEFAULT nextval('fazle_reconciliation_log_id_seq') PRIMARY KEY,
    source              VARCHAR(40) NOT NULL,
    source_ref          VARCHAR(120),
    matched             BOOLEAN NOT NULL DEFAULT false,
    match_method        VARCHAR(40),
    matched_employee_id INTEGER,
    draft_id            INTEGER,
    attempted_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    details             TEXT
);

ALTER SEQUENCE fazle_reconciliation_log_id_seq OWNED BY fazle_reconciliation_log.id;

CREATE INDEX IF NOT EXISTS idx_recon_source_ref ON fazle_reconciliation_log (source, source_ref);

-- ── fazle_reminders_sent ──────────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS fazle_reminders_sent_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE IF NOT EXISTS fazle_reminders_sent (
    id              BIGINT NOT NULL DEFAULT nextval('fazle_reminders_sent_id_seq') PRIMARY KEY,
    reminder_key    VARCHAR(120) NOT NULL UNIQUE,
    topic           VARCHAR(40) NOT NULL,
    ref_id          VARCHAR(80),
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    meta_json       JSONB NOT NULL DEFAULT '{}'::jsonb
);

ALTER SEQUENCE fazle_reminders_sent_id_seq OWNED BY fazle_reminders_sent.id;

CREATE INDEX IF NOT EXISTS idx_reminders_topic_sent ON fazle_reminders_sent (topic, sent_at DESC);

-- ── fazle_scheduled_jobs ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fazle_scheduled_jobs (
    job_name            VARCHAR(80) NOT NULL PRIMARY KEY,
    last_run_at         TIMESTAMPTZ,
    last_status         VARCHAR(20),
    last_duration_ms    INTEGER,
    last_error          TEXT,
    next_run_at         TIMESTAMPTZ,
    run_count           INTEGER NOT NULL DEFAULT 0,
    meta_json           JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- ── fazle_report_runs ─────────────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS fazle_report_runs_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;

CREATE TABLE IF NOT EXISTS fazle_report_runs (
    id              BIGINT NOT NULL DEFAULT nextval('fazle_report_runs_id_seq') PRIMARY KEY,
    report_name     VARCHAR(60) NOT NULL,
    args_json       JSONB,
    requested_by    VARCHAR(80),
    status          VARCHAR(20) NOT NULL DEFAULT 'ok',
    duration_ms     INTEGER,
    row_count       INTEGER,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER SEQUENCE fazle_report_runs_id_seq OWNED BY fazle_report_runs.id;

CREATE INDEX IF NOT EXISTS idx_report_runs_name_at ON fazle_report_runs (report_name, created_at DESC);

-- ── fazle_unified_contacts ────────────────────────────────────────────────────
-- See migration header: this is the "active duplication" table the
-- governance report flagged (modules/contact_sync writes here AND to
-- wbom_contacts on every sync, with no DB-level consistency guarantee).
-- Backfilling its schema here does not resolve that duplication -- it only
-- brings the table, as it exists today, into the governed migration path.
-- Resolving the duplication itself is a separate decision, not in this
-- migration's scope (schema preservation only, per instruction).

CREATE TABLE IF NOT EXISTS fazle_unified_contacts (
    phone           TEXT NOT NULL PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT '',
    source_bridge   TEXT NOT NULL DEFAULT '',
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_fuc_name ON fazle_unified_contacts (display_name);

-- ── fazle_contact_sync_log ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fazle_contact_sync_log (
    bridge              TEXT NOT NULL PRIMARY KEY,
    synced_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    contacts_upserted   INTEGER NOT NULL DEFAULT 0
);
