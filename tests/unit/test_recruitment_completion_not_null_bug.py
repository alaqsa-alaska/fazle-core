"""Regression test -- recruitment funnel completion NOT NULL bug
(2026-08-21, urgent Facebook-boost recovery pass).

fazle_recruitment_sessions.collection_step is NOT NULL (default 'name'),
but intake_message()'s "Collection complete" branch tried to set it to
None -- silently violating that constraint and rolling back the entire
completion transition every single time. Confirmed live: no candidate has
ever actually reached funnel_stage='scored' via the normal step-by-step
flow; every completion attempt fell back to re-asking the last question
forever (a real candidate's session got stuck exactly this way during the
2026-08-21 recovery, replaying his real conversation through the funnel).
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_completion_never_sets_collection_step_to_none():
    """The literal bug: update['collection_step'] must never be None at
    the point _apply_audited_transition()/_update_session() is called for
    the final scoring transition."""
    from modules.recruitment_flow import intake_message

    session = {
        "id": 36, "phone": "8801700000099", "candidate_id": 13,
        "collection_step": "phone_confirm", "funnel_stage": "collecting",
        "full_name": "Test Candidate", "age": 25, "area": "Chittagong",
        "job_preference": "Security Guard", "experience_years": 0,
        "source_bridge": "bridge3", "source_message": "চাকরি চাই",
    }
    transition_mock = AsyncMock(return_value=True)

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._apply_audited_transition", new=transition_mock):
        result = await intake_message("8801700000099", "01700000099", "bridge3", role="unknown")

    transition_mock.assert_awaited_once()
    update_arg = transition_mock.await_args.args[6]
    assert update_arg["collection_step"] is not None
    assert update_arg["collection_step"] != ""
    assert update_arg["funnel_stage"] == "scored"
    assert result["action"] == "scored"
    assert result["reply"] != session_stuck_reply()


def session_stuck_reply():
    from modules.recruitment_flow import STEP_QUESTIONS
    return STEP_QUESTIONS.get("phone_confirm")


async def test_completion_via_update_session_path_also_avoids_none():
    """The candidate_id-is-None branch (bare _update_session call) hits
    the identical column, must be fixed identically."""
    from modules.recruitment_flow import intake_message

    session = {
        "id": 37, "phone": "8801700000098", "candidate_id": None,
        "collection_step": "phone_confirm", "funnel_stage": "collecting",
        "full_name": "Test Candidate", "age": 25, "area": "Chittagong",
        "job_preference": "Security Guard", "experience_years": 0,
        "source_bridge": "bridge3", "source_message": "চাকরি চাই",
    }
    update_session_mock = AsyncMock()

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._update_session", new=update_session_mock):
        result = await intake_message("8801700000098", "01700000098", "bridge3", role="unknown")

    update_session_mock.assert_awaited_once()
    call_args = update_session_mock.await_args.args
    update_arg = call_args[1]
    assert update_arg["collection_step"] is not None
    assert result["action"] == "scored"
