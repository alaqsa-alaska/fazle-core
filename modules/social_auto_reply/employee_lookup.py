"""Read-only employee and payment lookups from existing wbom_* tables.

SELECT only. No writes. No schema changes. No new tables.
"""
from __future__ import annotations

import re
from typing import Any

from app.database import fetch_all, fetch_one, fetch_val


async def find_by_mobile(mobile: str) -> dict[str, Any] | None:
    from modules.number_identity import normalize_phone as get_phone_variants
    variants = get_phone_variants(mobile)
    if not variants:
        return None
    return await fetch_one(
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


async def find_by_name(name: str) -> dict[str, Any] | None:
    return await fetch_one(
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
