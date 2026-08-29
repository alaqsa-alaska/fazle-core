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

# Stage J4/J5/J21 (2026-08-29): the smallest safe representation fix for
# "review vs mutation" ambiguity, with NO schema migration -- reuses
# evidence_refs (jsonb, already following propose_business_action()'s own
# {"type": ..., "value": ...} convention). A business_record_review
# proposal is treated as an ACTION proposal (one Owner APPROVAL = one
# execution, J2) ONLY when its evidence_refs contains an explicit
# {"type": "proposed_operation", "value": <op>} entry naming one of these
# operations -- otherwise it is (and remains) review-only, recorded as an
# acknowledgement, per J20's critical invariant: a generic historical
# review proposal (e.g. #31, which has no such entry -- confirmed live)
# is NEVER silently reinterpreted as financial authorization after the
# fact. Only cash_approve is wired this stage (the one concrete example
# in the directive); anything else is refused, not guessed.
SUPPORTED_PROPOSED_OPERATIONS = frozenset({"cash_approve"})


def get_proposed_operation(evidence_refs) -> Optional[str]:
    """Pure, directly testable. Returns the proposed_operation value if
    evidence_refs (a list of {"type", "value"} dicts -- or the raw jsonb
    string asyncpg returns it as, with no codec registered in
    app.database, confirmed live) contains one, else None."""
    if not evidence_refs:
        return None
    if isinstance(evidence_refs, str):
        import json
        try:
            evidence_refs = json.loads(evidence_refs)
        except (ValueError, TypeError):
            return None
    for ref in evidence_refs:
        if isinstance(ref, dict) and ref.get("type") == "proposed_operation":
            return ref.get("value")
    return None


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
# BusinessExecutionError rather than guessing at a table name. "conversation"
# added Stage H.5 (H5.13) after a real live proposal (#29) turned out to
# reference a bare conversation_key, not a draft id -- see
# audit_proposal_references() for the full live audit.
_KNOWN_REFERENCE_KINDS = frozenset({"cash_txn", "draft", "conversation", "escort_program"})


async def _current_state_for_reference(kind: str, ref_id: str) -> dict:
    """Re-reads the CURRENT authoritative state for one referenced
    object. Returns {} if the object no longer exists at all (also
    treated as stale by the caller -- an object that vanished can't be
    the same one the proposal was about)."""
    if kind == "cash_txn":
        row = await fetch_one(
            "SELECT id, transaction_status, employee_id, amount, txn_ref, deleted_at "
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
    if kind == "conversation":
        row = await fetch_one(
            "SELECT COUNT(*) AS message_count, MAX(received_at) AS last_received_at "
            "FROM wbom_whatsapp_messages WHERE conversation_key = $1",
            ref_id,
        )
        return dict(row) if row and row["message_count"] else {}
    if kind == "escort_program":
        row = await fetch_one(
            "SELECT program_id AS id, status FROM wbom_escort_programs WHERE program_id = $1",
            int(ref_id),
        )
        return dict(row) if row else {}
    raise BusinessExecutionError(f"no known state-lookup for business_reference kind {kind!r}")


def _is_actionable(kind: str, state: dict, proposed_at=None) -> bool:
    """Whether the CURRENT state is still in a condition this bridge is
    allowed to act on. Deliberately narrow per kind -- 'not actionable'
    covers both 'already resolved by someone else' and 'never existed'.

    conversation (H5.13): a conversation-referencing proposal (typically
    business_reply_draft, proposed BEFORE any draft exists) is stale the
    moment new conversation activity arrives after the proposal was
    created -- the reply it would send was grounded in a snapshot of the
    conversation that no longer reflects what the sender has since said."""
    if not state:
        return False
    if kind == "cash_txn":
        return state.get("transaction_status") == "pending" and not state.get("deleted_at")
    if kind == "draft":
        return state.get("status") == "pending"
    if kind == "conversation":
        last = state.get("last_received_at")
        if proposed_at is None or last is None:
            return True
        return last <= proposed_at
    if kind == "escort_program":
        return state.get("status") in ("draft", "confirmed")
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
    if not _is_actionable(kind, current_state, proposed_at=action.get("created_at")):
        raise StaleProposalError(
            f"action #{action_id} refers to {business_reference}, whose current state "
            f"({current_state or 'not found'}) is no longer actionable -- it was likely "
            "already resolved through the normal path since this proposal was created. "
            "Execution refused; the proposal's own approved status is left unchanged."
        )

    if action_type == "business_record_review":
        proposed_op = get_proposed_operation(action.get("evidence_refs"))
        if proposed_op is None:
            # J20: no proposed_operation was ever presented to the Owner
            # for this proposal -- it is review-only. This is #31's exact
            # case, confirmed live: never reinterpreted as cash_approve.
            result = {"executed": False, "no_op": True, "note": "business_record_review has no mutation -- recorded as acknowledged"}
        elif proposed_op not in SUPPORTED_PROPOSED_OPERATIONS:
            raise BusinessExecutionError(
                f"action #{action_id} declares proposed_operation={proposed_op!r}, which this bridge "
                f"does not support executing (supported: {sorted(SUPPORTED_PROPOSED_OPERATIONS)})"
            )
        elif proposed_op == "cash_approve":
            # J15/J16: converge on the EXACT SAME authoritative function
            # CASH APPROVE <ref> uses -- no duplicated financial-mutation
            # logic. kind/domain were already validated as cash_txn above
            # (_KNOWN_REFERENCE_KINDS + the actionability re-read).
            if kind != "cash_txn" or action.get("domain") != "cash":
                raise BusinessExecutionError(
                    f"action #{action_id} declares proposed_operation='cash_approve' but its "
                    f"business_reference/domain is not a cash transaction ({business_reference!r}, domain={action.get('domain')!r})"
                )
            from modules.financial_operations import cash_approve
            approve_result = await cash_approve(ref_id, executor)
            if not approve_result.get("ok"):
                raise StaleProposalError(
                    f"action #{action_id}'s proposed cash_approve was refused at execution time: "
                    f"{approve_result.get('error')} -- current state changed since proposal/approval; "
                    "execution refused, proposal's own approved status left unchanged"
                )
            result = {"executed": True, "operation": "cash_approve", "txn_id": approve_result.get("txn_id"), "txn_ref": approve_result.get("txn_ref")}
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


# ── H5.12/H5.14: proposal reference audit + legacy compatibility report ──
async def audit_proposal_references() -> list[dict]:
    """READ-only. One row per hermes_action_approvals business proposal
    (every domain, every status -- not just pending), reporting exactly
    what H5.12 asks for. Never mutates a proposal's own status; a proposal
    found STALE/unexecutable here is reported, not rejected or rewritten
    -- H5.14's explicit "do not destructively rewrite historical Stage F
    proposals blindly"."""
    from app.database import fetch_all

    rows = await fetch_all(
        "SELECT id, domain, status, action_type, business_reference, created_at "
        "FROM hermes_action_approvals WHERE domain IS NOT NULL ORDER BY id"
    )
    out = []
    for r in rows:
        row = dict(r)
        ref = row.get("business_reference") or ""
        try:
            kind, ref_id = parse_business_reference(ref)
            reference_kind = kind
        except BusinessExecutionError:
            kind, ref_id, reference_kind = None, None, "UNTYPED"

        executable = kind in _KNOWN_REFERENCE_KINDS if kind else False
        # Migratable: a legacy reference this bridge doesn't recognize as
        # typed, but whose raw string is EXACTLY a live conversation_key
        # (verified against wbom_whatsapp_messages, never inferred from
        # shape/regex -- H5.13's explicit "do not infer object type from
        # numeric shape or domain text"). Covers both "no colon at all"
        # (UNTYPED) and "colon present but the kind prefix is unrecognized"
        # (e.g. proposal #29's bare "<phone>:<bridge>"). Reported only,
        # never auto-rewritten -- H5.14.
        migratable = False
        if not executable and ref:
            match = await fetch_one(
                "SELECT 1 FROM wbom_whatsapp_messages WHERE conversation_key = $1 LIMIT 1", ref,
            )
            migratable = bool(match)

        stale = None
        if executable and row["status"] == "pending":
            try:
                current_state = await _current_state_for_reference(kind, ref_id)
                stale = not _is_actionable(kind, current_state, proposed_at=row["created_at"])
            except BusinessExecutionError:
                stale = None

        out.append({
            "proposal_id": row["id"],
            "domain": row["domain"],
            "action_type": row["action_type"],
            "current_reference": ref,
            "reference_kind": reference_kind,
            "executable": executable,
            "migratable": migratable,
            "stale": stale,
            "status": row["status"],
            "created_at": row["created_at"],
        })
    return out
