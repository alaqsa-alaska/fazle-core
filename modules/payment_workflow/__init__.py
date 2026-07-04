"""
Fazle Core — Payment Workflow
Handles:
  1. Escort duty release → payment draft → admin notification
  2. Advance payment request → admin approval draft
  3. Salary finalization (after admin approval)

Source business logic: resources/Cash Payment Accountant-Admin.txt

Flow (Escort Payment):
  Employee sends release slip photo
  → System verifies duty record exists
  → Calculates expected payment (daily_rate × duty_days - advances)
  → Creates payment draft in fazle_payment_drafts
  → Sends draft to admin WhatsApp (suppressed in safe mode)
  → Admin sends PAID <id> <amount> <method>
  → System sends accountant message
  → Records in fpe_cash_transactions

Flow (Advance):
  Employee requests advance
  → System checks recent payments and pending duty
  → Creates advance draft
  → Admin approves with ADVANCE <id> <amount> <method>
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from app.database import fetch_one, fetch_all, execute, fetch_val, get_pool
from app.config import get_settings
from modules.fazle_payroll_engine.employee import match_or_create_employee
from modules.fazle_payroll_engine.accounting import create_transaction
from modules.fazle_payroll_engine.models import PayoutMethod, TxnCategory, PaymentSource
from modules.fazle_payroll_engine.payment_event import payment_event_from_whatsapp, payment_event_to_request

log = logging.getLogger("fazle.payment")

# ── Config ─────────────────────────────────────────────────────────────────────
DEFAULT_DAILY_RATE = 400  # ৳400/day for escort duty (PAY-01: 12,000 ÷ 30)


# ── Escort payment draft ───────────────────────────────────────────────────────

async def create_escort_payment_draft(
    employee_id: int,
    escort_program_id: Optional[int] = None,
    override_days: Optional[float] = None,
    source: str = "bridge1",
    conn: Optional[Any] = None,
) -> dict:
    """
    Create a payment draft after escort duty release.
    Returns draft info including the text to send to admin.
    """
    try:
        emp = await fetch_one(
            """SELECT employee_id, employee_name, employee_mobile, designation,
                      basic_salary, bkash_number, nagad_number
               FROM wbom_employees WHERE employee_id = $1""",
            employee_id,
            conn=conn,
        )
        if not emp:
            return {"error": f"Employee {employee_id} not found", "draft_id": None}

        duty_days = override_days
        prog_name = "Unknown Program"
        prog: dict = {}

        # Try to get program info
        if escort_program_id:
            prog = await fetch_one(
                """SELECT program_id, program_date, mother_vessel, end_date,
                          completion_time, lighter_vessel, master_mobile,
                          shift, end_shift, day_count, food_bill, conveyance
                   FROM wbom_escort_programs WHERE program_id = $1""",
                escort_program_id,
                conn=conn,
            )
            if prog:
                prog_name = prog.get("mother_vessel") or f"Program #{escort_program_id}"
                if duty_days is None:
                    # Calculate from dates
                    start = prog.get("program_date")
                    ct = prog.get("completion_time")
                    end = prog.get("end_date") or (ct.date() if ct else None)
                    if start and end:
                        if hasattr(start, "date"):
                            start = start.date()
                        if hasattr(end, "date"):
                            end = end.date()
                        delta = (end - start).days + 1
                        duty_days = max(float(delta), 1.0)

        if duty_days is None:
            duty_days = 1.0

        # Authoritative settlement:
        # (basic_salary / 30 * roster duty_days) - food - conveyance -
        # advances for this escort program in the current payroll month.
        daily_rate = float(emp.get("basic_salary") or DEFAULT_DAILY_RATE) / 30
        gross_amount = round(duty_days * daily_rate, 2)
        food_bill = float(prog.get("food_bill") or 0)
        conveyance = float(prog.get("conveyance") or 0)

        # Only advances tied to this escort program and current payroll month.
        advances = await fetch_val(
            """SELECT COALESCE(SUM(amount), 0)
               FROM fpe_cash_transactions
               WHERE employee_id = $1 AND txn_category = 'advance'
                 AND program_id = $2
                 AND txn_date >= DATE_TRUNC('month', CURRENT_DATE)::date
                 AND txn_date < (DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '1 month')::date
                 AND transaction_status = 'final'
                 AND deleted_at IS NULL""",
            employee_id, escort_program_id,
            conn=conn,
        ) or 0.0
        net_payable = max(gross_amount - food_bill - conveyance - float(advances), 0)

        bkash = emp.get("bkash_number") or emp.get("nagad_number") or "?"

        # Phase 7: include full vessel context in draft text
        vessel_lines = ""
        if escort_program_id and prog:  # type: ignore[name-defined]
            lighter = prog.get("lighter_vessel") or ""
            master_mob = prog.get("master_mobile") or ""
            prog_date_raw = prog.get("program_date")
            prog_date_str = (
                prog_date_raw.strftime("%d %b %Y")
                if prog_date_raw and hasattr(prog_date_raw, "strftime")
                else str(prog_date_raw or "")
            )
            shift_raw = prog.get("shift") or ""
            shift_label = (
                "দিন (D)" if str(shift_raw).upper().startswith("D")
                else "রাত (N)" if str(shift_raw).upper().startswith("N")
                else shift_raw
            )
            vessel_lines = (
                f"জাহাজ (MV): {prog_name}\n"
                + (f"লাইটার: {lighter}\n" if lighter else "")
                + (f"মাস্টার মোবাইল: {master_mob}\n" if master_mob else "")
                + (f"তারিখ: {prog_date_str}\n" if prog_date_str else "")
                + (f"শিফট: {shift_label}\n" if shift_label else "")
            )
        else:
            vessel_lines = f"ডিউটি: {prog_name}\n"

        draft_text = (
            f"💼 এস্কর্ট পেমেন্ট রিকোয়েস্ট:\n\n"
            f"কর্মী: {emp['employee_name']}\n"
            f"{vessel_lines}"
            f"দিন: {duty_days:.1f}\n"
            f"গ্রস বেতন: ৳{gross_amount:,.0f}\n"
            f"খাবার বিল কর্তন: ৳{food_bill:,.0f}\n"
            f"কনভেয়েন্স কর্তন: ৳{conveyance:,.0f}\n"
            f"অগ্রিম কর্তন: ৳{advances:,.0f}\n"
            f"নেট দেয়: ৳{net_payable:,.0f}\n"
            f"বিকাশ/নগদ: {bkash}\n\n"
            f"✅ অনুমোদন দিতে: PAID <draft_id> {net_payable:.0f} bkash"
        )

        # Serialize release-draft creation inside the caller's transaction.
        if conn is not None and escort_program_id is not None:
            await conn.execute("SELECT pg_advisory_xact_lock($1)", int(escort_program_id))
        existing_draft_id = await fetch_val(
            """SELECT id FROM fazle_payment_drafts
               WHERE escort_program_id=$1 AND draft_type='escort_payment'
               ORDER BY id DESC LIMIT 1""",
            escort_program_id,
            conn=conn,
        )
        if existing_draft_id:
            draft_id = existing_draft_id
            await execute(
                """UPDATE fazle_payment_drafts
                   SET employee_id=$2, employee_name=$3, employee_mobile=$4,
                       duty_days=$5, gross_amount=$6, food_bill=$7, conveyance=$8,
                       advance_deduction=$9, expected_amount=$10, status='pending',
                       draft_text=$11, source=$12, updated_at=NOW()
                   WHERE id=$1""",
                draft_id, employee_id, emp["employee_name"], emp.get("employee_mobile"),
                duty_days, gross_amount, food_bill, conveyance, advances, net_payable,
                draft_text, source, conn=conn,
            )
        else:
            draft_id = await fetch_val(
                """INSERT INTO fazle_payment_drafts
                   (draft_type, employee_id, employee_name, employee_mobile,
                    escort_program_id, duty_days, gross_amount, food_bill, conveyance,
                    advance_deduction, expected_amount, status, draft_text, source, updated_at)
               VALUES ('escort_payment', $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                       'pending', $11, $12, NOW())
               RETURNING id""",
                employee_id, emp["employee_name"], emp.get("employee_mobile"),
                escort_program_id, duty_days, gross_amount, food_bill, conveyance,
                advances, net_payable, draft_text, source,
                conn=conn,
            )

        if draft_id:
            # Update draft_text with actual ID
            draft_text = draft_text.replace("<draft_id>", str(draft_id))
            await execute(
                "UPDATE fazle_payment_drafts SET draft_text=$1 WHERE id=$2",
                draft_text, draft_id,
                conn=conn,
            )

        log.info(f"[payment] escort draft #{draft_id} created for emp {employee_id}, ৳{net_payable:,.0f}")
        return {
            "draft_id": draft_id,
            "draft_text": draft_text,
            "employee_name": emp["employee_name"],
            "expected_amount": net_payable,
            "duty_days": duty_days,
        }

    except Exception as e:
        log.error(f"[payment] create_escort_payment_draft error: {e}")
        if conn is not None:
            raise
        return {"error": str(e), "draft_id": None}


# ── Advance payment draft ──────────────────────────────────────────────────────

async def create_advance_request_draft(
    employee_id: int,
    requested_amount: Optional[float] = None,
    source: str = "bridge1",
) -> dict:
    """
    Create an advance payment approval draft for admin.
    """
    try:
        emp = await fetch_one(
            """SELECT employee_id, employee_name, employee_mobile,
                      basic_salary, bkash_number, nagad_number
               FROM wbom_employees WHERE employee_id = $1""",
            employee_id,
        )
        if not emp:
            return {"error": f"Employee {employee_id} not found", "draft_id": None}

        # Recent payments this month
        month_start = date.today().replace(day=1)
        paid_this_month = await fetch_val(
            """SELECT COALESCE(SUM(amount), 0)
               FROM fpe_cash_transactions
               WHERE employee_id = $1
                 AND txn_date >= $2
                 AND transaction_status = 'final'
                 AND deleted_at IS NULL""",
            employee_id, month_start,
        ) or 0.0

        # Active duties count
        active_duties = await fetch_val(
            """SELECT COUNT(*)
               FROM wbom_escort_programs
               WHERE escort_employee_id = $1 AND status = 'active'""",
            employee_id,
        ) or 0

        bkash = emp.get("bkash_number") or emp.get("nagad_number") or "?"
        amount_str = f"৳{requested_amount:,.0f}" if requested_amount else "অনির্দিষ্ট"

        draft_text = (
            f"💰 অগ্রিম পেমেন্ট রিকোয়েস্ট:\n\n"
            f"কর্মী: {emp['employee_name']}\n"
            f"মোবাইল: {emp.get('employee_mobile','?')}\n"
            f"চাওয়া পরিমাণ: {amount_str}\n"
            f"এই মাসে পেয়েছে: ৳{paid_this_month:,.0f}\n"
            f"সক্রিয় ডিউটি: {active_duties}\n"
            f"বিকাশ/নগদ: {bkash}\n\n"
            f"✅ অনুমোদন দিতে: ADVANCE <draft_id> <পরিমাণ> bkash/nagad/cash\n"
            f"🚫 বাতিল করতে: REJECT <draft_id>"
        )

        draft_id = await fetch_val(
            """INSERT INTO fazle_payment_drafts
                   (draft_type, employee_id, employee_name, employee_mobile,
                    expected_amount, status, draft_text, source, updated_at)
               VALUES ('advance', $1, $2, $3, $4, 'pending', $5, $6, NOW())
               RETURNING id""",
            employee_id, emp["employee_name"], emp.get("employee_mobile"),
            requested_amount or 0, draft_text, source,
        )

        if draft_id:
            draft_text = draft_text.replace("<draft_id>", str(draft_id))
            await execute(
                "UPDATE fazle_payment_drafts SET draft_text=$1 WHERE id=$2",
                draft_text, draft_id,
            )

        log.info(f"[payment] advance draft #{draft_id} for emp {employee_id}")
        return {
            "draft_id": draft_id,
            "draft_text": draft_text,
            "employee_name": emp["employee_name"],
        }

    except Exception as e:
        log.error(f"[payment] advance_request_draft error: {e}")
        return {"error": str(e), "draft_id": None}


# ── Finalize payment (after admin approves) ────────────────────────────────────

async def finalize_payment(draft_id: int, approved_amount: float, method: str) -> dict:
    """
    C1B: Record payment in fpe_cash_transactions after admin approval.
    Returns accountant_message to forward to accountant.
    """
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                draft_row = await conn.fetchrow(
                    "SELECT * FROM fazle_payment_drafts WHERE id = $1 FOR UPDATE", draft_id
                )
                if not draft_row:
                    return {"error": f"Draft #{draft_id} not found"}
                draft = dict(draft_row)
                if draft.get("status") == "sent":
                    return {
                        "already_finalized": True,
                        "accountant_msg": draft.get("accountant_msg"),
                        "employee_name": draft.get("employee_name"),
                        "amount": float(draft.get("approved_amount") or approved_amount),
                        "method": draft.get("payment_method") or method,
                    }

                method_map = {"bkash": "Bkash", "nagad": "Nagad", "cash": "Cash"}
                method_display = method_map.get(method.lower(), method.upper())

                txn_type = "advance" if draft.get("draft_type") == "advance" else "escort_payment"

                # C1B: resolve FPE employee and create canonical transaction
                raw_phone = draft.get("employee_mobile")
                fpe_emp = await match_or_create_employee(
                    name_raw=draft.get("employee_name"),
                    payout_phone=raw_phone,
                    employee_id_phone=raw_phone,
                )
                if not fpe_emp:
                    return {"error": f"Could not resolve FPE employee for draft #{draft_id}"}

                try:
                    payout_method = PayoutMethod(method)
                except ValueError:
                    payout_method = PayoutMethod.cash

                txn_category = TxnCategory.advance if txn_type == "advance" else TxnCategory.salary

                event = payment_event_from_whatsapp(
                    employee_id=fpe_emp.employee_id,
                    employee_name_raw=draft.get("employee_name"),
                    employee_id_phone=raw_phone,
                    employee_phone=raw_phone,
                    payout_phone=raw_phone,
                    payout_method=payout_method,
                    amount=Decimal(str(approved_amount)),
                    txn_date=date.today(),
                    txn_category=txn_category,
                    wa_message_id=f"payment-draft:{draft_id}",
                    source_channel="payment_workflow",
                    source_message_text=f"Draft #{draft_id} — approved by admin",
                    created_by="payment_workflow",
                    program_id=draft.get("escort_program_id"),
                    metadata={
                        "legacy_draft_id": draft_id,
                        "draft_type": draft.get("draft_type"),
                        "employee_name": draft.get("employee_name"),
                        "employee_mobile": raw_phone,
                    },
                )
                event.source = PaymentSource.escort if txn_type == "escort_payment" else PaymentSource.nl_advance

                req = payment_event_to_request(event)
                txn_row = await create_transaction(req)

                accountant_msg = (
                    f"💳 পেমেন্ট নির্দেশনা\n\n"
                    f"কর্মী: {draft.get('employee_name','?')}\n"
                    f"মোবাইল: {draft.get('employee_mobile','?')}\n"
                    f"পরিমাণ: ৳{approved_amount:,.0f}\n"
                    f"পদ্ধতি: {method_display}\n"
                    f"ধরন: {'অগ্রিম' if txn_type == 'advance' else 'এস্কর্ট পেমেন্ট'}\n"
                    f"Draft: #{draft_id}"
                )

                await conn.execute(
                    """UPDATE fazle_payment_drafts
                       SET status='sent', approved_amount=$1, payment_method=$2,
                           accountant_msg=$3, approved_at=NOW(), updated_at=NOW(),
                           transaction_id=$5, txn_ref=$6
                       WHERE id=$4""",
                    approved_amount, method, accountant_msg, draft_id, txn_row.id, txn_row.txn_ref,
                )

        log.info(f"[payment] finalized draft #{draft_id}: ৳{approved_amount:,.0f} via {method} txn={txn_row.id}")
        return {
            "accountant_msg": accountant_msg,
            "employee_name": draft.get("employee_name"),
            "amount": approved_amount,
            "method": method_display,
            "transaction_id": txn_row.id,
            "txn_ref": txn_row.txn_ref,
        }

    except Exception as e:
        log.error(f"[payment] finalize error: {e}")
        return {"error": str(e)}


# ── Employee advance request detector ─────────────────────────────────────────

ADVANCE_KEYWORDS = [
    # Core advance / salary
    "অগ্রিম", "অগ্রীম", "advance", "আগাম",
    "টাকা দেন", "টাকা লাগবে", "টাকা দরকার",
    "পেমেন্ট দেন", "বেতন দেন",
    # Emergency / personal crisis
    "ইমার্জেন্সি", "জরুরি টাকা", "জরুরী টাকা",
    "চিকিৎসার জন্য", "হাসপাতালে", "হাসপাতাল",
    "পরিবারের জন্য", "পরিবার সংকট",
    "বিপদে পড়েছি", "বিপদ", "সংকট",
    # Help / assistance
    "সাহায্য করুন", "সাহায্য লাগবে", "হেল্প",
]


def is_advance_request(text: str) -> bool:
    """Check if employee message is an advance / emergency payment request."""
    t = text.lower()
    return any(kw in t for kw in ADVANCE_KEYWORDS)
