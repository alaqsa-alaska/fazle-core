"""
Fazle Core — Sprint-3B: Draft Approval → Accountant Forward → Canonical Transaction → Ledger
==============================================================================================

This module implements the Financial Approval Layer that sits between the
Sprint-3A draft (status='pending') and the canonical accounting pipeline.

Flow:
    Draft (pending, from Sprint-3A)
        → Admin Decision (APPROVED / EDIT / REJECT)
        → [APPROVED] Draft Lock
            → Accountant Message Generated
            → Canonical create_transaction() called
            → _upsert_ledger() called (inside create_transaction)
            → Audit logged
            → Draft status='completed'
        → [REJECT] Draft status='rejected', no transaction, no ledger
        → [EDIT] Version increment, before/after state saved, then re-approve

HARD RULES (Sprint-3B):
    • Only an Approved Draft may create a transaction.
    • One Draft = One Transaction (idempotency enforced).
    • create_transaction() and _upsert_ledger() are called EXACTLY as-is —
      no parallel logic, no direct ledger writes.
    • The accountant message format is 100% compatible with the existing
      parser (parse_message) so the WhatsApp Admin ↔ Accountant flow is
      unchanged.
    • Expired drafts cannot be approved.
    • Duplicate approvals are rejected safely.

Protected components (called, NOT modified):
    • create_transaction()        — called from accounting.py
    • _upsert_ledger()            — called from inside create_transaction()
    • accounting_worker()         — untouched
    • parse_message()             — untouched
    • WhatsApp Admin ↔ Accountant Flow — untouched

Success Metric: Approved Draft → Single Canonical Transaction → Correct Ledger → Complete Audit
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.database import fetch_one, fetch_val, execute, db_conn, get_pool

log = logging.getLogger("fazle.draft_approval")

# ── Constants ──────────────────────────────────────────────────────────────────

# Acceptable states for a draft to be acted upon.
DRAFT_STATE_PENDING = "pending"

# Terminal / non-actionable states.
DRAFT_NON_PENDING_STATES = frozenset({
    "approved", "completed", "rejected", "expired", "duplicate", "sent",
})

# Supported admin commands.
CMD_APPROVED = "APPROVED"
CMD_EDIT = "EDIT"
CMD_REJECT = "REJECT"

# Audit event names.
EVT_DRAFT_CREATED = "created"
EVT_DRAFT_EDITED = "edited"
EVT_DRAFT_APPROVED = "approved"
EVT_DRAFT_REJECTED = "rejected"
EVT_DRAFT_EXPIRED = "expired"
EVT_TRANSACTION_CREATED = "transaction_created"
EVT_LEDGER_UPDATED = "ledger_updated"
EVT_ACCOUNTANT_FORWARDED = "accountant_forwarded"


# ── STEP 1 & 2: Draft Retrieval + State Validation ────────────────────────────

async def retrieve_draft(draft_id: int) -> Optional[dict]:
    """
    STEP 1 — Retrieve a draft by ID.

    Returns the draft row as a dict, or None if not found.
    """
    row = await fetch_one(
        """SELECT id, draft_type, employee_id, employee_name, employee_mobile,
                  escort_program_id, expected_amount, approved_amount,
                  payment_method, payout_mobile, purpose, status, source,
                  draft_text, accountant_msg, admin_phone,
                  verification_summary, source_message, conversation_summary,
                  draft_created_by, conversation_id, expires_at,
                  transaction_id, txn_ref, rejected_reason, reviewed_by,
                  reviewed_at, version, before_state, after_state, editor,
                  completed_at, created_at, updated_at
           FROM fazle_payment_drafts WHERE id = $1""",
        draft_id,
    )
    return dict(row) if row else None


def is_draft_pending(draft: dict) -> bool:
    """
    STEP 2 — Validate that the draft is in an acceptable (pending) state.

    Returns True only if status == 'pending'.
    Any other state (approved, completed, rejected, expired, duplicate, sent)
    blocks financial action.
    """
    status = (draft.get("status") or "").strip().lower()
    return status == DRAFT_STATE_PENDING


def is_draft_expired(draft: dict) -> bool:
    """Check whether the draft's 24-hour TTL has elapsed."""
    expires_at = draft.get("expires_at")
    if not expires_at:
        return False
    now = datetime.now(timezone.utc)
    if hasattr(expires_at, "tzinfo") and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now


def is_draft_already_processed(draft: dict) -> bool:
    """Check whether the draft already has a transaction (idempotency guard)."""
    return draft.get("transaction_id") is not None or draft.get("txn_ref") is not None


# ── STEP 12: Draft Expiry ─────────────────────────────────────────────────────

async def expire_draft(draft_id: int) -> bool:
    """
    Mark a draft as expired (24-hour TTL elapsed).

    An expired draft can NEVER be approved.
    Returns True if the draft was expired, False if it was already terminal.
    """
    result = await execute(
        """UPDATE fazle_payment_drafts
           SET status = 'expired',
               reviewed_at = COALESCE(reviewed_at, NOW()),
               updated_at = NOW()
           WHERE id = $1 AND status = 'pending'""",
        draft_id,
    )
    if result and "UPDATE 1" in (result or ""):
        await _audit(draft_id, EVT_DRAFT_EXPIRED, None, {"status": "expired"},
                     performed_by="system", reason="ttl_24h_elapsed")
        log.info("[draft_approval] draft #%d expired (TTL)", draft_id)
        return True
    return False


# ── STEP 14: Audit ────────────────────────────────────────────────────────────

async def _audit(
    draft_id: int,
    event: str,
    before_state: Optional[dict],
    after_state: Optional[dict],
    *,
    performed_by: str = "admin",
    reason: Optional[str] = None,
) -> None:
    """Write a draft lifecycle audit row."""
    try:
        await execute(
            """INSERT INTO fazle_draft_audit_log
                   (draft_id, event, before_state, after_state, performed_by, reason)
               VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)""",
            draft_id,
            event,
            json.dumps(before_state, default=str) if before_state else None,
            json.dumps(after_state, default=str) if after_state else None,
            performed_by,
            reason,
        )
    except Exception as e:
        log.error("[draft_approval] audit write failed for draft #%d event=%s: %s",
                  draft_id, event, e)


# ── STEP 6: Accountant Forward ────────────────────────────────────────────────

def build_accountant_message(draft: dict, amount: float, method: str) -> str:
    """
    STEP 6 — Build the accountant forward message.

    Format MUST be 100% compatible with the existing parser (parse_message)
    and the current WhatsApp Admin ↔ Accountant flow.

    Canonical format (from parser.py observed patterns):
        ID: <payout_mobile> <employee_name> <payout_mobile>(<method_code>) <amount>/-

    Method codes (parser-compatible):
        bkash → B
        nagad → N
        cash  → cash
        rocket → R
    """
    method_map = {
        "bkash": "B",
        "nagad": "N",
        "cash": "cash",
        "rocket": "R",
        "bank": "bank",
    }
    method_code = method_map.get(method.lower(), method.upper())

    payout_phone = draft.get("payout_mobile") or draft.get("employee_mobile") or ""
    # Normalize to 01XXXXXXXXX for the parser
    if payout_phone.startswith("880"):
        payout_phone = "0" + payout_phone[3:]
    elif payout_phone.startswith("+880"):
        payout_phone = "0" + payout_phone[4:]

    employee_name = draft.get("employee_name") or "?"
    amount_int = int(round(amount))

    # Canonical single-line format that parse_message() understands.
    accountant_msg = (
        f"ID: {payout_phone} {employee_name} {payout_phone}({method_code}) {amount_int}/-"
    )
    return accountant_msg


# ── STEP 7 & 8: Canonical Transaction + Ledger ────────────────────────────────

async def _resolve_fpe_employee_id(wbom_employee_id: Optional[int],
                                    phone: Optional[str],
                                    name: Optional[str]) -> Optional[int]:
    """
    Resolve the wbom_employees.employee_id to fpe_employees.id.

    The canonical create_transaction() expects an fpe_employees.id.
    We look up by primary_phone first, then by name.
    If no fpe_employees row exists, we use match_or_create_employee.
    """
    if wbom_employee_id is None and not phone and not name:
        return None

    # Try phone match first
    if phone:
        # Normalize phone
        p = phone.strip()
        if p.startswith("+880"):
            p = "0" + p[4:]
        elif p.startswith("880"):
            p = "0" + p[3:]
        row = await fetch_one(
            "SELECT id FROM fpe_employees WHERE primary_phone = $1 AND status = 'active'",
            p,
        )
        if row:
            return row["id"]
        # Try employee_id_phone
        row = await fetch_one(
            "SELECT id FROM fpe_employees WHERE employee_id_phone = $1 AND status = 'active'",
            p,
        )
        if row:
            return row["id"]

    # Try name match
    if name:
        from modules.fazle_payroll_engine.normalizer import normalize_name
        name_norm = normalize_name(name)
        if name_norm:
            row = await fetch_one(
                "SELECT id FROM fpe_employees WHERE name_normalized = $1 AND status = 'active'",
                name_norm,
            )
            if row:
                return row["id"]

    # Fall back to match_or_create_employee
    try:
        from modules.fazle_payroll_engine.employee import match_or_create_employee
        result = await match_or_create_employee(
            name_raw=name,
            payout_phone=phone,
            employee_id_phone=phone,
        )
        if result:
            return result.employee_id
    except Exception as e:
        log.warning("[draft_approval] employee match failed: %s", e)

    return None


async def create_canonical_transaction(
    draft: dict,
    amount: float,
    method: str,
    admin_phone: str,
) -> dict:
    """
    STEP 7 & 8 — Call the canonical create_transaction().

    This is the ONLY path to a financial transaction.
    create_transaction() internally calls _upsert_ledger().

    CONSTITUTIONAL RULE (Business Constitution §1.3):
        The Draft is the Source of Truth. Financial data is read FROM the Draft
        and passed TO create_transaction(). The accountant message is built
        AFTER the transaction as a NOTIFICATION ONLY — it is NEVER re-parsed
        to create a transaction.

    Returns:
        {
            "transaction_id": int,
            "txn_ref": str,
            "accountant_msg": str,
        }

    Idempotency:
        create_transaction() uses txn_ref = sha256(wa_message_id + employee_id
        + amount + period + method).  We pass a deterministic wa_message_id
        derived from the draft_id so that duplicate approvals produce the same
        txn_ref and the second call returns the existing transaction.
    """
    from modules.fazle_payroll_engine.accounting import create_transaction
    from modules.fazle_payroll_engine.models import (
        TransactionCreateRequest, PayoutMethod, TxnCategory,
        TransactionStatus, ApprovalStatus,
    )
    from modules.fazle_payroll_engine.payment_event import (
        payment_event_from_employee_draft, payment_event_to_request,
    )

    # Resolve FPE employee ID
    fpe_employee_id = await _resolve_fpe_employee_id(
        draft.get("employee_id"),
        draft.get("payout_mobile") or draft.get("employee_mobile"),
        draft.get("employee_name"),
    )
    if fpe_employee_id is None:
        raise ValueError(f"Could not resolve FPE employee for draft #{draft['id']}")

    # Map draft purpose → txn_category
    purpose = (draft.get("purpose") or draft.get("draft_type") or "advance").lower()
    category_map = {
        "advance": TxnCategory.advance,
        "salary": TxnCategory.salary,
        "food_bill": TxnCategory.deduction,
        "conveyance": TxnCategory.deduction,
        "emergency": TxnCategory.advance,
        "escort_payment": TxnCategory.salary,
    }
    txn_category = category_map.get(purpose, TxnCategory.advance)

    # Map method → PayoutMethod
    method_lower = (method or "cash").lower()
    payout_method = PayoutMethod(method_lower) if method_lower in PayoutMethod.__members__.values() else PayoutMethod.cash

    payout_phone = draft.get("payout_mobile") or draft.get("employee_mobile")
    # Normalize phone
    if payout_phone and payout_phone.startswith("+880"):
        payout_phone = "0" + payout_phone[4:]
    elif payout_phone and payout_phone.startswith("880"):
        payout_phone = "0" + payout_phone[3:]

    source_msg = draft.get("source_message") or draft.get("draft_text") or ""

    # C1B: build a PaymentEvent from the employee draft, then convert to request.
    # This ensures all canonical fields (source, source_message_id, etc.) are populated.
    event = payment_event_from_employee_draft(
        employee_id=fpe_employee_id,
        amount=Decimal(str(amount)),
        payout_method=payout_method,
        payout_phone=payout_phone,
        txn_date=date.today(),
        draft_id=draft["id"],
        submitted_by=f"admin:{admin_phone}",
        txn_category=txn_category,
        metadata={
            "draft_type": draft.get("draft_type"),
            "purpose": draft.get("purpose"),
            "escort_program_id": draft.get("escort_program_id"),
            "employee_name": draft.get("employee_name"),
            "payout_mobile": draft.get("payout_mobile"),
            "employee_mobile": draft.get("employee_mobile"),
        },
    )
    event.employee_name_raw = draft.get("employee_name")
    event.source_message_text = source_msg
    event.employee_id_phone = payout_phone
    event.employee_phone = payout_phone

    # Admin has approved this draft — mark transaction as final and approved
    # so that create_transaction() will update the employee ledger.
    event.transaction_status = TransactionStatus.final
    event.approval_status = ApprovalStatus.approved
    event.approved_by = f"admin:{admin_phone}"
    event.approved_at = datetime.now(timezone.utc)

    req = payment_event_to_request(event)
    req.created_by = f"admin:{admin_phone}"

    txn_row = await create_transaction(req)

    log.info(
        "[draft_approval] canonical transaction created: id=%d ref=%s emp=%s amount=%s",
        txn_row.id, txn_row.txn_ref[:16], fpe_employee_id, amount,
    )

    # Build accountant message
    accountant_msg = build_accountant_message(draft, amount, method)

    return {
        "transaction_id": txn_row.id,
        "txn_ref": txn_row.txn_ref,
        "accountant_msg": accountant_msg,
    }


# ── STEP 5: APPROVED Workflow ─────────────────────────────────────────────────

async def approve_draft(
    draft_id: int,
    amount: float,
    method: str,
    admin_phone: str,
) -> dict:
    """
    STEP 5 — Full APPROVED workflow.

    Steps:
        1. Retrieve draft (STEP 1)
        2. Validate pending state (STEP 2)
        3. Check expiry (STEP 12)
        4. Check idempotency — already processed? (STEP 13)
        5. Lock draft (status → 'approved')
        6. Build accountant message (STEP 6)
        7. Call canonical create_transaction() (STEP 7)
           → _upsert_ledger() called internally (STEP 8)
        8. Audit: transaction_created, ledger_updated, accountant_forwarded (STEP 14)
        9. Finalize draft: status='completed', save transaction_id, txn_ref (STEP 10)

    Returns:
        {
            "ok": True,
            "draft_id": int,
            "status": "completed",
            "transaction_id": int,
            "txn_ref": str,
            "accountant_msg": str,
            "employee_name": str,
            "amount": float,
            "method": str,
        }
        or
        { "ok": False, "error": str, "draft_id": int }
    """
    # STEP 1: Retrieve
    draft = await retrieve_draft(draft_id)
    if not draft:
        return {"ok": False, "error": f"Draft #{draft_id} not found", "draft_id": draft_id}

    before_state = _snapshot(draft)

    # STEP 2: Validate pending
    if not is_draft_pending(draft):
        return {
            "ok": False,
            "error": f"Draft #{draft_id} is not pending (status={draft.get('status')})",
            "draft_id": draft_id,
        }

    # STEP 12: Check expiry
    if is_draft_expired(draft):
        await expire_draft(draft_id)
        return {
            "ok": False,
            "error": f"Draft #{draft_id} has expired (24h TTL) — cannot approve",
            "draft_id": draft_id,
        }

    # STEP 13: Idempotency — already processed?
    if is_draft_already_processed(draft):
        return {
            "ok": False,
            "error": f"Draft #{draft_id} already has a transaction (id={draft.get('transaction_id')})",
            "draft_id": draft_id,
            "transaction_id": draft.get("transaction_id"),
            "txn_ref": draft.get("txn_ref"),
        }

    # STEP 5a: Lock draft (status → 'approved')
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Row-level lock to prevent concurrent approvals
            row = await conn.fetchrow(
                "SELECT id, status, transaction_id FROM fazle_payment_drafts "
                "WHERE id = $1 FOR UPDATE",
                draft_id,
            )
            if not row:
                return {"ok": False, "error": f"Draft #{draft_id} not found", "draft_id": draft_id}
            if row["status"] != "pending":
                return {
                    "ok": False,
                    "error": f"Draft #{draft_id} is not pending (status={row['status']})",
                    "draft_id": draft_id,
                }
            if row["transaction_id"] is not None:
                return {
                    "ok": False,
                    "error": f"Draft #{draft_id} already has a transaction (id={row['transaction_id']})",
                    "draft_id": draft_id,
                    "transaction_id": row["transaction_id"],
                }

            await conn.execute(
                """UPDATE fazle_payment_drafts
                   SET status = 'approved',
                       approved_amount = $1,
                       payment_method = $2,
                       reviewed_by = $3,
                       reviewed_at = NOW(),
                       updated_at = NOW()
                   WHERE id = $4""",
                amount, method, admin_phone, draft_id,
            )

    # STEP 6 & 7: Build accountant message + canonical transaction
    try:
        txn_result = await create_canonical_transaction(draft, amount, method, admin_phone)
    except Exception as e:
        log.error("[draft_approval] transaction creation failed for draft #%d: %s", draft_id, e)
        # Roll back to pending so admin can retry
        await execute(
            "UPDATE fazle_payment_drafts SET status='pending', updated_at=NOW() WHERE id=$1",
            draft_id,
        )
        await _audit(draft_id, EVT_DRAFT_APPROVED, before_state,
                     {"status": "pending", "error": str(e)},
                     performed_by=admin_phone, reason="transaction_creation_failed")
        return {"ok": False, "error": f"Transaction creation failed: {e}", "draft_id": draft_id}

    transaction_id = txn_result["transaction_id"]
    txn_ref = txn_result["txn_ref"]
    accountant_msg = txn_result["accountant_msg"]

    # STEP 14: Audit events
    after_state = {
        "status": "completed",
        "transaction_id": transaction_id,
        "txn_ref": txn_ref,
        "approved_amount": amount,
        "payment_method": method,
        "reviewed_by": admin_phone,
    }
    await _audit(draft_id, EVT_DRAFT_APPROVED, before_state, after_state,
                 performed_by=admin_phone)
    await _audit(draft_id, EVT_TRANSACTION_CREATED, None,
                 {"transaction_id": transaction_id, "txn_ref": txn_ref},
                 performed_by=admin_phone)
    await _audit(draft_id, EVT_LEDGER_UPDATED, None,
                 {"transaction_id": transaction_id, "txn_ref": txn_ref},
                 performed_by=admin_phone, reason="via_create_transaction")
    await _audit(draft_id, EVT_ACCOUNTANT_FORWARDED, None,
                 {"accountant_msg": accountant_msg},
                 performed_by=admin_phone)

    # STEP 10: Finalize draft — status='completed'
    await execute(
        """UPDATE fazle_payment_drafts
           SET status = 'completed',
               transaction_id = $1,
               txn_ref = $2,
               accountant_msg = $3,
               approved_amount = $4,
               payment_method = $5,
               completed_at = NOW(),
               updated_at = NOW()
           WHERE id = $6""",
        transaction_id, txn_ref, accountant_msg, amount, method, draft_id,
    )

    log.info(
        "[draft_approval] draft #%d completed: txn_id=%d ref=%s amount=%.2f method=%s",
        draft_id, transaction_id, txn_ref[:16], amount, method,
    )

    return {
        "ok": True,
        "draft_id": draft_id,
        "status": "completed",
        "transaction_id": transaction_id,
        "txn_ref": txn_ref,
        "accountant_msg": accountant_msg,
        "employee_name": draft.get("employee_name"),
        "amount": amount,
        "method": method,
    }


# ── STEP 4: EDIT Workflow ─────────────────────────────────────────────────────

async def edit_draft(
    draft_id: int,
    new_amount: Optional[float],
    new_method: Optional[str],
    new_payout_mobile: Optional[str],
    admin_phone: str,
    reason: Optional[str] = None,
) -> dict:
    """
    STEP 4 — EDIT workflow.

    Steps:
        1. Retrieve draft (STEP 1)
        2. Validate pending state (STEP 2)
        3. Save before_state (current snapshot)
        4. Apply edits (amount, method, payout_mobile)
        5. Increment version
        6. Save after_state
        7. Audit: edited event with before/after

    The old draft data is NEVER lost — before_state is preserved in JSONB.

    Returns:
        { "ok": True, "draft_id": int, "version": int, ... }
        or
        { "ok": False, "error": str, "draft_id": int }
    """
    draft = await retrieve_draft(draft_id)
    if not draft:
        return {"ok": False, "error": f"Draft #{draft_id} not found", "draft_id": draft_id}

    if not is_draft_pending(draft):
        return {
            "ok": False,
            "error": f"Draft #{draft_id} is not pending (status={draft.get('status')})",
            "draft_id": draft_id,
        }

    if is_draft_expired(draft):
        await expire_draft(draft_id)
        return {
            "ok": False,
            "error": f"Draft #{draft_id} has expired — cannot edit",
            "draft_id": draft_id,
        }

    before_state = _snapshot(draft)

    # Apply edits
    new_version = (draft.get("version") or 0) + 1
    new_amount_val = new_amount if new_amount is not None else draft.get("expected_amount")
    new_method_val = new_method or draft.get("payment_method")
    new_payout_val = new_payout_mobile or draft.get("payout_mobile")

    await execute(
        """UPDATE fazle_payment_drafts
           SET expected_amount = $1,
               payment_method = $2,
               payout_mobile = $3,
               version = $4,
               before_state = $5::jsonb,
               after_state = $6::jsonb,
               editor = $7,
               updated_at = NOW()
           WHERE id = $8""",
        new_amount_val,
        new_method_val,
        new_payout_val,
        new_version,
        json.dumps(before_state, default=str),
        json.dumps({
            "expected_amount": new_amount_val,
            "payment_method": new_method_val,
            "payout_mobile": new_payout_val,
            "version": new_version,
            "editor": admin_phone,
        }, default=str),
        admin_phone,
        draft_id,
    )

    after_state = {
        "expected_amount": new_amount_val,
        "payment_method": new_method_val,
        "payout_mobile": new_payout_val,
        "version": new_version,
        "editor": admin_phone,
        "reason": reason,
    }
    await _audit(draft_id, EVT_DRAFT_EDITED, before_state, after_state,
                 performed_by=admin_phone, reason=reason)

    log.info(
        "[draft_approval] draft #%d edited: v%d amount=%s method=%s by=%s",
        draft_id, new_version, new_amount_val, new_method_val, admin_phone,
    )

    return {
        "ok": True,
        "draft_id": draft_id,
        "version": new_version,
        "expected_amount": new_amount_val,
        "payment_method": new_method_val,
        "payout_mobile": new_payout_val,
    }


# ── STEP 11: Reject Workflow ───────────────────────────────────────────────────

async def reject_draft(
    draft_id: int,
    admin_phone: str,
    reason: Optional[str] = None,
) -> dict:
    """
    STEP 11 — Reject workflow.

    Steps:
        1. Retrieve draft (STEP 1)
        2. Validate pending state (STEP 2)
        3. Set status='rejected', save reason, reviewer, time
        4. Audit: rejected event

    NO transaction is created.
    NO ledger is updated.

    Returns:
        { "ok": True, "draft_id": int, "status": "rejected" }
        or
        { "ok": False, "error": str, "draft_id": int }
    """
    draft = await retrieve_draft(draft_id)
    if not draft:
        return {"ok": False, "error": f"Draft #{draft_id} not found", "draft_id": draft_id}

    if not is_draft_pending(draft):
        return {
            "ok": False,
            "error": f"Draft #{draft_id} is not pending (status={draft.get('status')})",
            "draft_id": draft_id,
        }

    before_state = _snapshot(draft)

    await execute(
        """UPDATE fazle_payment_drafts
           SET status = 'rejected',
               rejected_reason = $1,
               reviewed_by = $2,
               reviewed_at = NOW(),
               updated_at = NOW()
           WHERE id = $3""",
        reason or "admin_reject",
        admin_phone,
        draft_id,
    )

    after_state = {
        "status": "rejected",
        "rejected_reason": reason or "admin_reject",
        "reviewed_by": admin_phone,
    }
    await _audit(draft_id, EVT_DRAFT_REJECTED, before_state, after_state,
                 performed_by=admin_phone, reason=reason)

    log.info("[draft_approval] draft #%d rejected by %s: %s", draft_id, admin_phone, reason)

    return {
        "ok": True,
        "draft_id": draft_id,
        "status": "rejected",
        "reason": reason or "admin_reject",
    }


# ── STEP 3: Admin Command Processing ───────────────────────────────────────────

async def process_admin_decision(
    command: str,
    draft_id: int,
    admin_phone: str,
    *,
    amount: Optional[float] = None,
    method: Optional[str] = None,
    new_payout_mobile: Optional[str] = None,
    reason: Optional[str] = None,
) -> dict:
    """
    STEP 3 — Admin command dispatcher.

    Supported commands:
        APPROVED  → approve_draft()
        EDIT      → edit_draft()
        REJECT    → reject_draft()

    Unknown commands are rejected.

    Returns the result dict from the underlying workflow.
    """
    cmd = (command or "").strip().upper()

    if cmd == CMD_APPROVED:
        if amount is None:
            return {"ok": False, "error": "APPROVED requires <amount>", "draft_id": draft_id}
        if not method:
            return {"ok": False, "error": "APPROVED requires <method>", "draft_id": draft_id}
        return await approve_draft(draft_id, amount, method, admin_phone)

    if cmd == CMD_EDIT:
        return await edit_draft(
            draft_id, amount, method, new_payout_mobile, admin_phone, reason
        )

    if cmd == CMD_REJECT:
        return await reject_draft(draft_id, admin_phone, reason)

    return {"ok": False, "error": f"Unknown command: {command}", "draft_id": draft_id}


# ── STEP 9: Employee Balance (read-only, from ledger) ─────────────────────────

async def get_employee_balance(employee_id: int, period: Optional[str] = None) -> dict:
    """
    STEP 9 — Read employee balance from the existing ledger.

    All totals come from fpe_employee_ledger — NO manual calculation.

    Returns:
        {
            "employee_id": int,
            "period": str,
            "opening_balance": Decimal,
            "total_earned": Decimal,
            "total_paid": Decimal,
            "total_advance": Decimal,
            "closing_balance": Decimal,
            "txn_count": int,
        }
        or None if no ledger row exists.
    """
    if period is None:
        period = date.today().strftime("%Y-%m")

    row = await fetch_one(
        """SELECT employee_id, accounting_period, opening_balance,
                  total_earned, total_paid, total_advance, closing_balance,
                  txn_count, last_updated
           FROM fpe_employee_ledger
           WHERE employee_id = $1 AND accounting_period = $2""",
        employee_id, period,
    )
    if not row:
        return {
            "employee_id": employee_id,
            "period": period,
            "opening_balance": Decimal("0"),
            "total_earned": Decimal("0"),
            "total_paid": Decimal("0"),
            "total_advance": Decimal("0"),
            "closing_balance": Decimal("0"),
            "txn_count": 0,
        }
    return dict(row)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _snapshot(draft: dict) -> dict:
    """Create a JSON-serializable snapshot of the draft for audit."""
    snap = {}
    for k, v in draft.items():
        if isinstance(v, (datetime, date)):
            snap[k] = v.isoformat()
        elif isinstance(v, Decimal):
            snap[k] = str(v)
        else:
            snap[k] = v
    return snap


# ── Audit retrieval (for tests/reports) ───────────────────────────────────────

async def get_draft_audit_events(draft_id: int) -> list[dict]:
    """Return all audit events for a draft, ordered by time."""
    rows = await fetch_all(
        """SELECT id, draft_id, event, before_state, after_state,
                  performed_by, reason, created_at
           FROM fazle_draft_audit_log
           WHERE draft_id = $1
           ORDER BY created_at ASC""",
        draft_id,
    )
    return [dict(r) for r in rows]


async def fetch_all(sql: str, *args):
    """Local fetch_all wrapper (avoids importing at module top for test isolation)."""
    from app.database import fetch_all as _fa
    return await _fa(sql, *args)