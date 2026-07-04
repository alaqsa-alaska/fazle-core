"""
Fazle Core — Admin Employee CRUD Routes
Phase 18B: Safe Add/Edit/Deactivate on top of wbom_employees.

Rules:
  - NEVER hard-delete rows (soft-delete via status='Inactive')
  - employee_mobile is immutable (identity anchor)
  - After INSERT, pre-seed FPE via match_or_create_employee (non-fatal)
  - Deactivate propagates to fpe_employees by phone (non-fatal)
  - All mutations require X-Internal-Key header
"""
import logging
import math
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, field_validator

from app.config import get_settings
from app.database import execute, fetch_all, fetch_one, fetch_val
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone

log = logging.getLogger("fazle.admin_employees")

router = APIRouter(prefix="/api/admin")

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


# ── Request models ─────────────────────────────────────────────────────────────

class EmployeeCreate(BaseModel):
    employee_name: str
    employee_mobile: str
    designation: str = "Staff"
    joining_date: Optional[str] = None   # ISO date string e.g. "2024-01-15"
    bkash_number: Optional[str] = None
    nagad_number: Optional[str] = None
    basic_salary: Optional[float] = None
    nid_number: Optional[str] = None
    emergency_contact: Optional[str] = None
    address: Optional[str] = None
    bank_account: Optional[str] = None

    @field_validator("employee_name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("employee_name must not be blank")
        return v.strip()

    @field_validator("designation")
    @classmethod
    def desig_not_blank(cls, v: str) -> str:
        return v.strip() if v else "Staff"


class EmployeeUpdate(BaseModel):
    employee_name: Optional[str] = None
    designation: Optional[str] = None
    joining_date: Optional[str] = None   # ISO date or empty string to clear
    bkash_number: Optional[str] = None
    nagad_number: Optional[str] = None
    basic_salary: Optional[float] = None
    nid_number: Optional[str] = None
    emergency_contact: Optional[str] = None
    address: Optional[str] = None
    status: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_dict(row) -> dict:
    """Convert asyncpg Record to JSON-serializable dict (dates → ISO strings)."""
    import datetime
    d = {}
    for k, v in dict(row).items():
        if isinstance(v, (datetime.date, datetime.datetime)):
            d[k] = v.isoformat()
        elif isinstance(v, float) and (v != v):  # NaN guard
            d[k] = None
        else:
            d[k] = v
    return d


async def _seed_fpe(name: str, mobile: str, wbom_employee_id: int | None = None) -> None:
    """Pre-seed FPE with new employee and set wbom_employee_id link.  Non-fatal."""
    try:
        from modules.fazle_payroll_engine.employee import match_or_create_employee
        from app.database import execute as _exec
        result = await match_or_create_employee(
            name_raw=name,
            payout_phone=mobile,
            employee_id_phone=mobile,
        )
        # Set the WBOM link so both tables stay in sync
        if result and wbom_employee_id:
            await _exec(
                "UPDATE fpe_employees SET wbom_employee_id = $1 WHERE id = $2 AND wbom_employee_id IS NULL",
                wbom_employee_id, result.employee_id,
            )
    except Exception as exc:
        log.warning("[admin_employees] FPE seed failed mobile=%s: %s", mobile, exc)


# ── POST /api/admin/employees ─────────────────────────────────────────────────

@router.post("/employees", dependencies=[Depends(_require_api_key)], status_code=201)
async def create_employee(body: EmployeeCreate):
    """Create a new employee in wbom_employees, then pre-seed FPE."""
    norm_mobile = normalize_bd_phone(body.employee_mobile)
    if not norm_mobile:
        raise HTTPException(400, f"Invalid mobile number: {body.employee_mobile!r}")

    existing = await fetch_one(
        "SELECT employee_id FROM wbom_employees WHERE employee_mobile = $1",
        norm_mobile,
    )
    if existing:
        raise HTTPException(
            409,
            f"Employee with mobile {norm_mobile} already exists "
            f"(employee_id={existing['employee_id']})",
        )

    joining_date = body.joining_date if body.joining_date and body.joining_date.strip() else None

    emp_id = await fetch_val(
        """INSERT INTO wbom_employees
               (employee_name, employee_mobile, designation, joining_date,
                bkash_number, nagad_number, basic_salary, nid_number,
                emergency_contact, address, bank_account, status,
                created_at, updated_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'Active',now(),now())
           RETURNING employee_id""",
        body.employee_name,
        norm_mobile,
        body.designation,
        joining_date,
        body.bkash_number or None,
        body.nagad_number or None,
        body.basic_salary,
        body.nid_number or None,
        body.emergency_contact or None,
        body.address or None,
        body.bank_account or None,
    )

    row = await fetch_one(
        "SELECT * FROM wbom_employees WHERE employee_id = $1", emp_id
    )

    await _seed_fpe(body.employee_name, norm_mobile, wbom_employee_id=emp_id)

    log.info("[admin_employees] created employee_id=%d mobile=%s", emp_id, norm_mobile)
    return {"employee": _row_to_dict(row), "fpe_seeded": True}


# ── GET /api/admin/employees ──────────────────────────────────────────────────

@router.get("/employees", dependencies=[Depends(_require_api_key)])
async def list_employees(
    status: str = Query("active"),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    sort: str = Query("name_asc"),
):
    """List wbom_employees with filter/search/pagination."""
    # Normalize status — DB stores 'Active'/'Inactive'
    status_val = "Active" if status.lower() in ("active", "1", "true") else "Inactive"
    offset = (page - 1) * page_size

    # Build WHERE clause dynamically
    conditions = ["status = $1"]
    params: list = [status_val]
    idx = 2

    if q and q.strip():
        conditions.append(
            f"(employee_name ILIKE ${idx} OR employee_mobile ILIKE ${idx} "
            f"OR CAST(employee_id AS TEXT) = ${idx})"
        )
        params.append(f"%{q.strip()}%")
        idx += 1

    where_sql = " AND ".join(conditions)

    # Sort mapping
    sort_map = {
        "name_asc": "employee_name ASC",
        "name_desc": "employee_name DESC",
        "id_asc": "employee_id ASC",
        "id_desc": "employee_id DESC",
        "joined_asc": "joining_date ASC NULLS LAST",
        "joined_desc": "joining_date DESC NULLS LAST",
    }
    order_sql = sort_map.get(sort, "employee_name ASC")

    total = await fetch_val(
        f"SELECT COUNT(*) FROM wbom_employees WHERE {where_sql}",
        *params,
    )

    rows = await fetch_all(
        f"""SELECT * FROM wbom_employees
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT ${idx} OFFSET ${idx + 1}""",
        *params, page_size, offset,
    )

    return {
        "employees": [_row_to_dict(r) for r in rows],
        "total": total or 0,
        "page": page,
        "pages": max(1, math.ceil((total or 0) / page_size)),
    }


# ── GET /api/admin/employees/by-phone/{phone} ─────────────────────────────────
# NOTE: must be defined BEFORE /{emp_id} to avoid routing conflict

@router.get("/employees/by-phone/{phone}", dependencies=[Depends(_require_api_key)])
async def get_employee_by_phone(phone: str):
    """Look up wbom employee by mobile (used by frontend Edit button)."""
    norm = normalize_bd_phone(phone)
    if not norm:
        raise HTTPException(400, f"Invalid phone: {phone!r}")
    row = await fetch_one(
        "SELECT * FROM wbom_employees WHERE employee_mobile = $1", norm
    )
    if not row:
        raise HTTPException(404, "No WBOM employee found for that phone")
    return {"employee": _row_to_dict(row)}


# ── GET /api/admin/employees/{emp_id} ────────────────────────────────────────

@router.get("/employees/{emp_id}", dependencies=[Depends(_require_api_key)])
async def get_employee(emp_id: int):
    """Get single wbom employee with related record counts."""
    row = await fetch_one(
        "SELECT * FROM wbom_employees WHERE employee_id = $1", emp_id
    )
    if not row:
        raise HTTPException(404, "Employee not found")

    counts = await fetch_one(
        """SELECT
               (SELECT COUNT(*) FROM fpe_cash_transactions   WHERE employee_id=$1 AND transaction_status='final') AS txn_count,
               (SELECT COUNT(*) FROM wbom_escort_programs    WHERE escort_employee_id=$1) AS escort_count,
               (SELECT COUNT(*) FROM wbom_attendance         WHERE employee_id=$1) AS attendance_count,
               (SELECT COUNT(*) FROM wbom_salary_records     WHERE employee_id=$1) AS salary_record_count
        """,
        emp_id,
    )

    return {
        "employee": _row_to_dict(row),
        "related_counts": dict(counts) if counts else {},
    }


# ── PUT /api/admin/employees/{emp_id} ────────────────────────────────────────

@router.put("/employees/{emp_id}", dependencies=[Depends(_require_api_key)])
async def update_employee(emp_id: int, body: EmployeeUpdate):
    """Update safe operational fields on wbom_employees."""
    existing = await fetch_one(
        "SELECT employee_id FROM wbom_employees WHERE employee_id = $1", emp_id
    )
    if not existing:
        raise HTTPException(404, "Employee not found")

    if body.status is not None and body.status not in ("Active", "Inactive"):
        raise HTTPException(400, "status must be 'Active' or 'Inactive'")

    # Build SET clause from non-None fields
    allowed_fields = {
        "employee_name", "designation", "joining_date",
        "bkash_number", "nagad_number", "basic_salary",
        "nid_number", "emergency_contact", "address", "status",
    }

    set_parts: list[str] = ["updated_at = now()"]
    params: list = []
    idx = 1

    for field in allowed_fields:
        val = getattr(body, field, None)
        if val is None:
            continue
        # Convert empty string to NULL for optional fields
        if field not in ("employee_name", "designation", "status") and val == "":
            val = None
        set_parts.append(f"{field} = ${idx}")
        params.append(val)
        idx += 1

    if len(set_parts) == 1:  # only updated_at — nothing to do
        row = await fetch_one(
            "SELECT * FROM wbom_employees WHERE employee_id=$1", emp_id
        )
        return {"employee": _row_to_dict(row)}

    params.append(emp_id)
    set_sql = ", ".join(set_parts)

    await execute(
        f"UPDATE wbom_employees SET {set_sql} WHERE employee_id = ${idx}",
        *params,
    )

    row = await fetch_one(
        "SELECT * FROM wbom_employees WHERE employee_id=$1", emp_id
    )
    log.info("[admin_employees] updated employee_id=%d", emp_id)
    return {"employee": _row_to_dict(row)}


# ── PATCH /api/admin/employees/{emp_id}/deactivate ───────────────────────────

@router.patch("/employees/{emp_id}/deactivate", dependencies=[Depends(_require_api_key)])
async def deactivate_employee(emp_id: int):
    """Soft-delete: set wbom_employees.status='Inactive' and sync to FPE."""
    row = await fetch_one(
        "SELECT employee_id, employee_name, employee_mobile, status "
        "FROM wbom_employees WHERE employee_id=$1",
        emp_id,
    )
    if not row:
        raise HTTPException(404, "Employee not found")

    if row["status"] == "Inactive":
        return {"ok": True, "message": "Already inactive", "employee_id": emp_id}

    # Soft-delete in WBOM
    await execute(
        "UPDATE wbom_employees SET status='Inactive', updated_at=now() WHERE employee_id=$1",
        emp_id,
    )

    # Propagate to FPE by phone (non-fatal)
    try:
        mobile = row["employee_mobile"]
        await execute(
            """UPDATE fpe_employees
               SET status='inactive', updated_at=now()
               WHERE (primary_phone=$1 OR employee_id_phone=$1)
                 AND status != 'inactive'""",
            mobile,
        )
        log.info(
            "[admin_employees] deactivated employee_id=%d mobile=%s", emp_id, mobile
        )
    except Exception as exc:
        log.warning("[admin_employees] FPE deactivate sync failed emp_id=%d: %s", emp_id, exc)

    return {
        "ok": True,
        "message": f"Employee '{row['employee_name']}' deactivated",
        "employee_id": emp_id,
    }


@router.get("/debug/auth-check")
async def debug_auth_check(key: str = Depends(_API_KEY_HEADER)):
    """Safe diagnostics endpoint — no auth required, returns auth analysis only."""
    settings = get_settings()
    header_present = key is not None and key != ""
    header_length = len(key) if key else 0
    env_loaded = bool(settings.internal_api_key)
    auth_ok = (key == settings.internal_api_key) if key else False
    return {
        "env_loaded": env_loaded,
        "header_present": header_present,
        "header_length": header_length,
        "auth_result": "ok" if auth_ok else "failed",
    }
