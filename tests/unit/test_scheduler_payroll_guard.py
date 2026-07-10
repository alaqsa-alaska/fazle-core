"""Unit tests — modules/scheduler::job_daily_payroll guard (Owner decision, 2026-07-11)"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestJobDailyPayrollGuard:
    async def test_guarded_by_default_no_authoritative_call(self, test_db_pool):
        """office_payroll_authoritative_enabled defaults to False -- the job
        must run dry-run-only and must never call compute_all_for_period_
        office_only (the authoritative, payroll-run-creating path)."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.scheduler import job_daily_payroll

        with patch("modules.payroll.compute_all_for_period_office_only", new=AsyncMock()) as authoritative_call:
            result = await job_daily_payroll()

        authoritative_call.assert_not_called()
        assert result["mode"] == "dry_run_guarded"

    async def test_flag_enabled_calls_authoritative_path(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from app.config import get_settings
        from modules.scheduler import job_daily_payroll

        settings = get_settings()
        with patch.object(settings, "office_payroll_authoritative_enabled", True):
            with patch(
                "modules.payroll.compute_all_for_period_office_only",
                new=AsyncMock(return_value={"ok": True, "created": 0, "existing": 0, "failed": 0, "skipped_ineligible": 0, "total": 0, "run_ids": []}),
            ) as authoritative_call:
                result = await job_daily_payroll()

        authoritative_call.assert_called_once()
        assert result["status"] == "ok"
        assert "result" in result
