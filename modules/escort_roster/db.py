"""
Escort Roster — Database Layer

Handles all DB operations for the escort_roster module:
  - sync_program_to_roster()   — upsert a wbom_escort_programs row into escort_roster_entries
  - sync_all_programs()        — bulk sync ALL programs (safe: idempotent)
  - recalculate_entry()        — recompute pay for an existing roster entry
  - get_roster_summary()       — aggregate stats
  - get_roster_list()          — paginated, filtered list for API
  - get_roster_detail()        — single program with slip matches
  - update_roster_entry()      — partial update (inline edit)
  - get_conveyance_config()    — read conveyance/rate table
  - log_audit()                — append to escort_roster_audit_logs

remarks TEXT column may contain: clean JSON, corrupted JSON (text appended),
or plain text. All parsing must be fault-tolerant.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app.database import execute, fetch_all, fetch_one, fetch_val
from .calculations import calculate_pay, parse_date_shift

log = logging.getLogger("fazle.escort_roster.db")


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _safe_json(text: Optional[str]) -> dict:
    """Parse a remarks TEXT column that may contain JSON or corrupted JSON."""
    if not text:
        return {}
    raw = text.strip()
    # Truncate at first non-JSON character after a closing brace
    brace = raw.rfind("}")
    if brace != -1:
        raw = raw[: brace + 1]
    try:
        return json.loads(raw)
    except Exception:
        return {}


async def _get_conveyance_for_destination(destination: Optional[str]) -> Decimal:
    """Look up conveyance amount for a destination key."""
    if not destination:
        return Decimal("0")
    key = destination.strip().lower()
    row = await fetch_one(
        "SELECT conveyance_amount FROM escort_calculation_config WHERE destination_key = $1",
        key,
    )
    if row:
        return Decimal(str(row["conveyance_amount"]))
    # Fuzzy fallback: partial match
    row = await fetch_one(
        "SELECT conveyance_amount FROM escort_calculation_config "
        "WHERE $1 ILIKE '%' || destination_key || '%' OR destination_key ILIKE '%' || $1 || '%' "
        "ORDER BY LENGTH(destination_key) DESC LIMIT 1",
        key,
    )
    if row:
        return Decimal(str(row["conveyance_amount"]))
    return Decimal("0")


async def _get_shift_rate() -> Decimal:
    """Default shift rate from config (uses first row's value)."""
    val = await fetch_val(
        "SELECT shift_rate FROM escort_calculation_config ORDER BY id LIMIT 1"
    )
    return Decimal(str(val)) if val else Decimal("200")


# ────────────────────────────────────────────────────────────────────────────
# Core sync
# ────────────────────────────────────────────────────────────────────────────

async def sync_program_to_roster(program_id: int, actor: str = "system") -> dict:
    """
    Upsert a wbom_escort_programs row into escort_roster_entries.
    Computes pay fields from start_date/shift → end_date/end_shift.
    Returns the upserted row dict.
    """
    prog = await fetch_one(
        """
        SELECT p.*, e.employee_name AS _escort_name
        FROM wbom_escort_programs p
        LEFT JOIN wbom_employees e ON e.employee_id = p.escort_employee_id
        WHERE p.program_id = $1
        """,
        program_id,
    )
    if not prog:
        raise ValueError(f"Program {program_id} not found")

    # Determine start date + shift
    start_date: Optional[date] = prog.get("start_date") or prog.get("program_date")
    start_shift: str = (prog.get("shift") or "D").upper()[:1] or "D"

    # Determine end date + shift
    end_date: Optional[date] = prog.get("end_date")
    end_shift: str = (prog.get("end_shift") or "N").upper()[:1] or "N"

    # Escort name: DB column preferred, fall back to employee record
    escort_name = prog.get("escort_name") or prog.get("_escort_name")

    # Build pay calculation if dates available
    pay: dict = {}
    if start_date and end_date:
        conveyance = await _get_conveyance_for_destination(prog.get("destination"))
        shift_rate = await _get_shift_rate()
        pay = calculate_pay(start_date, start_shift, end_date, end_shift, conveyance, shift_rate)
    elif prog.get("total_payment"):
        # Legacy: use existing total_payment
        pay = {
            "total_shifts": None,
            "total_days": float(prog.get("total_days") or prog.get("day_count") or 0),
            "salary": float(prog["total_payment"]) - float(prog.get("conveyance") or 0),
            "conveyance": float(prog.get("conveyance") or 0),
            "total": float(prog["total_payment"]),
        }

    # Keep roster_status aligned with the source program lifecycle.
    # `confirmed` is a first-class roster state so confirmed programs are never
    # shown as draft after bridge source-of-truth reconciliation.
    prog_status = (prog.get("status") or "draft").strip()
    status_map = {
        "draft": "draft",
        "confirmed": "confirmed",
        "assigned": "active",
        "running": "active",
        "completed": "completed",
        "cancelled": "cancelled",
        "expired": "expired",
    }
    roster_status = status_map.get(prog_status.lower(), "draft")

    existing = await fetch_one(
        "SELECT id, calc_version FROM escort_roster_entries WHERE program_id = $1",
        program_id,
    )

    if existing:
        old_version = existing["calc_version"]
        await execute(
            """
            UPDATE escort_roster_entries SET
                mother_vessel       = $2,
                lighter_vessel      = $3,
                master_mobile       = $4,
                escort_name         = $5,
                escort_mobile       = $6,
                destination         = $7,
                start_date          = $8,
                start_shift         = $9,
                end_date            = $10,
                end_shift           = $11,
                total_shifts        = $12,
                total_days          = $13,
                salary              = $14,
                conveyance          = $15,
                total               = $16,
                release_point       = $17,
                roster_status       = $18,
                calc_version        = $19,
                escort_employee_id  = $20,
                last_synced_at      = NOW(),
                updated_at          = NOW()
            WHERE program_id = $1
            """,
            program_id,
            prog["mother_vessel"],
            prog["lighter_vessel"],
            prog.get("master_mobile"),
            escort_name,
            prog.get("escort_mobile"),
            prog.get("destination"),
            start_date,
            start_shift,
            end_date,
            end_shift,
            pay.get("total_shifts"),
            pay.get("total_days"),
            pay.get("salary"),
            pay.get("conveyance"),
            pay.get("total"),
            prog.get("release_point") or prog.get("release_location"),
            roster_status,
            old_version + 1,
            prog.get("escort_employee_id"),
        )
        action = "sync_update"
    else:
        await execute(
            """
            INSERT INTO escort_roster_entries (
                program_id, mother_vessel, lighter_vessel, master_mobile,
                escort_name, escort_mobile, destination,
                start_date, start_shift, end_date, end_shift,
                total_shifts, total_days, salary, conveyance, total,
                release_point, roster_status, escort_employee_id,
                calc_version, last_synced_at
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,1,NOW()
            )
            """,
            program_id,
            prog["mother_vessel"],
            prog["lighter_vessel"],
            prog.get("master_mobile"),
            escort_name,
            prog.get("escort_mobile"),
            prog.get("destination"),
            start_date,
            start_shift,
            end_date,
            end_shift,
            pay.get("total_shifts"),
            pay.get("total_days"),
            pay.get("salary"),
            pay.get("conveyance"),
            pay.get("total"),
            prog.get("release_point") or prog.get("release_location"),
            roster_status,
            prog.get("escort_employee_id"),
        )
        action = "sync_insert"

    await log_audit(
        program_id=program_id,
        action=action,
        new_data={**dict(prog), **pay, "roster_status": roster_status},
        performed_by=actor,
        source="sync",
    )

    row = await fetch_one(
        "SELECT * FROM escort_roster_entries WHERE program_id = $1", program_id
    )
    return dict(row) if row else {}


async def sync_all_programs(actor: str = "system") -> dict:
    """
    Bulk sync ALL wbom_escort_programs rows into escort_roster_entries.
    Idempotent — safe to re-run.
    Returns {"synced": N, "errors": [...]}.
    """
    rows = await fetch_all("SELECT program_id FROM wbom_escort_programs ORDER BY program_id")
    synced = 0
    errors = []
    for row in rows:
        pid = row["program_id"]
        try:
            await sync_program_to_roster(pid, actor=actor)
            synced += 1
        except Exception as e:
            log.warning(f"[sync_all] program_id={pid} error: {e}")
            errors.append({"program_id": pid, "error": str(e)})
    return {"synced": synced, "errors": errors}


async def recalculate_entry(program_id: int, actor: str = "system") -> dict:
    """
    Recompute pay fields for an existing roster entry.
    Source-backed rows resync from wbom_escort_programs; manual roster-only rows
    fall back to their own stored dates so the UI recalculate action never 404s.
    """
    try:
        return await sync_program_to_roster(program_id, actor=actor)
    except ValueError:
        row = await fetch_one(
            "SELECT * FROM escort_roster_entries WHERE program_id = $1", program_id
        )
        if not row:
            raise ValueError(f"Program {program_id} not found")

        old_row = dict(row)
        start_date = row.get("start_date")
        start_shift = (row.get("start_shift") or "D").upper()[:1] or "D"
        end_date = row.get("end_date")
        end_shift = (row.get("end_shift") or "N").upper()[:1] or "N"

        updates: dict[str, Any] = {}
        if start_date and end_date:
            conveyance = (
                Decimal(str(row["conveyance"]))
                if row.get("conveyance") is not None
                else await _get_conveyance_for_destination(row.get("destination"))
            )
            shift_rate = await _get_shift_rate()
            updates = calculate_pay(
                start_date,
                start_shift,
                end_date,
                end_shift,
                conveyance,
                shift_rate,
            )

        if updates:
            await execute(
                """
                UPDATE escort_roster_entries SET
                    total_shifts = $2,
                    total_days = $3,
                    salary = $4,
                    conveyance = $5,
                    total = $6,
                    updated_at = NOW()
                WHERE program_id = $1
                """,
                program_id,
                updates["total_shifts"],
                updates["total_days"],
                updates["salary"],
                updates["conveyance"],
                updates["total"],
            )
        else:
            await execute(
                "UPDATE escort_roster_entries SET updated_at = NOW() WHERE program_id = $1",
                program_id,
            )

        fresh = await fetch_one(
            "SELECT * FROM escort_roster_entries WHERE program_id = $1", program_id
        )
        await log_audit(
            program_id=program_id,
            action="recalculate",
            old_data=old_row,
            new_data=updates or {"note": "manual_roster_noop"},
            performed_by=actor,
            source="manual_recalculate",
        )
        return dict(fresh) if fresh else {}


# ────────────────────────────────────────────────────────────────────────────
# Queries
# ────────────────────────────────────────────────────────────────────────────

async def get_roster_list(
    page: int = 1,
    page_size: int = 50,
    search: Optional[str] = None,
    status: Optional[str] = None,
    start_from: Optional[str] = None,
    start_to: Optional[str] = None,
    sort_by: str = "start_date",
    sort_dir: str = "desc",
) -> dict:
    """
    Paginated roster list with search/filter.
    Returns {"items": [...], "total": N, "page": N, "page_size": N, "pages": N}.
    """
    allowed_sort = {
        "start_date", "end_date", "total", "total_shifts", "total_days",
        "mother_vessel", "lighter_vessel", "escort_name", "program_id", "created_at"
    }
    if sort_by not in allowed_sort:
        sort_by = "start_date"
    sort_dir = "DESC" if sort_dir.lower() == "desc" else "ASC"

    conditions = []
    params: list[Any] = []
    idx = 1

    if search:
        conditions.append(
            f"(e.mother_vessel ILIKE ${idx} OR e.lighter_vessel ILIKE ${idx} "
            f"OR e.escort_name ILIKE ${idx} OR e.master_mobile ILIKE ${idx} "
            f"OR e.escort_mobile ILIKE ${idx} OR e.destination ILIKE ${idx})"
        )
        params.append(f"%{search}%")
        idx += 1

    if status:
        # Filter on the operational program status, not the payroll roster_status
        conditions.append(f"p.status = ${idx}")
        params.append(status)  # preserve case (Running, Assigned, Completed, etc.)
        idx += 1

    if start_from:
        conditions.append(f"COALESCE(e.start_date, p.program_date) >= ${idx}")
        params.append(start_from)
        idx += 1

    if start_to:
        conditions.append(f"COALESCE(e.start_date, p.program_date) <= ${idx}")
        params.append(start_to)
        idx += 1

    # Always JOIN wbom_escort_programs so p.status filter works
    join_clause = "LEFT JOIN wbom_escort_programs p ON p.program_id = e.program_id"
    where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

    total = await fetch_val(
        f"SELECT COUNT(*) FROM escort_roster_entries e {join_clause} {where_clause}",
        *params,
    ) or 0

    offset = (page - 1) * page_size
    params_paged = params + [page_size, offset]
    rows = await fetch_all(
        f"""
        SELECT e.*,
               p.status AS program_status,
               p.is_historical,
               p.remarks,
               p.capacity,
               p.whatsapp_message_id
        FROM escort_roster_entries e
        {join_clause}
        {where_clause}
        ORDER BY {('COALESCE(e.start_date, p.program_date)' if sort_by == 'start_date' else 'e.' + sort_by)} {sort_dir} NULLS LAST
        LIMIT ${idx} OFFSET ${idx+1}
        """,
        *params_paged,
    )

    items = [dict(r) for r in rows]
    # Convert date/Decimal to serialisable types
    for item in items:
        for k, v in item.items():
            if isinstance(v, date):
                item[k] = v.isoformat()
            elif isinstance(v, Decimal):
                item[k] = float(v)

    pages = max(1, -(-int(total) // page_size))  # ceiling division
    return {
        "items": items,
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


async def get_roster_detail(program_id: int) -> Optional[dict]:
    """
    Full detail for a single program: roster entry + slip matches + audit log.
    """
    entry = await fetch_one(
        """
        SELECT e.*,
               p.status AS program_status, p.remarks, p.capacity,
               p.whatsapp_message_id, p.assignment_time, p.completion_time
        FROM escort_roster_entries e
        LEFT JOIN wbom_escort_programs p ON p.program_id = e.program_id
        WHERE e.program_id = $1
        """,
        program_id,
    )
    if not entry:
        return None

    result = dict(entry)
    # Serialise dates/Decimals
    for k, v in result.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)

    # Parse remarks safely
    result["remarks_parsed"] = _safe_json(result.get("remarks"))

    # Slip matches
    matches = await fetch_all(
        """
        SELECT rm.*, se.source_file AS image_path, se.escort_name AS extracted_escort_name,
               se.escort_mobile AS extracted_escort_mobile, se.confidence,
               se.raw_text, se.created_at AS extraction_date
        FROM escort_release_matches rm
        LEFT JOIN escort_slip_extractions se ON se.id = rm.extraction_id
        WHERE rm.program_id = $1
        ORDER BY rm.match_confidence DESC, rm.created_at DESC
        LIMIT 20
        """,
        program_id,
    )
    result["slip_matches"] = [dict(m) for m in matches]

    # Audit log (last 20)
    audits = await fetch_all(
        """
        SELECT id, action, performed_by, source, created_at
        FROM escort_roster_audit_logs
        WHERE program_id = $1
        ORDER BY created_at DESC
        LIMIT 20
        """,
        program_id,
    )
    result["audit_log"] = [
        {**dict(a), "created_at": a["created_at"].isoformat() if a.get("created_at") else None}
        for a in audits
    ]

    # Shift logs
    shift_logs = await fetch_all(
        """
        SELECT * FROM escort_shift_logs
        WHERE program_id = $1
        ORDER BY shift_date, shift
        """,
        program_id,
    )
    result["shift_logs"] = [
        {**dict(s), "shift_date": s["shift_date"].isoformat() if s.get("shift_date") else None}
        for s in shift_logs
    ]

    return result


async def update_roster_entry(
    program_id: int,
    updates: dict,
    actor: str = "system",
) -> Optional[dict]:
    """
    Partial update of escort_roster_entries.
    Allowed fields: notes, release_point, roster_status, conveyance, end_date, end_shift.
    """
    allowed = {
        "notes", "release_point", "roster_status", "conveyance",
        "end_date", "end_shift", "start_date", "start_shift",
        "mother_vessel", "lighter_vessel", "master_mobile",
        "escort_name", "escort_mobile",
    }
    clean = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not clean:
        raise ValueError("No valid update fields provided")

    old = await fetch_one(
        "SELECT * FROM escort_roster_entries WHERE program_id = $1", program_id
    )
    if not old:
        raise ValueError(f"Roster entry for program {program_id} not found")

    set_clauses = []
    params: list[Any] = [program_id]
    idx = 2
    for k, v in clean.items():
        set_clauses.append(f"{k} = ${idx}")
        params.append(v)
        idx += 1
    set_clauses.append(f"updated_at = NOW()")

    await execute(
        f"UPDATE escort_roster_entries SET {', '.join(set_clauses)} WHERE program_id = $1",
        *params,
    )

    # If dates changed, recalculate pay
    if any(k in clean for k in ("start_date", "start_shift", "end_date", "end_shift")):
        row = await fetch_one(
            "SELECT * FROM escort_roster_entries WHERE program_id = $1", program_id
        )
        if row and row.get("start_date") and row.get("end_date"):
            conveyance = await _get_conveyance_for_destination(row.get("destination"))
            if clean.get("conveyance") is not None:
                conveyance = Decimal(str(clean["conveyance"]))
            shift_rate = await _get_shift_rate()
            pay = calculate_pay(
                row["start_date"], row["start_shift"] or "D",
                row["end_date"], row["end_shift"] or "N",
                conveyance, shift_rate,
            )
            await execute(
                """
                UPDATE escort_roster_entries SET
                    total_shifts = $2, total_days = $3,
                    salary = $4, conveyance = $5, total = $6,
                    updated_at = NOW()
                WHERE program_id = $1
                """,
                program_id,
                pay["total_shifts"], pay["total_days"],
                pay["salary"], pay["conveyance"], pay["total"],
            )

    await log_audit(
        program_id=program_id,
        action="edit",
        old_data=dict(old),
        new_data=clean,
        performed_by=actor,
        source="api",
    )
    return await fetch_one(
        "SELECT * FROM escort_roster_entries WHERE program_id = $1", program_id
    )


async def create_roster_entry(data: dict, actor: str = "system") -> dict:
    """
    Manually create a new escort_roster_entries row.
    Generates a safe program_id using the DB sequence gap above any existing value.
    """
    # Use a transaction-safe ID: max of escort programs or roster entries + 1
    max_pid = await fetch_val(
        """
        SELECT GREATEST(
            COALESCE((SELECT MAX(program_id) FROM escort_roster_entries), 900000),
            COALESCE((SELECT MAX(program_id) FROM wbom_escort_programs), 900000)
        ) + 1
        """
    )
    program_id = int(max_pid)

    # Calculate pay if dates are provided
    salary = None
    total_shifts = None
    total_days = None
    total = None
    conveyance_val = data.get("conveyance")
    if data.get("start_date") and data.get("end_date"):
        destination = data.get("destination")
        conveyance = (
            Decimal(str(conveyance_val))
            if conveyance_val is not None
            else await _get_conveyance_for_destination(destination)
        )
        shift_rate = await _get_shift_rate()
        pay = calculate_pay(
            data["start_date"], data.get("start_shift") or "D",
            data["end_date"], data.get("end_shift") or "N",
            conveyance, shift_rate,
        )
        total_shifts = pay["total_shifts"]
        total_days = pay["total_days"]
        salary = pay["salary"]
        conveyance_val = pay["conveyance"]
        total = pay["total"]

    await execute(
        """
        INSERT INTO escort_roster_entries (
            program_id, mother_vessel, lighter_vessel, master_mobile,
            escort_name, escort_mobile, destination,
            start_date, start_shift, end_date, end_shift,
            total_shifts, total_days, salary, conveyance, total,
            release_point, roster_status, notes, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4,
            $5, $6, $7,
            $8, $9, $10, $11,
            $12, $13, $14, $15, $16,
            $17, $18, $19, NOW(), NOW()
        )
        """,
        program_id,
        data.get("mother_vessel"),
        data.get("lighter_vessel"),
        data.get("master_mobile"),
        data.get("escort_name"),
        data.get("escort_mobile"),
        data.get("destination"),
        data.get("start_date"),
        data.get("start_shift") or "D",
        data.get("end_date"),
        data.get("end_shift") or "N",
        total_shifts,
        total_days,
        salary,
        conveyance_val,
        total,
        data.get("release_point"),
        data.get("roster_status") or "draft",
        data.get("notes"),
    )

    await log_audit(
        program_id=program_id,
        action="create",
        old_data={},
        new_data=data,
        performed_by=actor,
        source="api",
    )
    row = await fetch_one(
        "SELECT * FROM escort_roster_entries WHERE program_id = $1", program_id
    )
    return dict(row)


async def get_roster_summary() -> dict:
    """Aggregate stats for dashboard widget."""
    rows = await fetch_all(
        """
        SELECT roster_status, COUNT(*) AS cnt,
               COALESCE(SUM(total), 0) AS total_amount
        FROM escort_roster_entries
        GROUP BY roster_status
        """
    )
    by_status: dict = {}
    grand_total = 0.0
    grand_count = 0
    for r in rows:
        by_status[r["roster_status"]] = {
            "count": int(r["cnt"]),
            "total_amount": float(r["total_amount"] or 0),
        }
        grand_count += int(r["cnt"])
        grand_total += float(r["total_amount"] or 0)

    active_programs = await fetch_val(
        "SELECT COUNT(*) FROM wbom_escort_programs WHERE status IN ('Assigned','Running')"
    )
    unmatched_slips = await fetch_val(
        "SELECT COUNT(*) FROM escort_release_matches WHERE admin_action IS NULL"
    )
    # True draft count = programs not yet confirmed/assigned (operational sense)
    true_draft_count = await fetch_val(
        "SELECT COUNT(*) FROM wbom_escort_programs WHERE status = 'draft'"
    )

    return {
        "by_status": by_status,
        "grand_total": grand_count,
        "grand_total_amount": round(grand_total, 2),
        "active_programs": int(active_programs or 0),
        "pending_slip_reviews": int(unmatched_slips or 0),
        "true_draft_count": int(true_draft_count or 0),
    }


# ────────────────────────────────────────────────────────────────────────────
# Audit logging
# ────────────────────────────────────────────────────────────────────────────

async def log_audit(
    program_id: int,
    action: str,
    old_data: Optional[dict] = None,
    new_data: Optional[dict] = None,
    performed_by: str = "system",
    source: str = "system",
) -> None:
    """Append a row to escort_roster_audit_logs."""
    def _safe(d: Optional[dict]) -> Optional[str]:
        if d is None:
            return None
        try:
            return json.dumps({
                k: v.isoformat() if hasattr(v, "isoformat") else
                   float(v) if isinstance(v, Decimal) else v
                for k, v in d.items()
            })
        except Exception:
            return None

    try:
        await execute(
            """
            INSERT INTO escort_roster_audit_logs
                (program_id, action, old_data, new_data, performed_by, source)
            VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)
            """,
            program_id,
            action,
            _safe(old_data),
            _safe(new_data),
            performed_by,
            source,
        )
    except Exception as e:
        log.warning(f"[audit] log_audit failed: {e}")


# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────

async def get_conveyance_config() -> list[dict]:
    rows = await fetch_all(
        "SELECT * FROM escort_calculation_config ORDER BY destination"
    )
    return [dict(r) for r in rows]


# ────────────────────────────────────────────────────────────────────────────
# Draft management
# ────────────────────────────────────────────────────────────────────────────

async def get_draft_entries(page: int = 1, page_size: int = 50) -> dict:
    """Return paginated draft entries from escort_roster_entries."""
    offset = (page - 1) * page_size
    total = await fetch_val(
        "SELECT COUNT(*) FROM escort_roster_entries WHERE roster_status = 'draft'"
    )
    rows = await fetch_all(
        """
        SELECT *
        FROM escort_roster_entries
        WHERE roster_status = 'draft'
        ORDER BY id DESC
        LIMIT $1 OFFSET $2
        """,
        page_size,
        offset,
    )
    return {
        "total": total or 0,
        "page": page,
        "page_size": page_size,
        "items": [dict(r) for r in rows],
    }


async def cleanup_draft_entries(actor: str = "system") -> dict:
    """
    Delete ALL draft entries from escort_roster_entries.
    Logs each deletion to escort_roster_audit_logs.
    Returns summary of deleted entries.
    """
    drafts = await fetch_all(
        "SELECT * FROM escort_roster_entries WHERE roster_status = 'draft'"
    )
    if not drafts:
        return {"deleted": 0, "message": "No draft entries found"}

    deleted = 0
    for draft in drafts:
        d = dict(draft)
        await log_audit(
            program_id=d.get("program_id"),
            action="draft_cleanup",
            old_data=d,
            new_data=None,
            performed_by=actor,
            source="api",
        )

    result = await execute(
        "DELETE FROM escort_roster_entries WHERE roster_status = 'draft'"
    )
    deleted = len(drafts)
    log.info(f"[cleanup_drafts] Deleted {deleted} draft entries (actor={actor})")
    return {"deleted": deleted, "message": f"Deleted {deleted} draft entries"}


async def expire_stale_drafts(hours: int = 48, actor: str = "scheduler") -> dict:
    """
    Expire stale draft entries and their source programs without hard delete.
    Keeping the rows preserves audit trail and prevents payroll/attendance gaps.
    """
    stale = await fetch_all(
        """
        SELECT * FROM escort_roster_entries
        WHERE roster_status = 'draft'
          AND created_at < NOW() - ($1 || ' hours')::INTERVAL
        """,
        str(hours),
    )
    if not stale:
        return {"expired": 0, "message": "No stale drafts found"}

    for draft in stale:
        d = dict(draft)
        await log_audit(
            program_id=d.get("program_id"),
            action="draft_expired",
            old_data=d,
            new_data={"roster_status": "expired", "program_status": "expired"},
            performed_by=actor,
            source="scheduler",
        )

    program_ids = [r["program_id"] for r in stale]
    await execute(
        """
        UPDATE escort_roster_entries
           SET roster_status = 'expired',
               updated_at = NOW()
         WHERE roster_status = 'draft'
           AND created_at < NOW() - ($1 || ' hours')::INTERVAL
        """,
        str(hours),
    )
    if program_ids:
        await execute(
            """
            UPDATE wbom_escort_programs
               SET status = 'expired'
             WHERE program_id = ANY($1::int[])
               AND status = 'draft'
            """,
            program_ids,
        )
    count = len(stale)
    log.info(f"[expire_drafts] Expired {count} stale drafts (>{hours}h, actor={actor})")
    return {"expired": count, "message": f"Expired {count} stale draft(s) older than {hours}h"}


async def reconcile_drafts_for_confirmation(
    lighter_vessel: str,
    mother_vessel: Optional[str] = None,
    actor: str = "reconciler",
) -> dict:
    """
    After a program is confirmed, delete any orphaned draft rows in
    wbom_escort_programs whose lighter_vessel fuzzy-matches the confirmed one.

    Only touches rows with status='draft'. Cleans escort_roster_entries first
    to avoid FK violations, then hard-deletes from wbom_escort_programs.
    """
    if not lighter_vessel or not lighter_vessel.strip():
        return {"reconciled": 0}

    from difflib import SequenceMatcher
    import re as _re

    lv_key = lighter_vessel.strip().lower()
    mv_key = _re.sub(r"^mv\.?\s+", "", (mother_vessel or "").lower()).strip()

    candidates = await fetch_all(
        """
        SELECT program_id, mother_vessel, lighter_vessel
        FROM wbom_escort_programs
        WHERE status = 'draft'
          AND lighter_vessel IS NOT NULL AND lighter_vessel != ''
        ORDER BY program_id DESC
        LIMIT 300
        """,
    )

    to_delete: list[int] = []
    for row in candidates:
        lv_sim = SequenceMatcher(
            None, lv_key, (row["lighter_vessel"] or "").strip().lower()
        ).ratio()
        if lv_sim < 0.75:
            continue
        # Optional mother vessel filter — skip if both non-empty and too different
        if mv_key:
            mv_row = _re.sub(r"^mv\.?\s+", "", (row["mother_vessel"] or "").lower()).strip()
            if mv_row and SequenceMatcher(None, mv_key, mv_row).ratio() < 0.60:
                continue
        to_delete.append(row["program_id"])

    if not to_delete:
        return {"reconciled": 0}

    # Remove from roster mirror first (FK dependency)
    await execute(
        "DELETE FROM escort_roster_entries WHERE program_id = ANY($1::int[])",
        to_delete,
    )

    for pid in to_delete:
        await log_audit(
            program_id=pid,
            action="draft_reconciled",
            old_data={"lighter_vessel": lighter_vessel, "reason": "superseded_by_confirmation"},
            new_data=None,
            performed_by=actor,
            source="reconciler",
        )

    await execute(
        "DELETE FROM wbom_escort_programs WHERE program_id = ANY($1::int[]) AND status = 'draft'",
        to_delete,
    )

    log.info(
        f"[reconcile_drafts] Reconciled {len(to_delete)} drafts "
        f"for lighter={lighter_vessel!r} (actor={actor})"
    )
    return {"reconciled": len(to_delete), "program_ids": to_delete}


async def cleanup_empty_drafts(min_age_hours: int = 1, actor: str = "scheduler") -> dict:
    """
    Delete draft rows in wbom_escort_programs that have NO meaningful data
    (empty mother_vessel, lighter_vessel, and escort_name) and are at least
    min_age_hours old. These are junk rows from failed OCR / partial extractions.
    """
    empty_rows = await fetch_all(
        """
        SELECT program_id FROM wbom_escort_programs
        WHERE status = 'draft'
          AND (mother_vessel  IS NULL OR TRIM(mother_vessel)  = '')
          AND (lighter_vessel IS NULL OR TRIM(lighter_vessel) = '')
          AND (escort_name    IS NULL OR TRIM(escort_name)    = '')
          AND assignment_time < NOW() - ($1 || ' hours')::INTERVAL
        ORDER BY program_id ASC
        """,
        str(min_age_hours),
    )
    if not empty_rows:
        return {"deleted": 0, "message": "No empty draft programs found"}

    ids = [r["program_id"] for r in empty_rows]

    # Clean up roster mirror first (FK)
    await execute(
        "DELETE FROM escort_roster_entries WHERE program_id = ANY($1::int[])",
        ids,
    )
    await execute(
        "DELETE FROM wbom_escort_programs WHERE program_id = ANY($1::int[]) AND status = 'draft'",
        ids,
    )

    log.info(f"[cleanup_empty_drafts] Deleted {len(ids)} empty draft programs (actor={actor})")
    return {"deleted": len(ids), "message": f"Deleted {len(ids)} empty draft programs"}


async def cleanup_junk_drafts(actor: str = "system") -> dict:
    """
    Delete draft programs where lighter_vessel looks like a junk partial extraction:
    numbered list lines ("15. VESSEL, CAPACITY: ..., PHONE: ..."),
    raw contact headers, or other clear non-vessel content.
    FK-safe: deletes escort_roster_entries first.
    """
    junk_rows = await fetch_all(
        """
        SELECT program_id, mother_vessel, lighter_vessel
        FROM wbom_escort_programs
        WHERE status = 'draft'
          AND lighter_vessel IS NOT NULL
          AND TRIM(lighter_vessel) != ''
          AND (
              lighter_vessel ~ '^[0-9]+\.\s'
              OR lighter_vessel ILIKE '%CAPACITY:%'
              OR lighter_vessel ILIKE '%PHONE:%'
              OR lighter_vessel ILIKE '%Contact number%'
              OR lighter_vessel ILIKE '%S/N:%'
              OR lighter_vessel ILIKE 'SI. No%'
              OR lighter_vessel ILIKE '@BR-%'
          )
        ORDER BY program_id ASC
        """
    )
    if not junk_rows:
        return {"deleted": 0, "message": "No junk draft programs found"}

    ids = [r["program_id"] for r in junk_rows]

    await execute(
        "DELETE FROM escort_roster_entries WHERE program_id = ANY($1::int[])",
        ids,
    )
    await execute(
        "DELETE FROM wbom_escort_programs WHERE program_id = ANY($1::int[]) AND status = 'draft'",
        ids,
    )

    for row in junk_rows:
        await log_audit(
            program_id=row["program_id"],
            action="junk_draft_cleanup",
            old_data=dict(row),
            new_data=None,
            performed_by=actor,
            source="cleanup",
        )

    log.info(f"[cleanup_junk_drafts] Deleted {len(ids)} junk draft programs (actor={actor})")
    return {"deleted": len(ids), "program_ids": ids}


async def delete_draft_program(program_id: int, actor: str = "api") -> dict:
    """
    Permanently delete a single draft program (both escort_roster_entries and
    wbom_escort_programs rows).  Refuses if the program is not in draft status.
    """
    prog = await fetch_one(
        "SELECT program_id, status, mother_vessel FROM wbom_escort_programs WHERE program_id = $1",
        program_id,
    )
    if not prog:
        return {"error": f"Program {program_id} not found"}
    if prog["status"] != "draft":
        return {"error": f"Program {program_id} is '{prog['status']}', only draft programs can be deleted"}

    await execute(
        "DELETE FROM escort_roster_entries WHERE program_id = $1",
        program_id,
    )
    await execute(
        "DELETE FROM wbom_escort_programs WHERE program_id = $1 AND status = 'draft'",
        program_id,
    )
    log.info(f"[delete_draft] Deleted draft program_id={program_id} (actor={actor})")
    return {"deleted": True, "program_id": program_id}


async def upsert_conveyance_config(
    destination: str,
    conveyance_amount: float,
    shift_rate: float = 200.0,
    actor: str = "system",
) -> dict:
    key = destination.strip().lower()
    await execute(
        """
        INSERT INTO escort_calculation_config
            (destination, destination_key, conveyance_amount, shift_rate, updated_by, updated_at)
        VALUES ($1, $2, $3, $4, $5, NOW())
        ON CONFLICT (destination_key) DO UPDATE SET
            conveyance_amount = EXCLUDED.conveyance_amount,
            shift_rate        = EXCLUDED.shift_rate,
            updated_by        = EXCLUDED.updated_by,
            updated_at        = NOW()
        """,
        destination.strip(),
        key,
        conveyance_amount,
        shift_rate,
        actor,
    )
    row = await fetch_one(
        "SELECT * FROM escort_calculation_config WHERE destination_key = $1", key
    )
    return dict(row) if row else {}
