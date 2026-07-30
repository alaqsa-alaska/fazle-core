"""Unit tests -- Unified Admin AI Engine, Bucket 2 Step 5 (BUS-017).

Covers the payroll page-truth adapter: registry entry correctness, the
structural no-write-function-reference proof (Critical-module import
safety), and the handler's read/exception-handling behavior. Mocks
modules.fazle_payroll_engine.routes.health/list_transactions directly
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


class TestBus017Registry:
    def test_bus017_exists(self, loader):
        assert loader.exists("BUS-017")

    def test_bus017_min_role_is_admin(self, loader):
        assert loader.get("BUS-017").metadata.get("min_role") == "admin"

    def test_bus017_authority_reflects_fpe_source(self, loader):
        entry = loader.get("BUS-017")
        assert entry.authority == "fpe_readonly_view"
        assert "fpe_cash_transactions" in entry.purpose

    def test_no_existing_entry_disturbed(self, loader):
        # Spot-check a couple of pre-existing entries are still exactly
        # as they were before BUS-017 was added.
        assert loader.get("BUS-015").metadata == {"min_role": "admin"}
        assert loader.get("BUS-016").metadata == {"min_role": "admin"}


class TestPayrollPageSummaryStructuralSafety:
    def test_no_write_function_reference(self):
        source = inspect.getsource(ReadonlyBusinessAdapter._payroll_page_summary)
        forbidden = (
            "create_transaction", "reverse_transaction",
            "execute(", "INSERT", "UPDATE", "DELETE",
        )
        for token in forbidden:
            assert token not in source, f"forbidden write-capable reference found: {token!r}"

    def test_only_approved_functions_imported(self):
        source = inspect.getsource(ReadonlyBusinessAdapter._payroll_page_summary)
        assert "from modules.fazle_payroll_engine.routes import health" in source
        assert "from modules.fazle_payroll_engine.routes import list_transactions" in source

    def test_list_transactions_called_with_all_params_explicit(self):
        """Guards against the Query(None) sentinel-truthiness pitfall --
        every parameter must be passed by keyword, never relying on the
        function's own FastAPI-Query defaults."""
        source = inspect.getsource(ReadonlyBusinessAdapter._payroll_page_summary)
        required_kwargs = (
            "employee_id=None", "period=period", "method=None", "txn_category=None",
            "date_from=None", "date_to=None", "page=None", "page_size=None",
            "limit=20", "offset=0",
        )
        for kw in required_kwargs:
            assert kw in source, f"expected explicit kwarg not found in source: {kw!r}"


class TestPayrollPageSummaryHandler:
    async def test_returns_correct_envelope_shape(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-017")

        fake_health = {"status": "ok", "total_messages_ingested": 100, "total_transactions": 42, "pending_processing": 0}
        fake_transactions = {
            "total": 2, "page": 1, "page_size": 20, "pages": 1,
            "summary": {"total": 2, "total_amount": 15000.0, "first_txn": "2026-07-01", "last_txn": "2026-07-15"},
            "transactions": [
                {"employee_display_name": "Karim", "amount": 5000.0, "txn_date": "2026-07-01", "payout_method": "bkash", "txn_category": "salary"},
                {"employee_display_name": "Rahim", "amount": 10000.0, "txn_date": "2026-07-15", "payout_method": "cash", "txn_category": "salary"},
            ],
        }

        with patch("modules.fazle_payroll_engine.routes.health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.fazle_payroll_engine.routes.list_transactions", new=AsyncMock(return_value=fake_transactions)):
            # Note: month/year TEXT extraction is delegated to the existing
            # shared _extract_month_year() helper (also used by BUS-003/
            # BUS-004). Discovered while writing this test: its year regex
            # (r"(20\\d{2})") is double-escaped, the same bug class Step 2
            # fixed in _extract_name_hint() -- but NOT fixed here, so it
            # never matches a year from free text at all, in any language
            # (confirmed empirically, even plain ASCII "2026" fails). This
            # is a separate, pre-existing, out-of-scope bug shared by other
            # existing handlers -- flagged, not fixed in this step. Passing
            # month/year directly via payload (the function's first-checked,
            # already-working path: "if isinstance(month, int) and
            # isinstance(year, int): return month, year") to correctly
            # exercise this handler's OWN logic independent of that bug.
            envelope = await adapter._payroll_page_summary(entry, {"month": 7, "year": 2026, "question": "payroll summary"})

        assert envelope.status == "ok"
        assert envelope.data["source"] == "fpe_cash_transactions"
        assert envelope.data["total_transactions"] == 42
        assert envelope.data["period"] == "2026-07"
        assert len(envelope.data["rows"]) == 2
        assert envelope.data["rows"][0]["employee_name"] == "Karim"

    async def test_no_period_detected_skips_transactions_call(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-017")
        fake_health = {"status": "ok", "total_transactions": 10}

        with patch("modules.fazle_payroll_engine.routes.health", new=AsyncMock(return_value=fake_health)), \
             patch("modules.fazle_payroll_engine.routes.list_transactions", new=AsyncMock()) as mock_txns:
            envelope = await adapter._payroll_page_summary(entry, {"question": "payroll status"})

        mock_txns.assert_not_awaited()
        assert envelope.data["period"] == "all"
        assert envelope.data["rows"] == []
        assert envelope.data["total_transactions"] == 10

    async def test_exception_returns_failed_status(self, loader):
        adapter = ReadonlyBusinessAdapter(loader)
        entry = loader.get("BUS-017")

        with patch("modules.fazle_payroll_engine.routes.health", new=AsyncMock(side_effect=RuntimeError("db down"))):
            envelope = await adapter._payroll_page_summary(entry, {"question": "payroll summary"})

        assert envelope.status == "failed"
        assert envelope.errors
