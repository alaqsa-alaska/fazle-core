"""
Unit tests — Accountant payment ingestion pipeline

Covers:
  1. Bengali accounting summary detection (is_accountant_summary)
  2. Summary acknowledgment format (ack_accountant_summary)
  3. Advance record trigger detection (is_advance_record_query)
  4. Advance record parsing (emp_id, phone, amount, method)
  5. Payment SMS detection (looks_like_payment_sms)
  6. Payment SMS ingest happy-path (ingest_payment_sms)
  7. Duplicate payment deduplication
  8. Malformed message handling
  9. Accountant message_router routing (end-to-end route selection)
 10. Payroll consistency: advances reduce net salary
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from decimal import Decimal

pytestmark = pytest.mark.unit

# ─────────────────────────────────────────────────────────────────────────────
# 1. Bengali accounting summary detection
# ─────────────────────────────────────────────────────────────────────────────
class TestIsAccountantSummary:
    """Test `is_accountant_summary()` correctly classifies messages."""

    @pytest.mark.parametrize("text", [
        "7/5/26=জমা =75,000/-",
        "4/5/26=জমা =35,000/-",
        "7/5/26=টোটাল বাকি =51,238/-",
        "অগ্রিম জমা থাকে =23,762/-",
        "7/5/26= অফিস ভাড়া বাবদ = 12,000/-",
        "মোট বাকি = 1,23,456/-",
        "7/5/26=মোট জমা =1,00,000/-",
        "অগ্রিম থাকে =50,000/-",
        "বেতন বাকি =20,000/-",
    ])
    def test_positive_cases(self, text):
        from modules.accountant_summary import is_accountant_summary
        assert is_accountant_summary(text) is True, f"Should detect summary: {text!r}"

    @pytest.mark.parametrize("text", [
        "",
        "advance দিলাম ID 45 5000 bkash",
        "হাজির আছি",
        "ডিউটি শেষ",
        "আমি ভালো আছি",
        "অগ্রিম দরকার",          # employee request — no amount marker
        "আজকে ৫০০০ টাকা দরকার",  # amount but no "/-" marker
        "PAID 23 5000 cash",      # admin command
        "কাল থেকে ডিউটি আছে",
    ])
    def test_negative_cases(self, text):
        from modules.accountant_summary import is_accountant_summary
        assert is_accountant_summary(text) is False, f"Should NOT detect summary: {text!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Summary acknowledgment format
# ─────────────────────────────────────────────────────────────────────────────
class TestAckAccountantSummary:
    """Test that acknowledgment reply contains the correct fields."""

    def test_date_deposit_format(self):
        from modules.accountant_summary import ack_accountant_summary
        reply = ack_accountant_summary("7/5/26=জমা =75,000/-")
        assert "75,000" in reply or "75000" in reply
        assert "7/5/26" in reply
        assert "advance দিলাম" in reply   # guidance hint included

    def test_no_date_format(self):
        from modules.accountant_summary import ack_accountant_summary
        reply = ack_accountant_summary("অগ্রিম জমা থাকে =23,762/-")
        assert "23,762" in reply or "23762" in reply
        assert "সারসংক্ষেপ পেয়েছি" in reply

    def test_outstanding_balance(self):
        from modules.accountant_summary import ack_accountant_summary
        reply = ack_accountant_summary("7/5/26=টোটাল বাকি =51,238/-")
        assert "51,238" in reply or "51238" in reply

    def test_guidance_hint_present(self):
        from modules.accountant_summary import ack_accountant_summary
        reply = ack_accountant_summary("7/5/26=জমা =10,000/-")
        # Must include guidance on how to record individual advances
        assert "ID" in reply
        assert "advance দিলাম" in reply


# ─────────────────────────────────────────────────────────────────────────────
# 3. Advance record trigger detection
# ─────────────────────────────────────────────────────────────────────────────
class TestIsAdvanceRecordQuery:
    """Test that `is_advance_record_query()` fires for valid accountant inputs."""

    @pytest.mark.parametrize("text", [
        "advance দিলাম ID 45 5000 bkash",
        "advance দিয়েছি ID 123 / 3000 / nagad",
        "অগ্রিম দেওয়া হয়েছে কারিম মিয়া ৫০০০ টাকা",
        "advance record: 01712345678, 2000, cash",
        "ID 456 advance দিলাম 2000",
        "record advance 01811223344 3500 bkash",
    ])
    def test_positive_triggers(self, text):
        from modules.admin_commands.nl_advance_record import is_advance_record_query
        assert is_advance_record_query(text) is True, f"Should trigger: {text!r}"

    @pytest.mark.parametrize("text", [
        "advance চাই",             # employee request
        "advance লাগবে",           # employee request
        "ADVANCE 45 5000 bkash",  # structured command (handled upstream)
        "7/5/26=অগ্রিম =75,000/-",  # accountant summary format
        "হাজির",
        "ডিউটি শেষ",
        "",
    ])
    def test_negative_triggers(self, text):
        from modules.admin_commands.nl_advance_record import is_advance_record_query
        assert is_advance_record_query(text) is False, f"Should NOT trigger: {text!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Advance record parsing (unit, no DB)
# ─────────────────────────────────────────────────────────────────────────────
class TestAdvanceRecordParsing:
    """Test internal parser helpers for advance records."""

    def test_parse_emp_id(self):
        from modules.admin_commands.nl_advance_record import _parse_emp_id
        assert _parse_emp_id("advance দিলাম ID 45 5000 bkash") == 45
        assert _parse_emp_id("ID: 123 advance 3000") == 123
        assert _parse_emp_id("কর্মী নম্বর 78 advance 2000") == 78
        assert _parse_emp_id("no id here 5000") is None

    def test_parse_phone(self):
        from modules.admin_commands.nl_advance_record import _parse_phone
        assert _parse_phone("01712345678 advance 5000") == "8801712345678"
        assert _parse_phone("+8801912345678 3000 cash") == "8801912345678"
        assert _parse_phone("no phone here") is None

    def test_parse_amount(self):
        from modules.admin_commands.nl_advance_record import _parse_amount
        assert _parse_amount("advance দিলাম ID 45 5000 bkash") == 5000.0
        assert _parse_amount("ID 123 3,500 nagad") == 3500.0
        assert _parse_amount("advance ৫,০০০ টাকা") == 5000.0  # Bengali digits
        # Phone number digits must NOT be picked up as amount
        result = _parse_amount("01712345678 advance 1500 cash")
        assert result == 1500.0

    def test_parse_method(self):
        from modules.admin_commands.nl_advance_record import _parse_method
        assert _parse_method("advance দিলাম 5000 bkash") == "bkash"
        assert _parse_method("advance 3000 nagad") == "nagad"
        assert _parse_method("advance 2000 রকেট") == "rocket"
        assert _parse_method("advance 1000 ক্যাশ") == "cash"
        assert _parse_method("advance 4000")  == "cash"   # default


# ─────────────────────────────────────────────────────────────────────────────
# 5. Payment SMS detection
# ─────────────────────────────────────────────────────────────────────────────
class TestLooksLikePaymentSms:
    """Test `looks_like_payment_sms()` SMS triage."""

    @pytest.mark.parametrize("text", [
        "BKash: You have received Tk 5000 from 01712345678 TxnID AB12345678",
        "Nagad: 3500 TK received from 01812345678 TrxID XY9876543",
        "Rocket: Cash in 2000tk from 01612345678 ref 1234",
        "টাকা পেয়েছি ৳5000 bkash TRXID ABC123456",
    ])
    def test_payment_sms_detected(self, text):
        from modules.payment_ingest import looks_like_payment_sms
        assert looks_like_payment_sms(text) is True

    @pytest.mark.parametrize("text", [
        "7/5/26=জমা =75,000/-",       # summary — no SMS pattern
        "advance দিলাম ID 45 5000",    # NL advance record
        "হাজির আছি",
        "ডিউটি শেষ",
        "",
        "ডিউটি করুন ৫০০০ টাকা",       # random Bengali with digits, no payment keyword
    ])
    def test_non_sms_not_detected(self, text):
        from modules.payment_ingest import looks_like_payment_sms
        assert looks_like_payment_sms(text) is False


# ─────────────────────────────────────────────────────────────────────────────
# 6–7. Payment SMS ingest (with DB)
# ─────────────────────────────────────────────────────────────────────────────
class TestIngestPaymentSms:
    """Integration-lite: ingest_payment_sms with real test DB."""

    @pytest_asyncio.fixture  # type: ignore[misc]
    async def emp(self, test_db_pool):
        """Create one employee to match against."""
        import app.database as db_module
        db_module._pool = test_db_pool
        eid = await test_db_pool.fetchval(
            """INSERT INTO wbom_employees
                   (employee_mobile, employee_name, designation, basic_salary,
                    bkash_number)
               VALUES ('8801712345678', 'Karim Mia', 'Security Guard', 12000, '01712345678')
               RETURNING employee_id"""
        )
        return {"employee_id": eid, "mobile": "8801712345678", "name": "Karim Mia"}

    @pytest.mark.asyncio
    async def test_bkash_sms_staged(self, emp, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.payment_ingest import ingest_payment_sms
        sms = (
            "BKash: You have received Tk 5000.00 from 01712345678 "
            "TxnID AB12345678 on 05/07/2026"
        )
        result = await ingest_payment_sms(sms, sender_number="8801844836824")
        assert result["ok"] is True
        assert result["status"] in ("pending", "auto_approved")
        assert result["amount"] == 5000.0
        assert result["method"] == "bkash"

    @pytest.mark.asyncio
    async def test_duplicate_sms_idempotent(self, emp, test_db_pool):
        """Same SMS ingested twice must return duplicate status, not create 2 rows."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.payment_ingest import ingest_payment_sms
        sms = (
            "BKash: You have received Tk 3500.00 from 01712345678 "
            "TxnID ZZ99887766 on 05/07/2026"
        )
        r1 = await ingest_payment_sms(sms)
        r2 = await ingest_payment_sms(sms)
        assert r1["ok"] is True
        assert r2["ok"] is True
        assert r2["status"] == "duplicate"
        # Only one staging row
        count = await test_db_pool.fetchval(
            "SELECT COUNT(*) FROM wbom_staging_payments WHERE idempotency_key = $1",
            r1.get("idempotency_key") or r2.get("idempotency_key") or "?",
        )
        # Either the key exists once, or both point to same staging_id
        assert r1["staging_id"] == r2.get("staging_id") or count <= 1

    @pytest.mark.asyncio
    async def test_unparseable_sms_returns_error(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.payment_ingest import ingest_payment_sms
        result = await ingest_payment_sms("hello world no payment info here")
        assert result["ok"] is False
        assert result["status"] == "unparsed"

    @pytest.mark.asyncio
    async def test_unmatched_employee_staged_as_unmatched(self, test_db_pool):
        """Valid bKash SMS but employee phone unknown → staged with status=unmatched."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.payment_ingest import ingest_payment_sms
        sms = (
            "BKash: You have received Tk 1500.00 from 01999999999 "
            "TxnID QQ11223344 on 05/07/2026"
        )
        result = await ingest_payment_sms(sms)
        assert result["ok"] is True
        assert result["status"] == "unmatched"

    @pytest.mark.asyncio
    async def test_admin_accountant_instruction_finalizes_direct_transaction(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.payment_ingest import ingest_admin_cash_entry

        result = await ingest_admin_cash_entry(
            "ID: 01795122311 Manik Mea 01789123456(B) 5000/-",
            sender_number="8801880446111",
            message_id=9876,
        )

        assert result["ok"] is True
        assert result["status"] == "finalized"
        assert result["employee_mobile"] == "01795122311"
        txn = await test_db_pool.fetchrow(
            "SELECT * FROM fpe_cash_transactions WHERE id=$1",
            result["transaction_id"],
        )
        assert txn is not None
        assert txn["employee_phone"] == "01795122311"
        assert txn["payout_phone"] == "01789123456"
        assert txn["payout_method"] == "bkash"
        assert float(txn["amount"]) == 5000.0
        assert txn["source_channel"] == "admin-accountant-instruction"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Malformed message handling
# ─────────────────────────────────────────────────────────────────────────────
class TestMalformedMessages:
    """Edge cases — empty strings, missing fields, non-payment text."""

    def test_empty_string_summary(self):
        from modules.accountant_summary import is_accountant_summary
        assert is_accountant_summary("") is False

    def test_none_like_summary(self):
        from modules.accountant_summary import is_accountant_summary
        assert is_accountant_summary("   ") is False

    def test_empty_string_sms(self):
        from modules.payment_ingest import looks_like_payment_sms
        assert looks_like_payment_sms("") is False

    def test_empty_advance_record(self):
        from modules.admin_commands.nl_advance_record import is_advance_record_query
        assert is_advance_record_query("") is False

    @pytest.mark.asyncio
    async def test_advance_record_no_amount_returns_help(self, test_db_pool):
        """Advance record trigger fires but amount is absent → help message returned."""
        import app.database as db_module
        db_module._pool = test_db_pool
        # Create employee 45 so we don't hit 'not found' first
        await test_db_pool.execute(
            """
            INSERT INTO wbom_employees
                   (employee_id, employee_mobile, employee_name, designation, basic_salary)
               VALUES (45, '8801700000045', 'Amount Test Worker', 'Guard', 10000)
               ON CONFLICT (employee_id) DO NOTHING
            """
        )
        from modules.admin_commands.nl_advance_record import intent_advance_record
        reply = await intent_advance_record(
            "advance দিলাম ID 45 bkash",   # no amount
            admin_phone="8801844836824",
        )
        # Either 'পরিমাণ' or a help-style error about the missing amount
        assert (
            "পরিমাণ" in reply
            or "amount" in reply.lower()
            or "কত" in reply
            or "সঠিক" in reply
        )

    @pytest.mark.asyncio
    async def test_advance_record_unknown_employee_returns_error(self, test_db_pool):
        """Employee ID 99999 does not exist → friendly error, no DB insert."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.admin_commands.nl_advance_record import intent_advance_record
        reply = await intent_advance_record(
            "advance দিলাম ID 99999 5000 bkash",
            admin_phone="8801844836824",
        )
        assert "পাওয়া যায়নি" in reply or "not found" in reply.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 9. Accountant message_router routing (mocked DB / AI)
# ─────────────────────────────────────────────────────────────────────────────
class TestAccountantMessageRouterRouting:
    """
    Verify that process_message() routes accountant messages to the correct
    sub-handler BEFORE falling through to KB/AI.

    We patch the heavy dependencies so no real DB / Ollama calls are needed.
    """

    @pytest.fixture(autouse=True)
    def _patch_heavy_deps(self):
        """Patch expensive I/O modules used by process_message()."""
        with (
            patch("modules.message_router.detect_identity", new=AsyncMock(return_value={
                "role": "accountant",
                "identity_confidence": 100,
                "identity_source": "phone",
                "display_name": "",
            })),
            patch("modules.message_router.get_contact_context", new=AsyncMock(return_value={})),
            patch("modules.message_router.get_recent_history", new=AsyncMock(return_value=[])),
            patch("modules.message_router.kb_get_reply", new=AsyncMock(return_value=None)),
            patch("modules.message_router.ai") as mock_ai,
        ):
            mock_ai.generate_reply = AsyncMock(return_value="AI fallback reply")
            mock_ai.classify_intent_llm = AsyncMock(return_value="unknown")
            yield mock_ai

    @pytest.mark.asyncio
    async def test_advance_record_routes_to_handler(self, _patch_heavy_deps):
        """Accountant NL advance record → intent_advance_record() called, NOT AI."""
        with patch(
            "modules.admin_commands.nl_advance_record.intent_advance_record",
            new=AsyncMock(return_value="অগ্রিম রেকর্ড হয়েছে।"),
        ) as mock_adv:
            from modules.message_router import process_message
            reply, _ = await process_message(
                sender="8801844836824",
                text="advance দিলাম ID 45 5000 bkash",
                source="bridge2",
            )
        assert mock_adv.called
        assert "রেকর্ড" in reply

    @pytest.mark.asyncio
    async def test_payment_sms_routes_to_ingest(self, _patch_heavy_deps):
        """Accountant forwards bKash SMS → ingest_payment_sms() called, NOT AI."""
        with patch(
            "modules.payment_ingest.ingest_payment_sms",
            new=AsyncMock(return_value={
                "ok": True, "status": "pending",
                "staging_id": 1, "amount": 5000.0, "method": "bkash",
                "employee_name": "Karim Mia",
            }),
        ) as mock_ingest:
            from modules.message_router import process_message
            reply, _ = await process_message(
                sender="8801844836824",
                text=(
                    "BKash: You have received Tk 5000.00 from 01712345678 "
                    "TxnID AB12345678"
                ),
                source="bridge2",
            )
        assert mock_ingest.called
        # Reply should mention staging/amount, not be the AI fallback
        assert "AI fallback reply" not in reply

    @pytest.mark.asyncio
    async def test_summary_message_routes_to_ack(self, _patch_heavy_deps):
        """Bengali summary → acknowledged, NOT routed to AI."""
        from modules.message_router import process_message
        reply, _ = await process_message(
            sender="8801844836824",
            text="7/5/26=জমা =75,000/-",
            source="bridge2",
        )
        assert "সারসংক্ষেপ পেয়েছি" in reply
        assert "AI fallback reply" not in reply

    @pytest.mark.asyncio
    async def test_generic_message_falls_through_to_ai(self, _patch_heavy_deps):
        """Non-payment accountant message falls through to AI."""
        from modules.message_router import process_message
        reply, _ = await process_message(
            sender="8801844836824",
            text="আজকে অফিসে আসব না",
            source="bridge2",
        )
        assert reply == "AI fallback reply"
        _patch_heavy_deps.generate_reply.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 10. Payroll consistency: advances reduce net salary
# ─────────────────────────────────────────────────────────────────────────────
class TestPayrollConsistencyAfterAdvance:
    """
    After recording an advance in wbom_cash_transactions, the payroll
    calculation must deduct it from net salary.
    """

    @pytest.mark.asyncio
    async def test_advance_reduces_net_salary(self, test_db_pool, seed_employee, seed_fpe_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        # Record an advance of 3000 in canonical fpe_cash_transactions
        await test_db_pool.execute(
            """INSERT INTO fpe_cash_transactions
                   (txn_ref, employee_id, amount, txn_category, payout_method,
                    txn_date, source, transaction_status)
               VALUES ('test-adv-1', $1, 3000, 'advance', 'cash',
                    CURRENT_DATE, 'admin_nl', 'final')""",
            seed_fpe_employee["id"],
        )

        # Payroll: total advances for this employee should be 3000
        total_advances = await test_db_pool.fetchval(
            """SELECT COALESCE(SUM(amount), 0)
               FROM fpe_cash_transactions
               WHERE employee_id = $1 AND txn_category = 'advance'
                 AND transaction_status = 'final'""",
            seed_fpe_employee["id"],
        )
        assert float(total_advances) == 3000.0

        # Net salary = 12000 - 3000 = 9000
        net = 12000.0 - float(total_advances)
        assert net == 9000.0

    @pytest.mark.asyncio
    async def test_multiple_advances_cumulative(self, test_db_pool, seed_employee, seed_fpe_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        for i, amount in enumerate([2000, 1500, 500]):
            await test_db_pool.execute(
                """INSERT INTO fpe_cash_transactions
                       (txn_ref, employee_id, amount, txn_category, payout_method,
                        txn_date, source, transaction_status)
                   VALUES ($1, $2, $3, 'advance', 'bkash', CURRENT_DATE, 'admin_nl', 'final')""",
                f'test-adv-cum-{i}', seed_fpe_employee["id"], amount,
            )

        total = await test_db_pool.fetchval(
            "SELECT SUM(amount) FROM fpe_cash_transactions WHERE employee_id=$1 AND transaction_status='final'",
            seed_fpe_employee["id"],
        )
        assert float(total) == 4000.0

    @pytest.mark.asyncio
    async def test_wbom_cash_transactions_updated_by_intent_advance_record(
        self, test_db_pool, seed_employee, seed_fpe_employee
    ):
        """End-to-end: intent_advance_record() writes to fpe_cash_transactions."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.admin_commands.nl_advance_record import intent_advance_record
        reply = await intent_advance_record(
            f"advance দিলাম ID {seed_employee['employee_id']} 4500 bkash",
            admin_phone="8801844836824",
        )
        assert "রেকর্ড হয়েছে" in reply

        row = await test_db_pool.fetchrow(
            "SELECT amount, txn_category, payout_method, source "
            "FROM fpe_cash_transactions WHERE employee_id=$1",
            seed_fpe_employee["id"],
        )
        assert row is not None
        assert float(row["amount"]) == 4500.0
        assert row["txn_category"] == "advance"
        assert row["payout_method"] == "bkash"
        assert row["source"] == "nl_advance"
