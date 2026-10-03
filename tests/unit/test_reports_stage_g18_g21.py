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
            [],  # client_pending
            [],  # office_pending
            [],  # attendance_pending_drafts
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
                 0,   # financial_ops_failed_recent (Stage H19)
             ])), \
             patch(
                 "modules.fazle_payroll_engine.review_linkage_repair.classify_all_pending_cash_transactions",
                 new=AsyncMock(return_value=[]),
             ):
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

    async def test_approval_queue_format_stage_g6_14(self):
        """Draft <id> — <Category> — APPROVE / EDIT / REJECT, the exact
        Owner-example format."""
        from modules.reports import _b_business_communication_summary

        with patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[
            [],  # domain_counts
            [],  # pending_drafts
            [{"id": 4099, "recipient": "x", "created_at": None}],  # client_pending
            [{"id": 4100, "recipient": "y", "created_at": None}],  # office_pending
            [{"id": 4101, "recipient": "z", "created_at": None}],  # attendance_pending_drafts
            [{"id": 31, "domain": "cash", "action_type": "business_record_review", "business_reference": "cash_txn:4171", "created_at": None}],  # pending_business_actions
        ])), \
             patch("modules.reports.fetch_val", new=AsyncMock(side_effect=[0, 0, 0, 0, 0, 0, 0, 0])), \
             patch(
                 "modules.fazle_payroll_engine.review_linkage_repair.classify_all_pending_cash_transactions",
                 new=AsyncMock(return_value=[{
                     "txn_id": 4171, "txn_ref": "abc123def456", "employee_name_raw": "X",
                     "amount": 500, "payout_method": "cash", "created_at": None,
                     "classification": "VALID_PENDING_REVIEWABLE",
                 }]),
             ):
            payload = await _b_business_communication_summary(hours=12)

        queue = payload["approval_queue"]
        assert "Draft 4099 — Client — APPROVE / EDIT / REJECT" in queue
        assert "Draft 4100 — Office/Internal — APPROVE / EDIT / REJECT" in queue
        assert "Draft 4101 — Attendance — APPROVE / EDIT / REJECT" in queue
        assert any("Proposal 31" in item and "cash" in item for item in queue)
        assert any("abc123def456" in item and "CASH APPROVE" in item for item in queue)
        assert payload["summary"]["approval_queue_count"] == 5
        assert payload["summary"]["client_pending_drafts"] == 1
        assert payload["summary"]["office_internal_pending_drafts"] == 1

    async def test_legacy_unexecutable_proposal_flagged_stage_h5_14(self):
        """A proposal whose business_reference isn't a recognized typed
        kind (e.g. proposal #29's bare conversation_key with no prefix)
        must be flagged [LEGACY] in the approval queue, not presented as
        equally actionable as a typed one."""
        from modules.reports import _b_business_communication_summary

        with patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[
            [],  # domain_counts
            [],  # pending_drafts
            [],  # client_pending
            [],  # office_pending
            [],  # attendance_pending_drafts
            [{"id": 29, "domain": "recruitment", "action_type": "business_reply_draft", "business_reference": "8801617239992:bridge3", "created_at": None}],
        ])), \
             patch("modules.reports.fetch_val", new=AsyncMock(side_effect=[0, 0, 0, 0, 0, 0, 0, 0])), \
             patch(
                 "modules.fazle_payroll_engine.review_linkage_repair.classify_all_pending_cash_transactions",
                 new=AsyncMock(return_value=[]),
             ):
            payload = await _b_business_communication_summary(hours=12)

        queue = payload["approval_queue"]
        assert any("Proposal 29" in item and "LEGACY" in item for item in queue)
        assert payload["summary"]["pending_business_proposals_legacy_unexecutable"] == 1

    async def test_render_text_shows_financial_anomalies_section_stage_h20(self):
        from modules.reports import render_text
        payload = {
            "report": "system_observation", "args": {},
            "summary": {"financial_anomaly_count": 1},
            "financial_anomalies": [{"kind": "old_pending_cash_txn", "txn_ref": "abc123def456", "age_days": 9.5}],
            "rows": [],
        }
        text = render_text(payload)
        assert "Financial anomalies" in text
        assert "abc123def456" in text

    async def test_render_text_shows_approval_queue_section(self):
        from modules.reports import render_text
        payload = {
            "report": "business_communication_summary", "args": {},
            "summary": {"approval_queue_count": 1},
            "approval_queue": ["Draft 4099 — Client — APPROVE / EDIT / REJECT"],
            "rows": [],
        }
        text = render_text(payload)
        assert "Draft 4099 — Client — APPROVE / EDIT / REJECT" in text
        assert "message_body" not in text


class TestSystemObservationBuilder:
    @pytest.fixture(autouse=True)
    def _no_pending_cash_by_default(self):
        """Stage H.5: _b_system_observation now also calls
        classify_all_pending_cash_transactions() for the
        financial_provenance_health signal -- default it to "no pending
        cash at all" for every test in this class unless a test overrides
        it, so the pre-existing tests below don't need to know about it."""
        with patch(
            "modules.fazle_payroll_engine.review_linkage_repair.classify_all_pending_cash_transactions",
            new=AsyncMock(return_value=[]),
        ):
            yield

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
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], [], [], []])):
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
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], [], [], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["priority"] == "TRANSIENT"
        assert payload["summary"]["action_required_probes"] == []

    async def test_db_or_bridge_degradation_is_action_required(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "critical", "probes": {"db": {"status": "critical"}, "cpu": {"status": "ok"}}}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], [], [], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["priority"] == "ACTION_REQUIRED"
        assert "db" in payload["summary"]["action_required_probes"]

    async def test_healthy_system_reports_healthy_priority(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok"}}}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], [], [], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["priority"] == "HEALTHY"

    async def test_critical_open_incident_forces_critical_priority(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok"}}}
        incident_rows = [{"severity": "critical", "source": "guardian", "title": "fazle_core_unreachable", "n": 1}]
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], incident_rows, [], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["priority"] == "CRITICAL"

    async def test_financial_anomalies_surfaced_read_only_stage_h20(self):
        """Stage H20: old pending cash txns and duplicate-looking pending
        cash txns are surfaced as evidence, with no fix/reject/reverse
        taken automatically (test_read_only_no_execute_calls above already
        proves the builder writes nothing)."""
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}}}
        old_txn = {"id": 1, "txn_ref": "old123", "employee_name_raw": "A", "amount": 100, "age_days": 9.5}
        dup = {"employee_id": 5, "amount": 100, "txn_date": None, "n": 2, "txn_refs": ["a", "b"]}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], [], [old_txn], [dup]])):
            payload = await _b_system_observation()
        assert payload["summary"]["financial_anomaly_count"] == 2
        kinds = {a["kind"] for a in payload["financial_anomalies"]}
        assert kinds == {"old_pending_cash_txn", "duplicate_looking_pending_cash"}

    async def test_financial_provenance_health_alert_on_all_orphaned_stage_h5_20(self):
        """H5.20: pending cash > 0 AND reviewable/direct == 0 must alert
        and escalate priority -- the exact 27/27 situation Stage H found
        live."""
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}}}
        orphans = [{"classification": "TEST_ARTIFACT_SUSPECT"} for _ in range(4)]
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], [], [], []])), \
             patch(
                 "modules.fazle_payroll_engine.review_linkage_repair.classify_all_pending_cash_transactions",
                 new=AsyncMock(return_value=orphans),
             ):
            payload = await _b_system_observation()
        health = payload["summary"]["financial_provenance_health"]
        assert health["alert"] is True
        assert health["reviewable_or_direct"] == 0
        assert payload["summary"]["priority"] == "ACTION_REQUIRED"

    async def test_financial_provenance_health_no_alert_when_reviewable(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}}}
        rows = [{"classification": "VALID_PENDING_REVIEWABLE"} for _ in range(5)]
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[], [], [], []])), \
             patch(
                 "modules.fazle_payroll_engine.review_linkage_repair.classify_all_pending_cash_transactions",
                 new=AsyncMock(return_value=rows),
             ):
            payload = await _b_system_observation()
        assert payload["summary"]["financial_provenance_health"]["alert"] is False
        assert payload["summary"]["priority"] == "HEALTHY"

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
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[stale_row], [], [], []])):
            payload = await _b_system_observation()
        assert payload["summary"]["stale_job_count"] == 0
        assert payload["summary"]["obsolete_job_count"] == 1

    async def test_currently_registered_stale_job_is_stale_and_action_required(self):
        from modules.reports import _b_system_observation
        fake_health = {"status": "ok", "probes": {"db": {"status": "ok"}, "cpu": {"status": "ok"}}}
        stale_row = {"job_name": "business_summary_report", "last_status": "ok", "last_run_at": None, "run_count": 1, "seconds_since_last_run": 999999}
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler.list_job_names", return_value=["business_summary_report"]), \
             patch("modules.reports.fetch_all", new=AsyncMock(side_effect=[[stale_row], [], [], []])):
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
