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


# ── Operations domains investigated but NOT wired (Stage D7, disclosed) ──
# wbom_attendance.status and fpe_cash_transactions.{transaction_status,
# approval_status,review_status} both exist as real, deterministic,
# already-live structured state -- confirmed present, NOT confirmed
# reliably joinable to a WhatsApp conversation_key within this stage
# (would need the same per-domain phone/message-linkage verification pass
# escort received; not done here). Reported as
# STRUCTURED_STATE_SOURCE_CANDIDATE, not wired into the attention query,
# rather than wiring an unverified join and risking a false
# requires_action claim.
STRUCTURED_STATE_SOURCE_CANDIDATES: dict[str, str] = {
    "attendance": "wbom_attendance.status (join-verification not done this stage)",
    "cash": "fpe_cash_transactions.{transaction_status,approval_status,review_status} (join-verification not done this stage)",
}
