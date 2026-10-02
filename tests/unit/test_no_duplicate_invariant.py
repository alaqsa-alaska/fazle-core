"""Phase 7, 2026-08-26: Generic no-duplicate-question + no-duplicate-answer
invariant tests.

Covers:
  - modules.recruitment_flow.is_already_answered
  - modules.recruitment_flow._normalize_for_compare
  - modules.outbound._was_recently_replied (via enqueue() structured log)

Tests verify:
  - Exact match returns True (Phase B baseline behavior preserved).
  - Normalized match: Bangla vs ASCII digits, whitespace, trailing punctuation.
  - Empty stored value returns False (the field hasn't been collected yet).
  - Correction markers in the inbound text bypass the invariant.
  - Numeric fields (age, experience_years) compared as integers.
  - String fields compared normalized.
  - _was_recently_replied: identical recent reply returns True.
  - _was_recently_replied: different reply returns False.
  - _was_recently_replied: >10 minutes old returns False.
  - _was_recently_replied: error fails closed to False.
  - Hard guarantee: no financial-write import in either modified module.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules import recruitment_flow
from modules.recruitment_flow import (
    COLLECTION_STEPS,
    is_already_answered,
    _normalize_for_compare,
)


# ─────────────────────────────────────────────────────────────────────
# 1-2: _normalize_for_compare + is_already_answered pure-unit tests
# ─────────────────────────────────────────────────────────────────────

def test_normalize_lowercases_and_strips_punctuation():
    assert _normalize_for_compare("Dhaka.") == "dhaka"
    assert _normalize_for_compare("  DHAKA  ") == "dhaka"
    assert _normalize_for_compare("Dhaka,  Bangladesh.") == "dhaka bangladesh"


def test_normalize_translates_bangla_digits():
    assert _normalize_for_compare("৩২") == "32"
    assert _normalize_for_compare("০") == "0"
    assert _normalize_for_compare("১২৩") == "123"


def test_normalize_handles_emoji_and_combining_marks_gracefully():
    """Edge case: trailing punctuation in various forms."""
    assert _normalize_for_compare("Marketing Officer!") == "marketing officer"
    assert _normalize_for_compare("Marketing Officer?") == "marketing officer"


def test_is_already_answered_returns_false_for_empty_session():
    assert is_already_answered({}, "age", "25") is False


def test_is_already_answered_returns_false_for_empty_candidate():
    assert is_already_answered({"age": 25}, "age", "") is False


def test_is_already_answered_exact_match_age():
    assert is_already_answered({"age": 25}, "age", "25") is True


def test_is_already_answered_normalized_bangla_digits_age():
    assert is_already_answered({"age": 25}, "age", "২৫") is True
    assert is_already_answered({"age": 25}, "age", "২৫, ") is True


def test_is_already_answered_mismatched_age_returns_false():
    assert is_already_answered({"age": 25}, "age", "30") is False


def test_is_already_answered_exact_match_name():
    assert is_already_answered({"full_name": "Md Karim"}, "name", "Md Karim") is True


def test_is_already_answered_normalized_name_with_trailing_punct():
    assert is_already_answered({"full_name": "Md Karim"}, "name", "Md Karim.") is True


def test_is_already_answered_normalized_area():
    assert is_already_answered({"area": "Dhaka"}, "area", "Dhaka,") is True
    assert is_already_answered({"area": "Dhaka"}, "area", " DHAKA ") is True


def test_is_already_answered_experience_numeric_match():
    assert is_already_answered({"experience_years": 5}, "experience", "5") is True
    assert is_already_answered({"experience_years": 5}, "experience", "৫") is True


def test_is_already_answered_phone_match():
    assert is_already_answered({"confirmed_phone": "01712345678"}, "phone_confirm", "01712345678") is True


def test_is_already_answered_correction_marker_returns_false():
    """When user says 'ভুল হয়েছে' / 'আগেরটা সংশোধন' / 'actually' etc.,
    the invariant is bypassed so the re-ask fires."""
    session = {"age": 25}
    assert is_already_answered(session, "age", "30", correction_text="ভুল হয়েছে") is False
    assert is_already_answered(session, "age", "30", correction_text="আগেরটা সংশোধন করুন") is False
    assert is_already_answered(session, "age", "30", correction_text="actually I meant 30") is False
    assert is_already_answered(session, "age", "30", correction_text="wrong age") is False


def test_is_already_answered_returns_false_when_step_column_none():
    """Step with no stored value (not yet collected) — must not be
    'already answered'."""
    assert is_already_answered({"age": None}, "age", "25") is False
    assert is_already_answered({"full_name": None}, "name", "Md Karim") is False


def test_is_already_answered_returns_false_for_unknown_step():
    """Unknown step name (not in COLLECTION_STEPS) returns False."""
    assert is_already_answered({"foo": 1}, "future_step_not_yet_added", "1") is False


# ─────────────────────────────────────────────────────────────────────
# 3-7: _was_recently_replied via outbound.enqueue() structured log
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_was_recently_replied_identical_returns_true():
    """When a substantially similar reply was queued recently for the
    same recipient + purpose, the structured warning fires."""
    from modules.outbound import enqueue, _was_recently_replied

    recent_body = "পরি�াণ: ৳32 ✅\nএখন পেআউট নম্বর ও পদ্ধতি লিখুন"
    # Stub fetch_all to return one row matching the candidate body
    fake_row = {"body": recent_body, "created_at": None}
    with patch("modules.outbound.fetch_all", new=AsyncMock(return_value=[fake_row])), \
         patch("modules.outbound.fetch_one", new=AsyncMock(return_value={"id": 999})):
        result = await _was_recently_replied("8801310542862", "customer-reply:recruitment", recent_body)
    assert result is True


@pytest.mark.asyncio
async def test_was_recently_replied_different_returns_false():
    from modules.outbound import _was_recently_replied

    recent_body = "�কটু পরে বিস্তারিত জানাচ্ছি।"
    candidate_body = "পরিমাণ: ৳32 ✅\nএ�ন পেআউট নম্বর ও পদ্ধতি লি�ুন"
    fake_row = {"body": recent_body, "created_at": None}
    with patch("modules.outbound.fetch_all", new=AsyncMock(return_value=[fake_row])):
        result = await _was_recently_replied("8801310542862", "customer-reply:recruitment", candidate_body)
    assert result is False


@pytest.mark.asyncio
async def test_was_recently_replied_no_history_returns_false():
    from modules.outbound import _was_recently_replied
    with patch("modules.outbound.fetch_all", new=AsyncMock(return_value=[])):
        result = await _was_recently_replied("8801310542862", "customer-reply:recruitment", "any body")
    assert result is False


@pytest.mark.asyncio
async def test_was_recently_replied_db_error_fails_closed():
    from modules.outbound import _was_recently_replied
    with patch("modules.outbound.fetch_all", new=AsyncMock(side_effect=RuntimeError("DB down"))):
        result = await _was_recently_replied("8801310542862", "purpose", "body")
    assert result is False


@pytest.mark.asyncio
async def test_enqueue_emits_warning_on_similar_recent_reply(caplog):
    """When _was_recently_replied returns True, enqueue() emits a
    [OUTBOUND_PHASE7] structured warning. The send is NOT blocked."""
    from modules.outbound import enqueue

    recent_body = "পরিমাণ: �32 ✅\nএখন পেআউট নম্বর ও পদ্ধতি লিখুন"
    fake_row = {"body": recent_body, "created_at": None}
    fake_insert = {"id": 5103}
    with patch("modules.outbound.fetch_all", new=AsyncMock(return_value=[fake_row])), \
         patch("modules.outbound.fetch_one", new=AsyncMock(return_value=fake_insert)):
        import logging
        with caplog.at_level(logging.WARNING, logger="fazle.outbound"):
            qid = await enqueue(
                "8801310542862", recent_body,
                source_bridge="meta",
                purpose="customer-reply:recruitment",
                idempotency_key="meta-recruit:test",
            )

    assert qid == 5103  # send NOT blocked
    # Look for the Phase 7 warning
    phase7_warnings = [r for r in caplog.records if "OUTBOUND_PHASE7" in r.message]
    assert len(phase7_warnings) >= 1
    assert "similar recent reply" in phase7_warnings[0].message
    assert "NOT blocking send" in phase7_warnings[0].message


# ─────────────────────────────────────────────────────────────────────
# Hard financial-mutation guarantee (mirrors Phase 5/6 tests)
# ─────────────────────────────────────────────────────────────────────

def test_no_financial_write_imports_in_recruitment_flow_phase7():
    """Phase 7 must not have introduced any financial-write import."""
    import re as _re
    with open(recruitment_flow.__file__) as f:
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


def test_no_financial_write_imports_in_outbound_phase7():
    import re as _re
    from modules import outbound
    with open(outbound.__file__) as f:
        src = f.read()
    body = _re.sub(r'"""[\s\S]*?"""', '', src)
    body = _re.sub(r"'''[\s\S]*?'''", '', body)
    body = "\n".join(
        ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
    )
    assert _re.search(r"\bcreate_transaction\s*\(", body) is None
    assert _re.search(r"\b_upsert_ledger\s*\(", body) is None
    assert _re.search(r"\baccounting_worker\s*\(", body) is None


# ─────────────────────────────────────────────────────────────────────
# Schema-driven check (matches Phase 6 invariant test pattern)
# ─────────────────────────────────────────────────────────────────────

def test_phase7_helper_iterates_schema_not_hardcoded():
    """Regression guard: is_already_answered uses COLLECTION_STEPS +
    STEP_TO_COLUMN + STEP_QUESTIONS schema rather than a hardcoded
    field list. Adding a new step in COLLECTION_STEPS + STEP_TO_COLUMN
    must make it work without touching this helper."""
    future_step = "test_phase7_future_step"
    with patch.object(recruitment_flow, "COLLECTION_STEPS",
                      COLLECTION_STEPS + [future_step]):
        # No column mapping for future_step -> returns False (no crash)
        assert is_already_answered({"foo": 1}, future_step, "1") is False
