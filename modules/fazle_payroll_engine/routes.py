"""
Fazle Payroll Engine — FastAPI router.

All routes under /api/fpe/ — registered in app/main.py.
Auth: uses the same X-Internal-Key header as the rest of fazle-core.

Endpoints:
  POST  /api/fpe/ingest              — ingest a WhatsApp message (from bridge webhooks)
  GET   /api/fpe/transactions        — list transactions (filterable)
  GET   /api/fpe/transactions/{id}   — single transaction
  POST  /api/fpe/transactions/{id}/reverse — create reversal
  POST  /api/fpe/transactions/manual — create manual transaction
  GET   /api/fpe/employees           — list employees
  GET   /api/fpe/employees/{id}      — single employee + ledger summary
  GET   /api/fpe/ledger/{emp_id}     — full ledger for employee
  GET   /api/fpe/unmatched           — review unmatched messages
  POST  /api/fpe/unmatched/{id}/mark-reviewed
  GET   /api/fpe/sync/status         — sync checkpoint status
  POST  /api/fpe/sync/trigger        — trigger immediate historical sync pass
  GET   /api/fpe/health              — module health check
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.database import execute, fetch_all, fetch_one, fetch_val
from .accounting import create_transaction, reverse_transaction
from .employee import _resolve_canonical, create_employee_manual
from .historical_sync import run_historical_sync_once
from .ingestion import ingest_message
from .normalizer import normalize_bd_phone, normalize_name, normalize_search_text, collapse_search_text
from .models import (
    IncomeCreateRequest,
    IngestionRequest,
    ManualTxnRequest,
    ReversalRequest,
    TxnCategory,
    TransactionCreateRequest,
)

log = logging.getLogger("fazle.fpe.routes")

router = APIRouter(prefix="/api/fpe", tags=["fazle_payroll_engine"])

REVIEW_QUEUE_RETENTION_HOURS = 24
EMPLOYEE_INACTIVE_DAYS = 90

# Local copy of require_api_key to avoid circular import (app.main → fpe → app.main)
_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_api_key(key: str = Depends(_API_KEY_HEADER)):
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return key
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin and admin.get("status") == "active":
                return key
        except Exception:
            pass
    raise HTTPException(status_code=403, detail="Unauthorized")


def _review_queue_candidate_sql(alias: str = "u") -> str:
    """Rows eligible for the accounting review queue."""
    return (
        f"{alias}.detected_amount IS NOT NULL "
        f"AND COALESCE(BTRIM({alias}.detected_employee_name), '') <> '' "
        f"AND COALESCE(BTRIM({alias}.detected_payout_phone), '') <> '' "
        f"AND COALESCE(BTRIM({alias}.detected_payout_method), '') <> '' "
        f"AND LOWER({alias}.detected_payout_method) <> 'unknown'"
    )


async def _cleanup_stale_review_queue_rows() -> dict[str, int]:
    """
    Hard-delete stale review-queue rows older than 24 hours.

    This only targets transient review data:
      - fpe_unmatched_messages rows (review queue storage)
      - related fpe_review_audit_logs rows
    It never touches canonical ledger transactions.
    """
    cutoff = datetime.utcnow() - timedelta(hours=REVIEW_QUEUE_RETENTION_HOURS)
    stale_rows = await fetch_all(
        "SELECT id FROM fpe_unmatched_messages WHERE created_at < $1 LIMIT 5000",
        cutoff,
    )
    stale_ids = [int(r["id"]) for r in stale_rows]
    if not stale_ids:
        return {"deleted_reviews": 0, "deleted_audit_logs": 0}

    deleted_logs = await fetch_val(
        "SELECT COUNT(*) FROM fpe_review_audit_logs WHERE review_item_id = ANY($1::bigint[])",
        stale_ids,
    ) or 0
    await execute(
        "DELETE FROM fpe_review_audit_logs WHERE review_item_id = ANY($1::bigint[])",
        stale_ids,
    )
    await execute(
        "DELETE FROM fpe_unmatched_messages WHERE id = ANY($1::bigint[])",
        stale_ids,
    )
    log.info(
        "[fpe.cleanup] deleted %d stale review rows and %d audit rows older than %dh",
        len(stale_ids),
        int(deleted_logs),
        REVIEW_QUEUE_RETENTION_HOURS,
    )
    return {"deleted_reviews": len(stale_ids), "deleted_audit_logs": int(deleted_logs)}


async def _mark_stale_employees_inactive() -> int:
    """Mark employees inactive when their latest payment is older than 90 days."""
    cutoff = date.today() - timedelta(days=EMPLOYEE_INACTIVE_DAYS)
    rows = await fetch_all(
        """
        WITH latest_paid AS (
            SELECT employee_id, MAX(txn_date) AS last_payment_date
            FROM fpe_cash_transactions
            WHERE employee_id IS NOT NULL
              AND NOT is_reversal
              AND deleted_at IS NULL
            GROUP BY employee_id
        )
        SELECT e.id
        FROM fpe_employees e
        JOIN latest_paid lp ON lp.employee_id = e.id
        WHERE e.status = 'active'
          AND lp.last_payment_date < $1
        """,
        cutoff,
    )
    employee_ids = [int(r["id"]) for r in rows]
    if not employee_ids:
        return 0

    await execute(
        "UPDATE fpe_employees SET status = 'inactive', updated_at = NOW() "
        "WHERE id = ANY($1::int[]) AND status = 'active'",
        employee_ids,
    )
    log.info(
        "[fpe.maintenance] marked %d employees inactive using %d-day payment inactivity window",
        len(employee_ids),
        EMPLOYEE_INACTIVE_DAYS,
    )
    return len(employee_ids)


async def _resolve_review_employee_candidate(
    *,
    detected_name: Optional[str],
    detected_phone: Optional[str],
) -> Optional[dict]:
    """
    Resolve a review item to one canonical employee using exact evidence only.

    This keeps approve/reject actions one-click for admins without guessing:
      1. exact phone / employee-id / phone alias
      2. exact normalized name, only when it resolves to one canonical employee
    """
    phone = normalize_bd_phone(detected_phone) if detected_phone else None
    if phone:
        phone_row = await fetch_one(
            """
            SELECT e.id, e.full_name, e.employee_id_phone, e.primary_phone, e.canonical_employee_id
            FROM fpe_employees e
            LEFT JOIN fpe_employee_aliases a
              ON a.employee_id = e.id AND a.alias_type = 'phone'
            WHERE e.status = 'active'
              AND (
                e.employee_id_phone = $1
                OR e.primary_phone = $1
                OR a.alias_value = $1
              )
            ORDER BY e.id ASC
            LIMIT 1
            """,
            phone,
        )
        if phone_row:
            canon = await _resolve_canonical(dict(phone_row))
            return {
                "employee_id": canon["id"],
                "employee_name": canon.get("full_name"),
                "employee_id_phone": canon.get("employee_id_phone"),
                "match_type": "exact_phone",
            }

    if detected_name:
        name_norm = normalize_name(detected_name)
        if name_norm:
            name_rows = await fetch_all(
                """
                SELECT id, canonical_employee_id, full_name, primary_phone, employee_id_phone
                FROM fpe_employees
                WHERE status = 'active'
                  AND (
                    name_normalized = $1
                    OR lower(full_name) = lower($2)
                    OR id IN (
                        SELECT employee_id
                        FROM fpe_employee_aliases
                        WHERE alias_type = 'name' AND alias_value = $1
                    )
                  )
                LIMIT 5
                """,
                name_norm,
                detected_name.strip(),
            )
            canon_map: dict[int, dict] = {}
            for row in name_rows:
                canon = await _resolve_canonical(dict(row))
                canon_map[canon["id"]] = canon
            if len(canon_map) == 1:
                canon = next(iter(canon_map.values()))
                return {
                    "employee_id": canon["id"],
                    "employee_name": canon.get("full_name"),
                    "employee_id_phone": canon.get("employee_id_phone"),
                    "match_type": "exact_name",
                }

    return None


# ── Health ────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    total_msgs = await fetch_val("SELECT COUNT(*) FROM fpe_wa_messages") or 0
    total_txns = await fetch_val("SELECT COUNT(*) FROM fpe_cash_transactions WHERE NOT is_reversal") or 0
    pending = await fetch_val(
        "SELECT COUNT(*) FROM fpe_message_processing_state WHERE status='pending'"
    ) or 0
    return {
        "status": "ok",
        "total_messages_ingested": total_msgs,
        "total_transactions": total_txns,
        "pending_processing": pending,
    }


# ── Ingest ────────────────────────────────────────────────────────────────────

@router.post("/ingest", dependencies=[Depends(_require_api_key)])
async def ingest(req: IngestionRequest):
    """Ingest a single WhatsApp message from any source."""
    fpe_id = await ingest_message(req)
    if fpe_id is None:
        return {"status": "duplicate", "fpe_wa_message_id": None}
    return {"status": "ingested", "fpe_wa_message_id": fpe_id}


# ── Transactions ──────────────────────────────────────────────────────────────

@router.get("/transactions", dependencies=[Depends(_require_api_key)])
async def list_transactions(
    employee_id: Optional[int] = Query(None),
    period: Optional[str] = Query(None, description="YYYY-MM"),
    method: Optional[str] = Query(None),
    txn_category: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=200),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    # page/page_size override limit/offset when provided (preferred new contract).
    if page is not None or page_size is not None:
        ps = page_size or 20
        pg = page or 1
        limit = ps
        offset = (pg - 1) * ps

    conditions = ["NOT is_reversal", "deleted_at IS NULL"]
    params: list = []
    i = 1

    if employee_id:
        conditions.append(f"employee_id = ${i}")
        params.append(employee_id)
        i += 1
    if period:
        conditions.append(f"accounting_period = ${i}")
        params.append(period)
        i += 1
    if method:
        conditions.append(f"payout_method = ${i}")
        params.append(method)
        i += 1
    if txn_category:
        conditions.append(f"txn_category = ${i}")
        params.append(txn_category)
        i += 1
    if date_from:
        conditions.append(f"txn_date >= ${i}")
        params.append(date_from)
        i += 1
    if date_to:
        conditions.append(f"txn_date <= ${i}")
        params.append(date_to)
        i += 1

    where = " AND ".join(conditions)
    params += [limit, offset]

    rows = await fetch_all(
        f"""
        SELECT
            t.id,
            t.txn_ref,
            t.employee_id,
            t.employee_name_raw,
            t.amount,
            t.payout_phone,
            t.payout_method,
            t.txn_date,
            t.txn_category,
            t.accounting_period,
            t.is_reversal,
            t.created_at,
            -- Authoritative employee identity (resolved via canonical soft-link).
            -- Spec: visible Employee ID = employee_id_phone (NOT employee_code).
            -- Display name fallback: parsed raw → canonical/record → '(unknown)'.
            c.id           AS canonical_employee_id,
            c.employee_code AS employee_code,
            COALESCE(t.employee_name_raw, c.full_name, e.full_name, '(unknown)')
                           AS employee_display_name,
            COALESCE(c.employee_id_phone, e.employee_id_phone,
                     c.primary_phone, e.primary_phone, t.payout_phone)
                           AS employee_display_id_phone,
            COALESCE(c.primary_phone, e.primary_phone, t.payout_phone)
                           AS employee_display_phone
        FROM fpe_cash_transactions t
        LEFT JOIN fpe_employees e ON e.id = t.employee_id
        LEFT JOIN fpe_employees c
               ON c.id = COALESCE(e.canonical_employee_id, e.id)
        WHERE {where.replace('employee_id', 't.employee_id')
                    .replace('accounting_period', 't.accounting_period')
                    .replace('payout_method', 't.payout_method')
                    .replace('txn_category', 't.txn_category')
                    .replace('NOT is_reversal', 'NOT t.is_reversal')
                    .replace('deleted_at IS NULL', 't.deleted_at IS NULL')}
        ORDER BY t.txn_date DESC, t.id DESC
        LIMIT ${i} OFFSET ${i+1}
        """,
        *params,
    )
    summary_row = await fetch_one(
        f"""SELECT COUNT(*)::bigint AS total,
                   COALESCE(SUM(amount),0)::numeric AS total_amount,
                   MIN(txn_date) AS first_txn,
                   MAX(txn_date) AS last_txn
              FROM fpe_cash_transactions WHERE {where}""",
        *params[:-2],
    )
    summary = dict(summary_row) if summary_row else {"total": 0, "total_amount": 0, "first_txn": None, "last_txn": None}
    total = summary["total"]
    page_size_resp = limit
    page_resp = (offset // limit) + 1 if limit else 1
    pages_resp = (total + limit - 1) // limit if (total and limit) else 1
    return {
        "total": total,
        "page": page_resp,
        "page_size": page_size_resp,
        "pages": pages_resp,
        "summary": {
            "total": int(summary["total"] or 0),
            "total_amount": float(summary["total_amount"] or 0),
            "first_txn": summary["first_txn"].isoformat() if summary["first_txn"] else None,
            "last_txn": summary["last_txn"].isoformat() if summary["last_txn"] else None,
        },
        "transactions": [dict(r) for r in rows],
    }


@router.get("/transactions/{txn_id}", dependencies=[Depends(_require_api_key)])
async def get_transaction(txn_id: int):
    row = await fetch_one(
        """
        SELECT t.*,
               c.id           AS canonical_employee_id,
               c.employee_code AS employee_code,
               COALESCE(t.employee_name_raw, c.full_name, e.full_name, '(unknown)')
                              AS employee_display_name,
               COALESCE(c.employee_id_phone, e.employee_id_phone,
                        c.primary_phone, e.primary_phone, t.payout_phone)
                              AS employee_display_id_phone,
               COALESCE(c.primary_phone, e.primary_phone, t.payout_phone)
                              AS employee_display_phone
        FROM fpe_cash_transactions t
        LEFT JOIN fpe_employees e ON e.id = t.employee_id
        LEFT JOIN fpe_employees c
               ON c.id = COALESCE(e.canonical_employee_id, e.id)
        WHERE t.id = $1
        """,
        txn_id,
    )
    if not row:
        raise HTTPException(404, "Transaction not found")
    return dict(row)


@router.post("/transactions/{txn_id}/reverse", dependencies=[Depends(_require_api_key)])
async def reverse_txn(txn_id: int, req: ReversalRequest):
    try:
        txn = await reverse_transaction(txn_id, req.reason, req.created_by)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": "reversed", "reversal": txn.model_dump()}


@router.post("/transactions/manual", dependencies=[Depends(_require_api_key)])
async def create_manual_txn(req: ManualTxnRequest):
    emp = await fetch_one(
        "SELECT id, status FROM fpe_employees WHERE id = $1",
        req.employee_id,
    )
    if not emp:
        raise HTTPException(400, "Employee not found. Use Add New Employee first.")
    if emp["status"] != "active":
        raise HTTPException(400, "Selected employee is inactive.")

    create_req = TransactionCreateRequest(
        employee_id=req.employee_id,
        amount=req.amount,
        payout_method=req.payout_method,
        payout_phone=req.payout_phone,
        txn_date=req.txn_date,
        txn_category=req.txn_category,
        source_message_text=f"manual: {req.reason}",
        created_by="admin_manual",
    )
    txn = await create_transaction(create_req)
    return {"status": "created", "transaction": txn.model_dump()}


# ── Employees ─────────────────────────────────────────────────────────────────

def _looks_invalid_name(s: Optional[str]) -> bool:
    """Phone-numeric / placeholder detector — mirrors employee._is_valid_human_name."""
    if not s:
        return True
    v = s.strip()
    if len(v) < 2:
        return True
    if v.lower() in {"unknown", "unnamed", "none", "n/a", "na", "(unknown)", "(unnamed)"}:
        return True
    import re as _re
    digits_only = _re.sub(r"[\s\-\+\(\)\.]", "", v)
    if digits_only.isdigit():
        return True
    if not _re.search(r"[A-Za-z\u0980-\u09FF]", v):
        return True
    return False


def _pick_best_name(*candidates: Optional[str]) -> Optional[str]:
    """Choose the richest valid human name (longest token count, then length)."""
    valid = [c.strip() for c in candidates if c and not _looks_invalid_name(c)]
    if not valid:
        return None
    valid.sort(key=lambda s: (len(s.split()), len(s)), reverse=True)
    return valid[0]


@router.get("/employees", dependencies=[Depends(_require_api_key)])
async def list_employees(
    status: str = Query("active"),
    q: Optional[str] = Query(None, description="search by name / phone / id_phone"),
    employee_id: Optional[int] = Query(None, gt=0),
    page: Optional[int] = Query(None, ge=1),
    page_size: Optional[int] = Query(None, ge=1, le=500),
    limit: int = Query(200, le=500),
    offset: int = Query(0),
    sort: str = Query("name_asc", regex="^(name_asc|name_desc|id_asc|id_desc)$"),
):
    """List CANONICAL employees with aggregated totals + best display name.

    - Excludes duplicate rows (canonical_employee_id IS NOT NULL).
    - Aggregates transactions across the canonical + every member that resolves
      to it (so merged employees show ONE merged row).
    - Display name picks the richest valid human name among full_name + name aliases.
    """
    if page is not None or page_size is not None:
        ps = page_size or 50
        pg = page or 1
        limit = ps
        offset = (pg - 1) * ps

    await _mark_stale_employees_inactive()

    params: list = [status]
    where = ["e.status = $1", "e.canonical_employee_id IS NULL"]
    if employee_id is not None:
        params.append(employee_id)
        where.append(f"e.id = ${len(params)}")
    if q:
        params.append(f"%{q.strip()}%")
        i = len(params)
        where.append(
            f"(e.full_name ILIKE ${i} OR e.employee_id_phone ILIKE ${i} "
            f"OR e.primary_phone ILIKE ${i} OR e.employee_code ILIKE ${i} "
            f"OR EXISTS (SELECT 1 FROM fpe_employee_aliases a "
            f"           WHERE a.employee_id = e.id AND a.alias_value ILIKE ${i}))"
        )
    where_sql = " AND ".join(where)

    total = await fetch_val(
        f"SELECT COUNT(*) FROM fpe_employees e WHERE {where_sql}", *params
    )

    params.extend([limit, offset])
    # Sort key: rows with a real letter-starting name (English or Bangla) first, sorted A-Z;
    # rows that fall back to phone/id come last. This matches the UX "Employee Name A→Z".
    _name_expr_e = "COALESCE(NULLIF(e.full_name, ''), e.primary_phone, e.employee_id_phone)"
    _name_expr_c = "COALESCE(NULLIF(c.full_name, ''), c.primary_phone, c.employee_id_phone)"
    _has_letter = "(COALESCE(e.full_name,'') ~ '[A-Za-zঀ-৿]')"
    _has_letter_c = "(COALESCE(c.full_name,'') ~ '[A-Za-zঀ-৿]')"
    inner_order = {
        "name_asc":  f"({_has_letter}) DESC, LOWER({_name_expr_e}) ASC NULLS LAST, e.id ASC",
        "name_desc": f"({_has_letter}) DESC, LOWER({_name_expr_e}) DESC NULLS LAST, e.id ASC",
        "id_asc":    "e.id ASC",
        "id_desc":   "e.id DESC",
    }[sort]
    outer_order = {
        "name_asc":  f"({_has_letter_c}) DESC, LOWER({_name_expr_c}) ASC NULLS LAST, c.id ASC",
        "name_desc": f"({_has_letter_c}) DESC, LOWER({_name_expr_c}) DESC NULLS LAST, c.id ASC",
        "id_asc":    "c.id ASC",
        "id_desc":   "c.id DESC",
    }[sort]
    rows = await fetch_all(
        f"""
        WITH canon AS (
          SELECT e.id, e.employee_code, e.full_name, e.primary_phone,
                 e.employee_id_phone, e.department, e.status,
                 e.created_source, e.created_at
          FROM fpe_employees e
          WHERE {where_sql}
          ORDER BY {inner_order}
          LIMIT ${len(params) - 1} OFFSET ${len(params)}
        ),
        members AS (
          SELECT c.id AS canon_id, m.id AS member_id
          FROM canon c
          JOIN fpe_employees m
            ON m.id = c.id OR m.canonical_employee_id = c.id
        ),
        agg AS (
          SELECT mb.canon_id,
                 COUNT(t.id)::bigint        AS total_transactions,
                 COALESCE(SUM(t.amount),0)  AS total_paid,
                 MAX(t.txn_date)            AS last_payment_date
          FROM members mb
          LEFT JOIN fpe_cash_transactions t
                 ON t.employee_id = mb.member_id AND t.is_reversal = FALSE
          GROUP BY mb.canon_id
        ),
        aliases AS (
          SELECT mb.canon_id,
                 array_agg(DISTINCT a.alias_value)
                   FILTER (WHERE a.alias_type = 'name') AS name_aliases
          FROM members mb
          LEFT JOIN fpe_employee_aliases a ON a.employee_id = mb.member_id
          GROUP BY mb.canon_id
        )
        SELECT c.*,
               COALESCE(ag.total_transactions, 0) AS total_transactions,
               COALESCE(ag.total_paid, 0)         AS total_paid,
               ag.last_payment_date,
               al.name_aliases
        FROM canon c
        LEFT JOIN agg     ag ON ag.canon_id = c.id
        LEFT JOIN aliases al ON al.canon_id = c.id
        ORDER BY {outer_order}
        """,
        *params,
    )

    employees = []
    for r in rows:
        d = dict(r)
        # Pick best display name across full_name + alias names
        name_aliases = d.pop("name_aliases", None) or []
        best = _pick_best_name(d.get("full_name"), *name_aliases)
        d["display_name"] = (
            best
            or d.get("employee_id_phone")
            or d.get("primary_phone")
            or "(unnamed)"
        )
        d["display_id"] = d.get("employee_id_phone") or d.get("primary_phone") or ""
        d["mobile_number"] = d.get("primary_phone") or d.get("employee_id_phone") or ""
        d["name_aliases"] = name_aliases
        d["total_transactions"] = int(d["total_transactions"] or 0)
        d["total_paid"] = float(d["total_paid"] or 0)
        d["last_payment_date"] = (
            d["last_payment_date"].isoformat() if d.get("last_payment_date") else None
        )
        employees.append(d)

    page_resp = (offset // limit) + 1 if limit else 1
    pages_resp = (total + limit - 1) // limit if (total and limit) else 1
    return {
        "total": total,
        "page": page_resp,
        "page_size": limit,
        "pages": pages_resp,
        "employees": employees,
    }


@router.post("/employees", dependencies=[Depends(_require_api_key)])
async def create_employee(payload: dict):
    """Create a new FPE employee from the protected payroll SPA."""
    try:
        employee = await create_employee_manual(
            full_name=(payload.get("full_name") or payload.get("employee_name") or "").strip(),
            employee_mobile=(payload.get("employee_mobile") or payload.get("employee_id_phone") or "").strip(),
            role_or_type=(payload.get("role") or payload.get("role_type") or payload.get("department")),
            status=(payload.get("status") or "active").strip() or "active",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"status": "created", "employee": employee}


@router.get("/employees/search", dependencies=[Depends(_require_api_key)])
async def search_employees(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
):
    """
    Search employees by phone, employee_id_phone, name, or alias.
    Returns canonical employee profiles with match_type. READ-ONLY.

    NOTE: Registered BEFORE /employees/{emp_id} so the literal "search"
    path segment wins over the int path-param matcher.
    """
    raw = q.strip()
    if not raw:
        return {"query": q, "count": 0, "results": []}

    digits = "".join(ch for ch in raw if ch.isdigit())
    is_phone_like = len(digits) >= 6
    phone_norm = normalize_bd_phone(raw) if is_phone_like else None
    name_norm = normalize_name(raw)

    matches: list[tuple[int, str, int]] = []
    seen_src: set[int] = set()

    def _add(emp_id: int, match_type: str, priority: int):
        if emp_id and emp_id not in seen_src:
            matches.append((emp_id, match_type, priority))
            seen_src.add(emp_id)

    if phone_norm:
        for r in await fetch_all(
            "SELECT id FROM fpe_employees WHERE employee_id_phone = $1 LIMIT $2",
            phone_norm, limit,
        ):
            _add(r["id"], "employee_id_phone", 1)
    if phone_norm and len(matches) < limit:
        for r in await fetch_all(
            "SELECT id FROM fpe_employees WHERE primary_phone = $1 LIMIT $2",
            phone_norm, limit,
        ):
            _add(r["id"], "primary_phone", 2)
    if phone_norm and len(matches) < limit:
        for r in await fetch_all(
            "SELECT employee_id FROM fpe_employee_aliases "
            "WHERE alias_type = 'phone' AND alias_value = $1 LIMIT $2",
            phone_norm, limit,
        ):
            _add(r["employee_id"], "alias_phone", 3)
    if name_norm and len(matches) < limit:
        for r in await fetch_all(
            "SELECT id FROM fpe_employees WHERE name_normalized = $1 LIMIT $2",
            name_norm, limit,
        ):
            _add(r["id"], "name_exact", 4)
    if name_norm and len(matches) < limit:
        for r in await fetch_all(
            "SELECT employee_id FROM fpe_employee_aliases "
            "WHERE alias_type = 'name' AND alias_value = $1 LIMIT $2",
            name_norm, limit,
        ):
            _add(r["employee_id"], "alias_name", 5)
    if name_norm and len(matches) < limit and len(name_norm) >= 3:
        try:
            from rapidfuzz import fuzz
        except Exception:
            fuzz = None
        if fuzz is not None:
            cand = await fetch_all(
                "SELECT id, name_normalized FROM fpe_employees "
                "WHERE name_normalized IS NOT NULL AND status = 'active'"
            )
            scored: list[tuple[int, float]] = []
            for c in cand:
                if c["id"] in seen_src:
                    continue
                s = fuzz.token_set_ratio(name_norm, c["name_normalized"] or "")
                if s >= 90:
                    scored.append((c["id"], s))
            scored.sort(key=lambda x: -x[1])
            for emp_id, _s in scored[: max(0, limit - len(matches))]:
                _add(emp_id, "fuzzy_name", 6)

    canon_best: dict[int, tuple[str, int]] = {}
    for src_id, match_type, prio in matches:
        src_row = await fetch_one(
            "SELECT id, canonical_employee_id, full_name, primary_phone, "
            "name_normalized, employee_code FROM fpe_employees WHERE id = $1",
            src_id,
        )
        if not src_row:
            continue
        canon = await _resolve_canonical(dict(src_row))
        cid = canon["id"]
        if cid not in canon_best or prio < canon_best[cid][1]:
            canon_best[cid] = (match_type, prio)

    results: list[tuple[int, dict]] = []
    for cid, (mt, prio) in canon_best.items():
        prof = await _collect_canonical_profile(cid)
        if not prof:
            continue
        prof["match_type"] = mt
        prof.pop("_member_ids", None)
        results.append((prio, prof))
    results.sort(key=lambda x: (x[0], (x[1].get("full_name") or "").lower()))
    final = [r[1] for r in results[:limit]]

    return {
        "query": q,
        "phone_normalized": phone_norm,
        "name_normalized": name_norm,
        "count": len(final),
        "results": final,
    }


# ── Type-Ahead Suggest (READ-ONLY, fuzzy, alias-aware) ────────────────────────
@router.get("/employees/suggest", dependencies=[Depends(_require_api_key)])
async def suggest_employees(
    q: str = Query(..., min_length=1, max_length=80),
    limit: int = Query(8, ge=1, le=20),
):
    """Smart type-ahead/autocomplete search.

    READ-ONLY. NEVER mutates accounting. Aggregates across canonical employee
    + soft-merged members. Uses pg_trgm similarity() for fuzzy matching with
    a 6-tier priority scoring fallback so that even misspelled queries return
    the nearest probable match instead of an empty list.
    """
    raw = (q or "").strip()
    if not raw:
        return {"query": q, "results": []}

    digits = "".join(ch for ch in raw if ch.isdigit())
    is_phone_like = len(digits) >= 4 and len(digits) >= len(raw) - 3
    phone_norm = normalize_bd_phone(raw) if is_phone_like and len(digits) >= 6 else None
    digits_tail = digits[-10:] if len(digits) >= 4 else None
    qlow = raw.lower()
    name_norm = normalize_name(raw) or qlow
    q_search = normalize_search_text(raw)        # "al momin"
    q_collapsed = collapse_search_text(raw)      # "almomin"
    logger.debug(
        "suggest q=%r qlow=%r q_search=%r q_collapsed=%r phone=%r digits=%r",
        raw, qlow, q_search, q_collapsed, phone_norm, digits_tail,
    )

    # ── Stage 1: collect candidate source-employee ids with score ───────────
    # Each branch returns (employee_id, score, match_type). We UNION them and
    # later resolve to canonical + dedupe on canonical id (keeping max score).
    candidates: dict[int, tuple[float, str]] = {}

    def _bump(eid: int, score: float, mt: str):
        if not eid:
            return
        cur = candidates.get(eid)
        if cur is None or score > cur[0]:
            candidates[eid] = (score, mt)

    # ── Phone path ──────────────────────────────────────────────────────────
    if phone_norm or digits_tail:
        # Tier 1: exact employee_id_phone
        if phone_norm:
            for r in await fetch_all(
                "SELECT id FROM fpe_employees WHERE employee_id_phone = $1",
                phone_norm,
            ):
                _bump(r["id"], 1.00, "employee_id_phone")
        # Tier 2: exact primary_phone OR payout_phone (via txn lookup)
        if phone_norm:
            for r in await fetch_all(
                "SELECT id FROM fpe_employees WHERE primary_phone = $1",
                phone_norm,
            ):
                _bump(r["id"], 0.95, "primary_phone")
            for r in await fetch_all(
                "SELECT employee_id FROM fpe_employee_aliases "
                "WHERE alias_type = 'phone' AND alias_value = $1",
                phone_norm,
            ):
                _bump(r["employee_id"], 0.93, "alias_phone")
            for r in await fetch_all(
                "SELECT DISTINCT employee_id FROM fpe_cash_transactions "
                "WHERE payout_phone = $1 AND employee_id IS NOT NULL LIMIT 20",
                phone_norm,
            ):
                _bump(r["employee_id"], 0.90, "payout_phone")
        # Tier 5: partial phone (suffix / contains)
        if digits_tail:
            like = f"%{digits_tail}%"
            for r in await fetch_all(
                "SELECT id FROM fpe_employees "
                "WHERE employee_id_phone LIKE $1 OR primary_phone LIKE $1 LIMIT 20",
                like,
            ):
                _bump(r["id"], 0.78, "phone_partial")

    # ── Name path ───────────────────────────────────────────────────────────
    if not is_phone_like or not candidates:
        prefix_like = qlow + "%"
        substr_like = "%" + qlow + "%"
        col_prefix  = (q_collapsed + "%") if q_collapsed else None
        col_substr  = ("%" + q_collapsed + "%") if q_collapsed else None

        # Tier 3: exact normalized name / full_name
        for r in await fetch_all(
            "SELECT id FROM fpe_employees "
            "WHERE lower(name_normalized) = $1 OR lower(full_name) = $1",
            qlow,
        ):
            _bump(r["id"], 0.92, "name_exact")

        # Tier 3b: exact on COLLAPSED form — handles hyphen/space variants
        # ("al-momin" == "al momin" == "almomin" → all collapse to "almomin")
        if q_collapsed and len(q_collapsed) >= 2:
            for r in await fetch_all(
                "SELECT id FROM fpe_employees "
                "WHERE regexp_replace(lower(coalesce(full_name,'')), '[^a-z0-9]+', '', 'g') = $1",
                q_collapsed,
            ):
                _bump(r["id"], 0.91, "name_exact_collapsed")
            for r in await fetch_all(
                "SELECT employee_id FROM fpe_employee_aliases "
                "WHERE alias_type = 'name' "
                "  AND regexp_replace(lower(coalesce(alias_value,'')), '[^a-z0-9]+', '', 'g') = $1",
                q_collapsed,
            ):
                _bump(r["employee_id"], 0.87, "alias_exact_collapsed")

        # Tier 4: alias exact (name)
        for r in await fetch_all(
            "SELECT employee_id FROM fpe_employee_aliases "
            "WHERE alias_type = 'name' AND lower(alias_value) = $1",
            qlow,
        ):
            _bump(r["employee_id"], 0.88, "alias_exact")

        # Tier 5: prefix on full_name / aliases
        for r in await fetch_all(
            "SELECT id FROM fpe_employees "
            "WHERE lower(full_name) LIKE $1 OR lower(name_normalized) LIKE $1 LIMIT 30",
            prefix_like,
        ):
            _bump(r["id"], 0.80, "name_prefix")
        for r in await fetch_all(
            "SELECT employee_id FROM fpe_employee_aliases "
            "WHERE alias_type = 'name' AND lower(alias_value) LIKE $1 LIMIT 30",
            prefix_like,
        ):
            _bump(r["employee_id"], 0.78, "alias_prefix")

        # Tier 5b: prefix on COLLAPSED form
        if col_prefix and len(q_collapsed) >= 2:
            for r in await fetch_all(
                "SELECT id FROM fpe_employees "
                "WHERE regexp_replace(lower(coalesce(full_name,'')), '[^a-z0-9]+', '', 'g') LIKE $1 "
                "LIMIT 30",
                col_prefix,
            ):
                _bump(r["id"], 0.79, "name_prefix_collapsed")
            for r in await fetch_all(
                "SELECT employee_id FROM fpe_employee_aliases "
                "WHERE alias_type = 'name' "
                "  AND regexp_replace(lower(coalesce(alias_value,'')), '[^a-z0-9]+', '', 'g') LIKE $1 "
                "LIMIT 30",
                col_prefix,
            ):
                _bump(r["employee_id"], 0.77, "alias_prefix_collapsed")

        # Tier 6: substring (helpful for middle-of-name)
        for r in await fetch_all(
            "SELECT id FROM fpe_employees "
            "WHERE lower(full_name) LIKE $1 LIMIT 30",
            substr_like,
        ):
            _bump(r["id"], 0.70, "name_substr")

        # Tier 6a: substring on COLLAPSED form (e.g. "momin" → "almomin")
        if col_substr and len(q_collapsed) >= 3:
            for r in await fetch_all(
                "SELECT id FROM fpe_employees "
                "WHERE regexp_replace(lower(coalesce(full_name,'')), '[^a-z0-9]+', '', 'g') LIKE $1 "
                "LIMIT 30",
                col_substr,
            ):
                _bump(r["id"], 0.72, "name_substr_collapsed")
            for r in await fetch_all(
                "SELECT employee_id FROM fpe_employee_aliases "
                "WHERE alias_type = 'name' "
                "  AND regexp_replace(lower(coalesce(alias_value,'')), '[^a-z0-9]+', '', 'g') LIKE $1 "
                "LIMIT 30",
                col_substr,
            ):
                _bump(r["employee_id"], 0.71, "alias_substr_collapsed")
            # Also scan historical raw names — recovers employees referenced
            # only via free-form WhatsApp text like "Al-Momin 01714958528(N) 500/-"
            for r in await fetch_all(
                "SELECT DISTINCT employee_id FROM fpe_cash_transactions "
                "WHERE employee_id IS NOT NULL "
                "  AND regexp_replace(lower(coalesce(employee_name_raw,'')), '[^a-z0-9]+', '', 'g') LIKE $1 "
                "LIMIT 30",
                col_substr,
            ):
                _bump(r["employee_id"], 0.74, "raw_substr_collapsed")

        # Tier 6b: pg_trgm fuzzy similarity (typo tolerance)
        if len(qlow) >= 3 or (q_collapsed and len(q_collapsed) >= 3):
            try:
                fuzzy = await fetch_all(
                    """
                    SELECT id, GREATEST(
                        similarity(lower(coalesce(full_name,'')), $1),
                        similarity(coalesce(name_normalized,''), $1),
                        similarity(regexp_replace(lower(coalesce(full_name,'')), '[^a-z0-9]+', '', 'g'), $2)
                    ) AS sim
                    FROM fpe_employees
                    WHERE (lower(coalesce(full_name,'')) % $1
                        OR coalesce(name_normalized,'') % $1
                        OR regexp_replace(lower(coalesce(full_name,'')), '[^a-z0-9]+', '', 'g') % $2)
                    ORDER BY sim DESC
                    LIMIT 30
                    """,
                    qlow, q_collapsed or qlow,
                )
                for r in fuzzy:
                    s = float(r["sim"] or 0)
                    if s >= 0.30:
                        _bump(r["id"], min(0.85, 0.40 + s * 0.45), f"fuzzy:{s:.2f}")
                # alias trigram (raw + collapsed)
                fuzzy_a = await fetch_all(
                    """
                    SELECT employee_id, GREATEST(
                        similarity(lower(alias_value), $1),
                        similarity(regexp_replace(lower(coalesce(alias_value,'')), '[^a-z0-9]+', '', 'g'), $2)
                    ) AS sim
                    FROM fpe_employee_aliases
                    WHERE alias_type = 'name'
                      AND (lower(alias_value) % $1
                        OR regexp_replace(lower(coalesce(alias_value,'')), '[^a-z0-9]+', '', 'g') % $2)
                    ORDER BY sim DESC
                    LIMIT 30
                    """,
                    qlow, q_collapsed or qlow,
                )
                for r in fuzzy_a:
                    s = float(r["sim"] or 0)
                    if s >= 0.30:
                        _bump(r["employee_id"], min(0.83, 0.38 + s * 0.45), f"alias_fuzzy:{s:.2f}")
                # historical raw name on transactions (raw + collapsed)
                fuzzy_t = await fetch_all(
                    """
                    SELECT employee_id,
                        MAX(GREATEST(
                            similarity(lower(coalesce(employee_name_raw,'')), $1),
                            similarity(regexp_replace(lower(coalesce(employee_name_raw,'')), '[^a-z0-9]+', '', 'g'), $2)
                        )) AS sim
                    FROM fpe_cash_transactions
                    WHERE employee_id IS NOT NULL
                      AND (lower(coalesce(employee_name_raw,'')) % $1
                        OR regexp_replace(lower(coalesce(employee_name_raw,'')), '[^a-z0-9]+', '', 'g') % $2)
                    GROUP BY employee_id
                    ORDER BY sim DESC
                    LIMIT 20
                    """,
                    qlow, q_collapsed or qlow,
                )
                for r in fuzzy_t:
                    s = float(r["sim"] or 0)
                    if s >= 0.40:
                        _bump(r["employee_id"], min(0.78, 0.35 + s * 0.40), f"raw_fuzzy:{s:.2f}")
            except Exception as exc:  # pg_trgm missing or other issue
                logger.debug("trigram suggest skipped: %s", exc)

    if not candidates:
        return {"query": q, "results": []}

    # ── Stage 2: collapse to canonical, then enrich ─────────────────────────
    canon_best: dict[int, tuple[float, str]] = {}
    for src_id, (score, mt) in candidates.items():
        src_row = await fetch_one(
            "SELECT id, canonical_employee_id, full_name, primary_phone, "
            "name_normalized, employee_code FROM fpe_employees WHERE id = $1",
            src_id,
        )
        if not src_row:
            continue
        canon = await _resolve_canonical(dict(src_row))
        cid = canon["id"]
        cur = canon_best.get(cid)
        if cur is None or score > cur[0]:
            canon_best[cid] = (score, mt)

    # ── Stage 3: enrich with display_name + totals + aliases ────────────────
    enriched: list[dict] = []
    for cid, (score, mt) in canon_best.items():
        emp = await fetch_one(
            "SELECT id, full_name, employee_id_phone, primary_phone, status "
            "FROM fpe_employees WHERE id = $1",
            cid,
        )
        if not emp:
            continue
        member_ids_rows = await fetch_all(
            "SELECT id FROM fpe_employees WHERE id=$1 OR canonical_employee_id=$1",
            cid,
        )
        member_ids = [r["id"] for r in member_ids_rows] or [cid]
        alias_rows = await fetch_all(
            "SELECT alias_value FROM fpe_employee_aliases "
            "WHERE employee_id = ANY($1::int[]) AND alias_type='name'",
            member_ids,
        )
        name_aliases = sorted({a["alias_value"] for a in alias_rows if a["alias_value"]})
        # Also pull historical raw names that match the search query — these
        # surface variants like "Al-Momin" that exist only inside transaction
        # rows (employee_name_raw) and not in fpe_employees.full_name.
        raw_aliases: list[str] = []
        if q_collapsed and len(q_collapsed) >= 2:
            raw_rows = await fetch_all(
                "SELECT DISTINCT employee_name_raw FROM fpe_cash_transactions "
                "WHERE employee_id = ANY($1::int[]) "
                "  AND employee_name_raw IS NOT NULL "
                "  AND regexp_replace(lower(employee_name_raw), '[^a-z0-9]+', '', 'g') LIKE $2 "
                "LIMIT 5",
                member_ids, "%" + q_collapsed + "%",
            )
            raw_aliases = [r["employee_name_raw"] for r in raw_rows if r["employee_name_raw"]]
        agg = await fetch_one(
            "SELECT COALESCE(SUM(amount),0) AS total_paid, "
            "MAX(txn_date) AS last_activity, COUNT(*)::bigint AS total_txns "
            "FROM fpe_cash_transactions "
            "WHERE employee_id = ANY($1::int[]) AND is_reversal = FALSE",
            member_ids,
        )
        display_name = (
            _pick_best_name(emp["full_name"], *name_aliases)
            or emp["employee_id_phone"]
            or f"#{cid}"
        )
        # Merge raw historical names (matching the query) into alias surface
        # so the user sees the textual form they actually searched for.
        merged_aliases = sorted({*name_aliases, *raw_aliases})
        shown_aliases = [a for a in merged_aliases if a != display_name][:6]
        enriched.append({
            "employee_id": cid,
            "employee_id_phone": emp["employee_id_phone"],
            "display_name": display_name,
            "mobile_number": emp["employee_id_phone"] or emp["primary_phone"],
            "status": emp["status"],
            "aliases": shown_aliases,
            "alias_count": len(merged_aliases),
            "similarity": round(score, 3),
            "match_type": mt,
            "total_paid": float(agg["total_paid"] or 0),
            "total_transactions": int(agg["total_txns"] or 0),
            "last_activity": agg["last_activity"].isoformat() if agg["last_activity"] else None,
        })

    # ── Stage 4: sort by score DESC, then total_paid, then last_activity ────
    enriched.sort(
        key=lambda r: (
            -r["similarity"],
            -r["total_paid"],
            r["last_activity"] or "",
        )
    )
    return {
        "query": q,
        "normalized_query": qlow,
        "search_normalized": q_search,
        "search_collapsed": q_collapsed,
        "phone_normalized": phone_norm,
        "results": enriched[:limit],
    }


@router.get("/employees/{emp_id}", dependencies=[Depends(_require_api_key)])
async def get_employee(emp_id: int):
    emp = await fetch_one("SELECT * FROM fpe_employees WHERE id = $1", emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    # Always resolve to canonical for display purposes
    canon = await _resolve_canonical(dict(emp))
    canon_id = canon["id"]

    member_ids_rows = await fetch_all(
        "SELECT id FROM fpe_employees WHERE id = $1 OR canonical_employee_id = $1",
        canon_id,
    )
    member_ids = [r["id"] for r in member_ids_rows]

    aliases = await fetch_all(
        "SELECT DISTINCT alias_type, alias_value FROM fpe_employee_aliases "
        "WHERE employee_id = ANY($1::int[]) ORDER BY alias_type, alias_value",
        member_ids,
    )
    ledger = await fetch_all(
        """
        SELECT accounting_period, total_paid, total_advance, closing_balance, txn_count
        FROM fpe_employee_ledger
        WHERE employee_id = ANY($1::int[])
        ORDER BY accounting_period DESC
        LIMIT 12
        """,
        member_ids,
    )
    agg = await fetch_one(
        """
        SELECT COUNT(*)::bigint AS total_transactions,
               COALESCE(SUM(amount),0) AS total_paid,
               MAX(txn_date) AS last_payment_date,
               MIN(txn_date) AS first_payment_date
        FROM fpe_cash_transactions
        WHERE employee_id = ANY($1::int[]) AND is_reversal = FALSE
        """,
        member_ids,
    )
    canon_full = await fetch_one(
        "SELECT * FROM fpe_employees WHERE id = $1", canon_id
    )
    name_aliases = [a["alias_value"] for a in aliases if a["alias_type"] == "name"]
    display_name = (
        _pick_best_name(canon_full["full_name"], *name_aliases)
        or canon_full["employee_id_phone"]
        or canon_full["primary_phone"]
        or "(unnamed)"
    )
    emp_out = dict(canon_full)
    emp_out["display_name"] = display_name
    emp_out["mobile_number"] = canon_full["primary_phone"] or canon_full["employee_id_phone"]
    return {
        "employee": emp_out,
        "aliases": [dict(a) for a in aliases],
        "ledger_summary": [dict(l) for l in ledger],
        "totals": {
            "total_transactions": int(agg["total_transactions"] or 0) if agg else 0,
            "total_paid": float(agg["total_paid"] or 0) if agg else 0.0,
            "first_payment_date": agg["first_payment_date"].isoformat() if agg and agg["first_payment_date"] else None,
            "last_payment_date": agg["last_payment_date"].isoformat() if agg and agg["last_payment_date"] else None,
        },
        "member_ids": member_ids,
        "requested_id": emp_id,
        "canonical_employee_id": canon_id,
    }


@router.patch("/employees/{emp_id}", dependencies=[Depends(_require_api_key)])
async def update_employee(emp_id: int, payload: dict):
    """Edit employee profile metadata (mutable) with safe canonical merge.

    Editable fields:
      - full_name        (str | null)  — enriches name_normalized; aliases preserved
      - primary_phone    (str | null)  — normalized
      - employee_id_phone(str | null)  — if collides with existing employee
                                          → soft-merge via canonical_employee_id
      - aliases          (list[{alias_type, alias_value}]) — additive only

    STRICTLY FORBIDDEN:
      - Mutating fpe_cash_transactions (ownership / amount / txn_ref) — never done.
      - Hard-deleting any employee row.
    """
    emp = await fetch_one("SELECT * FROM fpe_employees WHERE id = $1", emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    new_name = payload.get("full_name")
    new_primary = payload.get("primary_phone")
    new_idphone = payload.get("employee_id_phone")
    new_aliases = payload.get("aliases") or []

    # Normalize phones
    if new_primary is not None:
        new_primary = normalize_bd_phone(new_primary) if new_primary else None
    if new_idphone is not None:
        new_idphone = normalize_bd_phone(new_idphone) if new_idphone else None

    merge_target_id: Optional[int] = None
    # ── Detect collision on employee_id_phone → soft-merge
    if new_idphone and new_idphone != emp["employee_id_phone"]:
        existing = await fetch_one(
            "SELECT id, canonical_employee_id FROM fpe_employees "
            "WHERE employee_id_phone = $1 AND id <> $2 AND status = 'active' LIMIT 1",
            new_idphone, emp_id,
        )
        if existing:
            # Resolve existing to its canonical; this row becomes a duplicate of it.
            target = await _resolve_canonical(dict(existing))
            if target["id"] != emp_id:
                merge_target_id = target["id"]

    sets: list[str] = []
    params: list = []
    i = 1
    if new_name is not None:
        # Allow explicit clear or update; only normalize if non-empty
        sets.append(f"full_name = ${i}"); params.append(new_name.strip() if new_name else None); i += 1
        sets.append(f"name_normalized = ${i}")
        params.append(normalize_name(new_name) if new_name else None); i += 1
    if new_primary is not None:
        sets.append(f"primary_phone = ${i}"); params.append(new_primary); i += 1
    if new_idphone is not None and merge_target_id is None:
        sets.append(f"employee_id_phone = ${i}"); params.append(new_idphone); i += 1

    if merge_target_id is not None:
        sets.append(f"canonical_employee_id = ${i}"); params.append(merge_target_id); i += 1

    if sets:
        sets.append("updated_at = NOW()")
        params.append(emp_id)
        await execute(
            f"UPDATE fpe_employees SET {', '.join(sets)} WHERE id = ${i}",
            *params,
        )

    # ── Aliases (additive, idempotent)
    accepted_aliases: list[dict] = []
    target_for_aliases = merge_target_id or emp_id
    for a in new_aliases:
        atype = (a or {}).get("alias_type")
        aval = (a or {}).get("alias_value")
        if not atype or not aval:
            continue
        if atype not in {"phone", "name", "employee_id"}:
            continue
        if atype == "phone":
            aval = normalize_bd_phone(aval) or aval
        elif atype == "name":
            aval = normalize_name(aval) or aval
        await execute(
            "INSERT INTO fpe_employee_aliases (employee_id, alias_type, alias_value) "
            "VALUES ($1, $2, $3) ON CONFLICT (alias_type, alias_value) DO NOTHING",
            target_for_aliases, atype, aval,
        )
        accepted_aliases.append({"alias_type": atype, "alias_value": aval})

    # ── If we merged, also fold the old identity values in as aliases of the target
    if merge_target_id is not None:
        for atype, aval in [
            ("phone", emp["primary_phone"]),
            ("phone", emp["employee_id_phone"]),
            ("name",  emp["name_normalized"]),
        ]:
            if aval:
                await execute(
                    "INSERT INTO fpe_employee_aliases (employee_id, alias_type, alias_value) "
                    "VALUES ($1, $2, $3) ON CONFLICT (alias_type, alias_value) DO NOTHING",
                    merge_target_id, atype, aval,
                )
        log.info(
            "[fpe.emp] soft-merged employee id=%d → canonical id=%d via PATCH",
            emp_id, merge_target_id,
        )

    final_id = merge_target_id or emp_id
    return {
        "status": "ok",
        "employee_id": final_id,
        "merged_into": merge_target_id,
        "aliases_added": accepted_aliases,
    }


# ── Employee Search & Transaction History (READ-ONLY) ────────────────────────
#
# Strict invariants for everything below:
#   * READ-ONLY. Zero INSERT / UPDATE / DELETE / mutation of any kind.
#   * Identity resolution (canonical_employee_id) is honoured but never written.
#   * Original txn_ref / employee_id / amount on each transaction is returned
#     exactly as recorded — accounting history is immutable.

async def _collect_canonical_profile(canon_id: int) -> Optional[dict]:
    """Build a canonical employee profile + aliases + payout phones."""
    emp = await fetch_one(
        """
        SELECT id, employee_code, full_name, primary_phone,
               employee_id_phone, canonical_employee_id, name_normalized,
               status, created_source, created_at
        FROM fpe_employees WHERE id = $1
        """,
        canon_id,
    )
    if not emp:
        return None

    # All employee rows that resolve to this canonical (canonical itself + duplicates)
    member_ids_rows = await fetch_all(
        "SELECT id FROM fpe_employees WHERE id = $1 OR canonical_employee_id = $1",
        canon_id,
    )
    member_ids = [r["id"] for r in member_ids_rows]

    aliases_rows = await fetch_all(
        """
        SELECT DISTINCT alias_type, alias_value
        FROM fpe_employee_aliases
        WHERE employee_id = ANY($1::int[])
        ORDER BY alias_type, alias_value
        """,
        member_ids,
    )
    aliases = [dict(a) for a in aliases_rows]

    payout_phones: list[str] = []
    seen_phones: set[str] = set()
    for p in (emp["primary_phone"], emp["employee_id_phone"]):
        if p and p not in seen_phones:
            payout_phones.append(p); seen_phones.add(p)
    for a in aliases:
        if a["alias_type"] == "phone" and a["alias_value"] not in seen_phones:
            payout_phones.append(a["alias_value"]); seen_phones.add(a["alias_value"])

    return {
        "employee_id": emp["id"],
        "canonical_employee_id": emp["id"],   # this row IS the canonical
        "employee_code": emp["employee_code"],
        "full_name": emp["full_name"],
        "employee_id_phone": emp["employee_id_phone"],
        "primary_phone": emp["primary_phone"],
        "status": emp["status"],
        "aliases": aliases,
        "payout_phones": payout_phones,
        "_member_ids": member_ids,
    }


@router.get("/employees/{emp_id}/transactions", dependencies=[Depends(_require_api_key)])
async def employee_transaction_history(
    emp_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
):
    """
    Paginated transaction history for the canonical employee that emp_id resolves to.
    READ-ONLY. Returns transactions exactly as recorded (no rewrite of employee_id /
    txn_ref / amount). Excludes reversal rows.
    """
    src = await fetch_one(
        "SELECT id, canonical_employee_id, full_name, primary_phone, "
        "name_normalized, employee_code FROM fpe_employees WHERE id = $1",
        emp_id,
    )
    if not src:
        raise HTTPException(404, "Employee not found")
    canon = await _resolve_canonical(dict(src))
    canon_id = canon["id"]

    profile = await _collect_canonical_profile(canon_id)
    if not profile:
        raise HTTPException(404, "Canonical employee not found")
    member_ids = profile.pop("_member_ids", [canon_id])

    # ── Build WHERE clause for txn query
    where = ["t.employee_id = ANY($1::int[])", "t.is_reversal = FALSE"]
    params: list = [member_ids]
    if date_from:
        params.append(date_from)
        where.append(f"t.txn_date >= ${len(params)}")
    if date_to:
        params.append(date_to)
        where.append(f"t.txn_date <= ${len(params)}")
    where_sql = " AND ".join(where)

    # ── Summary (independent of pagination)
    summary_row = await fetch_one(
        f"""
        SELECT COUNT(*)::bigint AS total_transactions,
               COALESCE(SUM(t.amount), 0) AS total_paid,
               MIN(t.txn_date) AS first_transaction,
               MAX(t.txn_date) AS last_transaction
        FROM fpe_cash_transactions t
        WHERE {where_sql}
        """,
        *params,
    )
    total_records = int(summary_row["total_transactions"]) if summary_row else 0
    total_pages = (total_records + page_size - 1) // page_size if total_records else 0

    # ── Paginated rows
    offset = (page - 1) * page_size
    page_params = list(params) + [page_size, offset]
    rows = await fetch_all(
        f"""
        SELECT t.id, t.txn_ref, t.txn_date, t.amount, t.payout_method,
               t.payout_phone, t.employee_name_raw, t.source_message_text,
               t.txn_category, t.accounting_period, t.created_at,
               t.employee_id AS original_employee_id,
               e.full_name AS original_employee_name,
               -- Per spec: prefer parsed raw name; fall back to canonical employee name.
               COALESCE(t.employee_name_raw, e.full_name, '(unknown)')
                                  AS employee_display_name,
               -- Per spec: visible Employee ID = employee_id_phone
               -- (record → primary_phone → payout_phone fallback).
               COALESCE(e.employee_id_phone, e.primary_phone, t.payout_phone)
                                  AS employee_display_id_phone,
               e.employee_id_phone AS original_employee_id_phone
        FROM fpe_cash_transactions t
        LEFT JOIN fpe_employees e ON e.id = t.employee_id
        WHERE {where_sql}
        ORDER BY t.txn_date DESC, t.id DESC
        LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
        """,
        *page_params,
    )

    transactions = [
        {
            "id": r["id"],
            "txn_ref": r["txn_ref"],
            "date": r["txn_date"].isoformat() if r["txn_date"] else None,
            "amount": float(r["amount"]) if r["amount"] is not None else 0.0,
            "payment_method": r["payout_method"],
            "payout_phone": r["payout_phone"],
            "employee_name": r["employee_display_name"],
            "employee_id_phone": r["employee_display_id_phone"],
            "comment": None,  # reserved for future remarks column
            "source_message": r["source_message_text"],
            "txn_category": r["txn_category"],
            "accounting_period": r["accounting_period"],
            "original_employee_id": r["original_employee_id"],
        }
        for r in rows
    ]

    return {
        "employee": {
            "employee_id": profile["employee_id"],
            "canonical_employee_id": profile["canonical_employee_id"],
            "employee_code": profile["employee_code"],
            "full_name": profile["full_name"],
            "employee_id_phone": profile["employee_id_phone"],
            "primary_phone": profile["primary_phone"],
            "status": profile["status"],
            "aliases": profile["aliases"],
            "payout_phones": profile["payout_phones"],
        },
        "summary": {
            "total_transactions": total_records,
            "total_paid": float(summary_row["total_paid"]) if summary_row and summary_row["total_paid"] is not None else 0.0,
            "first_transaction": summary_row["first_transaction"].isoformat() if summary_row and summary_row["first_transaction"] else None,
            "last_transaction": summary_row["last_transaction"].isoformat() if summary_row and summary_row["last_transaction"] else None,
        },
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_records": total_records,
        },
        "filters": {
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
        },
        "transactions": transactions,
    }


# ── Ledger ────────────────────────────────────────────────────────────────────

@router.get("/ledger/{emp_id}", dependencies=[Depends(_require_api_key)])
async def get_ledger(emp_id: int, periods: int = Query(12, le=36)):
    rows = await fetch_all(
        """
        SELECT accounting_period, opening_balance, total_earned, total_paid,
               total_advance, closing_balance, txn_count, last_updated
        FROM fpe_employee_ledger
        WHERE employee_id = $1
        ORDER BY accounting_period DESC
        LIMIT $2
        """,
        emp_id, periods,
    )
    return {"employee_id": emp_id, "ledger": [dict(r) for r in rows]}


# ── Unmatched messages ────────────────────────────────────────────────────────

@router.get("/unmatched", dependencies=[Depends(_require_api_key)])
async def list_unmatched(reviewed: bool = Query(False), limit: int = Query(50, le=200)):
    await _cleanup_stale_review_queue_rows()
    rows = await fetch_all(
        """
        SELECT u.id, u.fpe_wa_message_id, u.reason, u.raw_content,
               u.reviewed, u.created_at, m.timestamp_wa, m.source
        FROM fpe_unmatched_messages u
        JOIN fpe_wa_messages m ON m.id = u.fpe_wa_message_id
        WHERE u.reviewed = $1
        ORDER BY u.created_at DESC
        LIMIT $2
        """,
        reviewed, limit,
    )
    return {"unmatched": [dict(r) for r in rows]}


@router.post("/unmatched/{unmatched_id}/mark-reviewed", dependencies=[Depends(_require_api_key)])
async def mark_reviewed(unmatched_id: int):
    await execute(
        "UPDATE fpe_unmatched_messages SET reviewed=TRUE, reviewed_at=NOW() WHERE id=$1",
        unmatched_id,
    )
    return {"status": "ok"}


# ── Sync ──────────────────────────────────────────────────────────────────────

@router.get("/sync/status", dependencies=[Depends(_require_api_key)])
async def sync_status():
    rows = await fetch_all(
        """
        SELECT source, source_number, chat_jid, last_message_id, last_timestamp,
               total_ingested, last_sync_at, last_checked_at
        FROM fpe_sync_checkpoints
        ORDER BY last_sync_at DESC
        """
    )
    return {"checkpoints": [dict(r) for r in rows]}


@router.post("/sync/trigger", dependencies=[Depends(_require_api_key)])
async def trigger_sync(chat_jids: Optional[list[str]] = None):
    """Trigger an immediate historical sync pass (runs in the background)."""
    import asyncio

    async def _run():
        try:
            result = await run_historical_sync_once(chat_jids)
            log.info("[fpe.routes] manual sync result: %s", result)
        except Exception as exc:
            log.error("[fpe.routes] manual sync failed: %s", exc)

    asyncio.create_task(_run(), name="fpe_manual_sync")
    return {"status": "sync_triggered"}


# ── Pipeline stats ────────────────────────────────────────────────────────────

@router.get("/stats", dependencies=[Depends(_require_api_key)])
async def pipeline_stats():
    status_counts = await fetch_all(
        """
        SELECT status, COUNT(*) as count
        FROM fpe_message_processing_state
        GROUP BY status
        ORDER BY status
        """
    )
    method_totals = await fetch_all(
        """
        SELECT payout_method, COUNT(*) as txn_count, SUM(amount) as total_amount
        FROM fpe_cash_transactions
        WHERE NOT is_reversal
        GROUP BY payout_method
        ORDER BY total_amount DESC NULLS LAST
        """
    )
    return {
        "processing_pipeline": {r["status"]: r["count"] for r in status_counts},
        "by_method": [dict(r) for r in method_totals],
    }


# ── Safe identity normalization ──────────────────────────────────────────────

@router.get("/normalization/summary", dependencies=[Depends(_require_api_key)])
async def normalization_summary_route():
    from .normalization import normalization_summary
    return await normalization_summary()


@router.get("/normalization/review", dependencies=[Depends(_require_api_key)])
async def normalization_review_list(limit: int = Query(100, ge=1, le=500)):
    from .normalization import list_pending_reviews
    return {"reviews": await list_pending_reviews(limit=limit)}


@router.post(
    "/normalization/review/{review_id}/resolve",
    dependencies=[Depends(_require_api_key)],
)
async def normalization_review_resolve(
    review_id: int,
    decision: str = Query(..., regex="^(approved_merge|rejected|kept_separate)$"),
    reviewer: str = Query(..., min_length=1, max_length=128),
    note: Optional[str] = Query(None, max_length=512),
):
    from .normalization import resolve_review
    try:
        return await resolve_review(review_id, decision, reviewer, note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/normalization/employees/{employee_id}/link-canonical",
    dependencies=[Depends(_require_api_key)],
)
async def normalization_link_canonical(
    employee_id: int,
    canonical_id: int = Query(..., ge=1),
    reviewer: str = Query(..., min_length=1, max_length=128),
    reason: str = Query(..., min_length=1, max_length=512),
    confidence: float = Query(1.0, ge=0.0, le=1.0),
):
    from .normalization import link_duplicate
    try:
        return await link_duplicate(
            duplicate_id=employee_id,
            canonical_id=canonical_id,
            reason=reason,
            reviewer=reviewer,
            confidence=confidence,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/normalization/employees/{employee_id}/aliases",
    dependencies=[Depends(_require_api_key)],
)
async def normalization_add_alias(
    employee_id: int,
    alias_type: str = Query(..., regex="^(phone|name|employee_id)$"),
    alias_value: str = Query(..., min_length=1, max_length=256),
    reviewer: str = Query("admin", min_length=1, max_length=128),
):
    from .normalization import add_alias_safe
    inserted = await add_alias_safe(employee_id, alias_type, alias_value, reviewer)
    return {"employee_id": employee_id, "alias_type": alias_type,
            "alias_value": alias_value, "inserted": inserted}


@router.post(
    "/normalization/employees/{employee_id}/inactivate",
    dependencies=[Depends(_require_api_key)],
)
async def normalization_inactivate(
    employee_id: int,
    reviewer: str = Query(..., min_length=1, max_length=128),
    reason: str = Query(..., min_length=1, max_length=512),
):
    from .normalization import mark_inactive
    try:
        return await mark_inactive(employee_id, reason, reviewer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(
    "/normalization/employees/{employee_id}/canonical",
    dependencies=[Depends(_require_api_key)],
)
async def normalization_canonical(employee_id: int):
    from .normalization import resolve_to_canonical
    canonical_id = await resolve_to_canonical(employee_id)
    return {"employee_id": employee_id, "canonical_id": canonical_id}


# ── Zero-loss admin: reconciliation / needs-review / DLQ / gap scan ──────────
#
# Architectural rule (do NOT relax):
#   Only verified accounting entries belong in fpe_cash_transactions.
#   Detected money awaiting verification lives in fpe_unmatched_messages and
#   is surfaced through /admin/needs-review. Promotion to the ledger goes
#   through create_transaction() so the same idempotency + audit trail
#   applies, and the original unmatched row is marked 'promoted' (never
#   deleted, never silently mutated).

from .reconcile import compute_reconciliation
from .gap_scan import run_gap_scan_once
from .accounting import create_transaction as _acct_create_transaction
from .models import PayoutMethod as _PayoutMethod
import json as _json


async def _audit_log(
    *,
    review_item_id: Optional[int],
    action: str,
    actor: str,
    old_state: Optional[dict] = None,
    new_state: Optional[dict] = None,
    reason: Optional[str] = None,
) -> None:
    """Append an immutable row to fpe_review_audit_logs. Best-effort: never raises."""
    try:
        await execute(
            """
            INSERT INTO fpe_review_audit_logs
                (review_item_id, action, actor, old_state, new_state, reason)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
            """,
            review_item_id,
            action,
            actor,
            _json.dumps(old_state, default=str) if old_state is not None else None,
            _json.dumps(new_state, default=str) if new_state is not None else None,
            reason,
        )
    except Exception as exc:  # pragma: no cover — audit must never break flow
        log.warning("[fpe.audit] failed to log %s by=%s: %s", action, actor, exc)


@router.get("/admin/reconcile", dependencies=[Depends(_require_api_key)])
async def admin_reconcile(
    period: Optional[str] = Query(None, regex=r"^\d{4}-\d{2}$"),
    source: Optional[str] = Query(None, regex=r"^(bridge1|bridge2|meta)$"),
):
    """Reconciliation invariant: ledger_sum + unmatched_review_sum == parser_sum."""
    await _cleanup_stale_review_queue_rows()
    return await compute_reconciliation(period=period, source=source)


@router.get("/admin/needs-review", dependencies=[Depends(_require_api_key)])
async def admin_needs_review(
    status: str = Query("pending", regex="^(pending|promoted|dismissed|duplicate)$"),
    reason: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Pending Accounting Candidates — money detected but not yet in ledger."""
    await _cleanup_stale_review_queue_rows()
    args: list = [status]
    where = f"u.review_status = $1 AND {_review_queue_candidate_sql('u')}"
    if reason:
        args.append(reason)
        where += f" AND u.reason = ${len(args)}"
    args.extend([limit, offset])
    limit_idx = len(args) - 1
    offset_idx = len(args)

    rows = await fetch_all(
        f"""
        SELECT
            u.id, u.fpe_wa_message_id, u.reason,
            u.detected_amount, u.detected_payout_phone,
            u.detected_employee_name, u.detected_payout_method,
            u.detected_txn_date, u.parser_confidence,
            u.retry_count, u.review_status,
            u.resolved_employee_id, u.promoted_txn_id,
            u.reviewer, u.review_note,
            u.raw_content, u.created_at,
            m.timestamp_wa, m.source, m.source_number, m.chat_jid,
            m.sender_phone
        FROM fpe_unmatched_messages u
        JOIN fpe_wa_messages m ON m.id = u.fpe_wa_message_id
        WHERE {where}
        ORDER BY u.created_at DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
        """,
        *args,
    )
    total = await fetch_val(
        f"SELECT COUNT(*) FROM fpe_unmatched_messages u WHERE {where.replace('$1', '$1')}",
        *args[: len(args) - 2],
    ) or 0
    items = [dict(r) for r in rows]
    for item in items:
        candidate = await _resolve_review_employee_candidate(
            detected_name=item.get("detected_employee_name"),
            detected_phone=item.get("detected_payout_phone"),
        )
        item["auto_employee"] = candidate
    return {"total": int(total), "items": items}


@router.post(
    "/admin/needs-review/{unmatched_id}/promote",
    dependencies=[Depends(_require_api_key)],
)
async def admin_needs_review_promote(
    unmatched_id: int,
    employee_id: int = Query(..., gt=0),
    reviewer: str = Query(..., min_length=1, max_length=128),
    payout_method: Optional[str] = Query(None),
    txn_date_override: Optional[date] = Query(None),
    txn_category: str = Query("salary"),
    amount_override: Optional[Decimal] = Query(None),
    note: Optional[str] = Query(None, max_length=512),
):
    """
    Promote a pending review row into a verified ledger transaction.

    Strict rules:
      * Original unmatched row is NEVER deleted — only its review_status flips
        to 'promoted' and resolved_employee_id / promoted_txn_id are filled.
      * Amount must come from parser-detected value unless reviewer explicitly
        overrides via amount_override (audited via review_note).
      * Idempotent: a second promotion is rejected if promoted_txn_id is set.
    """
    await _cleanup_stale_review_queue_rows()
    row = await fetch_one(
        """
        SELECT u.id, u.fpe_wa_message_id, u.review_status, u.promoted_txn_id,
               u.detected_amount, u.detected_payout_phone,
               u.detected_employee_name, u.detected_payout_method,
               u.detected_txn_date, u.raw_content
        FROM fpe_unmatched_messages u
        WHERE u.id = $1
        """,
        unmatched_id,
    )
    if not row:
        raise HTTPException(404, "unmatched row not found")
    if row["promoted_txn_id"] is not None or row["review_status"] == "promoted":
        raise HTTPException(409, "already promoted")

    amount = amount_override if amount_override is not None else row["detected_amount"]
    if amount is None or Decimal(amount) <= 0:
        raise HTTPException(400, "no amount available to promote (provide amount_override)")
    method_str = payout_method or row["detected_payout_method"] or "unknown"
    try:
        method = (
            _PayoutMethod(method_str)
            if method_str in _PayoutMethod._value2member_map_
            else _PayoutMethod.unknown
        )
    except (ValueError, KeyError):
        method = _PayoutMethod.unknown

    txn_date = txn_date_override or row["detected_txn_date"] or date.today()

    try:
        category = TxnCategory(txn_category)
    except (ValueError, KeyError):
        raise HTTPException(400, f"invalid txn_category: {txn_category}")

    txn_req = TransactionCreateRequest(
        fpe_wa_message_id=row["fpe_wa_message_id"],
        employee_id=employee_id,
        employee_name_raw=row["detected_employee_name"],
        amount=Decimal(amount),
        payout_phone=row["detected_payout_phone"],
        payout_method=method,
        txn_date=txn_date,
        txn_category=category,
        source_message_text=row["raw_content"],
        created_by=f"review:{reviewer}",
    )
    txn = await _acct_create_transaction(txn_req)

    await execute(
        """
        UPDATE fpe_unmatched_messages
        SET review_status      = 'promoted',
            resolved_employee_id = $2,
            promoted_txn_id    = $3,
            reviewer           = $4,
            review_note        = COALESCE($5, review_note),
            reviewed           = TRUE,
            reviewed_at        = NOW()
        WHERE id = $1
        """,
        unmatched_id, employee_id, txn.id, reviewer, note,
    )
    log.info(
        "[fpe.admin] promoted unmatched=%d -> txn_id=%d ref=%s reviewer=%s",
        unmatched_id, txn.id, txn.txn_ref[:12], reviewer,
    )
    await _audit_log(
        review_item_id=unmatched_id,
        action="promote",
        actor=reviewer,
        old_state={
            "review_status": row["review_status"],
            "detected_amount": str(row["detected_amount"]) if row["detected_amount"] is not None else None,
            "detected_employee_name": row["detected_employee_name"],
            "detected_payout_method": row["detected_payout_method"],
        },
        new_state={
            "review_status": "promoted",
            "resolved_employee_id": employee_id,
            "promoted_txn_id": txn.id,
            "txn_ref": txn.txn_ref,
            "amount": str(amount),
            "payout_method": method.value,
            "txn_date": str(txn_date),
            "txn_category": category.value,
            "amount_override_used": amount_override is not None,
        },
        reason=note,
    )
    return {
        "status": "promoted",
        "unmatched_id": unmatched_id,
        "txn_id": txn.id,
        "txn_ref": txn.txn_ref,
    }


@router.post(
    "/admin/needs-review/{unmatched_id}/dismiss",
    dependencies=[Depends(_require_api_key)],
)
async def admin_needs_review_dismiss(
    unmatched_id: int,
    reviewer: str = Query(..., min_length=1, max_length=128),
    reason: str = Query(..., min_length=1, max_length=512),
    as_duplicate: bool = Query(False),
):
    """Mark a review row as dismissed (or duplicate). Row is preserved."""
    await _cleanup_stale_review_queue_rows()
    row = await fetch_one(
        "SELECT id, review_status FROM fpe_unmatched_messages WHERE id=$1",
        unmatched_id,
    )
    if not row:
        raise HTTPException(404, "unmatched row not found")
    if row["review_status"] == "promoted":
        raise HTTPException(409, "cannot dismiss a promoted row")
    new_status = "duplicate" if as_duplicate else "dismissed"
    await execute(
        """
        UPDATE fpe_unmatched_messages
        SET review_status = $2,
            reviewer      = $3,
            review_note   = $4,
            reviewed      = TRUE,
            reviewed_at   = NOW()
        WHERE id = $1
        """,
        unmatched_id, new_status, reviewer, reason,
    )
    await _audit_log(
        review_item_id=unmatched_id,
        action="duplicate" if as_duplicate else "dismiss",
        actor=reviewer,
        old_state={"review_status": row["review_status"]},
        new_state={"review_status": new_status},
        reason=reason,
    )
    return {"status": new_status, "unmatched_id": unmatched_id}


@router.get("/admin/dlq", dependencies=[Depends(_require_api_key)])
async def admin_dlq(limit: int = Query(50, ge=1, le=500)):
    """Dead-letter queue — messages the worker has stopped retrying."""
    rows = await fetch_all(
        """
        SELECT mps.id AS mps_id, mps.fpe_wa_message_id, mps.status,
               mps.attempts, mps.last_error, mps.queued_at, mps.processed_at,
               m.source, m.source_number, m.chat_jid, m.timestamp_wa,
               LEFT(COALESCE(m.raw_content,''), 240) AS preview
        FROM fpe_message_processing_state mps
        JOIN fpe_wa_messages m ON m.id = mps.fpe_wa_message_id
        WHERE mps.status = 'failed' AND mps.attempts >= 5
        ORDER BY mps.processed_at DESC NULLS LAST
        LIMIT $1
        """,
        limit,
    )
    total = await fetch_val(
        "SELECT COUNT(*) FROM fpe_message_processing_state "
        "WHERE status='failed' AND attempts >= 5"
    ) or 0
    return {"total": int(total), "items": [dict(r) for r in rows]}


@router.post(
    "/admin/dlq/{fpe_wa_message_id}/requeue",
    dependencies=[Depends(_require_api_key)],
)
async def admin_dlq_requeue(
    fpe_wa_message_id: int,
    reviewer: str = Query(..., min_length=1, max_length=128),
    reset_attempts: bool = Query(True),
):
    """Re-enqueue a DLQ message: status -> pending. attempts reset by default."""
    row = await fetch_one(
        "SELECT id, status, attempts FROM fpe_message_processing_state "
        "WHERE fpe_wa_message_id = $1",
        fpe_wa_message_id,
    )
    if not row:
        raise HTTPException(404, "processing state not found")
    new_attempts = 0 if reset_attempts else row["attempts"]
    await execute(
        """
        UPDATE fpe_message_processing_state
        SET status = 'pending',
            attempts = $2,
            last_error = NULL
        WHERE fpe_wa_message_id = $1
        """,
        fpe_wa_message_id, new_attempts,
    )
    log.warning(
        "[fpe.admin] DLQ requeue fpe_wa_message_id=%d by=%s reset_attempts=%s",
        fpe_wa_message_id, reviewer, reset_attempts,
    )
    await _audit_log(
        review_item_id=None,
        action="dlq_requeue",
        actor=reviewer,
        old_state={
            "fpe_wa_message_id": fpe_wa_message_id,
            "status": row["status"],
            "attempts": row["attempts"],
        },
        new_state={"status": "pending", "attempts": new_attempts},
    )
    return {
        "status": "requeued",
        "fpe_wa_message_id": fpe_wa_message_id,
        "attempts": new_attempts,
    }


@router.post("/admin/gap-scan/trigger", dependencies=[Depends(_require_api_key)])
async def admin_gap_scan_trigger(chat_jids: Optional[list[str]] = None):
    """Run an immediate ID-based gap scan across all bridges."""
    runs = await run_gap_scan_once(chat_jids)
    return {
        "runs": runs,
        "totals": {
            "missing": sum(r.get("missing_count", 0) for r in runs),
            "backfilled": sum(r.get("backfilled", 0) for r in runs),
            "skipped_no_content": sum(r.get("skipped_no_content", 0) for r in runs),
        },
    }


@router.get("/admin/gap-scan/runs", dependencies=[Depends(_require_api_key)])
async def admin_gap_scan_runs(limit: int = Query(50, ge=1, le=500)):
    rows = await fetch_all(
        """
        SELECT id, source, chat_jid, sqlite_count, archive_count,
               missing_count, backfilled, duration_ms, error,
               started_at, finished_at
        FROM fpe_gap_scan_runs
        ORDER BY started_at DESC
        LIMIT $1
        """,
        limit,
    )
    return {"runs": [dict(r) for r in rows]}


@router.get("/admin/review-summary", dependencies=[Depends(_require_api_key)])
async def admin_review_summary():
    """Dashboard cards for /payroll/review."""
    await _cleanup_stale_review_queue_rows()
    pending = await fetch_val(
        "SELECT COUNT(*) FROM fpe_unmatched_messages "
        f"WHERE review_status='pending' AND {_review_queue_candidate_sql('fpe_unmatched_messages')}"
    ) or 0
    low_conf = await fetch_val(
        "SELECT COUNT(*) FROM fpe_unmatched_messages "
        f"WHERE review_status='pending' AND {_review_queue_candidate_sql('fpe_unmatched_messages')} "
        "AND parser_confidence IS NOT NULL AND parser_confidence < 0.5"
    ) or 0
    pending_amount = await fetch_val(
        "SELECT COALESCE(SUM(detected_amount),0) FROM fpe_unmatched_messages "
        f"WHERE review_status='pending' AND {_review_queue_candidate_sql('fpe_unmatched_messages')}"
    ) or 0
    rejected = await fetch_val(
        "SELECT COUNT(*) FROM fpe_unmatched_messages "
        f"WHERE review_status IN ('dismissed','duplicate') AND {_review_queue_candidate_sql('fpe_unmatched_messages')}"
    ) or 0
    promoted_today = await fetch_val(
        "SELECT COUNT(*) FROM fpe_unmatched_messages "
        f"WHERE review_status='promoted' AND {_review_queue_candidate_sql('fpe_unmatched_messages')} "
        "AND reviewed_at::date = CURRENT_DATE"
    ) or 0
    dlq = await fetch_val(
        "SELECT COUNT(*) FROM fpe_message_processing_state "
        "WHERE status='failed' AND attempts >= 5"
    ) or 0
    return {
        "pending_review": int(pending),
        "low_confidence": int(low_conf),
        "pending_amount": str(pending_amount),
        "rejected": int(rejected),
        "promoted_today": int(promoted_today),
        "dlq": int(dlq),
    }


@router.get("/admin/audit", dependencies=[Depends(_require_api_key)])
async def admin_audit_list(
    review_item_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List immutable review audit log entries."""
    where = ["1=1"]
    args: list = []
    if review_item_id is not None:
        args.append(review_item_id)
        where.append(f"review_item_id = ${len(args)}")
    if action:
        args.append(action)
        where.append(f"action = ${len(args)}")
    if actor:
        args.append(actor)
        where.append(f"actor = ${len(args)}")
    args.extend([limit, offset])
    rows = await fetch_all(
        f"""
        SELECT id, review_item_id, action, actor,
               old_state, new_state, reason, created_at
        FROM fpe_review_audit_logs
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}
        """,
        *args,
    )
    return {"items": [dict(r) for r in rows]}


# ── Cash Transactions (dashboard view) ───────────────────────────────────────

@router.get("/cash", dependencies=[Depends(_require_api_key)])
async def list_cash_transactions(
    employee_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, description="Search by name, phone, sender, or ref"),
    sender_phone: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    amount_min: Optional[Decimal] = Query(None),
    amount_max: Optional[Decimal] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    """List cash transactions from WhatsApp Cash commands and manual entries."""
    ps = page_size
    offset = (page - 1) * ps

    conditions = ["NOT t.is_reversal"]
    params: list = []
    i = 1

    if employee_id:
        conditions.append(f"t.employee_id = ${i}"); params.append(employee_id); i += 1
    if sender_phone:
        sp = sender_phone.strip()
        conditions.append(f"t.payout_phone LIKE ${i}"); params.append(f"%{sp}%"); i += 1
    if date_from:
        conditions.append(f"t.txn_date >= ${i}"); params.append(date_from); i += 1
    if date_to:
        conditions.append(f"t.txn_date <= ${i}"); params.append(date_to); i += 1
    if amount_min is not None:
        conditions.append(f"t.amount >= ${i}"); params.append(amount_min); i += 1
    if amount_max is not None:
        conditions.append(f"t.amount <= ${i}"); params.append(amount_max); i += 1
    if q:
        qs = q.strip()
        conditions.append(
            f"(t.employee_name_raw ILIKE ${i} OR t.payout_phone LIKE ${i} "
            f"OR t.txn_ref LIKE ${i} OR e.full_name ILIKE ${i} "
            f"OR t.source_message_text ILIKE ${i})"
        )
        params.append(f"%{qs}%"); i += 1

    where = " AND ".join(conditions)
    total = await fetch_val(
        f"SELECT COUNT(*) FROM fpe_cash_transactions t "
        f"LEFT JOIN fpe_employees e ON e.id = t.employee_id WHERE {where}",
        *params,
    ) or 0

    summary_row = await fetch_one(
        f"SELECT COALESCE(SUM(t.amount),0)::numeric AS total_amount "
        f"FROM fpe_cash_transactions t "
        f"LEFT JOIN fpe_employees e ON e.id = t.employee_id WHERE {where}",
        *params,
    )
    total_amount = float(summary_row["total_amount"]) if summary_row else 0.0

    params.extend([ps, offset])
    rows = await fetch_all(
        f"""
        SELECT
            t.id, t.txn_ref,
            t.employee_id,
            COALESCE(t.employee_name_raw, e.full_name, '(unknown)') AS employee_name,
            t.amount,
            t.payout_phone       AS sender_phone,
            t.payout_method,
            t.txn_date,
            t.txn_category,
            t.accounting_period,
            t.source_message_text AS message_body,
            t.is_reversal,
            t.created_at,
            COALESCE(e.employee_id_phone, e.primary_phone, t.payout_phone) AS employee_id_phone
        FROM fpe_cash_transactions t
        LEFT JOIN fpe_employees e ON e.id = t.employee_id
        WHERE {where}
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT ${i} OFFSET ${i+1}
        """,
        *params,
    )

    pages = (total + ps - 1) // ps if ps else 1
    return {
        "total": int(total),
        "page": page,
        "page_size": ps,
        "pages": pages,
        "summary": {"total_amount": total_amount, "count": int(total)},
        "transactions": [dict(r) for r in rows],
    }


@router.get("/cash/{txn_id}", dependencies=[Depends(_require_api_key)])
async def get_cash_transaction(txn_id: int):
    """Get a single cash transaction with full employee detail."""
    row = await fetch_one(
        """
        SELECT t.*,
               COALESCE(t.employee_name_raw, e.full_name, '(unknown)') AS employee_name,
               COALESCE(e.employee_id_phone, e.primary_phone, t.payout_phone) AS employee_id_phone,
               e.department, e.status AS employee_status
        FROM fpe_cash_transactions t
        LEFT JOIN fpe_employees e ON e.id = t.employee_id
        WHERE t.id = $1
        """,
        txn_id,
    )
    if not row:
        raise HTTPException(404, "Cash transaction not found")
    return dict(row)


# ── Shorthand / Staging Payments ─────────────────────────────────────────────

@router.get("/staging-payments", dependencies=[Depends(_require_api_key)])
async def list_staging_payments(
    status: Optional[str] = Query(None, description="pending|auto_approved|unmatched"),
    q: Optional[str] = Query(None, description="Search by name or mobile"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    """List accountant cash shorthand entries from wbom_staging_payments."""
    ps = page_size
    offset = (page - 1) * ps

    conditions: list[str] = []
    params: list = []
    i = 1

    if status:
        conditions.append(f"s.status = ${i}"); params.append(status); i += 1
    if q:
        qs = q.strip()
        conditions.append(
            f"(s.extracted_name ILIKE ${i} OR s.extracted_mobile LIKE ${i} "
            f"OR e.employee_name ILIKE ${i})"
        )
        params.append(f"%{qs}%"); i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await fetch_val(
        f"SELECT COUNT(*) FROM wbom_staging_payments s "
        f"LEFT JOIN wbom_employees e ON e.employee_id = s.matched_employee_id "
        f"{where}",
        *params,
    ) or 0

    params.extend([ps, offset])
    rows = await fetch_all(
        f"""
        SELECT
            s.staging_id,
            s.created_at,
            s.extracted_name,
            s.extracted_mobile,
            s.amount,
            s.payment_method,
            s.status,
            s.sender_number,
            s.matched_employee_id,
            s.name_match_ratio,
            s.final_transaction_id,
            s.approved_by,
            COALESCE(e.employee_name, s.extracted_name, '(unknown)') AS employee_name
        FROM wbom_staging_payments s
        LEFT JOIN wbom_employees e ON e.employee_id = s.matched_employee_id
        {where}
        ORDER BY s.created_at DESC
        LIMIT ${i} OFFSET ${i+1}
        """,
        *params,
    )

    pages = (total + ps - 1) // ps if ps else 1
    return {
        "total": int(total),
        "page": page,
        "page_size": ps,
        "pages": pages,
        "items": [dict(r) for r in rows],
    }


# ── Income Transactions ───────────────────────────────────────────────────────

@router.get("/income", dependencies=[Depends(_require_api_key)])
async def list_income_transactions(
    employee_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, description="Search by name, phone, ref, or message"),
    reporter_phone: Optional[str] = Query(None),
    period: Optional[str] = Query(None, description="YYYY-MM"),
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    amount_min: Optional[Decimal] = Query(None),
    amount_max: Optional[Decimal] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    """List income transactions with summary statistics."""
    ps = page_size
    offset = (page - 1) * ps

    conditions = ["1=1"]
    params: list = []
    i = 1

    if employee_id:
        conditions.append(f"t.employee_id = ${i}"); params.append(employee_id); i += 1
    if reporter_phone:
        rp = reporter_phone.strip()
        conditions.append(f"t.reported_by_phone LIKE ${i}"); params.append(f"%{rp}%"); i += 1
    if period:
        conditions.append(f"t.accounting_period = ${i}"); params.append(period); i += 1
    if date_from:
        conditions.append(f"t.txn_date >= ${i}"); params.append(date_from); i += 1
    if date_to:
        conditions.append(f"t.txn_date <= ${i}"); params.append(date_to); i += 1
    if amount_min is not None:
        conditions.append(f"t.amount >= ${i}"); params.append(amount_min); i += 1
    if amount_max is not None:
        conditions.append(f"t.amount <= ${i}"); params.append(amount_max); i += 1
    if q:
        qs = q.strip()
        conditions.append(
            f"(t.employee_name_raw ILIKE ${i} OR t.reported_by_phone LIKE ${i} "
            f"OR t.txn_ref LIKE ${i} OR e.full_name ILIKE ${i} "
            f"OR t.source_message_text ILIKE ${i})"
        )
        params.append(f"%{qs}%"); i += 1

    where = " AND ".join(conditions)
    total = await fetch_val(
        f"SELECT COUNT(*) FROM fpe_income_transactions t "
        f"LEFT JOIN fpe_employees e ON e.id = t.employee_id WHERE {where}",
        *params,
    ) or 0

    # Summary: total, today, this month
    summary_row = await fetch_one(
        f"""
        SELECT
            COALESCE(SUM(t.amount), 0)::numeric                                   AS total_amount,
            COALESCE(SUM(t.amount) FILTER (WHERE t.txn_date = CURRENT_DATE), 0)   AS today_amount,
            COALESCE(SUM(t.amount) FILTER (WHERE t.txn_date >= date_trunc('month', CURRENT_DATE)), 0)
                                                                                   AS month_amount
        FROM fpe_income_transactions t
        LEFT JOIN fpe_employees e ON e.id = t.employee_id
        WHERE {where}
        """,
        *params,
    )
    summary = {
        "total_amount": float(summary_row["total_amount"]) if summary_row else 0.0,
        "today_amount": float(summary_row["today_amount"]) if summary_row else 0.0,
        "month_amount": float(summary_row["month_amount"]) if summary_row else 0.0,
        "count": int(total),
    }

    params.extend([ps, offset])
    rows = await fetch_all(
        f"""
        SELECT
            t.id, t.txn_ref,
            t.employee_id,
            COALESCE(t.employee_name_raw, e.full_name, '(unknown)') AS employee_name,
            t.amount,
            t.reported_by_phone,
            t.txn_date,
            t.accounting_period,
            t.source_message_text AS message_body,
            t.created_at,
            COALESCE(e.employee_id_phone, e.primary_phone, t.reported_by_phone) AS employee_id_phone,
            (t.employee_id IS NOT NULL AND e.created_source = 'income_command') AS auto_created_employee
        FROM fpe_income_transactions t
        LEFT JOIN fpe_employees e ON e.id = t.employee_id
        WHERE {where}
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT ${i} OFFSET ${i+1}
        """,
        *params,
    )

    pages = (total + ps - 1) // ps if ps else 1
    return {
        "total": int(total),
        "page": page,
        "page_size": ps,
        "pages": pages,
        "summary": summary,
        "transactions": [dict(r) for r in rows],
    }


@router.get("/income/{income_id}", dependencies=[Depends(_require_api_key)])
async def get_income_transaction(income_id: int):
    """Get a single income transaction with full detail."""
    row = await fetch_one(
        """
        SELECT t.*,
               COALESCE(t.employee_name_raw, e.full_name, '(unknown)') AS employee_name,
               COALESCE(e.employee_id_phone, e.primary_phone, t.reported_by_phone) AS employee_id_phone,
               e.department, e.status AS employee_status,
               (t.employee_id IS NOT NULL AND e.created_source = 'income_command') AS auto_created_employee
        FROM fpe_income_transactions t
        LEFT JOIN fpe_employees e ON e.id = t.employee_id
        WHERE t.id = $1
        """,
        income_id,
    )
    if not row:
        raise HTTPException(404, "Income transaction not found")
    return dict(row)


@router.post("/income", dependencies=[Depends(_require_api_key)])
async def create_income_transaction(req: IncomeCreateRequest):
    """Manually create an income transaction using existing table columns."""
    # Validate date format
    try:
        txn_date = date.fromisoformat(req.txn_date)
    except ValueError:
        raise HTTPException(400, "txn_date must be YYYY-MM-DD")

    clean_name = (req.received_from or "").strip()
    if not clean_name:
        raise HTTPException(400, "received_from is required")
    if not req.income_head.strip():
        raise HTTPException(400, "income_head is required")
    if req.amount <= 0:
        raise HTTPException(400, "amount must be positive")
    if not req.money_received_by.strip():
        raise HTTPException(400, "money_received_by is required")

    mobile = normalize_bd_phone(req.mobile_number or "") if req.mobile_number else None
    period = txn_date.strftime("%Y-%m")

    # Pack income_head and money_received_by into source_message_text
    source_text = f"[{req.income_head.strip()}] Received by: {req.money_received_by.strip()}"

    # Generate a txn_ref if not provided
    txn_ref = (req.money_receipt_no or "").strip() or None
    if not txn_ref:
        import uuid as _uuid
        txn_ref = f"MAN-{_uuid.uuid4().hex[:10].upper()}"

    # Idempotency guard on txn_ref
    existing = await fetch_val(
        "SELECT id FROM fpe_income_transactions WHERE txn_ref = $1", txn_ref
    )
    if existing:
        raise HTTPException(409, f"Income with ref '{txn_ref}' already exists (id={existing})")

    from app.database import db_conn as _db_conn
    async with _db_conn() as conn:
        new_id = await conn.fetchval(
            """
            INSERT INTO fpe_income_transactions
                (txn_ref, employee_name_raw, amount, txn_date, accounting_period,
                 reported_by_phone, source_message_text)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            txn_ref,
            clean_name,
            Decimal(str(req.amount)),
            txn_date,
            period,
            mobile,
            source_text,
        )

    row = await fetch_one("SELECT * FROM fpe_income_transactions WHERE id = $1", new_id)
    return dict(row)
