"""Stage G1 (2026-08-28) -- the formal Owner-defined operational authority
policy resolver: resolve_authority_lane().

Discovery, not invention: this codebase already has a rich, carefully
designed identity/role vocabulary (modules.identity_brain._ROLE_PRIORITY)
and an existing routing split (Route A vs Route B, modules.route_b's own
ROUTE_B_ROLES) that implicitly encode most of Owner Directive Stage G's
5-lane policy -- e.g. "Client X in CSV ... Hermes skips" (identity_brain's
own comment) already means client roles never get an autonomous AI reply
today. resolve_authority_lane() does not replace or rewire any of that
existing routing -- it is a NEW, explicit, auditable classification layer
on TOP of the same identity/role facts, so every caller (autonomous-reply
code, draft-creation code, the attention/business-actions layer, a human
reading an audit log) can ask "which lane does this belong to" and get
one authoritative, testable answer instead of each call site re-deriving
the policy from role strings independently.

Never infers a lane from message text -- only from authoritative context
(identity_role, actor_type, domain, group/direct-chat status, source
bridge, business object / action type), per Stage G1's explicit
instruction.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── The 5 lanes (Owner-defined) ──────────────────────────────────────────
AUTONOMOUS_OPERATIONAL = "autonomous_operational"        # A: recruitment + employee self-service
OWNER_SUPERVISED_DRAFT = "owner_supervised_draft"         # B: client + office/internal
TRUSTED_ADMIN_EXECUTION = "trusted_admin_execution"       # C: trusted direct Admin instruction
AUTONOMOUS_MONITORING = "autonomous_monitoring"           # D: system monitoring (not a conversation lane)
GATED_SYSTEM_MODIFICATION = "gated_system_modification"   # E: code/infra changes

LANES = frozenset({
    AUTONOMOUS_OPERATIONAL, OWNER_SUPERVISED_DRAFT, TRUSTED_ADMIN_EXECUTION,
    AUTONOMOUS_MONITORING, GATED_SYSTEM_MODIFICATION,
})

# ── Role -> lane-relevant category, reusing identity_brain's own
# _ROLE_PRIORITY vocabulary verbatim (never a second taxonomy). Any role
# not listed here (family/family_ceo/friend_relative/vendor/
# political_contact/government_contact/personal_business/repeat_client's
# unlisted siblings, or a genuinely new future role) falls through to the
# safe default below -- never silently treated as autonomous.
_RECRUITMENT_ROLES = frozenset({"candidate"})
_EMPLOYEE_ROLES = frozenset({"employee", "escort", "security_guard", "supervisor"})
_OFFICE_INTERNAL_ROLES = frozenset({"internal_coordinator", "internal_operation", "employee_internal"})
_CLIENT_ROLES = frozenset({
    "vip_client", "security_service_client", "client_escort_buyer",
    "escort_client", "repeat_client",
})
_ADMIN_ROLES = frozenset({"admin"})


@dataclass(frozen=True)
class LaneContext:
    """Authoritative context for lane resolution -- every field here comes
    from something the system already verified (a DB-resolved identity
    role, the transport-level source_bridge, the parsed chat_jid), never
    from parsing message text."""
    identity_role: str = "unknown"
    actor_type: Optional[str] = None          # external_user/human_device/hermes/automation/system
    is_group: bool = False
    domain: Optional[str] = None              # recruitment/escort/attendance/cash/office_internal/None
    source_bridge: Optional[str] = None
    sender_is_admin_number: bool = False       # sender_phone == settings.admin_bridge2_number
    channel_is_trusted_admin: bool = False     # source_bridge in _HERMES_CONTROL_CHANNELS (see admin_directives.router)
    action_type: Optional[str] = None          # a hermes_action_approvals action_type, when resolving for one


@dataclass(frozen=True)
class LaneDecision:
    lane: str
    reason: str


def resolve_authority_lane(ctx: LaneContext) -> LaneDecision:
    """The one authoritative classifier. Every branch's `reason` is
    logged/returned so the decision itself is auditable (Stage G1's
    explicit requirement) -- never a bare enum with no explanation."""

    # E: system modification -- action_type in the existing Stage D/E/F
    # infra-action set always wins, regardless of who's asking. Preserves
    # the existing BUILD/RUN/diff/commit/restart/deploy gates untouched.
    _INFRA_ACTION_TYPES = frozenset({
        "file_mutation", "git_commit", "deploy", "migration", "restart",
        "production_write", "opencode_dispatch",
    })
    if ctx.action_type in _INFRA_ACTION_TYPES:
        return LaneDecision(GATED_SYSTEM_MODIFICATION, f"action_type={ctx.action_type} is an infra-action type")

    # D: monitoring is never resolved from conversation context -- it's a
    # separate, non-conversational lane a caller asks for explicitly
    # (see modules.reports/modules.scheduler's own monitoring jobs), not
    # something a message routes into. Included here only so the enum is
    # complete and a monitoring caller can assert its own lane explicitly.
    if ctx.domain == "system_monitoring":
        return LaneDecision(AUTONOMOUS_MONITORING, "explicit system_monitoring domain")

    # Stage E3/F10's group short-circuit boundary, reasserted here as an
    # explicit lane decision (not just an absence of routing): a group
    # message is NEVER trusted_admin_execution, no matter who posted it or
    # whether the sender phone matches the admin number -- GROUP MESSAGE
    # != TRUSTED ADMIN COMMAND (Stage G6's explicit instruction).
    if ctx.is_group:
        return LaneDecision(OWNER_SUPERVISED_DRAFT, "group conversation -- office group stays supervised-draft, never trusted-admin, regardless of participant identity")

    # An internal echo/system-generated row is never a request needing a
    # lane at all in the sense of "who gets to act" -- but if a caller
    # still asks, the safe answer is supervised, never autonomous-send or
    # trusted-admin (Stage G's "internal echo never trusted-admin").
    if ctx.actor_type in ("system", "hermes", "automation"):
        return LaneDecision(OWNER_SUPERVISED_DRAFT, f"actor_type={ctx.actor_type} is an internal/system-generated row, not a genuine external request")

    # C: trusted direct Admin instruction -- requires BOTH the exact
    # identity check AND the exact channel check already established by
    # modules.admin_directives.router._handle_hermes_relay (sender_phone
    # == admin_bridge2_number AND source_bridge in _HERMES_CONTROL_CHANNELS)
    # -- reused via the two boolean fields on LaneContext rather than
    # re-implementing the check here, so there is exactly one place that
    # check's actual logic lives.
    if ctx.sender_is_admin_number and ctx.channel_is_trusted_admin and ctx.identity_role in _ADMIN_ROLES:
        return LaneDecision(TRUSTED_ADMIN_EXECUTION, "verified admin identity on a trusted direct channel")

    # A: autonomous operational -- recruitment + employee self-service.
    if ctx.identity_role in _RECRUITMENT_ROLES:
        return LaneDecision(AUTONOMOUS_OPERATIONAL, f"identity_role={ctx.identity_role} is a recruitment role")
    if ctx.identity_role in _EMPLOYEE_ROLES:
        return LaneDecision(AUTONOMOUS_OPERATIONAL, f"identity_role={ctx.identity_role} is an employee self-service role")

    # B: owner-supervised draft -- client + office/internal.
    if ctx.identity_role in _CLIENT_ROLES:
        return LaneDecision(OWNER_SUPERVISED_DRAFT, f"identity_role={ctx.identity_role} is a client role")
    if ctx.identity_role in _OFFICE_INTERNAL_ROLES:
        return LaneDecision(OWNER_SUPERVISED_DRAFT, f"identity_role={ctx.identity_role} is an office/internal role")

    # Safe default: anything not positively classified (unknown, family,
    # vendor, political_contact, government_contact, personal_business,
    # or a genuinely new future role) is supervised-draft, never
    # autonomous-send and never trusted-admin. Unknown/ambiguous identity
    # must not receive autonomous treatment (Stage G3's explicit rule).
    return LaneDecision(OWNER_SUPERVISED_DRAFT, f"identity_role={ctx.identity_role!r} not positively classified -- safe default, never autonomous")
