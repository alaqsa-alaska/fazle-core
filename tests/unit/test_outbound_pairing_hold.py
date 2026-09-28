"""Outbound-side pairing-state hold (BRIDGE3_FIRST_REPAIR_INCIDENT_2026-09-02).

The pairing hold must block BOTH newly-generated AND already-queued Bridge3
outbound work:
  * modules.outbound.enqueue() refuses a bridge3 enqueue while held.
  * modules.outbound.sweep_once() leaves an already-queued bridge3 row PENDING
    (no attempt consumed, no DLQ) while held.
  * a failover to a held bridge is refused (re-raised into normal backoff).
  * bridge1 / bridge2 dispatch is untouched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.asyncio


# ── enqueue() refuses a held bridge ────────────────────────────────────────

async def test_enqueue_refused_when_bridge3_held():
    from modules import outbound
    from modules.identity_role_authorization import AuthorizationDecision
    with patch.object(outbound, "_pairing_held", new=AsyncMock(return_value=(True, "HISTORY_SYNC_QUARANTINE"))), \
         patch.object(outbound, "fetch_one", new=AsyncMock()) as mock_insert:
        qid = await outbound.enqueue("8801711111111", "hi", source_bridge="bridge3",
                                     purpose="customer-reply:recruitment", idempotency_key="k1")
    assert qid is None
    mock_insert.assert_not_awaited()  # never reached the INSERT


async def test_enqueue_allowed_when_bridge3_released():
    from modules import outbound
    from modules.identity_role_authorization import AuthorizationDecision
    with patch.object(outbound, "_pairing_held", new=AsyncMock(return_value=(False, ""))), \
         patch.object(outbound, "fetch_one", new=AsyncMock(return_value={"id": 42})):
        qid = await outbound.enqueue("8801711111111", "hi", source_bridge="bridge3",
                                     purpose="x", idempotency_key="k2")
    assert qid == 42


async def test_enqueue_bridge1_never_consults_hold_for_normal_case():
    from modules import outbound
    from modules.identity_role_authorization import AuthorizationDecision
    # bridge1 is not fail-closed; _pairing_held returns (False, "") for it
    with patch.object(outbound, "_pairing_held", new=AsyncMock(return_value=(False, ""))), \
         patch.object(outbound, "fetch_one", new=AsyncMock(return_value={"id": 7})):
        qid = await outbound.enqueue("8801880446111", "admin msg", source_bridge="bridge1",
                                     purpose="hermes_admin_send", idempotency_key="k3")
    assert qid == 7


# ── _pairing_held wrapper is fail-closed for bridge3 ───────────────────────

async def test_pairing_held_wrapper_fail_closed_on_exception():
    from modules import outbound
    from modules.identity_role_authorization import AuthorizationDecision

    def _boom(_name, *a, **k):
        raise RuntimeError("bridge_poller import broke")

    with patch("builtins.__import__", side_effect=_boom):
        held3, _ = await outbound._pairing_held("bridge3")
        held1, _ = await outbound._pairing_held("bridge1")
    assert held3 is True     # bridge3 fail-closed
    assert held1 is False    # bridge1 fail-open (unchanged)


# ── sweep_once() holds an already-queued bridge3 row ───────────────────────

def _row(rid, bridge, fallback=None):
    return {"id": rid, "recipient": "8801711111111", "body": "reply",
            "source_bridge": bridge, "fallback_channel": fallback,
            "attempts": 0, "max_attempts": 3, "purpose": "customer-reply:recruitment",
            "meta_json": "{}", "idempotency_key": f"key{rid}"}


async def test_sweep_leaves_held_bridge3_row_pending(monkeypatch):
    from modules import outbound
    from modules.identity_role_authorization import AuthorizationDecision

    monkeypatch.setenv("OUTBOUND_ENABLED", "true")
    rows = [_row(1, "bridge3"), _row(2, "bridge1")]

    # fake pool/conn
    class _Conn:
        async def fetch(self, *a, **k):
            return rows
        async def execute(self, *a, **k):
            return None
        def transaction(self):
            class _T:
                async def __aenter__(s): return s
                async def __aexit__(s, *a): return False
            return _T()
    class _Pool:
        def acquire(self):
            class _A:
                async def __aenter__(s): return _Conn()
                async def __aexit__(s, *a): return False
            return _A()
        async def execute(self, *a, **k):
            _Pool.updates.append(a)
            return None
    _Pool.updates = []

    sent_via = []

    async def _fake_send(bridge, recipient, body, *, authorization=None):
        sent_via.append(bridge)
        return f"{bridge}:{recipient}"

    async def _held(bridge):
        return (bridge == "bridge3", "HISTORY_SYNC_QUARANTINE" if bridge == "bridge3" else "")

    with patch.object(outbound, "get_pool", return_value=_Pool()), \
         patch.object(outbound, "_send_with_channel", new=_fake_send), \
         patch.object(outbound, "_pairing_held", new=_held), \
             patch.object(outbound, "_authorize_claimed_row", new=AsyncMock(return_value=SimpleNamespace(decision=AuthorizationDecision(True, "allowed", 1, False)))), \
         patch.object(outbound, "record_error", new=AsyncMock()):
        result = await outbound.sweep_once(limit=10)

    # bridge3 row held (not sent), bridge1 row sent
    assert sent_via == ["bridge1"]
    assert result["held"] == 1
    assert result["sent"] == 1
    # the held row was UPDATE'd back to pending (not failed/dlq)
    held_updates = [a for a in _Pool.updates if "bridge_pairing_hold" in str(a)]
    assert held_updates, "expected a pending/hold UPDATE for the bridge3 row"
    assert "status='pending'" in str(held_updates[0])
