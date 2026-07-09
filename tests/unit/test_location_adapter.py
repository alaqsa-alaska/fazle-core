from __future__ import annotations

import importlib
from types import SimpleNamespace

import httpx
import pytest

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self):
        return self._payload


class _ScriptedAsyncClient:
    outcomes: list = []
    calls: int = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, headers=None):
        del url, params, headers
        _ScriptedAsyncClient.calls += 1
        if not _ScriptedAsyncClient.outcomes:
            raise AssertionError("No scripted outcome left for client.get")
        outcome = _ScriptedAsyncClient.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _settings(**overrides):
    base = {
        "locationwhere_base_url": "http://locationwhere.test/api/v1",
        "locationwhere_jwt_token": "jwt-token",
        "locationwhere_employee_location_endpoint": "/location/live",
        "locationwhere_device_status_endpoint": "/device/{device_id}/status",
        "locationwhere_health_endpoint": "/gateway/status",
        "location_adapter_timeout_s": 2.0,
        "location_adapter_retries": 2,
        "location_adapter_cache_ttl_s": 30,
        "location_adapter_circuit_max_failures": 5,
        "location_adapter_circuit_window_s": 120,
        "location_adapter_circuit_open_s": 60,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _reset_scripted_client_state():
    _ScriptedAsyncClient.outcomes = []
    _ScriptedAsyncClient.calls = 0


async def _no_audit(**_kwargs):
    return None


def _get_settings_default():
    return _settings()


async def test_cache_hit_avoids_second_http_call(monkeypatch):
    from modules import location_adapter as m
    m = importlib.reload(m)

    monkeypatch.setattr(m, "get_settings", _get_settings_default)
    monkeypatch.setattr(m, "_write_audit", _no_audit)
    monkeypatch.setattr(m.httpx, "AsyncClient", _ScriptedAsyncClient)

    _ScriptedAsyncClient.outcomes = [
        _FakeResponse(200, {"employeeId": "E-100", "lat": 22.3, "lon": 91.8})
    ]

    first = await m.get_employee_location("E-100")
    second = await m.get_employee_location("E-100")

    assert first["ok"] is True
    assert first["cache_hit"] is False
    assert second["ok"] is True
    assert second["cache_hit"] is True
    assert _ScriptedAsyncClient.calls == 1


async def test_retry_then_success_on_http_5xx(monkeypatch):
    from modules import location_adapter as m
    m = importlib.reload(m)

    monkeypatch.setattr(m, "get_settings", lambda: _settings(location_adapter_retries=2))
    monkeypatch.setattr(m, "_write_audit", _no_audit)
    monkeypatch.setattr(m.httpx, "AsyncClient", _ScriptedAsyncClient)

    _ScriptedAsyncClient.outcomes = [
        _FakeResponse(503, {"error": "temporary"}),
        _FakeResponse(200, {"deviceId": "D-9", "state": "online"}),
    ]

    result = await m.get_device_status("D-9")

    assert result["ok"] is True
    assert result["attempts"] == 2
    assert result["data"]["state"] == "online"
    assert _ScriptedAsyncClient.calls == 2


async def test_circuit_opens_and_short_circuits_follow_up_call(monkeypatch):
    from modules import location_adapter as m
    m = importlib.reload(m)

    monkeypatch.setattr(
        m,
        "get_settings",
        lambda: _settings(
            location_adapter_retries=0,
            location_adapter_circuit_max_failures=2,
            location_adapter_circuit_window_s=120,
            location_adapter_circuit_open_s=60,
        ),
    )
    monkeypatch.setattr(m, "_write_audit", _no_audit)
    monkeypatch.setattr(m.httpx, "AsyncClient", _ScriptedAsyncClient)

    _ScriptedAsyncClient.outcomes = [
        httpx.TimeoutException("timeout-1"),
        httpx.TimeoutException("timeout-2"),
    ]

    first = await m.check_location_service_health()
    second = await m.check_location_service_health()
    third = await m.check_location_service_health()

    assert first["ok"] is False
    assert second["ok"] is False
    assert third["ok"] is False
    assert third["error"] == "circuit_open"
    assert third["circuit_state"] == "open"
    assert _ScriptedAsyncClient.calls == 2
