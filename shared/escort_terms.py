"""
Canonical escort vocabulary (Owner Phase 2, 2026-08-30).

The word "escort" alone is ambiguous in this codebase — it has meant BOTH
sides of the escort service:

    escort_worker  — OUR employee: a security escort assigned to a vessel /
                     lighter. A row in wbom_employees (designation ~ "escort")
                     and/or fpe_employees; appears in wbom_escort_programs as
                     escort_employee_id / escort_name / escort_mobile. Managed
                     like any internal staff member (attendance, payroll,
                     manager-voice instructions).

    escort_client  — a CUSTOMER who buys Escort Service from us. Identified by
                     a real client/order relationship (ESCORT_CLIENT_PHONES
                     allowlist, a verified client_escort_buyer contact, an
                     escort order record) — never by a contact-name prefix
                     alone.

These are DIFFERENT ENTITIES and must never be conflated in a new
identity/classification decision.

Compatibility: the legacy bare role literal ``"escort"`` is still emitted and
consumed across the codebase (identity_brain._ROLE_PRIORITY, ROUTE_B_ROLES,
authority_lanes, reply_policy, reports, role_classifier, …). It is retained as
an explicit **alias for escort_worker** — this module does not rename it. New
code that needs the unambiguous form should call ``canonical_escort_role()``.
"""
from __future__ import annotations

# Canonical role strings
ESCORT_WORKER = "escort_worker"
ESCORT_CLIENT = "escort_client"

# Legacy bare role literal — an alias for the worker side.
ESCORT_LEGACY = "escort"

# Every role string that means "our escort employee".
ESCORT_WORKER_ROLES = frozenset({ESCORT_WORKER, ESCORT_LEGACY})

# Every role string that means "a customer buying escort service".
ESCORT_CLIENT_ROLES = frozenset({ESCORT_CLIENT, "client_escort_buyer"})

# Business-classification domains (kept distinct so a worker's duty report and
# a client's order request are never bucketed together).
ESCORT_ASSIGNMENT_DOMAIN = "escort_assignment"  # worker-side operations
ESCORT_ORDER_DOMAIN = "escort_order"            # client-side order / request


def canonical_escort_role(role: str | None) -> str | None:
    """Normalise a role string to its unambiguous canonical form.

    ``"escort"`` -> ``"escort_worker"``; everything else is returned unchanged.
    """
    if role is None:
        return None
    r = role.strip()
    if r == ESCORT_LEGACY:
        return ESCORT_WORKER
    return r


def is_escort_worker_role(role: str | None) -> bool:
    return canonical_escort_role(role) == ESCORT_WORKER if role else False


def is_escort_client_role(role: str | None) -> bool:
    return (role or "").strip() in ESCORT_CLIENT_ROLES


def escort_domain_for_role(role: str | None) -> str | None:
    """Map an escort-side role to its business-classification domain."""
    if is_escort_worker_role(role):
        return ESCORT_ASSIGNMENT_DOMAIN
    if is_escort_client_role(role):
        return ESCORT_ORDER_DOMAIN
    return None
