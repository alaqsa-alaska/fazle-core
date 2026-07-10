"""Unit tests — modules/client_billing/bills (Phase 2B, Commit C)"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


async def _make_contact(conn, phone="8801670535255"):
    return await conn.fetchval(
        "INSERT INTO wbom_contacts (whatsapp_number) VALUES ($1) RETURNING contact_id", phone
    )


async def _make_program(conn, contact_id, status="completed"):
    return await conn.fetchval(
        "INSERT INTO wbom_escort_programs (status, contact_id) VALUES ($1, $2) RETURNING program_id",
        status, contact_id,
    )


async def _make_profile(contact_id, **rates):
    from modules.client_billing.profiles import create_client_billing_profile
    from unittest.mock import patch

    with patch(
        "modules.client_billing.profiles._escort_client_phone_list",
        return_value=["8801670535255"],
    ):
        return await create_client_billing_profile(
            "Test Client Co", "8801670535255", contact_id=contact_id, **rates,
        )


def _use_pool(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool


class TestGenerateBill:
    @pytest.mark.asyncio
    async def test_successful_generation(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.bills import generate_bill

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            program_id = await _make_program(conn, contact_id)
        await _make_profile(contact_id, general_rate=5000, employee_wage_general=3000)

        bill = await generate_bill(program_id, "general")
        assert bill["program_id"] == program_id
        assert float(bill["service_charge"]) == 5000
        assert float(bill["total_amount"]) == 5000
        assert bill["payment_status"] == "Pending"
        assert bill["bill_number"].startswith("AQS-")
        assert bill["estimated_profit"] == 2000  # 5000 - 3000 wage

    @pytest.mark.asyncio
    async def test_duplicate_program_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.client_billing.bills import generate_bill

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            program_id = await _make_program(conn, contact_id)
        await _make_profile(contact_id, general_rate=5000)

        await generate_bill(program_id, "general")
        with pytest.raises(HTTPException) as exc:
            await generate_bill(program_id, "general")
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_non_completed_program_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.client_billing.bills import generate_bill

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            program_id = await _make_program(conn, contact_id, status="confirmed")
        await _make_profile(contact_id, general_rate=5000)

        with pytest.raises(HTTPException) as exc:
            await generate_bill(program_id, "general")
        assert exc.value.status_code == 400
        assert "not Completed" in exc.value.detail

    @pytest.mark.asyncio
    async def test_missing_contact_id_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.client_billing.bills import generate_bill

        async with test_db_pool.acquire() as conn:
            program_id = await _make_program(conn, contact_id=None)

        with pytest.raises(HTTPException) as exc:
            await generate_bill(program_id, "general")
        assert exc.value.status_code == 400
        assert "no linked contact_id" in exc.value.detail

    @pytest.mark.asyncio
    async def test_missing_billing_profile_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.client_billing.bills import generate_bill

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            program_id = await _make_program(conn, contact_id)

        with pytest.raises(HTTPException) as exc:
            await generate_bill(program_id, "general")
        assert exc.value.status_code == 400
        assert "No active client billing profile" in exc.value.detail

    @pytest.mark.asyncio
    async def test_rate_not_configured_for_duty_type_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.client_billing.bills import generate_bill

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            program_id = await _make_program(conn, contact_id)
        await _make_profile(contact_id, general_rate=5000)  # no rate_24h set

        with pytest.raises(HTTPException) as exc:
            await generate_bill(program_id, "24h")
        assert exc.value.status_code == 400
        assert "rate_24h" in exc.value.detail

    @pytest.mark.asyncio
    async def test_invalid_duty_type_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.client_billing.bills import generate_bill

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            program_id = await _make_program(conn, contact_id)
        await _make_profile(contact_id, general_rate=5000)

        with pytest.raises(HTTPException) as exc:
            await generate_bill(program_id, "weekly")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_bill_number_sequential_same_year(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.bills import generate_bill

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            p1 = await _make_program(conn, contact_id)
            p2 = await _make_program(conn, contact_id)
        await _make_profile(contact_id, general_rate=1000)

        b1 = await generate_bill(p1, "general")
        b2 = await generate_bill(p2, "general")
        n1 = int(b1["bill_number"].rsplit("-", 1)[1])
        n2 = int(b2["bill_number"].rsplit("-", 1)[1])
        assert n2 == n1 + 1


class TestListBillablePrograms:
    @pytest.mark.asyncio
    async def test_excludes_already_billed_and_unlinked(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.bills import generate_bill, list_billable_programs

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            billable = await _make_program(conn, contact_id)
            no_contact = await _make_program(conn, contact_id=None)
        await _make_profile(contact_id, general_rate=1000)

        before = await list_billable_programs()
        assert any(p["program_id"] == billable for p in before)
        assert not any(p["program_id"] == no_contact for p in before)

        await generate_bill(billable, "general")
        after = await list_billable_programs()
        assert not any(p["program_id"] == billable for p in after)
