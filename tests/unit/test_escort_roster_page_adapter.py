"""Unit tests -- Unified Admin AI Engine, Bucket 2 Step 6 (BUS-018).

Covers the escort roster page-truth adapter: registry entry correctness,
the structural no-write-function-reference proof, and the handler's
summary-only / summary+list / exception-handling behavior. Mocks
modules.escort_roster.db.get_roster_summary/get_roster_list directly
(the source module, since the handler imports them locally at call time)
-- no real Postgres needed.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from modules.ai_console.live_adapters import ReadonlyBusinessAdapter
from modules.ai_console.registry import build_registry_loader

pytestmark = pytest.mark.unit


@pytest.fixture()
def loader():
    return build_registry_loader()


FAKE_SUMMARY = {
    "by_status": {"active": {"count": 5, "total_amount": 20000.0, "net_payable_amount": 18000.0}},
    "grand_total": 5,
    "grand_total_amount": 20000.0,
    "net_payable_total": 18000.0,
    "active_programs": 5,
    "pending_slip_reviews": 0,
    "true_draft_count": 0,
}


class TestBus018Registry:
    def test_bus018_exists(self, loader):
        assert loader.exists("BUS-018")

    def test_bus018_min_role_is_admin(self, loader):
        assert loader.get("BUS-018").metadata.get("min_role") == "admin"

    def test_bus018_authority_reflects_source(self, loader):
        entry = loader.get("BUS-018")
        assert entry.authority == "escort_roster_readonly_view"
        assert "escort_roster_entries" in entry.purpose

    def test_no_existing_entry_disturbed(self, loader):
        assert loader.get("BUS-017").metadata == {"min_role": "admin"}


class TestEscortRosterPageSummaryStructuralSafety:
    def test_no_write_function_reference(self):
        source = inspect.getsource(ReadonlyBusinessAdapter._escort_roster_page_summary)
        for token in ("execute(", "INSERT", "UPDATE", "DELETE"):
            assert token not in source, f"forbidden write-capable reference found: {token!r}"

    def test_only_approved_functions_imported(self):
        source = inspect.getsource(ReadonlyBusinessAdapter._escort_roster_page_summary)
        assert "from modules.escort_roster.db import get_roster_list, get_roster_summary" in source


class TestEscortRosterPageSummaryHandler:
    async def test_summary_only_when_no_hint(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-018")

        # Note: intentionally using an empty question here, not e.g. "escort
        # roster summary" -- _extract_name_hint() (a shared helper, not
        # part of this step's scope) doesn't strip "roster"/"summary" from
        # its Bangla/English keyword list, so that phrase would leave a
        # residual "roster" hint and correctly trigger get_roster_list(),
        # which is this handler working as designed, not a bug -- just not
        # what this specific test case is isolating.
        with patch("modules.escort_roster.db.get_roster_summary", new=AsyncMock(return_value=FAKE_SUMMARY)), \
             patch("modules.escort_roster.db.get_roster_list", new=AsyncMock()) as mock_list:
            envelope = await adapter._escort_roster_page_summary(entry, {"question": ""})

        mock_list.assert_not_awaited()
        assert envelope.data["source"] == "escort_roster_entries"
        assert envelope.data["total_count"] == 5
        assert envelope.data["total_amount"] == 20000.0
        assert envelope.data["net_payable_total"] == 18000.0
        assert envelope.data["rows"] == []

    async def test_summary_plus_list_when_hint_present(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-018")
        fake_list = {"items": [{"escort_name": "Joy", "mother_vessel": "MV Test"}], "total": 1, "page": 1, "page_size": 20, "pages": 1}

        with patch("modules.escort_roster.db.get_roster_summary", new=AsyncMock(return_value=FAKE_SUMMARY)), \
             patch("modules.escort_roster.db.get_roster_list", new=AsyncMock(return_value=fake_list)) as mock_list:
            envelope = await adapter._escort_roster_page_summary(entry, {"search": "Joy", "question": "escort roster"})

        mock_list.assert_awaited_once_with(
            page=1, page_size=20, search="Joy", status="active",
            start_from=None, start_to=None, sort_by="start_date", sort_dir="desc",
        )
        assert envelope.data["rows"] == [{"escort_name": "Joy", "mother_vessel": "MV Test"}]
        assert envelope.data["source"] == "escort_roster_entries"

    async def test_exception_returns_failed_status(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-018")

        with patch("modules.escort_roster.db.get_roster_summary", new=AsyncMock(side_effect=RuntimeError("db down"))):
            envelope = await adapter._escort_roster_page_summary(entry, {"question": "roster summary"})

        assert envelope.status == "failed"
        assert envelope.errors
