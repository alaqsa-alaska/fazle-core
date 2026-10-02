"""Offline guards for the K3.2 Recruitment reconciliation.

These tests inspect the planned migration and use synthetic rows. They never
connect to ai-postgres and never build or mutate BM25/Qdrant indexes.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "db/migrations/086_k32_recruitment_reconciliation.sql"
SEED = ROOT / "scripts/seed_recruitment_kb.sql"
AUTHORITY = ROOT / "knowledge_base/00_governance/recruitment_fact_authority.json"

FAQ_IDS = {
    107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 119, 120,
    121, 122, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135,
    136, 137, 138, 139, 140, 141, 142, 143, 144, 145, 146, 147, 148,
    149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159, 160, 161,
    162, 163, 164, 165, 166, 167, 168, 169, 172, 174, 175, 176, 177,
    178,
}
SUPPRESSED_IDS = FAQ_IDS - {117}
GOVERNANCE = {
    "age_range": "33cc9acf-2bff-431a-83a5-9f0b1b3b204b",
    "education_required": "8d30d1be-e191-48ab-b508-caf918f3b376",
    "joining_fee": "10d7caf7-cd80-4aa0-8da5-eea25aa2fab4",
    "joining_fee_refund": "2edeab73-b3c4-4202-b02d-648864b9d5e5",
    "min_upfront_payment": "b7798e75-8461-4ef4-b7ea-31f80a0e9e60",
    "monthly_deduction": "3e387cc3-8a2c-4429-9837-410a16b7149b",
    "no_leave_first": "8d658ca1-52d0-4859-9650-2b6c298817c9",
    "resignation_notice": "b9e64026-97df-492c-928f-5d48b3e1bdac",
    "resignation_penalty": "f3972e1d-cb71-42cd-a61a-6a9d282a4457",
}


def _migration_text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _faq_plan() -> dict[int, tuple[str, bool]]:
    section = _migration_text().split(
        "CREATE TEMP TABLE _k32_recruitment_governance_expected", 1
    )[0]
    return {
        int(row_id): (key, retain == "true")
        for row_id, key, retain in re.findall(
            r"\((\d+), '([^']+)', (true|false)\)", section
        )
    }


def _governance_plan() -> dict[str, str]:
    section = _migration_text().split(
        "INSERT INTO _k32_recruitment_governance_expected", 1
    )[1].split(";", 1)[0]
    return {
        key: row_id
        for row_id, key in re.findall(
            r"\('([^']+)'[^,]*, '([^']+)'\)", section
        )
    }


def test_exact_66_faq_rows_and_65_suppression_rows_are_planned():
    plan = _faq_plan()
    assert set(plan) == FAQ_IDS
    assert len(plan) == 66
    assert {row_id for row_id, (_, retain) in plan.items() if not retain} == SUPPRESSED_IDS
    assert plan[117] == ("b11_greeting", True)


def test_all_nine_governance_rows_are_planned_by_uuid_and_fact_key():
    assert _governance_plan() == GOVERNANCE


@dataclass
class _Faq:
    row_id: int
    category: str
    is_active: bool
    value: str


@dataclass
class _Governance:
    row_id: str
    status: str = "active"
    deprecated_at: str | None = None
    deprecation_reason: str | None = None


def _apply_synthetic_reconciliation(faq_rows, governance_rows):
    for row in faq_rows:
        if row.row_id in SUPPRESSED_IDS and row.category == "recruitment":
            row.is_active = False
    for row in governance_rows:
        if row.row_id in GOVERNANCE.values() and row.status == "active":
            row.status = "deprecated"
            row.deprecated_at = row.deprecated_at or "synthetic-now"
            row.deprecation_reason = row.deprecation_reason or "K3.2 legacy record"


def test_reconciliation_is_idempotent_preserves_history_and_leaves_other_domains():
    faq = [
        _Faq(row_id, "recruitment", True, f"historical-{row_id}")
        for row_id in FAQ_IDS
    ] + [_Faq(9001, "payment", True, "payment-policy")]
    governance = [_Governance(row_id) for row_id in GOVERNANCE.values()]
    before = [(row.row_id, row.is_active, row.value) for row in faq]

    _apply_synthetic_reconciliation(faq, governance)
    once = [(row.row_id, row.is_active, row.value) for row in faq]
    _apply_synthetic_reconciliation(faq, governance)

    assert once == [(row.row_id, row.is_active, row.value) for row in faq]
    assert [(row.row_id, row.is_active, row.value) for row in faq if row.row_id in SUPPRESSED_IDS] == [
        (row_id, False, f"historical-{row_id}") for row_id in sorted(SUPPRESSED_IDS)
    ]
    assert next(row for row in faq if row.row_id == 117).is_active is True
    assert next(row for row in faq if row.row_id == 9001).is_active is True
    assert all(row.status == "deprecated" for row in governance)
    assert before == [
        (row.row_id, True, f"historical-{row.row_id}") for row in faq
        if row.row_id != 9001
    ] + [(9001, True, "payment-policy")]


def test_migration_does_not_rewrite_content_or_authority():
    sql = _migration_text().lower()
    assert "reply_text =" not in sql
    assert "value =" not in sql
    assert "fact_value =" not in sql
    assert "recruitment_fact_authority" not in sql
    assert hashlib.sha256(AUTHORITY.read_bytes()).hexdigest() == (
        "3be6857189370ab7c57551ff93b47e56207876ce0e6e698e32f66ee54cbb65e8"
    )


def test_seed_cannot_delete_or_reactivate_reconciled_rows():
    seed = SEED.read_text(encoding="utf-8").lower()
    assert "delete from fazle_knowledge_base" not in seed
    assert "on conflict (key) do nothing" in seed
    assert "is_active = true" not in seed


def test_migration_has_transactional_fail_safe_preconditions_and_rollback_shape():
    sql = _migration_text().lower()
    assert sql.count("begin;") == 1
    assert sql.count("commit;") == 1
    assert "raise exception 'k3.2 precondition failed" in sql
    assert "set is_active = false" in sql
    assert "set status = 'deprecated'" in sql
    assert "drop table" not in sql
    assert "delete from" not in sql


@pytest.mark.asyncio
async def test_existing_k31_guard_still_blocks_legacy_policy_rows(monkeypatch):
    import modules.knowledge_base as kb

    async def rows(_query):
        return [{
            "key": "b11_salary",
            "category": "recruitment",
            "trigger_keywords": ["salary"],
            "reply_text": "LEGACY salary BDT 1",
        }]

    monkeypatch.setattr(kb, "fetch_all", rows)
    reply = await kb.get_reply("Operation Officer salary", "recruitment")
    assert "LEGACY" not in (reply or "")


@pytest.mark.asyncio
async def test_retained_row_117_is_greeting_only(monkeypatch):
    import modules.knowledge_base as kb

    async def rows(_query):
        return [{
            "key": "b11_greeting",
            "category": "recruitment",
            "trigger_keywords": ["hello"],
            "reply_text": "GREETING_ONLY",
        }]

    monkeypatch.setattr(kb, "fetch_all", rows)
    assert await kb.get_reply("hello", "unknown") == "GREETING_ONLY"
    assert "GREETING_ONLY" not in (await kb.get_reply("salary", "recruitment") or "")


@pytest.mark.asyncio
async def test_existing_k31_guard_fails_closed_for_unknown_vacancies(monkeypatch):
    import modules.knowledge_base as kb

    async def rows(_query):
        return []

    monkeypatch.setattr(kb, "fetch_all", rows)
    assert await kb.get_reply("এখন কয়টি vacancy আছে?", "unknown") == (
        "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"
    )


@pytest.mark.asyncio
async def test_recorded_migration_recovery_is_scoped_and_idempotent_on_disposable_postgres():
    import importlib.util
    import os
    import asyncpg

    utility_path = ROOT / "scripts/reconcile_recorded_k32.py"
    spec = importlib.util.spec_from_file_location("reconcile_recorded_k32", utility_path)
    utility = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(utility)

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        await conn.execute("""
            CREATE TEMP TABLE schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT now()
            );
            CREATE TEMP TABLE fazle_knowledge_base (
                id BIGINT PRIMARY KEY,
                key TEXT NOT NULL,
                category TEXT NOT NULL,
                is_active BOOLEAN NOT NULL
            );
            CREATE TEMP TABLE fazle_knowledge_governance (
                id UUID PRIMARY KEY,
                fact_key TEXT NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL,
                deprecated_at TIMESTAMPTZ,
                deprecation_reason TEXT
            );
        """)
        await conn.execute(
            "INSERT INTO schema_migrations(name) VALUES($1)",
            utility.MIGRATION_NAME,
        )
        for row_id, (key, _retain) in utility.FAQ_EXPECTED.items():
            await conn.execute(
                "INSERT INTO fazle_knowledge_base(id, key, category, is_active) VALUES($1, $2, 'recruitment', true)",
                row_id,
                key,
            )
        await conn.execute(
            "INSERT INTO fazle_knowledge_base(id, key, category, is_active) VALUES(9001, 'payment_policy', 'payment', true)"
        )
        for key, row_id in utility.GOVERNANCE_EXPECTED.items():
            await conn.execute(
                "INSERT INTO fazle_knowledge_governance(id, fact_key, category, status) VALUES($1::uuid, $2, 'recruitment', 'active')",
                row_id,
                key,
            )

        first = await utility.reconcile_recorded_k32(conn)
        second = await utility.reconcile_recorded_k32(conn)

        assert first["status"] == "reconciled"
        assert second["status"] == "already_reconciled"
        assert await conn.fetchval(
            "SELECT count(*) FROM schema_migrations WHERE name = $1",
            utility.MIGRATION_NAME,
        ) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM fazle_knowledge_base WHERE category = 'recruitment' AND is_active"
        ) == 1
        assert await conn.fetchval(
            "SELECT is_active FROM fazle_knowledge_base WHERE id = 117"
        ) is True
        assert await conn.fetchval(
            "SELECT is_active FROM fazle_knowledge_base WHERE id = 9001"
        ) is True
        assert await conn.fetchval(
            "SELECT count(*) FROM fazle_knowledge_governance WHERE status = 'deprecated' AND deprecated_at IS NOT NULL"
        ) == 9
    finally:
        await conn.close()
