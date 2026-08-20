"""
modules.hermes_tasks -- persistent Task/Execution State + diff-level Action
Approval for Earth (2026-08-19, Owner-directed follow-on to the P0-P2
reliability-hardening pass and the BUILD/RUN/business-action capability
audit -- see project_hermes_earth_rename_autonomy_20260819 memory).

Two things live here, deliberately in one module (they're tightly coupled --
an approval's own status directly drives its parent task's status):

  hermes_tasks / hermes_task_events
      Persistent goal/task/subtask state that survives a conversation
      ending. A later Earth session recovers unfinished work via
      list_tasks(owner="earth", status not in terminal) rather than
      starting cold -- the actual session-recovery mechanism this was
      built for.

  hermes_action_approvals
      The diff-level "propose -> approve -> execute" gate for file
      mutations / git commits / deploys / migrations / restarts /
      production writes / opencode_dispatch calls. Approving does NOT
      execute anything -- it only flips status to 'approved'; execution is
      a separate, later step the caller performs and then records via
      record_execution_result(). This is deliberately NOT a replacement
      for the existing RUN-mode + confirm=True gate on every fazle-mcp
      write tool -- it's an additional, second layer specifically for
      code/infra-shaped actions, on top of that existing mode gate.

Shape mirrors modules.draft_approval's proven fazle_payment_drafts +
fazle_draft_audit_log pattern throughout: row-level SELECT ... FOR UPDATE
locks before a state transition (get_pool()/conn.transaction(), same as
approve_draft()), a separate append-only audit table with before/after +
performed_by (same as _audit()/fazle_draft_audit_log), server-side-only
actor identity (never a client-supplied field -- see routes.py).

Migration: db/migrations/068_hermes_task_execution_state.sql.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.database import fetch_one, fetch_all, fetch_val, execute, get_pool

log = logging.getLogger("fazle.hermes_tasks")

# ── Constants ────────────────────────────────────────────────────────────────

TASK_STATUSES = frozenset({
    "BACKLOG", "READY", "IN_PROGRESS", "WAITING_APPROVAL", "VERIFYING",
    "DONE", "FAILED", "BLOCKED",
})
TASK_TERMINAL_STATUSES = frozenset({"DONE", "FAILED"})
TASK_PRIORITIES = frozenset({"low", "normal", "high", "urgent"})
EXECUTION_MODES = frozenset({"READ", "BUILD", "RUN"})

# Allowed status transitions. BLOCKED/FAILED are reachable from any
# non-terminal state (an admin or Earth can always flag a stuck/failed
# task); FAILED->READY/BACKLOG allows a retry. DONE/never leaves (terminal).
_TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "BACKLOG": frozenset({"READY", "BLOCKED", "FAILED"}),
    "READY": frozenset({"IN_PROGRESS", "BACKLOG", "BLOCKED", "FAILED"}),
    "IN_PROGRESS": frozenset({"WAITING_APPROVAL", "VERIFYING", "DONE", "BLOCKED", "FAILED"}),
    "WAITING_APPROVAL": frozenset({"IN_PROGRESS", "VERIFYING", "BLOCKED", "FAILED"}),
    "VERIFYING": frozenset({"DONE", "IN_PROGRESS", "FAILED"}),
    "BLOCKED": frozenset({"READY", "IN_PROGRESS", "FAILED"}),
    "FAILED": frozenset({"READY", "BACKLOG"}),
    "DONE": frozenset(),
}

ACTION_TYPES = frozenset({
    "file_mutation", "git_commit", "deploy", "migration", "restart",
    "production_write", "opencode_dispatch",
})
ACTION_RISKS = frozenset({"low", "medium", "high", "critical"})


class TaskError(ValueError):
    """Raised for a caller mistake (bad status, illegal transition, unknown
    id) -- distinct from a genuine DB/infra failure, so routes.py can map
    it to a 400 rather than a 500."""


def _is_expired(expires_at) -> bool:
    """True if expires_at is set and in the past. None/unset never expires
    (matches the pre-existing columns that don't carry a TTL at all)."""
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expires_at


# ── Task events (append-only audit trail) ───────────────────────────────────

async def _event(
    task_id: int, event: str, before_status: Optional[str], after_status: Optional[str],
    *, performed_by: str, detail: Optional[dict] = None,
) -> None:
    try:
        await execute(
            """INSERT INTO hermes_task_events
                   (task_id, event, before_status, after_status, performed_by, detail)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
            task_id, event, before_status, after_status, performed_by,
            json.dumps(detail, default=str) if detail else None,
        )
    except Exception as e:
        log.error("[hermes_tasks] event write failed for task #%d event=%s: %s", task_id, event, e)


# ── Tasks ────────────────────────────────────────────────────────────────────

async def create_task(
    title: str,
    description: Optional[str] = None,
    parent_task_id: Optional[int] = None,
    priority: str = "normal",
    execution_mode: Optional[str] = None,
    created_from: Optional[str] = None,
    trace_id: Optional[str] = None,
    depends_on: Optional[list[int]] = None,
    created_by_phone: Optional[str] = None,
) -> int:
    if not title or not title.strip():
        raise TaskError("title must not be empty")
    if priority not in TASK_PRIORITIES:
        raise TaskError(f"priority must be one of {sorted(TASK_PRIORITIES)}, got {priority!r}")
    if execution_mode is not None and execution_mode not in EXECUTION_MODES:
        raise TaskError(f"execution_mode must be one of {sorted(EXECUTION_MODES)}, got {execution_mode!r}")

    task_id = await fetch_val(
        """INSERT INTO hermes_tasks
               (parent_task_id, title, description, priority, execution_mode,
                created_from, trace_id, depends_on, created_by_phone)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           RETURNING id""",
        parent_task_id, title.strip()[:500], description, priority, execution_mode,
        created_from, trace_id, depends_on or [], created_by_phone,
    )
    task_id = int(task_id)
    await _event(task_id, "created", None, "BACKLOG", performed_by=created_by_phone or "earth")
    log.info("[hermes_tasks] created task #%d: %s", task_id, title.strip()[:80])
    return task_id


async def list_tasks(
    status: Optional[str] = None, owner: Optional[str] = None,
    parent_task_id: Optional[int] = None, limit: int = 50,
) -> list[dict]:
    limit = min(max(int(limit or 50), 1), 200)
    clauses, params = [], []
    if status:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    if owner:
        params.append(owner)
        clauses.append(f"owner = ${len(params)}")
    if parent_task_id is not None:
        params.append(parent_task_id)
        clauses.append(f"parent_task_id = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = await fetch_all(
        f"SELECT * FROM hermes_tasks {where} ORDER BY priority DESC, created_at DESC LIMIT ${len(params)}",
        *params,
    )
    return [dict(r) for r in rows]


async def get_task(task_id: int) -> Optional[dict]:
    task = await fetch_one("SELECT * FROM hermes_tasks WHERE id = $1", task_id)
    if not task:
        return None
    events = await fetch_all(
        "SELECT * FROM hermes_task_events WHERE task_id = $1 ORDER BY created_at ASC", task_id,
    )
    result = dict(task)
    result["events"] = [dict(e) for e in events]
    return result


async def claim_task(task_id: int, owner: str = "earth") -> dict:
    """Only READY/BACKLOG -> IN_PROGRESS. Row-locked so two concurrent
    claims can't both succeed (same pattern as approve_draft())."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT id, status FROM hermes_tasks WHERE id = $1 FOR UPDATE", task_id)
            if not row:
                raise TaskError(f"task #{task_id} not found")
            if row["status"] not in ("READY", "BACKLOG"):
                raise TaskError(f"task #{task_id} is not claimable (status={row['status']})")
            await conn.execute(
                "UPDATE hermes_tasks SET status='IN_PROGRESS', owner=$1, claimed_at=NOW(), updated_at=NOW() WHERE id=$2",
                owner, task_id,
            )
    await _event(task_id, "claimed", row["status"], "IN_PROGRESS", performed_by=owner)
    return {"ok": True, "task_id": task_id, "status": "IN_PROGRESS"}


async def update_task_status(
    task_id: int, new_status: str, *, performed_by: str = "earth",
    next_action: Optional[str] = None, failure_reason: Optional[str] = None,
    verification: Optional[dict] = None,
) -> dict:
    if new_status not in TASK_STATUSES:
        raise TaskError(f"status must be one of {sorted(TASK_STATUSES)}, got {new_status!r}")

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT id, status FROM hermes_tasks WHERE id = $1 FOR UPDATE", task_id)
            if not row:
                raise TaskError(f"task #{task_id} not found")
            current = row["status"]
            if new_status != current and new_status not in _TASK_TRANSITIONS.get(current, frozenset()):
                raise TaskError(
                    f"illegal transition {current} -> {new_status} for task #{task_id} "
                    f"(allowed from {current}: {sorted(_TASK_TRANSITIONS.get(current, []))})"
                )
            completed_at_clause = ", completed_at = NOW()" if new_status in TASK_TERMINAL_STATUSES else ""
            await conn.execute(
                f"""UPDATE hermes_tasks
                    SET status=$1, next_action=COALESCE($2, next_action),
                        failure_reason=COALESCE($3, failure_reason),
                        verification=COALESCE($4::jsonb, verification),
                        updated_at=NOW(){completed_at_clause}
                    WHERE id=$5""",
                new_status, next_action, failure_reason,
                json.dumps(verification, default=str) if verification else None,
                task_id,
            )
    await _event(
        task_id, "status_changed", current, new_status, performed_by=performed_by,
        detail={"next_action": next_action, "failure_reason": failure_reason} if (next_action or failure_reason) else None,
    )
    return {"ok": True, "task_id": task_id, "status": new_status}


# ── Build authorization (2026-08-19, Owner revision) ────────────────────────
# A broader, task-scoped grant distinct from the per-action approvals below --
# "fix it"/"go ahead" style admin instructions must authorize many subsequent
# file edits within an approved repo scope, not require a separate approval
# per edit. Earth calls authorize_build() only after recognizing the admin's
# real, current instruction as sufficient authorization for this task's
# implementation phase -- same discipline as every other Tier C/D tool,
# applied to a task rather than a single business record.

async def authorize_build(
    task_id: int, repos: list[str], granted_by: str, ttl_hours: int = 4,
) -> dict:
    if not repos:
        raise TaskError("repos must not be empty -- name at least one repo path this authorization covers")
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT id, status FROM hermes_tasks WHERE id = $1 FOR UPDATE", task_id)
            if not row:
                raise TaskError(f"task #{task_id} not found")
            await conn.execute(
                """UPDATE hermes_tasks
                   SET build_authorized=true, build_authorized_at=NOW(), build_authorized_by=$1,
                       build_scope=$2::jsonb, build_expires_at=NOW() + ($3 || ' hours')::interval,
                       updated_at=NOW()
                   WHERE id=$4""",
                granted_by, json.dumps({"repos": repos}), str(int(ttl_hours)), task_id,
            )
    await _event(task_id, "build_authorized", None, None, performed_by=granted_by,
                 detail={"repos": repos, "ttl_hours": ttl_hours})
    log.info("[hermes_tasks] task #%d BUILD authorized by %s for %s (%dh)", task_id, granted_by, repos, ttl_hours)
    return {"ok": True, "task_id": task_id, "build_authorized": True, "repos": repos, "ttl_hours": ttl_hours}


async def find_authorizing_task_for_path(path: str) -> Optional[dict]:
    """The check the CLI plugin calls before allowing a WORKSPACE_MUTATION
    (file edit) outside a fazle-mcp tool call. Returns the first live,
    unexpired, build_authorized task whose build_scope.repos contains a
    prefix of `path`, or None. Deliberately a simple Python prefix-match
    over a small candidate set (build_authorized tasks are never numerous)
    rather than a JSONB containment query -- easier to reason about and to
    unit-test."""
    rows = await fetch_all(
        """SELECT id, build_scope, build_expires_at FROM hermes_tasks
           WHERE build_authorized = true AND status NOT IN ('DONE', 'FAILED')""",
    )
    for row in rows:
        if _is_expired(row["build_expires_at"]):
            continue
        scope = row["build_scope"]
        if isinstance(scope, str):
            scope = json.loads(scope)
        repos = (scope or {}).get("repos") or []
        if any(path.startswith(repo) for repo in repos):
            return {"task_id": row["id"], "repos": repos}
    return None


# ── Action approvals ─────────────────────────────────────────────────────────

async def propose_action(
    task_id: Optional[int], action_type: str, summary: str,
    files_changed: Optional[list[str]] = None, diff: Optional[str] = None,
    command: Optional[str] = None, risk: str = "medium",
    expected_effect: Optional[str] = None, rollback_plan: Optional[str] = None,
    requested_by: str = "earth",
) -> int:
    if action_type not in ACTION_TYPES:
        raise TaskError(f"action_type must be one of {sorted(ACTION_TYPES)}, got {action_type!r}")
    if risk not in ACTION_RISKS:
        raise TaskError(f"risk must be one of {sorted(ACTION_RISKS)}, got {risk!r}")
    if not summary or not summary.strip():
        raise TaskError("summary must not be empty")

    action_id = await fetch_val(
        """INSERT INTO hermes_action_approvals
               (task_id, action_type, summary, files_changed, diff, command,
                risk, expected_effect, rollback_plan, requested_by)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
           RETURNING id""",
        task_id, action_type, summary.strip(), files_changed, diff, command,
        risk, expected_effect, rollback_plan, requested_by,
    )
    action_id = int(action_id)
    if task_id is not None:
        try:
            await update_task_status(task_id, "WAITING_APPROVAL", performed_by=requested_by)
        except TaskError as e:
            # Task might already be in a state that can't transition to
            # WAITING_APPROVAL (e.g. it's already there, or it's terminal) --
            # the proposal itself is still valid and recorded; don't lose it
            # over a task-side transition mismatch.
            log.warning("[hermes_tasks] propose_action #%d: task #%s status update skipped: %s", action_id, task_id, e)
        await _event(task_id, "approval_requested", None, None, performed_by=requested_by, detail={"action_id": action_id})
    log.info("[hermes_tasks] proposed action #%d (%s, risk=%s) for task=%s", action_id, action_type, risk, task_id)
    return action_id


async def authorize_action(
    task_id: Optional[int], action_type: str, summary: str, authorized_by: str,
    files_changed: Optional[list[str]] = None, diff: Optional[str] = None,
    command: Optional[str] = None, risk: str = "medium",
    expected_effect: Optional[str] = None, rollback_plan: Optional[str] = None,
) -> int:
    """Fused propose+approve (2026-08-19, Owner revision): creates a
    hermes_action_approvals row already status='approved' in one
    transaction. Earth calls this ONLY after recognizing the admin's real,
    current natural-language instruction ("commit it", "commit, push and
    deploy") as sufficient authorization for THIS SPECIFIC action -- exactly
    mirroring how approve_payment_draft is already callable today from a
    natural sentence, not a rigid command. propose_action()+approve_action()
    remain the separate two-step path (Earth proposing, a human deciding
    later) -- this does not replace them, it adds the common one-step case.
    Same validation, same expiry window, same audit trail as the two-step
    path; the CLI plugin's own checks (is_action_approved(), diff-hash
    comparison for git_commit) can't tell which path produced an approval."""
    if action_type not in ACTION_TYPES:
        raise TaskError(f"action_type must be one of {sorted(ACTION_TYPES)}, got {action_type!r}")
    if risk not in ACTION_RISKS:
        raise TaskError(f"risk must be one of {sorted(ACTION_RISKS)}, got {risk!r}")
    if not summary or not summary.strip():
        raise TaskError("summary must not be empty")

    action_id = await fetch_val(
        """INSERT INTO hermes_action_approvals
               (task_id, action_type, summary, files_changed, diff, command,
                risk, expected_effect, rollback_plan, requested_by,
                status, reviewed_by, reviewed_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'approved', $10, NOW())
           RETURNING id""",
        task_id, action_type, summary.strip(), files_changed, diff, command,
        risk, expected_effect, rollback_plan, authorized_by,
    )
    action_id = int(action_id)
    if task_id is not None:
        await _event(task_id, "approved", None, "approved", performed_by=authorized_by,
                     detail={"action_id": action_id, "fused": True})
    log.info("[hermes_tasks] action #%d authorized (fused propose+approve) by %s (%s, risk=%s) for task=%s",
             action_id, authorized_by, action_type, risk, task_id)
    return action_id


async def list_actions(
    status: Optional[str] = "pending", task_id: Optional[int] = None, action_type: Optional[str] = None,
) -> list[dict]:
    """General-purpose action listing. Default status='pending' preserves
    get_pending_actions()'s original behavior; status='approved' +
    action_type is what the CLI plugin uses to check for a matching
    authorization before allowing a SERVICE_MUTATION/DATABASE_MUTATION/
    DEPLOYMENT terminal command through. Does NOT filter out expired rows
    here -- callers that need an authorization decision should use
    is_action_approved(action_id) on a specific candidate, which does
    enforce expiry; this is a listing/discovery helper."""
    clauses, params = [], []
    if status:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    if task_id is not None:
        params.append(task_id)
        clauses.append(f"task_id = ${len(params)}")
    if action_type:
        params.append(action_type)
        clauses.append(f"action_type = ${len(params)}")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await fetch_all(f"SELECT * FROM hermes_action_approvals {where} ORDER BY created_at DESC", *params)
    return [dict(r) for r in rows]


async def get_pending_actions(task_id: Optional[int] = None) -> list[dict]:
    return await list_actions(status="pending", task_id=task_id)


async def get_action(action_id: int) -> Optional[dict]:
    row = await fetch_one("SELECT * FROM hermes_action_approvals WHERE id = $1", action_id)
    return dict(row) if row else None


async def approve_action(action_id: int, reviewed_by_phone: str) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT id, status, task_id FROM hermes_action_approvals WHERE id = $1 FOR UPDATE", action_id)
            if not row:
                raise TaskError(f"action #{action_id} not found")
            if row["status"] != "pending":
                raise TaskError(f"action #{action_id} is not pending (status={row['status']})")
            await conn.execute(
                "UPDATE hermes_action_approvals SET status='approved', reviewed_by=$1, reviewed_at=NOW() WHERE id=$2",
                reviewed_by_phone, action_id,
            )
    await _event(row["task_id"], "approved", "pending", "approved", performed_by=reviewed_by_phone, detail={"action_id": action_id}) if row["task_id"] else None
    log.info("[hermes_tasks] action #%d approved by %s", action_id, reviewed_by_phone)
    return {"ok": True, "action_id": action_id, "status": "approved"}


async def reject_action(action_id: int, reviewed_by_phone: str, reason: Optional[str] = None) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT id, status, task_id FROM hermes_action_approvals WHERE id = $1 FOR UPDATE", action_id)
            if not row:
                raise TaskError(f"action #{action_id} not found")
            if row["status"] != "pending":
                raise TaskError(f"action #{action_id} is not pending (status={row['status']})")
            await conn.execute(
                "UPDATE hermes_action_approvals SET status='rejected', reviewed_by=$1, reviewed_at=NOW() WHERE id=$2",
                reviewed_by_phone, action_id,
            )
    if row["task_id"]:
        await _event(row["task_id"], "rejected", "pending", "rejected", performed_by=reviewed_by_phone, detail={"action_id": action_id, "reason": reason})
        try:
            await update_task_status(row["task_id"], "BLOCKED", performed_by=reviewed_by_phone, failure_reason=reason or "action rejected")
        except TaskError as e:
            log.warning("[hermes_tasks] reject_action #%d: task #%s status update skipped: %s", action_id, row["task_id"], e)
    log.info("[hermes_tasks] action #%d rejected by %s: %s", action_id, reviewed_by_phone, reason)
    return {"ok": True, "action_id": action_id, "status": "rejected"}


async def is_action_approved(action_id: int, expected_action_type: Optional[str] = None) -> bool:
    """The check opencode_dispatch's hard gate (and the CLI plugin) calls.
    Deliberately returns a plain bool, not a dict -- callers that need the
    reason should call get_action() themselves.

    2026-08-19 fix: expires_at existed on the schema since migration 068 but
    was never actually checked here -- a real gap against the "approval
    expires -> execution rejected" acceptance test. A pending action past
    its window is lazily flipped to 'expired' on discovery (same shape as
    draft_approval.expire_draft()); an already-approved-but-unexecuted
    action past its window is treated as not-approved without a write (its
    own status stays 'approved' as a historical record, but it no longer
    authorizes execution)."""
    row = await fetch_one(
        "SELECT status, action_type, expires_at, task_id FROM hermes_action_approvals WHERE id = $1", action_id,
    )
    if not row:
        return False
    if row["status"] == "pending" and _is_expired(row["expires_at"]):
        try:
            await execute(
                "UPDATE hermes_action_approvals SET status='expired' WHERE id=$1 AND status='pending'",
                action_id,
            )
            if row["task_id"]:
                await _event(row["task_id"], "expired", "pending", "expired",
                             performed_by="system", detail={"action_id": action_id})
        except Exception as e:
            log.error("[hermes_tasks] expiry write failed for action #%d: %s", action_id, e)
        return False
    if row["status"] != "approved":
        return False
    if _is_expired(row["expires_at"]):
        return False
    if expected_action_type and row["action_type"] != expected_action_type:
        return False
    return True


async def record_execution_result(
    action_id: int, result: dict, verification: Optional[dict] = None,
) -> dict:
    """Only valid from status='approved'. Sets status='executed'; if the
    action has a task_id, advances that task to VERIFYING (the caller is
    still expected to independently move it to DONE/FAILED after actually
    checking the result -- this function does not itself judge success)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, status, task_id, expires_at FROM hermes_action_approvals WHERE id = $1 FOR UPDATE",
                action_id,
            )
            if not row:
                raise TaskError(f"action #{action_id} not found")
            if row["status"] != "approved":
                raise TaskError(f"action #{action_id} is not approved (status={row['status']})")
            if _is_expired(row["expires_at"]):
                raise TaskError(f"action #{action_id}'s approval has expired -- re-authorize before executing")
            await conn.execute(
                """UPDATE hermes_action_approvals
                   SET status='executed', executed_at=NOW(), execution_result=$1::jsonb
                   WHERE id=$2""",
                json.dumps(result, default=str), action_id,
            )
    if row["task_id"]:
        await _event(row["task_id"], "verified" if verification else "status_changed", None, "VERIFYING",
                     performed_by="earth", detail={"action_id": action_id, "result": result, "verification": verification})
        try:
            await update_task_status(row["task_id"], "VERIFYING", performed_by="earth", verification=verification)
        except TaskError as e:
            log.warning("[hermes_tasks] record_execution_result #%d: task #%s status update skipped: %s", action_id, row["task_id"], e)
    log.info("[hermes_tasks] action #%d execution recorded", action_id)
    return {"ok": True, "action_id": action_id, "status": "executed"}
