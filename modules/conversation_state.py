"""Stage D3-D7 (2026-08-28) -- cross-domain conversation state, derived
from EXISTING authoritative business records, never stored redundantly on
message rows and never a new mutable status column on
wbom_whatsapp_messages (Stage D5's explicit preference: current state
belongs on the authoritative business record it already lives on, not
copied onto every historical message).

Explicitly NOT `status`/`is_processed` (Stage D3) -- those are message
delivery/routing-pipeline states, not business conversation state. Not a
generalization of recruitment-only `post_intake_status` into a universal
column either (Stage D3's other explicit prohibition) -- each domain's
own authoritative field stays exactly as-is; this module only maps each
domain's own existing values into the smaller, shared vocabulary below.

Cross-domain vocabulary (Stage D3/D4) -- derived from what two domains'
real, existing data actually supports (recruitment's post_intake_status,
escort's wbom_escort_programs.status), NOT the example list in the Stage D
brief, which was explicitly "examples, not an approved taxonomy":

    state:        waiting_for_counterparty | waiting_for_company | active
                  | resolved | unclear
    next_actor:   applicant | company | none | unknown
    requires_action: bool -- derived ONLY from state/next_actor, never
                  from message age, sentiment, or text content.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class ConversationState(TypedDict):
    state: str
    next_actor: str
    requires_action: bool
    state_source: str


UNKNOWN_STATE: ConversationState = {
    "state": "unclear",
    "next_actor": "unknown",
    "requires_action": False,
    "state_source": "none",
}

# ── Recruitment (Stage D6) ───────────────────────────────────────────────
# Source of truth: wbom_candidates.post_intake_status (migration 072,
# Owner-specified 7-value set -- modules.recruitment_flow.POST_INTAKE_STATUSES).
# Mapped here, never renamed/redefined there. NULL means "not yet reached
# post-intake" (pre-scored, or scored before migration 072 -- see that
# migration's own comment), mapped to "unclear", not fabricated as active.
#
# Judgment calls made explicit (not hidden):
#   - awaiting_agreement / not_ready_now: we are waiting ON the candidate,
#     so requires_action=False from the company's operational-queue
#     perspective (matches the Stage D4 worked example in the brief).
#   - agreed / joining_ready / salary_objection: the candidate has done
#     their part; company action (arrange joining, respond to objection)
#     is what's pending -- requires_action=True.
#   - declined: terminal (POST_INTAKE_TERMINAL_STATUSES) -- resolved,
#     nothing pending.
#   - future_contact: lower-confidence call, flagged here rather than
#     silently asserted -- the candidate asked to be recontacted later, so
#     no one else will act unless the company does; mapped to
#     requires_action=True on that basis, but this is the one state in
#     this table with a real, disclosed ambiguity (it could equally be
#     read as "not urgent yet").
RECRUITMENT_STATE_MAP: dict[Optional[str], ConversationState] = {
    None: {"state": "unclear", "next_actor": "unknown", "requires_action": False, "state_source": "wbom_candidates.post_intake_status"},
    "awaiting_agreement": {"state": "waiting_for_counterparty", "next_actor": "applicant", "requires_action": False, "state_source": "wbom_candidates.post_intake_status"},
    "not_ready_now": {"state": "waiting_for_counterparty", "next_actor": "applicant", "requires_action": False, "state_source": "wbom_candidates.post_intake_status"},
    "agreed": {"state": "waiting_for_company", "next_actor": "company", "requires_action": True, "state_source": "wbom_candidates.post_intake_status"},
    "joining_ready": {"state": "waiting_for_company", "next_actor": "company", "requires_action": True, "state_source": "wbom_candidates.post_intake_status"},
    "salary_objection": {"state": "waiting_for_company", "next_actor": "company", "requires_action": True, "state_source": "wbom_candidates.post_intake_status"},
    "future_contact": {"state": "waiting_for_company", "next_actor": "company", "requires_action": True, "state_source": "wbom_candidates.post_intake_status"},
    "declined": {"state": "resolved", "next_actor": "none", "requires_action": False, "state_source": "wbom_candidates.post_intake_status"},
}


def resolve_recruitment_state(post_intake_status: Optional[str]) -> ConversationState:
    return RECRUITMENT_STATE_MAP.get(post_intake_status, dict(UNKNOWN_STATE))  # type: ignore[return-value]


# ── Escort / Operations (Stage D7) ───────────────────────────────────────
# Source of truth: wbom_escort_programs.status (modules/escort/routes.py's
# creation path: draft -> confirmed -> completed, or cancelled). Real,
# deterministic, already-live business record -- not invented for Stage D.
#   draft:      program created but not yet confirmed -- company action
#               pending (confirm/reject the assignment).
#   confirmed:  assignment made, duty in progress/pending -- from a
#               WhatsApp-conversation-attention lens nothing is blocked on
#               a reply right now (the assigned employee is executing the
#               physical duty, not messaging back) -- next_actor=none.
#   completed / cancelled: terminal, resolved.
ESCORT_STATE_MAP: dict[Optional[str], ConversationState] = {
    None: {"state": "unclear", "next_actor": "unknown", "requires_action": False, "state_source": "wbom_escort_programs.status"},
    "draft": {"state": "waiting_for_company", "next_actor": "company", "requires_action": True, "state_source": "wbom_escort_programs.status"},
    "confirmed": {"state": "active", "next_actor": "none", "requires_action": False, "state_source": "wbom_escort_programs.status"},
    "completed": {"state": "resolved", "next_actor": "none", "requires_action": False, "state_source": "wbom_escort_programs.status"},
    "cancelled": {"state": "resolved", "next_actor": "none", "requires_action": False, "state_source": "wbom_escort_programs.status"},
}


def resolve_escort_state(status: Optional[str]) -> ConversationState:
    return ESCORT_STATE_MAP.get(status, dict(UNKNOWN_STATE))  # type: ignore[return-value]


# ── Attendance (Stage E6) ─────────────────────────────────────────────
# Stage D7 found wbom_attendance.status but did not verify linkage. Traced
# this stage: wbom_attendance has no phone column at all -- it links via
# employee_id only. The authoritative phone->employee_id resolution
# already exists in production (modules.identity_brain._lookup_employee /
# _lookup_attendance_identity: wbom_employees.employee_mobile, normalized
# via the same _phone_variants() every other identity-resolution call in
# this codebase already uses) -- reused here, not reinvented.
#
# The genuine WORKFLOW state (what "requires action" needs) is NOT
# status ('Present'/'Half Day'/'Absent' -- a business FACT about the day,
# see modules.attendance.crud.DUTY_STATUS_VALUES, not a pending/done
# signal) -- it's the approval pipeline: modules.attendance.__init__.
# save_attendance() sets `approved_at = NOW() if approved_by else None`
# (confirmed by reading that function directly, migration 040, Owner
# decision 2026-07-11) -- so approved_at IS NULL is a real, code-enforced
# "not yet approved" signal, not a guess.
ATTENDANCE_STATE_MAP: dict[bool, ConversationState] = {
    True: {"state": "resolved", "next_actor": "none", "requires_action": False, "state_source": "wbom_attendance.approved_at"},
    False: {"state": "waiting_for_company", "next_actor": "company", "requires_action": True, "state_source": "wbom_attendance.approved_at"},
}


def resolve_attendance_state(approved_at) -> ConversationState:
    """approved_at: the latest wbom_attendance row's approved_at value for
    this employee (a datetime, or None if that row is not yet approved).
    Only call this once an actual attendance row was found for the
    employee -- a phone that resolves to an employee with NO attendance
    row at all should be reported as UNKNOWN_STATE by the caller instead,
    not routed through here (a missing record is not the same claim as
    "record exists but pending approval")."""
    return dict(ATTENDANCE_STATE_MAP[approved_at is not None])  # type: ignore[return-value]


# ── Cash / Payment (Stage E7) ────────────────────────────────────────────
# fpe_cash_transactions has direct phone columns (employee_phone,
# employee_id_phone, payout_phone -- same "01XXXXXXXXX" canonical BD
# format as escort's master_mobile/wbom_employees.employee_mobile,
# normalized via the same modules.number_identity.canonical_phone()) and
# a real FK to fpe_employees(id) -- the OTHER employee identity system
# (see CLAUDE.md: wbom_employees/fpe_employees are bridged, not merged).
# transaction_status is the genuine, dominant, already-live state field
# (confirmed live distribution: final=4390, pending=27, reversed=1) --
# approval_status/review_status track the SAME pending/approved
# distinction from two other angles on the same 27 pending rows, kept as
# additional evidence but transaction_status alone already answers
# "does this need company attention".
CASH_STATE_MAP: dict[Optional[str], ConversationState] = {
    None: {"state": "unclear", "next_actor": "unknown", "requires_action": False, "state_source": "fpe_cash_transactions.transaction_status"},
    "pending": {"state": "waiting_for_company", "next_actor": "company", "requires_action": True, "state_source": "fpe_cash_transactions.transaction_status"},
    "final": {"state": "resolved", "next_actor": "none", "requires_action": False, "state_source": "fpe_cash_transactions.transaction_status"},
    "reversed": {"state": "resolved", "next_actor": "none", "requires_action": False, "state_source": "fpe_cash_transactions.transaction_status"},
}


def resolve_cash_state(transaction_status: Optional[str]) -> ConversationState:
    return CASH_STATE_MAP.get(transaction_status, dict(UNKNOWN_STATE))  # type: ignore[return-value]


# ── Operations domains still NOT wired (unchanged from Stage D7) ────────
STRUCTURED_STATE_SOURCE_CANDIDATES: dict[str, str] = {}
