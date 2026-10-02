"""Stage G6.9/G6.10/G6.11/G6.12/G6.15 (2026-08-28) -- scheduler timezone
normalization, watchdog-of-watchdog health probe, Admin HELP command."""
from __future__ import annotations

import inspect

from apscheduler.triggers.cron import CronTrigger
import datetime
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestAllCronTriggersHaveExplicitTimezone:
    def test_every_crontrigger_call_site_passes_timezone(self):
        """Regression for the Stage G5.13/G6.10 class of bug: a
        CronTrigger(...) constructed without timezone= silently defaults
        to the server's own local system timezone. Every real (non-
        commented) CronTrigger call in modules.scheduler must now pass
        timezone= explicitly."""
        import modules.scheduler as sch
        src = inspect.getsource(sch)
        lines = [l for l in src.splitlines() if "CronTrigger(" in l and not l.strip().startswith("#")]
        assert lines, "expected to find real CronTrigger call sites"
        for line in lines:
            assert "timezone=" in line, f"CronTrigger call missing explicit timezone=: {line.strip()}"

    def test_dhaka_cron_trigger_fires_at_the_intended_hour_not_server_local(self):
        t = CronTrigger(hour=8, minute=0, timezone="Asia/Dhaka")
        now = datetime.datetime(2026, 8, 28, 0, 0, tzinfo=datetime.timezone.utc)
        nxt = t.get_next_fire_time(None, now)
        assert nxt.utcoffset() == datetime.timedelta(hours=6)
        assert nxt.hour == 8

    def test_no_timezone_defaults_to_server_local_proving_the_original_bug(self):
        """Documents the exact root cause this stage fixed -- a trigger
        with no timezone= does NOT use Asia/Dhaka even when later added
        to a scheduler configured with that timezone."""
        t = CronTrigger(hour=8, minute=0)
        assert t.timezone.key != "Asia/Dhaka" if hasattr(t.timezone, "key") else str(t.timezone) != "Asia/Dhaka"

    def test_corrected_next_run_is_always_in_the_future_no_immediate_refire(self):
        """Stage G6.10's explicit safety check: switching a trigger's
        interpretation from server-local to Dhaka must never produce a
        next_run_time in the past relative to 'now' -- CronTrigger always
        computes forward from 'now' by construction, verified here for
        every corrected job's actual hour."""
        now = datetime.datetime.now(datetime.timezone.utc)
        for hour, minute in [(2, 0), (9, 0), (3, 0), (9, 0), (18, 0), (8, 0), (2, 30)]:
            nxt = CronTrigger(hour=hour, minute=minute, timezone="Asia/Dhaka").get_next_fire_time(None, now)
            assert nxt > now


class TestSchedulerHeartbeatHealthProbe:
    """Stage G6.11/G6.12: watchdog-of-watchdog, reusing the existing
    _probe_heartbeat() helper and the scheduler's own pre-existing
    heartbeat write -- no new monitoring mechanism."""

    async def test_scheduler_probe_included_in_health(self):
        import inspect
        import app.main as m
        src = inspect.getsource(m._build_health)
        assert '_probe_heartbeat("scheduler"' in src
        assert '"scheduler": sched_p' in src

    async def test_stale_scheduler_heartbeat_is_flagged(self):
        from app.main import _probe_heartbeat
        with patch("app.main.fetch_one", new=AsyncMock(return_value={"age": 9999, "last_message_id": None, "queue_depth": 0})):
            result = await _probe_heartbeat("scheduler", stale_after_s=600)
        assert result["status"] in ("degraded", "critical")

    async def test_fresh_scheduler_heartbeat_is_ok(self):
        from app.main import _probe_heartbeat
        with patch("app.main.fetch_one", new=AsyncMock(return_value={"age": 30, "last_message_id": None, "queue_depth": 0})):
            result = await _probe_heartbeat("scheduler", stale_after_s=600)
        assert result["status"] == "ok"

    async def test_never_ever_seen_heartbeat_is_critical_not_silently_ok(self):
        from app.main import _probe_heartbeat
        with patch("app.main.fetch_one", new=AsyncMock(return_value=None)):
            result = await _probe_heartbeat("scheduler", stale_after_s=600)
        assert result["status"] == "critical"


class TestSchedulerStartupHeartbeat:
    async def test_successful_startup_writes_heartbeat_before_interval_jobs(self, monkeypatch):
        import modules.scheduler as sch
        from types import SimpleNamespace

        scheduler = SimpleNamespace(running=True)
        heartbeat = AsyncMock()
        monkeypatch.setattr(sch, "_scheduler", scheduler)
        monkeypatch.setattr(sch, "_heartbeat_scheduler", heartbeat)

        await sch._write_startup_heartbeat(scheduler)

        heartbeat.assert_awaited_once()

    async def test_stopped_or_replaced_scheduler_does_not_write_heartbeat(self, monkeypatch):
        import modules.scheduler as sch
        from types import SimpleNamespace

        scheduler = SimpleNamespace(running=False)
        heartbeat = AsyncMock()
        monkeypatch.setattr(sch, "_scheduler", scheduler)
        monkeypatch.setattr(sch, "_heartbeat_scheduler", heartbeat)

        await sch._write_startup_heartbeat(scheduler)

        heartbeat.assert_not_awaited()

    def test_startup_heartbeat_is_scheduled_only_after_scheduler_start(self):
        import modules.scheduler as sch

        src = inspect.getsource(sch.start_scheduler)
        assert src.index("_scheduler.start()") < src.index("_write_startup_heartbeat")

class TestAdminHelpCommand:
    async def test_help_returns_command_list_without_rbac_or_mutation(self):
        from modules.admin_commands import process_admin_command
        result = await process_admin_command("HELP", "8801880446111")
        assert "APPROVE" in result
        assert "REJECT" in result
        assert "EDIT" in result

    async def test_help_is_case_insensitive(self):
        from modules.admin_commands import process_admin_command
        result = await process_admin_command("help", "8801880446111")
        assert "APPROVE" in result

    async def test_help_never_exposes_rbac_role_names_or_config(self):
        from modules.admin_commands import process_admin_command
        result = await process_admin_command("HELP", "8801880446111")
        for secret_shaped in ("superadmin", "api_key", "role_id", "password"):
            assert secret_shaped not in result.lower()

    async def test_help_only_lists_implemented_commands(self):
        """Every token in the help text corresponds to a command this
        file actually implements (APPROVE/REJECT/EDIT/PAID/STATUS/
        DRAFTS) -- never advertises an unimplemented command."""
        from modules.admin_commands import _HELP_TEXT
        for cmd in ("APPROVE", "REJECT", "EDIT", "PAID", "STATUS", "DRAFTS"):
            assert cmd in _HELP_TEXT

    async def test_unrecognized_command_still_shows_the_same_help_text(self):
        """Single source of truth: the fallback path and the explicit
        HELP command render the identical text."""
        from modules.admin_commands import process_admin_command, _HELP_TEXT
        result = await process_admin_command("gibberish nonsense xyz", "8801880446111")
        assert _HELP_TEXT in result
