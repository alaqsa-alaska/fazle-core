"""
Batch 17 — Reports & Admin Insights
====================================
Pure-SQL report builders + simple cache + audit log.

Builders return JSON-serialisable dicts shaped as:
    {"report": str, "args": dict, "generated_at": ISO, "rows": [...], "summary": {...}}

Each builder is registered in `_BUILDERS` so the FastAPI endpoint
`/reports/{name}` can dispatch by name with arbitrary kwargs.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from app.database import fetch_all, fetch_one, fetch_val, execute

log = logging.getLogger("fazle.reports")

# ── cache (10 min default) ───────────────────────────────────────────────────
DEFAULT_TTL_SEC = 600


def _today() -> date:
    return date.today()


async def _cache_get(key: str) -> Optional[dict]:
    row = await fetch_one(
        "SELECT payload_json FROM fazle_report_cache "
        "WHERE cache_key=$1 AND expires_at > now()",
        key,
    )
    if not row:
        return None
    p = row["payload_json"]
    return p if isinstance(p, dict) else json.loads(p)


async def _cache_set(key: str, name: str, payload: dict, ttl: int) -> None:
    await execute(
        """INSERT INTO fazle_report_cache (cache_key, report_name, payload_json, expires_at)
           VALUES ($1, $2, $3::jsonb, now() + ($4 || ' seconds')::interval)
           ON CONFLICT (cache_key) DO UPDATE
              SET payload_json=EXCLUDED.payload_json,
                  expires_at=EXCLUDED.expires_at,
                  created_at=now()""",
        key, name, json.dumps(payload, default=str), str(ttl),
    )


async def _record_run(name: str, args: dict, status: str, duration_ms: int,
                      row_count: int = 0, requested_by: Optional[str] = None,
                      error: Optional[str] = None) -> None:
    try:
        await execute(
            """INSERT INTO fazle_report_runs
                  (report_name, args_json, requested_by, status, duration_ms, row_count, error)
               VALUES ($1, $2::jsonb, $3, $4, $5, $6, $7)""",
            name, json.dumps(args, default=str), requested_by, status,
            duration_ms, row_count, error,
        )
    except Exception as e:
        log.warning(f"[reports] failed to record run {name}: {e}")


# ── builders ────────────────────────────────────────────────────────────────
async def _b_daily_summary(date: Optional[date] = None) -> dict:  # noqa: A002
    """Daily activity snapshot — payments in/out, programs, payroll status."""
    d = date or _today()
    # C1B: daily summary reads from canonical fpe_cash_transactions
    pay = await fetch_one(
        """SELECT
              COALESCE(SUM(CASE WHEN amount >= 0 THEN amount END), 0) AS total_out,
              COALESCE(SUM(CASE WHEN amount < 0  THEN amount END), 0) AS total_in,
              COUNT(*) FILTER (WHERE amount >= 0) AS out_count,
              COUNT(*) FILTER (WHERE amount < 0)  AS in_count
           FROM fpe_cash_transactions
           WHERE txn_date = $1
             AND transaction_status = 'final'""",
        d,
    ) or {}
    by_method = await fetch_all(
        """SELECT payout_method,
                  COUNT(*) AS cnt, COALESCE(SUM(amount),0) AS amt
           FROM fpe_cash_transactions
           WHERE txn_date=$1 AND transaction_status='final'
           GROUP BY payout_method
           ORDER BY payout_method""",
        d,
    )
    programs = await fetch_one(
        """SELECT
              COUNT(*)                                                    AS total,
              COUNT(*) FILTER (WHERE status IN ('Active','Assigned'))     AS active,
              COUNT(*) FILTER (WHERE status='Completed')                  AS completed
           FROM wbom_escort_programs
           WHERE program_date = $1""",
        d,
    ) or {}
    pending_staging = await fetch_val(
        "SELECT COUNT(*) FROM wbom_staging_payments WHERE status='pending'"
    )
    return {
        "report": "daily_summary",
        "args": {"date": d.isoformat()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "date": d.isoformat(),
            "payments_in":  float(pay.get("total_in", 0)),
            "payments_out": float(pay.get("total_out", 0)),
            "in_count":  int(pay.get("in_count", 0)),
            "out_count": int(pay.get("out_count", 0)),
            "programs_total":     int(programs.get("total", 0)),
            "programs_active":    int(programs.get("active", 0)),
            "programs_completed": int(programs.get("completed", 0)),
            "staging_pending": int(pending_staging or 0),
        },
        "rows": [
            {
                "payment_method": r["payment_method"],
                "transaction_type": r["transaction_type"],
                "count": r["cnt"],
                "amount": float(r["amt"]),
            } for r in by_method
        ],
    }


async def _b_monthly_payroll(year: int, month: int) -> dict:
    """Monthly payroll — totals by status + per-employee net."""
    rows = await fetch_all(
        """SELECT pr.run_id, pr.employee_id, e.employee_name, pr.status,
                  pr.basic_salary, pr.program_allowance, pr.other_allowance,
                  pr.total_advances, pr.total_deductions,
                  pr.gross_salary, pr.net_salary, pr.total_programs,
                  pr.payment_method, pr.paid_at
           FROM wbom_payroll_runs pr
           LEFT JOIN wbom_employees e ON e.employee_id = pr.employee_id
           WHERE pr.period_year=$1 AND pr.period_month=$2
           ORDER BY pr.status, e.employee_name""",
        year, month,
    )
    summary = {"runs": len(rows)}
    by_status: dict[str, dict] = {}
    total_net = 0.0
    total_paid = 0.0
    for r in rows:
        st = r["status"] or "draft"
        bucket = by_status.setdefault(st, {"count": 0, "net": 0.0})
        bucket["count"] += 1
        bucket["net"] += float(r["net_salary"])
        total_net += float(r["net_salary"])
        if st == "paid":
            total_paid += float(r["net_salary"])
    summary["by_status"] = by_status
    summary["total_net_accrued"] = total_net
    summary["total_net_paid"] = total_paid
    summary["total_net_pending"] = total_net - total_paid
    return {
        "report": "monthly_payroll",
        "args": {"year": year, "month": month},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "rows": [
            {
                "run_id": r["run_id"],
                "employee_id": r["employee_id"],
                "employee_name": r["employee_name"],
                "status": r["status"],
                "total_programs": r["total_programs"],
                "gross_salary": float(r["gross_salary"]),
                "net_salary": float(r["net_salary"]),
                "advances": float(r["total_advances"]),
                "deductions": float(r["total_deductions"]),
                "payment_method": r["payment_method"],
                "paid_at": r["paid_at"].isoformat() if r["paid_at"] else None,
            } for r in rows
        ],
    }


async def _b_escort_utilization(start: date, end: date) -> dict:
    """Escort utilization — programs per escort over a date range."""
    rows = await fetch_all(
        """SELECT p.escort_employee_id, e.employee_name,
                  COUNT(*) AS programs,
                  COUNT(*) FILTER (WHERE p.status='Completed') AS completed,
                  COALESCE(SUM(p.day_count), 0)   AS total_days,
                  COALESCE(SUM(p.conveyance), 0)  AS total_conveyance
           FROM wbom_escort_programs p
           LEFT JOIN wbom_employees e ON e.employee_id = p.escort_employee_id
           WHERE p.program_date BETWEEN $1 AND $2
             AND p.escort_employee_id IS NOT NULL
           GROUP BY p.escort_employee_id, e.employee_name
           ORDER BY programs DESC""",
        start, end,
    )
    return {
        "report": "escort_utilization",
        "args": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "escorts": len(rows),
            "total_programs": sum(int(r["programs"]) for r in rows),
            "total_days": float(sum(float(r["total_days"]) for r in rows)),
        },
        "rows": [
            {
                "escort_employee_id": r["escort_employee_id"],
                "employee_name": r["employee_name"],
                "programs": int(r["programs"]),
                "completed": int(r["completed"]),
                "total_days": float(r["total_days"]),
                "total_conveyance": float(r["total_conveyance"]),
            } for r in rows
        ],
    }


async def _b_payment_reconciliation(days: int = 7) -> dict:
    """Payment reconciliation — matched vs unmatched staging in last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    summary = await fetch_one(
        """SELECT
              COUNT(*) FILTER (WHERE matched_employee_id IS NOT NULL) AS matched,
              COUNT(*) FILTER (WHERE matched_employee_id IS NULL)     AS unmatched,
              COUNT(*) FILTER (WHERE status='pending')                AS pending,
              COUNT(*) FILTER (WHERE status='approved')               AS approved,
              COUNT(*) FILTER (WHERE status='rejected')               AS rejected,
              COUNT(*)                                                AS total
           FROM wbom_staging_payments
           WHERE created_at >= $1""",
        cutoff,
    ) or {}
    age_buckets = await fetch_all(
        """SELECT
              CASE
                WHEN created_at > now() - INTERVAL '1 hour'  THEN '0-1h'
                WHEN created_at > now() - INTERVAL '6 hours' THEN '1-6h'
                WHEN created_at > now() - INTERVAL '24 hours' THEN '6-24h'
                WHEN created_at > now() - INTERVAL '3 days'  THEN '1-3d'
                ELSE '>3d'
              END AS bucket,
              COUNT(*) AS cnt
           FROM wbom_staging_payments
           WHERE matched_employee_id IS NULL AND status='pending'
           GROUP BY bucket
           ORDER BY bucket""",
    )
    return {
        "report": "payment_reconciliation",
        "args": {"days": days},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {k: int(v or 0) for k, v in (summary or {}).items()},
        "rows": [
            {"age_bucket": r["bucket"], "unmatched_count": int(r["cnt"])}
            for r in age_buckets
        ],
    }


async def _b_cash_position(days: int = 30) -> dict:
    """Cash flow position — net by payment method over last N days."""
    cutoff = _today() - timedelta(days=days)
    # C1B: cash position reads from canonical fpe_cash_transactions
    rows = await fetch_all(
        """SELECT payout_method,
                  COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) AS total_in,
                  COALESCE(SUM(CASE WHEN amount >= 0 THEN amount END), 0) AS total_out,
                  COUNT(*) FILTER (WHERE amount < 0)  AS in_count,
                  COUNT(*) FILTER (WHERE amount >= 0) AS out_count
           FROM fpe_cash_transactions
           WHERE txn_date >= $1 AND transaction_status='final'
           GROUP BY payout_method
           ORDER BY payout_method""",
        cutoff,
    )
    total_in = sum(float(r["total_in"]) for r in rows)
    total_out = sum(float(r["total_out"]) for r in rows)
    return {
        "report": "cash_position",
        "args": {"days": days},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "since": cutoff.isoformat(),
            "total_in": total_in,
            "total_out": total_out,
            "net": total_in - total_out,
        },
        "rows": [
            {
                "payment_method": r["payment_method"],
                "in":  float(r["total_in"]),
                "out": float(r["total_out"]),
                "net": float(r["total_in"]) - float(r["total_out"]),
                "in_count":  int(r["in_count"]),
                "out_count": int(r["out_count"]),
            } for r in rows
        ],
    }


# ── registry + dispatch ─────────────────────────────────────────────────────
_BUILDERS: dict[str, Callable[..., Awaitable[dict]]] = {
    "daily_summary":          _b_daily_summary,
    "monthly_payroll":        _b_monthly_payroll,
    "escort_utilization":     _b_escort_utilization,
    "payment_reconciliation": _b_payment_reconciliation,
    "cash_position":          _b_cash_position,
}


def list_reports() -> list[str]:
    return sorted(_BUILDERS.keys())


def _coerce_args(name: str, args: dict) -> dict:
    """Coerce string args from query string into proper types."""
    out: dict[str, Any] = {}
    for k, v in (args or {}).items():
        if v is None or v == "":
            continue
        if k in ("year", "month", "days"):
            out[k] = int(v)
        elif k in ("date", "start", "end", "d"):
            out["d" if k == "d" else k] = (
                v if isinstance(v, date) else date.fromisoformat(str(v))
            )
        else:
            out[k] = v
    return out


def _cache_key(name: str, args: dict) -> str:
    items = sorted((k, str(v)) for k, v in args.items())
    return f"{name}:" + ",".join(f"{k}={v}" for k, v in items)


async def run_report(name: str, args: Optional[dict] = None,
                     requested_by: Optional[str] = None,
                     ttl: int = DEFAULT_TTL_SEC,
                     use_cache: bool = True) -> dict:
    args = _coerce_args(name, args or {})
    if name not in _BUILDERS:
        raise KeyError(f"unknown report: {name}")
    key = _cache_key(name, args)
    if use_cache:
        cached = await _cache_get(key)
        if cached:
            cached["_cached"] = True
            return cached
    t0 = time.time()
    err = None
    payload: dict = {}
    try:
        payload = await _BUILDERS[name](**args)
    except Exception as e:
        err = str(e)
        raise
    finally:
        dur_ms = int((time.time() - t0) * 1000)
        await _record_run(
            name, args, "ok" if err is None else "error",
            dur_ms, len(payload.get("rows", [])) if payload else 0,
            requested_by, err,
        )
    if use_cache:
        await _cache_set(key, name, payload, ttl)
    payload["_cached"] = False
    return payload


# ── rendering helpers ───────────────────────────────────────────────────────
def render_text(payload: dict, max_rows: int = 25) -> str:
    """Render a report as Bangla-friendly plain text for WhatsApp."""
    name = payload.get("report", "?")
    args = payload.get("args", {})
    summary = payload.get("summary", {})
    rows = payload.get("rows", [])
    head = f"📊 {name} ({', '.join(f'{k}={v}' for k, v in args.items())})"
    lines = [head, "─" * 32]
    for k, v in summary.items():
        if isinstance(v, dict):
            lines.append(f"• {k}:")
            for sk, sv in v.items():
                lines.append(f"   - {sk}: {_fmt(sv)}")
        else:
            lines.append(f"• {k}: {_fmt(v)}")
    if rows:
        lines.append("")
        lines.append(f"rows ({min(len(rows), max_rows)}/{len(rows)}):")
        for r in rows[:max_rows]:
            lines.append("  " + " | ".join(f"{k}={_fmt(v)}" for k, v in r.items()))
        if len(rows) > max_rows:
            lines.append(f"  … আরও {len(rows) - max_rows}টি")
    return "\n".join(lines)


def render_csv(payload: dict) -> str:
    rows = payload.get("rows", [])
    if not rows:
        return ""
    import csv
    import io
    keys = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=keys)
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k) for k in keys})
    return buf.getvalue()


def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}" if abs(v) >= 0.01 else "0"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v) if v is not None else "—"


# ── scheduler entry ─────────────────────────────────────────────────────────
async def job_daily_admin_digest() -> dict:
    """B17.5 — runs at 08:00 Asia/Dhaka, enqueue daily summary to admin."""
    import os
    from modules import outbound
    admins = [a.strip() for a in (os.getenv("ADMIN_NUMBERS", "") or "").split(",") if a.strip()]
    if not admins:
        return {"status": "ok", "skipped": "no admin"}
    today = _today()
    try:
        payload = await run_report("daily_summary", {"date": today}, use_cache=False)
    except Exception as e:
        log.exception(f"[reports] digest build failed: {e}")
        return {"status": "error", "error": str(e)}
    text = render_text(payload, max_rows=15)
    key_date = today.isoformat().replace("-", "")
    sent = 0
    for adm in admins:
        try:
            await outbound.enqueue(
                adm, text, source_bridge="bridge2", purpose="daily-digest",
                idempotency_key=f"daily-digest-{key_date}-{adm}",
            )
            sent += 1
        except Exception as e:
            log.warning(f"[reports] digest enqueue failed for {adm}: {e}")
    return {"status": "ok", "sent": sent, "admins": len(admins)}


async def cleanup_cache() -> int:
    """Delete expired cache rows. Returns rows deleted."""
    res = await execute("DELETE FROM fazle_report_cache WHERE expires_at < now()")
    try:
        return int(str(res).rsplit(" ", 1)[-1])
    except Exception:
        return 0
