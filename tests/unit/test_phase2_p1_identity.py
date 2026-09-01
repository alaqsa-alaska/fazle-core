"""
tests/unit/test_phase2_p1_identity.py
=====================================

P1 running-code identity fixes (2026-09-01), all in the LIVE resolvers
(identity_brain / user_role / message_router) — independent of the Phase-2
feature flag:

  * accountant: ACCOUNTANT_PHONE resolves deterministically to
    role="accountant" via a settings tier (not a deletable DB seed row),
    even when the number is also an active employee.
  * wbom_contacts / fazle_contact_roles identity lookups are scoped to
    platform='whatsapp' — a same-number row on another platform never
    becomes WhatsApp identity authority.
  * an INACTIVE employee that exists only via fpe_employees still resolves
    by phone (no more `e.status='active'` filter on the fallback).
  * no duplicate identity is ever created (resolvers are read-only).
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from modules.identity_brain import detect_identity


@pytest_asyncio.fixture(autouse=True)
async def _inject_test_db_pool(test_db_pool):
    import app.database as db_module
    prev = db_module._pool
    db_module._pool = test_db_pool
    try:
        yield
    finally:
        db_module._pool = prev


@pytest.fixture
def _settings_env(monkeypatch):
    from app.config import get_settings

    def _set(**kv):
        for k, v in kv.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
    yield _set
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_accountant_phone_wins_over_employee_role(seed_employee, _settings_env):
    """The accountant's number is also an active Security Guard employee.
    Step 1b must classify them as accountant, not employee/security_guard."""
    _settings_env(ACCOUNTANT_PHONE=seed_employee["employee_mobile"])
    res = await detect_identity(seed_employee["employee_mobile"])
    assert res["identity_role"] == "accountant"
    assert res["identity_source"] == "settings"
    assert res["identity_confidence"] == 100


@pytest.mark.asyncio
async def test_accountant_still_works_without_seed_row(_settings_env, test_db_pool):
    """No fazle_contact_roles seed at all -> still accountant (the point of
    moving it to a settings tier)."""
    _settings_env(ACCOUNTANT_PHONE="8801912121212")
    res = await detect_identity("8801912121212")
    assert res["identity_role"] == "accountant"


@pytest.mark.asyncio
async def test_wbom_contacts_platform_filter(test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, relation, is_active) "
            "VALUES ('8801755550001', 'FB Only Person', 'facebook', 'vip', true)")
    res = await detect_identity("8801755550001")
    # a facebook-platform contact must NOT drive WhatsApp identity
    assert res["identity_role"] == "unknown"
    assert not res.get("display_name")


@pytest.mark.asyncio
async def test_wbom_contacts_whatsapp_row_still_resolves(test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, relation, is_active) "
            "VALUES ('8801755550002', 'Real WA Contact', 'whatsapp', 'vip', true)")
    res = await detect_identity("8801755550002")
    assert res.get("display_name") == "Real WA Contact"


@pytest.mark.asyncio
async def test_inactive_fpe_only_employee_still_resolves(test_db_pool):
    async with test_db_pool.acquire() as conn:
        emp_id = await conn.fetchval(
            "INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary) "
            "VALUES ('8801833330001', 'Dormant Boatman', 'Security Guard', 'Inactive', 9000) "
            "RETURNING employee_id")
        # fpe row keyed on a DIFFERENT phone than employee_mobile, status inactive,
        # linked back to the wbom row via wbom_employee_id
        await conn.execute(
            "INSERT INTO fpe_employees "
            "(full_name, name_normalized, primary_phone, employee_id_phone, status, wbom_employee_id) "
            "VALUES ('Dormant Boatman', 'dormant boatman', '8801833339999', '8801833339999', "
            "        'inactive', $1)", emp_id)
        before = await conn.fetchval("SELECT count(*) FROM wbom_employees")

    res = await detect_identity("8801833339999")   # the fpe-only phone
    assert res.get("employee_id") == emp_id
    assert res["identity_role"] in ("employee", "security_guard")

    async with test_db_pool.acquire() as conn:
        after = await conn.fetchval("SELECT count(*) FROM wbom_employees")
    assert after == before   # never creates a duplicate
