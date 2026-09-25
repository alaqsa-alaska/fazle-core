"""Stage G8/G27 (2026-08-28) -- confirms the existing
modules.admin_commands._cmd_approve()'s idempotency guarantee (Repeated
APPROVE must not send twice), traced this stage, not modified. This is a
regression proof for a pre-existing property, added because Stage G27
explicitly lists it as a required test and the existing
test_admin_draft_review_workflow.py suite didn't have one named for this
specific case (it does cover the equivalent reject case,
test_reject_already_sent_returns_warning)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestRepeatedApproveIsIdempotent:
    async def test_second_approve_on_already_sent_draft_does_not_resend(self):
        from modules.admin_commands import _cmd_approve

        with patch("modules.admin_commands.fetch_one", new=AsyncMock(return_value={
            "id": 5, "status": "sent", "recipient": "8801700000000",
            "reply_text": "already delivered", "source": "bridge2", "intent": "unknown",
        })), \
             patch("modules.admin_commands.execute", new=AsyncMock()) as exec_mock:
            result = await _cmd_approve(5, "8801880446111")

        assert "ইতিমধ্যে" in result or "already" in result.lower()
        # No status/sent_at UPDATE, no bridge.send() attempt -- the early
        # return happens before either.
        exec_mock.assert_not_awaited()

    async def test_already_approved_status_is_also_refused_not_resent(self):
        """status='approved' is NOT in the allowed re-entry set
        ('pending', None, 'edited') either -- confirmed by reading
        _cmd_approve's own condition -- so a second APPROVE call after
        the first already flipped status to 'approved' is refused the
        same way as 'sent', never triggering a second send attempt."""
        from modules.admin_commands import _cmd_approve

        row = {
            "id": 5, "status": "approved", "recipient": "8801700000000",
            "reply_text": "text", "source": "bridge2", "intent": "unknown",
        }
        with patch("modules.admin_commands.fetch_one", new=AsyncMock(return_value=row)), \
             patch("modules.admin_commands.execute", new=AsyncMock()) as exec_mock:
            result = await _cmd_approve(5, "8801880446111")
        assert "ইতিমধ্যে" in result or "already" in result.lower()
        exec_mock.assert_not_awaited()

    async def test_pending_draft_is_allowed_to_queue_once(self):
        """Contrast case: a genuinely pending draft is queued once;
        approval is not delivery."""
        from modules.admin_commands import _cmd_approve

        row = {
            "id": 5, "status": "pending", "recipient": "8801700000000",
            "reply_text": "text", "source": "bridge2", "intent": "unknown",
        }
        with patch("modules.admin_commands.fetch_one", new=AsyncMock(return_value=row)), \
             patch("modules.admin_commands.execute", new=AsyncMock()) as exec_mock:
            result = await _cmd_approve(5, "8801880446111")
        assert "কিউতে রাখা হয়েছে" in result
        assert exec_mock.await_count >= 1


class TestRejectIdempotencyAlreadyCovered:
    def test_existing_coverage_pointer(self):
        """No new test needed here -- test_admin_draft_review_workflow.py
        ::TestRejectFlow::test_reject_already_sent_returns_warning already
        proves repeated/late REJECT is harmless (Stage G10's requirement).
        This is a documentation-only marker, not a duplicate assertion."""
        assert True
