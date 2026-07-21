"""
Fazle Core — Route B Conversation Handoff

Owner-approved staged rollout (2026-07-22). Lets modules.route_b transfer a
conversation to an existing canonical workflow once identity/intent becomes
sufficiently clear, without Route B ever gaining write access to any
business table itself.

Non-negotiable design boundaries (Owner decisions, all enforced here, not
just documented):
  - Ollama may only ever RECOMMEND a category (modules.ollama.classify_route_b_category).
    It never creates a session, assigns a role, activates an employee, links
    a phone number, or writes a final business row. Every function below
    that actually changes state is an EXISTING canonical function from
    modules.recruitment_flow / modules.employee_conversation / modules.escort
    -- nothing here duplicates their logic, this module only decides WHEN to
    call them and WITH what corroboration.
  - A known employee (active or inactive, phone already on file in
    wbom_employees) never reaches this module at all -- identity_brain
    resolves them to Tier 10 before message_router ever calls Route B. So
    "employee claim" here is, by construction, always a phone that did NOT
    match -- and it NEVER gets direct Tier 10 access. It always produces an
    admin-review draft; real access is only ever (re-)granted the existing,
    trusted way, by identity_brain recognizing a registered phone later.
  - Master switch: settings.route_b_handoff_enabled (Stage 1 recruitment +
    Stage 2 employee-claim review). Escort/security_guard multi-message
    handoff (Stage 3) additionally requires settings.route_b_escort_handoff_enabled
    -- "prepared but not activated until separately reviewed" per Owner
    decision, distinct from the Stage 1/2 flag.
  - Every handoff decision (including "no handoff") that reaches a terminal
    outcome is recorded in fazle_route_b_handoffs for audit + cooldown/loop
    prevention.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from app.config import get_settings
from app.database import execute, fetch_val
from app import ollama as ollama_mod
from shared.draft_reply import create_draft_reply
from modules.recruitment_flow import (
    recruitment_eligibility,
    route_recruitment_message,
    intake_message,
    get_active_session,
)
from modules.employee_conversation import resolve_employee_identity, is_employee_active

log = logging.getLogger("fazle.route_b_handoff")


@dataclass
class HandoffResult:
    reply: str
    admin_note: Optional[dict]
    destination: str   # 'recruitment' | 'employee_claim_review' | 'escort_order' | 'none'
    reason: str
    confidence: str
    outcome: str        # 'handed_off' | 'admin_review' | 'declined'


# ── Audit + idempotency/cooldown ────────────────────────────────────────────────

async def _record_handoff(
    *, phone: str, message_id: Optional[str], destination: str,
    reason: str, confidence: str, outcome: str,
) -> None:
    """Auditable handoff record. Idempotent per message_id via the partial
    unique index (db/migrations/057_route_b_handoffs.sql) -- a retried
    delivery of the same inbound message can never produce two rows."""
    try:
        await execute(
            """INSERT INTO fazle_route_b_handoffs
                   (phone, message_id, source_route, destination, reason, confidence, outcome)
               VALUES ($1, $2, 'route_b', $3, $4, $5, $6)
               ON CONFLICT (message_id) WHERE message_id IS NOT NULL DO NOTHING""",
            phone, message_id, destination, reason, confidence, outcome,
        )
    except Exception as e:
        log.warning("[route_b_handoff] audit insert failed phone=%s: %s", phone, e)


async def _on_cooldown(phone: str, destination: str) -> bool:
    """True if a handoff to this destination for this phone was already
    recorded within the cooldown window -- prevents re-drafting/reclassifying
    on every message of an ongoing ambiguous exchange."""
    settings = get_settings()
    try:
        row_id = await fetch_val(
            """SELECT id FROM fazle_route_b_handoffs
               WHERE phone = $1 AND destination = $2
                 AND created_at > NOW() - ($3 * INTERVAL '1 hour')
               ORDER BY created_at DESC LIMIT 1""",
            phone, destination, settings.route_b_handoff_cooldown_hours,
        )
        return row_id is not None
    except Exception as e:
        log.warning("[route_b_handoff] cooldown check failed phone=%s: %s", phone, e)
        return False


# ── Stage 1.1 — active recruitment-session continuation ────────────────────────

async def _check_active_recruitment_session(
    sender: str, text: str, source: str, role: str,
) -> Optional[HandoffResult]:
    """If a recruitment funnel session is already in progress, continue it
    unconditionally via the canonical intake_message() -- it advances an
    EXISTING session's collection_step for any answer text with no keyword
    gate (the gate only applies to brand-new sessions), so a bare funnel
    answer like "25" correctly continues without needing to look
    recruitment-shaped on its own. Never calls Ollama."""
    session = await get_active_session(sender)
    if not session:
        return None
    intake = await intake_message(sender, text, source, role=role)
    reply = (intake.get("reply") or "").strip()
    return HandoffResult(
        reply=reply, admin_note=None, destination="recruitment",
        reason="active_session_continuation", confidence="session",
        outcome="handed_off" if reply else "declined",
    )


# ── Stage 1.2 — recruitment handoff (Ollama hint, Python decides) ──────────────

async def _check_recruitment_handoff(
    sender: str, text: str, source: str, role: str, intent: str,
    history: str, ollama_category: str,
) -> Optional[HandoffResult]:
    # Deterministic-first, current message only -- identical to what
    # message_router Tier 7/8 already computed for this same message before
    # Route B ever ran. In practice this is always False by construction
    # (Tier 7/8 would have already handed off otherwise), kept for
    # correctness/defense rather than assumed.
    decision = await recruitment_eligibility(sender, text, role=role, intent=intent)
    if decision.get("eligible"):
        rec_route = await route_recruitment_message(
            phone=sender, text=text, source=source, role=role, intent=intent,
            history=history, contact_context="",
        )
        reply = (rec_route.get("reply") or "").strip()
        if reply:
            return HandoffResult(
                reply=reply, admin_note=None, destination="recruitment",
                reason=decision.get("reason", "explicit_recruitment"),
                confidence="deterministic", outcome="handed_off",
            )

    if ollama_category != "recruitment":
        return None

    # Ollama sees conversation-level signal the single-message deterministic
    # layer can't (it takes text only, no history). Never trust it directly
    # -- give the SAME deterministic function (is_recruitment_trigger /
    # detect_recruitment_intent, both plain text-pattern functions) a wider
    # window to work with: the immediately preceding turn + the current
    # message. This is not new recruitment-domain logic, it's the existing
    # function fed more of the existing conversation.
    last_line = (history or "").strip().splitlines()[-1] if (history or "").strip() else ""
    window_text = f"{last_line}\n{text}".strip() if last_line else text
    wide_decision = await recruitment_eligibility(sender, window_text, role=role, intent="recruitment")
    if not wide_decision.get("eligible"):
        return None  # Ollama's hint not corroborated even with more context

    rec_route = await route_recruitment_message(
        phone=sender, text=text, source=source, role=role, intent="recruitment",
        history=history, contact_context="",
    )
    if rec_route.get("eligible") and (rec_route.get("reply") or "").strip():
        return HandoffResult(
            reply=rec_route["reply"].strip(), admin_note=None, destination="recruitment",
            reason=f"ollama_suggested+history_corroborated:{wide_decision.get('reason')}",
            confidence="ollama+detector", outcome="handed_off",
        )

    # The narrow (current-message-only) internal re-check inside
    # route_recruitment_message() disagreed with the wide corroboration
    # check above (e.g. current message alone has zero keyword signal, only
    # the combined window does). Fall back to the canonical session-creation
    # primitive directly, supplying the same wider window purely to satisfy
    # its own keyword gate for a brand-new session -- the returned reply is
    # still the fixed, canonical "what's your name?" prompt, not anything
    # derived from Ollama's output.
    intake = await intake_message(sender, window_text, source, role=role)
    if intake.get("action") == "ignored" or not (intake.get("reply") or "").strip():
        return None
    return HandoffResult(
        reply=intake["reply"].strip(), admin_note=None, destination="recruitment",
        reason=f"ollama_suggested+history_corroborated_intake:{wide_decision.get('reason')}",
        confidence="ollama+detector", outcome="handed_off",
    )


# ── Stage 2 — employee / former-employee claim (built+tested, master flag only) ─

_EMPLOYEE_ID_CLAIM_RE = re.compile(
    r"(?:employee\s*id|emp\s*id|আইডি|id\s*no)\s*[:#-]?\s*(\d{1,10})", re.IGNORECASE,
)
_NAME_CLAIM_RE = re.compile(
    r"(?:আমার\s*নাম|আমি|amar naam|my name is|i am|i'm)\s+([A-Za-zঀ-৿][A-Za-zঀ-৿\s]{1,39})",
    re.IGNORECASE,
)


def _extract_employee_claim_fields(text: str) -> tuple[Optional[int], Optional[str]]:
    """Deterministic (non-Ollama) best-effort extraction. Ollama is never the
    source of the employee_id/name actually looked up -- only of the initial
    "this looks like an employee claim" category hint that triggers this
    check at all. A wrong/loose extraction here only ever produces an
    unverified admin-review entry, never access, so a conservative regex is
    an acceptable (and intentionally narrow) heuristic."""
    m = _EMPLOYEE_ID_CLAIM_RE.search(text or "")
    employee_id = int(m.group(1)) if m else None
    m2 = _NAME_CLAIM_RE.search(text or "")
    name_hint = m2.group(1).strip()[:60] if m2 else None
    return employee_id, name_hint


async def _check_employee_claim_handoff(
    sender: str, text: str, source: str, ollama_category: str, message_id: Optional[str],
) -> Optional[HandoffResult]:
    if ollama_category not in ("employee_claim", "former_employee_claim"):
        return None

    if await _on_cooldown(sender, "employee_claim_review"):
        return HandoffResult(
            reply="", admin_note=None, destination="employee_claim_review",
            reason="cooldown_suppressed", confidence="n/a", outcome="declined",
        )

    employee_id, name_hint = _extract_employee_claim_fields(text)
    identity = await resolve_employee_identity(sender, employee_id=employee_id, name_hint=name_hint)
    verified = bool(identity.get("verified"))
    active = is_employee_active(identity) if verified else False

    if verified and active:
        confidence = "phone_or_id_verified_active"
    elif verified:
        confidence = "phone_or_id_verified_inactive_or_former"
    elif identity.get("resolution") == "name_match":
        confidence = "name_match_only_unverified"
    else:
        confidence = "unresolved"

    from modules.message_router import get_primary_admin

    admin_text = (
        "⚠️ EMPLOYEE/FORMER-EMPLOYEE CLAIM — ROUTE B (ADMIN REVIEW ONLY)\n"
        f"Sender: {sender}\n"
        f"Claim type: {ollama_category}\n"
        f"resolve_employee_identity(): employee_id={identity.get('employee_id')} "
        f"name={identity.get('employee_name')} status={identity.get('status')} "
        f"resolution={identity.get('resolution')} verified={verified}\n\n"
        f"Message: {text}\n\n"
        "This sender was NOT granted employee access. If genuine, correct/link "
        "their phone number on the wbom_employees record -- they will then be "
        "recognized automatically on their next message via the normal, "
        "unchanged Tier 10 path. No action here grants access directly."
    )
    admin_note = {
        "admin_phone": get_primary_admin(),
        "bridge": source,
        "purpose": "route_b_employee_claim_review",
        "text": admin_text,
    }
    await create_draft_reply(
        sender=sender, bridge=source, draft_text=admin_text,
        role="unknown", intent="route_b_employee_claim_review",
        source_module="route_b_handoff",
    )
    return HandoffResult(
        reply="", admin_note=admin_note, destination="employee_claim_review",
        reason=f"ollama_suggested:{ollama_category}", confidence=confidence, outcome="admin_review",
    )


# ── Stage 3 — escort/security_guard multi-message handoff (NOT activated) ──────
# Gated by settings.route_b_escort_handoff_enabled, checked by the caller
# (evaluate_handoff) in addition to being inert-by-default -- kept here fully
# built and tested per Owner instruction ("prepare but do not activate").

async def _check_escort_operational_handoff(
    sender: str, text: str, source: str, role: str, history: str,
) -> Optional[HandoffResult]:
    if role not in ("escort", "security_guard"):
        return None

    from modules.escort import is_order_complete, handle_escort_client_message

    if is_order_complete(text):
        reply, admin_note = await handle_escort_client_message(text, sender, source, is_verified=False)
        return HandoffResult(
            reply=reply, admin_note=admin_note, destination="escort_order",
            reason="strict_order_complete", confidence="phone_unverified", outcome="handed_off",
        )

    # Multi-message: fields may be spread across turns (e.g. mother vessel in
    # an earlier message, lighter+mobile in this one). Use ONLY the governed
    # conversation history Route B already fetched (wbom_whatsapp_messages),
    # never anything from Ollama's own output.
    if not history:
        return None
    window_text = f"{history}\n{text}"
    if not is_order_complete(window_text):
        return None
    reply, admin_note = await handle_escort_client_message(window_text, sender, source, is_verified=False)
    return HandoffResult(
        reply=reply, admin_note=admin_note, destination="escort_order",
        reason="strict_order_complete_multi_message", confidence="phone_unverified", outcome="handed_off",
    )


# ── Ollama-failure protection ───────────────────────────────────────────────────

async def record_ollama_failure(sender: str, source: str, message_id: Optional[str]) -> Optional[dict]:
    """Called by modules.route_b when Ollama's own reply equals
    app.ollama.FALLBACK_REPLY_TEXT (Ollama unreachable/non-200/exception --
    generate_reply() never raises, it degrades to this fixed string). The
    sender still gets that safe, non-empty fallback text either way -- this
    only additionally alerts an admin, once per cooldown window, so a
    prolonged Ollama outage doesn't silently leave real senders unanswered
    with nobody aware. Never raises."""
    try:
        if await _on_cooldown(sender, "none"):
            return None
        from modules.message_router import get_primary_admin
        admin_note = {
            "admin_phone": get_primary_admin(),
            "bridge": source,
            "purpose": "route_b_ollama_failure",
            "text": (
                "⚠️ ROUTE B: OLLAMA UNAVAILABLE\n"
                f"Sender: {sender}\n"
                "The sender received the standard safe fallback reply; no AI "
                "response was generated. Check Ollama health if this repeats."
            ),
        }
        await _record_handoff(
            phone=sender, message_id=message_id, destination="none",
            reason="ollama_failure", confidence="n/a", outcome="admin_review",
        )
        return admin_note
    except Exception as e:
        log.warning("[route_b_handoff] record_ollama_failure error phone=%s: %s", sender, e)
        return None


# ── Entry point ──────────────────────────────────────────────────────────────────

async def evaluate_handoff(
    *, sender: str, text: str, source: str, identity: dict, intent: str,
    history: str, message_id: Optional[str],
) -> Optional[HandoffResult]:
    """Single entry point called from modules.route_b.handle_route_b_message()
    after Ollama's own reply has been generated. Returns None if no handoff
    applies (Route B's own Ollama reply stands unchanged)."""
    settings = get_settings()
    if not settings.route_b_handoff_enabled:
        return None  # master switch off -- zero behavior change from baseline

    role = identity.get("identity_role") or identity.get("role") or "unknown"

    session_result = await _check_active_recruitment_session(sender, text, source, role)
    if session_result:
        await _record_handoff(
            phone=sender, message_id=message_id, destination=session_result.destination,
            reason=session_result.reason, confidence=session_result.confidence,
            outcome=session_result.outcome,
        )
        return session_result

    ollama_category = "none"
    try:
        ollama_category = await ollama_mod.classify_route_b_category(text, history=history)
    except Exception as e:  # classify_route_b_category itself never raises; belt-and-suspenders
        log.warning("[route_b_handoff] unexpected classify error phone=%s: %s", sender, e)
        ollama_category = "none"

    rec_result = await _check_recruitment_handoff(sender, text, source, role, intent, history, ollama_category)
    if rec_result:
        await _record_handoff(
            phone=sender, message_id=message_id, destination=rec_result.destination,
            reason=rec_result.reason, confidence=rec_result.confidence, outcome=rec_result.outcome,
        )
        return rec_result

    claim_result = await _check_employee_claim_handoff(sender, text, source, ollama_category, message_id)
    if claim_result:
        if claim_result.reason != "cooldown_suppressed":
            await _record_handoff(
                phone=sender, message_id=message_id, destination=claim_result.destination,
                reason=claim_result.reason, confidence=claim_result.confidence, outcome=claim_result.outcome,
            )
        return claim_result

    if settings.route_b_escort_handoff_enabled:
        escort_result = await _check_escort_operational_handoff(sender, text, source, role, history)
        if escort_result:
            await _record_handoff(
                phone=sender, message_id=message_id, destination=escort_result.destination,
                reason=escort_result.reason, confidence=escort_result.confidence, outcome=escort_result.outcome,
            )
            return escort_result

    return None
