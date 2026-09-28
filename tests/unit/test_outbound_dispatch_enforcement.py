"""IR-2D2 final pre-transport authorization tests."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.identity_role_authorization import AuthorizationDecision
from modules.outbound_authorization import issue_transport_authorization


def _decision(allowed: bool, reason: str = "allowed"):
    return AuthorizationDecision(
        allowed=allowed,
        reason_code=reason,
        authorization_version=7,
        manual_review_required=not allowed,
        restriction_category=None if allowed else "authorization",
    )


def _row(queue_id: int = 1, fallback: str | None = None) -> dict:
    return {
        "id": queue_id,
        "recipient": "synthetic-recipient",
        "body": "synthetic body",
        "source_bridge": "bridge1",
        "fallback_channel": fallback,
        "attempts": 0,
        "max_attempts": 3,
        "purpose": "customer_auto_reply",
        "meta_json": {},
        "idempotency_key": f"ir2d2-{queue_id}",
    }


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    async def fetch(self, *args, **kwargs):
        return self.rows

    async def execute(self, *args, **kwargs):
        self.updates.append(args)

    def transaction(self):
        class _Transaction:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        return _Transaction()


class _Pool:
    def __init__(self, conn):
        self.conn = conn
        self.updates = []

    def acquire(self):
        pool = self

        class _Acquire:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *args):
                return False

        return _Acquire()

    async def execute(self, *args, **kwargs):
        self.updates.append(args)


@pytest.mark.asyncio
async def test_allowed_claim_reaches_transport_once(monkeypatch):
    from modules import outbound

    row = _row()
    conn = _Conn([row])
    pool = _Pool(conn)
    transport = AsyncMock(return_value="bridge1:accepted")
    auth = SimpleNamespace(decision=_decision(True))
    monkeypatch.setenv("OUTBOUND_ENABLED", "true")
    monkeypatch.setattr(outbound, "get_pool", lambda: pool)
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(outbound, "_authorize_claimed_row", AsyncMock(return_value=auth))
    monkeypatch.setattr(outbound, "_send_with_channel", transport)

    result = await outbound.sweep_once()

    assert result["sent"] == 1
    transport.assert_awaited_once()
    assert transport.await_args.kwargs["authorization"].decision.allowed is True


@pytest.mark.asyncio
async def test_denied_claim_has_zero_transport_calls_and_manual_review_marker(monkeypatch):
    from modules import outbound

    row = _row()
    conn = _Conn([row])
    pool = _Pool(conn)
    transport = AsyncMock()
    denied = SimpleNamespace(decision=_decision(False, "identity_hold"))
    monkeypatch.setenv("OUTBOUND_ENABLED", "true")
    monkeypatch.setattr(outbound, "get_pool", lambda: pool)
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(outbound, "_authorize_claimed_row", AsyncMock(return_value=denied))
    monkeypatch.setattr(outbound, "_send_with_channel", transport)

    result = await outbound.sweep_once()

    assert result["held"] == 1
    transport.assert_not_awaited()
    assert any("authorization_denied:identity_hold" in str(args) for args in conn.updates)
    assert any("INTERVAL '1 day'" in str(args) for args in conn.updates)


@pytest.mark.asyncio
async def test_fallback_is_revalidated_independently(monkeypatch):
    from modules import outbound

    row = _row(fallback="bridge2")
    conn = _Conn([row])
    pool = _Pool(conn)
    source_transport = AsyncMock(side_effect=RuntimeError("source unavailable"))
    decisions = [
        SimpleNamespace(decision=_decision(True)),
        SimpleNamespace(decision=_decision(False, "channel_restricted")),
    ]
    authorize = AsyncMock(side_effect=decisions)
    monkeypatch.setenv("OUTBOUND_ENABLED", "true")
    monkeypatch.setattr(outbound, "get_pool", lambda: pool)
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(outbound, "_authorize_claimed_row", authorize)
    monkeypatch.setattr(outbound, "_send_with_channel", source_transport)

    result = await outbound.sweep_once()

    assert result["held"] == 1
    assert authorize.await_count == 2
    assert source_transport.await_count == 1
    assert source_transport.await_args.args[0] == "bridge1"


@pytest.mark.asyncio
async def test_bridge_transport_requires_capability_before_http():
    from app.bridge import BridgeClient, BridgeSendError

    calls = []

    class _Response:
        status_code = 200
        text = ""

    class _HTTP:
        async def post(self, *args, **kwargs):
            calls.append((args, kwargs))
            return _Response()

    client = BridgeClient.__new__(BridgeClient)
    client.label = "synthetic"
    client.base_url = "http://synthetic"
    client._client = _HTTP()
    client._set_send = AsyncMock()

    with pytest.raises(BridgeSendError, match="authorization_required"):
        await client.send_strict("synthetic-recipient", "body")
    assert calls == []

    await client.send_strict(
        "synthetic-recipient", "body",
        authorization=issue_transport_authorization(_decision(True)),
    )
    assert len(calls) == 1
