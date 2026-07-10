"""
Fazle Core — Client Bill Generation (Phase 2B, Commit C)

Generates a wbom_billing_records row from a Completed escort program and
its client's billing profile (db/migrations/034/035), using
invoice_number_counters/next_invoice_number() (db/migrations/033) for the
bill_number.

Design decision, disclosed (not part of any prior Owner decision):
docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md's original Commit C sketch
assumed an AUTOMATIC hook inside modules.escort_lifecycle's
_handle_release_event_tx(), firing on every first-time Completed
transition. That flow is triggered by a live WhatsApp "[RELEASE
CONFIRMED]" admin message and has no field anywhere (program-side or
parsed from the release text) to carry a duty_type/billing-frequency
selection — wbom_escort_programs has no such column, and duty_type
(General/24H/12H/Monthly) is a distinct concept from the existing
start/end `shift` (D/N) field. Silently auto-generating a real,
invoice-numbered client bill inside a live production messaging path,
using a guessed default duty_type, was judged higher-risk than building
a manual, opt-in admin action — no completed program today even has a
matching client billing profile, so there is no live data to have
validated an automatic path against yet. This module instead exposes a
manual "Generate Bill" action (routes.py), matching how the Client
Billing Profile feature itself was built. Wiring an automatic trigger
into escort_lifecycle remains a separate, explicit follow-up decision.

Owner Decision Required #2 (program_id uniqueness) resolved via
db/migrations/036 with a disclosed default: one bill per program.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg
from fastapi import HTTPException

from app.database import fetch_all, fetch_one, fetch_val, get_pool
from modules.billing.invoice_numbering import next_invoice_number

_DUTY_TYPE_FIELDS = {
    "general": ("general_rate", "employee_wage_general"),
    "24h": ("rate_24h", "employee_wage_24h"),
    "12h": ("rate_12h", "employee_wage_12h"),
    "monthly": ("rate_monthly", "employee_wage_monthly"),
}


async def list_billable_programs() -> list[dict]:
    """Completed programs with a contact_id set, an active billing profile
    for that contact, and not already billed — the admin's selection pool
    for the Generate Bill action."""
    rows = await fetch_all(
        """
        SELECT p.program_id, p.mother_vessel, p.lighter_vessel, p.contact_id,
               p.program_date, p.end_date, cbp.client_id, cbp.client_name
        FROM wbom_escort_programs p
        JOIN wbom_client_billing_profiles cbp
            ON cbp.contact_id = p.contact_id AND cbp.is_active = true
        WHERE lower(COALESCE(p.status, '')) = 'completed'
          AND p.contact_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM wbom_billing_records b WHERE b.program_id = p.program_id
          )
        ORDER BY p.program_date DESC, p.program_id DESC
        """
    )
    return [dict(r) for r in rows]


async def _resolve_billing_profile(contact_id: int) -> dict:
    profiles = await fetch_all(
        "SELECT * FROM wbom_client_billing_profiles WHERE contact_id = $1 AND is_active = true",
        contact_id,
    )
    if not profiles:
        raise HTTPException(
            status_code=400,
            detail=f"No active client billing profile found for contact_id {contact_id}. "
                   "Create one in Client Settings first.",
        )
    if len(profiles) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"contact_id {contact_id} matches more than one active client billing "
                   "profile — resolve this data conflict before generating a bill.",
        )
    return dict(profiles[0])


async def generate_bill(
    program_id: int,
    duty_type: str,
    *,
    other_charges: float = 0,
    remarks: Optional[str] = None,
) -> dict:
    duty_key = (duty_type or "").strip().lower()
    if duty_key not in _DUTY_TYPE_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"duty_type must be one of {sorted(_DUTY_TYPE_FIELDS)}",
        )
    if other_charges < 0:
        raise HTTPException(status_code=400, detail="other_charges must not be negative")

    program = await fetch_one(
        "SELECT program_id, status, contact_id FROM wbom_escort_programs WHERE program_id = $1",
        program_id,
    )
    if not program:
        raise HTTPException(status_code=404, detail=f"program_id {program_id} not found")
    if (program["status"] or "").strip().lower() != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"program_id {program_id} is not Completed (status={program['status']!r})",
        )
    contact_id = program["contact_id"]
    if contact_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"program_id {program_id} has no linked contact_id; link it to a client "
                   "contact before generating a bill.",
        )

    profile = await _resolve_billing_profile(contact_id)
    rate_field, wage_field = _DUTY_TYPE_FIELDS[duty_key]
    rate = profile.get(rate_field)
    if rate is None:
        raise HTTPException(
            status_code=400,
            detail=f"{rate_field} is not configured for client {profile['client_name']!r} "
                   f"(client_id={profile['client_id']}); set it in Client Settings first.",
        )

    service_charge = float(rate)
    total_amount = service_charge + float(other_charges)

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            bill_number = await next_invoice_number(conn=conn)
            try:
                row = await fetch_one(
                    """
                    INSERT INTO wbom_billing_records
                        (program_id, contact_id, bill_date, bill_number,
                         service_charge, other_charges, total_amount, payment_status, remarks)
                    VALUES ($1, $2, CURRENT_DATE, $3, $4, $5, $6, 'Pending', $7)
                    RETURNING *
                    """,
                    program_id, contact_id, bill_number,
                    service_charge, other_charges, total_amount, remarks,
                    conn=conn,
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(
                    status_code=409,
                    detail=f"program_id {program_id} has already been billed",
                )

    result = dict(row)
    wage = profile.get(wage_field)
    result["estimated_profit"] = (
        service_charge
        - float(wage or 0)
        - float(profile.get("vat_amount") or 0)
        - float(profile.get("tax_amount") or 0)
        - float(profile.get("other_charges") or 0)
        if wage is not None else None
    )
    result["duty_type"] = duty_key
    result["client_name"] = profile["client_name"]
    return result


async def list_bills() -> list[dict]:
    rows = await fetch_all(
        """
        SELECT b.*, cbp.client_name
        FROM wbom_billing_records b
        LEFT JOIN wbom_client_billing_profiles cbp ON cbp.contact_id = b.contact_id
        ORDER BY b.bill_date DESC, b.bill_id DESC
        """
    )
    return [dict(r) for r in rows]


async def get_bill(bill_id: int) -> Optional[dict]:
    row = await fetch_one(
        """
        SELECT b.*, cbp.client_name
        FROM wbom_billing_records b
        LEFT JOIN wbom_client_billing_profiles cbp ON cbp.contact_id = b.contact_id
        WHERE b.bill_id = $1
        """,
        bill_id,
    )
    return dict(row) if row else None
