"""Stage F (2026-08-28) -- Business Brain: turns a verified Business Inbox
attention item (Stage D/E's GET /api/whatsapp/conversations/attention)
into a structured, evidence-backed PROPOSED action.

Explicitly reuses the existing propose -> approve -> execute -> verify
gate (modules.hermes_tasks.propose_action(), migration 068, extended by
migration 076) rather than a second, parallel approval framework -- see
this module's own tests for the "do not invent a second approval
architecture" proof.

CAPABILITY != AUTHORIZATION (Stage F3): this module can only ever create
a 'pending' proposal. It never approves, never sends a message, never
mutates a business record, never executes anything. Approval requires
the existing task_approve (superadmin) RBAC gate on
POST /api/actions/{id}/approve; sending a drafted reply requires the
existing, separate fazle_draft_replies / approve_draft() pipeline
(Hermes Capability Expansion Level 1, 2026-08-10) -- neither gate is
touched or weakened here.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

log = logging.getLogger("fazle.business_actions")

# Stage E's proven, deterministic-state domains -- the ONLY domains a
# proposal may be created for. Payroll/Salary, Employee/HR lifecycle,
# Client, and Operations-other-than-Escort stay honestly unsupported
# (Stage E8's coverage matrix) -- propose_business_action() refuses them
# rather than silently accepting and fabricating a proposal. Office/
# Internal is deliberately NOT in this set either -- Stage E3/F4's
# explicit "Office group remains READ/INDEX only, no Hermes automatic
# reply, no action proposal" boundary.
SUPPORTED_DOMAINS = frozenset({"recruitment", "escort", "attendance", "cash"})

# Stage F3 Class B (draft/propose -- no mutation of authoritative
# business state). Both map to action_type values added by migration 076.
BUSINESS_ACTION_TYPES = frozenset({"business_reply_draft", "business_record_review"})


class BusinessActionError(ValueError):
    """Caller mistake (unsupported domain/action type, missing evidence)
    -- distinct from a genuine DB/infra failure."""


def business_idempotency_key(domain: str, business_reference: str, proposed_action_type: str, source_event_ref: str) -> str:
    """Stage F7: stable identifier from (business object + action type +
    source event reference) -- never message text/body. Same sha256
    convention modules.bridge_poller._core_message_hash already
    established for canonical message dedup, reused here rather than
    inventing a second hashing scheme."""
    raw = f"{domain}|{business_reference}|{proposed_action_type}|{source_event_ref}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def propose_business_action(
    domain: str,
    business_reference: str,
    proposed_action_type: str,
    reason: str,
    evidence_refs: list[dict],
    source_event_ref: str,
    requested_by: str = "hermes",
) -> dict:
    """Create a structured proposed action for one Business Inbox
    attention item. Returns {"ok": True, "action_id": int, "status":
    "pending"|<existing status if idempotency matched>, "duplicate": bool}.

    Raises BusinessActionError for an unsupported domain/action_type or
    missing evidence -- callers (the fazle-core route, fazle-mcp tool)
    should surface this as a 400, not silently fall back to guessing."""
    if domain not in SUPPORTED_DOMAINS:
        raise BusinessActionError(
            f"domain must be one of {sorted(SUPPORTED_DOMAINS)} (Stage E's proven "
            f"deterministic-state domains), got {domain!r} -- unsupported domains stay "
            f"honestly unsupported, never a fabricated proposal"
        )
    if proposed_action_type not in BUSINESS_ACTION_TYPES:
        raise BusinessActionError(f"proposed_action_type must be one of {sorted(BUSINESS_ACTION_TYPES)}, got {proposed_action_type!r}")
    if not business_reference or not business_reference.strip():
        raise BusinessActionError("business_reference is required")
    if not reason or not reason.strip():
        raise BusinessActionError("reason is required")
    if not evidence_refs:
        raise BusinessActionError("evidence_refs must not be empty -- a proposal must cite what justified it")
    if not source_event_ref or not source_event_ref.strip():
        raise BusinessActionError("source_event_ref is required for idempotency")
    # Real enforcement of "reference, don't copy" (Stage F2), not just a
    # documented convention -- a reference is an id/short label, not
    # message content. Bounded generously enough for a real business
    # reference (e.g. a conversation_key) while still rejecting anything
    # body-shaped.
    for ref in evidence_refs:
        if not isinstance(ref, dict) or "type" not in ref or "value" not in ref:
            raise BusinessActionError('each evidence_refs entry must be {"type": ..., "value": ...}')
        if len(str(ref.get("value", ""))) > 200:
            raise BusinessActionError(
                "evidence_refs value too long (>200 chars) -- this must be a reference "
                "(id/key), not message content; store the id and let a reader fetch the "
                "actual record separately"
            )

    from app.database import fetch_one
    from modules.hermes_tasks import propose_action, get_action

    idem_key = business_idempotency_key(domain, business_reference, proposed_action_type, source_event_ref)

    # Checked here (not just inside propose_action()) so this function can
    # report `duplicate: true/false` plainly rather than making the
    # caller infer it -- propose_action() will independently re-check the
    # same key and return the same existing id either way, so there is no
    # race where this check says "new" but the insert silently dedupes
    # (the partial unique index still protects against a genuine race
    # between two concurrent calls; this is a reporting convenience only).
    pre_existing = await fetch_one(
        "SELECT id FROM hermes_action_approvals WHERE idempotency_key = $1 "
        "AND status IN ('pending', 'approved', 'executed')",
        idem_key,
    )

    # Class B actions are always risk="low" -- no mutation of
    # authoritative business state happens by creating this row (Stage F3).
    action_id = await propose_action(
        task_id=None,
        action_type=proposed_action_type,
        summary=reason.strip()[:2000],
        risk="low",
        expected_effect=f"{proposed_action_type} for {domain} conversation {business_reference}",
        requested_by=requested_by,
        domain=domain,
        business_reference=business_reference,
        evidence_refs=evidence_refs,
        idempotency_key=idem_key,
    )

    row = await get_action(action_id)
    return {
        "ok": True,
        "action_id": action_id,
        "status": row["status"] if row else "pending",
        "domain": domain,
        "business_reference": business_reference,
        "idempotency_key": idem_key,
        "duplicate": bool(pre_existing),
    }
