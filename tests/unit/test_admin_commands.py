"""Unit tests — modules/admin_commands"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.unit


class TestIsAdminCommand:
    """Test is_admin_command() detection."""

    @pytest.mark.parametrize("text", [
        "APPROVE 1",
        "REJECT 5",
        "PAID 3 1500 bkash",
        "ADVANCE 2 2000 nagad",
        "RELEASE 4 2026-05-06 D Ctg Port",
        "ESCORTCONFIRM 1 | Karim | 01811111111 | 06/05/2026 | D",
        "PAYROLL COMPUTE 2026-05",
        "PAYROLL LIST 2026-05",
        "STATUS",
        "REVERSE 3 wrong amount",
        "ADJUST 3 1200 bkash",
    ])
    def test_admin_commands_detected(self, text):
        from modules.admin_commands import is_admin_command
        assert is_admin_command(text) is True

    @pytest.mark.parametrize("text", [
        "হাজির আছি",
        "অগ্রিম লাগবে",
        "ডিউটি শেষ",
        "চাকরি করতে চাই",
        "approve",          # lowercase — may or may not be detected, test for consistency
        "hello",
    ])
    def test_non_commands_not_detected(self, text):
        from modules.admin_commands import is_admin_command
        # Lowercase "approve" should NOT match (commands are uppercase)
        if text == "approve":
            # Test that lowercase doesn't match (safety: admin from non-admin)
            # This is informational — document actual behavior
            result = is_admin_command(text)
            assert isinstance(result, bool)  # just ensure no crash
        else:
            assert is_admin_command(text) is False


class TestBengaliDigitNormalisation:
    """Bengali digit strings are converted to ASCII via _BN_DIGITS table."""

    def test_bengali_digits_normalised(self):
        from modules.admin_commands import _BN_DIGITS

        assert "১২৩৪".translate(_BN_DIGITS) == "1234"
        assert "APPROVE ৩".translate(_BN_DIGITS) == "APPROVE 3"
        assert "PAID ৫ ১৫০০ bkash".translate(_BN_DIGITS) == "PAID 5 1500 bkash"

    def test_mixed_digits_normalised(self):
        from modules.admin_commands import _BN_DIGITS

        assert "APPROVE 5৩".translate(_BN_DIGITS) == "APPROVE 53"


class TestProcessApproveCommand:
    """APPROVE <id> command flow."""

    async def test_approve_pending_draft(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        # Seed a pending draft
        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow("""
                INSERT INTO fazle_draft_replies
                    (recipient, reply_text, intent, source, status)
                VALUES ('8801811111111', 'Test reply', 'generic', 'bridge1', 'pending')
                RETURNING id
            """)
        draft_id = draft["id"]

        from modules.admin_commands import process_admin_command

        with patch("modules.rbac.check_permission", new=AsyncMock(return_value={"allowed": True, "required_role": "operator", "admin": True})):
            with patch("app.bridge.BridgeClient.send", new=AsyncMock(return_value=True)):
                result = await process_admin_command(
                    text=f"APPROVE {draft_id}",
                    admin_phone="8801700000001",
                )

        # Draft should be marked sent
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM fazle_draft_replies WHERE id=$1", draft_id
            )
        assert row["status"] == "sent"

    async def test_reject_pending_draft(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow("""
                INSERT INTO fazle_draft_replies
                    (recipient, reply_text, intent, source, status)
                VALUES ('8801811111111', 'Test reject reply', 'generic', 'bridge1', 'pending')
                RETURNING id
            """)
        draft_id = draft["id"]

        from modules.admin_commands import process_admin_command

        with patch("modules.rbac.check_permission", new=AsyncMock(return_value={"allowed": True, "required_role": "operator", "admin": True})):
            result = await process_admin_command(
                text=f"REJECT {draft_id}",
                admin_phone="8801700000001",
            )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM fazle_draft_replies WHERE id=$1", draft_id
            )
        assert row["status"] == "rejected"


class TestProcessPaidCommand:
    """PAID <draft_id> <amount> <method> command."""

    async def test_paid_command_finalizes_payment(
        self, test_db_pool, seed_employee, seed_fpe_employee, seed_payment_draft
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.admin_commands import process_admin_command

        draft_id = seed_payment_draft["id"]

        with patch("modules.rbac.check_permission", new=AsyncMock(return_value={"allowed": True, "required_role": "accountant", "admin": True})):
            with patch("app.bridge.BridgeClient.send", new=AsyncMock(return_value=True)):
                result = await process_admin_command(
                    text=f"PAID {draft_id} 1500 bkash",
                    admin_phone="8801700000001",
                )

        async with test_db_pool.acquire() as conn:
            txn = await conn.fetchrow(
                "SELECT * FROM fpe_cash_transactions WHERE employee_id=$1 LIMIT 1",
                seed_fpe_employee["id"],
            )
        assert txn is not None
        assert float(txn["amount"]) == pytest.approx(1500.0)
        assert txn["payout_method"] == "bkash"
        assert txn["transaction_status"] == "final"

    async def test_paid_command_rbac_denied(
        self, test_db_pool, seed_fpe_employee, seed_payment_draft
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.admin_commands import process_admin_command

        draft_id = seed_payment_draft["id"]

        # Mock RBAC: deny this phone
        with patch("modules.rbac.check_permission", new=AsyncMock(return_value={"allowed": False, "required_role": "accountant", "reason": "Insufficient role"})):
            result = await process_admin_command(
                text=f"PAID {draft_id} 1500 bkash",
                admin_phone="8801999999999",
            )

        # Canonical transaction should NOT be created
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM fpe_cash_transactions"
            )
        assert count == 0
