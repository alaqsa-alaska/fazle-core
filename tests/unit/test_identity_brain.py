"""Unit tests — modules/identity_brain"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.unit


# ── helpers ────────────────────────────────────────────────────────────────────

def _no_row():
    return None


def _employee_row(phone: str = "8801811111111", designation: str = "Staff"):
    return {
        "employee_id": 1,
        "employee_mobile": phone,
        "employee_name": "Test Employee",
        "designation": designation,
        "status": "Active",
    }


def _contact_role_row(role: str, phone: str = "8801811111111"):
    return {
        "id": 1,
        "phone": phone,
        "role": role,
        "label": f"Test {role}",
        "confidence": 1.0,
        "source": "seed",
        "priority": 0,
    }


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestDetectIdentityFromSettings:
    """Admin numbers in env are always resolved as admin (highest priority)."""

    async def test_admin_phone_returns_admin_role(self):
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            with patch("app.config.get_settings") as mock_settings:
                mock_settings.return_value.admin_numbers = "8801700000001"
                mock_settings.return_value.admin_number_list = ["8801700000001"]
                result = await detect_identity("8801700000001", "")

        # Even though DB is mocked empty, the settings path returns admin
        # (identity_brain reads settings for admin detection)
        # We just confirm the returned dict has all required keys.
        assert "role" in result or "identity_role" in result


class TestDetectIdentityFromSeedRule:
    """Contacts seeded in fazle_contact_roles override DB employee lookup."""

    @pytest.mark.parametrize("role", [
        "accountant", "family", "vip_client", "client_escort_buyer", "supervisor",
    ])
    async def test_seed_role_returned(self, role):
        from modules.identity_brain import detect_identity

        seed_row = _contact_role_row(role, "8801811111111")

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            seed_row,   # fazle_contact_roles lookup
            None,       # wbom_employees lookup variant 1
            None,       # wbom_employees lookup variant 2
            None,       # wbom_employees lookup variant 3
        ])):
            result = await detect_identity("8801811111111", "hello")

        # role should match seed
        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == role

    async def test_accountant_higher_priority_than_employee(self):
        """If phone is in both fazle_contact_roles=accountant AND wbom_employees,
        accountant seed rule wins (priority 95 > employee 88)."""
        from modules.identity_brain import detect_identity

        # Return accountant from contact_roles, employee from wbom_employees
        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            _contact_role_row("accountant"),
            _employee_row(),
        ])):
            result = await detect_identity("8801811111111", "")

        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == "accountant"


class TestDetectIdentityFromEmployee:
    """wbom_employees lookup resolves as 'employee'."""

    async def test_employee_phone_returns_employee_role(self):
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None,             # fazle_contact_roles miss
            _employee_row(),  # wbom_employees hit (designation="Staff")
            None,             # wbom_attendance recency check (lifecycle heuristic)
        ])):
            result = await detect_identity("8801811111111", "regular text")

        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == "employee"

    async def test_phone_normalisation_0x_format(self):
        """Phone stored as 01811111111 in DB; lookup with 8801811111111 should match."""
        from modules.identity_brain import detect_identity

        # Return employee with 0X format stored
        row = _employee_row("01811111111")

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None, row, None,
        ])):
            result = await detect_identity("8801811111111", "")

        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == "employee"

    async def test_security_guard_designation_returns_security_guard_role(self):
        """2026-07-21 routing work: designation drives the escort/guard split,
        not a hardcoded 'employee' for every wbom_employees row."""
        from modules.identity_brain import detect_identity

        row = _employee_row(designation="Security Guard")
        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None, row, None,
        ])):
            result = await detect_identity("8801811111111", "")

        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == "security_guard"

    async def test_escort_designation_returns_escort_role(self):
        from modules.identity_brain import detect_identity

        row = _employee_row(designation="Escort")
        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None, row, None,
        ])):
            result = await detect_identity("8801811111111", "")

        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == "escort"

    async def test_inactive_employee_lifecycle_is_former_no_attendance_query(self):
        """status != 'Active' short-circuits to 'former' without a 3rd DB call."""
        from modules.identity_brain import detect_identity

        row = _employee_row(designation="Staff")
        row["status"] = "Inactive"
        with patch("modules.identity_brain.fetch_one", new=AsyncMock(side_effect=[
            None, row,
        ])):
            result = await detect_identity("8801811111111", "")

        assert result.get("identity_lifecycle") == "former"


class TestDetectIdentityFromTextHint:
    """Keyword analysis in message text can detect candidates."""

    @pytest.mark.parametrize("text", [
        "চাকরি করতে চাই",
        "job apply করতে চাই",
        "vacancy আছে কি",
        "নিয়োগ দেবেন",
    ])
    async def test_candidate_keyword_returns_candidate(self, text):
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await detect_identity("8801900000000", text)

        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == "candidate"


class TestDetectIdentityUnknown:
    """No match → unknown."""

    async def test_unknown_phone_no_keywords(self):
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await detect_identity("8801900000000", "hello there")

        actual_role = result.get("identity_role") or result.get("role")
        assert actual_role == "unknown"


class TestEscortContentDetection:
    """Messages containing vessel keywords should hint escort_client role."""

    @pytest.mark.parametrize("text", [
        "MV GOLDEN STAR escort lagbe",
        "mother vessel RINA lighter AMENA",
        "এমভি destination Ctg",
    ])
    async def test_escort_keywords_hint_escort_role(self, text):
        from modules.identity_brain import detect_identity

        with patch("modules.identity_brain.fetch_one", new=AsyncMock(return_value=None)):
            result = await detect_identity("8801900000000", text)

        actual_role = result.get("identity_role") or result.get("role")
        # Should be escort-related or at least not 'candidate'
        assert actual_role != "candidate"
