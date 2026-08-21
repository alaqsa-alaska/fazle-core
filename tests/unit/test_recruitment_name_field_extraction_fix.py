"""Regression tests — Owner Correction 2 / recruitment case 9694 (2026-08-21).

The candidate's actual message at the 'name' collection step was
"আমি HSC শেষ করেছি, এই শিক্ষাগত যোগ্যতায় কোন পদ আছে" (an education
statement/question, not a name) -- intake_message() stored it verbatim as
full_name because the 'name' step accepted any text unconditionally.
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_education_sentence_at_name_step_does_not_pollute_full_name():
    from modules.recruitment_flow import intake_message

    session = {
        "id": 42,
        "phone": "8801865499694",
        "candidate_id": 17,
        "collection_step": "name",
        "funnel_stage": "collecting",
        "source_message": "চাকরি চাই",
        "source_bridge": "bridge1",
    }
    update_mock = AsyncMock()
    audit_mock = AsyncMock(return_value=True)

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._update_session", new=update_mock), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=audit_mock):
        result = await intake_message(
            "8801865499694",
            "আমি HSC শেষ করেছি, এই শিক্ষাগত যোগ্যতায় কোন পদ আছে",
            "bridge1",
            role="candidate",
        )

    # Must NOT advance the step / write full_name from this text.
    update_mock.assert_not_awaited()
    assert result["action"] == "collecting"
    assert result["session_id"] == 42
    # The raw text is still preserved verbatim in the conversation audit trail.
    audit_mock.assert_awaited_once()
    assert audit_mock.await_args.args[2] == "আমি HSC শেষ করেছি, এই শিক্ষাগত যোগ্যতায় কোন পদ আছে"


async def test_ordinary_name_at_name_step_is_accepted():
    from modules.recruitment_flow import intake_message

    session = {
        "id": 42,
        "phone": "8801700000001",
        "candidate_id": 17,
        "collection_step": "name",
        "funnel_stage": "collecting",
        "source_message": "চাকরি চাই",
        "source_bridge": "bridge1",
    }
    transition_mock = AsyncMock(return_value=True)

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._apply_audited_transition", new=transition_mock):
        result = await intake_message("8801700000001", "Md Karim Hossain", "bridge1", role="candidate")

    transition_mock.assert_awaited_once()
    update_arg = transition_mock.await_args.args[6]
    assert update_arg["full_name"] == "Md Karim Hossain"
    assert result["reply"] != "দুঃখিত, বুঝতে পারিনি। দয়া করে শুধু আপনার পুরো নাম লিখুন (যেমনঃ Md Karim)।"


async def test_question_mark_text_rejected_at_name_step():
    from modules.recruitment_flow import intake_message

    session = {
        "id": 43, "phone": "8801700000002", "candidate_id": 18,
        "collection_step": "name", "funnel_stage": "collecting",
        "source_message": "চাকরি চাই", "source_bridge": "bridge1",
    }
    update_mock = AsyncMock()

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._update_session", new=update_mock), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)):
        result = await intake_message("8801700000002", "বেতন কত হবে?", "bridge1", role="candidate")

    update_mock.assert_not_awaited()
    assert result["action"] == "collecting"


async def test_looks_like_name_helper_directly():
    from modules.recruitment_flow import _looks_like_name

    assert _looks_like_name("Md Karim") is True
    assert _looks_like_name("রহিম উদ্দিন") is True
    assert _looks_like_name("আমি HSC শেষ করেছি, এই শিক্ষাগত যোগ্যতায় কোন পদ আছে") is False
    assert _looks_like_name("এই যোগ্যতায় কোন পদ আছে?") is False
    assert _looks_like_name("চাকরি আছে কি?") is False
    assert _looks_like_name("") is False
    assert _looks_like_name("x" * 61) is False
