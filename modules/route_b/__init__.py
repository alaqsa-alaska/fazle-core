"""
Fazle Core — Route B (Ollama-only reply handler)

2026-07-21 identity-first routing work. Handles every sender whose
identity_brain role is in modules.identity_brain.ROUTE_B_ROLES (escort,
security_guard, candidate, unknown) plus a money/advance-request override
for those same roles. Called from modules.message_router.process_message()
as just another branch — NOT a separate ingestion path — so it inherits the
exact same downstream safety pipeline (bridge_poller.process_bridge_inbound:
cooldown, keyword flood, prompt-injection, DRAFT_ALWAYS, financial-draft
gate, loop detection, AI_SAFE_MODE, etc.) that every other branch already
goes through. Returns (reply_text, admin_notification | None) — identical
contract to process_message() itself.

Design boundary (Owner-approved):
  - Bypasses KB/template machinery and calls app.ollama.generate_reply()
    directly — the one Ollama-only function with no Groq/GitHub fallback.
  - Read-only context gathering from: wbom_whatsapp_messages, wbom_contacts,
    the per-bridge whatsmeow_contacts SQLite (WhatsApp profile name), and
    the already-computed identity_brain identity dict. Never queries
    wbom_employees, fpe_cash_transactions, wbom_escort_programs,
    escort_roster_entries, or attendance tables directly.
  - Money/advance intent additionally returns an admin_notification dict;
    the actual WhatsApp send is done by the existing bridge_poller delivery
    pipeline exactly like every other branch's admin_notification. This
    module only ever writes to fazle_draft_replies (via the shared
    create_draft_reply helper) and fazle_payment_drafts.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from app.config import get_settings
from app.database import fetch_all, fetch_one, fetch_val
from app.ollama import generate_reply as ollama_generate_reply
from shared.draft_reply import create_draft_reply
from modules.payment_workflow import is_advance_request

log = logging.getLogger("fazle.route_b")

_HISTORY_LIMIT = 20
_CONTACT_NAME_MAX_ROWS = 1


async def _get_conversation_history(phone: str, source: str, limit: int = _HISTORY_LIMIT) -> str:
    """Last N messages, both directions, for this phone on this source.
    Read-only against wbom_whatsapp_messages."""
    try:
        rows = await fetch_all(
            """SELECT direction, message_body, received_at
               FROM wbom_whatsapp_messages
               WHERE sender_number = $1 OR receiver_number = $1
               ORDER BY received_at DESC
               LIMIT $2""",
            phone, limit,
        )
    except Exception as e:
        log.debug("[route_b] history fetch failed phone=%s: %s", phone, e)
        return ""
    lines = []
    for r in reversed(rows):
        tag = "তাকে" if r["direction"] == "outbound" else "সে"
        body = (r.get("message_body") or "").strip()
        if body:
            lines.append(f"[{tag}] {body[:200]}")
    return "\n".join(lines)


async def _get_saved_contact(phone: str) -> Optional[dict]:
    """wbom_contacts row for this number, if any. Read-only."""
    try:
        from modules.number_identity import normalize_phone as _variants
        variants = _variants(phone) or [phone]
        return await fetch_one(
            """SELECT display_name, company_name, relation, notes, personality_hint,
                      interaction_count, last_seen
               FROM wbom_contacts
               WHERE whatsapp_number = ANY($1) AND is_active = true
               LIMIT 1""",
            variants,
        )
    except Exception as e:
        log.debug("[route_b] contact fetch failed phone=%s: %s", phone, e)
        return None


def _whatsapp_db_for_source(source: str) -> Optional[str]:
    settings = get_settings()
    if source == "bridge1":
        return settings.bridge1_whatsapp_db_path
    if source == "bridge2":
        return settings.bridge2_whatsapp_db_path
    return None  # meta has no whatsmeow SQLite store


def _read_whatsapp_profile_name(whatsapp_db: str, phone_digits: str) -> str:
    """Read-only whatsmeow_contacts lookup for the sender's real WhatsApp
    profile name (push_name/full_name/first_name) — same URI=ro pattern
    bridge_poller._load_lid_map() already uses. Sync — caller runs it in a
    thread if called from an async context with a tight loop budget."""
    try:
        con = sqlite3.connect(f"file:{whatsapp_db}?mode=ro", uri=True, timeout=3.0)
        row = con.execute(
            """SELECT push_name, full_name, first_name, business_name
               FROM whatsmeow_contacts
               WHERE their_jid LIKE ? LIMIT 1""",
            (f"{phone_digits}%",),
        ).fetchone()
        con.close()
        if not row:
            return ""
        push_name, full_name, first_name, business_name = row
        return (push_name or full_name or first_name or business_name or "").strip()
    except Exception as e:
        log.debug("[route_b] whatsapp profile-name read failed: %s", e)
        return ""


async def _get_profile_name(phone: str, source: str) -> str:
    whatsapp_db = _whatsapp_db_for_source(source)
    if not whatsapp_db:
        return ""
    import asyncio
    digits = "".join(ch for ch in phone if ch.isdigit())
    return await asyncio.to_thread(_read_whatsapp_profile_name, whatsapp_db, digits)


def _build_identity_context(identity: dict, profile_name: str, contact: Optional[dict]) -> str:
    lines = []
    role = identity.get("identity_role") or identity.get("role") or "unknown"
    lifecycle = identity.get("identity_lifecycle")
    lines.append(f"Sender system role: {role}" + (f" ({lifecycle})" if lifecycle else ""))
    if identity.get("employee_name"):
        lines.append(f"System record name: {identity['employee_name']}")
    if identity.get("designation"):
        lines.append(f"Designation: {identity['designation']}")
    if profile_name:
        lines.append(f"WhatsApp profile name: {profile_name}")
    if contact:
        if contact.get("display_name"):
            lines.append(f"Saved contact name: {contact['display_name']}")
        if contact.get("relation"):
            lines.append(f"Saved relation: {contact['relation']}")
        if contact.get("notes"):
            lines.append(f"Notes: {contact['notes']}")
    return "\n".join(lines)


_MONEY_ADVANCE_INTENT = "money_advance_request"


async def _create_money_advance_admin_draft(
    *, sender: str, source: str, text: str, reply_text: str, identity: dict,
) -> Optional[dict]:
    """Generalized admin-notification pattern (copied from
    modules.employee_conversation's shape, role restriction removed).
    Writes ONLY to fazle_draft_replies (via the shared, race-safe
    create_draft_reply helper) and fazle_payment_drafts — never to
    wbom_employees/fpe_cash_transactions/escort tables."""
    from modules.message_router import get_primary_admin

    role = identity.get("identity_role") or identity.get("role") or "unknown"
    await create_draft_reply(
        sender=sender,
        bridge=source,
        draft_text=reply_text,
        role=role,
        intent=_MONEY_ADVANCE_INTENT,
        context='{"draft_type": "route_b_money_advance"}',
        source_module="route_b",
    )

    draft_id = None
    try:
        draft_id = await fetch_val(
            """INSERT INTO fazle_payment_drafts
                   (draft_type, employee_id, employee_name, employee_mobile,
                    payout_mobile, payment_method, expected_amount, purpose,
                    status, source, draft_text, created_at, updated_at)
               VALUES ('route_b_advance_request', $1, $2, $3,
                       $3, NULL, NULL, $4,
                       'pending', $5, $6, NOW(), NOW())
               RETURNING id""",
            identity.get("employee_id"),
            identity.get("employee_name") or identity.get("display_name"),
            sender,
            f"Route B ({role}) money/advance request",
            source,
            text[:500],
        )
    except Exception as e:
        log.warning("[route_b] fazle_payment_drafts insert failed sender=%s: %s", sender, e)

    return {
        "admin_phone": get_primary_admin(),
        "bridge": source,
        "purpose": "route_b_money_advance",
        "text": (
            "⚠️ MONEY/ADVANCE REQUEST — ROUTE B\n"
            f"Sender: {sender}\n"
            f"Role: {role}\n"
            f"Draft: #{draft_id}\n\n"
            f"{text}"
        ),
    }


_UNKNOWN_SENDER_PROBE = (
    "This sender is not yet identified in our system. Before answering their "
    "question, naturally and politely ask who they are (name, and whether "
    "they're calling about a job, an existing duty/escort assignment, or "
    "something else) as part of your reply — do not use a robotic template, "
    "weave it into a normal conversational response to what they actually "
    "asked."
)


async def handle_route_b_message(
    sender: str, text: str, source: str, identity: dict, intent: str = "",
) -> tuple[str, Optional[dict]]:
    """Route B entry point — called from message_router.process_message()
    for roles in identity_brain.ROUTE_B_ROLES, and for a money/advance
    request from those same roles."""
    role = identity.get("identity_role") or identity.get("role") or "unknown"

    history = await _get_conversation_history(sender, source)
    contact = await _get_saved_contact(sender)
    profile_name = await _get_profile_name(sender, source)
    identity_ctx = _build_identity_context(identity, profile_name, contact)

    db_context_parts = [identity_ctx]
    if role == "unknown":
        db_context_parts.append(_UNKNOWN_SENDER_PROBE)

    money_request = is_advance_request(text)
    effective_intent = _MONEY_ADVANCE_INTENT if money_request else (intent or "unknown")

    db_context = "\n\n".join(p for p in db_context_parts if p)

    reply = await ollama_generate_reply(
        text, effective_intent, db_context, history=history, role=role, source=source,
    )

    admin_notification = None
    if money_request:
        admin_notification = await _create_money_advance_admin_draft(
            sender=sender, source=source, text=text, reply_text=reply, identity=identity,
        )

    return reply, admin_notification
