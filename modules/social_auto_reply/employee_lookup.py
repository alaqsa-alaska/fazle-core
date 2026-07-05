"""Read-only employee and payment lookups from existing WBOM/FPE tables.

SELECT only. No writes. No schema changes. No new tables.
"""
from __future__ import annotations

from typing import Any

from app.database import fetch_all, fetch_one, fetch_val


def _phone_variants(mobile: str) -> list[str]:
    digits = "".join(ch for ch in (mobile or "") if ch.isdigit())
    if not digits:
        return []
    if digits.startswith("880") and len(digits) == 13:
        canonical = digits
    elif digits.startswith("0") and len(digits) == 11:
        canonical = "880" + digits[1:]
    elif digits.startswith("1") and len(digits) == 10:
        canonical = "880" + digits
    else:
        return []
    return ["0" + canonical[3:], canonical, "+" + canonical]


async def find_by_mobile(mobile: str) -> dict[str, Any] | None:
    variants = _phone_variants(mobile)
    if not variants:
        return None
    row = await fetch_one(
        """
        SELECT employee_id, employee_name, designation, basic_salary, status,
               employee_mobile, joining_date
        FROM wbom_employees
        WHERE employee_mobile = ANY($1)
          AND status NOT IN ('deleted', 'terminated')
        LIMIT 1
        """,
        variants,
    )
    if row:
        return row

    # Fallback: FPE-linked employee created through payroll/admin/escort flows.
    return await fetch_one(
        """
        SELECT w.employee_id,
               COALESCE(NULLIF(w.employee_name, ''), NULLIF(e.full_name, '')) AS employee_name,
               COALESCE(NULLIF(w.designation, ''), NULLIF(e.department, ''), 'Staff') AS designation,
               w.basic_salary,
               COALESCE(w.status, CASE WHEN e.status = 'active' THEN 'Active' ELSE 'Inactive' END) AS status,
               COALESCE(w.employee_mobile, e.primary_phone, e.employee_id_phone) AS employee_mobile,
               w.joining_date
        FROM fpe_employees e
        LEFT JOIN wbom_employees w ON w.employee_id = e.wbom_employee_id
        WHERE e.status = 'active'
          AND (e.primary_phone = ANY($1) OR e.employee_id_phone = ANY($1))
        ORDER BY e.id DESC
        LIMIT 1
        """,
        variants,
    )


async def find_by_name(name: str) -> dict[str, Any] | None:
    row = await fetch_one(
        """
        SELECT employee_id, employee_name, designation, basic_salary, status,
               employee_mobile, joining_date
        FROM wbom_employees
        WHERE lower(employee_name) LIKE lower($1)
          AND status NOT IN ('deleted', 'terminated')
        ORDER BY employee_id DESC
        LIMIT 1
        """,
        f"%{name.strip()}%",
    )
    if row:
        return row

    return await fetch_one(
        """
        SELECT w.employee_id,
               COALESCE(NULLIF(w.employee_name, ''), NULLIF(e.full_name, '')) AS employee_name,
               COALESCE(NULLIF(w.designation, ''), NULLIF(e.department, ''), 'Staff') AS designation,
               w.basic_salary,
               COALESCE(w.status, CASE WHEN e.status = 'active' THEN 'Active' ELSE 'Inactive' END) AS status,
               COALESCE(w.employee_mobile, e.primary_phone, e.employee_id_phone) AS employee_mobile,
               w.joining_date
        FROM fpe_employees e
        LEFT JOIN wbom_employees w ON w.employee_id = e.wbom_employee_id
        WHERE e.status = 'active'
          AND lower(e.full_name) LIKE lower($1)
        ORDER BY e.id DESC
        LIMIT 1
        """,
        f"%{name.strip()}%",
    )


async def get_payment_history(employee_id: int) -> list[dict[str, Any]]:
    return await fetch_all(
        """
        SELECT amount, payout_method AS payment_method, txn_date AS transaction_date,
               transaction_status AS status, txn_category AS transaction_type
        FROM fpe_cash_transactions
        WHERE employee_id = $1
          AND transaction_status = 'final'
          AND deleted_at IS NULL
        ORDER BY txn_date DESC, created_at DESC
        LIMIT 15
        """,
        employee_id,
    )


async def get_total_paid(employee_id: int) -> float:
    val = await fetch_val(
        """
        SELECT COALESCE(SUM(amount), 0)
        FROM fpe_cash_transactions
        WHERE employee_id = $1
          AND transaction_status = 'final'
          AND deleted_at IS NULL
        """,
        employee_id,
    )
    return float(val or 0)


async def get_program_count(employee_id: int) -> int:
    val = await fetch_val(
        """
        SELECT COUNT(*)
        FROM wbom_escort_programs
        WHERE employee_id = $1
          AND status IN ('completed', 'released', 'closed')
        """,
        employee_id,
    )
    return int(val or 0)
