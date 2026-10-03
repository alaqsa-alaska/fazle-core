"""Phase 6, 2026-08-26: Employee short-context intent refinement tests.

Covers modules.employee_conversation.refine_short_intent + _is_short_reply.

Tests verify:
  - Canonical short replies (হ্যাঁ / না / ৩২ / চট্টগ্রাম / ওটাই) are
    detected as short.
  - Bengali-digit-only short replies are detected.
  - Long messages (>=7 chars) are NOT short — no behavior change.
  - When an open Sprint-3A session exists AND raw_intent is greeting/
    chitchat/unknown/empty, the helper overrides to "confirmation".
  - When raw_intent is already specific (e.g. "salary_query"), the
    helper preserves it.
  - When no session exists, helper returns raw_intent unchanged.
  - DB lookup failure fails closed to raw_intent.
  - Hard guarantee: helper does NOT call create_transaction /
    _upsert_ledger / accounting_worker.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock

import pytest

from modules import employee_conversation
from modules.employee_conversation import _is_short_reply, refine_short_intent


# ─────────────────────────────────────────────────────────────────────
# 1-4: _is_short_reply unit tests (synchronous, no DB)
# ─────────────────────────────────────────────────────────────────────

def test_is_short_reply_detects_haan():
    assert _is_short_reply("হ্যাঁ") is True
    assert _is_short_reply("হ্যা") is True


def test_is_short_reply_detects_naa():
    assert _is_short_reply("না") is True
    assert _is_short_reply("নেই") is True


def test_is_short_reply_detects_bengali_digits():
    assert _is_short_reply("৩২") is True
    assert _is_short_reply("০০০") is True
    assert _is_short_reply("০") is True


def test_is_short_reply_detects_canonical_words():
    for w in ("আগেই", "ওটাই", "ওইটা", "সেটাই", "আগেরটা", "আগেরটার"):
        assert _is_short_reply(w) is True, f"expected short for {w!r}"


def test_is_short_reply_detects_already_said_class():
    assert _is_short_reply("আগেই বলেছি") is True
    assert _is_short_reply("ওটাই দিয়েছি") is True
    assert _is_short_reply("সেটা হয়ে গেছে") is True
    assert _is_short_reply("আগের কথা ছিল") is True


def test_is_short_reply_rejects_long_messages():
    assert _is_short_reply("আগেরটার কী হলো?") is False
    assert _is_short_reply("হ্যাঁ আমি বলেছি") is False  # >6 chars
    assert _is_short_reply("Hello world") is False
    assert _is_short_reply("") is False
    assert _is_short_reply("   ") is False


def test_is_short_reply_strips_trailing_punctuation():
    assert _is_short_reply("হ্যাঁ।") is True
    assert _is_short_reply("না?") is True
    assert _is_short_reply("৩২,") is True


# ─────────────────────────────────────────────────────────────────────
# 5-10: refine_short_intent async tests (mock get_conversation_session)
# ─────────────────────────────────────────────────────────────────────

_OPEN_SESSION = {
    "id": 99,
    "phone": "8801000000077",
    "step": "ec_amount",
    "purpose": "advance",
    "ctx": {"reason": "office", "amount": None, "payout": None},
}


@pytest.mark.asyncio
async def test_short_hyaan_with_open_session_overrides_to_confirmation():
    with patch_session(_OPEN_SESSION):
        result = await refine_short_intent(
            "8801000000077", "হ্যাঁ", "greeting",
        )
    assert result == "confirmation"


@pytest.mark.asyncio
async def test_short_naa_with_open_session_overrides_to_confirmation():
    with patch_session(_OPEN_SESSION):
        result = await refine_short_intent(
            "8801000000077", "না", "chitchat",
        )
    assert result == "confirmation"


@pytest.mark.asyncio
async def test_short_bengali_digit_with_open_session_overrides_to_confirmation():
    """৩২ as a numeric answer to a pending amount question."""
    with patch_session(_OPEN_SESSION):
        result = await refine_short_intent(
            "8801000000077", "৩২", "unknown",
        )
    assert result == "confirmation"


@pytest.mark.asyncio
async def test_already_said_with_open_session_overrides_to_confirmation():
    """আগেই বলেছি = "I already told you" — needs context."""
    with patch_session(_OPEN_SESSION):
        result = await refine_short_intent(
            "8801000000077", "আগেই বলেছি", "chitchat",
        )
    assert result == "confirmation"


@pytest.mark.asyncio
async def test_short_with_already_specific_intent_preserved():
    """If the upstream classifier already said salary_query, don't override."""
    with patch_session(_OPEN_SESSION):
        result = await refine_short_intent(
            "8801000000077", "৩২", "salary_query",
        )
    assert result == "salary_query"


@pytest.mark.asyncio
async def test_short_with_no_open_session_returns_raw_intent_unchanged():
    with patch_session(None):
        result = await refine_short_intent(
            "8801000000077", "হ্যাঁ", "greeting",
        )
    assert result == "greeting"


@pytest.mark.asyncio
async def test_long_message_returns_raw_intent_unchanged_even_with_session():
    """আগেরটার কী হলো? is a 12-char message — not short."""
    with patch_session(_OPEN_SESSION):
        result = await refine_short_intent(
            "8801000000077", "আগেরটার কী হলো?", "chitchat",
        )
    assert result == "chitchat"


@pytest.mark.asyncio
async def test_short_with_empty_raw_intent_overrides_to_confirmation():
    with patch_session(_OPEN_SESSION):
        result = await refine_short_intent(
            "8801000000077", "হ্যাঁ", "",
        )
    assert result == "confirmation"


@pytest.mark.asyncio
async def test_db_failure_fails_closed_to_raw_intent():
    """If get_conversation_session raises, return raw_intent unchanged."""
    from modules.employee_conversation import get_conversation_session
    original = get_conversation_session
    employee_conversation.get_conversation_session = AsyncMock(
        side_effect=RuntimeError("DB down")
    )
    try:
        result = await refine_short_intent(
            "8801000000077", "হ্যাঁ", "greeting",
        )
    finally:
        employee_conversation.get_conversation_session = original
    assert result == "greeting"


# ─────────────────────────────────────────────────────────────────────
# Hard financial-mutation guarantee (mirrors Phase 5 test)
# ─────────────────────────────────────────────────────────────────────

def test_no_financial_write_imports_in_ec_module_phase6():
    """Phase 6 must not have introduced any financial-write import."""
    import re as _re
    src_file = employee_conversation.__file__
    with open(src_file) as f:
        src = f.read()
    body = _re.sub(r'"""[\s\S]*?"""', '', src)
    body = _re.sub(r"'''[\s\S]*?'''", '', body)
    body = "\n".join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
    )
    assert _re.search(r"\bcreate_transaction\s*\(", body) is None
    assert _re.search(r"\b_upsert_ledger\s*\(", body) is None
    assert _re.search(r"\baccounting_worker\s*\(", body) is None
    assert "from modules.fazle_payroll_engine" not in body


# ─────────────────────────────────────────────────────────────────────
# Helper: patch get_conversation_session at the call-site import
# ─────────────────────────────────────────────────────────────────────

def patch_session(return_value):
    """Patch get_conversation_session at the module location where
    refine_short_intent imports it from (modules.employee_conversation)."""
    from unittest.mock import patch
    return patch.object(
        employee_conversation,
        "get_conversation_session",
        new=AsyncMock(return_value=return_value),
    )
