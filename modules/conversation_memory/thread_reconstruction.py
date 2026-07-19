"""
WhatsApp Thread Reconstruction (Phase B Part 5) + shadow-mode
message_router hook (Part 8, Stage 1 scope only).

Stage 1 deliberately does NOT include the multi-message escort parser
(Stage 2) or Bangla semantic extraction (Stage 3) -- this module feeds
the existing single-message modules.escort.parse_escort_message() output
into the new conversation memory engine, in shadow mode only, so the
infrastructure (feature flags, thread creation on real traffic, field
merge behavior) is proven on live data before Stage 2 builds real
multi-message completeness logic on top of it.

Shadow mode contract (Part 13): reconstruct and record the proposed
memory state; never change the reply, never create a draft/program/queue
row, never affect existing behavior even if this code raises -- every
call site wraps this in a broad except and logs, never propagates.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.config import get_settings

log = logging.getLogger("fazle.conversation_memory.thread_reconstruction")


async def shadow_observe_escort_client_message(
    *,
    text: str,
    sender_phone: str,
    source: str,
    message_id: Optional[str],
    sender_role: str,
) -> None:
    """Fire-and-forget shadow observation for an authorized escort-client
    message. Called from modules.message_router, upstream of the
    existing (unchanged) escort routing decision. Never raises to the
    caller -- any failure here must never affect real message routing.

    Only runs when BOTH conversation_memory_enabled and
    whatsapp_thread_reconstruction_enabled are set (both default False) --
    Part 13's explicit shadow-mode-first / feature-flag requirement.
    """
    settings = get_settings()
    if not (settings.conversation_memory_enabled and settings.whatsapp_thread_reconstruction_enabled):
        return

    try:
        await _observe(text=text, sender_phone=sender_phone, source=source, message_id=message_id, sender_role=sender_role)
    except Exception as exc:
        log.error("[shadow] conversation memory observation failed (non-fatal, no effect on routing): %s", exc)


async def _observe(*, text: str, sender_phone: str, source: str, message_id: Optional[str], sender_role: str) -> None:
    import modules.conversation_memory as cm
    from modules.escort import parse_escort_message, _is_allowed_escort_client
    from modules.bridge_poller import _is_system_generated_notification

    # Explicit, defense-in-depth authorization gate (Stage 2 §3) -- the
    # caller (message_router's `if role_str in _ESCORT_ROLES:` branch)
    # already enforces this, but this module must never assume it was
    # called correctly. Belt-and-braces: re-check here too, so a future
    # caller wiring this hook into a different branch can't silently
    # grant order-creation-candidate status to an unauthorized sender.
    if not _is_allowed_escort_client(sender_phone):
        log.debug("[shadow] sender not in ESCORT_CLIENT_PHONES, refusing to observe: %s", sender_phone)
        return

    # Inbound client messages should never carry the outbound-only
    # _AUTOMATED_SUFFIX_ANCHOR marker (a human never types it, and this
    # is the inbound path -- bridge_poller's own guard is for the
    # OUTGOING scan). Checked anyway, explicitly, rather than assumed --
    # if it ever fires, treat the message as untrustworthy input, not a
    # contributor to conversation state.
    if _is_system_generated_notification(text):
        log.warning("[shadow] inbound text unexpectedly carries a system-generated marker, refusing to observe: sender=%s", sender_phone)
        return

    if message_id and await cm.is_message_processed(message_id, source):
        log.debug("[shadow] message already processed, skipping: %s", message_id)
        return

    thread = await cm.get_open_thread(bridge_id=source, counterparty_phone=sender_phone, business_domain="escort")
    if thread is None:
        thread = await cm.create_thread(
            platform="whatsapp", bridge_id=source, counterparty_phone=sender_phone,
            business_domain="escort", sender_role_at_open=sender_role,
            business_intent="escort_order", first_message_id=message_id,
        )

    order = parse_escort_message(text)
    fields = dict(thread["fields"])
    applied_any = False
    conflicts: list = list(thread.get("conflict_flags") or [])

    def _merge(field_name: str, value, confidence: float) -> None:
        nonlocal fields, applied_any
        if not value:
            return
        fields, result = cm.merge_field(
            fields, field_name, value=value, source_message_id=message_id,
            confidence=confidence, extraction_method="single_message_parser",
            is_system_generated=False,
        )
        if result.applied:
            applied_any = True
        if result.conflict:
            conflicts.append({
                "field": field_name, "incoming_value": value,
                "existing_value": fields.get(field_name, {}).get("value"),
                "source_message_id": message_id,
            })

    _merge("mother_vessel", order.get("mother_vessel"), 0.7)
    _merge("importer", order.get("importer"), 0.6)
    _merge("cargo_type", order.get("cargo_type"), 0.6)
    lighters = order.get("lighters") or []
    if lighters:
        first = lighters[0]
        _merge("lighter_vessel", first.get("lighter_vessel"), 0.7)
        _merge("master_mobile", first.get("master_mobile"), 0.8)
        _merge("destination", first.get("destination"), 0.6)
        _merge("capacity", first.get("capacity"), 0.6)

    required = {"mother_vessel", "lighter_vessel", "master_mobile"}
    is_complete = required.issubset(fields.keys())
    new_state = thread["state"]
    if conflicts:
        new_state = "conflicted"
    elif is_complete and thread["state"] == "open":
        new_state = "partial"  # Stage 1 never advances to draft_created -- shadow only

    if applied_any or new_state != thread["state"] or message_id != thread.get("last_message_id"):
        await cm.update_thread(
            thread["id"], fields=fields, state=new_state,
            last_message_id=message_id, last_system_action="shadow_observed",
            conflict_flags=conflicts or None,
        )
        log.info(
            "[shadow] thread=%s state=%s complete=%s fields=%s (no reply/draft affected)",
            thread["id"], new_state, is_complete, sorted(fields.keys()),
        )

    if message_id:
        await cm.mark_message_processed(message_id, source, thread["id"])
