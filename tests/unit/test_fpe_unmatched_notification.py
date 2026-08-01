"""
Unit tests for modules.fazle_payroll_engine.ingestion.store_unmatched()'s
2026-08-01 push-notification behavior.

A message that lands in fpe_unmatched_messages with a detected amount (i.e.
it looked like a real cash instruction, not routine chit-chat/balance-summary
noise) must trigger an immediate WhatsApp alert to the configured admin
number(s) instead of only being discoverable by someone proactively checking
the Payroll -> Unmatched tab. No DB access — execute() and enqueue() are
fully mocked.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from modules.fazle_payroll_engine.ingestion import store_unmatched

pytestmark = pytest.mark.asyncio


@patch("modules.outbound.enqueue", new_callable=AsyncMock)
@patch("modules.fazle_payroll_engine.ingestion.execute", new_callable=AsyncMock)
async def test_notifies_admin_when_amount_detected(mock_execute, mock_enqueue, monkeypatch):
    monkeypatch.setenv("ADMIN_NUMBERS", "8801880446111,8801958122300")
    mock_execute.return_value = "INSERT 0 1"

    await store_unmatched(
        10030, "missing_employee_name", "ID: Runo 01773558211(N) 800/-",
        detected_amount=Decimal("800"),
        detected_payout_phone="01773558211",
        detected_employee_name=None,
    )

    assert mock_enqueue.await_count == 2
    recipients = {c.args[0] for c in mock_enqueue.await_args_list}
    assert recipients == {"8801880446111", "8801958122300"}
    for call in mock_enqueue.await_args_list:
        assert call.kwargs["purpose"] == "fpe-unmatched-review"
        assert "800" in call.args[1]
        assert call.kwargs["idempotency_key"].startswith("fpe-unmatched-10030-")


@patch("modules.outbound.enqueue", new_callable=AsyncMock)
@patch("modules.fazle_payroll_engine.ingestion.execute", new_callable=AsyncMock)
async def test_no_notification_for_routine_skip_without_amount(mock_execute, mock_enqueue, monkeypatch):
    """balance_summary / admin_other / accountant_other skips never pass
    detected_amount — these must stay silent (no notification spam)."""
    monkeypatch.setenv("ADMIN_NUMBERS", "8801880446111")
    mock_execute.return_value = "INSERT 0 1"

    await store_unmatched(9999, "balance_summary", "31/7/26=টোটাল বাকি =27,435/-")

    mock_enqueue.assert_not_awaited()


@patch("modules.outbound.enqueue", new_callable=AsyncMock)
@patch("modules.fazle_payroll_engine.ingestion.execute", new_callable=AsyncMock)
async def test_no_notification_on_conflict_no_insert(mock_execute, mock_enqueue, monkeypatch):
    """If the INSERT is a no-op (already exists), never double-notify."""
    monkeypatch.setenv("ADMIN_NUMBERS", "8801880446111")
    mock_execute.return_value = "INSERT 0 0"

    await store_unmatched(
        10030, "missing_employee_name", "ID: Runo 01773558211(N) 800/-",
        detected_amount=Decimal("800"),
    )

    mock_enqueue.assert_not_awaited()


@patch("modules.outbound.enqueue", new_callable=AsyncMock)
@patch("modules.fazle_payroll_engine.ingestion.execute", new_callable=AsyncMock)
async def test_no_admin_numbers_configured_is_a_noop(mock_execute, mock_enqueue, monkeypatch):
    monkeypatch.setenv("ADMIN_NUMBERS", "")
    mock_execute.return_value = "INSERT 0 1"

    await store_unmatched(
        10030, "missing_employee_name", "ID: Runo 01773558211(N) 800/-",
        detected_amount=Decimal("800"),
    )

    mock_enqueue.assert_not_awaited()
