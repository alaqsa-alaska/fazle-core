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
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], []])):
            payload = await _b_system_observation()
        assert payload["report"] == "system_observation"
        assert payload["summary"]["overall_status"] == "ok"
        assert payload["summary"]["degraded_probe_count"] == 1
        assert "cpu" in payload["summary"]["degraded_probes"]

    async def test_cpu_only_degradation_is_transient_not_action_required(self):
        """Stage G5.15: known ambient CPU spikes must not dominate/
        escalate the report on their own."""
        from modules.reports import _b_system_observation
        fake_health = {"status": "critical", "probes": {"db": {"status": "ok"}, "cpu": {"status": "critical", "load_pct": 150}}}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["priority"] == "TRANSIENT"
        assert payload["summary"]["action_required_probes"] == []

    async def test_db_or_bridge_degradation_is_action_required(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "critical", "probes": {"db": {"status": "critical"}, "cpu": {"status": "ok"}}}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["priority"] == "ACTION_REQUIRED"
        assert "db" in payload["summary"]["action_required_probes"]

    async def test_healthy_system_reports_healthy_priority(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok"}}}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["priority"] == "HEALTHY"

    async def test_critical_open_incident_forces_critical_priority(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok"}}}
        incident_rows = [{"severity": "critical", "source": "guardian", "title": "fazle_core_unreachable", "n": 1}]
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], incident_rows])):
            payload = await _b_system_observation()
        assert payload["summary"]["priority"] == "CRITICAL"

    async def test_job_not_in_current_registry_is_obsolete_not_stale(self):
        """Stage G5.11/G5.15: a job whose code was removed (found this
        stage: gap_scan_daily/hourly, dlq_divergence_guard,
        escort_draft_cleanup, hermes_periodic_review) must not be
        conflated with a currently-registered job that has simply gone
        quiet."""
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok"}}}
        stale_row = {"job_name": "gap_scan_daily", "last_status": "ok", "last_run_at": None, "run_count": 9, "seconds_since_last_run": 999999}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=["business_summary_report"]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[stale_row], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["stale_job_count"] == 0
        assert payload["summary"]["obsolete_job_count"] == 1

    async def test_currently_registered_stale_job_is_stale_and_action_required(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok"}}}
        stale_row = {"job_name": "business_summary_report", "last_status": "ok", "last_run_at": None, "run_count": 1, "seconds_since_last_run": 999999}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=["business_summary_report"]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[stale_row], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["stale_job_count"] == 1
        assert payload["summary"]["priority"] == "ACTION_REQUIRED"


class TestSchedulerRegistrationUsesOneJobId:
    """Regression for a real bug caught live during this stage's own
    deploy verification: registering each twice-daily job under two
    separate APScheduler ids ("..._am"/"..._pm") made
    GET /scheduler/status (modules.scheduler._next_run_for, which looks
    up _scheduler.get_job(bare_registry_name)) always report
    next_run_at=null for both new jobs, even though they were still
    genuinely scheduled to fire. Fixed by using one add_job() call per
    job with a comma-separated CronTrigger hour string."""

    def test_single_add_job_call_per_stage_g_report_job(self):
        import inspect
        import modules.scheduler as sch
        src = inspect.getsource(sch)
        assert src.count('id="business_summary_report"') == 1
        assert 'id="business_summary_report_am"' not in src
        assert 'id="business_summary_report_pm"' not in src
        assert src.count('id="system_observation_report"') == 1
        assert 'id="system_observation_report_am"' not in src
        assert 'id="system_observation_report_pm"' not in src

    def test_cron_trigger_fires_twice_daily(self):
        from apscheduler.triggers.cron import CronTrigger
        import datetime
        t = CronTrigger(hour="9,21", minute=0)
        now = datetime.datetime(2026, 8, 28, 0, 0, tzinfo=datetime.timezone.utc)
        first = t.get_next_fire_time(None, now)
        second = t.get_next_fire_time(first, first)
        assert first.hour == 9
        assert second.hour == 21
        assert second.date() == first.date()


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
