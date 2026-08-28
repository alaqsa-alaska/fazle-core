"""Stage H5-H9 (2026-08-28) -- narrow financial operation adapter around
the authoritative FPE cash-transaction functions.

This module NEVER writes fpe_cash_transactions/fpe_unmatched_messages
directly with raw SQL -- every mutation goes through the one existing
authoritative function for that state change:
  - approve a pending transaction  -> modules.fazle_payroll_engine.routes.operator_approve()
  - reject a pending submission    -> modules.fazle_payroll_engine.routes.operator_reject()
  - reverse a final transaction    -> modules.fazle_payroll_engine.accounting.reverse_transaction()

This module does NOT grant authorization by itself. It assumes the
caller (modules.admin_commands.process_admin_command) has already
resolved the caller into the trusted_admin_execution authority lane
(modules.authority_lanes) and passed modules.rbac.check_permission() for
the specific command ("cash_approve"/"cash_reject"/"cash_reverse",
modules.rbac.COMMAND_ROLE) BEFORE any function here runs. Authorization
comes from that authority-lane + RBAC check, plus (for reverse) an
explicit Owner-supplied reason every single time -- never from anything
in this file.

Every operation here:
  1. resolves an EXPLICIT target (numeric id or a >=6-char txn_ref
     prefix) -- never guesses on an ambiguous match, always returns the
     candidate list instead of picking one;
  2. re-reads the CURRENT transaction_status from the DB immediately
     before acting (never trusts a stale caller-held value);
  3. validates the precondition for that specific operation
     (pending-before-approve/reject, final-and-not-already-reversed
     before-reverse);
  4. calls the ONE authoritative function for that transition;
  5. is safe to replay: a second identical command hits the
     authoritative function's own idempotency/precondition check and
     returns a clear "already <state>" error, never a duplicate
     accounting effect.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.database import fetch_one, fetch_all

log = logging.getLogger("fazle.financial_ops")

_TXN_FIELDS = (
    "id, txn_ref, employee_id, employee_name_raw, employee_id_phone, "
    "employee_phone, amount, payout_method, transaction_status, is_reversal, "
    "txn_category, accounting_period, reversed_txn_id, txn_date, created_at, "
    "source_message_id, source, deleted_at, deleted_by"
)


async def resolve_cash_transaction(ref: str) -> dict:
    """Resolve a human-typed reference to exactly one fpe_cash_transactions
    row. Accepts a bare numeric id, or a txn_ref prefix of at least 6
    characters (short enough to type, long enough that a collision is
    exceedingly unlikely -- and if one still occurs, the caller gets the
    candidate list, never a silent pick). Never returns an ambiguous match
    as if it were resolved.

    Stage I (I2, real finding): a numeric-id/txn-ref lookup can match a
    row that was already soft-deleted (fpe_cash_transactions.deleted_at
    IS NOT NULL, modules.admin_transactions.soft_delete_transaction() --
    the one authoritative deletion path, reverses ledger, never a
    physical delete). Resolution itself does not filter these out (an
    Admin asking "what happened to this ref" should still get an answer,
    not a bare "not found") -- but the row is returned WITH deleted_at
    populated, so every caller (cash_approve/cash_reject/cash_reverse)
    can refuse to act on it explicitly, by name, rather than silently
    re-processing a transaction someone already resolved."""
    ref = (ref or "").strip()
    if not ref:
        return {"ok": False, "error": "empty reference"}

    if ref.isdigit():
        row = await fetch_one(
            f"SELECT {_TXN_FIELDS} FROM fpe_cash_transactions WHERE id = $1",
            int(ref),
        )
        if row:
            return {"ok": True, "txn": dict(row)}
        return {"ok": False, "error": f"no transaction found with id {ref}"}

    if len(ref) < 6:
        return {
            "ok": False,
            "error": "reference too short to resolve safely -- provide the full txn-ref (or its numeric id)",
        }

    rows = await fetch_all(
        f"SELECT {_TXN_FIELDS} FROM fpe_cash_transactions WHERE txn_ref LIKE $1 || '%' LIMIT 5",
        ref,
    )
    if not rows:
        return {"ok": False, "error": f"no transaction found matching '{ref}'"}
    if len(rows) > 1:
        return {"ok": False, "ambiguous": True, "candidates": [dict(r) for r in rows]}
    return {"ok": True, "txn": dict(rows[0])}


def _refuse_if_deleted(txn: dict) -> Optional[dict]:
    """Shared guard for every mutation entry point below -- a soft-
    deleted transaction (see resolve_cash_transaction's own docstring)
    must never be approved/rejected/reversed. Returns an error dict if
    deleted, None if safe to proceed."""
    if txn.get("deleted_at"):
        return {
            "ok": False,
            "error": (
                f"transaction {txn['txn_ref']} was already deleted "
                f"(deleted_at={txn['deleted_at']}, deleted_by={txn.get('deleted_by')}) -- "
                "refusing to act on a deleted transaction"
            ),
            "txn": txn,
        }
    return None


async def _find_linked_pending_review(txn_id: int) -> Optional[dict]:
    """The operator_approve()/operator_reject() authoritative functions
    key off fpe_unmatched_messages.id (the review submission), not the
    transaction id directly -- resolve the one still-pending review row
    linked to this transaction via promoted_txn_id. Returns None if no
    such row exists (the transaction may have been created by a different
    path, e.g. a direct manual entry, with no operator-review workflow to
    approve/reject through)."""
    return await fetch_one(
        "SELECT id FROM fpe_unmatched_messages "
        "WHERE promoted_txn_id = $1 AND review_status = 'operator_pending'",
        txn_id,
    )


async def cash_approve(ref: str, admin_phone: str) -> dict:
    """H6 -- CASH APPROVE <txn-ref>. Resolves the exact pending
    transaction, validates it is still pending and has a linked
    operator-review row, then promotes it to final via the authoritative
    operator_approve(). Never approves an ambiguous match."""
    resolved = await resolve_cash_transaction(ref)
    if not resolved.get("ok"):
        return resolved
    txn = resolved["txn"]

    deleted = _refuse_if_deleted(txn)
    if deleted:
        return deleted

    if txn["transaction_status"] != "pending":
        return {
            "ok": False,
            "error": (
                f"transaction {txn['txn_ref']} is not pending "
                f"(current status: {txn['transaction_status']}) -- cannot approve; "
                "if this looks like a repeat of an already-approved command, no action is needed"
            ),
            "txn": txn,
        }

    pending = await _find_linked_pending_review(txn["id"])
    if not pending:
        return {
            "ok": False,
            "error": f"transaction {txn['txn_ref']} has no linked pending operator-review row -- cannot approve via this path",
            "txn": txn,
        }

    from modules.fazle_payroll_engine.routes import operator_approve

    try:
        result = await operator_approve(
            pending_id=pending["id"],
            employee_id=txn["employee_id"],
            reviewer=admin_phone,
            payout_method=None,
            txn_category=txn.get("txn_category") or "salary",
            amount_override=None,
            note=None,
        )
    except Exception as e:  # HTTPException from the route function, e.g. 409 on a raced double-approve
        return {"ok": False, "error": str(getattr(e, "detail", e)), "txn": txn}

    log.info(
        "[financial_ops] cash_approve txn_id=%s txn_ref=%s reviewer=%s",
        txn["id"], txn["txn_ref"], admin_phone,
    )
    return {"ok": True, "txn_id": txn["id"], "txn_ref": txn["txn_ref"], "txn": txn, "result": result}


async def cash_reject(ref: str, reason: str, admin_phone: str) -> dict:
    """H7 -- CASH REJECT <txn-ref> <reason>. A reason is always required.
    Note (existing authoritative behavior, not invented here):
    operator_reject() marks the linked review submission 'rejected'; it
    does NOT change the transaction's own transaction_status -- the
    fpe_cash_transactions row remains 'pending' (unresolved), matching
    the pre-existing contract of that function. This adapter does not
    add a new state transition on top of it."""
    if not reason or not reason.strip():
        return {"ok": False, "error": "a reason is required: CASH REJECT <txn-ref> <reason>"}

    resolved = await resolve_cash_transaction(ref)
    if not resolved.get("ok"):
        return resolved
    txn = resolved["txn"]

    deleted = _refuse_if_deleted(txn)
    if deleted:
        return deleted

    if txn["transaction_status"] != "pending":
        return {
            "ok": False,
            "error": (
                f"transaction {txn['txn_ref']} is not pending "
                f"(current status: {txn['transaction_status']}) -- cannot reject"
            ),
            "txn": txn,
        }

    pending = await _find_linked_pending_review(txn["id"])
    if not pending:
        return {
            "ok": False,
            "error": f"transaction {txn['txn_ref']} has no linked pending operator-review row -- cannot reject via this path",
            "txn": txn,
        }

    from modules.fazle_payroll_engine.routes import operator_reject

    try:
        result = await operator_reject(pending_id=pending["id"], reviewer=admin_phone, reason=reason.strip())
    except Exception as e:  # HTTPException, e.g. 409 on a replayed reject
        return {"ok": False, "error": str(getattr(e, "detail", e)), "txn": txn}

    log.info(
        "[financial_ops] cash_reject txn_id=%s txn_ref=%s reviewer=%s reason=%s",
        txn["id"], txn["txn_ref"], admin_phone, reason.strip()[:200],
    )
    return {"ok": True, "txn_id": txn["id"], "txn_ref": txn["txn_ref"], "txn": txn, "result": result}


async def cash_reverse(ref: str, reason: str, admin_phone: str) -> dict:
    """H8 (highest risk) -- CASH REVERSE <txn-ref> <reason>. NO standing
    autonomous reverse: this function is only ever reached from an
    explicit Owner/trusted-admin command naming this exact transaction
    and stating a reason, every single time -- there is no code path
    anywhere in this repo that calls this function without both. Only a
    'final' transaction that is not itself a reversal and has not
    already been reversed may be reversed; replay is a safe no-op
    (returns the existing reversal, never a second one)."""
    if not reason or not reason.strip():
        return {"ok": False, "error": "a reason is required: CASH REVERSE <txn-ref> <reason>"}

    resolved = await resolve_cash_transaction(ref)
    if not resolved.get("ok"):
        return resolved
    txn = resolved["txn"]

    deleted = _refuse_if_deleted(txn)
    if deleted:
        return deleted

    if txn["transaction_status"] != "final":
        return {
            "ok": False,
            "error": (
                f"transaction {txn['txn_ref']} is not final "
                f"(current status: {txn['transaction_status']}) -- only a final transaction can be reversed"
            ),
            "txn": txn,
        }
    if txn["is_reversal"]:
        return {"ok": False, "error": f"transaction {txn['txn_ref']} is itself a reversal entry -- cannot reverse a reversal", "txn": txn}

    existing_rev = await fetch_one(
        "SELECT id, txn_ref FROM fpe_cash_transactions WHERE reversed_txn_id = $1",
        txn["id"],
    )
    if existing_rev:
        return {
            "ok": False,
            "error": (
                f"transaction {txn['txn_ref']} was already reversed "
                f"(reversal id={existing_rev['id']}, ref={existing_rev['txn_ref']}) -- "
                "replay is a no-op, not a second reversal"
            ),
            "txn": txn,
        }

    from modules.fazle_payroll_engine.accounting import reverse_transaction

    try:
        result = await reverse_transaction(txn["id"], reason.strip(), created_by=admin_phone)
    except ValueError as e:
        return {"ok": False, "error": str(e), "txn": txn}

    log.info(
        "[financial_ops] cash_reverse txn_id=%s txn_ref=%s reviewer=%s reason=%s",
        txn["id"], txn["txn_ref"], admin_phone, reason.strip()[:200],
    )
    return {"ok": True, "txn_id": txn["id"], "txn_ref": txn["txn_ref"], "txn": txn, "result": result}


# ── H9: card formatting ──────────────────────────────────────────────────

def format_cash_review_card(txn: dict, provenance: Optional[dict] = None) -> str:
    """FINANCIAL REVIEW card for a pending cash transaction, matching the
    Owner-specified example format, extended per Stage H.5 (H5.16) with
    Review ID / employee ID+mobile / txn type / date / provenance
    evidence / explicit status. Presented for human review only -- never
    a one-tap auto-action (in particular, never suggests CASH REVERSE as
    a casual recommendation); the recipient must type the exact command
    line themselves.

    provenance (optional): the linked fpe_unmatched_messages row, if
    resolved by the caller (see get_cash_review_card_for_transaction) --
    when present, its raw_content is shown as the evidence this
    transaction was actually submitted with."""
    ref = txn.get("txn_ref") or str(txn.get("id"))
    short_ref = ref[:16]
    employee = txn.get("employee_name_raw") or f"employee_id={txn.get('employee_id')}"
    mobile = txn.get("employee_phone") or txn.get("employee_id_phone") or "—"
    amount = txn.get("amount")
    method = txn.get("payout_method") or "?"
    txn_type = txn.get("txn_category") or "?"
    txn_date = txn.get("txn_date") or "?"
    evidence_line = "no linked review evidence found" if provenance is None else (
        f'"{(provenance.get("raw_content") or "").strip()[:200]}"'
    )
    lines = [
        f"FINANCIAL REVIEW — Review ID {short_ref}",
        f"Employee: {employee} (ID {txn.get('employee_id')}, {mobile})",
        f"Transaction ref: {ref}",
        f"Amount: ৳{amount} — Type: {txn_type} — Date: {txn_date}",
        f"Current status: {txn.get('transaction_status')}",
        f"Evidence: {evidence_line}",
        f"Reason for attention: pending cash transaction awaiting operator approval",
        f"Recommended action: review, then approve or reject explicitly",
        f"Commands:",
        f"  CASH APPROVE {ref}",
        f"  CASH REJECT {ref} <reason>",
    ]
    return "\n".join(lines)


async def get_cash_review_card_for_transaction(ref: str) -> dict:
    """I/O wrapper: resolves the transaction AND its linked review-row
    evidence (if any), then renders the enriched card. Kept separate from
    the pure format_cash_review_card() above so the formatter itself
    stays directly unit-testable with no DB access."""
    resolved = await resolve_cash_transaction(ref)
    if not resolved.get("ok"):
        return resolved
    txn = resolved["txn"]
    provenance = await fetch_one(
        "SELECT raw_content, review_note FROM fpe_unmatched_messages WHERE promoted_txn_id = $1",
        txn["id"],
    )
    return {"ok": True, "txn": txn, "card": format_cash_review_card(txn, dict(provenance) if provenance else None)}


async def cash_details(ref: str) -> dict:
    """I5 -- CASH DETAILS <txn-ref>. Trusted-Admin READ-ONLY command:
    the full review context for one transaction (I4's field list), never
    mutates anything -- structurally the only DB calls here are SELECTs
    via resolve_cash_transaction()/fetch_one(). Adds age and a duplicate-
    suspect flag (same definition classify_all_pending_cash_transactions
    already uses) on top of get_cash_review_card_for_transaction()'s
    employee/amount/date/evidence/status fields."""
    result = await get_cash_review_card_for_transaction(ref)
    if not result.get("ok"):
        return result
    txn = result["txn"]

    age_note = ""
    if txn.get("created_at"):
        from datetime import datetime, timezone
        created = txn["created_at"]
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).total_seconds() / 86400.0
        age_note = f"\nAge: {age_days:.1f} days"

    dup_note = ""
    if txn.get("transaction_status") == "pending" and not txn.get("deleted_at"):
        dup = await fetch_one(
            "SELECT COUNT(*) AS n FROM fpe_cash_transactions "
            "WHERE transaction_status='pending' AND deleted_at IS NULL AND id != $1 "
            "AND employee_id = $2 AND amount = $3 AND txn_date = $4",
            txn["id"], txn.get("employee_id"), txn.get("amount"), txn.get("txn_date"),
        )
        if dup and dup["n"]:
            dup_note = f"\n⚠️ {dup['n']} other pending transaction(s) share the same employee/amount/date -- possible duplicate"

    return {"ok": True, "txn": txn, "card": result["card"] + age_note + dup_note}


def format_cash_result(op: str, r: dict) -> str:
    """Renders a cash_approve/cash_reject/cash_reverse() result dict as
    the WhatsApp confirmation/error text. `op` is one of
    'approve'/'reject'/'reverse', used only for the success label."""
    if not r.get("ok"):
        if r.get("ambiguous"):
            lines = "\n".join(
                f"  - {c['txn_ref']} (id={c['id']}, ৳{c['amount']}, {c.get('employee_name_raw') or c.get('employee_id')})"
                for c in r["candidates"]
            )
            return f"❌ একাধিক transaction মিলেছে, নির্দিষ্ট করে দিন:\n{lines}"
        return f"❌ {r.get('error')}"

    txn_ref = r.get("txn_ref", "")
    if op == "approve":
        return (
            f"✅ CASH APPROVED — {txn_ref}\n"
            f"Transaction #{r['txn_id']} → status: final\n"
            f"Ledger updated ✓ | Audit logged ✓"
        )
    if op == "reject":
        return (
            f"🚫 CASH REJECTED (review submission) — {txn_ref}\n"
            f"Transaction #{r['txn_id']} remains pending (no ledger effect).\n"
            f"Review submission marked rejected."
        )
    if op == "reverse":
        reversal = r.get("result")
        rev_ref = getattr(reversal, "txn_ref", None) or (reversal.get("txn_ref") if isinstance(reversal, dict) else None)
        return (
            f"↩️ CASH REVERSED — {txn_ref}\n"
            f"Original transaction #{r['txn_id']} unchanged (never mutated).\n"
            f"New reversal entry created" + (f": {rev_ref}" if rev_ref else "") + "\n"
            f"Ledger updated ✓ | Audit logged ✓"
        )
    return f"✅ {op} ok — {txn_ref}"
