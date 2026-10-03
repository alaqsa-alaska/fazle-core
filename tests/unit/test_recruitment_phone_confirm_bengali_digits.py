"""Regression test — Bengali-numeral phone at the recruitment funnel's
final 'phone_confirm' step (2026-08-21 closure pass). Proves the fix
reaches the actual questionnaire flow, not just the parser in isolation:
a Bengali-digit phone number is correctly captured as confirmed_phone and
the funnel completes (funnel_stage='scored') on that single reply, rather
than the step being repeatedly re-asked.
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_bengali_digit_phone_confirm_completes_funnel():
    from modules.recruitment_flow import intake_message

    session = {
        "id": 77,
        "phone": "8801700000077",
        "candidate_id": 30,
        "collection_step": "phone_confirm",
        "funnel_stage": "collecting",
        "full_name": "Karim Hossain",
        "age": 25,
        "area": "Chittagong",
        "job_preference": "Security Guard",
        "experience_years": 2,
    }
    transition_mock = AsyncMock(return_value=True)

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._apply_audited_transition", new=transition_mock):
        result = await intake_message(
            "8801700000077", "০১৭১৬০৮১৯২৫", "bridge1", role="candidate",
        )

    transition_mock.assert_awaited_once()
    args = transition_mock.await_args.args
    update_arg = args[6]
    final_session_arg = args[7]
    # The Bengali-digit reply must be correctly parsed into confirmed_phone —
    # not silently dropped (which would fall back to "use original phone",
    # a real but different candidate-facing number in general).
    assert update_arg["confirmed_phone"] == "01716081925"
    assert update_arg["funnel_stage"] == "scored"
    assert final_session_arg["confirmed_phone"] == "01716081925"
    # Completes in one reply — not re-asked.
    assert result["action"] == "scored"
    assert result["reply"] != "আপনার কনফার্মেশনের জন্য: আপনার মোবাইল নম্বর কত? (যেটায় কল করতে পারব)"


async def test_ascii_digit_phone_confirm_unaffected():
    """Sanity check the fix doesn't change the ordinary ASCII-digit path."""
    from modules.recruitment_flow import intake_message

    session = {
        "id": 78,
        "phone": "8801700000078",
        "candidate_id": 31,
        "collection_step": "phone_confirm",
        "funnel_stage": "collecting",
        "full_name": "Karim Hossain",
        "age": 25,
        "area": "Chittagong",
        "job_preference": "Security Guard",
        "experience_years": 2,
    }
    transition_mock = AsyncMock(return_value=True)

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._apply_audited_transition", new=transition_mock):
        await intake_message("8801700000078", "01716081925", "bridge1", role="candidate")

    update_arg = transition_mock.await_args.args[6]
    assert update_arg["confirmed_phone"] == "01716081925"
