"""Maintenance-mode safety tests for automatic bridge send-control enablement."""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bridge import BridgeClient

pytestmark = pytest.mark.unit


class _HTTP:
    def __init__(self, *, allowed: bool = False, fail: bool = False):
        self.allowed = allowed
        self.fail = fail
        self.post_calls: list[tuple[str, dict]] = []
        self.get_calls: list[str] = []

    async def post(self, url, *, json, timeout):
        self.post_calls.append((url, json))
        if self.fail:
            raise RuntimeError("synthetic bridge control failure")
        return SimpleNamespace(status_code=200)

    async def get(self, url, *, timeout):
        self.get_calls.append(url)
        return SimpleNamespace(json=lambda: {"allowed": self.allowed})



def _bridge(http: _HTTP) -> BridgeClient:
    bridge = BridgeClient.__new__(BridgeClient)
    bridge.base_url = "http://bridge-test"
    bridge.label = "BR-TEST"
    bridge._client = http
    return bridge


@pytest.mark.asyncio
async def test_all_maintenance_flags_false_never_enable_bridge(monkeypatch):
    monkeypatch.setenv("AUTO_REPLY_ENABLED", "false")
    monkeypatch.setenv("RECRUITMENT_AUTOREPLY_ENABLED", "false")
    monkeypatch.setenv("OUTBOUND_ENABLED", "false")
    http = _HTTP()

    assert await _bridge(http).ensure_enabled() is False
    assert http.post_calls == []
    assert http.get_calls == []


@pytest.mark.asyncio
async def test_outbound_disabled_blocks_enable_even_if_auto_flags_are_true(monkeypatch):
    monkeypatch.setenv("AUTO_REPLY_ENABLED", "true")
    monkeypatch.setenv("RECRUITMENT_AUTOREPLY_ENABLED", "true")
    monkeypatch.setenv("OUTBOUND_ENABLED", "false")
    http = _HTTP()

    assert await _bridge(http).ensure_enabled() is False
    assert http.post_calls == []


@pytest.mark.asyncio
async def test_disabled_bridge_remains_disabled_in_maintenance(monkeypatch):
    monkeypatch.setenv("OUTBOUND_ENABLED", "false")
    http = _HTTP(allowed=False)
    bridge = _bridge(http)

    assert await bridge.ensure_enabled() is False
    assert http.post_calls == []
    assert http.allowed is False


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "false", "maybe", "truthy"])
async def test_missing_or_invalid_outbound_authorization_fails_closed(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("OUTBOUND_ENABLED", raising=False)
    else:
        monkeypatch.setenv("OUTBOUND_ENABLED", value)
    http = _HTTP()

    assert await _bridge(http).ensure_enabled() is False
    assert http.post_calls == []


@pytest.mark.asyncio
async def test_maintenance_retry_after_startup_failure_never_enables(monkeypatch):
    monkeypatch.setenv("OUTBOUND_ENABLED", "false")
    http = _HTTP(fail=True)
    bridge = _bridge(http)

    assert await bridge.ensure_enabled() is False
    assert await bridge.ensure_enabled() is False
    assert http.post_calls == []


@pytest.mark.asyncio
async def test_explicitly_authorized_nonmaintenance_behavior_is_preserved(monkeypatch):
    monkeypatch.setenv("OUTBOUND_ENABLED", "true")
    http = _HTTP(allowed=True)

    assert await _bridge(http).ensure_enabled() is True
    assert http.post_calls == [("http://bridge-test/api/send-control", {"allow": True})]
    assert http.get_calls == ["http://bridge-test/api/send-status"]


@pytest.mark.asyncio
async def test_startup_guard_preserves_inbound_poller_start(monkeypatch):
    import modules.bridge_poller as poller

    monkeypatch.setenv("OUTBOUND_ENABLED", "false")
    clients = [_bridge(_HTTP()), _bridge(_HTTP()), _bridge(_HTTP())]
    monkeypatch.setattr(poller, "init_tables", lambda: asyncio.sleep(0))
    monkeypatch.setattr(poller, "get_bridge1", lambda: clients[0])
    monkeypatch.setattr(poller, "get_bridge2", lambda: clients[1])
    monkeypatch.setattr(poller, "get_bridge3", lambda: clients[2])
    started = []
    tasks = []

    async def fake_poller(config):
        started.append(config["name"])

    monkeypatch.setattr(poller, "_poll_bridge", fake_poller)
    monkeypatch.setattr(poller, "BRIDGE_CONFIGS", [{"name": "bridge1"}, {"name": "bridge2"}, {"name": "bridge3"}])
    monkeypatch.setattr(poller.asyncio, "create_task", lambda coroutine: tasks.append(coroutine) or object())

    await poller.start_pollers()
    for coroutine in tasks:
        coroutine.close()

    assert all(client._client.post_calls == [] for client in clients)
    assert len(tasks) == 3
    assert started == []  # tasks are scheduled; they are not run by the mock create_task


def test_manual_send_path_and_automatic_paths_remain_distinct():
    bridge_source = inspect.getsource(BridgeClient)
    assert "await self._set_send(True)" in bridge_source
    assert "await self._set_send(False)" in bridge_source
    assert "async def ensure_enabled" in bridge_source


def test_every_automatic_enable_call_routes_through_shared_guard():
    import modules.bridge_poller as poller

    source = inspect.getsource(poller)
    executable = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
    assert executable.count("ensure_enabled()") == 4
    assert ".post(" not in executable
    assert "await start_pollers()" in (Path("app/main.py").read_text(encoding="utf-8"))
