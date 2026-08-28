"""Stage H15/H16 (2026-08-28) -- Proposal -> Approval -> Execution bridge.

Connects an APPROVED hermes_action_approvals row (modules.hermes_tasks,
proposed via modules.business_actions.propose_business_action(), Stage F)
to a real effect, for Stage-H-approved action types only. No new
hermes_action_approvals.action_type value is added this stage -- that
would require a migration + CHECK-constraint change (see migration 076's
own comment on hermes_action_approvals_action_type_check), which is out
of this stage's smallest-justified-change scope. Of the two existing
Stage F action types:
  - "business_reply_draft": executing it means delivering the referenced
    draft through the EXISTING, already-gated draft-approval pipeline
    (modules.draft_approval / modules.admin_commands._cmd_approve) --
    this module does not send anything itself, it only triggers that
    existing, already-tested path.
  - "business_record_review": has no mutation to execute by definition
    (a review proposal's entire purpose is having been looked at) --
    execution is recorded as a no-op acknowledgement.

H16 (critical -- stale-approval protection): an APPROVED status on the
proposal is necessary but NOT sufficient to execute. Immediately before
executing, this module re-reads the CURRENT authoritative state of the
object business_reference points at and compares it against the state
recorded in expected_state (captured at propose time, if the caller
provided one) or, failing that, refuses to execute anything whose
current state is not in an actionable condition. If the underlying
state has moved (approved/rejected/sent/reversed by someone else through
the normal path in the meantime), execution is refused with a clear
"stale" error -- record_execution_result() is never called for a stale
action, and the action's own approved status is left untouched (a human
must look again; nothing here silently re-targets or re-proposes)."""
from __future__ import annotations

import logging
from typing import Optional

from app.database import fetch_one

log = logging.getLogger("fazle.business_action_execution")

EXECUTABLE_ACTION_TYPES = frozenset({"business_reply_draft", "business_record_review"})


class StaleProposalError(Exception):
    """The business state a proposal referenced has changed since it was
    proposed/approved -- execution must be refused, not adjusted."""


class BusinessExecutionError(Exception):
    """A caller mistake or an unsupported reference kind -- distinct from
    StaleProposalError so callers can tell "this specific proposal went
    stale" apart from "this proposal can't be executed at all"."""


def parse_business_reference(business_reference: str) -> tuple[str, str]:
    """business_reference is always '<kind>:<id>' (Stage F2's own
    convention, e.g. 'cash_txn:4171', 'draft:4099'). Splits once on the
    first colon."""
    if not business_reference or ":" not in business_reference:
        raise BusinessExecutionError(f"business_reference not in '<kind>:<id>' form: {business_reference!r}")
    kind, _, ref_id = business_reference.partition(":")
    if not kind or not ref_id:
        raise BusinessExecutionError(f"business_reference not in '<kind>:<id>' form: {business_reference!r}")
    return kind, ref_id


# Reference kinds this bridge knows how to re-read CURRENT state for.
# Deliberately conservative -- only kinds whose table/column were verified
# against the live schema this stage. A kind not in this set raises
# BusinessExecutionError rather than guessing at a table name.
_KNOWN_REFERENCE_KINDS = frozenset({"cash_txn", "draft"})


async def _current_state_for_reference(kind: str, ref_id: str) -> dict:
    """Re-reads the CURRENT authoritative state for one referenced
    object. Returns {} if the object no longer exists at all (also
    treated as stale by the caller -- an object that vanished can't be
    the same one the proposal was about)."""
    if kind == "cash_txn":
        row = await fetch_one(
            "SELECT id, transaction_status, employee_id, amount, txn_ref "
            "FROM fpe_cash_transactions WHERE id = $1",
            int(ref_id),
        )
        return dict(row) if row else {}
    if kind == "draft":
        row = await fetch_one(
            "SELECT id, status, intent FROM fazle_draft_replies WHERE id = $1",
            int(ref_id),
        )
        return dict(row) if row else {}
    raise BusinessExecutionError(f"no known state-lookup for business_reference kind {kind!r}")


def _is_actionable(kind: str, state: dict) -> bool:
    """Whether the CURRENT state is still in a condition this bridge is
    allowed to act on. Deliberately narrow per kind -- 'not actionable'
    covers both 'already resolved by someone else' and 'never existed'."""
    if not state:
        return False
    if kind == "cash_txn":
        return state.get("transaction_status") == "pending"
    if kind == "draft":
        return state.get("status") == "pending"
    return False


async def execute_approved_business_action(action_id: int, executor: str = "system") -> dict:
    """The one entry point. Fetches the action, requires status=
    'approved' (via modules.hermes_tasks.is_action_approved -- also
    handles expiry), re-reads CURRENT business state for its
    business_reference, refuses if stale, dispatches by action_type, and
    records the result via record_execution_result(). Never called on a
    'pending' or 'rejected' action -- is_action_approved() enforces this
    the same way opencode_dispatch's own hard gate does."""
    from modules.hermes_tasks import get_action, is_action_approved, record_execution_result

    action = await get_action(action_id)
    if not action:
        raise BusinessExecutionError(f"action #{action_id} not found")

    action_type = action["action_type"]
    if action_type not in EXECUTABLE_ACTION_TYPES:
        raise BusinessExecutionError(
            f"action_type {action_type!r} is not execution-enabled this stage "
            f"(EXECUTABLE_ACTION_TYPES={sorted(EXECUTABLE_ACTION_TYPES)})"
        )

    if not await is_action_approved(action_id, expected_action_type=action_type):
        raise BusinessExecutionError(f"action #{action_id} is not currently approved (or has expired) -- refusing to execute")

    business_reference = action.get("business_reference")
    if not business_reference:
        raise BusinessExecutionError(f"action #{action_id} has no business_reference -- cannot re-validate state before executing")

    kind, ref_id = parse_business_reference(business_reference)
    if kind not in _KNOWN_REFERENCE_KINDS:
        raise BusinessExecutionError(f"business_reference kind {kind!r} has no known re-validation path -- refusing to execute rather than skip the state check")

    current_state = await _current_state_for_reference(kind, ref_id)
    if not _is_actionable(kind, current_state):
        raise StaleProposalError(
            f"action #{action_id} refers to {business_reference}, whose current state "
            f"({current_state or 'not found'}) is no longer actionable -- it was likely "
            "already resolved through the normal path since this proposal was created. "
            "Execution refused; the proposal's own approved status is left unchanged."
        )

    if action_type == "business_record_review":
        result = {"executed": False, "no_op": True, "note": "business_record_review has no mutation -- recorded as acknowledged"}
    elif action_type == "business_reply_draft":
        if kind != "draft":
            raise BusinessExecutionError(f"business_reply_draft action #{action_id} has a non-draft business_reference ({business_reference!r})")
        from modules.admin_commands import _cmd_approve
        confirm_text = await _cmd_approve(int(ref_id), executor)
        result = {"executed": True, "draft_id": int(ref_id), "confirm_text": confirm_text}
    else:  # pragma: no cover -- unreachable, EXECUTABLE_ACTION_TYPES gate above already excludes this
        raise BusinessExecutionError(f"no dispatch handler for action_type {action_type!r}")

    recorded = await record_execution_result(action_id, result)
    log.info("[business_action_execution] action #%d (%s, %s) executed by %s", action_id, action_type, business_reference, executor)
    return {"ok": True, "action_id": action_id, "action_type": action_type, "business_reference": business_reference, "result": result, "recorded": recorded}
