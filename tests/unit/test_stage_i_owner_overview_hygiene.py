"""Stage I19/I20/I23/I24 (2026-08-29) -- Owner overview (STATUS command +
business inbox reorganization) and the production data-hygiene report."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestOwnerOverview:
    async def test_separates_action_required_from_legacy_exceptions(self):
        from modules.reports import build_owner_overview
        fake_summary = {
            "generated_at": "2026-08-29T00:00:00Z",
            "summary": {
                "action_required_total": 2, "cash_needs_review_broken_provenance": 0,
                "financial_ops_failed_recent": 0, "complaints": 0, "by_category": {},
                "autonomous_replies_sent": 0,
            },
            "approval_queue": [
                "Draft 1 — Client — APPROVE / EDIT / REJECT",
                "Proposal 29 — recruitment — review via /api/actions/29/approve [LEGACY — reference not executable under the typed contract]",
            ],
        }
        with patch("modules.reports._b_business_communication_summary", new=AsyncMock(return_value=fake_summary)):
            overview = await build_owner_overview(hours=24)
        assert "Draft 1 — Client — APPROVE / EDIT / REJECT" in overview["action_required"]
        assert not any("Draft 1" in x for x in overview["exceptions"])
        assert any("Proposal 29" in x for x in overview["exceptions"])
        assert not any("Proposal 29" in x for x in overview["action_required"])

    async def test_render_owner_overview_shows_all_sections(self):
        from modules.reports import render_owner_overview
        overview = {
            "action_required": ["Draft 1 — Client — APPROVE / EDIT / REJECT"],
            "exceptions": ["Proposal 29 — LEGACY"],
            "fyi": ["recruitment: 5 conversation(s), handled autonomously"],
        }
        text = render_owner_overview(overview)
        assert "ACTION REQUIRED" in text
        assert "EXCEPTIONS" in text
        assert "FYI" in text
        assert "Draft 1" in text


class TestDataHygieneReport:
    async def test_read_only_no_writes(self):
        import inspect
        from modules.reports import _b_data_hygiene
        src = inspect.getsource(_b_data_hygiene)
        assert "INSERT INTO" not in src
        assert "UPDATE " not in src
        assert "DELETE FROM" not in src
        assert "execute(" not in src

    async def test_registered_in_builders(self):
        from modules.reports import _BUILDERS
        assert "data_hygiene" in _BUILDERS

    async def test_reports_broken_provenance_and_legacy_proposals(self):
        from modules.reports import _b_data_hygiene
        broken_row = {"txn_id": 1, "txn_ref": "r1", "classification": "TEST_ARTIFACT_SUSPECT"}
        legacy_proposal = {
            "proposal_id": 29, "domain": "recruitment", "status": "pending",
            "action_type": "business_reply_draft", "reference_kind": "phone",
            "executable": False, "migratable": True, "stale": None, "created_at": None,
        }
        with patch(
            "modules.fazle_payroll_engine.review_linkage_repair.classify_all_pending_cash_transactions",
            new=AsyncMock(return_value=[broken_row]),
        ), patch(
            "modules.business_action_execution.audit_proposal_references",
            new=AsyncMock(return_value=[legacy_proposal]),
        ), patch("modules.outbound.dlq_count", new=AsyncMock(return_value=0)), \
             patch("modules.outbound.actionable_dlq_count", new=AsyncMock(return_value=0)), \
             patch("modules.outbound.list_dlq_entries", new=AsyncMock(return_value=[])), \
             patch("modules.scheduler.list_job_names", return_value=[]), \
             patch("modules.reports.fetch_all", new=AsyncMock(return_value=[])):
            payload = await _b_data_hygiene()
        assert payload["summary"]["broken_provenance_cash_count"] == 1
        assert payload["summary"]["stale_or_legacy_proposal_count"] == 1
        assert payload["stale_or_legacy_proposals"][0]["proposal_id"] == 29

    async def test_never_mass_replays_dlq(self):
        """I23: 'do NOT mass-replay' -- structural proof."""
        import inspect
        from modules.reports import _b_data_hygiene
        src = inspect.getsource(_b_data_hygiene)
        assert "replay" not in src.lower()
        assert "send(" not in src
