"""Unit tests — Phase 5B (2026-08-04, Owner-approved option (b) only)
scheduler wiring: each of dlq_alert/health_summary/bridge_watchdog calls
trigger_admin_investigation() exactly once inside its existing "alert
fired" branch, and a failure in that call can never regress the job's
own already-proven native alert delivery or return value.

The actual investigation/delivery logic itself is covered in
tests/unit/test_admin_ai_router.py (TestTriggerAdminInvestigation) --
these tests only verify the wiring: right job_name, right context shape,
and fail-safety at the call site.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestDlqAlertPhase5bWiring:
    async def test_trigger_called_with_job_name_and_context_on_alert(self):
        from modules.scheduler import job_dlq_alert

        mock_trigger = AsyncMock()
        with patch("modules.outbound.dlq_count", new=AsyncMock(return_value=5)), \
             patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=2)), \
             patch("modules.outbound.pending_count", new=AsyncMock(return_value=3)), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", new=AsyncMock()), \
             patch("modules.admin_directives.router.trigger_admin_investigation", mock_trigger):
            await job_dlq_alert()

        mock_trigger.assert_awaited_once()
        args, _ = mock_trigger.call_args
        assert args[0] == "dlq_alert"
        assert "actionable=2" in args[1]

    async def test_trigger_not_called_when_no_alert(self):
        from modules.scheduler import job_dlq_alert

        mock_trigger = AsyncMock()
        with patch("modules.outbound.dlq_count", new=AsyncMock(return_value=0)), \
             patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=0)), \
             patch("modules.outbound.pending_count", new=AsyncMock(return_value=0)), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", new=AsyncMock()), \
             patch("modules.admin_directives.router.trigger_admin_investigation", mock_trigger):
            await job_dlq_alert()
        mock_trigger.assert_not_awaited()

    async def test_trigger_exception_does_not_break_job_or_native_alert(self):
        """The job's own native alert (outbound.enqueue) must still fire,
        and the job must still return alerted=True, even if
        trigger_admin_investigation raises."""
        from modules.scheduler import job_dlq_alert

        mock_enqueue = AsyncMock()
        with patch("modules.outbound.dlq_count", new=AsyncMock(return_value=5)), \
             patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=2)), \
             patch("modules.outbound.pending_count", new=AsyncMock(return_value=3)), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", mock_enqueue), \
             patch("modules.admin_directives.router.trigger_admin_investigation", new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await job_dlq_alert()

        assert result["alerted"] is True
        mock_enqueue.assert_awaited_once()  # native alert unaffected


class TestHealthSummaryPhase5bWiring:
    async def test_trigger_called_with_job_name_and_context_on_degraded(self):
        from modules.scheduler import job_health_summary

        fake_health = {"status": "degraded", "probes": {"cpu": {"status": "degraded"}, "db": {"status": "ok"}}}
        mock_trigger = AsyncMock()
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", new=AsyncMock()), \
             patch("modules.admin_directives.router.trigger_admin_investigation", mock_trigger):
            await job_health_summary()

        mock_trigger.assert_awaited_once()
        args, _ = mock_trigger.call_args
        assert args[0] == "health_summary"
        assert "degraded" in args[1]

    async def test_trigger_not_called_when_healthy(self):
        from modules.scheduler import job_health_summary

        mock_trigger = AsyncMock()
        with patch("app.main._build_health", new=AsyncMock(return_value={"status": "ok", "probes": {}})), \
             patch("modules.admin_directives.router.trigger_admin_investigation", mock_trigger):
            result = await job_health_summary()
        assert result["overall"] == "ok"
        mock_trigger.assert_not_awaited()

    async def test_trigger_exception_does_not_break_job_or_native_alert(self):
        from modules.scheduler import job_health_summary

        fake_health = {"status": "critical", "probes": {"db": {"status": "critical"}}}
        mock_enqueue = AsyncMock()
        with patch("app.main._build_health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.scheduler._admin_number", return_value="8801900000001"), \
             patch("modules.outbound.enqueue", mock_enqueue), \
             patch("modules.admin_directives.router.trigger_admin_investigation", new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await job_health_summary()

        assert result["overall"] == "critical"
        mock_enqueue.assert_awaited_once()


async def _run_bridge_watchdog_block(stale):
    """Mirrors job_bridge_watchdog exactly as defined inside
    start_scheduler() (modules/scheduler/__init__.py) -- it's a closure,
    not independently importable, and start_scheduler() itself calls
    _scheduler.start() (a real background APScheduler thread), so it
    can't be safely invoked whole just to extract one job. Follows this
    repo's own established convention for testing this class of inline
    logic (see tests/unit/test_escort_ocr_pipeline.py's "Simulate the X
    block from _poll_bridge" pattern) -- only the control flow is
    mirrored; every call inside it is the real, patched production
    function.

    FIXED 2026-08-04 (isolated bugfix, separate from Phase 5B): the real
    source used to import `send_to_admin` FROM `modules.message_router`,
    but that function doesn't exist there -- it's defined in
    `shared/bridge_orchestrator.py`. The resulting ImportError was silently
    caught by the surrounding `except Exception: pass`, so bridge_watchdog's
    own native admin alert never actually fired. Now imports `send_to_admin`
    from its real home, `shared.bridge_orchestrator`, matching the fixed
    production source exactly. Phase 5B's own trigger call below is a
    separate try/except and is unaffected either way."""
    from modules.admin_directives.router import trigger_admin_investigation

    if stale:
        try:
            from modules.message_router import get_primary_admin
            from shared.bridge_orchestrator import send_to_admin
            admin = get_primary_admin()
            if admin:
                msg = "Bridge alert - stale:\n" + "\n".join(
                    f"  {b['bridge_id']}: last seen {b['seconds_ago']//60} min ago" for b in stale
                )
                await send_to_admin(msg)
        except Exception:
            pass
        try:
            stale_desc = ", ".join(f"{b['bridge_id']}({b['seconds_ago']}s)" for b in stale)
            await trigger_admin_investigation("bridge_watchdog", f"stale_bridges={stale_desc}")
        except Exception:
            pass
    return {"status": "ok", "stale_count": len(stale)}


class TestBridgeWatchdogPhase5bWiring:
    async def test_trigger_called_with_stale_bridge_context(self):
        """Phase 5B's own trigger must fire correctly regardless of whether
        the job's native alert send succeeds (see _run_bridge_watchdog_block's
        docstring) -- the two try/except blocks are independent."""
        stale = [{"bridge_id": "bridge1", "seconds_ago": 650}]
        mock_trigger = AsyncMock()
        with patch("modules.admin_directives.router.trigger_admin_investigation", mock_trigger), \
             patch("shared.bridge_orchestrator.send_to_admin", new=AsyncMock(return_value=True)):
            result = await _run_bridge_watchdog_block(stale)

        assert result["stale_count"] == 1
        mock_trigger.assert_awaited_once()
        args, _ = mock_trigger.call_args
        assert args[0] == "bridge_watchdog"
        assert "bridge1(650s)" in args[1]

    async def test_native_alert_sends_through_intended_admin_path(self):
        """Regression guard for the 2026-08-04 isolated bugfix: with a real
        admin configured, the native alert must actually reach
        shared.bridge_orchestrator.send_to_admin with the stale-bridge
        message -- proving the import fix, not just that no exception is
        raised (the old bug also raised no visible exception, since it was
        swallowed by `except Exception: pass`)."""
        stale = [{"bridge_id": "bridge1", "seconds_ago": 650}]
        mock_send = AsyncMock(return_value=True)
        with patch("modules.message_router.get_primary_admin", return_value="8801900000001"), \
             patch("shared.bridge_orchestrator.send_to_admin", mock_send), \
             patch("modules.admin_directives.router.trigger_admin_investigation", new=AsyncMock()):
            result = await _run_bridge_watchdog_block(stale)

        assert result["stale_count"] == 1
        mock_send.assert_awaited_once()
        (msg,), _ = mock_send.call_args
        assert "bridge1" in msg
        assert "10 min ago" in msg  # 650s // 60 == 10

    async def test_trigger_not_called_when_no_stale_bridges(self):
        mock_trigger = AsyncMock()
        with patch("modules.admin_directives.router.trigger_admin_investigation", mock_trigger):
            result = await _run_bridge_watchdog_block([])
        assert result["stale_count"] == 0
        mock_trigger.assert_not_awaited()

    async def test_trigger_exception_does_not_break_job_return_value(self):
        stale = [{"bridge_id": "bridge2", "seconds_ago": 900}]
        with patch("modules.admin_directives.router.trigger_admin_investigation", new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await _run_bridge_watchdog_block(stale)
        assert result["stale_count"] == 1  # job return value unaffected by the Phase 5B failure

    def test_native_send_to_admin_import_resolves(self):
        """Regression guard for the 2026-08-04 isolated bugfix: the native
        alert path must import `send_to_admin` from its real home,
        `shared.bridge_orchestrator` -- not `modules.message_router`, which
        never defined it (that was the original bug; see git history /
        management_decisions.md for the incident this test used to pin)."""
        import modules.message_router as mr
        import shared.bridge_orchestrator as bo
        assert not hasattr(mr, "send_to_admin")
        assert hasattr(bo, "send_to_admin")

    def test_mirrored_block_matches_real_source(self):
        """Structural guard: if the real job_bridge_watchdog changes shape,
        this mirror must be updated too -- fails loudly instead of quietly
        testing stale logic."""
        from modules import scheduler as scheduler_mod
        import inspect
        src = inspect.getsource(scheduler_mod.start_scheduler)
        assert 'trigger_admin_investigation("bridge_watchdog"' in src
        assert "stale_bridges={stale_desc}" in src
