"""Focused regressions for maintenance-mode health readiness."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


pytestmark = pytest.mark.asyncio


async def _probe(monkeypatch, *, enabled: bool, pending: int = 0, error: Exception | None = None):
    import app.main as main

    pending_count = AsyncMock(side_effect=error) if error else AsyncMock(return_value=pending)
    monkeypatch.setattr(main.outbound_queue, "pending_count", pending_count)
    monkeypatch.setattr(main.outbound_queue, "dlq_count", AsyncMock(return_value=103))
    monkeypatch.setattr(main.outbound_queue, "actionable_dlq_count", AsyncMock(return_value=0))
    monkeypatch.setattr(main.outbound_queue, "_outbound_enabled", lambda: enabled)
    return await main._probe_outbound()


async def test_disabled_with_pending_rows_is_explicitly_paused_not_degraded(monkeypatch):
    result = await _probe(monkeypatch, enabled=False, pending=2)

    assert result == {
        "status": "ok",
        "processing": "paused",
        "maintenance_mode": True,
        "pending": 2,
        "dlq": 103,
        "actionable_dlq": 0,
    }


async def test_disabled_with_no_pending_rows_remains_explicitly_paused(monkeypatch):
    result = await _probe(monkeypatch, enabled=False)

    assert result["status"] == "ok"
    assert result["processing"] == "paused"
    assert result["maintenance_mode"] is True
    assert result["pending"] == 0


async def test_enabled_with_pending_rows_preserves_degraded_behavior(monkeypatch):
    result = await _probe(monkeypatch, enabled=True, pending=3)

    assert result["status"] == "degraded"
    assert "processing" not in result


async def test_enabled_backlog_above_200_preserves_critical_behavior(monkeypatch):
    result = await _probe(monkeypatch, enabled=True, pending=201)

    assert result["status"] == "critical"
    assert result["pending"] == 201


async def test_database_error_is_not_hidden_by_maintenance_mode(monkeypatch):
    result = await _probe(monkeypatch, enabled=False, error=RuntimeError("database unavailable"))

    assert result["status"] == "critical"
    assert "database unavailable" in result["error"]


async def test_restart_gate_accepts_explicit_paused_outbound_probe():
    from scripts import restart_gate_readiness as readiness

    probes = {name: {"status": "ok"} for name in readiness.CORE_PROBES + readiness.AMBIENT_PROBES}
    probes["outbound"] = {
        "status": "ok",
        "processing": "paused",
        "maintenance_mode": True,
        "pending": 2,
    }

    result = readiness.classify_health(200, {"status": "ok", "probes": probes})

    assert result["state"] == "ready"
