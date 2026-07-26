"""Unit tests — modules/scheduler::job_dlq_alert idempotency (day-based cooldown)

Regression: the alert previously keyed its idempotency_key off the live
actionable/pending counts (f"dlq-alert-actionable-{a}-pending-{p}"). The job
runs every 15 minutes, and those counts fluctuate constantly, so any
fluctuation produced a "new" idempotency key -- effectively unlimited admin
spam during a flapping DLQ, with no real cooldown. The key is now day-based
so fluctuating counts within the same day share one key (deduped by
outbound.enqueue's own idempotency handling).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestJobDlqAlertIdempotency:
    async def test_key_is_day_based_not_count_based(self):
        from modules.scheduler import job_dlq_alert

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.dlq_count", new=AsyncMock(return_value=5)), \
             patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=2)), \
             patch("modules.outbound.pending_count", new=AsyncMock(return_value=3)), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", mock_enqueue):
            await job_dlq_alert()

        mock_enqueue.assert_called_once()
        key = mock_enqueue.call_args.kwargs["idempotency_key"]
        assert key.startswith("dlq-alert-")
        assert "actionable" not in key
        assert "pending" not in key

    async def test_same_day_different_counts_produce_same_key(self):
        """Three runs on the same day with different actionable/pending
        counts must produce the SAME idempotency_key -- proving count
        fluctuation alone can no longer mint a fresh alert."""
        from modules.scheduler import job_dlq_alert

        keys = []

        async def _capture_enqueue(*args, **kwargs):
            keys.append(kwargs["idempotency_key"])
            return 1

        with patch("modules.outbound.dlq_count", new=AsyncMock(return_value=2)), \
             patch("modules.outbound.actionable_dlq_count", new=AsyncMock(side_effect=[2, 5, 8])), \
             patch("modules.outbound.pending_count", new=AsyncMock(side_effect=[3, 0, 0])), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", side_effect=_capture_enqueue):
            await job_dlq_alert()
            await job_dlq_alert()
            await job_dlq_alert()

        assert len(set(keys)) == 1, f"expected one stable key across fluctuating counts, got {keys}"

    async def test_first_alert_of_day_still_sends_when_actionable_positive(self):
        from modules.scheduler import job_dlq_alert

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.dlq_count", new=AsyncMock(return_value=1)), \
             patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=1)), \
             patch("modules.outbound.pending_count", new=AsyncMock(return_value=0)), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", mock_enqueue):
            result = await job_dlq_alert()

        assert result["alerted"] is True
        mock_enqueue.assert_called_once()

    async def test_zero_actionable_no_alert(self):
        from modules.scheduler import job_dlq_alert

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.dlq_count", new=AsyncMock(return_value=0)), \
             patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=0)), \
             patch("modules.outbound.pending_count", new=AsyncMock(return_value=0)), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", mock_enqueue):
            result = await job_dlq_alert()

        assert result["alerted"] is False
        mock_enqueue.assert_not_called()
