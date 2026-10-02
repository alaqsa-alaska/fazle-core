"""Unit tests — modules.bridge_poller.process_bridge_inbound wiring of the
deterministic owner->accountant payment relay.

Proves:
  (a) when a message is CLAIMED by the relay (success OR any held failure),
      process_bridge_inbound STOPS immediately — no intent classification,
      recruitment, full routing (process_message -> Hermes/LLM), draft
      creation, or admin-review-draft rendering;
  (b) a PROTECTED financial-relay message NEVER falls through to generic
      routing even if the relay internals raise (Owner directive 2026-08-30
      part A);
  (c) a message OUTSIDE the protected scope routes normally, unchanged.
"""
from __future__ import annotations

import pytest

import modules.bridge_poller as bp
import modules.payment_relay as pr
import modules.outbound as outbound

pytestmark = pytest.mark.unit


OWNER = "8801817201596"
SINGLE = "ID: 01998846707 Maruf islam 01998846707(N) 200/-"


@pytest.fixture(autouse=True)
def _relay_env(monkeypatch):
    monkeypatch.setenv("PAYMENT_RELAY_ENABLED", "true")
    for k in ("PAYMENT_RELAY_SOURCE_NUMBER", "PAYMENT_RELAY_DEST_NUMBER",
              "PAYMENT_RELAY_BRIDGE", "PAYMENT_RELAY_INBOUND_BRIDGE",
              "PAYMENT_RELAY_RECEIVING_NUMBER"):
        monkeypatch.delenv(k, raising=False)
    _MAP = {"bridge1": "8801958122300", "bridge2": "8801880446111", "bridge3": "8801805529211"}
    monkeypatch.setattr(pr, "_configured_bridge_number",
                        lambda name: _MAP.get(str(name or "").strip().lower()))


def _tripwires(monkeypatch):
    hit = []

    def _boom_classify(_text):
        hit.append("classify_intent"); raise AssertionError("classify_intent ran")

    async def _boom_recruit(*a, **k):
        hit.append("recruitment_eligibility"); raise AssertionError("recruitment_eligibility ran")

    async def _boom_process_message(*a, **k):
        hit.append("process_message"); raise AssertionError("process_message ran")

    async def _boom_save_draft(*a, **k):
        hit.append("_save_draft"); raise AssertionError("_save_draft ran")

    def _boom_admin_review(*a, **k):
        hit.append("_build_admin_review_draft"); raise AssertionError("_build_admin_review_draft ran")

    monkeypatch.setattr(bp, "classify_intent", _boom_classify)
    monkeypatch.setattr(bp, "recruitment_eligibility", _boom_recruit)
    monkeypatch.setattr(bp, "process_message", _boom_process_message)
    if hasattr(bp, "_save_draft"):
        monkeypatch.setattr(bp, "_save_draft", _boom_save_draft)
    if hasattr(bp, "_build_admin_review_draft"):
        monkeypatch.setattr(bp, "_build_admin_review_draft", _boom_admin_review)
    return hit


async def test_relay_claim_stops_all_further_processing(monkeypatch):
    async def _fake_relay(*, bridge_name, sender, body, message_id=""):
        assert bridge_name == "bridge2" and sender == OWNER and body == SINGLE
        return {"relayed": True, "claimed": True, "status": "enqueued",
                "dest_number": "8801844836824", "outbound_bridge": "bridge2"}

    monkeypatch.setattr(pr, "maybe_relay_owner_payment", _fake_relay)
    hit = _tripwires(monkeypatch)

    out = await bp.process_bridge_inbound("bridge2", OWNER, SINGLE, message_id="wamid.RELAY")
    assert out is None
    assert hit == []


async def test_protected_message_held_on_enqueue_failure_stops(monkeypatch):
    """Real maybe_relay_owner_payment; bridge2 send fails -> held-failure claim
    -> STOP. No process_message / draft / admin-review / classify / recruitment.
    No outbound alternate bridge."""
    sends = []

    async def _raise_enqueue(*a, **k):
        sends.append((a, k))
        raise RuntimeError("bridge2 unavailable")

    monkeypatch.setattr(outbound, "enqueue", _raise_enqueue)
    hit = _tripwires(monkeypatch)

    out = await bp.process_bridge_inbound("bridge2", OWNER, SINGLE, message_id="wamid.HELD")
    assert out is None
    assert hit == []
    # enqueue was attempted exactly once, on bridge2, with NO fallback channel
    assert len(sends) == 1
    _a, _k = sends[0]
    assert _k.get("source_bridge") == "bridge2"
    assert _k.get("fallback_channel") is None


async def test_protected_message_held_when_relay_gate_itself_raises(monkeypatch):
    """Defense in depth: if maybe_relay_owner_payment raises, a PROTECTED
    message is still HELD + STOPped via the is_protected_payment_candidate
    re-check — never handed to generic routing."""
    async def _raising_relay(*, bridge_name, sender, body, message_id=""):
        raise RuntimeError("relay gate blew up")

    monkeypatch.setattr(pr, "maybe_relay_owner_payment", _raising_relay)
    hit = _tripwires(monkeypatch)

    out = await bp.process_bridge_inbound("bridge2", OWNER, SINGLE, message_id="wamid.RAISE")
    assert out is None
    assert hit == []


async def test_nonprotected_message_relay_gate_raises_continues_routing(monkeypatch):
    """A message OUTSIDE the protected scope (wrong sender) still routes
    normally even if the relay gate raises."""
    async def _raising_relay(*, bridge_name, sender, body, message_id=""):
        raise RuntimeError("relay gate blew up")

    def _sentinel_classify(_text):
        raise RuntimeError("__reached_normal_pipeline__")

    monkeypatch.setattr(pr, "maybe_relay_owner_payment", _raising_relay)
    monkeypatch.setattr(bp, "classify_intent", _sentinel_classify)

    with pytest.raises(RuntimeError, match="__reached_normal_pipeline__"):
        await bp.process_bridge_inbound("bridge2", "8801700000000", SINGLE, message_id="wamid.EXT")


async def test_non_claim_continues_normal_pipeline(monkeypatch):
    async def _fake_relay(*, bridge_name, sender, body, message_id=""):
        return None  # outside protected scope

    def _sentinel_classify(_text):
        raise RuntimeError("__reached_normal_pipeline__")

    monkeypatch.setattr(pr, "maybe_relay_owner_payment", _fake_relay)
    monkeypatch.setattr(bp, "classify_intent", _sentinel_classify)

    with pytest.raises(RuntimeError, match="__reached_normal_pipeline__"):
        await bp.process_bridge_inbound("bridge2", "8801700000000", SINGLE, message_id="wamid.NORMAL")
