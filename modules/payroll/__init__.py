"""
Fazle Core — Monthly Payroll (Batch 14)

Aggregates Batch 12/13 outputs into monthly payroll runs.

State machine on wbom_payroll_runs.status:
    draft → reviewed → approved → locked → paid
                                 ↘ cancelled (from any non-paid state)

All transitions write wbom_payroll_approval_log. Compute is idempotent on
UNIQUE(employee_id, period_year, period_month) WHERE status<>'cancelled'.
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date
from typing import Optional

from app.database import fetch_one, fetch_all, execute, fetch_val, get_pool
import asyncpg

log = logging.getLogger("fazle.payroll")

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_PER_PROGRAM_RATE = 400.0  # ৳/day for escort duty (PAY-01: 12,000 ÷ 30)
ALLOWED_TRANSITIONS = {
    "draft":     {"reviewed", "cancelled"},
    "reviewed":  {"approved", "draft", "cancelled"},
    "approved":  {"locked", "reviewed", "cancelled"},
    "locked":    {"paid", "approved", "cancelled"},
    "paid":      set(),
    "cancelled": set(),
}


# ── Compute ───────────────────────────────────────────────────────────────────

async def compute_run(
    employee_id: int,
    period_year: int,
    period_month: int,
    computed_by: str,
    per_program_rate: Optional[float] = None,
) -> dict:
    """
    Idempotent monthly payroll compute. Returns existing active draft when present.
    Writes wbom_payroll_runs (1 row) + wbom_payroll_run_items (N rows) atomically.
    """
    if not (1 <= period_month <= 12):
        return {"ok": False, "error": "invalid period_month"}

    # Idempotency: existing active run
    existing = await fetch_one(
        """SELECT run_id, status, net_salary, total_programs, gross_salary
           FROM wbom_payroll_runs
           WHERE employee_id=$1 AND period_year=$2 AND period_month=$3
             AND status <> 'cancelled'""",
        employee_id, period_year, period_month,
    )
    if existing:
        return {
            "ok": True, "already_exists": True,
            "run_id": int(existing["run_id"]),
            "status": existing["status"],
            "net_salary": float(existing["net_salary"]),
            "total_programs": int(existing["total_programs"]),
            "gross_salary": float(existing["gross_salary"]),
        }

    emp = await fetch_one(
        "SELECT employee_id, employee_name, designation, basic_salary, status "
        "FROM wbom_employees WHERE employee_id=$1",
        employee_id,
    )
    if not emp:
        return {"ok": False, "error": f"employee {employee_id} not found"}

    basic_salary = float(emp["basic_salary"] or 0)
    rate = float(per_program_rate if per_program_rate is not None else DEFAULT_PER_PROGRAM_RATE)

    # Period bounds
    last_day = monthrange(period_year, period_month)[1]
    period_start = date(period_year, period_month, 1)
    period_end = date(period_year, period_month, last_day)

    # Count completed programs in month (count by end_date or program_date when end null)
    prog_rows = await fetch_all(
        """SELECT program_id, COALESCE(day_count, 1) AS days,
                  COALESCE(end_date, program_date) AS effective_date,
                  mother_vessel
           FROM wbom_escort_programs
           WHERE escort_employee_id=$1
             AND status='Completed'
             AND COALESCE(end_date, program_date) BETWEEN $2 AND $3""",
        employee_id, period_start, period_end,
    )
    total_days = sum(float(r["days"] or 0) for r in prog_rows)
    total_programs = len(prog_rows)
    program_allowance = round(total_days * rate, 2)

    # C1B: Sum advances from canonical fpe_cash_transactions within period
    advances = await fetch_val(
        """SELECT COALESCE(SUM(amount), 0) FROM fpe_cash_transactions
           WHERE employee_id=$1 AND txn_category='advance' AND transaction_status='final'
             AND txn_date BETWEEN $2 AND $3""",
        employee_id, period_start, period_end,
    ) or 0
    total_advances = float(advances)

    # Sum non-advance deductions (none recorded yet, but reserve hook)
    other_allowance = 0.0
    total_deductions = 0.0

    gross = round(basic_salary + program_allowance + other_allowance, 2)
    net = round(max(gross - total_advances - total_deductions, 0), 2)

    # Insert atomically
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            run_id = await conn.fetchval(
                """INSERT INTO wbom_payroll_runs
                      (employee_id, period_year, period_month, status,
                       basic_salary, total_programs, per_program_rate,
                       program_allowance, other_allowance, total_advances,
                       total_deductions, gross_salary, net_salary,
                       computed_by, created_at, updated_at)
                   VALUES ($1, $2, $3, 'draft', $4, $5, $6, $7, $8, $9, $10,
                           $11, $12, $13, NOW(), NOW())
                   RETURNING run_id""",
                employee_id, period_year, period_month,
                basic_salary, total_programs, rate,
                program_allowance, other_allowance, total_advances,
                total_deductions, gross, net, computed_by,
            )
            # Items: basic, programs, advances
            await conn.execute(
                """INSERT INTO wbom_payroll_run_items
                      (run_id, component_type, component_label, amount, sign, source_table, notes)
                   VALUES ($1, 'basic', 'Basic Salary', $2, '+', 'wbom_employees', $3)""",
                run_id, basic_salary, f"emp:{employee_id}",
            )
            for pr in prog_rows:
                await conn.execute(
                    """INSERT INTO wbom_payroll_run_items
                          (run_id, component_type, component_label, amount, sign,
                           source_table, source_id, notes)
                       VALUES ($1, 'program', $2, $3, '+',
                               'wbom_escort_programs', $4, $5)""",
                    run_id,
                    f"Program @ {rate}/day × {float(pr['days']):.1f}",
                    round(float(pr["days"]) * rate, 2),
                    int(pr["program_id"]),
                    pr["mother_vessel"],
                )
            if total_advances > 0:
                await conn.execute(
                    """INSERT INTO wbom_payroll_run_items
                          (run_id, component_type, component_label, amount, sign,
                           source_table, notes)
                       VALUES ($1, 'advance', 'Advances Adjusted', $2, '-',
                               'fpe_cash_transactions',
                               'sum within period')""",
                    run_id, total_advances,
                )
            await conn.execute(
                """INSERT INTO wbom_payroll_approval_log
                      (run_id, action, actor, from_status, to_status, payload_json)
                   VALUES ($1, 'compute', $2, NULL, 'draft',
                           $3::jsonb)""",
                run_id, computed_by,
                f'{{"total_programs":{total_programs},"net_salary":{net}}}',
            )

    log.info(f"[payroll] computed run {run_id} emp={employee_id} {period_year}-{period_month:02d} "
             f"days={total_days} net={net}")
    return {
        "ok": True, "already_exists": False,
        "run_id": int(run_id), "status": "draft",
        "employee_id": employee_id, "employee_name": emp["employee_name"],
        "period": f"{period_year}-{period_month:02d}",
        "basic_salary": basic_salary, "per_program_rate": rate,
        "total_programs": total_programs, "total_days": total_days,
        "program_allowance": program_allowance,
        "total_advances": total_advances,
        "gross_salary": gross, "net_salary": net,
    }


async def compute_all_for_period(
    period_year: int, period_month: int, computed_by: str,
) -> dict:
    """Compute runs for all Active employees. Idempotent."""
    rows = await fetch_all(
        "SELECT employee_id FROM wbom_employees "
        "WHERE COALESCE(status,'Active')='Active' ORDER BY employee_id"
    )
    created = 0; existing = 0; failed = 0
    run_ids = []
    for r in rows:
        result = await compute_run(int(r["employee_id"]), period_year, period_month, computed_by)
        if not result.get("ok"):
            failed += 1; continue
        if result.get("already_exists"):
            existing += 1
        else:
            created += 1
        run_ids.append(result["run_id"])
    return {"ok": True, "created": created, "existing": existing,
            "failed": failed, "total": len(rows), "run_ids": run_ids}


# ── Transitions ───────────────────────────────────────────────────────────────

async def _transition(
    run_id: int, target: str, actor: str,
    *, reason: Optional[str] = None,
    payment_method: Optional[str] = None,
    payment_reference: Optional[str] = None,
    paid_amount: Optional[float] = None,
) -> dict:
    cur = await fetch_one(
        "SELECT run_id, status, net_salary, employee_id, period_year, period_month "
        "FROM wbom_payroll_runs WHERE run_id=$1",
        run_id,
    )
    if not cur:
        return {"ok": False, "error": f"run {run_id} not found"}
    cur_status = cur["status"]
    if target not in ALLOWED_TRANSITIONS.get(cur_status, set()):
        return {"ok": False, "error":
                f"transition {cur_status} → {target} not allowed"}

    sets = ["status=$2", "updated_at=NOW()"]
    params = [run_id, target]
    idx = 3
    if target == "reviewed":
        sets.append(f"submitted_by=${idx}"); params.append(actor); idx += 1
    elif target == "approved":
        sets.append(f"approved_by=${idx}"); params.append(actor); idx += 1
    elif target == "locked":
        sets.append(f"locked_by=${idx}"); params.append(actor); idx += 1
    elif target == "paid":
        sets.append(f"paid_by=${idx}"); params.append(actor); idx += 1
        sets.append(f"paid_at=NOW()")
        if payment_method:
            sets.append(f"payment_method=${idx}"); params.append(payment_method); idx += 1
        if payment_reference:
            sets.append(f"payment_reference=${idx}"); params.append(payment_reference); idx += 1
        # Idempotency key: emp+period+ref
        idem = f"pr-{cur['employee_id']}-{cur['period_year']}-{cur['period_month']:02d}"
        if payment_reference:
            idem += f"-{payment_reference[:30]}"
        sets.append(f"payout_idempotency_key=${idx}"); params.append(idem); idx += 1
    elif target == "cancelled":
        if reason:
            sets.append(f"correction_reason=${idx}"); params.append(reason); idx += 1

    sql = f"UPDATE wbom_payroll_runs SET {', '.join(sets)} WHERE run_id=$1"
    try:
        await execute(sql, *params)
    except asyncpg.UniqueViolationError as e:
        return {"ok": False, "error": f"idempotency conflict: {e}"}

    # Audit
    payload = {}
    if reason: payload["reason"] = reason
    if payment_method: payload["method"] = payment_method
    if payment_reference: payload["ref"] = payment_reference
    if paid_amount is not None: payload["amount"] = paid_amount
    import json
    await execute(
        """INSERT INTO wbom_payroll_approval_log
              (run_id, action, actor, from_status, to_status, reason, payload_json)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
        run_id, target, actor, cur_status, target, reason, json.dumps(payload),
    )
    log.info(f"[payroll] run {run_id} {cur_status} → {target} by {actor}")
    return {"ok": True, "run_id": run_id, "from_status": cur_status,
            "to_status": target, "net_salary": float(cur["net_salary"])}


async def submit_run(run_id: int, actor: str) -> dict:
    return await _transition(run_id, "reviewed", actor)

async def approve_run(run_id: int, actor: str) -> dict:
    return await _transition(run_id, "approved", actor)

async def lock_run(run_id: int, actor: str) -> dict:
    return await _transition(run_id, "locked", actor)

async def mark_paid(run_id: int, actor: str, amount: float,
                     method: str, reference: Optional[str] = None) -> dict:
    return await _transition(run_id, "paid", actor,
                              payment_method=method, payment_reference=reference,
                              paid_amount=amount)

async def cancel_run(run_id: int, actor: str, reason: str) -> dict:
    return await _transition(run_id, "cancelled", actor, reason=reason)


# ── Queries ───────────────────────────────────────────────────────────────────

async def get_run(run_id: int) -> Optional[dict]:
    row = await fetch_one(
        """SELECT r.*, e.employee_name, e.employee_mobile
           FROM wbom_payroll_runs r
           JOIN wbom_employees e USING (employee_id)
           WHERE r.run_id=$1""",
        run_id,
    )
    return dict(row) if row else None


async def list_runs(period_year: int, period_month: int,
                     status: Optional[str] = None) -> list[dict]:
    if status:
        rows = await fetch_all(
            """SELECT r.run_id, r.employee_id, e.employee_name,
                      r.status, r.gross_salary, r.net_salary, r.total_programs
               FROM wbom_payroll_runs r
               JOIN wbom_employees e USING (employee_id)
               WHERE r.period_year=$1 AND r.period_month=$2 AND r.status=$3
               ORDER BY r.run_id""",
            period_year, period_month, status,
        )
    else:
        rows = await fetch_all(
            """SELECT r.run_id, r.employee_id, e.employee_name,
                      r.status, r.gross_salary, r.net_salary, r.total_programs
               FROM wbom_payroll_runs r
               JOIN wbom_employees e USING (employee_id)
               WHERE r.period_year=$1 AND r.period_month=$2
               ORDER BY r.run_id""",
            period_year, period_month,
        )
    return [dict(r) for r in rows]
