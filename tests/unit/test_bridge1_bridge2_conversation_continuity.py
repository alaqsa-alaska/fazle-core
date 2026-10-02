"""Bridge1/Bridge2 conversational continuity (owner decision 2026-09-02).

Bridge3 stays frozen/quarantined; Bridge1 becomes the Facebook
Click-to-WhatsApp recruitment number; recruitment + employee questions may
arrive on any active authenticated bridge and are answered through the SAME
bridge. See docs/BRIDGE1_BRIDGE2_CONVERSATION_CONTINUITY_2026-09-02.md.

These tests pin the behaviour the change relies on. The full inbound→reply
pipeline is exercised by the existing recruitment / employee / router
suites (run in the same session); here we pin the specific invariants:

  * F6 (automated OTP / system-message suppression) now covers
    bridge1/bridge2/bridge3 — not Meta.
  * same-source reply affinity: every customer-reply enqueue in the poller
    uses source_bridge = the inbound bridge; no fan-out.
  * Bridge3 stays HELD even when the global `auto_reply.recruitment` /
    `auto_reply.employee` toggles are true.
"""
from __future__ import annotations

import inspect

import pytest

from shared.system_message import is_automated_system_message


# ── F6 now covers bridge1 / bridge2 / bridge3 ───────────────────────────────

def test_f6_gate_covers_all_three_whatsapp_bridges_not_meta():
    from modules import bridge_poller as bp
    src = inspect.getsource(bp)
    # the F6 branch
    i = src.index("is_automated_system_message(text)")
    window = src[i - 300:i + 50]
    assert 'bridge_name in ("bridge1", "bridge2", "bridge3")' in window
    assert '"meta"' not in window


@pytest.mark.parametrize("text", [
    "482913 is your one-time password",
    "G-728193 is your Google verification code",
    "Your WhatsApp account is successfully connected to your Instagram profile",
    "৪৮২৯১৩ is your Facebook confirmation code",
])
def test_f6_pure_function_flags_otp_regardless_of_bridge(text):
    # the function is bridge-agnostic; the poller gate decides which bridges apply it
    assert is_automated_system_message(text) is True


@pytest.mark.parametrize("text", [
    "আমি Security Guard পদে চাকরি করতে চাই",
    "আমার বয়স 27, অভিজ্ঞতা 3 বছর",
    "আমি আপনাদের কর্মী, আমার বেতন নিয়ে প্রশ্ন আছে",
    "1998",
])
def test_f6_never_suppresses_a_real_recruitment_or_employee_message(text):
    assert is_automated_system_message(text) is False


# ── same-source reply affinity ─────────────────────────────────────────────

def test_customer_reply_enqueues_use_inbound_bridge_as_source():
    """Every customer-facing enqueue in the poller's inbound path binds
    source_bridge to `bridge_name` (the bridge the message arrived on).
    No customer-reply enqueue may hard-code a different bridge or fan out."""
    from modules import bridge_poller as bp
    src = inspect.getsource(bp._poll_bridge) if hasattr(bp, "_poll_bridge") else inspect.getsource(bp)

    # find the customer/non-admin reply dispatch region and check the enqueue calls
    lowered = src
    # crude but effective: no customer-reply enqueue may use a literal bridge name
    for bad in ('source_bridge="bridge1"', "source_bridge='bridge1'",
                'source_bridge="bridge2"', "source_bridge='bridge2'",
                'source_bridge="bridge3"', "source_bridge='bridge3'"):
        assert bad not in lowered, f"hard-coded {bad} in the poller inbound path"
    # the customer-reply enqueue(s) exist and use bridge_name
    assert "source_bridge=bridge_name" in lowered


def test_no_fanout_one_inbound_at_most_one_customer_enqueue():
    """The customer/non-admin reply block must not loop over bridges or
    enqueue to more than one bridge for a single inbound."""
    from modules import bridge_poller as bp
    src = inspect.getsource(bp)
    # the block that sends a customer reply
    start = src.index("# STEP 7: loop detection")
    end = src.index("Write intent back to the most recent inbound row", start) \
        if "Write intent back" in src[start:] else start + 4000
    block = src[start:end]
    # exactly the single-recipient enqueue pattern, no "for ... in ... bridges"
    assert "for _b in" not in block and "for bridge in" not in block
    assert block.count("enqueue_outbound(") <= 2  # primary path + at most one guarded variant


# ── Bridge3 stays HELD regardless of the global conversational toggles ──────

@pytest.mark.asyncio
async def test_bridge3_held_even_with_global_recruitment_and_employee_true(monkeypatch):
    store = {
        "auto_reply.all": "true",
        "auto_reply.recruitment": "true",
        "auto_reply.employee": "true",
        "bridge3.pairing_state": "HISTORY_SYNC_QUARANTINE",
    }

    async def fake_get(key, default="false"):
        return store.get(key, default)

    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", fake_get)

    from modules.bridge_poller import _bridge_outbound_held, _bridge_pairing_state
    held, state = await _bridge_outbound_held("bridge3")
    assert held is True
    # bridge1/bridge2 are released (unset) — the global toggles enable them
    for b in ("bridge1", "bridge2"):
        h, _ = await _bridge_outbound_held(b)
        assert h is False

    # and the global recruitment toggle really is 'true' in this scenario
    from modules.wa_chat_frontend import get_effective_auto_reply
    assert await get_effective_auto_reply("recruitment") is True


@pytest.mark.asyncio
async def test_outbound_enqueue_still_refuses_bridge3_with_globals_true(monkeypatch):
    from modules import outbound

    async def fake_get(key, default="false"):
        return {"bridge3.pairing_state": "HISTORY_SYNC_QUARANTINE",
                "auto_reply.recruitment": "true"}.get(key, default)

    monkeypatch.setattr("modules.wa_chat_frontend.get_runtime_setting", fake_get)
    from unittest.mock import AsyncMock, patch
    with patch.object(outbound, "fetch_one", new=AsyncMock(return_value={"id": 1})) as ins:
        qid = await outbound.enqueue("8801711111111", "hi", source_bridge="bridge3",
                                     purpose="customer-reply:recruitment", idempotency_key="k")
    assert qid is None
    ins.assert_not_awaited()
