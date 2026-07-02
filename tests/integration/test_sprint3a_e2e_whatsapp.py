"""
Sprint-3A End-to-End WhatsApp Conversation Test
================================================

Simulates a REAL WhatsApp conversation flow through the message_router's
process_message() — the same function the WhatsApp webhook calls.

Flow:
    1. Employee sends "অগ্রিম চাই" via WhatsApp
    2. Router detects trigger → starts AI conversation
    3. Employee sends amount "২০০০"
    4. Employee sends payout "বিকাশ 01712345678"
    5. Employee confirms "confirm"
    6. Draft created → admin notification generated
    7. Verify: draft in DB, status=pending, 24h expiry, no transaction, no ledger

This test exercises the FULL production path:
    WhatsApp message → process_message() → employee_conversation → draft
"""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone

pytestmark = pytest.mark.integration


class TestE2EWhatsAppConversation:
    """End-to-end test: real WhatsApp message flow through process_message()."""

    async def test_full_whatsapp_conversation_to_draft(
        self, test_db_pool, seed_employee
    ):
        """
        E2E: Employee sends advance request via WhatsApp → full conversation
        → draft created → admin notified → NO transaction, NO ledger.
        """
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.message_router import process_message

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # ── Snapshot before ──────────────────────────────────────────────────
        async with test_db_pool.acquire() as conn:
            txn_before = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_cash_transactions"
            )
            draft_before = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_payment_drafts"
            )

        # ── Step 1: Employee sends advance request via WhatsApp ───────────────
        reply1, admin1 = await process_message(phone, "অগ্রিম চাই", "bridge1")

        # Conversation should have started — reply is non-empty
        assert reply1, "Expected non-empty reply after trigger"
        assert admin1 is None, "No admin notification at conversation start"

        # ── Step 2: Employee sends amount ────────────────────────────────────
        reply2, admin2 = await process_message(phone, "২০০০", "bridge1")
        assert reply2, "Expected reply after amount"
        assert admin2 is None, "No admin notification at amount step"

        # ── Step 3: Employee sends payout info ───────────────────────────────
        reply3, admin3 = await process_message(phone, "বিকাশ 01712345678", "bridge1")
        assert reply3, "Expected reply after payout"
        # At payout step, we may get confirmation prompt (no draft yet)
        assert admin3 is None, "No admin notification until confirmation"

        # ── Step 4: Employee confirms ────────────────────────────────────────
        reply4, admin4 = await process_message(phone, "confirm", "bridge1")

        # ── Verify: Draft created + admin notified ───────────────────────────
        assert reply4, "Expected reply after confirmation"
        assert admin4 is not None, "Expected admin notification after draft creation"
        assert "draft_id" in admin4, "Admin notification must contain draft_id"

        draft_id = admin4["draft_id"]

        # ── Verify: Draft in database ────────────────────────────────────────
        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_payment_drafts WHERE id = $1",
                draft_id,
            )
            txn_after = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_cash_transactions"
            )
            draft_after = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_payment_drafts"
            )

        # Draft exists with correct fields
        assert draft is not None, "Draft must exist in DB"
        assert draft["status"] == "pending", "Draft status must be 'pending'"
        assert draft["draft_type"] == "advance", "Draft type must be 'advance'"
        assert float(draft["expected_amount"]) == 2000.0, "Amount must be 2000"
        assert draft["employee_id"] == emp_id, "Employee ID must match"
        assert draft["payout_mobile"] == "01712345678", "Payout mobile must match"
        assert draft["payment_method"] == "bkash", "Payment method must be bkash"
        assert draft["purpose"] == "advance", "Purpose must be advance"
        assert draft["draft_created_by"] == "ai_conversation", "Created by AI"

        # ── Verify: 24-hour expiry ───────────────────────────────────────────
        delta = draft["expires_at"] - draft["created_at"]
        hours = delta.total_seconds() / 3600
        assert 23.9 <= hours <= 24.1, f"Expiry must be ~24h, got {hours:.1f}h"

        # ── Verify: Conversation summary saved ──────────────────────────────
        conv = draft["conversation_summary"]
        if isinstance(conv, str):
            conv = json.loads(conv)
        assert conv is not None, "Conversation summary must be saved"
        assert conv["purpose"] == "advance"
        assert conv["amount"] == 2000.0
        assert conv["turn_count"] >= 3, "Must have at least 3 conversation turns"

        # ── Verify: Verification summary saved ───────────────────────────────
        ver = draft["verification_summary"]
        if isinstance(ver, str):
            ver = json.loads(ver)
        assert ver is not None, "Verification summary must be saved"
        assert ver["verification_complete"] is True
        assert ver["employee_status"] == "Active"

        # ── Verify: source_message saved ─────────────────────────────────────
        assert draft["source_message"], "Source message must be saved"

        # ── Verify: Admin WhatsApp template ──────────────────────────────────
        admin_text = admin4["text"]
        assert seed_employee["employee_name"] in admin_text, "Admin msg must have employee name"
        assert "অগ্রিম" in admin_text, "Admin msg must have reason"
        assert "৳" in admin_text, "Admin msg must have amount"
        assert "01712345678" in admin_text, "Admin msg must have payout"
        assert "APPROVED" in admin_text, "Admin msg must have APPROVED command"
        assert "REJECT" in admin_text, "Admin msg must have REJECT command"

        # ── Verify: NO financial transaction ─────────────────────────────────
        assert txn_before == txn_after, (
            f"Transaction count must be unchanged: before={txn_before}, after={txn_after}"
        )

        # ── Verify: Draft count increased by exactly 1 ───────────────────────
        assert draft_after == draft_before + 1, (
            f"Draft count must increase by 1: before={draft_before}, after={draft_after}"
        )

    async def test_e2e_inactive_employee_kb_reply(
        self, test_db_pool, seed_employee
    ):
        """E2E: Inactive employee sends payment request → KB reply, no draft."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.message_router import process_message

        phone = seed_employee["employee_mobile"]

        # Mark employee inactive
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_employees SET status='Inactive' WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
            draft_before = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_payment_drafts"
            )

        reply, admin_note = await process_message(phone, "অগ্রিম চাই", "bridge1")

        # Should get a KB reply about inactive status
        assert reply, "Expected KB reply for inactive employee"
        assert admin_note is None, "No admin notification for inactive employee"

        # No draft created
        async with test_db_pool.acquire() as conn:
            draft_after = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_payment_drafts"
            )
        assert draft_before == draft_after, "No draft for inactive employee"

    async def test_e2e_salary_request_conversation(
        self, test_db_pool, seed_employee
    ):
        """E2E: Salary request triggers conversation and creates draft."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.message_router import process_message

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # Full salary conversation
        r1, _ = await process_message(phone, "বেতন চাই", "bridge1")
        assert r1, "Salary trigger should start conversation"

        r2, _ = await process_message(phone, "৫০০০", "bridge1")
        assert r2, "Amount step should reply"

        r3, _ = await process_message(phone, "নগদ 01812345678", "bridge1")
        assert r3, "Payout step should reply"

        r4, admin = await process_message(phone, "confirm", "bridge1")
        assert admin is not None, "Draft should be created for salary request"

        # Verify draft
        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT draft_type, purpose, expected_amount, payment_method "
                "FROM fazle_payment_drafts WHERE employee_id=$1 ORDER BY id DESC LIMIT 1",
                emp_id,
            )
        assert draft is not None
        assert draft["draft_type"] == "salary"
        assert draft["purpose"] == "salary"
        assert float(draft["expected_amount"]) == 5000.0
        assert draft["payment_method"] == "nagad"

    async def test_e2e_no_transaction_created(
        self, test_db_pool, seed_employee
    ):
        """E2E: After full conversation, NO wbom_cash_transactions entry exists."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.message_router import process_message

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # Run full conversation
        await process_message(phone, "অগ্রিম চাই", "bridge1")
        await process_message(phone, "৩০০০", "bridge1")
        await process_message(phone, "বিকাশ 01712345678", "bridge1")
        _, admin = await process_message(phone, "confirm", "bridge1")

        assert admin is not None, "Draft must be created"

        # Verify NO transaction for this employee
        async with test_db_pool.acquire() as conn:
            txn_count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_cash_transactions WHERE employee_id=$1",
                emp_id,
            )
        assert txn_count == 0, "No transaction must exist after Sprint-3A draft"

    async def test_e2e_employee_balance_unchanged(
        self, test_db_pool, seed_employee
    ):
        """E2E: Employee basic_salary unchanged after draft creation."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.message_router import process_message

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            salary_before = await conn.fetchval(
                "SELECT basic_salary FROM wbom_employees WHERE employee_id=$1",
                emp_id,
            )

        # Full conversation
        await process_message(phone, "অগ্রিম চাই", "bridge1")
        await process_message(phone, "১০০০", "bridge1")
        await process_message(phone, "cash", "bridge1")
        await process_message(phone, "confirm", "bridge1")

        async with test_db_pool.acquire() as conn:
            salary_after = await conn.fetchval(
                "SELECT basic_salary FROM wbom_employees WHERE employee_id=$1",
                emp_id,
            )

        assert salary_before == salary_after, "Employee balance must be unchanged"

    async def test_e2e_conversation_remembers_previous_answers(
        self, test_db_pool, seed_employee
    ):
        """E2E: Conversation doesn't repeat questions — remembers context."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.message_router import process_message

        phone = seed_employee["employee_mobile"]

        # Start conversation
        r1, _ = await process_message(phone, "অগ্রিম চাই", "bridge1")
        assert "পরিমাণ" in r1 or "টাকা" in r1, "Should ask for amount"

        # Send amount
        r2, _ = await process_message(phone, "২০০০", "bridge1")
        # Should NOT ask for amount again — should ask for payout
        assert "পেআউট" in r2 or "বিকাশ" in r2 or "নগদ" in r2, (
            "Should ask for payout, not repeat amount question"
        )

    async def test_e2e_incomplete_verification_no_draft(
        self, test_db_pool, seed_employee
    ):
        """E2E: If verification is incomplete, no draft is created."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.message_router import process_message

        phone = seed_employee["employee_mobile"]
        emp_id = seed_employee["employee_id"]

        # Start conversation but don't complete it
        await process_message(phone, "অগ্রিম চাই", "bridge1")
        await process_message(phone, "২০০০", "bridge1")
        # Don't send payout or confirm

        # Verify no draft
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_payment_drafts WHERE employee_id=$1",
                emp_id,
            )
        assert count == 0, "No draft should exist for incomplete verification"