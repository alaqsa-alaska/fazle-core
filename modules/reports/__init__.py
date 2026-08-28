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
              COUNT(*) FILTER (WHERE status IN ('confirmed','running'))    AS active,
              COUNT(*) FILTER (WHERE status='completed')                  AS completed
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
                "payment_method": r["payout_method"],
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
                  COUNT(*) FILTER (WHERE p.status='completed') AS completed,
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
                "payment_method": r["payout_method"],
                "in":  float(r["total_in"]),
                "out": float(r["total_out"]),
                "net": float(r["total_in"]) - float(r["total_out"]),
                "in_count":  int(r["in_count"]),
                "out_count": int(r["out_count"]),
            } for r in rows
        ],
    }


async def _b_unresolved_fpe_employees() -> dict:
    """Read-only report: fpe_employees rows needing human review for WBOM
    linkage. Distinguishes truly-unlinked rows from rows that are already
    correctly linked but still carry a stale resolution_status (Session 20
    Phase 2 finding, S20-10 governance entry) -- never conflates the two.
    Phone matching uses last-10-digit, digit-only comparison (same
    normalization style as match_employee() in modules/payment_ingest),
    checked against wbom_employees.employee_mobile/bkash_number/nagad_number
    -- the same three fields payment_ingest and fazle_payroll_engine.employee
    already treat as authoritative WBOM phone-identity anchors elsewhere in
    this codebase, not a new matching rule invented for this report.

    NO WRITES. This function issues one SELECT only. No auto-linking, no
    guessing, no bulk mutation -- output is for human review through the
    existing wbom-create-link.html / wbom-link-review.html tools (report
    field names documented below map to those tools' candidate shapes;
    wiring this report's live output into those static-array tools is a
    separate, not-yet-scoped task).

    Column mapping for future wiring:
      no_wbom_match       -> wbom-create-link.html: fpe_name==full_name,
                              phone, salary, joining_date, created_source, txn_count
      wbom_match_unlinked -> wbom-link-review.html: fpe_id, fpe_name, wbom_match_id==wbom_id,
                              wbom_match_name==wbom_name, phone, name_mismatch==mismatch
    """
    rows = await fetch_all(
        """
        SELECT
            fe.id AS fpe_id, fe.full_name AS fpe_name,
            fe.primary_phone, fe.employee_id_phone,
            fe.resolution_status, fe.wbom_employee_id,
            fe.created_source, fe.designation, fe.basic_salary, fe.joining_date,
            (SELECT COUNT(*) FROM fpe_cash_transactions t WHERE t.employee_id = fe.id) AS txn_count,
            wm.employee_id AS wbom_match_id,
            wm.employee_name AS wbom_match_name,
            wm.employee_mobile AS wbom_match_mobile
        FROM fpe_employees fe
        LEFT JOIN LATERAL (
            SELECT we.employee_id, we.employee_name, we.employee_mobile
            FROM wbom_employees we
            WHERE RIGHT(regexp_replace(we.employee_mobile, '\\D', '', 'g'), 10)
                    = RIGHT(regexp_replace(COALESCE(fe.primary_phone, fe.employee_id_phone, ''), '\\D', '', 'g'), 10)
               OR RIGHT(regexp_replace(COALESCE(we.bkash_number, ''), '\\D', '', 'g'), 10)
                    = RIGHT(regexp_replace(COALESCE(fe.primary_phone, fe.employee_id_phone, ''), '\\D', '', 'g'), 10)
               OR RIGHT(regexp_replace(COALESCE(we.nagad_number, ''), '\\D', '', 'g'), 10)
                    = RIGHT(regexp_replace(COALESCE(fe.primary_phone, fe.employee_id_phone, ''), '\\D', '', 'g'), 10)
            LIMIT 1
        ) wm ON TRUE
        WHERE fe.resolution_status = 'unresolved' OR fe.wbom_employee_id IS NULL
        ORDER BY fe.id
        """
    )

    rows_out: list[dict] = []
    for r in rows:
        base = {
            "fpe_id": r["fpe_id"],
            "fpe_name": r["fpe_name"],
            "resolution_status": r["resolution_status"],
            "phone": r["primary_phone"] or r["employee_id_phone"],
            "salary": float(r["basic_salary"]) if r["basic_salary"] is not None else None,
            "joining_date": r["joining_date"].isoformat() if r["joining_date"] else None,
            "created_source": r["created_source"],
            "designation": r["designation"],
            "txn_count": int(r["txn_count"]),
        }
        if r["wbom_employee_id"] is not None:
            rows_out.append({
                **base, "bucket": "linked_but_mislabeled",
                "wbom_employee_id": r["wbom_employee_id"],
                "wbom_match_id": None, "wbom_match_name": None, "name_mismatch": None,
            })
        elif r["wbom_match_id"] is not None:
            fpe_name = (r["fpe_name"] or "").strip().lower()
            wbom_name = (r["wbom_match_name"] or "").strip().lower()
            rows_out.append({
                **base, "bucket": "wbom_match_unlinked",
                "wbom_employee_id": None,
                "wbom_match_id": r["wbom_match_id"],
                "wbom_match_name": r["wbom_match_name"],
                "name_mismatch": fpe_name != wbom_name,
                "phone": r["wbom_match_mobile"],
            })
        else:
            rows_out.append({
                **base, "bucket": "no_wbom_match",
                "wbom_employee_id": None,
                "wbom_match_id": None, "wbom_match_name": None, "name_mismatch": None,
            })

    return {
        "report": "unresolved_fpe_employees",
        "args": {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "no_wbom_match_count": sum(1 for x in rows_out if x["bucket"] == "no_wbom_match"),
            "wbom_match_unlinked_count": sum(1 for x in rows_out if x["bucket"] == "wbom_match_unlinked"),
            "linked_but_mislabeled_count": sum(1 for x in rows_out if x["bucket"] == "linked_but_mislabeled"),
            "total_reviewed": len(rows_out),
        },
        "rows": rows_out,
    }


# ── Stage G18: Business Communication Summary (twice-daily target) ──────────
async def _b_business_communication_summary(hours: int = 12) -> dict:
    """Bounded, metadata-first business communication summary -- Stage
    G18. Reuses the exact domain-label convention Stage E's
    /api/whatsapp/conversations/attention already established
    (workflow_triggered/intent_detected = recruitment/escort_duty/
    attendance/payment_due), identity_role for client/office-internal
    (Stage G1's authority-lane role vocabulary), and the same "requires
    action" state sources Stage E6/E7 proved deterministic (attendance
    approved_at, cash transaction_status). Never dumps raw message
    bodies -- counts + reference IDs only, so an admin can request
    details separately (get_conversation_history)."""
    since = f"NOW() - INTERVAL '{int(hours)} hours'"

    domain_counts = await fetch_all(
        f"""
        SELECT
          CASE
            WHEN workflow_triggered='recruitment' OR intent_detected='recruitment' THEN 'recruitment'
            WHEN workflow_triggered='escort_duty' OR intent_detected='escort_duty' THEN 'escort'
            WHEN workflow_triggered='attendance' OR intent_detected='attendance' THEN 'attendance'
            WHEN workflow_triggered='payment_due' OR intent_detected='payment_due' THEN 'cash'
            WHEN identity_role IN ('vip_client','security_service_client','client_escort_buyer','escort_client','repeat_client') THEN 'client'
            WHEN identity_role IN ('internal_coordinator','internal_operation','employee_internal') THEN 'office_internal'
            WHEN identity_role IN ('employee','escort','security_guard','supervisor') THEN 'employee'
            ELSE 'other'
          END AS category,
          COUNT(DISTINCT conversation_key) AS contacts
        FROM wbom_whatsapp_messages
        WHERE received_at >= {since}
        GROUP BY 1
        ORDER BY 2 DESC
        """
    )
    complaints = await fetch_val(
        f"SELECT COUNT(DISTINCT conversation_key) FROM wbom_whatsapp_messages "
        f"WHERE received_at >= {since} AND (workflow_triggered='complaint' OR intent_detected='complaint')"
    )
    office_group_msgs = await fetch_val(
        f"SELECT COUNT(*) FROM wbom_whatsapp_messages WHERE received_at >= {since} AND conversation_key LIKE '%@g.us%'"
    )
    replies_sent = await fetch_val(
        f"SELECT COUNT(*) FROM wbom_whatsapp_messages WHERE received_at >= {since} AND direction='outbound' AND actor_type IN ('automation','hermes')"
    )
    pending_drafts = await fetch_all(
        "SELECT id, recipient, source, intent, created_at FROM fazle_draft_replies "
        "WHERE status IN ('pending','pending_selfie','edited') ORDER BY created_at DESC LIMIT 15"
    )
    pending_drafts_total = await fetch_val("SELECT COUNT(*) FROM fazle_draft_replies WHERE status IN ('pending','pending_selfie','edited')")

    attendance_needs_action = await fetch_val(
        "SELECT COUNT(*) FROM wbom_attendance WHERE approved_at IS NULL"
    )
    cash_needs_action = await fetch_val(
        "SELECT COUNT(*) FROM fpe_cash_transactions WHERE transaction_status='pending'"
    )
    escort_needs_action = await fetch_val(
        "SELECT COUNT(*) FROM wbom_escort_programs WHERE status='draft'"
    )
    pending_business_actions = await fetch_all(
        "SELECT id, domain, action_type, business_reference, created_at FROM hermes_action_approvals "
        "WHERE status='pending' ORDER BY created_at DESC LIMIT 15"
    )

    return {
        "report": "business_communication_summary",
        "args": {"hours": hours},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "window_hours": hours,
            "by_category": {r["category"]: r["contacts"] for r in domain_counts},
            "complaints": int(complaints or 0),
            "office_group_messages": int(office_group_msgs or 0),
            "autonomous_replies_sent": int(replies_sent or 0),
            "pending_drafts_total": int(pending_drafts_total or 0),
            "attendance_needs_action": int(attendance_needs_action or 0),
            "cash_needs_action": int(cash_needs_action or 0),
            "escort_needs_action": int(escort_needs_action or 0),
            "pending_business_proposals": len(pending_business_actions),
        },
        "rows": [dict(r) for r in pending_drafts] + [dict(r) for r in pending_business_actions],
    }


# ── Stage G21: System Observation Report (twice-daily target, READ-only) ────
async def _b_system_observation() -> dict:
    """Bounded system-health observation -- Stage G19/G21. Reuses
    app.main._build_health() (the same probe set job_health_summary
    already alerts from) plus queue/DLQ/watchdog-adjacent signals already
    tracked in fazle_scheduled_jobs. READ-only: this builder queries
    state, it never modifies anything -- Stage G19's explicit boundary."""
    from app.main import _build_health

    health = await _build_health(deep=False)
    degraded = {k: v for k, v in health["probes"].items() if v.get("status") not in ("ok", "skipped")}

    job_rows = await fetch_all(
        "SELECT job_name, last_status, last_run_at, run_count, "
        "EXTRACT(EPOCH FROM (NOW() - last_run_at))::int AS seconds_since_last_run "
        "FROM fazle_scheduled_jobs ORDER BY job_name"
    )
    stale_jobs = [
        dict(r) for r in job_rows
        if r["seconds_since_last_run"] is not None and r["seconds_since_last_run"] > 24 * 3600
    ]
    failed_jobs = [dict(r) for r in job_rows if r["last_status"] == "error"]

    incident_rows = await fetch_all(
        "SELECT severity, source, title, COUNT(*) AS n FROM agent.incidents "
        "WHERE resolved_at IS NULL GROUP BY severity, source, title "
        "ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'error' THEN 1 ELSE 2 END, title LIMIT 20"
    )

    return {
        "report": "system_observation",
        "args": {},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "overall_status": health["status"],
            "degraded_probe_count": len(degraded),
            "degraded_probes": list(degraded.keys()),
            "stale_job_count": len(stale_jobs),
            "failed_job_count": len(failed_jobs),
            "open_incident_count": len(incident_rows),
        },
        "rows": [{"probe": k, **v} for k, v in degraded.items()] + stale_jobs + failed_jobs + [dict(r) for r in incident_rows],
    }


# ── registry + dispatch ─────────────────────────────────────────────────────
_BUILDERS: dict[str, Callable[..., Awaitable[dict]]] = {
    "business_communication_summary": _b_business_communication_summary,
    "system_observation":             _b_system_observation,
    "daily_summary":          _b_daily_summary,
    "monthly_payroll":        _b_monthly_payroll,
    "escort_utilization":     _b_escort_utilization,
    "payment_reconciliation": _b_payment_reconciliation,
    "cash_position":          _b_cash_position,
    "unresolved_fpe_employees": _b_unresolved_fpe_employees,
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


# ── Stage G18/G24 (2026-08-28): Business Communication Summary, twice daily ──
async def job_business_summary_report() -> dict:
    """Stage G18 -- routine, twice-daily (see modules.scheduler's own
    CronTrigger registration for the exact times), reusing this file's
    existing report-builder/cache/audit infrastructure (run_report/
    render_text) and the existing outbound.enqueue delivery path every
    other scheduled admin message already uses -- not a new
    reporting/delivery mechanism. This is a ROUTINE report, deliberately
    separate from the existing urgent-alert jobs (dlq_alert,
    bridge_watchdog, health_summary) -- Stage G22's explicit
    routine-vs-urgent separation; none of those jobs are touched here."""
    import os
    from modules import outbound
    admins = [a.strip() for a in (os.getenv("ADMIN_NUMBERS", "") or "").split(",") if a.strip()]
    if not admins:
        return {"status": "ok", "skipped": "no admin"}
    try:
        payload = await run_report("business_communication_summary", {"hours": 12}, use_cache=False)
    except Exception as e:
        log.exception(f"[reports] business summary build failed: {e}")
        return {"status": "error", "error": str(e)}
    text = "📨 " + render_text(payload, max_rows=15)
    slot_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    sent = 0
    for adm in admins:
        try:
            await outbound.enqueue(
                adm, text, source_bridge="bridge2", purpose="business-summary",
                idempotency_key=f"business-summary-{slot_key}-{adm}",
            )
            sent += 1
        except Exception as e:
            log.warning(f"[reports] business summary enqueue failed for {adm}: {e}")
    return {"status": "ok", "sent": sent, "admins": len(admins)}


async def job_system_observation_report() -> dict:
    """Stage G19/G21/G24 -- routine, twice-daily. READ-only by
    construction: _b_system_observation() only queries state (Stage
    G19's explicit "system monitoring must remain READ/REPORT" boundary,
    Stage G21's "may recommend fixes but must NOT apply them") -- this
    job has no code path that can mutate anything."""
    import os
    from modules import outbound
    admins = [a.strip() for a in (os.getenv("ADMIN_NUMBERS", "") or "").split(",") if a.strip()]
    if not admins:
        return {"status": "ok", "skipped": "no admin"}
    try:
        payload = await run_report("system_observation", {}, use_cache=False)
    except Exception as e:
        log.exception(f"[reports] system observation build failed: {e}")
        return {"status": "error", "error": str(e)}
    text = "🩺 " + render_text(payload, max_rows=15)
    slot_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    sent = 0
    for adm in admins:
        try:
            await outbound.enqueue(
                adm, text, source_bridge="bridge2", purpose="system-observation",
                idempotency_key=f"system-observation-{slot_key}-{adm}",
            )
            sent += 1
        except Exception as e:
            log.warning(f"[reports] system observation enqueue failed for {adm}: {e}")
    return {"status": "ok", "sent": sent, "admins": len(admins)}


async def cleanup_cache() -> int:
    """Delete expired cache rows. Returns rows deleted."""
    res = await execute("DELETE FROM fazle_report_cache WHERE expires_at < now()")
    try:
        return int(str(res).rsplit(" ", 1)[-1])
    except Exception:
        return 0
