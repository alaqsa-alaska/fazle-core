"""Item 3 (2026-08-19, Hermes->Earth rename+autonomy pass): admin-directive
override for the recruitment minimum age. Mirrors the test shape already
used for _audit_candidate_conversation in test_recruitment_conversation_audit.py."""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_default_range_when_no_active_directive():
    from modules.recruitment_flow import _get_effective_age_range

    with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=[])):
        result = await _get_effective_age_range()

    assert result == (18, 55)


async def test_default_range_when_directive_lookup_raises():
    from modules.recruitment_flow import _get_effective_age_range

    with patch("modules.admin_directives.list_directives", new=AsyncMock(side_effect=RuntimeError("db down"))):
        result = await _get_effective_age_range()

    assert result == (18, 55)


async def test_active_directive_overrides_minimum_age():
    from modules.recruitment_flow import _get_effective_age_range

    rows = [{"id": 42, "directive_text": "23", "subject_key": "recruitment_minimum_age"}]
    with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=rows)) as mock_list:
        result = await _get_effective_age_range()

    assert result == (23, 55)
    mock_list.assert_awaited_once_with(subject_key="recruitment_minimum_age", status="active", limit=1)


async def test_malformed_directive_text_falls_back_to_default():
    from modules.recruitment_flow import _get_effective_age_range

    rows = [{"id": 42, "directive_text": "no number here", "subject_key": "recruitment_minimum_age"}]
    with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=rows)):
        result = await _get_effective_age_range()

    assert result == (18, 55)


async def test_out_of_bounds_directive_value_falls_back_to_default():
    from modules.recruitment_flow import _get_effective_age_range

    rows = [{"id": 42, "directive_text": "999", "subject_key": "recruitment_minimum_age"}]
    with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=rows)):
        result = await _get_effective_age_range()

    assert result == (18, 55)


@pytest.mark.parametrize(
    "text,age_range,expected",
    [
        ("25", (18, 55), 25),
        ("20", (23, 55), None),
        ("23", (23, 55), 23),
        ("no digits here", (18, 55), None),
    ],
)
async def test_parse_age_respects_supplied_range(text, age_range, expected):
    from modules.recruitment_flow import _parse_age

    assert _parse_age(text, age_range) == expected


async def test_intake_message_uses_effective_age_range_in_rejection_reply():
    from modules.recruitment_flow import intake_message

    session = {
        "id": 9,
        "phone": "8801700000009",
        "candidate_id": 17,
        "collection_step": "age",
        "funnel_stage": "collecting",
    }
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.admin_directives.list_directives", new=AsyncMock(
             return_value=[{"directive_text": "23", "subject_key": "recruitment_minimum_age"}]
         )):
        result = await intake_message(session["phone"], "20", source="bridge1", message_id="msg-age-2")

    assert result["action"] == "collecting"
    assert "২৩" in result["reply"]
    assert "৫৫" in result["reply"]
