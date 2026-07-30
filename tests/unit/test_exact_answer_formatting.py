"""Unit tests -- Unified Admin AI Engine, Bucket 2 Step 7 (exact-answer formatting).

Covers modules.ai_console.provider_registry.LLMProviderAdapter.respond()'s
exact-answer short-circuit for employee_search (BUS-001), payroll_page_summary
(BUS-017), and escort_roster_page_summary (BUS-018) -- in every covered case
the LLM must never be called, and the reply must be built directly from the
tool's own evidence data.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules.ai_console.envelopes import EvidenceEnvelope
from modules.ai_console.provider_registry import LLMProviderAdapter

pytestmark = pytest.mark.unit


def _evidence(business: list[dict]) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        request_id="test-req",
        mode="business",
        classification={"requested_model": None, "history": []},
        business=business,
    )


def _business_item(tool_name: str, data: dict) -> dict:
    return {
        "tool_id": "TEST-000",
        "tool_name": tool_name,
        "status": "ok",
        "confidence": "Verified",
        "source": "test",
        "timestamp": "2026-07-30T00:00:00Z",
        "data": data,
        "authority": "test",
        "warnings": [],
        "errors": [],
        "traceability": [],
    }


@pytest.fixture()
def adapter():
    return LLMProviderAdapter()


class TestEmployeeSearchExactAnswer:
    async def test_one_match(self, adapter):
        evidence = _evidence([_business_item("employee_search", {
            "matches": [{"employee_name": "বিজয় দাস", "employee_id": 123, "employee_mobile": "017XXXXXXXX"}],
            "count": 1,
            "query": "বিজয়",
        })])
        with patch("modules.ai_console.provider_registry.ai.generate_chat_reply", new=AsyncMock()) as mock_llm:
            result = await adapter.respond("বিজয় নামে কতজন কর্মচারি আছে?", evidence)

        mock_llm.assert_not_awaited()
        reply = result["response"]
        assert reply.startswith("মোট: ১ জন")
        assert "বিজয় দাস" in reply
        assert reply.endswith("(সোর্স: employee database)")

    async def test_three_matches(self, adapter):
        evidence = _evidence([_business_item("employee_search", {
            "matches": [
                {"employee_name": "বিজয় দাস", "employee_id": 1, "employee_mobile": "01700000001"},
                {"employee_name": "মো. বিজয়", "employee_id": 2, "employee_mobile": "01700000002"},
                {"employee_name": "বিজয় কুমার", "employee_id": 3, "employee_mobile": "01700000003"},
            ],
            "count": 3,
            "query": "বিজয়",
        })])
        with patch("modules.ai_console.provider_registry.ai.generate_chat_reply", new=AsyncMock()) as mock_llm:
            result = await adapter.respond("বিজয় নামে কতজন কর্মচারি আছে?", evidence)

        mock_llm.assert_not_awaited()
        reply = result["response"]
        assert "মোট: ৩ জন" in reply
        assert "১." in reply and "২." in reply and "৩." in reply

    async def test_zero_matches(self, adapter):
        evidence = _evidence([_business_item("employee_search", {
            "matches": [], "count": 0, "query": "নেইএমননামের",
        })])
        with patch("modules.ai_console.provider_registry.ai.generate_chat_reply", new=AsyncMock()) as mock_llm:
            result = await adapter.respond("নেইএমননামের নামে কতজন কর্মচারি আছে?", evidence)

        mock_llm.assert_not_awaited()
        reply = result["response"]
        assert "নম্বরের" not in reply  # the stale, phone-specific wording must be gone
        assert "নেইএমননামের" in reply
        assert "রেকর্ড পাওয়া যায়নি" in reply


class TestPayrollPageSummaryExactAnswer:
    async def test_with_rows(self, adapter):
        evidence = _evidence([_business_item("payroll_page_summary", {
            "total_transactions": 42,
            "period": "2026-07",
            "rows": [
                {"employee_name": "রহিম", "amount": 1000.0, "payout_method": "bkash", "txn_date": "2026-07-30"},
                {"employee_name": "করিম", "amount": 1500.0, "payout_method": "nagad", "txn_date": "2026-07-30"},
            ],
            "source": "fpe_cash_transactions",
        })])
        with patch("modules.ai_console.provider_registry.ai.generate_chat_reply", new=AsyncMock()) as mock_llm:
            result = await adapter.respond("আজকের পেমেন্ট কতজন?", evidence)

        mock_llm.assert_not_awaited()
        reply = result["response"]
        assert "২ জন" in reply
        assert "৳" in reply
        assert reply.endswith("(সোর্স: fpe_cash_transactions)")

    async def test_no_rows(self, adapter):
        evidence = _evidence([_business_item("payroll_page_summary", {
            "total_transactions": 0, "period": "all", "rows": [], "source": "fpe_cash_transactions",
        })])
        with patch("modules.ai_console.provider_registry.ai.generate_chat_reply", new=AsyncMock()) as mock_llm:
            result = await adapter.respond("পেমেন্ট সামারি দাও", evidence)

        mock_llm.assert_not_awaited()
        assert "কোনো পেমেন্ট লেনদেন পাওয়া যায়নি" in result["response"]


class TestEscortRosterPageSummaryExactAnswer:
    async def test_with_summary_data(self, adapter):
        evidence = _evidence([_business_item("escort_roster_page_summary", {
            "by_status": {"active": {"count": 3, "total_amount": 5000.0, "net_payable_amount": 4500.0}},
            "total_count": 3,
            "total_amount": 5000.0,
            "net_payable_total": 4500.0,
            "rows": [{"escort_name": "জয় দাস", "mother_vessel": "MV ABC", "roster_status": "active"}],
            "source": "escort_roster_entries",
        })])
        with patch("modules.ai_console.provider_registry.ai.generate_chat_reply", new=AsyncMock()) as mock_llm:
            result = await adapter.respond("এসকর্ট রোস্টার-এ জয় আছে?", evidence)

        mock_llm.assert_not_awaited()
        reply = result["response"]
        assert "মোট রোস্টারে" in reply
        assert reply.endswith("(সোর্স: escort_roster_entries)")

    async def test_no_data(self, adapter):
        evidence = _evidence([_business_item("escort_roster_page_summary", {
            "by_status": {}, "total_count": 0, "total_amount": 0, "net_payable_total": 0,
            "rows": [], "source": "escort_roster_entries",
        })])
        with patch("modules.ai_console.provider_registry.ai.generate_chat_reply", new=AsyncMock()) as mock_llm:
            result = await adapter.respond("এসকর্ট রোস্টার", evidence)

        mock_llm.assert_not_awaited()
        assert "কোনো রোস্টার তথ্য পাওয়া যায়নি" in result["response"]


class TestOtherToolsStillUseLlm:
    async def test_unrelated_tool_falls_through_to_llm(self, adapter):
        evidence = _evidence([_business_item("payroll_summary", {"rows": [{"foo": "bar"}]})])
        with patch("modules.ai_console.provider_registry.ai.generate_chat_reply", new=AsyncMock(return_value="llm reply")) as mock_llm:
            result = await adapter.respond("payroll summary", evidence)

        mock_llm.assert_awaited_once()
        assert result["response"] == "llm reply"
