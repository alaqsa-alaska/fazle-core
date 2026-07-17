"""
Fazle Core — Client Billing Profiles (Phase 2B)

CRUD against wbom_client_billing_profiles (db/migrations/034_client_billing_profiles.sql,
035_client_billing_wage_general.sql).

Contact Person Mobile is validated against the live ESCORT_CLIENT_PHONES
.env list (re-read on every call, never cached) — reusing
modules.escort_roster.routes's existing _escort_client_phone_list and
modules.fazle_payroll_engine.normalizer's normalize_bd_phone/to_880
rather than duplicating phone-validation logic.
.env remains the sole authority for that list: this module only reads it.

employee_wage_general/24h/12h/monthly exist for the profit-ESTIMATION
calculation on this table only. They must never feed into, override, or be
read by modules.payroll, modules.payment_workflow, or any real
guard-payment calculation — see docs/CANONICAL_BUSINESS_RULES.md Payroll
section.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg
from fastapi import HTTPException

from app.database import execute, fetch_all, fetch_one, fetch_val
from modules.escort_roster.routes import _escort_client_phone_list
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone, to_880

_NUMERIC_FIELDS = (
    "general_rate", "rate_24h", "rate_12h", "rate_monthly",
    "employee_wage_general", "employee_wage_24h", "employee_wage_12h", "employee_wage_monthly",
    "vat_amount", "tax_amount", "other_charges",
)


async def _validate_contact_mobile(raw: str) -> str:
    """Normalize + require membership in the current ESCORT_CLIENT_PHONES list."""
    normalized = to_880(normalize_bd_phone(raw))
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid Bangladesh mobile number")
    authorized = _escort_client_phone_list()
    if normalized not in authorized:
        raise HTTPException(
            status_code=400,
            detail=f"Mobile number {normalized} is not in the authorized "
                    "ESCORT_CLIENT_PHONES list. Add it there first via the "
                    "existing /api/escort-roster/escort-clients endpoint.",
        )
    return normalized


def _validate_numeric_fields(fields: dict[str, Any]) -> None:
    for key in _NUMERIC_FIELDS:
        val = fields.get(key)
        if val is not None and val < 0:
            raise HTTPException(status_code=400, detail=f"{key} must not be negative")


async def _validate_contact_id(contact_id: Optional[int]) -> None:
    if contact_id is None:
        return
    exists = await fetch_val("SELECT 1 FROM wbom_contacts WHERE contact_id = $1", contact_id)
    if not exists:
        raise HTTPException(status_code=400, detail=f"contact_id {contact_id} does not exist")


async def create_client_billing_profile(
    client_name: str,
    contact_person_mobile: str,
    *,
    office_address: Optional[str] = None,
    contact_person_name: Optional[str] = None,
    chairman_name: Optional[str] = None,
    chairman_mobile: Optional[str] = None,
    managing_member_name: Optional[str] = None,
    managing_member_mobile: Optional[str] = None,
    general_rate: Optional[float] = None,
    rate_24h: Optional[float] = None,
    rate_12h: Optional[float] = None,
    rate_monthly: Optional[float] = None,
    employee_wage_general: Optional[float] = None,
    employee_wage_24h: Optional[float] = None,
    employee_wage_12h: Optional[float] = None,
    employee_wage_monthly: Optional[float] = None,
    vat_amount: float = 0,
    tax_amount: float = 0,
    other_charges: float = 0,
    notes: Optional[str] = None,
    contact_id: Optional[int] = None,
) -> dict:
    if not client_name or not client_name.strip():
        raise HTTPException(status_code=400, detail="client_name is required")

    normalized_mobile = await _validate_contact_mobile(contact_person_mobile)
    fields = dict(
        general_rate=general_rate, rate_24h=rate_24h, rate_12h=rate_12h, rate_monthly=rate_monthly,
        employee_wage_general=employee_wage_general,
        employee_wage_24h=employee_wage_24h, employee_wage_12h=employee_wage_12h,
        employee_wage_monthly=employee_wage_monthly,
        vat_amount=vat_amount, tax_amount=tax_amount, other_charges=other_charges,
    )
    _validate_numeric_fields(fields)
    await _validate_contact_id(contact_id)

    try:
        row = await fetch_one(
            """
            INSERT INTO wbom_client_billing_profiles (
                client_name, office_address, contact_person_name, contact_person_mobile,
                chairman_name, chairman_mobile, managing_member_name, managing_member_mobile,
                general_rate, rate_24h, rate_12h, rate_monthly,
                employee_wage_general, employee_wage_24h, employee_wage_12h, employee_wage_monthly,
                vat_amount, tax_amount, other_charges, notes, contact_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21)
            RETURNING *
            """,
            client_name.strip(), office_address, contact_person_name, normalized_mobile,
            chairman_name, chairman_mobile, managing_member_name, managing_member_mobile,
            general_rate, rate_24h, rate_12h, rate_monthly,
            employee_wage_general, employee_wage_24h, employee_wage_12h, employee_wage_monthly,
            vat_amount, tax_amount, other_charges, notes, contact_id,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail="This mobile number is already registered to another client profile",
        )
    return dict(row)


async def update_client_billing_profile(client_id: int, **fields: Any) -> dict:
    existing = await fetch_one(
        "SELECT * FROM wbom_client_billing_profiles WHERE client_id = $1", client_id
    )
    if not existing:
        raise HTTPException(status_code=404, detail=f"client_id {client_id} not found")

    if "contact_person_mobile" in fields and fields["contact_person_mobile"] is not None:
        fields["contact_person_mobile"] = await _validate_contact_mobile(fields["contact_person_mobile"])
    if "client_name" in fields and fields["client_name"] is not None and not fields["client_name"].strip():
        raise HTTPException(status_code=400, detail="client_name must not be empty")
    _validate_numeric_fields(fields)
    if "contact_id" in fields:
        await _validate_contact_id(fields["contact_id"])

    settable = {k: v for k, v in fields.items() if v is not None or k in (
        "office_address", "contact_person_name", "chairman_name", "chairman_mobile",
        "managing_member_name", "managing_member_mobile", "notes", "contact_id",
    )}
    if not settable:
        return dict(existing)

    set_clauses = []
    params: list[Any] = []
    for i, (key, val) in enumerate(settable.items(), start=1):
        set_clauses.append(f"{key} = ${i}")
        params.append(val)
    set_clauses.append(f"updated_at = now()")
    params.append(client_id)

    try:
        row = await fetch_one(
            f"UPDATE wbom_client_billing_profiles SET {', '.join(set_clauses)} "
            f"WHERE client_id = ${len(params)} RETURNING *",
            *params,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail="This mobile number is already registered to another client profile",
        )
    return dict(row)


async def list_client_billing_profiles(active_only: bool = True) -> list[dict]:
    if active_only:
        rows = await fetch_all(
            "SELECT * FROM wbom_client_billing_profiles WHERE is_active = true ORDER BY client_name"
        )
    else:
        rows = await fetch_all("SELECT * FROM wbom_client_billing_profiles ORDER BY client_name")
    return [dict(r) for r in rows]


async def get_client_billing_profile(client_id: int) -> Optional[dict]:
    row = await fetch_one("SELECT * FROM wbom_client_billing_profiles WHERE client_id = $1", client_id)
    return dict(row) if row else None


async def delete_client_billing_profile(client_id: int) -> dict:
    """Hard delete (Owner decision, 2026-07-10 — supersedes the original
    soft-delete default). Bills already generated against this client are
    unaffected: wbom_billing_records references contact_id, not client_id,
    and stores service_charge/total_amount at generation time rather than a
    live reference back to the profile row."""
    row = await fetch_one(
        "DELETE FROM wbom_client_billing_profiles WHERE client_id = $1 RETURNING *",
        client_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"client_id {client_id} not found")
    return dict(row)
