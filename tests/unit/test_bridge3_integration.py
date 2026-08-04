"""
Bridge3 (REC2) integration tests — technical bridge transport, NOT an Admin identity.

Bridge3 was previously an inert .env block (BRIDGE3_*, ADMIN_BRIDGE3_NUMBER)
silently dropped by Settings' `extra = "ignore"` — zero application-layer
wiring existed. This wires bridge3 into the existing bridge1/bridge2
transport infrastructure (Settings, app.bridge singletons, bridge_orchestrator
health/diagnostics, bridge_poller.BRIDGE_CONFIGS, modules.outbound dispatch)
reusing every existing mechanism.

UPDATE 2026-08-04 (Owner decision, reverses the same-day earlier "bridge3 =
Hermes admin command channel" change): bridge3 is a technical WhatsApp bridge
identity only, never a human Admin — Test 9 was rewritten accordingly (bridge3
must NOT be recognized as admin), and a new test confirms bridge2
(8801880446111) is the canonical Super Admin. Tests 1-8, 10-11 (transport
wiring, health tracking, outbound dispatch, loop-prevention) are unaffected —
bridge3 remains a live, tracked bridge, it just carries no admin privilege.
See knowledge_base/00_governance/management_decisions.md for the full record.

Tests:
  1. test_settings_has_bridge3_fields          — Settings exposes bridge3_* / admin_bridge3_number
  2. test_get_bridge3_is_a_singleton            — app.bridge.get_bridge3() singleton behavior
  3. test_bridge_configs_has_all_three_bridges  — BRIDGE_CONFIGS: bridge1+bridge2 unchanged, bridge3 added
  4. test_bridge3_health_tracked_not_prioritized — orchestrator tracks bridge3 health but excludes
                                                    it from BRIDGE_PRIORITY / ADMIN_BRIDGE_NAME
  5. test_probe_all_includes_bridge3            — probe_all() probes bridge3 too
  6. test_admin_failover_pool_unchanged         — send_with_failover never selects bridge3
                                                    (bridge1/bridge2 regression guard)
  7. test_outbound_dispatch_bridge3_fixed       — the binary-dispatch bug: bridge3 sends now
                                                    route via get_bridge3(), not get_bridge1()
  8. test_outbound_dispatch_bridge1_bridge2_unchanged — regression guard for the fix above
  9. test_identity_brain_does_not_recognize_bridge3_as_admin — REVERSED 2026-08-04 (Owner
     decision): bridge3 is a technical bridge identity only, not a human Admin.
     _check_admin_settings() must NOT grant admin recognition via admin_bridge3_number
     even if that Settings field happens to be set — see
     knowledge_base/00_governance/management_decisions.md for the reversal record.
 10. test_bridge_poller_fallback_bridge3_is_none — bridge3 has no cross-bridge outbound fallback
 11. test_inbound_query_excludes_own_outbound_messages — loop-prevention: is_from_me=0 filter
     is bridge-agnostic (shared _fetch_new_messages()), so bridge3 inherits it automatically.

All tests use in-process mocks / a throwaway SQLite file only — no live DB,
no live bridge processes required.
"""
from __future__ import annotations

import inspect
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

_CORE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _CORE_ROOT not in sys.path:
    sys.path.insert(0, _CORE_ROOT)


def _fresh_orchestrator():
    """Mirrors tests/unit/test_phase13d_bridge_orchestrator.py's fixture, plus bridge3."""
    mock_settings = MagicMock()
    mock_settings.bridge1_number = "8801958122300"
    mock_settings.bridge2_number = "8801880446111"
    mock_settings.bridge3_number = "8801805529211"
    mock_settings.bridge1_url = "http://localhost:8082"
    mock_settings.bridge2_url = "http://localhost:8081"
    mock_settings.bridge3_url = "http://localhost:8083"

    with patch("shared.bridge_orchestrator.get_settings", return_value=mock_settings):
        from shared.bridge_orchestrator import BridgeOrchestrator
        orch = BridgeOrchestrator()
    return orch


# ── Test 1: Settings fields ───────────────────────────────────────────────────

def test_settings_has_bridge3_fields():
    from app.config import Settings
    fields = Settings.model_fields if hasattr(Settings, "model_fields") else Settings.__fields__
    for name in (
        "bridge3_url", "bridge3_number", "bridge3_label",
        "bridge3_db_path", "bridge3_whatsapp_db_path", "admin_bridge3_number",
    ):
        assert name in fields, f"Settings is missing expected field {name!r}"


# ── Test 2: get_bridge3() singleton ───────────────────────────────────────────

def test_get_bridge3_is_a_singleton():
    import app.bridge as bridge_mod
    bridge_mod._bridge3 = None  # reset module-level singleton for a clean test
    b1 = bridge_mod.get_bridge3()
    b2 = bridge_mod.get_bridge3()
    assert b1 is b2, "get_bridge3() must return the same singleton instance"
    assert isinstance(b1, bridge_mod.BridgeClient)


# ── Test 3: BRIDGE_CONFIGS ────────────────────────────────────────────────────

def test_bridge_configs_has_all_three_bridges():
    from modules.bridge_poller import BRIDGE_CONFIGS
    names = [c["name"] for c in BRIDGE_CONFIGS]
    assert names == ["bridge1", "bridge2", "bridge3"], (
        f"BRIDGE_CONFIGS changed unexpectedly: {names} "
        "(bridge1/bridge2 order and presence must be preserved; bridge3 appended)"
    )
    bridge3_cfg = BRIDGE_CONFIGS[2]
    from app.bridge import get_bridge3
    assert bridge3_cfg["get_bridge"] is get_bridge3


# ── Test 4: health tracked, not prioritized ───────────────────────────────────

def test_bridge3_health_tracked_not_prioritized():
    from shared.bridge_orchestrator import BRIDGE_PRIORITY, ADMIN_BRIDGE_NAME

    orch = _fresh_orchestrator()
    assert "bridge3" in orch._health, "bridge3 must be health-tracked for RT-013 diagnostics"
    assert "bridge3" in orch._lag_samples

    # Regression: bridge1/bridge2 health entries must be unaffected
    assert orch._health["bridge1"].number == "8801958122300"
    assert orch._health["bridge2"].number == "8801880446111"
    assert orch._health["bridge3"].number == "8801805529211"

    # bridge3 must NOT participate in admin-notification failover
    assert "bridge3" not in BRIDGE_PRIORITY, (
        "bridge3 must stay out of BRIDGE_PRIORITY — that pool exists to reach "
        "bridge2's own admin self-chat number via alternate bridges; bridge3 "
        "is a different device and must never silently receive that traffic."
    )
    assert ADMIN_BRIDGE_NAME == "bridge2", "ADMIN_BRIDGE_NAME must remain bridge2 (unchanged)"


# ── Test 5: probe_all() covers bridge3 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_all_includes_bridge3():
    orch = _fresh_orchestrator()

    mock_b1, mock_b2, mock_b3 = AsyncMock(), AsyncMock(), AsyncMock()
    mock_b1.status = AsyncMock(return_value={"ok": True})
    mock_b2.status = AsyncMock(return_value={"ok": True})
    mock_b3.status = AsyncMock(return_value={"ok": True})

    with patch("shared.bridge_orchestrator.get_bridge1", return_value=mock_b1), \
         patch("shared.bridge_orchestrator.get_bridge2", return_value=mock_b2), \
         patch("shared.bridge_orchestrator.get_bridge3", return_value=mock_b3), \
         patch("shared.bridge_orchestrator.emit", new=AsyncMock()):
        await orch.probe_all()

    mock_b1.status.assert_awaited_once()
    mock_b2.status.assert_awaited_once()
    mock_b3.status.assert_awaited_once()
    assert orch._health["bridge3"].state == "healthy"


# ── Test 6: admin failover pool regression guard ──────────────────────────────

@pytest.mark.asyncio
async def test_admin_failover_pool_unchanged():
    """
    Even with bridge1 AND bridge2 both failing, send_with_failover must never
    reach for bridge3 — it queues for retry instead (bridge3 is excluded from
    BRIDGE_PRIORITY, see test 4).
    """
    from app.bridge import BridgeSendError

    orch = _fresh_orchestrator()

    mock_b1 = AsyncMock()
    mock_b1.send_strict = AsyncMock(side_effect=BridgeSendError("bridge1 down"))
    mock_b2 = AsyncMock()
    mock_b2.send_strict = AsyncMock(side_effect=BridgeSendError("bridge2 down"))
    mock_b3 = AsyncMock()
    mock_b3.send_strict = AsyncMock(return_value=None)  # would succeed if ever called

    with patch("shared.bridge_orchestrator.get_bridge1", return_value=mock_b1), \
         patch("shared.bridge_orchestrator.get_bridge2", return_value=mock_b2), \
         patch("shared.bridge_orchestrator.get_bridge3", return_value=mock_b3), \
         patch("shared.bridge_orchestrator.emit", new=AsyncMock()):
        success, used = await orch.send_with_failover(
            "880111@s.whatsapp.net", "test", preferred_bridge="bridge2",
        )

    assert success is False
    assert used == "queued"
    mock_b3.send_strict.assert_not_called()


# ── Test 7 & 8: outbound dispatch fix ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_outbound_dispatch_bridge3_fixed():
    """
    Regression test for the binary bridge2-or-bridge1 dispatch bug: a queue
    row with source_bridge='bridge3' must send via get_bridge3(), never
    silently fall through to get_bridge1() (which would leak the reply out
    over the HR number instead of the Admin's own bridge3 device).
    """
    from modules import outbound

    mock_b3 = AsyncMock()
    mock_b3.breaker = MagicMock()
    mock_b3.breaker.allow = MagicMock(return_value=True)
    mock_b3.breaker.record_success = MagicMock()
    mock_b3.send_strict = AsyncMock(return_value=None)

    mock_b1 = AsyncMock()
    mock_b1.send_strict = AsyncMock(side_effect=AssertionError("must not route bridge3 via bridge1"))

    with patch("app.bridge.get_bridge3", return_value=mock_b3), \
         patch("app.bridge.get_bridge1", return_value=mock_b1), \
         patch("app.bridge.get_bridge2", return_value=AsyncMock()):
        result = await outbound._send_with_channel("bridge3", "8801805529211", "answer for Admin")

    mock_b3.send_strict.assert_awaited_once_with("8801805529211", "answer for Admin")
    mock_b1.send_strict.assert_not_called()
    assert result == "bridge3:8801805529211"


@pytest.mark.asyncio
async def test_outbound_dispatch_bridge1_bridge2_unchanged():
    from modules import outbound

    for source_bridge, expected_attr in (("bridge1", "get_bridge1"), ("bridge2", "get_bridge2")):
        mock_client = AsyncMock()
        mock_client.breaker = MagicMock()
        mock_client.breaker.allow = MagicMock(return_value=True)
        mock_client.breaker.record_success = MagicMock()
        mock_client.send_strict = AsyncMock(return_value=None)

        with patch(f"app.bridge.{expected_attr}", return_value=mock_client), \
             patch("app.bridge.get_bridge3", return_value=AsyncMock()):
            result = await outbound._send_with_channel(source_bridge, "8801000000000", "hello")

        mock_client.send_strict.assert_awaited_once()
        assert result == f"{source_bridge}:8801000000000"


# ── Test 9: identity_brain admin recognition (REVERSED 2026-08-04) ────────────

def test_identity_brain_does_not_recognize_bridge3_as_admin():
    """Owner decision 2026-08-04: bridge3/REC2 is a technical WhatsApp bridge
    identity only, never a human Admin — reversing the same-day earlier
    bridge3-admin change. Even with admin_bridge3_number set on Settings (e.g.
    a stale/leftover config value), _check_admin_settings() must not grant
    admin recognition for that phone via that field — the attribute is no
    longer read at all."""
    from modules.identity_brain import _check_admin_settings

    mock_settings = MagicMock()
    mock_settings.admin_number_list = []
    mock_settings.admin_meta_number = ""
    mock_settings.admin_bridge1_number = ""
    mock_settings.admin_bridge2_number = ""
    mock_settings.admin_bridge3_number = "8801805529211"  # stale value, must be ignored

    with patch("app.config.get_settings", return_value=mock_settings):
        assert _check_admin_settings("8801805529211") is False
        assert _check_admin_settings("8801999999999") is False


def test_identity_brain_recognizes_bridge2_as_super_admin():
    """Bridge2 (8801880446111) is the canonical Super Admin identity —
    Owner-confirmed 2026-08-04. Recognized via admin_bridge2_number, same
    mechanism as before (unchanged for bridge1/bridge2)."""
    from modules.identity_brain import _check_admin_settings

    mock_settings = MagicMock()
    mock_settings.admin_number_list = []
    mock_settings.admin_meta_number = ""
    mock_settings.admin_bridge1_number = ""
    mock_settings.admin_bridge2_number = "8801880446111"
    mock_settings.admin_bridge3_number = ""

    with patch("app.config.get_settings", return_value=mock_settings):
        assert _check_admin_settings("8801880446111") is True


# ── Test 10: bridge3 outbound fallback is None ────────────────────────────────

def test_bridge_poller_fallback_bridge3_is_none():
    """
    Source-level check (matches this repo's existing test_c0d_source_bridge_fix.py
    convention): the fallback-channel dict used for the customer-reply send path
    must map bridge1<->bridge2 exactly as before, and bridge3 to no fallback.
    """
    import modules.bridge_poller as bp
    source = inspect.getsource(bp)
    assert '{"bridge1": "bridge2", "bridge2": "bridge1"}.get(bridge_name)' in source, (
        "Expected the bridge1/bridge2 fallback-channel mapping to be present "
        "and bridge3 to fall through .get()'s default of None."
    )


# ── Test 11: loop prevention is bridge-agnostic ───────────────────────────────

def test_inbound_query_excludes_own_outbound_messages():
    """
    modules.bridge_poller._fetch_new_messages() is the single, shared,
    bridge-agnostic function BRIDGE_CONFIGS-driven polling calls for every
    bridge (bridge1, bridge2, and now bridge3). Its SQL filters
    `WHERE is_from_me = 0`, so a bridge3 self-sent (Hermes) reply can never
    be re-ingested as a new inbound Admin command -- no new mechanism needed.
    """
    from modules.bridge_poller import _fetch_new_messages

    tmp_dir = tempfile.mkdtemp()
    db_path = os.path.join(tmp_dir, "messages.db")
    con = sqlite3.connect(db_path)
    con.execute(
        """CREATE TABLE messages (
            id TEXT, chat_jid TEXT, sender TEXT, content TEXT, timestamp TEXT,
            media_type TEXT, processed_text TEXT, filename TEXT, url TEXT, is_from_me INTEGER
        )"""
    )
    now = datetime.now(timezone.utc)
    inbound_ts = (now + timedelta(seconds=5)).isoformat(sep=" ")
    outbound_ts = (now + timedelta(seconds=10)).isoformat(sep=" ")
    con.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("m1", "8801805529211@s.whatsapp.net", "8801805529211", "আজকে মোট কতজন ডিউটিতে আছে?",
         inbound_ts, None, None, None, None, 0),
    )
    con.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("m2", "8801805529211@s.whatsapp.net", "8801805529211", "Hermes's own reply to the Admin",
         outbound_ts, None, None, None, None, 1),  # is_from_me=1 -- our own outbound reply
    )
    con.commit()
    con.close()

    try:
        messages, _max_ts = _fetch_new_messages(db_path, db_path, now - timedelta(seconds=60))
    finally:
        os.remove(db_path)
        os.rmdir(tmp_dir)

    texts = [m["text"] for m in messages]
    assert any("কতজন ডিউটিতে" in t for t in texts), "the real inbound Admin message must be returned"
    assert not any("Hermes's own reply" in t for t in texts), (
        "an is_from_me=1 row (our own outbound reply) must never be returned "
        "as a new inbound message -- this is the loop-prevention guarantee."
    )
