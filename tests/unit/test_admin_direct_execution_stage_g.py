"""Stage G13/G14/G27 (2026-08-28) -- modules.admin_direct_execution."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules.admin_direct_execution import (
    parse_employee_creation_instruction,
    is_trusted_direct_admin_channel,
    execute_trusted_employee_creation,
)

pytestmark = pytest.mark.asyncio


class TestParsing:
    def test_canonical_bangla_shape_parses(self):
        r = parse_employee_creation_instruction("Mohammad Karamat Ali, 01712345678, employee হিসাবে create করো")
        assert r == {"employee_name": "Mohammad Karamat Ali", "employee_mobile": "01712345678"}

    def test_english_reordered_shape_parses(self):
        r = parse_employee_creation_instruction("create Mohammad Karamat Ali, 01712345678 as employee")
        assert r["employee_name"] == "Mohammad Karamat Ali"
        assert r["employee_mobile"] == "01712345678"

    def test_missing_employee_keyword_does_not_parse(self):
        assert parse_employee_creation_instruction("Mohammad Karamat Ali, 01712345678, add করো") is None

    def test_missing_create_keyword_does_not_parse(self):
        assert parse_employee_creation_instruction("Mohammad Karamat Ali, 01712345678, employee") is None

    def test_no_mobile_does_not_parse(self):
        assert parse_employee_creation_instruction("Mohammad Karamat Ali, employee হিসাবে create করো") is None

    def test_unrelated_text_does_not_parse(self):
        assert parse_employee_creation_instruction("approve draft 165") is None

    def test_empty_text_does_not_parse(self):
        assert parse_employee_creation_instruction("") is None
        assert parse_employee_creation_instruction(None) is None


class TestChannelCheck:
    def test_matching_phone_and_channel_is_trusted(self):
        assert is_trusted_direct_admin_channel("880188X", "bridge2", "880188X", frozenset({"bridge1", "bridge2"})) is True

    def test_wrong_channel_is_not_trusted(self):
        assert is_trusted_direct_admin_channel("880188X", "bridge3", "880188X", frozenset({"bridge1", "bridge2"})) is False

    def test_wrong_phone_is_not_trusted(self):
        assert is_trusted_direct_admin_channel("880199Y", "bridge2", "880188X", frozenset({"bridge1", "bridge2"})) is False

    def test_empty_phone_is_not_trusted(self):
        assert is_trusted_direct_admin_channel("", "bridge2", "880188X", frozenset({"bridge1", "bridge2"})) is False


class TestExecuteFlow:
    async def _settings(self, admin_number="880188X"):
        from app.config import get_settings
        s = get_settings()
        return s.model_copy(update={"admin_bridge2_number": admin_number})

    async def test_untrusted_channel_is_refused_before_any_parsing(self):
        settings = await self._settings()
        with patch("app.config.get_settings", return_value=settings):
            result = await execute_trusted_employee_creation(
                "Mohammad Karamat Ali, 01712345678, employee হিসাবে create করো",
                "880199Y", "bridge3", dry_run=True,
            )
        assert result["ok"] is False
        assert result["stage"] == "channel_check"

    async def test_ambiguous_instruction_requests_clarification_not_a_guess(self):
        settings = await self._settings()
        with patch("app.config.get_settings", return_value=settings):
            result = await execute_trusted_employee_creation(
                "please create the new guy as employee", "880188X", "bridge2", dry_run=True,
            )
        assert result["ok"] is False
        assert result["needs_clarification"] is True

    async def test_duplicate_mobile_refused_not_created(self):
        settings = await self._settings()
        with patch("app.config.get_settings", return_value=settings), \
             patch("modules.fazle_payroll_engine.normalizer.normalize_bd_phone", return_value="8801712345678"), \
             patch("app.database.fetch_one", new=AsyncMock(return_value={"employee_id": 5, "employee_name": "Existing Person", "status": "Active"})):
            result = await execute_trusted_employee_creation(
                "Mohammad Karamat Ali, 01712345678, employee হিসাবে create করো",
                "880188X", "bridge2", dry_run=True,
            )
        assert result["ok"] is False
        assert result["stage"] == "duplicate_check"
        assert result["existing_employee_id"] == 5

    async def test_dry_run_never_calls_create_employee(self):
        settings = await self._settings()
        create_mock = AsyncMock()
        with patch("app.config.get_settings", return_value=settings), \
             patch("modules.fazle_payroll_engine.normalizer.normalize_bd_phone", return_value="8801712345678"), \
             patch("app.database.fetch_one", new=AsyncMock(return_value=None)), \
             patch("modules.admin_employees.create_employee", new=create_mock):
            result = await execute_trusted_employee_creation(
                "Mohammad Karamat Ali, 01712345678, employee হিসাবে create করো",
                "880188X", "bridge2", dry_run=True,
            )
        assert result["ok"] is True
        assert result["stage"] == "dry_run"
        assert result["would_create"] == {"employee_name": "Mohammad Karamat Ali", "employee_mobile": "01712345678"}
        create_mock.assert_not_awaited()

    async def test_non_dry_run_calls_the_existing_authoritative_create_employee(self):
        """The direct instruction itself is the authorization -- no
        second approval request is created (Stage G13's explicit rule);
        this proves execution, when explicitly requested, reuses the
        SAME function the frontend uses, not a parallel write path."""
        settings = await self._settings()
        create_mock = AsyncMock(return_value={"employee": {"employee_id": 88}})
        with patch("app.config.get_settings", return_value=settings), \
             patch("modules.fazle_payroll_engine.normalizer.normalize_bd_phone", return_value="8801712345678"), \
             patch("app.database.fetch_one", new=AsyncMock(return_value=None)), \
             patch("modules.admin_employees.create_employee", new=create_mock):
            result = await execute_trusted_employee_creation(
                "Mohammad Karamat Ali, 01712345678, employee হিসাবে create করো",
                "880188X", "bridge2", dry_run=False,
            )
        assert result["ok"] is True
        assert result["stage"] == "executed"
        create_mock.assert_awaited_once()
