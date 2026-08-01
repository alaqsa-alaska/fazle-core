"""
Unit tests — modules.admin_commands dispatch for the Step 5 (2026-08-01)
escort-confirmation-review command: "<id> Type: APPROVE|EDIT|REJECT".

DB-independent: mocks rbac and the modules.escort handler functions
directly, verifying process_admin_command() routes to the right one with
the right arguments (not the handlers' own internal DB logic, covered in
tests/unit/test_escort_confirmation_review_draft.py).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_RBAC_ALLOW = {"allowed": True, "required_role": "admin", "admin": True}


class TestEscortReviewDispatch:

    async def test_approve_routes_correctly(self):
        from modules.admin_commands import process_admin_command

        with patch("modules.rbac.check_permission", new=AsyncMock(return_value=_RBAC_ALLOW)), \
             patch("modules.rbac.record_audit", new=AsyncMock()), \
             patch("modules.escort.approve_escort_confirmation_review", new=AsyncMock(return_value="✅ approved")) as mock_approve:
            reply = await process_admin_command("321 Type: APPROVE", "8801880446111")

        mock_approve.assert_awaited_once_with(321, "8801880446111")
        assert reply == "✅ approved"

    async def test_reject_routes_correctly(self):
        from modules.admin_commands import process_admin_command

        with patch("modules.rbac.check_permission", new=AsyncMock(return_value=_RBAC_ALLOW)), \
             patch("modules.rbac.record_audit", new=AsyncMock()), \
             patch("modules.escort.reject_escort_confirmation_review", new=AsyncMock(return_value="🚫 rejected")) as mock_reject:
            reply = await process_admin_command("321 Type: REJECT", "8801880446111")

        mock_reject.assert_awaited_once_with(321, "8801880446111")
        assert reply == "🚫 rejected"

    async def test_edit_routes_with_corrected_text(self):
        from modules.admin_commands import process_admin_command

        corrected = "Mother Vessel: MV X\nLighter Vessel: Y\nEscort Mobile: 01700000000"
        with patch("modules.rbac.check_permission", new=AsyncMock(return_value=_RBAC_ALLOW)), \
             patch("modules.rbac.record_audit", new=AsyncMock()), \
             patch("modules.escort.edit_escort_confirmation_review", new=AsyncMock(return_value="✏️ edited")) as mock_edit:
            reply = await process_admin_command(f"321 Type: EDIT {corrected}", "8801880446111")

        mock_edit.assert_awaited_once_with(321, corrected, "8801880446111")
        assert reply == "✏️ edited"

    async def test_edit_without_text_rejected_before_reaching_handler(self):
        from modules.admin_commands import process_admin_command

        with patch("modules.rbac.check_permission", new=AsyncMock(return_value=_RBAC_ALLOW)), \
             patch("modules.rbac.record_audit", new=AsyncMock()), \
             patch("modules.escort.edit_escort_confirmation_review", new=AsyncMock()) as mock_edit:
            reply = await process_admin_command("321 Type: EDIT", "8801880446111")

        mock_edit.assert_not_awaited()
        assert "সম্পূর্ণ" in reply or "complete" in reply.lower() or "❌" in reply

    async def test_rbac_denied_blocks_dispatch(self):
        from modules.admin_commands import process_admin_command

        with patch("modules.rbac.check_permission", new=AsyncMock(
            return_value={"allowed": False, "required_role": "admin", "reason": "insufficient"}
        )), \
             patch("modules.rbac.record_audit", new=AsyncMock()), \
             patch("modules.escort.approve_escort_confirmation_review", new=AsyncMock()) as mock_approve:
            reply = await process_admin_command("321 Type: APPROVE", "8801900000000")

        mock_approve.assert_not_awaited()
        assert "⛔" in reply
