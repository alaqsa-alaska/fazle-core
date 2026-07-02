"""
Fazle Core — Admin Transaction CRUD Routes
Phase 19: Add / Edit / Soft-Delete on fpe_cash_transactions + smart employee matching.

Rules:
  - NEVER hard-delete financial history
  - Soft delete via deleted_at / deleted_by columns
  - Employee identity anchor: employee_id_phone (never mutated)
  - All mutations require X-Internal-Key header
  - Ledger is adjusted whenever amount or accounting_period changes

Smart employee matching (resolve_or_create_employee):
  Rule A: employee_id_phone exact match → return existing employee
  Rule B: payout_phone exact match on primary_phone → return existing employee
  Rule C: name fuzzy match with confidence > 0.95 (and no phones provided)
  Rule D: nothing matches → auto-create new employee
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import re

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, field_validator

from app.config import get_settings
from app.database import db_conn, execute, fetch_all, fetch_one, fetch_val
from modules.fazle_payroll_engine.accounting import create_transaction
from modules.fazle_payroll_engine.models import (
    PayoutMethod,
    TransactionCreateRequest,
    TxnCategory,
)
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
from modules.fazle_payroll_engine.payment_event import payment_event_from_manual

log = logging.getLogger("fazle.admin_transactions")

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


_OFFICE_ASSISTANT_NAMES = {"officeassistant01", "officeassistant02"}


def _normalize_identity(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _is_restricted_office_assistant_name(name: Optional[str]) -> bool:
    return _normalize_identity(name) in _OFFICE_ASSISTANT_NAMES


async def _get_actor_context(api_key: str) -> dict:
    settings = get_settings()
    if api_key and api_key == settings.internal_api_key:
        return {
            "name": "owner",
            "username": "",
            "phone": "",
            "roles": ["owner", "superadmin"],
            "normalized_name": "owner",
            "normalized_roles": ["owner", "superadmin"],
            "is_restricted_office_assistant": False,
            "can_manage_admin": True,
            "can_edit_delete_transactions": True,
            "permissions": {
                "can_view_admin_users": True,
                "can_add_user": True,
                "can_change_role": True,
                "can_deactivate_user": True,
                "can_reset_login_key": True,
                "can_view_admin_audit": True,
                "can_view_accounting_audit": True,
            },
        }

    try:
        from modules import rbac
        admin = await rbac.get_admin_by_api_key(api_key)
        if not admin:
            return {
                "name": "",
                "phone": "",
                "roles": [],
                "normalized_name": "",
                "normalized_roles": [],
                "is_restricted_office_assistant": False,
                "can_manage_admin": False,
                "can_edit_delete_transactions": False,
                "permissions": {
                    "can_view_admin_users": False,
                    "can_add_user": False,
                    "can_change_role": False,
                    "can_deactivate_user": False,
                    "can_reset_login_key": False,
                    "can_view_admin_audit": False,
                    "can_view_accounting_audit": False,
                },
            }

        roles = await rbac.get_roles(int(admin["id"]))
        normalized_name = _normalize_identity(admin.get("name"))
        normalized_roles = [_normalize_identity(role) for role in roles]
        can_manage_admin = any(role in {"admin", "owner", "superadmin"} for role in normalized_roles)
        can_edit = await rbac.check_permission(api_key=api_key, command="edit")
        can_view_users = await rbac.check_permission(api_key=api_key, command="user_list")
        can_add_user = await rbac.check_permission(api_key=api_key, command="user_add")
        can_change_role = await rbac.check_permission(api_key=api_key, command="user_role")
        can_deactivate_user = await rbac.check_permission(api_key=api_key, command="user_remove")
        can_reset_login = await rbac.check_permission(api_key=api_key, command="user_apikey")
        is_restricted_office_assistant = _is_restricted_office_assistant_name(admin.get("name"))
        return {
            "id": admin.get("id"),
            "name": admin.get("name") or "",
            "username": admin.get("username") or "",
            "phone": admin.get("phone") or "",
            "roles": roles,
            "normalized_name": normalized_name,
            "normalized_roles": normalized_roles,
            "is_restricted_office_assistant": is_restricted_office_assistant,
            "can_manage_admin": can_manage_admin,
            "can_edit_delete_transactions": bool(can_edit.get("allowed")) and not is_restricted_office_assistant,
            "permissions": {
                "can_view_admin_users": bool(can_view_users.get("allowed")),
                "can_add_user": bool(can_add_user.get("allowed")),
                "can_change_role": bool(can_change_role.get("allowed")),
                "can_deactivate_user": bool(can_deactivate_user.get("allowed")),
                "can_reset_login_key": bool(can_reset_login.get("allowed")),
                "can_view_admin_audit": bool(can_view_users.get("allowed")),
                "can_view_accounting_audit": bool(can_manage_admin),
            },
        }
    except Exception:
        return {
            "name": "",
            "phone": "",
            "roles": [],
            "normalized_name": "",
            "normalized_roles": [],
            "is_restricted_office_assistant": False,
            "can_manage_admin": False,
            "can_edit_delete_transactions": False,
            "permissions": {
                "can_view_admin_users": False,
                "can_add_user": False,
                "can_change_role": False,
                "can_deactivate_user": False,
                "can_reset_login_key": False,
                "can_view_admin_audit": False,
                "can_view_accounting_audit": False,
            },
        }


async def _require_transaction_mutation_access(api_key: str, action: str) -> dict:
    """Allow transaction mutations only for actors with edit permission and non-restricted identities."""
    actor = await _get_actor_context(api_key)
    if not actor.get("can_edit_delete_transactions"):
        raise HTTPException(status_code=403, detail=f"Not allowed to {action} transactions")
    return actor


async def _require_admin_console_access(api_key: str) -> dict:
    actor = await _get_actor_context(api_key)
    if not actor.get("can_manage_admin"):
        raise HTTPException(status_code=403, detail="Not allowed to access admin console")
    return actor


def _actor_label(actor: dict) -> str:
    return actor.get("name") or actor.get("phone") or "unknown"


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _session_payload(actor: dict) -> dict:
    return {
        "ok": True,
        "user": {
            "id": actor.get("id"),
            "name": actor.get("name") or "",
            "phone": actor.get("phone") or "",
            "username": actor.get("username") or "",
            "roles": actor.get("roles") or [],
            "normalized_name": actor.get("normalized_name") or "",
            "normalized_roles": actor.get("normalized_roles") or [],
            "is_restricted_office_assistant": bool(actor.get("is_restricted_office_assistant")),
            "can_edit_delete_transactions": bool(actor.get("can_edit_delete_transactions")),
            "can_manage_admin": bool(actor.get("can_manage_admin")),
            "permissions": actor.get("permissions") or {},
        },
    }


@router.post("/login")
async def admin_login(payload: dict):
    settings = get_settings()
    mode = str((payload or {}).get("mode") or "key").strip().lower()

    if mode in {"key", "api_key"}:
        key = str((payload or {}).get("key") or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="key required")
        if key != settings.internal_api_key:
            try:
                from modules import rbac
                admin = await rbac.get_admin_by_api_key(key)
                if not (admin and admin.get("status") == "active"):
                    raise HTTPException(status_code=401, detail="Invalid key")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=401, detail="Invalid key")
        actor = await _get_actor_context(key)
        return {
            **_session_payload(actor),
            "credential": {"type": "key", "value": key},
            "auth_mode": "key",
        }

    if mode not in {"password", "username"}:
        raise HTTPException(status_code=400, detail="mode must be key or password")

    username = str((payload or {}).get("username") or "").strip()
    password = str((payload or {}).get("password") or "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")

    from modules import rbac
    admin = await rbac.get_admin_by_username(username)
    if not admin or admin.get("status") != "active":
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not rbac.verify_password(password, admin.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = rbac.new_login_token()
    await execute(
        "UPDATE fazle_admins SET login_token_hash=$1, last_seen_at=NOW() WHERE id=$2",
        rbac.hash_api_key(token),
        admin["id"],
    )
    actor = await _get_actor_context(token)
    return {
        **_session_payload(actor),
        "credential": {"type": "token", "value": token},
        "auth_mode": "password",
    }


@router.get("/session", dependencies=[Depends(_require_api_key)])
async def get_admin_session(key: str = Depends(_require_api_key)):
    actor = await _get_actor_context(key)
    return _session_payload(actor)


@router.get("/accounting-audit", dependencies=[Depends(_require_api_key)])
async def get_accounting_audit(
    limit: int = Query(50, ge=1, le=500),
    entity_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    key: str = Depends(_require_api_key),
):
    await _require_admin_console_access(key)

    clauses = []
    params: list = []
    if entity_type:
        clauses.append(f"entity_type = ${len(params) + 1}")
        params.append(entity_type.strip().lower())
    if action:
        clauses.append(f"action = ${len(params) + 1}")
        params.append(action.strip().lower())

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = await fetch_all(
        f"""
        SELECT id, entity_type, entity_id, action, before_state, after_state,
               performed_by, reason, created_at
        FROM fpe_accounting_audit_logs
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ${len(params)}
        """,
        *params,
    )
    return {"count": len(rows), "audit": [dict(row) for row in rows]}


# ── Request / Response models ─────────────────────────────────────────────────

class AdminTxnCreate(BaseModel):
    employee_name: str
    employee_id_phone: Optional[str] = None  # identity anchor (PHONE used as employee ID)
    payout_phone: Optional[str] = None        # actual payment phone (may differ)
    amount: Decimal
    payout_method: str = "cash"
    txn_date: date
    txn_category: str = "salary"
    notes: Optional[str] = None               # stored as source_message_text

    @field_validator("payout_method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        allowed = {"bkash", "nagad", "cash", "bank", "unknown"}
        v = v.lower().strip()
        if v not in allowed:
            raise ValueError(f"payout_method must be one of {allowed}")
        return v

    @field_validator("txn_category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        allowed = {"salary", "advance", "bonus", "deduction", "other"}
        v = v.lower().strip()
        if v not in allowed:
            raise ValueError(f"txn_category must be one of {allowed}")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("amount must be positive")
        return v

    @field_validator("employee_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("employee_name is required")
        return v

    @field_validator("employee_id_phone", "payout_phone", mode="before")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = str(v).strip()
        # Must contain 7–15 digits, optionally with +/spaces/dashes
        if not re.match(r'^[\+\s\-\(\)0-9]{7,20}$', v):
            raise ValueError(f"Invalid phone number format: {v}")
        return v


class AdminTxnUpdate(BaseModel):
    employee_name_raw: Optional[str] = None
    payout_phone: Optional[str] = None
    amount: Optional[Decimal] = None
    payout_method: Optional[str] = None
    txn_date: Optional[date] = None
    txn_category: Optional[str] = None
    notes: Optional[str] = None  # stored as source_message_text

    @field_validator("payout_method")
    @classmethod
    def validate_method(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"bkash", "nagad", "cash", "bank", "unknown"}
        v = v.lower().strip()
        if v not in allowed:
            raise ValueError(f"payout_method must be one of {allowed}")
        return v

    @field_validator("txn_category")
    @classmethod
    def validate_category(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"salary", "advance", "bonus", "deduction", "other"}
        v = v.lower().strip()
        if v not in allowed:
            raise ValueError(f"txn_category must be one of {allowed}")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("amount must be positive")
        return v

    @field_validator("payout_phone", mode="before")
    @classmethod
    def validate_update_phone(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        v = str(v).strip()
        if not re.match(r'^[\+\s\-\(\)0-9]{7,20}$', v):
            raise ValueError(f"Invalid phone number format: {v}")
        return v


# ── Smart employee matching ───────────────────────────────────────────────────

async def resolve_or_create_employee(
    name: str,
    employee_id_phone: Optional[str],
    payout_phone: Optional[str],
) -> dict:
    """
    Return an fpe_employees row, creating one if needed.

    Rule A: employee_id_phone exact match  → use that employee (even if name differs)
    Rule B: payout_phone exact match       → use that employee
    Rule C: name fuzzy match >0.95         → use that employee (no phones given)
    Rule D: nothing found                  → auto-create with name + phone
    """
    norm_id_phone = normalize_bd_phone(employee_id_phone) if employee_id_phone else None
    norm_payout   = normalize_bd_phone(payout_phone)       if payout_phone       else None

    # Rule A — employee_id_phone is the strongest identity anchor
    if norm_id_phone:
        row = await fetch_one(
            "SELECT id, employee_code, full_name, primary_phone, employee_id_phone "
            "FROM fpe_employees WHERE employee_id_phone = $1 AND status = 'active'",
            norm_id_phone,
        )
        if row:
            log.info("[admin_txn] Rule A match id_phone=%s emp_id=%d", norm_id_phone, row["id"])
            return dict(row)

    # Rule B — payout phone matches primary_phone
    if norm_payout:
        row = await fetch_one(
            "SELECT id, employee_code, full_name, primary_phone, employee_id_phone "
            "FROM fpe_employees WHERE primary_phone = $1 AND status = 'active'",
            norm_payout,
        )
        if row:
            log.info("[admin_txn] Rule B match payout_phone=%s emp_id=%d", norm_payout, row["id"])
            return dict(row)

    # Rule C — no phones provided; try fuzzy name match
    if not norm_id_phone and not norm_payout and name:
        from modules.fazle_payroll_engine.employee import normalize_name
        name_norm = normalize_name(name)
        if name_norm:
            # Exact normalized name match
            row = await fetch_one(
                "SELECT id, employee_code, full_name, primary_phone, employee_id_phone "
                "FROM fpe_employees WHERE name_normalized = $1 AND status = 'active'",
                name_norm,
            )
            if row:
                log.info("[admin_txn] Rule C exact-name match emp_id=%d", row["id"])
                return dict(row)

            # Fuzzy name — use pg_trgm similarity (>= 0.95)
            row = await fetch_one(
                """
                SELECT id, employee_code, full_name, primary_phone, employee_id_phone,
                       similarity(name_normalized, $1) AS sim
                FROM fpe_employees
                WHERE name_normalized IS NOT NULL
                  AND status = 'active'
                  AND similarity(name_normalized, $1) >= 0.95
                ORDER BY similarity(name_normalized, $1) DESC
                LIMIT 1
                """,
                name_norm,
            )
            if row:
                log.info("[admin_txn] Rule C fuzzy-name match emp_id=%d sim=%.3f",
                         row["id"], row["sim"])
                return dict(row)

    # Rule D — create a new employee
    return await _create_admin_employee(name, norm_id_phone, norm_payout)


async def _create_admin_employee(
    full_name: str,
    employee_id_phone: Optional[str],
    primary_phone: Optional[str],
) -> dict:
    """
    Insert a new fpe_employees row with sequential EMP-XXXXX code.
    Uses the same pattern as the WhatsApp auto-create path.
    """
    from modules.fazle_payroll_engine.employee import normalize_name

    name_norm = normalize_name(full_name) or full_name.lower()

    async with db_conn() as conn:
        new_id: int = await conn.fetchval(
            """
            INSERT INTO fpe_employees
                (full_name, name_normalized, primary_phone, employee_id_phone,
                 status, created_source, resolution_status, confidence_score)
            VALUES ($1, $2, $3, $4, 'active', 'admin_manual', 'auto_created', 1.0)
            RETURNING id
            """,
            full_name,
            name_norm,
            primary_phone,
            employee_id_phone,
        )
        # Assign EMP-XXXXX code (same pattern as WhatsApp auto-create)
        code = f"EMP-{new_id:05d}"
        await conn.execute(
            "UPDATE fpe_employees SET employee_code = $1 WHERE id = $2",
            code, new_id,
        )

    log.info("[admin_txn] auto-created employee id=%d code=%s name=%s", new_id, code, full_name)

    return {
        "id": new_id,
        "employee_code": code,
        "full_name": full_name,
        "primary_phone": primary_phone,
        "employee_id_phone": employee_id_phone,
    }


# ── Ledger helpers ────────────────────────────────────────────────────────────

async def _adjust_ledger(
    employee_id: int,
    period: str,
    amount: Decimal,
    category: str,
) -> None:
    """Increment/decrement ledger bucket for employee+period (pass negative for reversal)."""
    from modules.fazle_payroll_engine.accounting import _upsert_ledger
    from modules.fazle_payroll_engine.models import TxnCategory

    try:
        cat = TxnCategory(category)
    except ValueError:
        cat = TxnCategory.salary

    await _upsert_ledger(employee_id, period, amount, cat)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/transactions", dependencies=[Depends(_require_api_key)])
async def add_admin_transaction(body: AdminTxnCreate, key: str = Depends(_require_api_key)):
    """
    Add a new payment transaction with smart employee matching.
    Creates or resolves the employee based on name + phones.
    Writes the canonical FPE transaction via create_transaction().
    """
    actor = await _require_transaction_mutation_access(key, "create")
    emp = await resolve_or_create_employee(
        body.employee_name,
        body.employee_id_phone,
        body.payout_phone,
    )

    # Normalise payout_phone using resolved employee phone if not supplied
    payout_phone = normalize_bd_phone(body.payout_phone) if body.payout_phone else emp.get("primary_phone")

    method = PayoutMethod(body.payout_method) if body.payout_method in PayoutMethod._value2member_map_ else PayoutMethod.cash
    category = TxnCategory(body.txn_category) if body.txn_category in TxnCategory._value2member_map_ else TxnCategory.salary

    event = payment_event_from_manual(
        employee_id=emp["id"],
        employee_name_raw=body.employee_name,
        amount=body.amount,
        payout_method=method,
        payout_phone=payout_phone,
        txn_date=body.txn_date,
        txn_category=category,
        source_message_text=body.notes or f"Admin manual entry — {body.employee_name}",
        created_by=_actor_label(actor),
        metadata={"admin_api": True, "employee_id_phone": body.employee_id_phone},
    )
    req = TransactionCreateRequest(**event.model_dump())
    txn = await create_transaction(req)

    log.info("[admin_txn] created txn id=%d emp=%d ref=%s", txn.id, emp["id"], txn.txn_ref[:16])
    new_row = await fetch_one("SELECT updated_at FROM fpe_cash_transactions WHERE id = $1", txn.id)
    return {
        "ok": True,
        "transaction_id": txn.id,
        "employee_id": emp["id"],
        "employee_code": emp.get("employee_code"),
        "full_name": emp.get("full_name"),
        "txn_ref": txn.txn_ref,
        "updated_at": new_row["updated_at"].isoformat() if new_row and new_row["updated_at"] else None,
    }


@router.put("/transactions/{txn_id}")
async def edit_admin_transaction(
    txn_id: int = Path(..., ge=1),
    body: AdminTxnUpdate = ...,
    x_if_match_updated_at: Optional[str] = Header(None),
    key: str = Depends(_require_api_key),
):
    """
    Edit an existing transaction. Adjusts ledger when amount or date changes.
    Non-financial edits (name, phone, notes) are applied directly.
    """
    actor = await _require_transaction_mutation_access(key, "edit")

    row = await fetch_one(
        "SELECT id, employee_id, employee_name_raw, amount, payout_phone, "
        "payout_method, txn_date, txn_category, accounting_period, deleted_at, "
        "updated_at, source_message_text "
        "FROM fpe_cash_transactions WHERE id = $1",
        txn_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found")
    if row["deleted_at"] is not None:
        raise HTTPException(status_code=409, detail="Transaction is soft-deleted")

    # Optimistic lock: if caller sent X-If-Match-Updated-At, compare with current DB value
    if x_if_match_updated_at:
        try:
            client_ts = datetime.fromisoformat(x_if_match_updated_at.replace("Z", "+00:00"))
            db_ts = row["updated_at"]
            if db_ts and abs((db_ts.replace(tzinfo=None) if db_ts.tzinfo else db_ts) -
                             (client_ts.replace(tzinfo=None) if client_ts.tzinfo else client_ts)
                             ).total_seconds() > 1:
                raise HTTPException(
                    status_code=409,
                    detail="Conflict: transaction was modified by another process. Reload and retry.",
                )
        except HTTPException:
            raise
        except Exception:
            pass  # ignore unparseable header; proceed without lock check

    old_amount  = row["amount"]
    old_period  = row["accounting_period"]
    old_cat     = row["txn_category"]
    employee_id = row["employee_id"]

    # Compute new values
    new_amount  = body.amount       if body.amount       is not None else old_amount
    new_date    = body.txn_date     if body.txn_date     is not None else row["txn_date"]
    new_period  = new_date.strftime("%Y-%m") if body.txn_date else old_period
    new_cat     = body.txn_category if body.txn_category else old_cat
    new_method  = body.payout_method or row["payout_method"]
    new_phone   = normalize_bd_phone(body.payout_phone) if body.payout_phone else row["payout_phone"]
    new_name    = body.employee_name_raw.strip() if body.employee_name_raw else row["employee_name_raw"]
    new_notes   = body.notes if body.notes is not None else row.get("source_message_text")

    # Build and apply the UPDATE
    sets = []
    params: list = []
    i = 1

    for col, val in [
        ("employee_name_raw",   new_name),
        ("payout_phone",        new_phone),
        ("amount",              new_amount),
        ("payout_method",       new_method),
        ("txn_date",            new_date),
        ("txn_category",        new_cat),
        ("accounting_period",   new_period),
        ("source_message_text", new_notes),
    ]:
        sets.append(f"{col} = ${i}")
        params.append(val)
        i += 1

    params.append(txn_id)
    await execute(
        f"UPDATE fpe_cash_transactions SET {', '.join(sets)} WHERE id = ${i}",
        *params,
    )

    # Audit log
    await execute(
        """
        INSERT INTO fpe_accounting_audit_logs
            (entity_type, entity_id, action, before_state, after_state, performed_by, reason)
        VALUES ('transaction', $1, 'admin_edit', $2::jsonb, $3::jsonb, $4, $5)
        """,
        txn_id,
        json.dumps({
            "employee_name_raw": row["employee_name_raw"],
            "payout_phone": row["payout_phone"],
            "amount": str(old_amount),
            "accounting_period": old_period,
            "txn_category": old_cat,
        }, default=_json_default),
        json.dumps({
            "employee_name_raw": new_name,
            "payout_phone": new_phone,
            "amount": str(new_amount),
            "accounting_period": new_period,
            "txn_category": new_cat,
        }, default=_json_default),
        _actor_label(actor),
        "module=payroll; detail=admin transaction edit",
    )

    # Adjust ledger if amount/period changed and employee is set
    if employee_id and (new_amount != old_amount or new_period != old_period):
        # Undo old
        await _adjust_ledger(employee_id, old_period, -old_amount, old_cat)
        # Apply new
        await _adjust_ledger(employee_id, new_period, new_amount, new_cat)

    log.info("[admin_txn] edited txn id=%d", txn_id)
    new_row = await fetch_one("SELECT updated_at FROM fpe_cash_transactions WHERE id = $1", txn_id)
    return {"ok": True, "transaction_id": txn_id, "updated_at": new_row["updated_at"].isoformat() if new_row and new_row["updated_at"] else None}


@router.delete("/transactions/{txn_id}")
async def soft_delete_transaction(
    txn_id: int = Path(..., ge=1),
    deleted_by: str = Query("admin_manual"),
    key: str = Depends(_require_api_key),
):
    """
    Soft-delete a transaction. Sets deleted_at/deleted_by and reverses the ledger.
    The row is NEVER physically removed.
    """
    actor = await _require_transaction_mutation_access(key, "delete")

    row = await fetch_one(
        "SELECT id, employee_id, amount, txn_category, accounting_period, deleted_at "
        "FROM fpe_cash_transactions WHERE id = $1",
        txn_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Transaction {txn_id} not found")
    if row["deleted_at"] is not None:
        raise HTTPException(status_code=409, detail="Transaction already deleted")

    now = datetime.utcnow()
    await execute(
        "UPDATE fpe_cash_transactions SET deleted_at = $1, deleted_by = $2 WHERE id = $3",
        now, deleted_by, txn_id,
    )

    # Audit log
    await execute(
        """
        INSERT INTO fpe_accounting_audit_logs
            (entity_type, entity_id, action, before_state, performed_by, reason)
        VALUES ('transaction', $1, 'admin_soft_delete', $2::jsonb, $3, $4)
        """,
        txn_id,
        json.dumps({
            "id": txn_id,
            "amount": str(row["amount"]),
            "accounting_period": row["accounting_period"],
        }, default=_json_default),
        _actor_label(actor),
        f"module=payroll; deleted_by={deleted_by}",
    )

    # Reverse ledger impact
    employee_id = row["employee_id"]
    if employee_id:
        await _adjust_ledger(
            employee_id,
            row["accounting_period"],
            -row["amount"],
            row["txn_category"],
        )

    log.info("[admin_txn] soft-deleted txn id=%d by=%s", txn_id, deleted_by)
    return {"ok": True, "transaction_id": txn_id, "deleted_at": now.isoformat()}
