"""
Persistent Task/Execution State + diff-level Action Approval — API routes
(2026-08-19, Owner-directed follow-on to the P0-P2 reliability-hardening
pass and the BUILD/RUN/business-action capability audit).

Deliberately dumb pass-throughs over modules.hermes_tasks — no business
logic here, matching the exact discipline modules/draft_approval/routes.py
already established for the structurally identical propose->approve
pattern. RBAC: task_read (viewer), task_write (operator, planning
bookkeeping), task_approve (superadmin, the one command that actually
authorizes a proposed code/infra action to proceed — same tier as
send_whatsapp_message).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import get_settings

log = logging.getLogger("fazle.hermes_tasks.routes")
router = APIRouter(tags=["hermes-tasks"])

# Local copy of the API-key dependency + require_command reimplementation,
# same circular-import-avoidance convention as modules/draft_approval/routes.py.
_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


def _require_command(command: str):
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
    client-supplied body field. Mirrors modules/draft_approval/routes.py's
    own _resolve_admin_identity() exactly."""
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


# ── Request models ───────────────────────────────────────────────────────────

class CreateTaskBody(BaseModel):
    title: str
    description: Optional[str] = None
    parent_task_id: Optional[int] = None
    priority: str = "normal"
    execution_mode: Optional[str] = None
    created_from: Optional[str] = None
    trace_id: Optional[str] = None
    depends_on: Optional[list[int]] = None


class UpdateTaskStatusBody(BaseModel):
    status: str
    next_action: Optional[str] = None
    failure_reason: Optional[str] = None
    verification: Optional[dict] = None


class ClaimTaskBody(BaseModel):
    owner: str = "earth"


class AuthorizeBuildBody(BaseModel):
    repos: list[str]
    ttl_hours: int = 4


class ProposeActionBody(BaseModel):
    task_id: Optional[int] = None
    action_type: str
    summary: str
    files_changed: Optional[list[str]] = None
    diff: Optional[str] = None
    command: Optional[str] = None
    risk: str = "medium"
    expected_effect: Optional[str] = None
    rollback_plan: Optional[str] = None


class RejectActionBody(BaseModel):
    reason: Optional[str] = None


class AuthorizeActionBody(BaseModel):
    task_id: Optional[int] = None
    action_type: str
    summary: str
    files_changed: Optional[list[str]] = None
    diff: Optional[str] = None
    command: Optional[str] = None
    risk: str = "medium"
    expected_effect: Optional[str] = None
    rollback_plan: Optional[str] = None


class ExecutionResultBody(BaseModel):
    result: dict


class ProposeBusinessActionBody(BaseModel):
    domain: str
    business_reference: str
    proposed_action_type: str
    reason: str
    evidence_refs: list[dict]
    source_event_ref: str
    verification: Optional[dict] = None


# ── Task routes ──────────────────────────────────────────────────────────────

@router.post("/api/tasks", dependencies=[Depends(_require_command("task_write"))])
async def create_task_route(body: CreateTaskBody, key: str = Depends(_API_KEY_HEADER)):
    from modules.hermes_tasks import create_task, TaskError
    actor = await _resolve_admin_identity(key)
    try:
        task_id = await create_task(
            body.title, body.description, body.parent_task_id, body.priority,
            body.execution_mode, body.created_from, body.trace_id, body.depends_on,
            created_by_phone=actor,
        )
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "task_id": task_id}


@router.get("/api/tasks", dependencies=[Depends(_require_command("task_read"))])
async def list_tasks_route(
    status: Optional[str] = None, owner: Optional[str] = None,
    parent_task_id: Optional[int] = None, limit: int = 50,
):
    from modules.hermes_tasks import list_tasks
    return {"tasks": await list_tasks(status, owner, parent_task_id, limit)}


@router.get("/api/tasks/authorizing-path", dependencies=[Depends(_require_command("task_read"))])
async def find_authorizing_task_route(path: str):
    """Read-only: the check the CLI plugin calls before allowing a
    WORKSPACE_MUTATION (file edit) via Earth's own direct file tool.
    Registered before /api/tasks/{task_id} so this fixed path is never
    shadowed by the int-typed parameter route."""
    from modules.hermes_tasks import find_authorizing_task_for_path
    result = await find_authorizing_task_for_path(path)
    return {"authorized": result is not None, "task": result}


@router.get("/api/tasks/{task_id}", dependencies=[Depends(_require_command("task_read"))])
async def get_task_route(task_id: int):
    from modules.hermes_tasks import get_task
    task = await get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"task #{task_id} not found")
    return task


@router.post("/api/tasks/{task_id}/claim", dependencies=[Depends(_require_command("task_write"))])
async def claim_task_route(task_id: int, body: ClaimTaskBody):
    from modules.hermes_tasks import claim_task, TaskError
    try:
        return await claim_task(task_id, body.owner)
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/tasks/{task_id}/authorize-build", dependencies=[Depends(_require_command("task_write"))])
async def authorize_build_route(task_id: int, body: AuthorizeBuildBody, key: str = Depends(_API_KEY_HEADER)):
    from modules.hermes_tasks import authorize_build, TaskError
    actor = await _resolve_admin_identity(key)
    try:
        return await authorize_build(task_id, body.repos, actor, body.ttl_hours)
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/api/tasks/{task_id}/status", dependencies=[Depends(_require_command("task_write"))])
async def update_task_status_route(task_id: int, body: UpdateTaskStatusBody, key: str = Depends(_API_KEY_HEADER)):
    from modules.hermes_tasks import update_task_status, TaskError
    actor = await _resolve_admin_identity(key)
    try:
        return await update_task_status(
            task_id, body.status, performed_by=actor, next_action=body.next_action,
            failure_reason=body.failure_reason, verification=body.verification,
        )
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Action approval routes ───────────────────────────────────────────────────

@router.post("/api/actions", dependencies=[Depends(_require_command("task_write"))])
async def propose_action_route(body: ProposeActionBody):
    from modules.hermes_tasks import propose_action, TaskError
    try:
        action_id = await propose_action(
            body.task_id, body.action_type, body.summary, body.files_changed,
            body.diff, body.command, body.risk, body.expected_effect, body.rollback_plan,
        )
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "action_id": action_id}


@router.post("/api/business-actions", dependencies=[Depends(_require_command("task_write"))])
async def propose_business_action_route(body: ProposeBusinessActionBody, key: str = Depends(_API_KEY_HEADER)):
    """Stage F2/F6 (2026-08-28): Business Inbox attention item ->
    structured proposed action. Same task_write tier as
    propose_action_route -- proposing costs nothing (Class B, no
    mutation); the separate task_approve tier on /api/actions/{id}/approve
    below still gates whether it may proceed toward being sent. Thin
    pass-through over modules.business_actions.propose_business_action(),
    same discipline as every other route in this file."""
    from modules.business_actions import propose_business_action, BusinessActionError
    actor = await _resolve_admin_identity(key)
    try:
        return await propose_business_action(
            body.domain, body.business_reference, body.proposed_action_type,
            body.reason, body.evidence_refs, body.source_event_ref,
            requested_by=actor if actor != "unknown-frontend-admin" else "hermes",
        )
    except BusinessActionError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/actions/authorize", dependencies=[Depends(_require_command("task_approve"))])
async def authorize_action_route(body: AuthorizeActionBody, key: str = Depends(_API_KEY_HEADER)):
    """Fused propose+approve -- task_approve (superadmin) tier since this IS
    the actual authorization-granting call, same gate as approve_action.
    Registered before /api/actions/{action_id} so this fixed path is never
    shadowed."""
    from modules.hermes_tasks import authorize_action, TaskError
    actor = await _resolve_admin_identity(key)
    try:
        action_id = await authorize_action(
            body.task_id, body.action_type, body.summary, actor, body.files_changed,
            body.diff, body.command, body.risk, body.expected_effect, body.rollback_plan,
        )
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "action_id": action_id, "status": "approved"}


@router.get("/api/actions", dependencies=[Depends(_require_command("task_read"))])
async def list_actions_route(
    task_id: Optional[int] = None, status: Optional[str] = "pending", action_type: Optional[str] = None,
    domain: Optional[str] = None,
):
    from modules.hermes_tasks import list_actions
    return {"actions": await list_actions(status, task_id, action_type, domain=domain)}


@router.get("/api/actions/authorization-state", dependencies=[Depends(_require_command("task_read"))])
async def action_authorization_state_route(action_type: str):
    """Read-only, EXPIRY-AWARE authorization check for one action type.

    2026-09-29 (Admin Canary capability-boundary audit). The CLI plugin's
    SERVICE_MUTATION / DATABASE_MUTATION / DEPLOYMENT gate used
    ``/api/actions?status=approved`` -- a discovery listing that explicitly
    does not filter expired rows -- so any expired approval ever left in the
    table authorized the action forever. This endpoint answers the actual
    authorization question by delegating to the existing, expiry-enforcing
    ``is_action_approved`` for every candidate.

    Registered BEFORE ``/api/actions/{action_id}`` (int-typed) so this fixed
    path is never shadowed, matching the /api/tasks/authorizing-path
    convention in this same file.
    """
    from modules.hermes_tasks import is_action_type_authorized, TaskError
    try:
        return await is_action_type_authorized(action_type)
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/actions/{action_id}", dependencies=[Depends(_require_command("task_read"))])
async def get_action_route(action_id: int):
    from modules.hermes_tasks import get_action
    action = await get_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail=f"action #{action_id} not found")
    return action


@router.post("/api/actions/{action_id}/approve", dependencies=[Depends(_require_command("task_approve"))])
async def approve_action_route(action_id: int, key: str = Depends(_API_KEY_HEADER)):
    from modules.hermes_tasks import approve_action, TaskError
    actor = await _resolve_admin_identity(key)
    try:
        return await approve_action(action_id, actor)
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/actions/{action_id}/reject", dependencies=[Depends(_require_command("task_approve"))])
async def reject_action_route(action_id: int, body: RejectActionBody, key: str = Depends(_API_KEY_HEADER)):
    from modules.hermes_tasks import reject_action, TaskError
    actor = await _resolve_admin_identity(key)
    try:
        return await reject_action(action_id, actor, body.reason)
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/actions/{action_id}/execution-result", dependencies=[Depends(_require_command("task_write"))])
async def record_execution_result_route(action_id: int, body: ExecutionResultBody):
    from modules.hermes_tasks import record_execution_result, TaskError
    try:
        return await record_execution_result(action_id, body.result, body.verification)
    except TaskError as e:
        raise HTTPException(status_code=400, detail=str(e))
