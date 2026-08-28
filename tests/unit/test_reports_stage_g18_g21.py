"""Stage G18/G21/G27 (2026-08-28) -- new report builders in modules.reports:
business_communication_summary, system_observation."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestBusinessSummaryBuilder:
    async def test_returns_bounded_summary_not_raw_bodies(self):
        from modules.reports import _b_business_communication_summary

        with patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[
            [{"category": "recruitment", "contacts": 5}],  # domain_counts
            [],  # pending_drafts
            [],  # pending_business_actions
        ])), \
             patch("modules.reports.fetch_val", new=AsyncMock(side_effect=[
                 2,   # complaints
                 3,   # office_group_msgs
                 10,  # replies_sent
                 7,   # pending_drafts_total
                 0,   # attendance_needs_action
                 4,   # cash_needs_action
                 1,   # escort_needs_action
             ])):
            payload = await _b_business_communication_summary(hours=12)

        assert payload["report"] == "business_communication_summary"
        assert payload["summary"]["by_category"] == {"recruitment": 5}
        assert payload["summary"]["complaints"] == 2
        assert payload["summary"]["office_group_messages"] == 3
        # No raw message_body key anywhere in the payload.
        assert "message_body" not in str(payload["rows"])

    async def test_registered_in_builders(self):
        from modules.reports import _BUILDERS
        assert "business_communication_summary" in _BUILDERS
        assert "system_observation" in _BUILDERS


class TestSystemObservationBuilder:
    async def test_read_only_no_execute_calls(self):
        """Stage G19: system monitoring must remain READ/REPORT --
        structural proof the builder never calls execute()/writes
        anything."""
        import inspect
        from modules.reports import _b_system_observation
        src = inspect.getsource(_b_system_observation)
        assert "execute(" not in src
        assert "INSERT INTO" not in src
        assert "UPDATE " not in src
        assert "DELETE FROM" not in src

    async def test_returns_health_and_job_summary(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "critical", "load_pct": 90}}}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], []])):
            payload = await _b_system_observation()
        assert payload["report"] == "system_observation"
        assert payload["summary"]["overall_status"] == "ok"
        assert payload["summary"]["degraded_probe_count"] == 1
        assert "cpu" in payload["summary"]["degraded_probes"]


class TestScheduledJobsNeverExecuteAutoFix:
    def test_business_summary_job_only_reports(self):
        import inspect
        from modules.reports import job_business_summary_report
        src = inspect.getsource(job_business_summary_report)
        assert "execute(" not in src

    def test_system_observation_job_only_reports(self):
        import inspect
        from modules.reports import job_system_observation_report
        src = inspect.getsource(job_system_observation_report)
        assert "execute(" not in src
        assert "restart" not in src.lower()
        assert "migrate" not in src.lower()
