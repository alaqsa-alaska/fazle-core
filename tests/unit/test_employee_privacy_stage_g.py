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


class TestOwnPayrollSummary:
    """Stage G6.8: Payroll/Salary READ. Own-data-only BY CONSTRUCTION --
    there is no parameter through which a caller can request another
    employee's data; the returned record is always the one resolved from
    requester_phone itself."""

    async def test_unresolved_identity_returns_error_not_data(self):
        from modules.employee_privacy import get_own_payroll_summary
        with patch("modules.identity_brain._lookup_employee", new=AsyncMock(return_value=None)):
            result = await get_own_payroll_summary("8801799999999")
        assert result["ok"] is False
        assert "basic_salary" not in result

    async def test_resolved_identity_returns_own_fields_only(self):
        from modules.employee_privacy import get_own_payroll_summary
        emp_row = {
            "employee_id": 42, "employee_name": "Karim", "designation": "Guard",
            "basic_salary": 15000, "status": "Active",
        }
        with patch("modules.identity_brain._lookup_employee", new=AsyncMock(return_value=emp_row)):
            result = await get_own_payroll_summary("8801700000042")
        assert result["ok"] is True
        assert result["employee_id"] == 42
        assert result["basic_salary"] == 15000.0

    def test_function_signature_has_no_target_employee_parameter(self):
        """Structural proof of the own-data-only guarantee: the function
        cannot be asked for anyone else's record -- there is no id/name
        parameter to redirect the lookup."""
        import inspect
        from modules.employee_privacy import get_own_payroll_summary
        params = list(inspect.signature(get_own_payroll_summary).parameters)
        assert params == ["requester_phone"]


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
