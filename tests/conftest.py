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

import asyncpg
import pytest
import pytest_asyncio
import respx
from httpx import AsyncClient, ASGITransport

# ── One-time test schema setup (sync, no event loop conflicts) ───────────────
# autouse=False: only runs for tests that explicitly request this fixture
# (or DB-dependent fixtures that chain to it). Pure unit tests are unaffected.
@pytest.fixture(scope="session", autouse=False)
def _setup_test_schema(override_env):
    """Drop and recreate the public schema once per session (sync wrapper)."""
    async def _run():
        pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=2)
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            await conn.execute(_SCHEMA_SQL)
        await pool.close()
    asyncio.run(_run())


# ─────────────────────────────────────────────────────────────────────────────
# 1. Environment / Settings override
# ─────────────────────────────────────────────────────────────────────────────

def _default_test_db_url() -> str:
    """Derive test DB URL from runtime-services.env (prod creds, fazle_test DB).
    CI can override via TEST_DATABASE_URL env var."""
    import subprocess
    try:
        prod = subprocess.check_output(
            ["bash", "-c",
             "grep '^DATABASE_URL=' /home/azim/secure-env-backup/runtime-services.env"
             " | cut -d= -f2-"],
            text=True,
        ).strip()
        return prod.rsplit("/", 1)[0] + "/fazle_test"
    except Exception:
        return "postgresql://postgres:postgres@172.20.0.3:5432/fazle_test"


TEST_DB_URL = os.getenv("TEST_DATABASE_URL") or _default_test_db_url()
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
    }
    with patch.dict(os.environ, env_overrides):
        yield


# ─────────────────────────────────────────────────────────────────────────────
# 2. Database fixtures
# ─────────────────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
-- Minimal schema for test DB (mirrors production structure)
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

CREATE TABLE IF NOT EXISTS wbom_contacts (
    contact_id    SERIAL PRIMARY KEY,
    contact_phone VARCHAR(20) UNIQUE,
    whatsapp_number VARCHAR(30),
    contact_name  VARCHAR(100),
    contact_type  VARCHAR(30),
    created_at    TIMESTAMPTZ DEFAULT NOW()
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
    reversal_of   INT REFERENCES wbom_cash_transactions(transaction_id),
    is_reversal   BOOLEAN DEFAULT FALSE,
    is_reversed   BOOLEAN DEFAULT FALSE,
    correction_note TEXT,
    reversal_reason TEXT,
    source        TEXT,
    idempotency_key TEXT,
    whatsapp_message_id INT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_test_txn_idempotency
    ON wbom_cash_transactions (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS wbom_staging_payments (
    staging_id      SERIAL PRIMARY KEY,
    message_id      INT,
    sender_number   TEXT,
    extracted_name  TEXT,
    extracted_mobile TEXT,
    amount          DECIMAL(10,2),
    payment_method  VARCHAR(10),
    transaction_type VARCHAR(20) DEFAULT 'received',
    matched_employee_id INT REFERENCES wbom_employees(employee_id),
    name_match_ratio FLOAT,
    status          VARCHAR(20) DEFAULT 'pending',
    idempotency_key TEXT UNIQUE,
    final_transaction_id INT,
    approved_by     TEXT,
    approved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS fazle_contact_roles (
    id            SERIAL PRIMARY KEY,
    phone         TEXT UNIQUE,
    role          TEXT,
    label         TEXT,
    confidence    FLOAT DEFAULT 1.0,
    source        TEXT DEFAULT 'seed',
    priority      INT DEFAULT 0,
    notes         TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fazle_draft_replies (
    id            SERIAL PRIMARY KEY,
    recipient     TEXT,
    reply_text    TEXT,
    intent        TEXT,
    draft_type    TEXT,
    meta          JSONB,
    source        TEXT,
    status        TEXT DEFAULT 'pending',
    approved_at   TIMESTAMPTZ,
    sent_at       TIMESTAMPTZ,
    admin_phone   TEXT,
    error_text    TEXT,
    reviewed_reply_id INT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fazle_payment_drafts (
    id            SERIAL PRIMARY KEY,
    draft_type    TEXT,
    employee_id   INT REFERENCES wbom_employees(employee_id),
    employee_name TEXT,
    employee_mobile TEXT,
    escort_program_id INT REFERENCES wbom_escort_programs(program_id),
    duty_days     FLOAT,
    gross_amount  FLOAT DEFAULT 0,
    food_bill     FLOAT DEFAULT 0,
    conveyance    FLOAT DEFAULT 0,
    advance_deduction FLOAT DEFAULT 0,
    expected_amount FLOAT,
    approved_amount FLOAT,
    approved_at   TIMESTAMPTZ,
    payment_method TEXT,
    payment_number TEXT,
    status        TEXT DEFAULT 'pending',
    source        TEXT,
    draft_text    TEXT,
    admin_reply   TEXT,
    accountant_msg TEXT,
    admin_phone   TEXT,
    source_bridge TEXT DEFAULT 'bridge2',
    notes         TEXT,
    correction_of INT REFERENCES fazle_payment_drafts(id),
    correction_type TEXT,
    correction_note TEXT,
    corrected_by  TEXT,
    corrected_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_payment_drafts_program_type
    ON fazle_payment_drafts (escort_program_id, draft_type)
    WHERE escort_program_id IS NOT NULL;

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

CREATE TABLE IF NOT EXISTS fazle_payment_correction_log (
    id              SERIAL PRIMARY KEY,
    action          TEXT NOT NULL,
    payment_draft_id INT REFERENCES fazle_payment_drafts(id),
    transaction_id  INT,
    counter_tx_id   INT,
    original_amount DECIMAL(12,2),
    correction_amount DECIMAL(12,2),
    method          TEXT,
    note            TEXT,
    performed_by    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fazle_knowledge_base (
    kb_id         SERIAL PRIMARY KEY,
    category      TEXT,
    keywords      TEXT[],
    reply_text    TEXT,
    language      TEXT DEFAULT 'bn',
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fazle_recruitment_sessions (
    session_id    SERIAL PRIMARY KEY,
    phone         TEXT UNIQUE,
    stage         TEXT DEFAULT 'init',
    data          JSONB DEFAULT '{}',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS fazle_reviewed_reply_memory (
    memory_id     SERIAL PRIMARY KEY,
    intent        TEXT,
    role          TEXT,
    trigger_hash  TEXT,
    reply_text    TEXT,
    use_count     INT DEFAULT 0,
    is_active     BOOLEAN DEFAULT TRUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fazle_report_cache (
    cache_key     TEXT PRIMARY KEY,
    payload       JSONB,
    expires_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS fazle_service_heartbeats (
    service_name  TEXT PRIMARY KEY,
    last_seen     TIMESTAMPTZ DEFAULT NOW()
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

-- Seed roles
INSERT INTO fazle_roles (name, level, description) VALUES
  ('viewer',     1, 'Read-only'),
  ('operator',   2, 'Approve drafts, process payments'),
  ('accountant', 3, 'Payroll and payment operations'),
  ('admin',      4, 'System management'),
  ('superadmin', 5, 'Full access')
ON CONFLICT (name) DO NOTHING;
"""


@pytest_asyncio.fixture
async def test_db_pool(_setup_test_schema) -> AsyncGenerator[asyncpg.Pool, None]:
    """Per-test asyncpg pool. Chains _setup_test_schema so the schema is
    created/wiped once per session before any DB test runs. Truncates all
    data tables in teardown so each test starts with clean state. Pure
    (no-DB) unit tests never request test_db_pool so they are unaffected."""
    pool = await asyncpg.create_pool(TEST_DB_URL, min_size=1, max_size=3)
    yield pool
    async with pool.acquire() as conn:
        try:
            await conn.execute("""
                TRUNCATE TABLE
                    fazle_payment_correction_log,
                    fazle_admin_audit,
                    fazle_reviewed_reply_memory,
                    fazle_payment_drafts,
                    fazle_draft_replies,
                    fazle_recruitment_sessions,
                    wbom_payroll_approval_log,
                    wbom_payroll_run_items,
                    wbom_payroll_runs,
                    wbom_staging_payments,
                    wbom_cash_transactions,
                    wbom_attendance,
                    wbom_escort_programs,
                    fazle_contact_roles,
                    wbom_contacts,
                    wbom_employees,
                    fazle_admins,
                    fazle_admin_roles,
                    wbom_whatsapp_messages,
                    fazle_db_backups
                RESTART IDENTITY CASCADE
            """)
        except Exception:
            pass
    await pool.close()


@pytest_asyncio.fixture
async def clean_tables(test_db_pool):
    """Truncate data tables before each test. Keeps schema and seed data."""
    yield
    async with test_db_pool.acquire() as conn:
        await conn.execute("""
            TRUNCATE TABLE
                fazle_payment_correction_log,
                fazle_admin_audit,
                fazle_reviewed_reply_memory,
                fazle_payment_drafts,
                fazle_draft_replies,
                fazle_recruitment_sessions,
                wbom_payroll_approval_log,
                wbom_payroll_run_items,
                wbom_payroll_runs,
                wbom_staging_payments,
                wbom_cash_transactions,
                wbom_attendance,
                wbom_escort_programs,
                fazle_contact_roles,
                wbom_contacts,
                wbom_employees,
                fazle_admins,
                fazle_admin_roles,
                wbom_whatsapp_messages,
                fazle_db_backups
            RESTART IDENTITY CASCADE
        """)


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

    from app.main import app as fastapi_app

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app),
        base_url="http://testserver",
        headers={"X-Internal-Key": TEST_API_KEY},
    ) as ac:
        yield ac


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
    """Mock Ollama LLM call."""
    with respx.mock(base_url="http://mock-ollama", assert_all_called=False) as mock:
        mock.post("/api/generate").mock(return_value=_ollama_response("ধন্যবাদ।"))
        yield mock


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
                 '2026-05-01', 'D', 'Running', '2026-05-01')
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
                 payment_method, payment_number, status, source,
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
