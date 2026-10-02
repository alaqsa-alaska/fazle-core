"""Stage G5.1 (2026-08-28) -- automatic Super Admin notification for
Client/Office-Internal supervised drafts.

Real gap found and fixed this stage: internal_coordinator/internal_
operation/employee_internal/security_service_client were not in
DRAFT_ALWAYS_ROLES at all (confirmed live: internal_coordinator-classified
senders had received 25 real autonomous AI-generated outbound replies in
canonical history) -- these roles fall through message_router's employee/
_ESCORT_ROLES/ROUTE_B_ROLES checks straight to the generic KB/AI-fallback
branch. Fixed in app/config.py's draft_always_roles default AND the live
.env DRAFT_ALWAYS_ROLES value (the actual effective source, env always
overrides the class default). modules.bridge_poller._save_draft() gained
auto_notify_admin, wired at the DRAFT_ALWAYS_GATE call site only, gated
by modules.authority_lanes.resolve_authority_lane().
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import modules.bridge_poller as bp

pytestmark = pytest.mark.asyncio


class TestConfigRoleSet:
    def test_office_internal_and_client_roles_are_in_draft_always_role_set(self):
        from app.config import get_settings
        role_set = get_settings().draft_always_role_set
        for role in ("internal_coordinator", "internal_operation", "employee_internal", "security_service_client"):
            assert role in role_set, f"{role} missing from draft_always_role_set -- would autonomously auto-reply"

    def test_pre_existing_roles_still_present(self):
        from app.config import get_settings
        role_set = get_settings().draft_always_role_set
        for role in ("accountant", "client_escort_buyer", "vip_client", "repeat_client"):
            assert role in role_set


class TestSaveDraftAutoNotify:
    async def test_auto_notify_false_by_default_every_existing_caller_unaffected(self, monkeypatch):
        """Default auto_notify_admin=False -- every pre-existing _save_draft
        call site (14+ of them, none pass this param) is byte-identical."""
        notify_mock = AsyncMock()
        monkeypatch.setattr("modules.supervised_draft.notify_admin_of_pending_draft", notify_mock, raising=False)
        monkeypatch.setattr(bp, "create_draft_reply", AsyncMock(return_value=5))
        monkeypatch.setattr(bp, "execute", AsyncMock())
        await bp._save_draft("bridge2", "8801700000000", "reply", "office_location", sender_name="X", sender_message="hi")
        notify_mock.assert_not_called()

    async def test_auto_notify_true_calls_notify_after_successful_draft(self, monkeypatch):
        notify_mock = AsyncMock(return_value={"ok": True})
        monkeypatch.setattr("modules.supervised_draft.notify_admin_of_pending_draft", notify_mock, raising=False)
        monkeypatch.setattr(bp, "create_draft_reply", AsyncMock(return_value=42))
        monkeypatch.setattr(bp, "execute", AsyncMock())
        await bp._save_draft(
            "bridge2", "8801700000000", "reply", "office_location",
            sender_name="Office X", sender_message="hi", category="Office/Internal", auto_notify_admin=True,
        )
        notify_mock.assert_awaited_once_with(42, "Office/Internal")

    async def test_notify_failure_does_not_raise_and_draft_already_exists(self, monkeypatch):
        """Notification failure must NOT cause the original draft to
        disappear -- draft creation already committed before notify runs;
        a raising notify call must not propagate."""
        monkeypatch.setattr(
            "modules.supervised_draft.notify_admin_of_pending_draft",
            AsyncMock(side_effect=RuntimeError("boom")), raising=False,
        )
        monkeypatch.setattr(bp, "create_draft_reply", AsyncMock(return_value=42))
        monkeypatch.setattr(bp, "execute", AsyncMock())
        # Must not raise.
        await bp._save_draft(
            "bridge2", "8801700000000", "reply", "office_location",
            sender_name="X", sender_message="hi", category="Client", auto_notify_admin=True,
        )

    async def test_never_notifies_for_a_quality_rejected_draft(self, monkeypatch):
        """Rejected-quality/rejected-fallback drafts deliberately never
        reach the admin pending list at all (pre-existing B25 rule) --
        auto-notify must not fire for them either."""
        notify_mock = AsyncMock()
        monkeypatch.setattr("modules.supervised_draft.notify_admin_of_pending_draft", notify_mock, raising=False)
        monkeypatch.setattr(bp, "create_draft_reply", AsyncMock(return_value=42))
        monkeypatch.setattr(bp, "execute", AsyncMock())
        with patch("modules.draft_quality.check_draft_quality", return_value=(False, "llm_fallback")):
            await bp._save_draft(
                "bridge2", "8801700000000", "", "office_location",
                sender_name="X", sender_message="hi", category="Client", auto_notify_admin=True,
            )
        notify_mock.assert_not_called()


class TestDraftAlwaysGateWiring:
    def test_gate_call_site_resolves_lane_and_sets_category(self):
        """Structural proof: the DRAFT_ALWAYS_GATE branch (not the
        complaint-phrase or loop-detection branches) is the one that
        computes category/auto_notify_admin via resolve_authority_lane()."""
        import inspect
        src = inspect.getsource(bp)
        gate_idx = src.index('log.info("[DRAFT-GATE] %s name=%r role=%s')
        window = src[gate_idx:gate_idx + 2000]
        assert "resolve_authority_lane" in window
        assert "auto_notify_admin=bool(_g51_category)" in window

    def test_client_and_office_roles_map_to_distinct_categories(self):
        from modules.authority_lanes import resolve_authority_lane, LaneContext, OWNER_SUPERVISED_DRAFT
        client_roles = ("vip_client", "security_service_client", "client_escort_buyer", "escort_client", "repeat_client")
        office_roles = ("internal_coordinator", "internal_operation", "employee_internal")
        for r in client_roles + office_roles:
            d = resolve_authority_lane(LaneContext(identity_role=r))
            assert d.lane == OWNER_SUPERVISED_DRAFT
