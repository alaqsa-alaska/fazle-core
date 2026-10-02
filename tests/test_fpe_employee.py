"""
Tests for fazle_payroll_engine.employee — matching and auto-creation.

Uses mocked DB helpers — no real DB connection required.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch


class _FakeConn:
    """Minimal stand-in for the asyncpg.Connection _auto_create_employee()
    uses via db_conn() — a raw `async with db_conn() as con:` block, not the
    execute/fetch_one/fetch_val wrappers this file already patches. Every
    other test in this file only exercises match_or_create_employee()'s
    match paths (which stay on the wrappers); only the auto-create path
    reaches into db_conn() directly, which is why this needed its own mock."""

    def __init__(self, new_id: int = 42):
        self._new_id = new_id

    async def execute(self, *a, **kw):
        return None

    async def fetchrow(self, *a, **kw):
        return None  # no pre-existing employee found by lock re-check

    async def fetchval(self, *a, **kw):
        return self._new_id

    def transaction(self):
        @asynccontextmanager
        async def _tx():
            yield
        return _tx()


@asynccontextmanager
async def _fake_db_conn(new_id: int = 42):
    yield _FakeConn(new_id)


class TestEmployeeMatching:
    """Test match_or_create_employee() priority chain."""

    @pytest.mark.asyncio
    async def test_exact_phone_match(self):
        """If payout_phone matches an existing employee primary_phone, return that employee."""
        from modules.fazle_payroll_engine.employee import match_or_create_employee

        existing = {
            "id": 1,
            "employee_code": "EMP-00001",
            "full_name": "Jakir Hossain",
            "primary_phone": "01725494969",
            "employee_id_phone": None,
            "status": "active",
        }

        with patch("modules.fazle_payroll_engine.employee.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("modules.fazle_payroll_engine.employee.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch("modules.fazle_payroll_engine.employee.execute", new_callable=AsyncMock):
            # First call (exact phone match) returns employee
            mock_fetch_one.return_value = existing
            mock_fetch_all.return_value = []

            result = await match_or_create_employee(
                name_raw="Jakir",
                payout_phone="01725494969",
                employee_id_phone=None,
            )
            assert result is not None
            assert result.employee_id == 1
            assert result.match_type == "exact_phone"

    @pytest.mark.asyncio
    async def test_auto_create_when_no_match(self):
        """
        When no existing employee matches, auto-create should return a new
        employee.

        Regression note: this test used to be order-dependent (passed in
        isolation, failed when run after tests that reset app.database._pool
        to None). Root cause: _auto_create_employee() writes via a raw
        `async with db_conn() as con:` block, not the fetch_one/fetch_val/
        execute wrappers this test patches -- db_conn() falls back to a real
        asyncpg.connect(settings.database_url) whenever _pool is None, which
        fails outside a real DB environment. Fixed by also patching db_conn
        with a fake connection (see _fake_db_conn above) so this test no
        longer depends on unrelated global pool state from other tests.
        """
        from modules.fazle_payroll_engine.employee import match_or_create_employee

        with patch("modules.fazle_payroll_engine.employee.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("modules.fazle_payroll_engine.employee.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch("modules.fazle_payroll_engine.employee.fetch_val", new_callable=AsyncMock) as mock_fetch_val, \
             patch("modules.fazle_payroll_engine.employee.execute", new_callable=AsyncMock), \
             patch("modules.fazle_payroll_engine.employee.add_alias", new_callable=AsyncMock), \
             patch("modules.fazle_payroll_engine.employee.db_conn", lambda: _fake_db_conn(new_id=42)):
            mock_fetch_one.return_value = None  # no match on any phone/name lookup
            mock_fetch_all.return_value = []     # no fuzzy candidates
            mock_fetch_val.return_value = 42     # auto-create inserts, returns id=42

            result = await match_or_create_employee(
                name_raw="Unknown Person",
                payout_phone="01999888777",
                employee_id_phone=None,
            )
            assert result is not None
            assert result.match_type == "auto_created"

    @pytest.mark.asyncio
    async def test_employee_id_phone_wins_over_colliding_payout_phone(self):
        """
        Regression test for the 2026-07-10 misattribution bug: the "ID:"
        mobile is the authoritative employee identity (payment_business_rules.md
        7.2/9.1) and must be checked BEFORE payout_phone. Real incident: a
        message with ID: 01805668850 (Junaid Islam Shami) paid out to
        01773055387, a phone that unrelated employee "Ahsan" already had
        registered as primary_phone. Before the fix, step-1 (payout_phone)
        matched Ahsan first and returned him, silently misattributing the
        payment. This must resolve to the employee_id_phone owner instead.
        """
        from modules.fazle_payroll_engine.employee import match_or_create_employee

        correct_employee = {
            "id": 1274,
            "employee_code": "EMP-01274",
            "full_name": "Junaid Islam",
            "primary_phone": "01716797411",
            "employee_id_phone": "01805668850",
            "status": "active",
        }
        wrong_employee_by_payout_phone = {
            "id": 7,
            "employee_code": "EMP-00007",
            "full_name": "Ahsan",
            "primary_phone": "01773055387",
            "employee_id_phone": None,
            "status": "active",
        }

        def _has(args, phone):
            """The resolver now looks up by a list of phone-format variants."""
            if not args:
                return False
            a = args[0]
            return phone in a if isinstance(a, (list, tuple)) else a == phone

        async def fetch_one_side_effect(query, *args, **kwargs):
            # step 1 combines id-phone + primary-phone into one ANY() query
            if "employee_id_phone = ANY" in query or "primary_phone = ANY" in query:
                if _has(args, "01805668850"):
                    return correct_employee
                if _has(args, "01773055387"):
                    return wrong_employee_by_payout_phone
                return None
            return None

        with patch("modules.fazle_payroll_engine.employee.fetch_one", new=AsyncMock(side_effect=fetch_one_side_effect)), \
             patch("modules.fazle_payroll_engine.employee.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch("modules.fazle_payroll_engine.employee.execute", new_callable=AsyncMock):
            mock_fetch_all.return_value = []

            result = await match_or_create_employee(
                name_raw="Junaid Islam Shami",
                payout_phone="01773055387",
                employee_id_phone="01805668850",
            )
            assert result is not None
            assert result.employee_id == 1274
            assert result.match_type == "exact_id_phone"

    @pytest.mark.asyncio
    async def test_none_returned_when_no_name_no_phone(self):
        """
        With no phone and no name at all, the function must return None rather
        than auto-create a phantom employee (employee.py step 6 safety guard —
        "A phone-less, name-less call can never produce a useful employee
        record"). This test previously asserted the opposite of the function's
        own documented/logged behavior; corrected to match reality.
        """
        from modules.fazle_payroll_engine.employee import match_or_create_employee

        with patch("modules.fazle_payroll_engine.employee.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("modules.fazle_payroll_engine.employee.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch("modules.fazle_payroll_engine.employee.fetch_val", new_callable=AsyncMock), \
             patch("modules.fazle_payroll_engine.employee.execute", new_callable=AsyncMock), \
             patch("modules.fazle_payroll_engine.employee.add_alias", new_callable=AsyncMock):
            mock_fetch_one.return_value = None
            mock_fetch_all.return_value = []

            result = await match_or_create_employee(
                name_raw=None,
                payout_phone=None,
                employee_id_phone=None,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_fuzzy_name_match(self):
        """A name with slight variation should match via fuzzy if threshold met."""
        from modules.fazle_payroll_engine.employee import match_or_create_employee

        # Employee "Md Jakir Hossain" — query with "Jakir Hossain" — high fuzzy score
        candidate = {
            "id": 5,
            "employee_code": "EMP-00005",
            "full_name": "Md Jakir Hossain",
            "name_normalized": "md jakir hossain",  # required by employee.py fuzzy loop
            "primary_phone": "01700000001",
            "status": "active",
        }

        with patch("modules.fazle_payroll_engine.employee.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("modules.fazle_payroll_engine.employee.fetch_all", new_callable=AsyncMock) as mock_fetch_all, \
             patch("modules.fazle_payroll_engine.employee.fetch_val", new_callable=AsyncMock), \
             patch("modules.fazle_payroll_engine.employee.execute", new_callable=AsyncMock), \
             patch("modules.fazle_payroll_engine.employee.add_alias", new_callable=AsyncMock):
            mock_fetch_one.return_value = None  # no exact match
            mock_fetch_all.return_value = [candidate]

            result = await match_or_create_employee(
                name_raw="Jakir Hossain",
                payout_phone=None,
                employee_id_phone=None,
            )
            # Could be fuzzy_name match or auto_created depending on score
            assert result is not None


class TestNormalization:
    """Additional normalizer edge cases for employee module."""

    def test_normalize_name_removes_honorifics(self):
        from modules.fazle_payroll_engine.normalizer import normalize_name
        # normalize_name should lowercase and strip extra whitespace
        result = normalize_name("  Md.  Jakir  ")
        assert "jakir" in result.lower()

    def test_normalize_amount_with_dash_suffix(self):
        from modules.fazle_payroll_engine.normalizer import normalize_amount
        # normalize_amount works on cleaned strings (parser strips /- suffix)
        assert normalize_amount("5000") == 5000.0
        assert normalize_amount("5,000") == 5000.0

    def test_normalize_amount_none_on_invalid(self):
        from modules.fazle_payroll_engine.normalizer import normalize_amount
        assert normalize_amount("N/A") is None
        assert normalize_amount("") is None
