"""Stage G3/G17/G27 (2026-08-28) -- modules.employee_privacy: the CRITICAL
PRIVACY RULE as one testable function."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules.employee_privacy import (
    is_authorized_for_employee_data, resolve_requester_employee_id,
)

pytestmark = pytest.mark.asyncio


class TestOwnDataAllowed:
    def test_same_employee_id_is_authorized(self):
        assert is_authorized_for_employee_data(42, "employee", 42) is True

    def test_admin_always_authorized_regardless_of_target(self):
        assert is_authorized_for_employee_data(None, "admin", 999) is True
        assert is_authorized_for_employee_data(1, "admin", 999) is True

    def test_accountant_always_authorized(self):
        assert is_authorized_for_employee_data(None, "accountant", 999) is True


class TestCrossEmployeeDenied:
    def test_different_employee_id_is_denied(self):
        """Stage G3: never disclose another employee's data because the
        requester merely asks for it."""
        assert is_authorized_for_employee_data(42, "employee", 99) is False

    def test_unresolved_requester_identity_is_denied(self):
        """Stage G3: unknown/ambiguous identity must not receive private
        employee data."""
        assert is_authorized_for_employee_data(None, "unknown", 42) is False

    def test_unresolved_target_is_denied_not_treated_as_unrestricted(self):
        """An unresolved target is not the same claim as 'no
        restriction' -- nothing to disclose means denied, not allowed."""
        assert is_authorized_for_employee_data(42, "employee", None) is False

    def test_both_unresolved_is_denied(self):
        assert is_authorized_for_employee_data(None, "unknown", None) is False


class TestResolveRequesterEmployeeId:
    async def test_uses_the_existing_phone_anchored_lookup(self):
        """Structural proof this delegates to the existing, already-proven
        identity_brain._lookup_employee(phone) -- never re-derives
        identity resolution independently."""
        with patch("modules.identity_brain._lookup_employee", new=AsyncMock(return_value={"employee_id": 7})):
            result = await resolve_requester_employee_id("8801700000000")
        assert result == 7

    async def test_no_match_returns_none(self):
        with patch("modules.identity_brain._lookup_employee", new=AsyncMock(return_value=None)):
            result = await resolve_requester_employee_id("8801700000000")
        assert result is None
