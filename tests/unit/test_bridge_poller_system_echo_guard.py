"""Unit tests — modules.bridge_poller cross-bridge system-notification echo guard.

Live bug (2026-08-30): modules.supervised_draft enqueued a "📋 ADMIN REVIEW
DRAFT" card to the primary admin number (= bridge2's own paired number) via
bridge1. It bounced back onto bridge2 as a genuine is_from_me=0 inbound from
bridge1's number, was re-classified as a new admin message, and a SECOND,
nested review card was rendered (drafts #4514 -> #4515).

The guard consumes such an echo iff BOTH:
  (1) sender is one of our own OTHER bridge numbers (pure provenance), AND
  (2) the body matches _is_system_generated_notification() /
      SYSTEM_NOTIFICATION_TITLE_PREFIXES.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import modules.bridge_poller as bp

pytestmark = pytest.mark.unit


BR1 = "8801958122300"
BR2 = "8801880446111"
BR3 = "8801805529211"

CARD = (
    "📋 ADMIN REVIEW DRAFT\n"
    "─────────────────\n"
    "Sender: (unknown) | 8801817201596\n"
    "Draft No: 4514\n"
    "Category: Office/Internal\n"
    "─────────────────\n"
    "Sender's Message:\n"
    "ID: 01813225542 Anowar SG 01813225542(N) 200/-\n"
    "─────────────────\n"
    "AI Generated Reply:\n"
    "এই তথ্যটি নিরাপদভাবে দেওয়া যাচ্ছে না। অফিসে যোগাযোগ করুন।"
)


@pytest.fixture(autouse=True)
def _bridge_numbers(monkeypatch):
    monkeypatch.setattr(
        bp, "_settings",
        SimpleNamespace(bridge1_number=BR1, bridge2_number=BR2, bridge3_number=BR3),
    )


# ── helper-level ────────────────────────────────────────────────────────────

def test_echo_from_other_bridge_number_with_system_card_is_an_echo():
    assert bp._is_cross_bridge_system_echo("bridge2", BR1, CARD) is True
    assert bp._is_cross_bridge_system_echo("bridge1", BR2, "⚠️ RELEASE REVIEW REQUIRED\nfoo") is True


def test_external_sender_with_card_prefix_is_NOT_an_echo():
    # a real external user pasting text that starts with the prefix
    assert bp._is_cross_bridge_system_echo("bridge2", "8801777777777", CARD) is False


def test_own_bridge_number_with_ordinary_text_is_NOT_an_echo():
    assert bp._is_cross_bridge_system_echo("bridge2", BR1, "hello how are you?") is False
    assert bp._is_cross_bridge_system_echo("bridge2", BR1, "ভাই কালকে আসবেন?") is False


def test_same_bridge_self_message_is_NOT_a_cross_bridge_echo():
    # sender == the bridge it arrived on → not "another" bridge
    assert bp._is_cross_bridge_system_echo("bridge2", BR2, CARD) is False


def test_hermes_prefix_is_left_to_the_message_router_guard():
    # "🤖 Hermes:" is NOT a SYSTEM_NOTIFICATION_TITLE_PREFIXES entry, so this
    # guard does not consume it — the existing message_router cross-bridge
    # Hermes-echo guard still handles it.
    assert bp._is_cross_bridge_system_echo("bridge2", BR1, "🤖 Hermes: your answer") is False


# ── integration through process_bridge_inbound ──────────────────────────────

def _tripwires(monkeypatch):
    hit = []

    def _c(_t):
        hit.append("classify_intent"); raise AssertionError("classify_intent ran")

    async def _r(*a, **k):
        hit.append("recruitment_eligibility"); raise AssertionError("recruitment_eligibility ran")

    async def _pm(*a, **k):
        hit.append("process_message"); raise AssertionError("process_message ran")

    async def _sd(*a, **k):
        hit.append("_save_draft"); raise AssertionError("_save_draft ran")

    def _ar(*a, **k):
        hit.append("_build_admin_review_draft"); raise AssertionError("_build_admin_review_draft ran")

    monkeypatch.setattr(bp, "classify_intent", _c)
    monkeypatch.setattr(bp, "recruitment_eligibility", _r)
    monkeypatch.setattr(bp, "process_message", _pm)
    if hasattr(bp, "_save_draft"):
        monkeypatch.setattr(bp, "_save_draft", _sd)
    if hasattr(bp, "_build_admin_review_draft"):
        monkeypatch.setattr(bp, "_build_admin_review_draft", _ar)
    return hit


async def test_bounced_card_is_consumed_no_nested_draft(monkeypatch):
    """Reproduction of the #4514/#4515 pattern: the card, arriving on bridge2
    from bridge1's number, is consumed — no classification, no process_message,
    no draft of any kind."""
    hit = _tripwires(monkeypatch)
    out = await bp.process_bridge_inbound("bridge2", BR1, CARD, message_id="wamid.ECHO")
    assert out is None
    assert hit == []


async def test_external_user_card_prefix_still_routes(monkeypatch):
    """An external user whose message merely starts with "📋 ADMIN REVIEW
    DRAFT" is NOT suppressed — normal routing remains available."""
    def _sentinel(_t):
        raise RuntimeError("__reached_normal_pipeline__")
    monkeypatch.setattr(bp, "classify_intent", _sentinel)
    with pytest.raises(RuntimeError, match="__reached_normal_pipeline__"):
        await bp.process_bridge_inbound("bridge2", "8801777777777", CARD, message_id="wamid.EXTCARD")


async def test_own_bridge_number_ordinary_text_still_routes(monkeypatch):
    def _sentinel(_t):
        raise RuntimeError("__reached_normal_pipeline__")
    monkeypatch.setattr(bp, "classify_intent", _sentinel)
    with pytest.raises(RuntimeError, match="__reached_normal_pipeline__"):
        await bp.process_bridge_inbound("bridge2", BR1, "just checking in", message_id="wamid.HUMAN")
