"""Unit tests -- modules.admin_directives.find_conflicting_directive() and
get_active_directives_context().

Phase 3, 2026-08-25 recovery pass ("general Owner business-rule mutation
architecture"): new functions, no prior coverage. Mocks the DB layer
(fetch_all) at the module's own imported binding, same pattern as
test_hermes_tasks.py -- no real Postgres needed.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestFindConflictingDirective:
    async def test_no_subject_type_or_key_returns_none_without_db_call(self):
        from modules.admin_directives import find_conflicting_directive
        mock_fetch = AsyncMock(return_value=[])
        with patch("modules.admin_directives.fetch_all", new=mock_fetch):
            result = await find_conflicting_directive(None, None, "some text")
        assert result is None
        mock_fetch.assert_not_awaited()

    async def test_no_existing_active_directive_returns_none(self):
        from modules.admin_directives import find_conflicting_directive
        with patch("modules.admin_directives.fetch_all", new=AsyncMock(return_value=[])):
            result = await find_conflicting_directive("recruitment_rule", "recruitment_minimum_age", "23")
        assert result is None

    async def test_same_text_different_case_and_whitespace_is_not_a_conflict(self):
        """Re-statement of the same rule (modulo case/whitespace) must not
        be flagged -- only a MATERIAL difference is a real conflict."""
        from modules.admin_directives import find_conflicting_directive
        existing = [{"directive_text": "  Minimum   AGE is 18  "}]
        with patch("modules.admin_directives.fetch_all", new=AsyncMock(return_value=existing)):
            result = await find_conflicting_directive(
                "recruitment_rule", "recruitment_minimum_age", "minimum age is 18",
            )
        assert result is None

    async def test_different_text_on_same_subject_is_a_conflict(self):
        from modules.admin_directives import find_conflicting_directive
        existing_row = {"id": 7, "directive_text": "minimum age is 18"}
        with patch("modules.admin_directives.fetch_all", new=AsyncMock(return_value=[existing_row])):
            result = await find_conflicting_directive(
                "recruitment_rule", "recruitment_minimum_age", "minimum age is 23",
            )
        assert result == existing_row

    async def test_db_query_filters_by_active_status_and_exact_subject(self):
        from modules.admin_directives import find_conflicting_directive
        mock_fetch = AsyncMock(return_value=[])
        with patch("modules.admin_directives.fetch_all", new=mock_fetch):
            await find_conflicting_directive("recruitment_rule", "recruitment_minimum_age", "23")
        args, _ = mock_fetch.call_args
        query = args[0]
        assert "status='active'" in query
        assert "subject_type=$1" in query
        assert "subject_key=$2" in query
        assert args[1:] == ("recruitment_rule", "recruitment_minimum_age")


class TestGetActiveDirectivesContext:
    async def test_no_active_directives_returns_empty_string(self):
        from modules.admin_directives import get_active_directives_context
        with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=[])):
            result = await get_active_directives_context()
        assert result == ""

    async def test_formats_directives_with_subject_and_date(self):
        from modules.admin_directives import get_active_directives_context
        rows = [
            {
                "subject_type": "recruitment_rule", "subject_key": "recruitment_minimum_age",
                "directive_text": "minimum age is 23", "created_at": datetime(2026, 8, 25),
            },
        ]
        with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=rows)):
            result = await get_active_directives_context()
        assert "recruitment_rule/recruitment_minimum_age" in result
        assert "minimum age is 23" in result
        assert "2026-08-25" in result

    async def test_multiple_active_directives_all_included(self):
        from modules.admin_directives import get_active_directives_context
        rows = [
            {"subject_type": "a", "subject_key": "x", "directive_text": "rule one", "created_at": None},
            {"subject_type": "b", "subject_key": "y", "directive_text": "rule two", "created_at": None},
        ]
        with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=rows)):
            result = await get_active_directives_context()
        assert "rule one" in result
        assert "rule two" in result

    async def test_db_error_fails_closed_to_empty_string_not_raise(self):
        from modules.admin_directives import get_active_directives_context
        with patch("modules.admin_directives.list_directives", new=AsyncMock(side_effect=RuntimeError("db down"))):
            result = await get_active_directives_context()
        assert result == ""

    async def test_row_with_empty_text_skipped(self):
        from modules.admin_directives import get_active_directives_context
        rows = [{"subject_type": "a", "subject_key": "x", "directive_text": "  ", "created_at": None}]
        with patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=rows)):
            result = await get_active_directives_context()
        assert result == ""


class TestBuildRecruitmentContextWithDirectives:
    """hermes_dispatch.build_recruitment_context_with_directives() --
    wraps the existing synchronous build_recruitment_context() (untouched)
    with the active-directives block appended. See its own docstring for
    why this is a new function rather than an async conversion of the
    original."""

    async def test_no_active_directives_returns_base_context_unchanged(self):
        from modules import hermes_dispatch
        with (
            patch.object(hermes_dispatch, "build_recruitment_context", return_value="base recruitment kb"),
            patch("modules.admin_directives.get_active_directives_context", new=AsyncMock(return_value="")),
        ):
            result = await hermes_dispatch.build_recruitment_context_with_directives("hi")
        assert result == "base recruitment kb"

    async def test_active_directive_appended_after_base_context(self):
        from modules import hermes_dispatch
        with (
            patch.object(hermes_dispatch, "build_recruitment_context", return_value="base recruitment kb"),
            patch(
                "modules.admin_directives.get_active_directives_context",
                new=AsyncMock(return_value="- [recruitment_rule/x] minimum age is 23"),
            ),
        ):
            result = await hermes_dispatch.build_recruitment_context_with_directives("hi")
        assert "base recruitment kb" in result
        assert "minimum age is 23" in result
        assert result.index("base recruitment kb") < result.index("minimum age is 23")

    async def test_empty_base_context_with_active_directive_returns_directive_only(self):
        from modules import hermes_dispatch
        with (
            patch.object(hermes_dispatch, "build_recruitment_context", return_value=""),
            patch(
                "modules.admin_directives.get_active_directives_context",
                new=AsyncMock(return_value="- [x/y] some rule"),
            ),
        ):
            result = await hermes_dispatch.build_recruitment_context_with_directives("hi")
        assert result == "- [x/y] some rule"


class TestBuildEmployeeContextWithDirectives:
    async def test_no_active_directives_returns_base_context_unchanged(self):
        from modules import hermes_dispatch
        with (
            patch.object(hermes_dispatch, "build_employee_context", return_value="base employee policy"),
            patch("modules.admin_directives.get_active_directives_context", new=AsyncMock(return_value="")),
        ):
            result = await hermes_dispatch.build_employee_context_with_directives("hi")
        assert result == "base employee policy"

    async def test_active_directive_appended_after_base_context(self):
        from modules import hermes_dispatch
        with (
            patch.object(hermes_dispatch, "build_employee_context", return_value="base employee policy"),
            patch(
                "modules.admin_directives.get_active_directives_context",
                new=AsyncMock(return_value="- [employee_rule/x] new leave policy"),
            ),
        ):
            result = await hermes_dispatch.build_employee_context_with_directives("hi")
        assert "base employee policy" in result
        assert "new leave policy" in result
