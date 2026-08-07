"""Unit tests — modules/hermes_kernel/shadow_logger

Correctness contracts:
  1. Flag=False  → no DB write attempted
  2. Flag=True   → execute() called with correct INSERT
  3. Cache invalidate → new config picked up; without invalidation cache is sticky
  4. Flag=False (integration) → hermes_decision_audit_log row count unchanged
     Requires a live DB; skipped automatically when DB is unavailable.
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.unit


def _fresh():
    from modules.hermes_kernel.shadow_logger import invalidate_flag_cache
    invalidate_flag_cache()


def _rec():
    from modules.hermes_kernel.data_classes import DecisionRecord
    return DecisionRecord(sender_phone="8801700000001", source="bridge1", raw_input_text="test")


def _evt(rec):
    from modules.hermes_kernel.data_classes import KernelEvent
    return KernelEvent(trace_id=rec.trace_id, event_name="EVT-001 message.received", payload={})


# ── 1. Flag=False → no DB write ───────────────────────────────────────────────

class TestFlagFalseProducesNoWrite:
    """When feature flags are disabled, execute() must never be called."""

    async def test_log_decision_no_write(self):
        _fresh()
        from modules.hermes_kernel.shadow_logger import log_decision
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = False
            m.return_value.hermes_event_log_enabled = False
            with patch("modules.preflight_guard.is_feature_active", return_value=False):
                with patch("app.database.execute", new=AsyncMock()) as ex:
                    await log_decision(_rec())
                    ex.assert_not_called()

    async def test_log_event_no_write(self):
        _fresh()
        from modules.hermes_kernel.shadow_logger import log_event
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = False
            m.return_value.hermes_event_log_enabled = False
            with patch("modules.preflight_guard.is_feature_active", return_value=False):
                with patch("app.database.execute", new=AsyncMock()) as ex:
                    r = _rec()
                    await log_event(_evt(r))
                    ex.assert_not_called()

    async def test_disabled_never_raises(self):
        """A flag=False call must complete without raising regardless of DB state."""
        _fresh()
        from modules.hermes_kernel.shadow_logger import log_decision, log_event
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = False
            m.return_value.hermes_event_log_enabled = False
            with patch("modules.preflight_guard.is_feature_active", return_value=False):
                r = _rec()
                await log_decision(r)
                await log_event(_evt(r))


# ── 2. Flag=True → execute() is called ───────────────────────────────────────

class TestFlagTrueProducesWrite:
    """When feature flags are enabled, execute() must be called once per record."""

    async def test_log_decision_calls_execute(self):
        _fresh()
        from modules.hermes_kernel.shadow_logger import log_decision
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = True
            m.return_value.hermes_event_log_enabled = True
            with patch("app.database.execute", new=AsyncMock()) as ex:
                await log_decision(_rec())
                assert ex.call_count == 1
                assert "hermes_decision_audit_log" in ex.call_args[0][0]

    async def test_log_event_calls_execute(self):
        _fresh()
        from modules.hermes_kernel.shadow_logger import log_event
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = True
            m.return_value.hermes_event_log_enabled = True
            with patch("app.database.execute", new=AsyncMock()) as ex:
                r = _rec()
                await log_event(_evt(r))
                assert ex.call_count == 1
                assert "hermes_event_log" in ex.call_args[0][0]

    async def test_db_exception_does_not_propagate(self):
        """A DB failure on the enabled path must be caught, not raised."""
        _fresh()
        from modules.hermes_kernel.shadow_logger import log_decision
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = True
            m.return_value.hermes_event_log_enabled = True
            with patch("app.database.execute", new=AsyncMock(side_effect=RuntimeError("DB down"))):
                await log_decision(_rec())  # must not raise

    async def test_runtime_ops_flag_enables_when_env_is_false(self):
        """Runtime ops flag True with env False must enable the logger."""
        _fresh()
        from modules.hermes_kernel.shadow_logger import log_decision
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = False
            m.return_value.hermes_event_log_enabled = False
            with patch("modules.preflight_guard.is_feature_active", return_value=True):
                with patch("app.database.execute", new=AsyncMock()) as ex:
                    await log_decision(_rec())
                    assert ex.call_count == 1


# ── 3. Cache invalidate → new config is picked up ────────────────────────────

class TestCacheInvalidation:
    """invalidate_flag_cache() must cause the next call to re-read settings."""

    async def test_invalidate_picks_up_new_value(self):
        from modules.hermes_kernel.shadow_logger import invalidate_flag_cache, _is_audit_enabled
        invalidate_flag_cache()
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = False
            assert _is_audit_enabled() is False
        invalidate_flag_cache()
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = True
            assert _is_audit_enabled() is True

    async def test_without_invalidation_cache_is_sticky(self):
        """Without invalidate_flag_cache(), a changed mock is NOT picked up."""
        from modules.hermes_kernel.shadow_logger import invalidate_flag_cache, _is_audit_enabled
        invalidate_flag_cache()
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = False
            _is_audit_enabled()  # populate cache
        # change mock without invalidating — cached False must persist
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = True
            assert _is_audit_enabled() is False


# ── 4. Integration: Flag=False → row count unchanged in live DB ───────────────

def _db_url() -> str | None:
    """Return live DB URL from env or secure backup; None when unavailable."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        for line in open("/home/azim/secure-env-backup/runtime-services.env"):
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


@pytest.mark.integration
class TestRowCountIntegration:
    """Verify that flag=False leaves hermes_decision_audit_log row count unchanged.

    Skipped automatically when no live DB is reachable so CI never fails.
    This test proves the real side effect is absent, not just the execute() call.
    """

    async def test_flag_false_row_count_unchanged(self):
        import asyncpg
        url = _db_url()
        if not url:
            pytest.skip("DATABASE_URL not available — skipping integration test")

        try:
            conn = await asyncpg.connect(url)
        except Exception as exc:
            pytest.skip(f"DB not reachable from test environment: {exc}")

        try:
            before = await conn.fetchval("SELECT COUNT(*) FROM hermes_decision_audit_log")
        finally:
            await conn.close()

        _fresh()
        from modules.hermes_kernel.shadow_logger import log_decision
        with patch("app.config.get_settings") as m:
            m.return_value.hermes_decision_audit_enabled = False
            m.return_value.hermes_event_log_enabled = False
            await log_decision(_rec())

        try:
            conn = await asyncpg.connect(url)
        except Exception as exc:
            pytest.skip(f"DB not reachable from test environment: {exc}")

        try:
            after = await conn.fetchval("SELECT COUNT(*) FROM hermes_decision_audit_log")
        finally:
            await conn.close()

        assert after == before, (
            f"hermes_decision_audit_log row count changed from {before} to {after} "
            f"despite hermes_decision_audit_enabled=False"
        )
