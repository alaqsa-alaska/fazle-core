"""Unit tests — modules.bridge_poller.process_bridge_inbound wiring of the
deterministic owner→accountant payment relay.

Proves:
  (a) when modules.payment_relay claims the message, process_bridge_inbound
      STOPS immediately — no intent classification, recruitment, or routing;
  (b) when the relay does not claim the message, the normal pipeline runs
      completely unchanged.
"""
from __future__ import annotations

import pytest

import modules.bridge_poller as bp

pytestmark = pytest.mark.unit


SINGLE = "ID: 01998846707 Maruf islam 01998846707(N) 200/-"


async def test_relay_claim_stops_all_further_processing(monkeypatch):
    """A claimed relay message must NOT reach: intent classification,
    recruitment eligibility, full routing (process_message → Hermes/LLM/
    employee lookup), draft creation, or the admin-review-draft renderer.
    """
    hit = []

    async def _fake_relay(*, bridge_name, sender, body, message_id=""):
        assert bridge_name == "bridge2"
        assert sender == "8801817201596"
        assert body == SINGLE
        return {"relayed": True, "status": "enqueued", "dest_number": "8801844836824",
                "outbound_bridge": "bridge2"}

    def _boom_classify(_text):
        hit.append("classify_intent")
        raise AssertionError("classify_intent ran after a relay claim")

    async def _boom_recruit(*a, **k):
        hit.append("recruitment_eligibility")
        raise AssertionError("recruitment_eligibility ran after a relay claim")

    async def _boom_process_message(*a, **k):
        hit.append("process_message")
        raise AssertionError("process_message (routing/Hermes/LLM) ran after a relay claim")

    async def _boom_save_draft(*a, **k):
        hit.append("_save_draft")
        raise AssertionError("a draft was created after a relay claim")

    def _boom_admin_review(*a, **k):
        hit.append("_build_admin_review_draft")
        raise AssertionError("admin-review draft rendered after a relay claim")

    monkeypatch.setattr("modules.payment_relay.maybe_relay_owner_payment", _fake_relay)
    monkeypatch.setattr(bp, "classify_intent", _boom_classify)
    monkeypatch.setattr(bp, "recruitment_eligibility", _boom_recruit)
    monkeypatch.setattr(bp, "process_message", _boom_process_message)
    if hasattr(bp, "_save_draft"):
        monkeypatch.setattr(bp, "_save_draft", _boom_save_draft)
    if hasattr(bp, "_build_admin_review_draft"):
        monkeypatch.setattr(bp, "_build_admin_review_draft", _boom_admin_review)

    out = await bp.process_bridge_inbound(
        "bridge2", "8801817201596", SINGLE, message_id="wamid.RELAY",
    )
    assert out is None
    assert hit == []


async def test_non_claim_continues_normal_pipeline(monkeypatch):
    ran = {"classify": False}

    async def _fake_relay(*, bridge_name, sender, body, message_id=""):
        return None  # rule does not apply

    def _sentinel_classify(_text):
        ran["classify"] = True
        raise RuntimeError("__reached_normal_pipeline__")

    monkeypatch.setattr("modules.payment_relay.maybe_relay_owner_payment", _fake_relay)
    monkeypatch.setattr(bp, "classify_intent", _sentinel_classify)

    with pytest.raises(RuntimeError, match="__reached_normal_pipeline__"):
        await bp.process_bridge_inbound(
            "bridge2", "8801700000000", SINGLE, message_id="wamid.NORMAL",
        )
    assert ran["classify"] is True


async def test_relay_gate_exception_does_not_break_routing(monkeypatch):
    async def _raising_relay(*, bridge_name, sender, body, message_id=""):
        raise RuntimeError("relay gate internal error")

    def _sentinel_classify(_text):
        raise RuntimeError("__reached_normal_pipeline__")

    monkeypatch.setattr("modules.payment_relay.maybe_relay_owner_payment", _raising_relay)
    monkeypatch.setattr(bp, "classify_intent", _sentinel_classify)

    # The relay gate error is swallowed; normal routing still proceeds.
    with pytest.raises(RuntimeError, match="__reached_normal_pipeline__"):
        await bp.process_bridge_inbound(
            "bridge2", "8801817201596", SINGLE, message_id="wamid.ERR",
        )
