"""Phase 3, 2026-08-26: recruitment session-state guard unit tests.

Covers modules.hermes_dispatch._build_recruitment_session_state_block and
shared.reply_policy.build_whatsapp_recruitment_policy's session_state kwarg.

The helper reads fazle_recruitment_sessions for a phone, iterates
COLLECTION_STEPS (the schema), classifies each step as collected or
missing, and returns a Bengali block the recruitment prompt uses to
prevent re-asking already-collected fields.

Tests verify:
  - generic over STEP_QUESTIONS keys (schema-driven, not hardcoded)
  - funnel_stage in ('collecting', 'new') → block present
  - funnel_stage past collection ('scored', 'expired', ...) → empty
  - numeric fields (age/experience_years) handled with is not None,
    so a stored 0 renders as collected
  - missing-field list excludes any step absent from STEP_QUESTIONS
  - DB lookup failure fails closed (empty + warning)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules import hermes_dispatch
from modules.recruitment_flow import COLLECTION_STEPS, STEP_QUESTIONS


def _row(**kwargs):
    """Build a session row dict with sensible defaults."""
    base = {
        "full_name": None,
        "age": None,
        "area": None,
        "job_preference": None,
        "experience_years": None,
        "confirmed_phone": None,
        "collection_step": "name",
        "funnel_stage": "collecting",
    }
    base.update(kwargs)
    return base


@pytest.mark.asyncio
async def test_helper_skips_when_no_session():
    with patch("app.database.fetch_one", new=AsyncMock(return_value=None)):
        result = await hermes_dispatch._build_recruitment_session_state_block(
            "8801000000001"
        )
    assert result == ""


@pytest.mark.asyncio
async def test_helper_skips_when_funnel_stage_past_collection():
    for stage in ("scored", "applied", "expired", "ignored", "rejected"):
        with patch(
            "app.database.fetch_one",
            new=AsyncMock(return_value=_row(funnel_stage=stage, full_name="X")),
        ):
            result = await hermes_dispatch._build_recruitment_session_state_block(
                "8801000000002"
            )
        assert result == "", f"expected empty for funnel_stage={stage}, got: {result!r}"


@pytest.mark.asyncio
async def test_helper_reports_collected_and_missing_for_collecting_session():
    row = _row(
        collection_step="job_preference",
        full_name="Md Karim",
        age=30,
        area="Dhaka",
        job_preference=None,
        experience_years=0,
        confirmed_phone=None,
    )
    with patch("app.database.fetch_one", new=AsyncMock(return_value=row)):
        result = await hermes_dispatch._build_recruitment_session_state_block(
            "8801000000003"
        )
    assert result != ""
    # Current step is job_preference
    assert "পদ-পছন্দ" in result
    # Already-collected: name, age, area, experience (=0, treated as collected)
    assert "নাম=Md Karim" in result
    assert "বয়স=" in result
    assert "এলাকা=Dhaka" in result
    assert "অভিজ্ঞতা=" in result
    # Missing: job_preference, phone_confirm
    missing_section = result.split("এখনো প্রয়োজন (still missing):", 1)[1].split(
        "নিয়ম"
    )[0]
    assert "পদ-পছন্দ" in missing_section
    assert "মোবাইল" in missing_section
    # Rule line present
    assert "ইতিমধ্যে সংগৃহীত তথ্য আর জিজ্ঞেস করবে না" in result


@pytest.mark.asyncio
async def test_helper_iterates_schema_not_hardcoded_list():
    """Regression guard: the helper must use COLLECTION_STEPS / STEP_QUESTIONS
    schema rather than a hardcoded field list. Adding a new field to
    STEP_QUESTIONS + COLLECTION_STEPS must make it appear in the output
    WITHOUT touching this helper."""
    future_step = "test_future_step"
    original_map = dict(hermes_dispatch._STEP_TO_COLUMN)
    original_disp = dict(hermes_dispatch._STEP_DISPLAY_NAME)
    original_steps = list(COLLECTION_STEPS)
    original_questions = dict(STEP_QUESTIONS)
    try:
        STEP_QUESTIONS[future_step] = "ভবিষ্যৎ প্রশ্ন?"
        hermes_dispatch._STEP_TO_COLUMN[future_step] = "future_col"
        hermes_dispatch._STEP_DISPLAY_NAME[future_step] = "ভবিষ্যৎ"
        COLLECTION_STEPS.append(future_step)

        row = _row(
            collection_step="test_future_step",
            full_name="Md Karim",
            future_col="future_value",
        )
        with patch("app.database.fetch_one", new=AsyncMock(return_value=row)):
            result = await hermes_dispatch._build_recruitment_session_state_block(
                "8801000000004"
            )
        assert "future_value" in result
        assert "ভবিষ্যৎ" in result
    finally:
        hermes_dispatch._STEP_TO_COLUMN.clear()
        hermes_dispatch._STEP_TO_COLUMN.update(original_map)
        hermes_dispatch._STEP_DISPLAY_NAME.clear()
        hermes_dispatch._STEP_DISPLAY_NAME.update(original_disp)
        while COLLECTION_STEPS and COLLECTION_STEPS[-1] == future_step:
            COLLECTION_STEPS.pop()
        STEP_QUESTIONS.clear()
        STEP_QUESTIONS.update(original_questions)


@pytest.mark.asyncio
async def test_helper_numeric_zero_is_collected():
    """Stored 0 in age or experience_years must be treated as a real
    collected value, not falsy-skipped (which would make Hermes re-ask)."""
    row = _row(collection_step="name", age=0, experience_years=0)
    with patch("app.database.fetch_one", new=AsyncMock(return_value=row)):
        result = await hermes_dispatch._build_recruitment_session_state_block(
            "8801000000005"
        )
    # Bengali digits for 0 = ০
    assert "বয়স=০" in result
    assert "অভিজ্ঞতা=০" in result


@pytest.mark.asyncio
async def test_helper_db_failure_fails_closed():
    """If fetch_one raises, helper must return "" and log a warning,
    never raise into the caller."""
    with patch(
        "app.database.fetch_one",
        new=AsyncMock(side_effect=RuntimeError("simulated DB down")),
    ):
        result = await hermes_dispatch._build_recruitment_session_state_block(
            "8801000000006"
        )
    assert result == ""


@pytest.mark.asyncio
async def test_helper_excludes_steps_missing_from_step_questions():
    """If a step is in COLLECTION_STEPS but NOT in STEP_QUESTIONS (schema
    drift), the helper must skip its DISPLAY from the block (no raw
    key name, no undefined label)."""
    original_questions = dict(STEP_QUESTIONS)
    try:
        STEP_QUESTIONS.pop("phone_confirm", None)
        row = _row(
            collection_step="phone_confirm",
            full_name="X",
            confirmed_phone="01712345678",
        )
        with patch("app.database.fetch_one", new=AsyncMock(return_value=row)):
            result = await hermes_dispatch._build_recruitment_session_state_block(
                "8801000000007"
            )
        # No raw 'phone_confirm' key string in the output
        assert "phone_confirm" not in result
        # The display label "মোবাইল নম্বর" must NOT appear
        assert "মোবাইল নম্বর" not in result
        # And since the only fields in this row that are also in
        # STEP_QUESTIONS are full_name (collected) and the rest missing,
        # the missing section should list them — but NOT phone_confirm.
        missing_section = result.split("এখনো প্রয়োজন (still missing):", 1)[1]
        assert "phone_confirm" not in missing_section
        assert "মোবাইল নম্বর" not in missing_section
    finally:
        STEP_QUESTIONS.clear()
        STEP_QUESTIONS.update(original_questions)


def test_prompt_builder_accepts_session_state_kwarg():
    """Backward-compat: build_whatsapp_recruitment_policy() with no
    session_state must produce the same prompt as before (no 5th section).
    With session_state, the new section appears between Knowledge and
    Conversation."""
    from shared.reply_policy import build_whatsapp_recruitment_policy

    base = build_whatsapp_recruitment_policy(
        source="bridge3",
        user_message="hi",
        kb_context="KB",
        history="",
    )
    assert "সেশন অবস্থা" not in base
    # Empty string session_state must NOT add the section (backward-compat)
    base_with_empty = build_whatsapp_recruitment_policy(
        source="bridge3", user_message="hi", kb_context="KB", history="",
        session_state="",
    )
    assert "সেশন অবস্থা" not in base_with_empty
    assert base == base_with_empty

    # Real session_state adds the new section
    with_state = build_whatsapp_recruitment_policy(
        source="bridge3",
        user_message="hi",
        kb_context="KB",
        history="",
        session_state="- বর্তমান ধাপ: নাম\n- ইতিমধ্যে সংগৃহীত: —\n- এখনো প্রয়োজন: নাম",
    )
    assert "সেশন অবস্থা" in with_state
    # New section appears AFTER Knowledge
    assert with_state.index("জ্ঞান ও তথ্য") < with_state.index("সেশন অবস্থা")
    # New section appears AFTER the role/business-rules/workflow sections
    assert with_state.index("ব্যবসায়িক নিয়ম") < with_state.index("সেশন অবস্থা")
    # New section appears BEFORE the User Question section
    assert with_state.index("সেশন অবস্থা") < with_state.index("## প্রশ্ন")
    # And the new prompt is materially different from base
    assert base != with_state
