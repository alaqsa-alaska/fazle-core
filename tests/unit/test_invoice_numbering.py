"""Unit tests — modules/billing/invoice_numbering (Phase 2B, Commit A)"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit


class TestNextInvoiceNumber:
    """AQS-YYYY-NNNNNN generator: format, concurrency safety, rollback safety, year independence."""

    @pytest.mark.asyncio
    async def test_format(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.billing.invoice_numbering import next_invoice_number

        result = await next_invoice_number(2099)
        assert result == "AQS-2099-000001"

    @pytest.mark.asyncio
    async def test_sequential_same_year(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.billing.invoice_numbering import next_invoice_number

        first = await next_invoice_number(2098)
        second = await next_invoice_number(2098)
        assert first == "AQS-2098-000001"
        assert second == "AQS-2098-000002"

    @pytest.mark.asyncio
    async def test_year_independence(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.billing.invoice_numbering import next_invoice_number

        await next_invoice_number(2097)
        first_2096 = await next_invoice_number(2096)
        assert first_2096 == "AQS-2096-000001", (
            "a different year must start its own count at 1, independent of other years"
        )

    @pytest.mark.asyncio
    async def test_concurrent_calls_produce_distinct_numbers(self, test_db_pool):
        """50 concurrent callers for the same year must never collide or leave a gap."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.billing.invoice_numbering import next_invoice_number

        results = await asyncio.gather(*[next_invoice_number(2095) for _ in range(50)])
        numbers = sorted(int(r.split("-")[-1]) for r in results)
        assert len(set(numbers)) == 50, "duplicate invoice number generated under concurrency"
        assert numbers == list(range(1, 51)), "gap or out-of-range number generated"

    @pytest.mark.asyncio
    async def test_rollback_does_not_leave_a_gap(self, test_db_pool):
        """A number reserved inside a transaction that later rolls back must not be lost —
        this is the deliberate design difference from a raw Postgres SEQUENCE (see
        db/migrations/033_invoice_number_counters.sql header)."""
        import asyncpg
        import app.database as db_module
        db_module._pool = test_db_pool

        before = await test_db_pool.fetchval(
            "SELECT COALESCE(last_number, 0) FROM invoice_number_counters WHERE year=2094"
        )
        try:
            async with test_db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.fetchval("SELECT next_invoice_number(2094)")
                    raise asyncpg.PostgresError("INTENTIONAL_ROLLBACK_FOR_TEST")
        except asyncpg.PostgresError:
            pass
        after = await test_db_pool.fetchval(
            "SELECT COALESCE(last_number, 0) FROM invoice_number_counters WHERE year=2094"
        )
        assert after == before, "rollback must undo the reservation, not leave a gap"
