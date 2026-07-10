"""
Tests for fazle_payroll_engine.accounting — immutable transaction engine.

Uses asyncpg directly against the test DB (follows conftest.py pattern).
Tests: create, idempotency, reversal, ledger upsert.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from decimal import Decimal
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

from modules.fazle_payroll_engine.models import (
    TransactionCreateRequest,
    PayoutMethod,
    TxnCategory,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def _txn_req(**overrides) -> TransactionCreateRequest:
    defaults = dict(
        fpe_wa_message_id=None,
        employee_id=999,
        employee_name_raw="TestEmp",
        amount=Decimal("5000"),
        payout_phone="01712345678",
        payout_method=PayoutMethod.bkash,
        txn_date=date(2026, 3, 31),
        txn_category=TxnCategory.salary,
        source_message_text="test",
        accounting_period="2026-03",
        created_by="test",
    )
    defaults.update(overrides)
    return TransactionCreateRequest(**defaults)


# ── Unit tests (no DB) — test helper functions ────────────────────────────

class TestTxnRef:
    """Verify txn_ref generation is deterministic and stable."""

    def test_same_inputs_give_same_ref(self):
        """Identical request parameters must always produce the same txn_ref."""
        import hashlib

        def make_ref(wa_id, emp_id, amount, period, method):
            raw = f"{wa_id}:{emp_id}:{amount}:{period}:{method}"
            h = hashlib.sha256(raw.encode()).hexdigest()[:16]
            return f"fpe-{h}"

        ref1 = make_ref("msg-001", 1, "5000", "2026-03", "bkash")
        ref2 = make_ref("msg-001", 1, "5000", "2026-03", "bkash")
        assert ref1 == ref2
        assert ref1.startswith("fpe-")
        assert len(ref1) == 20  # "fpe-" + 16 chars

    def test_different_amounts_different_refs(self):
        import hashlib

        def make_ref(wa_id, emp_id, amount, period, method):
            raw = f"{wa_id}:{emp_id}:{amount}:{period}:{method}"
            return hashlib.sha256(raw.encode()).hexdigest()[:16]

        ref1 = make_ref("msg-001", 1, "5000", "2026-03", "bkash")
        ref2 = make_ref("msg-001", 1, "6000", "2026-03", "bkash")
        assert ref1 != ref2


class TestAccountingPeriodDerivation:
    """accounting_period should be auto-derived from txn_date if not supplied."""

    def test_period_derived_from_date(self):
        """YYYY-MM derived from txn_date."""
        txn_date = date(2026, 3, 15)
        period = f"{txn_date.year}-{txn_date.month:02d}"
        assert period == "2026-03"

    def test_period_december(self):
        txn_date = date(2025, 12, 31)
        period = f"{txn_date.year}-{txn_date.month:02d}"
        assert period == "2025-12"


# ── Integration-style unit tests with mocked DB ───────────────────────────

class TestCreateTransaction:
    """Test create_transaction() with mocked DB helpers."""

    @pytest.mark.asyncio
    async def test_creates_transaction_and_returns_row(self):
        """create_transaction should insert row and return TransactionRow."""
        from modules.fazle_payroll_engine.accounting import create_transaction

        txn_row = {
            "id": 42,
            "txn_ref": "fpe-abcdef0123456789",
            "employee_id": 999,
            "employee_name_raw": "TestEmp",
            "amount": Decimal("5000"),
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "txn_date": date(2026, 3, 31),
            "txn_category": "salary",
            "accounting_period": "2026-03",
            "is_reversal": False,
            "created_at": datetime.now(timezone.utc),
        }

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 42
        mock_conn.execute.return_value = None

        # fetch_one: first call (idempotency check) returns None, second call returns row
        with patch("modules.fazle_payroll_engine.accounting.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("modules.fazle_payroll_engine.accounting.db_conn") as mock_db_conn, \
             patch("modules.fazle_payroll_engine.accounting._upsert_ledger", new_callable=AsyncMock):

            mock_fetch_one.side_effect = [None, txn_row]  # no existing, then return new row
            mock_db_conn.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db_conn.return_value.__aexit__ = AsyncMock(return_value=False)

            req = _txn_req()
            result = await create_transaction(req)

            assert result.id == 42
            assert result.amount == Decimal("5000")
            assert result.txn_ref.startswith("fpe-")
            assert mock_conn.fetchval.called  # INSERT was executed

    @pytest.mark.asyncio
    async def test_idempotent_on_duplicate_txn_ref(self):
        """If txn_ref already exists, return existing row without double-inserting."""
        from modules.fazle_payroll_engine.accounting import create_transaction

        existing_row = {
            "id": 10,
            "txn_ref": "fpe-existing12345678",
            "employee_id": 999,
            "employee_name_raw": "TestEmp",
            "amount": Decimal("5000"),
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "txn_date": date(2026, 3, 31),
            "txn_category": "salary",
            "accounting_period": "2026-03",
            "is_reversal": False,
            "created_at": datetime.now(timezone.utc),
        }

        with patch("modules.fazle_payroll_engine.accounting.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("modules.fazle_payroll_engine.accounting.db_conn") as mock_db_conn:
            mock_fetch_one.return_value = existing_row  # idempotency hit → return immediately
            req = _txn_req()
            result = await create_transaction(req)
            assert result.id == 10
            assert not mock_db_conn.called  # no INSERT


class TestReverseTransaction:
    """Test reverse_transaction() creates a negating row."""

    @pytest.mark.asyncio
    async def test_reversal_creates_negative_amount_row(self):
        from modules.fazle_payroll_engine.accounting import reverse_transaction

        original = {
            "id": 10,
            "txn_ref": "fpe-original1234567",
            "fpe_wa_message_id": None,
            "employee_id": 999,
            "employee_name_raw": "TestEmp",
            "employee_id_phone": "01712345678",
            "employee_phone": "01712345678",
            "amount": Decimal("5000"),
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "txn_date": date(2026, 3, 31),
            "txn_category": "salary",
            "accounting_period": "2026-03",
            "is_reversal": False,
            "reversal_of_txn_id": None,
            "source_message_text": "original",
            "created_by": "fpe_engine",
            "created_at": datetime.now(timezone.utc),
        }
        reversal_row = {
            **original,
            "id": 11,
            "txn_ref": "REV-fpe-original1234567",
            "amount": Decimal("-5000"),
            "is_reversal": True,
            "reversal_of_txn_id": 10,
            "source_message_text": "REVERSAL: test reason",
            "created_by": "admin",
        }

        mock_conn = AsyncMock()
        mock_conn.fetchval.return_value = 11
        mock_conn.execute.return_value = None

        with patch("modules.fazle_payroll_engine.accounting.fetch_one", new_callable=AsyncMock) as mock_fetch_one, \
             patch("modules.fazle_payroll_engine.accounting.db_conn") as mock_db_conn, \
             patch("modules.fazle_payroll_engine.accounting._upsert_ledger", new_callable=AsyncMock):
            # orig lookup → None for reversal idempotency check → final row
            mock_fetch_one.side_effect = [original, None, reversal_row]
            mock_db_conn.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_db_conn.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await reverse_transaction(10, "test reason", "admin")
            assert result.is_reversal is True
            assert result.amount == Decimal("-5000")

    @pytest.mark.asyncio
    async def test_cannot_reverse_nonexistent_transaction(self):
        from modules.fazle_payroll_engine.accounting import reverse_transaction

        with patch("modules.fazle_payroll_engine.accounting.fetch_one", new_callable=AsyncMock) as mock_fetch_one:
            mock_fetch_one.return_value = None
            with pytest.raises(ValueError, match="not found"):
                await reverse_transaction(9999, "reason", "admin")
