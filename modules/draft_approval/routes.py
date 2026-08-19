"""
Draft Approval (fazle_payment_drafts) — API routes (2026-08-19, Task 2 of
HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md's
approved 4-task plan).

Closes the headline capability-exposure gap that audit found: the full
canonical approve/edit/reject chain in modules.draft_approval (approve →
create_canonical_transaction() → ledger upsert → audit, all called EXACTLY
as-is, see this package's own __init__.py docstring's "HARD RULES") has
existed and been production-tested since Sprint-3B — but its only caller
was modules.admin_commands, reachable only through the admin's own literal
WhatsApp text commands (APPROVED/DREDIT/DREJECT). No HTTP route ever
wrapped it. These routes are deliberately dumb pass-throughs — no new
business logic, matching the same discipline modules/drafts/routes.py
already uses for the (structurally different) fazle_draft_replies table.

RBAC: reuses COMMAND_ROLE["paid"]/["edit"]/["reject"] (all "operator"
tier) — the EXACT tier already governing this EXACT action via the
WhatsApp admin_commands path (see modules/admin_commands's own
_classify_command()). Not a new or weaker gate.

GET /admin/payment-drafts (app/main.py) already exists for listing/
reading fazle_payment_drafts -- no new read route needed for that.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import get_settings

log = logging.getLogger("fazle.draft_approval.routes")
router = APIRouter(prefix="/api/payment-drafts", tags=["payment-drafts"])

# Local copy of the API-key dependency to avoid a circular import
# (app.main -> modules.draft_approval.routes -> app.main), matching the
# exact convention already used by every other domain module's routes.py
# (see modules/fazle_payroll_engine/routes.py, modules/attendance/routes.py).
_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


def _require_command(command: str):
    """Local reimplementation of app.main.require_command()'s logic (can't
    import it directly -- same circular-import reason as above). Calls the
    same modules.rbac.check_permission() with the same command name, so
    COMMAND_ROLE stays the single source of truth -- this does not weaken
    or duplicate the RBAC policy, only the dependency wiring."""

    async def _dep(key: str = Depends(_API_KEY_HEADER)) -> str:
        settings = get_settings()
        if key and key == settings.internal_api_key:
            return key
        from modules import rbac
        check = await rbac.check_permission(api_key=key or "", command=command)
        if check.get("allowed"):
            return key
        raise HTTPException(status_code=403, detail=check.get("reason") or "Forbidden")

    return _dep


async def _resolve_admin_identity(key: str) -> str:
    """Server-resolved actor identity from the API key only -- never a
    client-supplied body field. Mirrors modules/drafts/routes.py's own
    _resolve_reviewer_identity() exactly (duplicated, not imported,
    matching this codebase's established per-module convention for these
    small auth helpers)."""
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return "owner-internal-key"
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin:
                return admin.get("phone") or admin.get("name") or f"admin-{admin.get('id')}"
        except Exception:
            pass
    return "unknown-frontend-admin"


# ── Request models ──────────────────────────────────────────────────────────

class ApproveDraftBody(BaseModel):
    amount: float
    method: str


class EditDraftBody(BaseModel):
    new_amount: Optional[float] = None
    new_method: Optional[str] = None
    new_payout_mobile: Optional[str] = None
    reason: Optional[str] = None


class RejectDraftBody(BaseModel):
    reason: Optional[str] = None


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/{draft_id}/approve", dependencies=[Depends(_require_command("paid"))])
async def approve_payment_draft_route(
    draft_id: int, body: ApproveDraftBody, key: str = Depends(_API_KEY_HEADER)
):
    """Approve a pending fazle_payment_drafts row -- creates the canonical
    transaction + ledger entry via modules.draft_approval.approve_draft(),
    exactly the same function the WhatsApp `APPROVED <id> <amount>
    <method>` admin command already calls."""
    from modules.draft_approval import approve_draft
    admin_phone = await _resolve_admin_identity(key)
    result = await approve_draft(draft_id, body.amount, body.method, admin_phone)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "approve failed")
    return result


@router.post("/{draft_id}/edit", dependencies=[Depends(_require_command("edit"))])
async def edit_payment_draft_route(
    draft_id: int, body: EditDraftBody, key: str = Depends(_API_KEY_HEADER)
):
    """Edit a pending fazle_payment_drafts row (version increment,
    before/after state saved). Does NOT create a transaction -- a
    subsequent approve is still required. Mirrors the WhatsApp `DREDIT`
    admin command."""
    from modules.draft_approval import edit_draft
    admin_phone = await _resolve_admin_identity(key)
    result = await edit_draft(
        draft_id, body.new_amount, body.new_method, body.new_payout_mobile,
        admin_phone, body.reason,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "edit failed")
    return result


@router.post("/{draft_id}/reject", dependencies=[Depends(_require_command("reject"))])
async def reject_payment_draft_route(
    draft_id: int, body: RejectDraftBody, key: str = Depends(_API_KEY_HEADER)
):
    """Reject a pending fazle_payment_drafts row. No transaction, no
    ledger. Mirrors the WhatsApp `DREJECT` admin command."""
    from modules.draft_approval import reject_draft
    admin_phone = await _resolve_admin_identity(key)
    result = await reject_draft(draft_id, admin_phone, body.reason)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "reject failed")
    return result


@router.get("/{draft_id}/audit", dependencies=[Depends(_require_command("status"))])
async def get_payment_draft_audit_route(draft_id: int):
    """Read-only. All audit events recorded for one payment draft."""
    from modules.draft_approval import get_draft_audit_events
    return {"draft_id": draft_id, "events": await get_draft_audit_events(draft_id)}


@router.get("/employee/{employee_id}/balance", dependencies=[Depends(_require_command("status"))])
async def get_employee_balance_route(employee_id: int, period: Optional[str] = None):
    """Read-only. Employee balance for one accounting period (defaults to
    the current period), sourced entirely from fpe_employee_ledger -- no
    manual calculation."""
    from modules.draft_approval import get_employee_balance
    return await get_employee_balance(employee_id, period)
