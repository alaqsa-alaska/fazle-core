"""Stage G6.1/G6.2/G6.16 (2026-08-28) -- complete role-to-lane coverage
audit using the authoritative identity_brain._ROLE_PRIORITY vocabulary,
plus the live authority-drift consistency check.
"""
from __future__ import annotations

import pytest

from modules.authority_lanes import (
    resolve_authority_lane, LaneContext,
    AUTONOMOUS_OPERATIONAL, OWNER_SUPERVISED_DRAFT, TRUSTED_ADMIN_EXECUTION,
    find_unprotected_supervised_draft_roles,
)

pytestmark = pytest.mark.asyncio


class TestEveryKnownRoleMapsToExactlyOneLane:
    async def test_every_role_priority_key_resolves_without_error(self):
        """Structural completeness: resolve_authority_lane() must never
        raise or silently mis-handle any role currently known to
        identity_brain -- new roles fall through to the safe default
        (OWNER_SUPERVISED_DRAFT), never crash, never silently become
        autonomous by accident."""
        from modules.identity_brain import _ROLE_PRIORITY
        for role in _ROLE_PRIORITY:
            decision = resolve_authority_lane(LaneContext(identity_role=role))
            assert decision.lane in (
                AUTONOMOUS_OPERATIONAL, OWNER_SUPERVISED_DRAFT, TRUSTED_ADMIN_EXECUTION,
            )
            assert decision.reason

    async def test_recruitment_and_employee_roles_are_autonomous(self):
        for role in ("candidate", "employee", "escort", "security_guard", "supervisor"):
            d = resolve_authority_lane(LaneContext(identity_role=role))
            assert d.lane == AUTONOMOUS_OPERATIONAL, f"{role} should be autonomous_operational"

    async def test_client_and_office_roles_are_supervised(self):
        for role in (
            "vip_client", "security_service_client", "client_escort_buyer",
            "escort_client", "repeat_client",
            "internal_coordinator", "internal_operation", "employee_internal",
        ):
            d = resolve_authority_lane(LaneContext(identity_role=role))
            assert d.lane == OWNER_SUPERVISED_DRAFT, f"{role} should be owner_supervised_draft"

    async def test_admin_role_alone_without_channel_proof_is_not_trusted(self):
        """admin role by itself (no sender_is_admin_number/channel proof)
        must never resolve to trusted_admin_execution -- role alone is
        insufficient, matching the real _handle_hermes_relay gate's own
        three independent checks."""
        d = resolve_authority_lane(LaneContext(identity_role="admin"))
        assert d.lane != TRUSTED_ADMIN_EXECUTION

    async def test_out_of_scope_personal_roles_default_to_safe_supervised(self):
        """family/family_ceo/friend_relative/vendor/political_contact/
        government_contact/personal_business are outside the Owner's
        5-lane business-authority model entirely (not recruitment/
        employee/client/office/admin) -- the safe default still applies
        rather than inventing a 6th lane for them."""
        for role in (
            "family", "family_ceo", "friend_relative", "vendor",
            "political_contact", "government_contact", "personal_business",
        ):
            d = resolve_authority_lane(LaneContext(identity_role=role))
            assert d.lane == OWNER_SUPERVISED_DRAFT

    async def test_unknown_role_defaults_to_supervised(self):
        d = resolve_authority_lane(LaneContext(identity_role="unknown"))
        assert d.lane == OWNER_SUPERVISED_DRAFT


class TestAuthorityDriftPrevention:
    """Stage G6.2: the actual consistency check against LIVE settings +
    the real message_router._ESCORT_ROLES -- this is the test that would
    have caught Stage G.5's original 4-role gap before it ever reached
    production, and will catch the same class of gap for any future role
    addition."""

    async def test_no_supervised_draft_role_is_currently_unprotected(self):
        from app.config import get_settings
        from modules.message_router import _ESCORT_ROLES
        gaps = find_unprotected_supervised_draft_roles(get_settings().draft_always_role_set, _ESCORT_ROLES)
        assert gaps == [], f"CLIENT/OFFICE roles not covered by draft_always_role_set or _ESCORT_ROLES: {gaps}"

    async def test_the_four_stage_g5_roles_are_specifically_protected(self):
        from app.config import get_settings
        role_set = get_settings().draft_always_role_set
        for role in ("internal_coordinator", "internal_operation", "employee_internal", "security_service_client"):
            assert role in role_set

    def test_function_detects_a_synthetic_gap(self):
        """Proves the checker actually works (not a tautology) -- remove
        one role from the simulated protected set and confirm it's
        reported."""
        from app.config import get_settings
        from modules.message_router import _ESCORT_ROLES
        real_set = frozenset(r for r in get_settings().draft_always_role_set if r != "security_service_client")
        gaps = find_unprotected_supervised_draft_roles(real_set, _ESCORT_ROLES)
        assert "security_service_client" in gaps

    def test_escort_roles_alternative_protection_is_recognized(self):
        """escort_client/client_escort_buyer/vip_client/repeat_client are
        legitimately protected via the dedicated handle_escort_client_
        message() handler (_ESCORT_ROLES), not via draft_always_roles --
        the checker must not false-flag them when draft_always_role_set
        alone doesn't include them."""
        narrow_draft_always = frozenset({"accountant", "internal_coordinator", "internal_operation", "employee_internal", "security_service_client"})
        escort_roles = frozenset({"escort_client", "client_escort_buyer", "vip_client", "repeat_client"})
        gaps = find_unprotected_supervised_draft_roles(narrow_draft_always, escort_roles)
        assert gaps == []
