"""
Regression tests for the 2026-08-20 fix: GET /api/outbound/dead-letters
was missing entirely -- fazle-mcp's get_dlq_status has called this exact
path since 2026-08-15 expecting it to exist, every call 404'd. Root cause
was Case B (per the Owner's own investigation framing): the route was
genuinely intended but never implemented, not a wrong-route mismatch --
modules.outbound already had dlq_count()/actionable_dlq_count() (used by
/health's probe) but no row-level listing function and no dedicated route.
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestListDlqEntries:
    async def test_zero_dlq_returns_empty_list(self):
        from modules.outbound import list_dlq_entries
        with patch("modules.outbound.fetch_all", new=AsyncMock(return_value=[])):
            result = await list_dlq_entries(limit=20)
        assert result == []

    async def test_non_zero_dlq_returns_rows(self):
        from modules.outbound import list_dlq_entries
        rows = [
            {"id": 1, "recipient": "8801880446111", "purpose": "hermes_relay_reply",
             "source_bridge": "bridge2", "attempts": 3, "max_attempts": 3,
             "last_error": "timeout", "created_at": "2026-08-19T14:39:20+00:00",
             "updated_at": "2026-08-19T14:40:00+00:00", "actionable": True},
        ]
        with patch("modules.outbound.fetch_all", new=AsyncMock(return_value=rows)) as mock_fetch:
            result = await list_dlq_entries(limit=20)
        assert result == rows
        mock_fetch.assert_awaited_once()

    async def test_limit_clamped_into_query(self):
        from modules.outbound import list_dlq_entries
        with patch("modules.outbound.fetch_all", new=AsyncMock(return_value=[])) as mock_fetch:
            await list_dlq_entries(limit=5)
        args = mock_fetch.await_args.args
        assert args[-1] == 5  # limit is the final bound param

    async def test_alert_pipeline_purposes_excluded_from_actionable(self):
        """dlq-alert/health-summary/circuit-alert rows (the alert pipeline's
        own historical noise) must be flagged actionable=False, mirroring
        actionable_dlq_count()'s existing filter exactly -- confirmed via
        the real SQL predicate, not just a mocked return value."""
        from modules.outbound import list_dlq_entries, _ALERT_PIPELINE_PURPOSES
        assert _ALERT_PIPELINE_PURPOSES == ("dlq-alert", "health-summary", "circuit-alert")
        with patch("modules.outbound.fetch_all", new=AsyncMock(return_value=[])) as mock_fetch:
            await list_dlq_entries(limit=20)
        query = mock_fetch.await_args.args[0]
        assert "NOT IN ($1, $2, $3)" in query
        assert "status = 'dlq'" in query


class TestGetOutboundDeadLettersRoute:
    async def test_zero_dlq_shape(self):
        from app.main import get_outbound_dead_letters
        with (
            patch("modules.outbound.list_dlq_entries", new=AsyncMock(return_value=[])),
            patch("modules.outbound.dlq_count", new=AsyncMock(return_value=0)),
            patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=0)),
        ):
            result = await get_outbound_dead_letters(limit=20)
        assert result["items"] == []
        assert result["count"] == 0
        assert result["total"] == 0
        assert result["actionable"] == 0
        assert result["limit"] == 20
        assert "ts" in result

    async def test_non_zero_dlq_shape_and_schema_compat(self):
        """Output schema stays compatible with the sibling
        /api/queue/dead-letters route's own shape (items/count/total/limit/ts)
        plus the one addition (actionable) get_dlq_status's own docstring
        already documented as expected."""
        from app.main import get_outbound_dead_letters
        items = [{"id": 1, "recipient": "x", "actionable": True}]
        with (
            patch("modules.outbound.list_dlq_entries", new=AsyncMock(return_value=items)),
            patch("modules.outbound.dlq_count", new=AsyncMock(return_value=7)),
            patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=2)),
        ):
            result = await get_outbound_dead_letters(limit=20)
        assert result["items"] == items
        assert result["count"] == 1
        assert result["total"] == 7
        assert result["actionable"] == 2
        expected_keys = {"items", "count", "total", "actionable", "limit", "ts"}
        assert expected_keys.issubset(result.keys())

    async def test_limit_clamped_to_sane_bounds(self):
        from app.main import get_outbound_dead_letters
        with (
            patch("modules.outbound.list_dlq_entries", new=AsyncMock(return_value=[])) as mock_list,
            patch("modules.outbound.dlq_count", new=AsyncMock(return_value=0)),
            patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=0)),
        ):
            await get_outbound_dead_letters(limit=500)
        mock_list.assert_awaited_once_with(limit=100)

    async def test_route_registered_no_404(self):
        """The route itself exists in the app's route table -- the actual
        live regression (every call to this path 404'd since 2026-08-15)."""
        from app.main import app
        paths = {r.path for r in app.routes}
        assert "/api/outbound/dead-letters" in paths
