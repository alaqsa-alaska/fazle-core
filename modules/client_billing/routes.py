"""
Client Billing Profile — FastAPI Router

All routes under /admin/client-billing-profiles.
Auth: X-Internal-Key header or an active admin's API key, matching
modules.escort_roster.routes's exact pattern (reused, not duplicated).

RBAC (Owner decision, 2026-07-10): client rates, employee wages, and
profit-estimate figures are commercially sensitive — visible to `admin` and
`superadmin` roles only (role level >= 70), no one else (not `accountant`,
`operator`, or `viewer`, even though those roles hold a valid, active API
key that `_require_key` alone would accept). Previously every route here
was gated only by `_require_key`, which checks "is this any valid active
admin-tier key" without checking role — a real gap, since e.g. an
`operator`-tier HR-officer key would have passed it. `_require_admin_or_above`
closes that gap.

  GET    /admin/client-billing-profiles              — list (admin+/superadmin)
  GET    /admin/client-billing-profiles/{client_id}   — view one (admin+/superadmin)
  POST   /admin/client-billing-profiles               — add (admin+/superadmin)
  PUT    /admin/client-billing-profiles/{client_id}   — edit (admin+/superadmin)
  DELETE /admin/client-billing-profiles/{client_id}   — hard delete (superadmin only)
  GET    /admin/escort-clients/authorized-numbers     — read-only list of
                                                         currently-authorized
                                                         Contact Person Mobile
                                                         candidates (admin+)

Phase 2B Commit C — manual bill generation (see bills.py docstring for
why this is a manual admin action rather than an automatic hook):

  GET    /admin/bills/billable-programs               — Completed programs
                                                         eligible for billing (admin+/superadmin)
  GET    /admin/bills                                  — list bills (admin+/superadmin)
  GET    /admin/bills/{bill_id}                        — view one bill (admin+/superadmin)
  POST   /admin/bills/generate                         — generate a bill (admin+/superadmin)

Registered in app/main.py:
  from modules.client_billing.routes import router as client_billing_router
  app.include_router(client_billing_router)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from modules.escort_roster.routes import (
    _API_KEY_HEADER,
    _escort_client_phone_list,
    _require_key,
    _resolve_actor,
)

from .bills import generate_bill, get_bill, list_bills, list_billable_programs
from .profiles import (
    create_client_billing_profile,
    delete_client_billing_profile,
    get_client_billing_profile,
    list_client_billing_profiles,
    update_client_billing_profile,
)

router = APIRouter(tags=["client-billing"])


async def _require_superadmin(key: str = Depends(_API_KEY_HEADER)) -> dict:
    """Delete is destructive (hard delete) — gate at superadmin,
    matching this system's existing tier for user_add/user_role/user_remove."""
    actor = await _resolve_actor(key)
    roles = {str(r or "").strip().lower() for r in actor.get("roles", [])}
    if actor.get("is_internal") or roles.intersection({"superadmin", "owner"}):
        return actor
    raise HTTPException(status_code=403, detail="superadmin required")


async def _require_admin_or_above(key: str = Depends(_API_KEY_HEADER)) -> dict:
    """Client rates/wages/profit figures — admin or superadmin only (Owner
    decision 2026-07-10). Excludes accountant/operator/viewer tiers even
    though _require_key alone would accept any of them."""
    actor = await _resolve_actor(key)
    roles = {str(r or "").strip().lower() for r in actor.get("roles", [])}
    if actor.get("is_internal") or roles.intersection({"admin", "superadmin", "owner"}):
        return actor
    raise HTTPException(status_code=403, detail="admin or superadmin required")


class ClientBillingProfileIn(BaseModel):
    client_name: str
    contact_person_mobile: str
    office_address: Optional[str] = None
    contact_person_name: Optional[str] = None
    chairman_name: Optional[str] = None
    chairman_mobile: Optional[str] = None
    managing_member_name: Optional[str] = None
    managing_member_mobile: Optional[str] = None
    general_rate: Optional[float] = None
    rate_24h: Optional[float] = None
    rate_12h: Optional[float] = None
    rate_monthly: Optional[float] = None
    employee_wage_general: Optional[float] = None
    employee_wage_24h: Optional[float] = None
    employee_wage_12h: Optional[float] = None
    employee_wage_monthly: Optional[float] = None
    vat_amount: float = 0
    tax_amount: float = 0
    other_charges: float = 0
    notes: Optional[str] = None
    contact_id: Optional[int] = None


class ClientBillingProfilePatch(BaseModel):
    client_name: Optional[str] = None
    contact_person_mobile: Optional[str] = None
    office_address: Optional[str] = None
    contact_person_name: Optional[str] = None
    chairman_name: Optional[str] = None
    chairman_mobile: Optional[str] = None
    managing_member_name: Optional[str] = None
    managing_member_mobile: Optional[str] = None
    general_rate: Optional[float] = None
    rate_24h: Optional[float] = None
    rate_12h: Optional[float] = None
    rate_monthly: Optional[float] = None
    employee_wage_general: Optional[float] = None
    employee_wage_24h: Optional[float] = None
    employee_wage_12h: Optional[float] = None
    employee_wage_monthly: Optional[float] = None
    vat_amount: Optional[float] = None
    tax_amount: Optional[float] = None
    other_charges: Optional[float] = None
    notes: Optional[str] = None
    contact_id: Optional[int] = None


@router.get("/admin/client-billing-profiles", dependencies=[Depends(_require_admin_or_above)])
async def api_list_client_billing_profiles(active: bool = True):
    return {"profiles": await list_client_billing_profiles(active_only=active)}


@router.get("/admin/client-billing-profiles/{client_id}", dependencies=[Depends(_require_admin_or_above)])
async def api_get_client_billing_profile(client_id: int):
    profile = await get_client_billing_profile(client_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"client_id {client_id} not found")
    return profile


@router.post("/admin/client-billing-profiles", dependencies=[Depends(_require_admin_or_above)])
async def api_create_client_billing_profile(body: ClientBillingProfileIn):
    return await create_client_billing_profile(**body.model_dump())


@router.put("/admin/client-billing-profiles/{client_id}", dependencies=[Depends(_require_admin_or_above)])
async def api_update_client_billing_profile(client_id: int, body: ClientBillingProfilePatch):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return await update_client_billing_profile(client_id, **fields)


@router.delete("/admin/client-billing-profiles/{client_id}")
async def api_delete_client_billing_profile(client_id: int, actor: dict = Depends(_require_superadmin)):
    return await delete_client_billing_profile(client_id)


@router.get("/admin/escort-clients/authorized-numbers", dependencies=[Depends(_require_key)])
async def api_authorized_client_numbers():
    """Read-only view of the current ESCORT_CLIENT_PHONES list, for the
    Contact Person Mobile dropdown. Never writes to it — that stays the
    job of modules.escort_roster.routes's existing /escort-clients endpoints."""
    return {"phones": _escort_client_phone_list()}


class GenerateBillIn(BaseModel):
    program_id: int
    duty_type: str
    other_charges: float = 0
    remarks: Optional[str] = None


@router.get("/admin/bills/billable-programs", dependencies=[Depends(_require_admin_or_above)])
async def api_list_billable_programs():
    return {"programs": await list_billable_programs()}


@router.get("/admin/bills", dependencies=[Depends(_require_admin_or_above)])
async def api_list_bills():
    return {"bills": await list_bills()}


@router.get("/admin/bills/{bill_id}", dependencies=[Depends(_require_admin_or_above)])
async def api_get_bill(bill_id: int):
    bill = await get_bill(bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail=f"bill_id {bill_id} not found")
    return bill


@router.post("/admin/bills/generate", dependencies=[Depends(_require_admin_or_above)])
async def api_generate_bill(body: GenerateBillIn):
    return await generate_bill(
        program_id=body.program_id,
        duty_type=body.duty_type,
        other_charges=body.other_charges,
        remarks=body.remarks,
    )
