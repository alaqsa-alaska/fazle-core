"""
Unit tests — modules/recruitment_detector (2026-07-11)

3-layer recruitment intent detection, additive to the existing flat
keyword check in modules.recruitment_flow.is_recruitment_trigger.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


class TestEntityScoring:
    def test_decisive_category_triggers_alone(self):
        from modules.recruitment_detector import _score_entities, ENTITY_THRESHOLD
        score, cats = _score_entities("ভাই সুযোগ দেন")
        assert score >= ENTITY_THRESHOLD
        assert "opportunity_ask" in cats

    def test_two_supporting_categories_combine(self):
        from modules.recruitment_detector import _score_entities, ENTITY_THRESHOLD
        score, cats = _score_entities("আমি available আছি, আগে security তে ছিলাম")
        assert score >= ENTITY_THRESHOLD
        assert "availability" in cats and "experience_mention" in cats

    def test_single_supporting_category_alone_is_not_enough(self):
        from modules.recruitment_detector import _score_entities, ENTITY_THRESHOLD
        score, _ = _score_entities("আমি available আছি এখন")
        assert score < ENTITY_THRESHOLD

    def test_weak_category_alone_never_triggers(self):
        """'আমার নাম রহিম' appears constantly in non-recruitment contexts."""
        from modules.recruitment_detector import _score_entities, ENTITY_THRESHOLD
        score, _ = _score_entities("আমার নাম রহিম")
        assert score < ENTITY_THRESHOLD

    def test_unrelated_message_scores_zero(self):
        from modules.recruitment_detector import _score_entities
        score, cats = _score_entities("কাল payment কবে হবে?")
        assert score == 0
        assert cats == []

    def test_empty_message_scores_zero(self):
        from modules.recruitment_detector import _score_entities
        score, cats = _score_entities("")
        assert score == 0


class TestDetectRecruitmentIntent:
    async def test_layer1_entity_detects_decisive_phrase(self):
        from modules.recruitment_detector import detect_recruitment_intent
        result = await detect_recruitment_intent(text="ভাই সুযোগ দেন", phone="8801999000111", intent="unknown")
        assert result["is_recruitment"] is True
        assert result["layer"] == "entity"

    async def test_layer2_reuses_already_computed_intent_no_new_llm_call(self):
        """Layer 2 must not fire a second LLM call -- it only reads the
        intent value message_router already computed this turn."""
        from modules.recruitment_detector import detect_recruitment_intent
        with patch("app.llm.classify_intent_llm", new_callable=AsyncMock) as mock_llm:
            result = await detect_recruitment_intent(
                text="এই বিষয়ে জানতে চাই", phone="8801999000111", intent="recruitment",
            )
        assert result["is_recruitment"] is True
        assert result["layer"] == "intent"
        assert not mock_llm.called

    async def test_no_layer_matches_returns_false(self):
        from modules.recruitment_detector import detect_recruitment_intent
        with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)), \
             patch("modules.message_router.get_recent_history", new=AsyncMock(return_value=[])):
            result = await detect_recruitment_intent(text="হ্যালো", phone="8801999000111", intent="unknown")
        assert result["is_recruitment"] is False
        assert result["layer"] == "none"

    async def test_layer3_active_session_detects(self):
        from modules.recruitment_detector import detect_recruitment_intent
        with patch(
            "modules.recruitment_flow.get_active_session",
            new=AsyncMock(return_value={"collection_step": "age"}),
        ):
            result = await detect_recruitment_intent(text="২৮", phone="8801999000111", intent="unknown")
        assert result["is_recruitment"] is True
        assert result["layer"] == "context"

    async def test_context_check_never_raises_on_db_error(self):
        from modules.recruitment_detector import detect_recruitment_intent
        with patch(
            "modules.recruitment_flow.get_active_session",
            new=AsyncMock(side_effect=RuntimeError("db down")),
        ):
            result = await detect_recruitment_intent(text="হ্যালো", phone="8801999000111", intent="unknown")
        assert result["is_recruitment"] is False  # degrades safely, never raises

    async def test_empty_text_returns_false_immediately(self):
        from modules.recruitment_detector import detect_recruitment_intent
        result = await detect_recruitment_intent(text="", phone="8801999000111", intent="unknown")
        assert result["is_recruitment"] is False
        assert result["layer"] == "none"


class TestRecruitmentEligibilityIntegration:
    """The detector is wired into recruitment_flow.recruitment_eligibility(),
    not message_router directly -- that's the single existing decision
    function message_router already calls."""

    async def test_natural_phrase_now_eligible_via_detector(self):
        from modules.recruitment_flow import recruitment_eligibility
        with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)):
            result = await recruitment_eligibility(
                "8801999000111", "ভাই সুযোগ দেন", role="candidate", intent="unknown",
            )
        assert result["eligible"] is True
        assert result["reason"] == "detector_entity"
        assert result["autosend"] is True  # entity layer is high-confidence -> autosend

    async def test_intent_layer_result_is_draft_only_not_autosend(self):
        """Lower-confidence layers (intent/context) must go to draft review,
        matching the existing session_followup_draft precedent -- never
        autosend on a fuzzy signal."""
        from modules.recruitment_flow import recruitment_eligibility
        with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)):
            result = await recruitment_eligibility(
                "8801999000111", "এই বিষয়ে আরেকটু বলুন", role="candidate", intent="recruitment",
            )
        assert result["eligible"] is True
        assert result["reason"] == "detector_intent"
        assert result["autosend"] is False

    async def test_operational_role_still_excluded_before_detector_runs(self):
        """Hard exclusion gates (role/intent) must still short-circuit before
        the detector ever runs -- an accountant's message about vacancies
        must never be treated as a recruitment inquiry."""
        from modules.recruitment_flow import recruitment_eligibility
        result = await recruitment_eligibility(
            "8801999000111", "ভাই সুযোগ দেন", role="accountant", intent="unknown",
        )
        assert result["eligible"] is False
        assert result["reason"] == "operational_identity"


class TestUnknownSenderSoftAsk:
    """
    A brand-new number with zero prior history anywhere (identity_brain's
    phone-only secondary-evidence checks already ran and found nothing,
    hence role='unknown' rather than 'employee') gets a soft ask prepended
    to the first recruitment reply -- covers an existing employee texting
    from a genuinely new SIM. This is a soft ask, not a gate: the funnel
    proceeds identically either way.
    """

    async def test_unknown_role_gets_soft_ask_prepended(self):
        from modules.recruitment_flow import intake_message, UNKNOWN_SENDER_SOFT_ASK
        with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=None)), \
             patch("modules.recruitment_flow._create_session", new=AsyncMock(return_value=99)):
            result = await intake_message("8801999000111", "চাকরি দরকার", role="unknown")
        assert result["action"] == "created"
        assert result["reply"].startswith(UNKNOWN_SENDER_SOFT_ASK)

    async def test_candidate_role_does_not_get_soft_ask(self):
        """role='candidate' already means identity_brain matched an explicit
        job-related keyword -- not the 'truly unrecognized' case this ask
        targets."""
        from modules.recruitment_flow import intake_message, STEP_QUESTIONS
        with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=None)), \
             patch("modules.recruitment_flow._create_session", new=AsyncMock(return_value=99)):
            result = await intake_message("8801999000111", "চাকরি দরকার", role="candidate")
        assert result["reply"] == STEP_QUESTIONS["name"]

    async def test_default_role_matches_previous_no_role_behavior(self):
        """Backward compatibility: intake_message's role param defaults to
        'unknown', so any existing caller that never passed role keeps
        getting the soft ask -- not a silent behavior change to 'no ask'."""
        from modules.recruitment_flow import intake_message, UNKNOWN_SENDER_SOFT_ASK
        with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=None)), \
             patch("modules.recruitment_flow._create_session", new=AsyncMock(return_value=99)):
            result = await intake_message("8801999000111", "চাকরি দরকার")
        assert result["reply"].startswith(UNKNOWN_SENDER_SOFT_ASK)
