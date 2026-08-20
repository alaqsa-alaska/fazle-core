"""
Regression test for the 2026-08-20 /api/fpe/employees/search fix: the
name_exact and alias_name match tiers now filter status='active',
matching the fuzzy_name tier (which already did). Previously an
inactive/former employee could surface via exact/alias name match with
no distinction from an active one -- inconsistent with every other tier.
"""
import pytest

from tests.conftest import TEST_API_KEY

pytestmark = pytest.mark.integration


def _headers():
    return {"X-Internal-Key": TEST_API_KEY}


class TestEmployeesSearchActiveStatusConsistency:
    async def test_inactive_employee_not_returned_via_exact_name(self, clean_tables, test_db_pool, client):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fpe_employees (full_name, name_normalized, primary_phone, status)
                VALUES ('Former Employee', 'former employee', '01899999999', 'inactive')
            """)
        resp = await client.get(
            "/api/fpe/employees/search", params={"q": "Former Employee"}, headers=_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_inactive_employee_not_returned_via_alias_name(self, clean_tables, test_db_pool, client):
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO fpe_employees (full_name, name_normalized, primary_phone, status)
                VALUES ('Former Employee', 'former employee', '01899999998', 'inactive')
                RETURNING id
            """)
            await conn.execute(
                "INSERT INTO fpe_employee_aliases (employee_id, alias_type, alias_value) "
                "VALUES ($1, 'name', 'former alias')",
                row["id"],
            )
        resp = await client.get(
            "/api/fpe/employees/search", params={"q": "Former Alias"}, headers=_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    async def test_active_employee_still_returned_via_exact_name(self, clean_tables, test_db_pool, client):
        """Regression: the happy path must be unaffected by the fix."""
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fpe_employees (full_name, name_normalized, primary_phone, status)
                VALUES ('Current Employee', 'current employee', '01899999997', 'active')
            """)
        resp = await client.get(
            "/api/fpe/employees/search", params={"q": "Current Employee"}, headers=_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["results"][0]["match_type"] == "name_exact"
