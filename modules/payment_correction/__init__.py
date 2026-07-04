"""
Fazle Core — Payment Correction & Reversal (Batch 28)

Immutable-ledger approach:
- reverse_payment   : marks original transaction reversed; writes counter-transaction
- adjust_payment    : creates a linked correction draft; caller approves it via PAID command
- list_corrections  : recent correction log rows for dashboard/reporting

Admin WhatsApp commands (wired in modules.admin_commands):
  REVERSE <draft_id> <reason>
  ADJUST  <draft_id> <new_amount> <method> [reason]
"""

# =============================================================================
# MODULE STATUS: DORMANT
# Date audited: 2026-06-02
# External callers: 0 (grep confirmed — no import in app/, modules/, or service_runner.py)
# Functions defined: reverse_payment, adjust_payment, list_corrections
# Fully implemented but never invoked — the admin_commands REVERSE/ADJUST wiring
#   was never added. The module is safe to ignore during normal operation.
# DO NOT DELETE without explicit confirmation from Azim first.
# =============================================================================

from __future__ import annotations

import logging
from typing import Optional

from app.database import execute, fetch_one, fetch_val, fetch_all
from modules import observability as obs
from modules.fazle_payroll_engine.accounting import reverse_transaction

log = logging.getLogger("fazle.payment_correction")


# ── Reverse ────────────────────────────────────────────────────────────────────

async def reverse_payment(
    draft_id: int,
    admin_phone: str,
    reason: str = "",
) -> dict:
    """
    Reverse an approved payment.

    Steps:
    1. Validate draft exists and is in 'approved' or 'sent' status.
    2. Find the matching fpe_cash_transactions row via draft.transaction_id.
    3. Call FPE reverse_transaction() to create an immutable reversal row.
    4. Update fazle_payment_drafts to status='reversed', store correction metadata.
    5. Write fazle_payment_correction_log row.
    """
    draft = await fetch_one(
        "SELECT * FROM fazle_payment_drafts WHERE id = $1", draft_id
    )
    if not draft:
        return {"ok": False, "error": f"Payment draft #{draft_id} not found"}
    if draft["status"] not in ("approved", "sent"):
        return {
            "ok": False,
            "error": (
                f"Draft #{draft_id} has status '{draft['status']}'. "
                "Only 'approved' or 'sent' payments can be reversed."
            ),
        }
    if draft.get("correction_type") == "reversal":
        return {"ok": False, "error": f"Draft #{draft_id} is already a reversal — cannot double-reverse."}

    # Find original canonical transaction linked to this draft
    orig_tx_id: Optional[int] = draft.get("transaction_id")
    counter_tx_id: Optional[int] = None

    if orig_tx_id:
        try:
            rev_row = await reverse_transaction(
                txn_id=orig_tx_id,
                reason=reason or "reversed by admin",
                created_by=admin_phone,
            )
            counter_tx_id = rev_row.id
        except Exception as exc:
            log.error("[payment_correction] reverse_transaction failed for draft=%s txn=%s: %s", draft_id, orig_tx_id, exc)
            return {"ok": False, "error": f"Could not reverse canonical transaction {orig_tx_id}: {exc}"}

    # Update draft status
    await execute(
        """UPDATE fazle_payment_drafts
              SET status = 'reversed',
                  correction_type = 'reversal',
                  correction_note = $1,
                  corrected_by    = $2,
                  corrected_at    = NOW(),
                  updated_at      = NOW()
            WHERE id = $3""",
        (reason or "reversed by admin")[:500],
        admin_phone,
        draft_id,
    )

    # Audit log
    await execute(
        """INSERT INTO fazle_payment_correction_log
               (action, payment_draft_id, transaction_id, counter_tx_id,
                original_amount, correction_amount, method, note, performed_by)
           VALUES ('reversed', $1, $2, $3, $4, $5, $6, $7, $8)""",
        draft_id,
        orig_tx_id,
        counter_tx_id,
        float(draft.get("approved_amount") or draft.get("expected_amount") or 0),
        float(-(draft.get("approved_amount") or draft.get("expected_amount") or 0)),
        draft.get("payment_method") or "",
        (reason or "")[:500],
        admin_phone,
    )

    obs.inc("payment_correction_total", labels={"action": "reversed"})
    log.info(
        f"[payment_correction] reversed draft={draft_id} orig_tx={orig_tx_id} "
        f"counter_tx={counter_tx_id} by={admin_phone}"
    )
    return {
        "ok": True,
        "draft_id": draft_id,
        "original_transaction_id": orig_tx_id,
        "counter_transaction_id": counter_tx_id,
        "reversed_amount": float(draft.get("approved_amount") or draft.get("expected_amount") or 0),
    }


# ── Adjust ─────────────────────────────────────────────────────────────────────

async def adjust_payment(
    draft_id: int,
    new_amount: float,
    method: str,
    admin_phone: str,
    reason: str = "",
) -> dict:
    """
    Create an adjustment correction draft linked to an existing approved payment.

    The adjustment draft is created in 'pending' status.
    The admin then approves it with the standard PAID command.
    The original draft is marked 'adjusted' (not reversed).
    """
    if new_amount <= 0:
        return {"ok": False, "error": "Adjustment amount must be > 0"}

    draft = await fetch_one(
        "SELECT * FROM fazle_payment_drafts WHERE id = $1", draft_id
    )
    if not draft:
        return {"ok": False, "error": f"Payment draft #{draft_id} not found"}
    if draft["status"] not in ("approved", "sent"):
        return {
            "ok": False,
            "error": (
                f"Draft #{draft_id} has status '{draft['status']}'. "
                "Only 'approved' or 'sent' payments can be adjusted."
            ),
        }
    existing_correction = await fetch_one(
        "SELECT id FROM fazle_payment_drafts WHERE correction_of = $1 AND correction_type = 'adjustment' AND status != 'rejected'",
        draft_id,
    )
    if existing_correction:
        return {
            "ok": False,
            "error": (
                f"An adjustment draft #{existing_correction['id']} already exists for #{draft_id}. "
                "Reject it first before creating a new one."
            ),
        }

    orig_amount = float(draft.get("approved_amount") or draft.get("expected_amount") or 0)
    diff = round(new_amount - orig_amount, 2)
    sign = "+" if diff >= 0 else ""

    adj_text = (
        f"🔧 পেমেন্ট সংশোধন (Draft #{draft_id}):\n\n"
        f"কর্মী: {draft.get('employee_name', '?')}\n"
        f"পূর্বের পরিমাণ: ৳{orig_amount:,.0f}\n"
        f"নতুন পরিমাণ: ৳{new_amount:,.0f} ({sign}{diff:,.0f})\n"
        f"পদ্ধতি: {method}\n"
        f"কারণ: {reason or 'সংশোধন'}\n\n"
        f"✅ অনুমোদন দিতে: PAID <adj_draft_id> {new_amount:.0f} {method}"
    )

    adj_draft_id = await fetch_val(
        """INSERT INTO fazle_payment_drafts
               (draft_type, employee_id, employee_name, employee_mobile,
                escort_program_id, expected_amount, payment_method,
                status, admin_phone, draft_text, notes,
                correction_of, correction_type, correction_note, corrected_by, corrected_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7,
                   'pending', $8, $9, $10,
                   $11, 'adjustment', $12, $13, NOW())
           RETURNING id""",
        draft.get("draft_type") or "escort_payment",
        draft.get("employee_id"),
        draft.get("employee_name"),
        draft.get("employee_mobile"),
        draft.get("escort_program_id"),
        new_amount,
        method,
        admin_phone,
        adj_text,
        (reason or "")[:500],
        draft_id,
        (reason or "")[:500],
        admin_phone,
    )

    # Mark original as adjusted (not reversed — keeps the ledger intact)
    await execute(
        """UPDATE fazle_payment_drafts
              SET correction_type = 'adjustment',
                  correction_note = $1,
                  corrected_by    = $2,
                  corrected_at    = NOW(),
                  updated_at      = NOW()
            WHERE id = $3""",
        (reason or "adjusted")[:500],
        admin_phone,
        draft_id,
    )

    # Audit log
    await execute(
        """INSERT INTO fazle_payment_correction_log
               (action, payment_draft_id, original_amount, correction_amount, method, note, performed_by)
           VALUES ('adjusted', $1, $2, $3, $4, $5, $6)""",
        draft_id,
        orig_amount,
        new_amount,
        method,
        (reason or "")[:500],
        admin_phone,
    )

    obs.inc("payment_correction_total", labels={"action": "adjusted"})
    log.info(
        f"[payment_correction] adjustment draft={adj_draft_id} for original={draft_id} "
        f"amount={new_amount} by={admin_phone}"
    )
    return {
        "ok": True,
        "adjustment_draft_id": adj_draft_id,
        "original_draft_id": draft_id,
        "original_amount": orig_amount,
        "new_amount": new_amount,
        "diff": diff,
        "draft_text": adj_text,
    }


# ── List ───────────────────────────────────────────────────────────────────────

async def list_corrections(limit: int = 50) -> list[dict]:
    rows = await fetch_all(
        """SELECT l.id, l.action, l.payment_draft_id, l.transaction_id,
                  l.counter_tx_id, l.original_amount, l.correction_amount,
                  l.method, l.note, l.performed_by, l.created_at,
                  d.employee_name, d.draft_type
             FROM fazle_payment_correction_log l
             LEFT JOIN fazle_payment_drafts d ON d.id = l.payment_draft_id
            ORDER BY l.created_at DESC
            LIMIT $1""",
        limit,
    )
    return [dict(r) for r in rows]
