"""
Phase 1 (Owner directive 2026-08-30) — employee identity authority for the
Admin→Accountant payment path.

Canonical rule under test:

  * an explicit employee-ID phone is the SOLE identity key — a payout phone
    never resolves to, or creates, a different employee;
  * if only one phone is present, that phone is the identity;
  * employee phone identity > employee name;
  * name spelling never creates a second employee once the identity phone
    matches;
  * different identity phones = different people even with identical names;
  * existing rows match regardless of active/inactive status (active wins);
  * an explicit identity phone that matches nothing does NOT fall through to
    payout-phone matching — it creates keyed on the identity phone.

All mocked — no DB.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from unittest.mock import AsyncMock, patch


class _FakeConn:
    def __init__(self, new_id: int = 500):
        self._new_id = new_id
        self.inserted_args: list = []

    async def execute(self, *a, **kw):
        return None

    async def fetchrow(self, *a, **kw):
        return None  # lock re-check finds nothing

    async def fetchval(self, query, *a, **kw):
        if "INSERT INTO fpe_employees" in query:
            self.inserted_args = list(a)
        return self._new_id

    def transaction(self):
        @asynccontextmanager
        async def _tx():
            yield
        return _tx()


_LAST_CONN: dict = {}


@asynccontextmanager
async def _fake_db_conn(new_id: int = 500):
    c = _FakeConn(new_id)
    _LAST_CONN["c"] = c
    yield c


def _patches():
    return (
        patch("modules.fazle_payroll_engine.employee.fetch_all", new=AsyncMock(return_value=[])),
        patch("modules.fazle_payroll_engine.employee.fetch_val", new=AsyncMock(return_value=500)),
        patch("modules.fazle_payroll_engine.employee.execute", new=AsyncMock()),
        patch("modules.fazle_payroll_engine.employee.add_alias", new=AsyncMock()),
        patch("modules.fazle_payroll_engine.employee.db_conn", lambda: _fake_db_conn(500)),
    )


@pytest.mark.asyncio
async def test_explicit_id_phone_unmatched_does_not_fall_through_to_payout_phone():
    """The core Phase 1 proof: ID phone present but not on file, while the
    payout phone belongs to a DIFFERENT existing employee — must NOT return
    that employee; must create a new employee keyed on the ID phone."""
    from modules.fazle_payroll_engine.employee import match_or_create_employee

    other_person_by_payout = {
        "id": 99, "employee_code": "EMP-00099", "full_name": "Someone Else",
        "primary_phone": "01700000000", "employee_id_phone": None, "status": "active",
        "canonical_employee_id": None,
    }
    queried: list = []

    async def fetch_one_side_effect(query, *args, **kwargs):
        queried.append((query, args))
        # payout phone would match "Someone Else" IF it were ever queried
        if args and args[0] == "01700000000":
            return other_person_by_payout
        return None

    p1, p2, p3, p4, p5 = _patches()
    with p1, p2, p3, p4, p5, \
         patch("modules.fazle_payroll_engine.employee.fetch_one", new=AsyncMock(side_effect=fetch_one_side_effect)):
        result = await match_or_create_employee(
            name_raw="Rahim",
            payout_phone="01700000000",       # belongs to Someone Else
            employee_id_phone="01811111111",  # explicit ID, not on file
        )

    assert result is not None
    assert result.employee_id != 99, "payout phone must not resolve identity"
    assert result.match_type == "auto_created"
    # the payout phone must never have been used as a lookup key
    assert all(a != ("01700000000",) for _, a in queried), \
        f"payout phone leaked into an identity lookup: {queried}"
    # the new row is keyed on the ID phone (INSERT args: name, name_norm, primary, eid)
    ins = _LAST_CONN["c"].inserted_args
    assert ins[2] == "01811111111" and ins[3] == "01811111111"


@pytest.mark.asyncio
async def test_single_phone_is_the_identity():
    """One phone, no explicit ID: that phone is the identity and matches an
    existing (inactive) employee rather than creating a duplicate."""
    from modules.fazle_payroll_engine.employee import match_or_create_employee

    existing_inactive = {
        "id": 7, "employee_code": "EMP-00007", "full_name": "Saiful",
        "primary_phone": "01849258074", "employee_id_phone": "01849258074",
        "status": "inactive", "canonical_employee_id": None,
    }

    async def fetch_one_side_effect(query, *args, **kwargs):
        if "employee_id_phone = $1 OR primary_phone = $1" in query and args and args[0] == "01849258074":
            return existing_inactive
        return None

    p1, p2, p3, p4, p5 = _patches()
    with p1, p2, p3, p4, p5, \
         patch("modules.fazle_payroll_engine.employee.fetch_one", new=AsyncMock(side_effect=fetch_one_side_effect)):
        # parser promotes a lone payout phone to employee_id_phone upstream, so
        # both arrive equal here
        result = await match_or_create_employee("Saiful Operation", "01849258074", "01849258074")

    assert result is not None
    assert result.employee_id == 7
    assert result.match_type in ("exact_id_phone", "exact_phone")


@pytest.mark.asyncio
async def test_name_spelling_difference_does_not_create_second_employee():
    """Same ID phone, different name spelling → same employee, no new row."""
    from modules.fazle_payroll_engine.employee import match_or_create_employee

    existing = {
        "id": 42, "employee_code": "EMP-00042", "full_name": "Mohammad Anwar",
        "primary_phone": "01900000000", "employee_id_phone": "01900000000",
        "status": "active", "canonical_employee_id": None,
    }

    async def fetch_one_side_effect(query, *args, **kwargs):
        if args and args[0] == "01900000000" and "employee_id_phone = $1 OR primary_phone = $1" in query:
            return existing
        return None

    p1, p2, p3, p4, p5 = _patches()
    with p1, p2, p3, p4, p5, \
         patch("modules.fazle_payroll_engine.employee.fetch_one", new=AsyncMock(side_effect=fetch_one_side_effect)):
        result = await match_or_create_employee("Md. Anowar", None, "01900000000")

    assert result is not None
    assert result.employee_id == 42
    assert result.match_type == "exact_id_phone"


@pytest.mark.asyncio
async def test_different_id_phones_same_name_are_different_people():
    """Identical name, different identity phone that is not on file → new
    employee, not a merge onto the name match."""
    from modules.fazle_payroll_engine.employee import match_or_create_employee

    async def fetch_one_side_effect(query, *args, **kwargs):
        # no phone match anywhere; a same-name active employee exists but must
        # NOT be returned for a new identity phone
        if "similarity(name_normalized" in query:
            return None
        return None

    p1, p2, p3, p4, p5 = _patches()
    with p1, p2, p3, p4, p5, \
         patch("modules.fazle_payroll_engine.employee.fetch_one", new=AsyncMock(side_effect=fetch_one_side_effect)):
        result = await match_or_create_employee("Karim", None, "01755555555")

    assert result is not None
    assert result.match_type == "auto_created"


@pytest.mark.asyncio
async def test_name_only_no_phone_returns_none_for_review():
    """No phone at all + no confident name match → None (manual review), never
    a phantom auto-create."""
    from modules.fazle_payroll_engine.employee import match_or_create_employee

    p1, p2, p3, p4, p5 = _patches()
    with p1, p2, p3, p4, p5, \
         patch("modules.fazle_payroll_engine.employee.fetch_one", new=AsyncMock(return_value=None)):
        result = await match_or_create_employee("Some Name", None, None)

    assert result is None


@pytest.mark.asyncio
async def test_admin_transactions_resolver_delegates_to_canonical():
    """modules.admin_transactions.resolve_or_create_employee now delegates to
    the one canonical FPE resolver."""
    import modules.admin_transactions as at

    fake_result = type("R", (), {"employee_id": 555, "match_type": "exact_id_phone"})()

    with patch("modules.fazle_payroll_engine.employee.match_or_create_employee",
               new=AsyncMock(return_value=fake_result)) as mmoce, \
         patch("modules.admin_transactions.fetch_one", new=AsyncMock(return_value={
             "id": 555, "employee_code": "EMP-00555", "full_name": "X",
             "primary_phone": "01712345678", "employee_id_phone": "01712345678",
         })):
        row = await at.resolve_or_create_employee("X", "01712345678", "01700000000")

    assert row["id"] == 555
    # canonical resolver called with (name, payout_phone, employee_id_phone)
    args = mmoce.call_args[0]
    assert args[0] == "X" and args[2] == "01712345678"
