"""
Fazle Core — Master Test Fixtures

Provides:
  - test_db:          asyncpg pool connected to isolated test schema
  - client:           httpx AsyncClient wrapping the FastAPI app
  - override_settings: inject test env without touching real .env
  - mock_bridge1/2:   respx mocks for bridge HTTP calls
  - mock_ollama:      respx mock for Ollama AI
  - seed_employee:    creates a real employee row in test DB
  - seed_escort_program: creates a real escort program row
  - sample payloads for every WhatsApp message type
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

# ── Early env var setup — must run BEFORE any app.config import ──────────────
# Module-level imports in test files (e.g. from modules.fazle_payroll_engine.routes)
# trigger get_settings() during collection, before the override_env fixture runs.
# Set safe defaults here so collection never fails with ValidationError.
_early_test_db_pass = os.getenv("TEST_DB_PASSWORD", "changeme-set-TEST_DB_PASSWORD")
os.environ.setdefault("DATABASE_URL", os.getenv("TEST_DATABASE_URL", f"postgresql://postgres:{_early_test_db_pass}@172.20.0.6:5432/fazle_test"))
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key-fixture-only")
os.environ.setdefault("AUTO_REPLY_ENABLED", "false")
os.environ.setdefault("RECRUITMENT_AUTOREPLY_ENABLED", "false")
os.environ.setdefault("OLLAMA_URL", "http://mock-ollama")
os.environ.setdefault("BRIDGE1_URL", "http://mock-bridge1")
os.environ.setdefault("BRIDGE2_URL", "http://mock-bridge2")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("ADMIN_NUMBERS", "8801700000001,8801700000002")
os.environ.setdefault("META_VERIFY_TOKEN", "test_verify_token")
os.environ.setdefault("META_APP_SECRET", "test_app_secret")
os.environ.setdefault("ACCOUNTANT_PHONE", "")
os.environ.setdefault("ESCORT_CLIENT_PHONES", "")
os.environ.setdefault("SOCIAL_AUTO_REPLY_SINGLE_ENGINE", "false")

import asyncpg
import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient, ASGITransport

# ── One-time test schema setup (sync, no event loop conflicts) ───────────────
# autouse=False: only runs for tests that explicitly request this fixture
# (or DB-dependent fixtures that chain to it). Pure unit tests are unaffected.
# ── Schema setup via pytest_configure hook ───────────────────────────────────
# Runs before any event loop or fixture is created, so asyncio.run() is safe.

_schema_setup_done = False


def pytest_configure(config):
    """Run schema setup before any tests or event loops start."""
    import sys

    global _schema_setup_done

    # ── HARD SAFETY GUARD: verify DB name before any DROP SCHEMA ──────────
    db_name = _extract_db_name(TEST_DB_URL)
    if db_name.lower() not in _SAFE_TEST_DB_NAMES:
        print(f"\n{'='*60}")
        print(f"FATAL: Refusing to DROP SCHEMA on database '{db_name}'.")
        print(f"  Only these databases are safe: {', '.join(sorted(_SAFE_TEST_DB_NAMES))}")
        print(f"  URL: {TEST_DB_URL}")
        print(f"{'='*60}\n")
        sys.exit(1)

    async def _run():
        pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await conn.execute(_BASE_SCHEMA_SQL)
            applied = await _run_all_migrations(conn)
            await conn.execute(_RUNTIME_SCHEMA_SQL)
            await conn.execute(_SEED_SQL)
            print(f"\n[test-schema] {len(applied)} migration files applied")
        await pool.close()

    try:
        asyncio.run(_run())
        _schema_setup_done = True
    except Exception as e:
        print(f"\n[test-schema] FATAL: schema setup failed: {e}")
        # Don't exit — let tests that don't need DB still run


@pytest.fixture(scope="session", autouse=False)
def _setup_test_schema(override_env):
    """Marker fixture — schema is already set up by pytest_configure.
    Chaining this fixture ensures override_env has run."""
    assert _schema_setup_done, "Schema setup failed in pytest_configure"
    return True


# ─────────────────────────────────────────────────────────────────────────────
# 1c. Base schema — pre-migration tables that NO migration creates
# ─────────────────────────────────────────────────────────────────────────────

_BASE_SCHEMA_SQL = """
-- WBOM core tables (pre-date all migrations)
CREATE TABLE IF NOT EXISTS wbom_employees (
    employee_id   SERIAL PRIMARY KEY,
    employee_mobile VARCHAR(20) UNIQUE NOT NULL,
    employee_name VARCHAR(100) NOT NULL,
    designation   VARCHAR(30) DEFAULT 'Security Guard',
    joining_date  DATE,
    status        VARCHAR(20) DEFAULT 'Active',
    basic_salary  DECIMAL(10,2) DEFAULT 0,
    bkash_number  VARCHAR(20),
    nagad_number  VARCHAR(20),
    nid_number    VARCHAR(20),
    bank_account  VARCHAR(50),
    emergency_contact VARCHAR(20),
    address       TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Matches db/migrations/029's real production schema (contact_name/
-- contact_type never existed in production -- this stub previously guessed
-- wrong column names, the same class of bug fixed in identity_brain's
-- _lookup_contact() on 2026-07-11; it shadows 029's CREATE TABLE IF NOT
-- EXISTS since this bootstrap runs first, so keeping it wrong here means
-- every test silently ran against a schema production never had).
CREATE TABLE IF NOT EXISTS wbom_contacts (
    contact_id      SERIAL PRIMARY KEY,
    whatsapp_number VARCHAR(50) NOT NULL,
    display_name    VARCHAR(200) NOT NULL DEFAULT '',
    company_name    VARCHAR(300),
    relation_type_id INT,
    business_type_id INT,
    is_active       BOOLEAN DEFAULT TRUE,
    relation        VARCHAR(100) DEFAULT 'unknown',
    platform        VARCHAR(20) DEFAULT 'whatsapp',
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wbom_escort_programs (
    program_id    SERIAL PRIMARY KEY,
    mother_vessel VARCHAR(100),
    lighter_vessel VARCHAR(100),
    master_mobile VARCHAR(20),
    destination   VARCHAR(100),
    cargo_type    VARCHAR(100),
    importer      VARCHAR(100),
    escort_name   VARCHAR(100),
    escort_employee_id INT REFERENCES wbom_employees(employee_id),
    escort_mobile VARCHAR(20),
    program_date  DATE,
    shift         VARCHAR(1) DEFAULT 'D',
    status        VARCHAR(20) DEFAULT 'Assigned',
    is_historical BOOLEAN DEFAULT FALSE,
    start_date    DATE,
    assignment_time TIMESTAMPTZ DEFAULT NOW(),
    end_date      DATE,
    end_shift     VARCHAR(1),
    release_point VARCHAR(100),
    day_count     FLOAT,
    conveyance    DECIMAL(10,2) DEFAULT 0,
    food_bill     DECIMAL(10,2) DEFAULT 0,
    capacity      VARCHAR(20),
    completion_time TIMESTAMPTZ,
    total_payment DECIMAL(10,2) DEFAULT 0,
    release_location VARCHAR(100),
    contact_id    INT REFERENCES wbom_contacts(contact_id),
    remarks       TEXT,
    whatsapp_message_id INT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wbom_attendance (
    attendance_id SERIAL PRIMARY KEY,
    employee_id   INT REFERENCES wbom_employees(employee_id) ON DELETE CASCADE,
    attendance_date DATE NOT NULL,
    status        VARCHAR(20) DEFAULT 'Present',
    location      VARCHAR(100),
    check_in_time TIMESTAMPTZ,
    check_out_time TIMESTAMPTZ,
    remarks       TEXT,
    recorded_by   VARCHAR(50),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (employee_id, attendance_date)
);

CREATE TABLE IF NOT EXISTS wbom_cash_transactions (
    transaction_id SERIAL PRIMARY KEY,
    employee_id   INT REFERENCES wbom_employees(employee_id) ON DELETE CASCADE,
    program_id    INT REFERENCES wbom_escort_programs(program_id),
    transaction_type VARCHAR(20) NOT NULL,
    amount        DECIMAL(10,2) NOT NULL,
    payment_method VARCHAR(10),
    payment_mobile VARCHAR(20),
    employee_phone TEXT,
    payment_number TEXT,
    transaction_date DATE DEFAULT CURRENT_DATE,
    transaction_time TIMESTAMPTZ DEFAULT NOW(),
    status        VARCHAR(20) DEFAULT 'Completed',
    reference_number VARCHAR(50),
    remarks       TEXT,
    created_by    VARCHAR(50),
    source        TEXT,
    idempotency_key TEXT,
    whatsapp_message_id VARCHAR(100),
    approved_by   TEXT,
    approved_at   TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_test_txn_idempotency
    ON wbom_cash_transactions (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS wbom_staging_payments (
    staging_id      SERIAL PRIMARY KEY,
    message_id      INT,
    sender_number   VARCHAR(20),
    extracted_name  VARCHAR(100),
    extracted_mobile VARCHAR(20),
    amount          DECIMAL(12,2),
    payment_method  VARCHAR(20),
    transaction_type VARCHAR(30),
    matched_employee_id INT REFERENCES wbom_employees(employee_id),
    name_match_ratio NUMERIC(4,2),
    status          VARCHAR(20) DEFAULT 'pending',
    approved_by     VARCHAR(100),
    approved_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT NOW(),
    final_transaction_id INT REFERENCES wbom_cash_transactions(transaction_id),
    idempotency_key VARCHAR(64)
);
CREATE INDEX IF NOT EXISTS idx_staging_payments_status
    ON wbom_staging_payments (status);

CREATE TABLE IF NOT EXISTS wbom_payroll_runs (
    run_id        SERIAL PRIMARY KEY,
    employee_id   INT REFERENCES wbom_employees(employee_id),
    period_year   INT,
    period_month  INT,
    status        VARCHAR(20) DEFAULT 'draft',
    basic_salary  DECIMAL(10,2) DEFAULT 0,
    total_programs INT DEFAULT 0,
    per_program_rate DECIMAL(10,2) DEFAULT 0,
    program_allowance DECIMAL(10,2) DEFAULT 0,
    other_allowance DECIMAL(10,2) DEFAULT 0,
    total_advances DECIMAL(10,2) DEFAULT 0,
    total_deductions DECIMAL(10,2) DEFAULT 0,
    gross_salary  DECIMAL(10,2) DEFAULT 0,
    net_salary    DECIMAL(10,2) DEFAULT 0,
    computed_by   VARCHAR(50),
    submitted_by  VARCHAR(50),
    approved_by   VARCHAR(50),
    locked_by     VARCHAR(50),
    paid_by       VARCHAR(50),
    paid_at       TIMESTAMPTZ,
    payment_method        VARCHAR(20),
    payment_reference     TEXT,
    payout_idempotency_key TEXT UNIQUE,
    correction_reason     TEXT,
    computed_at   TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wbom_payroll_run_items (
    item_id       SERIAL PRIMARY KEY,
    run_id        INT REFERENCES wbom_payroll_runs(run_id) ON DELETE CASCADE,
    component_type VARCHAR(50),
    component_label VARCHAR(200),
    amount        DECIMAL(10,2),
    sign          VARCHAR(1) DEFAULT '+',
    source_table  VARCHAR(50),
    source_id     INT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS wbom_payroll_approval_log (
    log_id        SERIAL PRIMARY KEY,
    run_id        INT REFERENCES wbom_payroll_runs(run_id),
    action        VARCHAR(50),
    from_status   VARCHAR(20),
    to_status     VARCHAR(20),
    actor         VARCHAR(50),
    reason        TEXT,
    note          TEXT,
    payload_json  JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wbom_salary_records (
    record_id     SERIAL PRIMARY KEY,
    employee_id   INT REFERENCES wbom_employees(employee_id),
    salary_month  VARCHAR(7),
    amount        DECIMAL(10,2),
    status        VARCHAR(20) DEFAULT 'Pending',
    payment_date  DATE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wbom_employee_requests (
    request_id    SERIAL PRIMARY KEY,
    employee_id   INT REFERENCES wbom_employees(employee_id),
    request_type  VARCHAR(30),
    amount        DECIMAL(10,2),
    status        VARCHAR(20) DEFAULT 'pending',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wbom_cases (
    case_id       SERIAL PRIMARY KEY,
    employee_id   INT REFERENCES wbom_employees(employee_id),
    case_type     VARCHAR(30),
    description   TEXT,
    status        VARCHAR(20) DEFAULT 'open',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS wbom_whatsapp_messages (
    message_id          SERIAL PRIMARY KEY,
    sender_number       TEXT,
    message_body        TEXT,
    message_type        TEXT DEFAULT 'text',
    direction           TEXT,
    platform            TEXT,
    is_processed        BOOLEAN DEFAULT TRUE,
    contact_identifier  TEXT,
    contact_id          INT,
    identity_role       TEXT,
    identity_confidence FLOAT DEFAULT 0,
    workflow_triggered  TEXT,
    received_at         TIMESTAMPTZ DEFAULT NOW(),
    metadata_json       JSONB,
    canonical_phone     TEXT,
    phone_last10        TEXT,
    source_message_ref  TEXT,
    source_timestamp    TIMESTAMPTZ,
    source_context      TEXT,
    message_hash        TEXT UNIQUE,
    critical_contact    BOOLEAN DEFAULT FALSE,
    original_sender_number TEXT,
    critical_log_path   TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- fazle_contact_roles (pre-migration: migration 004 ALTERs it, but we pre-add
-- the columns so they survive even if migration 004's BEGIN/COMMIT transaction
-- rolls back due to seed INSERT failures)
CREATE TABLE IF NOT EXISTS fazle_contact_roles (
    id            SERIAL PRIMARY KEY,
    phone         TEXT NOT NULL,
    name          TEXT,
    role          TEXT,
    label         TEXT,
    platform      TEXT DEFAULT 'whatsapp',
    confidence    INTEGER NOT NULL DEFAULT 0,
    source        TEXT    NOT NULL DEFAULT 'seed_rule',
    priority      INTEGER NOT NULL DEFAULT 50,
    notes         TEXT    NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (phone),
    UNIQUE (phone, platform)
);

-- Admin tables (pre-migration)
CREATE TABLE IF NOT EXISTS fazle_roles (
    id            SERIAL PRIMARY KEY,
    name          TEXT UNIQUE,
    level         INT,
    description   TEXT
);

CREATE TABLE IF NOT EXISTS fazle_admins (
    id            SERIAL PRIMARY KEY,
    phone         TEXT UNIQUE,
    name          TEXT,
    username      TEXT,
    status        TEXT DEFAULT 'active',
    notes         TEXT,
    api_key_hash  TEXT,
    password_hash TEXT,
    login_token_hash TEXT,
    last_seen_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fazle_admin_roles (
    id            SERIAL PRIMARY KEY,
    admin_id      INT REFERENCES fazle_admins(id) ON DELETE CASCADE,
    role_name     TEXT REFERENCES fazle_roles(name),
    granted_by    TEXT,
    UNIQUE (admin_id, role_name)
);

CREATE TABLE IF NOT EXISTS fazle_admin_audit (
    id                SERIAL PRIMARY KEY,
    actor_phone       TEXT,
    actor_user_id     INT,
    actor_label       TEXT,
    channel           TEXT,
    command           TEXT,
    args              TEXT,
    allowed           BOOLEAN,
    required_role     TEXT,
    denied_reason     TEXT,
    result_summary    TEXT,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Misc runtime tables that pre-date migrations
CREATE TABLE IF NOT EXISTS fazle_report_cache (
    cache_key     TEXT PRIMARY KEY,
    payload       JSONB,
    expires_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS fazle_service_heartbeats (
    service_name      TEXT PRIMARY KEY,
    last_seen         TIMESTAMPTZ DEFAULT NOW(),
    last_message_id   TEXT,
    queue_depth       INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fazle_db_backups (
    id            BIGSERIAL PRIMARY KEY,
    filename      VARCHAR(200),
    path          TEXT,
    size_bytes    BIGINT,
    sha256        VARCHAR(64),
    status        VARCHAR(20) DEFAULT 'ok',
    duration_ms   INT,
    error         TEXT,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    rotated_at    TIMESTAMPTZ
);

-- escort_roster_entries (referenced by migration 008 FK)
CREATE TABLE IF NOT EXISTS escort_roster_entries (
    id SERIAL PRIMARY KEY,
    program_id INT UNIQUE NOT NULL,
    mother_vessel TEXT, lighter_vessel TEXT, master_mobile TEXT,
    escort_name TEXT, escort_mobile TEXT, destination TEXT,
    start_date DATE, start_shift CHAR(1), end_date DATE, end_shift CHAR(1),
    total_shifts INT, total_days NUMERIC(6,2), salary NUMERIC(10,2),
    conveyance NUMERIC(10,2) DEFAULT 0, food_bill NUMERIC(10,2) DEFAULT 0,
    advance_deduction NUMERIC(12,2) DEFAULT 0, net_payable NUMERIC(12,2) DEFAULT 0,
    total NUMERIC(12,2), release_point TEXT, roster_status TEXT DEFAULT 'draft',
    calc_version INT DEFAULT 1, last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(), updated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


# ─────────────────────────────────────────────────────────────────────────────
# 1d. Migration runner — discovers and applies all production migration SQL files
# ─────────────────────────────────────────────────────────────────────────────

def _discover_migration_files() -> list[tuple[str, str]]:
    """Return sorted list of (label, filepath) for all migration SQL files."""
    from pathlib import Path
    base = Path(__file__).resolve().parent.parent  # core/
    dirs = [
        base / "db" / "migrations",
        base / "modules" / "fazle_payroll_engine" / "migrations",
    ]
    files: list[tuple[str, str]] = []
    for d in dirs:
        if d.is_dir():
            for f in sorted(d.glob("*.sql")):
                files.append((f.name, str(f)))
    files.sort(key=lambda x: x[0])  # deterministic sorted order by filename
    return files


async def _run_all_migrations(conn: asyncpg.Connection) -> list[str]:
    """Execute all migration SQL files in sorted order.

    Files containing BEGIN/COMMIT are executed directly (PostgreSQL handles
    the inner transaction). Files without BEGIN/COMMIT are wrapped in a
    transaction that rolls back on failure so one bad file doesn't poison
    the connection for subsequent ones.
    """
    import logging
    import re
    log = logging.getLogger("fazle.test_schema")
    applied: list[str] = []
    files = _discover_migration_files()

    for label, filepath in files:
        with open(filepath, "r") as f:
            sql_text = f.read()
        # Strip BEGIN/COMMIT so statements run independently
        sql_text = re.sub(r"^\s*BEGIN\s*;", "", sql_text, flags=re.IGNORECASE | re.MULTILINE)
        sql_text = re.sub(r"^\s*COMMIT\s*;", "", sql_text, flags=re.IGNORECASE | re.MULTILINE)
        # Split into individual statements with proper handling of:
        # - dollar-quoted strings ($$ ... $$ or $tag$ ... $tag$)
        # - single-quoted strings ('...;...')
        # - parenthesis depth (semicolons inside CREATE TABLE)
        # - line comments (-- ...)
        statements = _split_sql_safe(sql_text)
        errors = 0
        for stmt in statements:
            s = stmt.strip()
            if not s:
                continue
            # Skip purely comment-only statements (every line starts with --)
            lines = [l.strip() for l in s.split("\n") if l.strip()]
            if all(l.startswith("--") for l in lines):
                continue
            try:
                async with conn.transaction():
                    await conn.execute(stmt)
            except Exception as e:
                err_msg = str(e)[:150]
                if any(kw in err_msg.lower() for kw in (
                    "already exists", "does not exist", "duplicate",
                    "multiple primary key", "transaction is aborted",
                )):
                    pass  # idempotent — already applied
                else:
                    errors += 1
                    log.warning("[migration:%s] stmt failed: %s", label, err_msg)
        applied.append(label)
        if errors:
            log.warning("[migration:%s] %d errors (tolerated)", label, errors)
    return applied


# ─────────────────────────────────────────────────────────────────────────────
# 1e. Runtime schema — tables created by app code at startup, not in migrations
# ─────────────────────────────────────────────────────────────────────────────

_RUNTIME_SCHEMA_SQL = """
-- Post-migration fixup: add columns that migrations fail to add due to
-- file corruption in migration 010 or BEGIN/COMMIT transaction rollback issues.
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'whatsapp';
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS source_channel TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS source_message_id TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS employee_id_phone TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS employee_phone TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS program_id BIGINT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS original_payload JSONB;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS transaction_status TEXT NOT NULL DEFAULT 'final';
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS approval_status TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS approved_by TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS review_status TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS submitted_by TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS deleted_by TEXT;
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE fpe_cash_transactions ADD COLUMN IF NOT EXISTS legacy_wbom_transaction_id BIGINT;

-- Bridge poller dedup tables (created at runtime by bridge_poller.init_tables)
CREATE TABLE IF NOT EXISTS bridge_poller_cursor (
    bridge      TEXT PRIMARY KEY,
    last_ts     TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_bridge_messages (
    message_id  TEXT    NOT NULL,
    bridge      TEXT    NOT NULL,
    phone       TEXT,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (message_id, bridge)
);
CREATE INDEX IF NOT EXISTS idx_pbm_bridge_ts
    ON processed_bridge_messages (bridge, processed_at DESC);
CREATE TABLE IF NOT EXISTS processed_outgoing_escort_messages (
    message_id   TEXT    NOT NULL,
    bridge       TEXT    NOT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (message_id, bridge)
);
CREATE TABLE IF NOT EXISTS outbound_safety_incidents (
    id              BIGSERIAL   PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recipient       TEXT        NOT NULL,
    bridge          TEXT        NOT NULL,
    blocked_reason  TEXT        NOT NULL,
    message_preview TEXT,
    queue_id        TEXT,
    source_module   TEXT        NOT NULL DEFAULT 'bridge_poller'
);
CREATE INDEX IF NOT EXISTS idx_osi_ts
    ON outbound_safety_incidents (ts DESC);
CREATE INDEX IF NOT EXISTS idx_osi_recipient
    ON outbound_safety_incidents (recipient, ts DESC);

-- Outbound queue (used by modules.outbound.enqueue)
CREATE TABLE IF NOT EXISTS fazle_outbound_queue (
    id              BIGSERIAL   PRIMARY KEY,
    recipient       TEXT        NOT NULL,
    body            TEXT        NOT NULL,
    source_bridge   TEXT        NOT NULL DEFAULT 'bridge2',
    fallback_channel TEXT,
    purpose         TEXT,
    idempotency_key TEXT UNIQUE,
    meta_json       JSONB       DEFAULT '{}'::jsonb,
    status          TEXT        NOT NULL DEFAULT 'pending',
    attempts        INT         NOT NULL DEFAULT 0,
    max_attempts    INT         NOT NULL DEFAULT 5,
    next_retry_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_at       TIMESTAMPTZ,
    sent_at         TIMESTAMPTZ,
    external_id     TEXT,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- LLM conversation log (fire-and-forget inserts from ollama.py)
CREATE TABLE IF NOT EXISTS llm_conversation_log (
    id          BIGSERIAL PRIMARY KEY,
    caller      TEXT,
    provider    TEXT,
    model       TEXT,
    messages    JSONB,
    reply       TEXT,
    latency_ms  INT,
    is_fallback BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Error log (used by app/error_log.py with UPSERT semantics)
CREATE TABLE IF NOT EXISTS fazle_error_log (
    module      TEXT,
    error_type  TEXT,
    message     TEXT,
    count       INT NOT NULL DEFAULT 1,
    first_seen  TIMESTAMPTZ DEFAULT NOW(),
    last_seen   TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_error_log
    ON fazle_error_log (module, error_type, md5(COALESCE(message, '')));
"""


# ─────────────────────────────────────────────────────────────────────────────
# 1f. Minimal seed data
# ─────────────────────────────────────────────────────────────────────────────

_SEED_SQL = """
INSERT INTO fazle_roles (name, level, description) VALUES
  ('viewer',     1, 'Read-only'),
  ('operator',   2, 'Approve drafts, process payments'),
  ('accountant', 3, 'Payroll and payment operations'),
  ('admin',      4, 'System management'),
  ('superadmin', 5, 'Full access')
ON CONFLICT (name) DO NOTHING;
"""


# ─────────────────────────────────────────────────────────────────────────────
# 1. Environment / Settings override
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# 1b. Hard safety guard — prevent accidental production DB destruction
# ─────────────────────────────────────────────────────────────────────────────

_FORBIDDEN_DB_NAMES = frozenset({
    "postgres", "fazle_core", "fazle", "core", "live", "production", "prod",
    "fazle_live", "fazle_prod", "whatsapp", "fazle_whatsapp",
})

_SAFE_TEST_DB_NAMES = frozenset({
    "fazle_test", "test", "fazle_test_db",
})


def _extract_db_name(url: str) -> str:
    """Extract the database name from a PostgreSQL URL."""
    if not url:
        return ""
    # Handle postgresql://user:pass@host:port/dbname?params
    import re
    m = re.search(r"/([^/?]+)(?:\?|$)", url)
    if m:
        return m.group(1)
    return ""


def _validate_test_db_url(url: str) -> str:
    """Validate that the DB URL points only to a safe test database.
    Fatal exit if it points to production."""
    import sys

    db_name = _extract_db_name(url)
    if not db_name:
        print(f"\n{'='*60}")
        print("FATAL: Could not extract database name from TEST_DB_URL.")
        print(f"  URL: {url}")
        print("Tests aborted to prevent potential data loss.")
        print(f"{'='*60}\n")
        sys.exit(1)

    if db_name.lower() in _FORBIDDEN_DB_NAMES:
        print(f"\n{'='*60}")
        print(f"FATAL: TEST_DB_URL points to '{db_name}' — a production database!")
        print(f"  URL: {url}")
        print("DROP SCHEMA would destroy production data.")
        print("Set TEST_DATABASE_URL to a fazle_test database.")
        print(f"{'='*60}\n")
        sys.exit(1)

    if db_name.lower() not in _SAFE_TEST_DB_NAMES:
        print(f"\n{'='*60}")
        print(f"WARNING: TEST_DB_URL database '{db_name}' is not in the known safe list.")
        print(f"  Allowed: {', '.join(sorted(_SAFE_TEST_DB_NAMES))}")
        print(f"  URL: {url}")
        print(f"{'='*60}\n")

    return url


# TEST_DB_URL: must be set explicitly via TEST_DATABASE_URL env var.
# If not set, construct from safe components: Docker host + fazle_test DB.
# The password may be provided via TEST_DB_PASSWORD env var (CI-friendly).
# NEVER read the full production DATABASE_URL from .env files.
_TEST_DB_HOST = os.getenv("TEST_DB_HOST", "172.20.0.6")
_TEST_DB_PORT = os.getenv("TEST_DB_PORT", "5432")
_TEST_DB_USER = os.getenv("TEST_DB_USER", "postgres")
_TEST_DB_PASS = os.getenv("TEST_DB_PASSWORD", "changeme-set-TEST_DB_PASSWORD")
_TEST_DB_NAME = "fazle_test"  # hardcoded — never overridable
TEST_DB_URL = os.getenv("TEST_DATABASE_URL") or f"postgresql://{_TEST_DB_USER}:{_TEST_DB_PASS}@{_TEST_DB_HOST}:{_TEST_DB_PORT}/{_TEST_DB_NAME}"
_validate_test_db_url(TEST_DB_URL)
TEST_API_KEY = "test-internal-key-fixture-only"


@pytest.fixture(scope="session", autouse=True)
def override_env(tmp_path_factory):
    """Patch environment before anything imports app.config."""
    env_overrides = {
        "DATABASE_URL": TEST_DB_URL,
        "INTERNAL_API_KEY": TEST_API_KEY,
        "AUTO_REPLY_ENABLED": "false",
        "RECRUITMENT_AUTOREPLY_ENABLED": "false",
        "BRIDGE1_URL": "http://mock-bridge1",
        "BRIDGE2_URL": "http://mock-bridge2",
        "OLLAMA_URL": "http://mock-ollama",
        "REDIS_URL": "redis://localhost:6379/15",   # isolated DB 15 for tests
        "ADMIN_NUMBERS": "8801700000001,8801700000002",
        "META_VERIFY_TOKEN": "test_verify_token",
        "META_APP_SECRET": "test_app_secret",
        "ACCOUNTANT_PHONE": "",
        "ESCORT_CLIENT_PHONES": "",
        "SOCIAL_AUTO_REPLY_SINGLE_ENGINE": "false",
    }
    with patch.dict(os.environ, env_overrides):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# 2. Database fixtures
# ─────────────────────────────────────────────────────────────────────────────

# (Schema is now built from production migrations — see _setup_test_schema above.)

# ── Dynamic TRUNCATE list ─────────────────────────────────────────────────────
# Instead of a hardcoded table list, we discover all tables at teardown time
# so we never miss a table created by a new migration.

async def _truncate_all_tables(conn: asyncpg.Connection) -> None:
    """Truncate all tables in the public schema, RESTART IDENTITY CASCADE.
    Skips fazle_roles (seed data must survive between tests)."""
    rows = await conn.fetch("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename != 'fazle_roles'
        ORDER BY tablename
    """)
    if not rows:
        return
    table_names = [r["tablename"] for r in rows]
    # Build a single TRUNCATE statement for all tables
    safe_names = ", ".join(f'"{t}"' for t in table_names)
    try:
        await conn.execute(f"TRUNCATE TABLE {safe_names} RESTART IDENTITY CASCADE")
    except Exception:
        pass  # non-fatal — some tables may have FK cycles


@pytest_asyncio.fixture
async def test_db_pool(_setup_test_schema) -> AsyncGenerator[asyncpg.Pool, None]:
    """Per-test asyncpg pool. Chains _setup_test_schema so the schema is
    created/wiped once per session before any DB test runs. Truncates all
    data tables in teardown so each test starts with clean state. Pure
    (no-DB) unit tests never request test_db_pool so they are unaffected."""
    pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=3)
    yield pool
    try:
        async with pool.acquire() as conn:
            await _truncate_all_tables(conn)
    finally:
        await pool.close()
        # Many tests point the shared app.database._pool singleton at this
        # fixture's pool directly (`db_module._pool = test_db_pool`), bypassing
        # dependency injection. If we don't clear that reference here, the next
        # test in the session that relies on app.database's global pool (e.g.
        # any ASGI call through app.main.app that never re-assigns it) inherits
        # a closed-pool reference and fails with "pool is closed" — regardless
        # of whether *this* test passed, failed, or errored. Only clear it if
        # it's still pointing at the pool we just closed, so we never stomp on
        # a different pool some other fixture may have set up in the meantime.
        import app.database as db_module
        if db_module._pool is pool:
            db_module._pool = None


@pytest_asyncio.fixture
async def clean_tables(test_db_pool):
    """Truncate data tables before each test. Keeps schema and seed data."""
    yield
    async with test_db_pool.acquire() as conn:
        await _truncate_all_tables(conn)


# ─────────────────────────────────────────────────────────────────────────────
# 3. FastAPI test client
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(test_db_pool) -> AsyncGenerator[AsyncClient, None]:
    """
    httpx AsyncClient backed by the real FastAPI app.
    DB pool is injected by patching app.database._pool.
    """
    import app.database as db_module
    db_module._pool = test_db_pool

    # Reset ollama_daemon's persistent client so RESPX mocks can intercept.
    try:
        from app import ollama_daemon as _od
        await _od.stop()
    except Exception:
        pass

    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app),
        base_url="http://testserver",
        headers={"X-Internal-Key": TEST_API_KEY},
    ) as ac:
        yield ac

    # Clean up ollama_daemon client after test.
    try:
        from app import ollama_daemon as _od
        await _od.stop()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_admin_command_dedup_cache():
    """Clear modules.admin_commands' in-process dedup LRU before each test.

    That cache is a module-level OrderedDict keyed by sha1(admin_phone+text)
    with a 30s TTL — correct for production (single-worker duplicate-send
    suppression) but not test-isolated: two tests in the same pytest process
    that send the same admin command text within 30s of each other collide
    and the second is silently treated as a duplicate.
    """
    from modules.admin_commands import _dedup_cache
    _dedup_cache.clear()
    yield
    _dedup_cache.clear()


@pytest.fixture
def auth_headers() -> dict:
    return {"X-Internal-Key": TEST_API_KEY}


@pytest.fixture
def no_auth_headers() -> dict:
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# 4. Mock external services (respx)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_bridge1():
    """Mock Bridge1 HTTP calls."""
    with respx.mock(base_url="http://mock-bridge1", assert_all_called=False) as mock:
        mock.post("/send").mock(return_value=_bridge_send_ok())
        mock.get("/health").mock(return_value=_bridge_health_ok("bridge1"))
        mock.get("/messages").mock(return_value=_bridge_messages_empty())
        yield mock


@pytest.fixture
def mock_bridge2():
    """Mock Bridge2 HTTP calls."""
    with respx.mock(base_url="http://mock-bridge2", assert_all_called=False) as mock:
        mock.post("/send").mock(return_value=_bridge_send_ok())
        mock.get("/health").mock(return_value=_bridge_health_ok("bridge2"))
        mock.get("/messages").mock(return_value=_bridge_messages_empty())
        yield mock


@pytest.fixture
def mock_ollama():
    """Mock Ollama LLM call by patching ollama_daemon.post_generate directly.

    RESPX transport interception doesn't work reliably with ollama_daemon's
    persistent httpx.AsyncClient, so we patch at the function level instead.
    Classify calls return a valid category; generate calls return a Bengali reply.
    """
    from app import ollama_daemon
    original = ollama_daemon.post_generate

    async def _mock_post_generate(payload, timeout=120.0):
        import httpx
        prompt = (payload or {}).get("prompt", "")
        # Classify calls expect a category name; generate calls expect a reply.
        if "Categories:" in prompt and "Category:" in prompt:
            return httpx.Response(200, json={"response": "greeting", "done": True})
        return _ollama_response("ধন্যবাদ।")

    ollama_daemon.post_generate = _mock_post_generate
    try:
        yield _mock_post_generate
    finally:
        ollama_daemon.post_generate = original


@pytest.fixture
def mock_all_services(mock_bridge1, mock_bridge2, mock_ollama):
    """Convenience: all external mocks active."""
    yield {"bridge1": mock_bridge1, "bridge2": mock_bridge2, "ollama": mock_ollama}


# ── Helper response builders ───────────────────────────────────────────────────

def _bridge_send_ok():
    from httpx import Response
    return Response(200, json={"status": "ok", "message_id": "test-msg-001"})


def _bridge_health_ok(name: str):
    from httpx import Response
    return Response(200, json={"status": "ok", "bridge": name, "connected": True})


def _bridge_messages_empty():
    from httpx import Response
    return Response(200, json={"messages": []})


def _bridge_send_fail():
    from httpx import Response
    return Response(503, json={"error": "bridge unavailable"})


def _ollama_response(text: str):
    from httpx import Response
    return Response(200, json={"response": text, "done": True})


# ─────────────────────────────────────────────────────────────────────────────
# 5. DB seed fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def seed_employee(test_db_pool) -> dict:
    """Insert one employee row and return it."""
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees
                (employee_mobile, employee_name, designation, status,
                 basic_salary, bkash_number, nagad_number)
            VALUES
                ('8801811111111', 'Test Guard Karim', 'Security Guard', 'Active',
                 9000.00, '01811111111', '01811111111')
            RETURNING *
        """)
    return dict(row)


@pytest_asyncio.fixture
async def seed_employee2(test_db_pool) -> dict:
    """A second employee for multi-employee tests."""
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees
                (employee_mobile, employee_name, designation, status,
                 basic_salary, bkash_number)
            VALUES
                ('8801822222222', 'Test Guard Rahim', 'Escort', 'Active',
                 10000.00, '01822222222')
            RETURNING *
        """)
    return dict(row)


@pytest_asyncio.fixture
async def seed_escort_program(test_db_pool, seed_employee) -> dict:
    """Insert one escort program row and return it."""
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_escort_programs
                (mother_vessel, lighter_vessel, master_mobile,
                 escort_employee_id, escort_mobile,
                 program_date, shift, status, start_date)
            VALUES
                ('MV TEST VESSEL', 'LT TEST LIGHTER', '8801933333333',
                 $1, $2,
                 '2026-05-01', 'D', 'running', '2026-05-01')
            RETURNING *
        """, seed_employee["employee_id"], seed_employee["employee_mobile"])
    return dict(row)


@pytest_asyncio.fixture
async def seed_admin(test_db_pool) -> dict:
    """Insert a superadmin in fazle_admins."""
    async with test_db_pool.acquire() as conn:
        admin_row = await conn.fetchrow("""
            INSERT INTO fazle_admins (phone, name, username, status)
            VALUES ('8801700000001', 'Test Admin', 'testadmin', 'active')
            RETURNING *
        """)
        role = await conn.fetchrow(
            "SELECT id FROM fazle_roles WHERE name='superadmin'"
        )
        await conn.execute("""
            INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by)
            VALUES ($1, 'superadmin', 'system') ON CONFLICT DO NOTHING
        """, admin_row["id"])
    return dict(admin_row)


@pytest_asyncio.fixture
async def seed_accountant(test_db_pool) -> dict:
    """Seed an accountant contact role entry."""
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO fazle_contact_roles (phone, role, label, source)
            VALUES ('8801944444444', 'accountant', 'Test Accountant', 'seed')
            RETURNING *
        """)
    return dict(row)


@pytest_asyncio.fixture
async def seed_payment_draft(test_db_pool, seed_employee, seed_escort_program) -> dict:
    """A pending payment draft for use in payment workflow tests."""
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO fazle_payment_drafts
                (draft_type, employee_id, employee_name, employee_mobile,
                 escort_program_id, duty_days, expected_amount,
                 payment_method, payout_mobile, status, source,
                 draft_text)
            VALUES
                ('escort_payment', $1, $2, $3,
                 $4, 5.0, 1500.00,
                 'bkash', '01811111111', 'pending', 'bridge1',
                 '💼 Test payment draft')
            RETURNING *
        """, seed_employee["employee_id"], seed_employee["employee_name"],
            seed_employee["employee_mobile"],
            seed_escort_program["program_id"])
    return dict(row)


@pytest_asyncio.fixture
async def seed_fpe_employee(test_db_pool, seed_employee) -> dict:
    """Insert an fpe_employees row matching the seeded wbom_employees row."""
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO fpe_employees
                (employee_code, full_name, name_normalized, primary_phone,
                 employee_id_phone, status, canonical_employee_id)
            VALUES
                ($1, $2, $3, $4, $5, 'active', $6)
            RETURNING *
        """, f"EMP{seed_employee['employee_id']}",
           seed_employee['employee_name'],
           seed_employee['employee_name'].lower().strip(),
           '01811111111',
           '01811111111',
           seed_employee['employee_id'])
    return dict(row)


@pytest_asyncio.fixture
async def seed_sprint3b_draft(test_db_pool, seed_employee) -> dict:
    """A pending Sprint-3B payment draft (from Sprint-3A employee conversation)."""
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO fazle_payment_drafts
                (draft_type, employee_id, employee_name, employee_mobile,
                 payout_mobile, purpose, expected_amount, payment_method,
                 status, source, draft_text, source_message,
                 draft_created_by, conversation_id, expires_at)
            VALUES
                ('advance', $1, $2, $3,
                 $3, 'advance', 2000.00, 'bkash',
                 'pending', 'bridge1', 'Test advance draft',
                 'ভাই অগ্রিম টাকা দরকার ২০০০ টাকা',
                 'ai_conversation', 'conv-test-001',
                 NOW() + INTERVAL '24 hours')
            RETURNING *
        """, seed_employee['employee_id'], seed_employee['employee_name'],
           seed_employee['employee_mobile'])
    return dict(row)


# ─────────────────────────────────────────────────────────────────────────────
# 6. WhatsApp payload builders
# ─────────────────────────────────────────────────────────────────────────────

def make_bridge_payload(sender: str, text: str, source: str = "bridge1") -> dict:
    """Minimal bridge webhook payload."""
    return {
        "sender": sender,
        "message": text,
        "timestamp": "2026-05-06T10:00:00Z",
        "message_id": f"test-{hash(text) % 100000}",
        "source": source,
    }


def make_meta_payload(sender: str, text: str) -> dict:
    """Meta WhatsApp Cloud API webhook payload."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "TEST_ENTRY_ID",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {"phone_number_id": "TEST_PHONE_ID"},
                    "contacts": [{"wa_id": sender, "profile": {"name": "Test User"}}],
                    "messages": [{
                        "id": "wamid.test001",
                        "from": sender,
                        "timestamp": "1746518400",
                        "type": "text",
                        "text": {"body": text},
                    }],
                },
                "field": "messages",
            }],
        }],
    }


# ── Pre-built payload constants ────────────────────────────────────────────────

GUARD_PHONE = "8801811111111"
ADMIN_PHONE = "8801700000001"
ACCOUNTANT_PHONE = "8801944444444"
CLIENT_PHONE = "8801955555555"
CANDIDATE_PHONE = "8801966666666"
UNKNOWN_PHONE = "8801977777777"

PAYLOAD_ESCORT_ORDER = make_bridge_payload(
    CLIENT_PHONE,
    "MV GOLDEN STAR lighter vessel NAJMA-3 master mobile 01933333333 "
    "wheat 5000MT escort lagbe 06/05/2026 Day shift",
)

PAYLOAD_GUARD_RELEASE = make_bridge_payload(
    GUARD_PHONE,
    "ডিউটি শেষ রিলিজ হয়েছি",
)

PAYLOAD_GUARD_ATTENDANCE = make_bridge_payload(
    GUARD_PHONE,
    "হাজির আছি MV GOLDEN STAR এ",
)

PAYLOAD_ADVANCE_REQUEST = make_bridge_payload(
    GUARD_PHONE,
    "ভাই অগ্রিম টাকা দরকার ২০০০ টাকা",
)

PAYLOAD_CANDIDATE_INQUIRY = make_bridge_payload(
    CANDIDATE_PHONE,
    "চাকরি করতে চাই নিরাপত্তা প্রহরী পদে",
)

PAYLOAD_ADMIN_APPROVE = make_bridge_payload(
    ADMIN_PHONE,
    "APPROVE 1",
    source="bridge2",
)

PAYLOAD_ADMIN_REJECT = make_bridge_payload(
    ADMIN_PHONE,
    "REJECT 1",
    source="bridge2",
)

def _split_sql_safe(sql_text: str) -> list[str]:
    """Split SQL into individual statements, properly handling:
    - Dollar-quoted strings ($$ ... $$ or $tag$ ... $tag$)
    - Single-quoted strings ('...;...')
    - Parenthesis depth (semicolons inside CREATE TABLE)
    - Line comments (-- ...)
    """
    statements: list[str] = []
    current: list[str] = []
    paren_depth = 0
    i = 0
    n = len(sql_text)
    in_single_quote = False
    in_dollar_quote = False
    dollar_tag = ""
    
    while i < n:
        ch = sql_text[i]

        # Check for line comment (-- ...) — consume verbatim through end of
        # line so semicolons inside human-readable comment text (extremely
        # common in pg_dump output, e.g. "-- Name: x; Type: TABLE; ...") never
        # get treated as statement boundaries.
        if not in_single_quote and not in_dollar_quote and ch == "-" and i + 1 < n and sql_text[i + 1] == "-":
            eol = sql_text.find("\n", i)
            eol = n if eol == -1 else eol + 1
            current.append(sql_text[i:eol])
            i = eol
            continue

        # Check for dollar-quoted string start/end
        if not in_single_quote and ch == "$":
            # Try to match a dollar quote tag
            j = i + 1
            while j < n and (sql_text[j].isalnum() or sql_text[j] == "_"):
                j += 1
            if j < n and sql_text[j] == "$":
                tag = sql_text[i:j+1]  # $tag$ or $$
                if in_dollar_quote and tag == dollar_tag:
                    in_dollar_quote = False
                    dollar_tag = ""
                    current.append(sql_text[i:j+1])
                    i = j + 1
                    continue
                elif not in_dollar_quote:
                    in_dollar_quote = True
                    dollar_tag = tag
                    current.append(sql_text[i:j+1])
                    i = j + 1
                    continue
        
        if in_dollar_quote:
            current.append(ch)
            i += 1
            continue
        
        # Check for single-quoted string
        if ch == "'" and not in_dollar_quote:
            if in_single_quote:
                # Check for escaped quote ('')
                if i + 1 < n and sql_text[i+1] == "'":
                    current.append("''")
                    i += 2
                    continue
                else:
                    in_single_quote = False
            else:
                in_single_quote = True
            current.append(ch)
            i += 1
            continue
        
        if in_single_quote:
            current.append(ch)
            i += 1
            continue
        
        # Track parenthesis depth
        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        
        # Check for statement end (semicolon at paren_depth 0)
        if ch == ";" and paren_depth == 0:
            current.append(ch)
            statements.append("".join(current))
            current = []
            i += 1
            continue
        
        current.append(ch)
        i += 1
    
    if current:
        statements.append("".join(current))
    return statements
