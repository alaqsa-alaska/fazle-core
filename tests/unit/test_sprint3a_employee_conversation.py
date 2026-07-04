"""
Sprint-3A Acceptance Tests — Employee Conversation + Verification + Draft Generation

Tests Test-1 through Test-12 + Regression Tests.

HARD RULE: No create_transaction(), no _upsert_ledger(), no financial writes.
Success Metric: Verified Draft Generated (NOT Transaction Created).
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────────
# Test-1: Advance Request → Conversation starts
# ─────────────────────────────────────────────────────────────────────────────
class Test1AdvanceRequestConversation:
    async def test_advance_trigger_starts_conversation(
        self, test_db_pool, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import (
            detect_payment_request_trigger,
            handle_employee_payment_request,
        )

        # Trigger detection
        purpose = detect_payment_request_trigger("অগ্রিম লাগবে")
        assert purpose == "advance"

        # Full conversation start
        reply, admin_note = await handle_employee_payment_request(
            phone=seed_employee["employee_mobile"],
            text="অগ্রিম লাগবে",
            source="bridge1",
            employee_id=seed_employee["employee_id"],
        )
        assert reply  # non-empty reply
        assert admin_note is None  # no draft yet, no admin notification


# ─────────────────────────────────────────────────────────────────────────────
# Test-2: Inactive Employee → Knowledge Base Reply
# ─────────────────────────────────────────────────────────────────────────────
class Test2InactiveEmployeeKBReply:
    async def test_inactive_employee_gets_kb_reply(
        self, test_db_pool, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        # Mark employee inactive
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_employees SET status='Inactive' WHERE employee_id=$1",
                seed_employee["employee_id"],
            )

        from modules.employee_conversation import (
            handle_employee_payment_request,
            kb_inactive_employee_reply,
        )

        reply, admin_note = await handle_employee_payment_request(
            phone=seed_employee["employee_mobile"],
            text="অগ্রিম চাই",
            source="bridge1",
            employee_id=seed_employee["employee_id"],
        )

        # Should get a KB reply about inactive status
        assert reply
        assert "সক্রিয় নয়" in reply or "নয়" in reply
        assert admin_note is None  # no draft, no admin notification

        # KB reply function works
        kb_reply = await kb_inactive_employee_reply()
        assert kb_reply
        assert "নীতি" in kb_reply or "নোটিশ" in kb_reply


# ─────────────────────────────────────────────────────────────────────────────
# Test-3: Missing Information → Draft NOT created
# ─────────────────────────────────────────────────────────────────────────────
class Test3MissingInfoNoDraft:
    async def test_missing_amount_no_draft(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # Start conversation
        await handle_employee_payment_request(phone, "অগ্রিম চাই", "bridge1", emp_id)

        # Send non-amount text (should not create draft)
        reply, admin_note = await handle_employee_payment_request(
            phone, "নাম করিম", "bridge1", emp_id
        )
        assert admin_note is None  # no draft

        # Verify no draft in DB
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_payment_drafts WHERE employee_id=$1",
                emp_id,
            )
        assert count == 0

    async def test_missing_payout_no_draft(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # Start + provide amount
        await handle_employee_payment_request(phone, "অগ্রিম চাই", "bridge1", emp_id)
        await handle_employee_payment_request(phone, "২০০০", "bridge1", emp_id)

        # Send invalid payout (no draft)
        reply, admin_note = await handle_employee_payment_request(
            phone, "কিছুই না", "bridge1", emp_id
        )
        assert admin_note is None

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_payment_drafts WHERE employee_id=$1",
                emp_id,
            )
        assert count == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test-4: Verification Complete → Draft Created
# ─────────────────────────────────────────────────────────────────────────────
class Test4VerificationCompleteDraftCreated:
    async def test_full_conversation_creates_draft(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # 1. Trigger
        r1, a1 = await handle_employee_payment_request(phone, "অগ্রিম চাই", "bridge1", emp_id)
        assert r1
        assert a1 is None

        # 2. Amount
        r2, a2 = await handle_employee_payment_request(phone, "২০০০", "bridge1", emp_id)
        assert "2,000" in r2 or "2000" in r2 or "২০০০" in r2
        assert a2 is None

        # 3. Payout
        r3, a3 = await handle_employee_payment_request(
            phone, "বিকাশ 01712345678", "bridge1", emp_id
        )
        assert a3 is None  # confirmation step, not draft yet

        # 4. Confirm
        r4, a4 = await handle_employee_payment_request(phone, "confirm", "bridge1", emp_id)
        assert a4 is not None  # draft created → admin notification
        assert "draft_id" in a4

        # Verify draft in DB
        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_payment_drafts WHERE employee_id=$1 ORDER BY id DESC LIMIT 1",
                emp_id,
            )
        assert draft is not None
        assert draft["draft_type"] == "advance"
        assert float(draft["expected_amount"]) == 2000.0


# ─────────────────────────────────────────────────────────────────────────────
# Test-5: Draft Status → pending
# ─────────────────────────────────────────────────────────────────────────────
class Test5DraftStatusPending:
    async def test_draft_status_is_pending(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        await _run_full_conversation(test_db_pool, phone, emp_id, "অগ্রিম চাই")

        async with test_db_pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM fazle_payment_drafts WHERE employee_id=$1 ORDER BY id DESC LIMIT 1",
                emp_id,
            )
        assert status == "pending"


# ─────────────────────────────────────────────────────────────────────────────
# Test-6: Expiry → 24 Hours
# ─────────────────────────────────────────────────────────────────────────────
class Test6Expiry24Hours:
    async def test_draft_expires_in_24_hours(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        await _run_full_conversation(test_db_pool, phone, emp_id, "অগ্রিম চাই")

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT created_at, expires_at
                   FROM fazle_payment_drafts
                   WHERE employee_id=$1 ORDER BY id DESC LIMIT 1""",
                emp_id,
            )
        assert row is not None
        delta = row["expires_at"] - row["created_at"]
        # Should be approximately 24 hours (allow small tolerance)
        hours = delta.total_seconds() / 3600
        assert 23.9 <= hours <= 24.1


# ─────────────────────────────────────────────────────────────────────────────
# Test-7: Admin Draft → WhatsApp Template
# ─────────────────────────────────────────────────────────────────────────────
class Test7AdminDraftTemplate:
    async def test_admin_message_has_required_fields(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        _, admin_note = await _run_full_conversation(
            test_db_pool, phone, emp_id, "অগ্রিম চাই"
        )

        assert admin_note is not None
        text = admin_note["text"]
        # Template must contain: Employee, Reason, Amount, Payout, Verification, Commands
        assert seed_employee["employee_name"] in text
        assert "অগ্রিম" in text  # reason
        assert "৳" in text  # amount
        assert "01712345678" in text  # payout
        assert "যাচাই" in text  # verification summary
        assert "APPROVED" in text
        assert "EDIT" in text
        assert "REJECT" in text


# ─────────────────────────────────────────────────────────────────────────────
# Test-8: No Transaction → Transaction count unchanged
# ─────────────────────────────────────────────────────────────────────────────
class Test8NoTransaction:
    async def test_no_cash_transaction_created(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # Count before
        async with test_db_pool.acquire() as conn:
            before = await conn.fetchval("SELECT COUNT(*) FROM wbom_cash_transactions")

        await _run_full_conversation(test_db_pool, phone, emp_id, "অগ্রিম চাই")

        # Count after
        async with test_db_pool.acquire() as conn:
            after = await conn.fetchval("SELECT COUNT(*) FROM wbom_cash_transactions")

        assert before == after  # unchanged


# ─────────────────────────────────────────────────────────────────────────────
# Test-9: No Ledger → Ledger count unchanged
# ─────────────────────────────────────────────────────────────────────────────
class Test9NoLedger:
    async def test_no_ledger_entry_created(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # Check fazle_payment_drafts count before (drafts ARE created, but no ledger)
        async with test_db_pool.acquire() as conn:
            txn_before = await conn.fetchval("SELECT COUNT(*) FROM wbom_cash_transactions")

        await _run_full_conversation(test_db_pool, phone, emp_id, "অগ্রিম চাই")

        async with test_db_pool.acquire() as conn:
            txn_after = await conn.fetchval("SELECT COUNT(*) FROM wbom_cash_transactions")
            # Draft should exist but with status pending (not 'sent' which would mean finalized)
            draft_status = await conn.fetchval(
                "SELECT status FROM fazle_payment_drafts WHERE employee_id=$1 ORDER BY id DESC LIMIT 1",
                emp_id,
            )

        assert txn_before == txn_after  # no ledger/transaction writes
        assert draft_status == "pending"  # draft not finalized


# ─────────────────────────────────────────────────────────────────────────────
# Test-10: WhatsApp Regression → Admin ↔ Accountant Flow unchanged
# ─────────────────────────────────────────────────────────────────────────────
class Test10WhatsAppRegression:
    async def test_admin_accountant_flow_not_affected(self, test_db_pool, seed_employee):
        """The existing admin command flow (PAID/ADVANCE) must still work."""
        import app.database as db_module
        db_module._pool = test_db_pool

        # The employee_conversation module does NOT import or modify
        # admin_commands, payment_workflow.finalize_payment, or the
        # accountant forward logic.  Verify the imports are intact.
        from modules import admin_commands  # noqa: F401
        from modules.payment_workflow import finalize_payment  # noqa: F401

        # Verify employee_conversation does NOT call protected functions.
        # Use AST to check actual imports/calls (not docstring mentions).
        import modules.employee_conversation as ec_mod
        import ast
        with open(ec_mod.__file__) as f:
            tree = ast.parse(f.read())
        imports = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.add(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.add(func.attr)
        assert "create_transaction" not in calls
        assert "_upsert_ledger" not in calls
        assert "finalize_payment" not in calls
        assert "modules.fazle_payroll_engine.accounting" not in imports
        assert "modules.payment_workflow" not in imports


# ─────────────────────────────────────────────────────────────────────────────
# Test-11: Knowledge Base Used → Evidence
# ─────────────────────────────────────────────────────────────────────────────
class Test11KnowledgeBaseUsed:
    async def test_kb_lookup_returns_policy(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        # Seed KB data
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO fazle_knowledge_base (category, key, trigger_keywords, reply_text)
                   VALUES ('employee_policy', 'advance_policy',
                           ARRAY['অগ্রিম','advance policy'], 'অগ্রিম নীতি: সর্বোচ্চ বেতনের ৫০%')
                   ON CONFLICT DO NOTHING"""
            )

        from modules.employee_conversation import kb_lookup_employee_policy

        reply = await kb_lookup_employee_policy("অগ্রিম নীতি কি?")
        assert reply is not None
        assert "অগ্রিম" in reply

    async def test_kb_inactive_employee_reply(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import kb_inactive_employee_reply

        reply = await kb_inactive_employee_reply()
        assert reply
        assert "সক্রিয় নয়" in reply or "নয়" in reply


# ─────────────────────────────────────────────────────────────────────────────
# Test-12: Conversation Summary → Saved in Draft
# ─────────────────────────────────────────────────────────────────────────────
class Test12ConversationSummarySaved:
    async def test_conversation_summary_in_draft(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        await _run_full_conversation(test_db_pool, phone, emp_id, "অগ্রিম চাই")

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT conversation_summary, verification_summary, source_message,
                          draft_created_by, purpose, payout_mobile
                   FROM fazle_payment_drafts
                   WHERE employee_id=$1 ORDER BY id DESC LIMIT 1""",
                emp_id,
            )

        assert row is not None
        # conversation_summary must be saved
        conv = row["conversation_summary"]
        if isinstance(conv, str):
            conv = json.loads(conv)
        assert conv is not None
        assert conv.get("purpose") == "advance"
        assert conv.get("amount") == 2000.0
        assert conv.get("turn_count", 0) >= 3  # at least trigger+amount+payout+confirm

        # verification_summary must be saved
        ver = row["verification_summary"]
        if isinstance(ver, str):
            ver = json.loads(ver)
        assert ver is not None
        assert ver.get("verification_complete") is True

        # source_message saved
        assert row["source_message"]
        # draft_created_by
        assert row["draft_created_by"] == "ai_conversation"
        # purpose + payout_mobile
        assert row["purpose"] == "advance"
        assert row["payout_mobile"] == "01712345678"


# ─────────────────────────────────────────────────────────────────────────────
# Regression Tests
# ─────────────────────────────────────────────────────────────────────────────
class TestRegressionProtectedComponents:
    """Verify protected functions are NOT called by employee_conversation.

    These tests check for actual import statements and function CALLS,
    not docstring mentions of the protected component names.
    """

    def _source_lines(self):
        """Return (imports, calls) parsed via AST — skips docstrings/comments."""
        import modules.employee_conversation as ec_mod
        import ast
        with open(ec_mod.__file__) as f:
            tree = ast.parse(f.read())
        imports = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    calls.add(func.id)
                elif isinstance(func, ast.Attribute):
                    calls.add(func.attr)
        return imports, calls

    def test_no_create_transaction_import(self):
        imports, calls = self._source_lines()
        assert "modules.fazle_payroll_engine.accounting" not in imports
        assert "create_transaction" not in calls

    def test_no_upsert_ledger_import(self):
        imports, calls = self._source_lines()
        assert "_upsert_ledger" not in calls

    def test_no_accounting_worker_import(self):
        imports, calls = self._source_lines()
        assert "accounting_worker" not in calls
        assert "modules.fazle_payroll_engine.workers" not in imports

    def test_no_parse_message_import(self):
        imports, calls = self._source_lines()
        assert "modules.fazle_payroll_engine.parser" not in imports
        assert "parse_message" not in calls

    def test_no_wbom_cash_transactions_write(self):
        """employee_conversation must never INSERT into wbom_cash_transactions."""
        import modules.employee_conversation as ec_mod
        with open(ec_mod.__file__) as f:
            source = f.read()
        assert "INSERT INTO wbom_cash_transactions" not in source
        assert "UPDATE wbom_cash_transactions" not in source

    def test_no_finalize_payment_call(self):
        imports, calls = self._source_lines()
        assert "finalize_payment" not in calls
        assert "modules.payment_workflow" not in imports

    async def test_employee_balance_unchanged(self, test_db_pool, seed_employee):
        """Employee balance (basic_salary) must not change after draft creation."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            salary_before = await conn.fetchval(
                "SELECT basic_salary FROM wbom_employees WHERE employee_id=$1",
                emp_id,
            )

        await _run_full_conversation(test_db_pool, phone, emp_id, "অগ্রিম চাই")

        async with test_db_pool.acquire() as conn:
            salary_after = await conn.fetchval(
                "SELECT basic_salary FROM wbom_employees WHERE employee_id=$1",
                emp_id,
            )

        assert salary_before == salary_after

    async def test_payroll_unchanged(self, test_db_pool, seed_employee):
        """No payroll run items created."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import handle_employee_payment_request

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            before = await conn.fetchval("SELECT COUNT(*) FROM wbom_payroll_runs")

        await _run_full_conversation(test_db_pool, phone, emp_id, "বেতন চাই")

        async with test_db_pool.acquire() as conn:
            after = await conn.fetchval("SELECT COUNT(*) FROM wbom_payroll_runs")

        assert before == after


# ─────────────────────────────────────────────────────────────────────────────
# Trigger Detection Tests (STEP 1)
# ─────────────────────────────────────────────────────────────────────────────
class TestTriggerDetection:
    @pytest.mark.parametrize("text,expected", [
        ("অগ্রিম চাই", "advance"),
        ("advance দরকার", "advance"),
        ("টাকা দরকার", "advance"),
        ("বেতন চাই", "salary"),
        ("খাবারের বিল", "food_bill"),
        ("ভাড়া লাগবে", "conveyance"),
        ("অসুস্থ", "emergency"),
        ("হাসপাতাল", "emergency"),
        ("doctor", "emergency"),
    ])
    def test_triggers_detected(self, text, expected):
        from modules.employee_conversation import detect_payment_request_trigger
        assert detect_payment_request_trigger(text) == expected

    @pytest.mark.parametrize("text", [
        "হ্যালো",
        "চাকরি চাই",
        "job",
        "আস্সালামুয়ালাইকুম",
        "ঠিকানা দেন",
    ])
    def test_non_triggers_rejected(self, text):
        from modules.employee_conversation import detect_payment_request_trigger
        assert detect_payment_request_trigger(text) is None


# ─────────────────────────────────────────────────────────────────────────────
# Identity Resolution Tests (STEP 2)
# ─────────────────────────────────────────────────────────────────────────────
class TestIdentityResolution:
    async def test_resolve_by_employee_id(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import resolve_employee_identity

        identity = await resolve_employee_identity(
            "0000000000", employee_id=seed_employee["employee_id"]
        )
        assert identity["employee_id"] == seed_employee["employee_id"]
        assert identity["resolution"] == "employee_id"
        assert identity["verified"] is True

    async def test_resolve_by_registered_mobile(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import resolve_employee_identity

        identity = await resolve_employee_identity(seed_employee["employee_mobile"])
        assert identity["employee_id"] == seed_employee["employee_id"]
        assert identity["resolution"] == "registered_mobile"
        assert identity["verified"] is True

    async def test_resolve_unknown(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.employee_conversation import resolve_employee_identity

        identity = await resolve_employee_identity("8801999999999")
        assert identity["employee_id"] is None
        assert identity["resolution"] == "unknown"
        assert identity["verified"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Draft Validation Tests (STEP 7)
# ─────────────────────────────────────────────────────────────────────────────
class TestDraftValidation:
    def test_valid_draft_passes(self):
        from modules.employee_conversation import validate_draft

        identity = {
            "verified": True,
            "status": "Active",
            "resolution": "employee_id",
        }
        ctx = {
            "employee_id": 1,
            "employee_name": "Test",
            "employee_mobile": "01811111111",
            "purpose": "advance",
            "amount": 2000,
            "payout_mobile": "01712345678",
            "payment_method": "bkash",
        }
        ok, errors = validate_draft(identity, ctx)
        assert ok is True
        assert errors == []

    def test_inactive_employee_fails(self):
        from modules.employee_conversation import validate_draft

        identity = {"verified": True, "status": "Inactive", "resolution": "employee_id"}
        ctx = {
            "employee_id": 1,
            "employee_name": "Test",
            "employee_mobile": "01811111111",
            "purpose": "advance",
            "amount": 2000,
            "payout_mobile": "01712345678",
            "payment_method": "bkash",
        }
        ok, errors = validate_draft(identity, ctx)
        assert ok is False
        assert "employee_not_active" in errors

    def test_missing_amount_fails(self):
        from modules.employee_conversation import validate_draft

        identity = {"verified": True, "status": "Active", "resolution": "employee_id"}
        ctx = {
            "employee_id": 1,
            "employee_name": "Test",
            "employee_mobile": "01811111111",
            "purpose": "advance",
            "amount": None,
            "payout_mobile": "01712345678",
            "payment_method": "bkash",
        }
        ok, errors = validate_draft(identity, ctx)
        assert ok is False
        assert "amount_missing" in errors


# ─────────────────────────────────────────────────────────────────────────────
# Helper: run a full conversation to draft creation
# ─────────────────────────────────────────────────────────────────────────────
async def _run_full_conversation(
    test_db_pool, phone: str, emp_id: int, trigger_text: str = "অগ্রিম চাই"
):
    """Run a complete conversation flow and return the final (reply, admin_note)."""
    import app.database as db_module
    db_module._pool = test_db_pool

    from modules.employee_conversation import handle_employee_payment_request

    await handle_employee_payment_request(phone, trigger_text, "bridge1", emp_id)
    await handle_employee_payment_request(phone, "২০০০", "bridge1", emp_id)
    await handle_employee_payment_request(phone, "বিকাশ 01712345678", "bridge1", emp_id)
    return await handle_employee_payment_request(phone, "confirm", "bridge1", emp_id)