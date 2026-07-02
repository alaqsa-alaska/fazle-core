"""
Fazle Core — Payroll & Payment Logic (Phase 4G)

Answers employee questions about salary, payments, duty count.
Uses: fpe_cash_transactions, wbom_employees, wbom_escort_programs, wbom_salary_records

NEVER fabricates amounts. If data is missing → returns under_review=True.
"""

import logging
from datetime import date, timedelta
from typing import TypedDict, Optional
from decimal import Decimal

from app.database import fetch_one, fetch_all, fetch_val

log = logging.getLogger("fazle.payroll")


class PayrollSummary(TypedDict):
    employee_id: int
    employee_name: str
    basic_salary: Optional[float]
    designation: Optional[str]

    # Payments this month
    paid_this_month: float
    payment_count_this_month: int

    # Last payment
    last_payment_amount: Optional[float]
    last_payment_date: Optional[str]
    last_payment_method: Optional[str]

    # Duty/program stats (last 30 days)
    duty_count_30d: int
    active_duties: int

    # Totals (all time)
    total_paid_ever: float

    # Status
    under_review: bool
    notes: str


async def get_payroll_summary(employee_id: int) -> PayrollSummary:
    """
    Fetch full payroll picture for an employee.
    Used by reply engine to answer 'টাকা কত?' / 'কবে পাবো?' questions.
    """
    today = date.today()
    month_start = today.replace(day=1)

    # Employee base info
    emp = await fetch_one(
        """SELECT employee_id, employee_name, designation, basic_salary, status
           FROM wbom_employees WHERE employee_id = $1""",
        employee_id,
    )
    if not emp:
        return _empty_summary(employee_id)

    # C1B: Payments this month from canonical fpe_cash_transactions
    month_txns = await fetch_all(
        """SELECT amount, payout_method, txn_date, transaction_status
           FROM fpe_cash_transactions
           WHERE employee_id = $1
             AND txn_date >= $2
             AND transaction_status = 'final'
           ORDER BY txn_date DESC""",
        employee_id, month_start,
    )
    paid_this_month = sum(
        float(r["amount"]) for r in month_txns if r["amount"]
    )

    # Last payment (all time)
    last_txn = await fetch_one(
        """SELECT amount, payout_method, txn_date
           FROM fpe_cash_transactions
           WHERE employee_id = $1
             AND transaction_status = 'final'
           ORDER BY txn_date DESC
           LIMIT 1""",
        employee_id,
    )

    # Total ever paid
    total_paid = await fetch_val(
        "SELECT COALESCE(SUM(amount), 0) FROM fpe_cash_transactions "
        "WHERE employee_id = $1 AND transaction_status = 'final'",
        employee_id,
    )

    # Duty count last 30 days
    thirty_ago = today - timedelta(days=30)
    duty_count = await fetch_val(
        """SELECT COUNT(*) FROM wbom_escort_programs
           WHERE escort_employee_id = $1
             AND program_date >= $2""",
        employee_id, thirty_ago,
    )

    # Active (ongoing) duties
    active_duties = await fetch_val(
        """SELECT COUNT(*) FROM wbom_escort_programs
           WHERE escort_employee_id = $1
             AND status NOT IN ('completed', 'cancelled')""",
        employee_id,
    )

    # Check salary_records for current month
    salary_rec = await fetch_one(
        """SELECT net_salary, payment_status, payment_date
           FROM wbom_salary_records
           WHERE employee_id = $1 AND year = $2 AND month = $3""",
        employee_id, today.year, today.month,
    )

    # Determine if under review
    has_data = (paid_this_month > 0 or total_paid > 0 or
                (emp.get("basic_salary") and float(emp["basic_salary"]) > 0))
    under_review = not has_data

    notes_parts = []
    if salary_rec:
        status = salary_rec.get("payment_status", "")
        if status == "paid":
            notes_parts.append(f"এই মাসের বেতন পরিশোধিত হয়েছে {salary_rec['payment_date']}")
        elif status == "pending":
            notes_parts.append("এই মাসের বেতন প্রক্রিয়াধীন")
    if active_duties:
        notes_parts.append(f"বর্তমানে {active_duties}টি ডিউটি চলছে")

    return PayrollSummary(
        employee_id=employee_id,
        employee_name=emp["employee_name"],
        basic_salary=float(emp["basic_salary"]) if emp.get("basic_salary") else None,
        designation=emp.get("designation"),
        paid_this_month=paid_this_month,
        payment_count_this_month=len(month_txns),
        last_payment_amount=float(last_txn["amount"]) if last_txn and last_txn["amount"] else None,
        last_payment_date=str(last_txn["transaction_date"]) if last_txn else None,
        last_payment_method=last_txn.get("payment_method") if last_txn else None,
        duty_count_30d=int(duty_count or 0),
        active_duties=int(active_duties or 0),
        total_paid_ever=float(total_paid or 0),
        under_review=under_review,
        notes=" | ".join(notes_parts),
    )


def format_payroll_context(s: PayrollSummary) -> str:
    """
    Format payroll summary as readable context for Ollama prompt injection.
    """
    lines = []
    lines.append(f"কর্মীর নাম: {s['employee_name']}")
    if s.get("designation"):
        lines.append(f"পদ: {s['designation']}")
    if s.get("basic_salary") and s["basic_salary"] > 0:
        lines.append(f"মূল বেতন: ৳{s['basic_salary']:,.0f}")

    if s["paid_this_month"] > 0:
        lines.append(f"এই মাসে পেমেন্ট: ৳{s['paid_this_month']:,.0f} ({s['payment_count_this_month']}বার)")
    else:
        lines.append("এই মাসে পেমেন্টের তথ্য নেই")

    if s.get("last_payment_amount") and s["last_payment_amount"] > 0:
        method = s.get("last_payment_method") or ""
        lines.append(f"সর্বশেষ পেমেন্ট: ৳{s['last_payment_amount']:,.0f} ({s['last_payment_date']}){' — '+method if method else ''}")

    if s["duty_count_30d"]:
        lines.append(f"গত ৩০ দিনে ডিউটি: {s['duty_count_30d']}টি")
    if s["active_duties"]:
        lines.append(f"চলমান ডিউটি: {s['active_duties']}টি")

    if s["under_review"]:
        lines.append("⚠️ পেমেন্ট বিস্তারিত পর্যালোচনাধীন")
    if s.get("notes"):
        lines.append(s["notes"])

    return "\n".join(lines)


def _empty_summary(employee_id: int) -> PayrollSummary:
    return PayrollSummary(
        employee_id=employee_id,
        employee_name="?",
        basic_salary=None,
        designation=None,
        paid_this_month=0.0,
        payment_count_this_month=0,
        last_payment_amount=None,
        last_payment_date=None,
        last_payment_method=None,
        duty_count_30d=0,
        active_duties=0,
        total_paid_ever=0.0,
        under_review=True,
        notes="",
    )
