"""
WhatsApp Thread Reconstruction (Phase B Part 5) + shadow-mode
message_router hook (Part 8).

Stage 1 (multi_message_escort_parser_enabled=False, the current
production default) feeds the existing single-message
modules.escort.parse_escort_message() output into the memory engine
against a single most-recent open thread per identity -- proven on live
production traffic since 2026-07-19.

Stage 2 (multi_message_escort_parser_enabled=True, still off in
production pending its own checkpoint) adds: multi-thread grouping /
interleaving protection (modules.conversation_memory.grouping), the full
Part 4 merge-rule set, existing-draft awareness (read-only), and
structured shadow-comparison records for later analysis. It still never
creates a draft/program/queue row -- "complete" only ever means "shadow
candidate," never a production write.

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

REQUIRED_COMPLETENESS_FIELDS = {"mother_vessel", "lighter_vessel", "master_mobile"}


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
    from modules.escort import _is_allowed_escort_client
    from modules.bridge_poller import _is_system_generated_notification

    # Explicit, defense-in-depth authorization gate (Stage 2 Part 3) -- the
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

    settings = get_settings()
    if settings.multi_message_escort_parser_enabled:
        thread_id = await _observe_stage2(text=text, sender_phone=sender_phone, source=source, message_id=message_id, sender_role=sender_role)
    else:
        thread_id = await _observe_stage1(text=text, sender_phone=sender_phone, source=source, message_id=message_id, sender_role=sender_role)

    if message_id:
        await cm.mark_message_processed(message_id, source, thread_id)


def _order_to_fields(order: dict) -> dict:
    """Flattens modules.escort.parse_escort_message()'s EscortOrder shape
    into the field-name space this module merges on. Only the first
    lighter is used, matching Stage 1's own scope -- multi-lighter orders
    are out of scope for both stages."""
    fields = {
        "mother_vessel": order.get("mother_vessel"),
        "importer": order.get("importer"),
        "cargo_type": order.get("cargo_type"),
    }
    lighters = order.get("lighters") or []
    if lighters:
        first = lighters[0]
        fields["lighter_vessel"] = first.get("lighter_vessel")
        fields["master_mobile"] = first.get("master_mobile")
        fields["destination"] = first.get("destination")
        fields["capacity"] = first.get("capacity")
    return fields


# ===========================================================================
# Stage 1 -- single most-recent-open-thread, live in production
# ===========================================================================

async def _observe_stage1(*, text: str, sender_phone: str, source: str, message_id: Optional[str], sender_role: str) -> Optional[int]:
    import modules.conversation_memory as cm
    from modules.escort import parse_escort_message

    thread = await cm.get_open_thread(bridge_id=source, counterparty_phone=sender_phone, business_domain="escort")
    if thread is None:
        thread = await cm.create_thread(
            platform="whatsapp", bridge_id=source, counterparty_phone=sender_phone,
            business_domain="escort", sender_role_at_open=sender_role,
            business_intent="escort_order", first_message_id=message_id,
        )

    order = parse_escort_message(text)
    parsed = _order_to_fields(order)
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

    _merge("mother_vessel", parsed.get("mother_vessel"), 0.7)
    _merge("importer", parsed.get("importer"), 0.6)
    _merge("cargo_type", parsed.get("cargo_type"), 0.6)
    _merge("lighter_vessel", parsed.get("lighter_vessel"), 0.7)
    _merge("master_mobile", parsed.get("master_mobile"), 0.8)
    _merge("destination", parsed.get("destination"), 0.6)
    _merge("capacity", parsed.get("capacity"), 0.6)

    is_complete = REQUIRED_COMPLETENESS_FIELDS.issubset(fields.keys())
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

    return thread["id"]


# ===========================================================================
# Stage 2 -- multi-thread grouping, existing-draft awareness, shadow
# comparison records. Gated on multi_message_escort_parser_enabled (off in
# production until its own checkpoint).
# ===========================================================================

_DEFAULT_FIELD_CONFIDENCE = {
    "mother_vessel": 0.7, "importer": 0.6, "cargo_type": 0.6,
    "lighter_vessel": 0.7, "master_mobile": 0.8, "destination": 0.6, "capacity": 0.6,
}


async def _observe_stage2(*, text: str, sender_phone: str, source: str, message_id: Optional[str], sender_role: str) -> Optional[int]:
    import modules.conversation_memory as cm
    from modules.conversation_memory.grouping import classify_threads_for_message, check_existing_draft_relation
    from modules.escort import parse_escort_message

    settings = get_settings()
    bangla_enabled = settings.bangla_semantic_parser_enabled

    order = parse_escort_message(text)
    parsed = _order_to_fields(order)
    # "current parser result" for the shadow comparison record is always
    # the EXISTING single-message parser alone, unenhanced -- the whole
    # point of Section 9's comparison is showing what the OLD parser
    # would have produced next to what this module produces.
    current_parser_result = {k: v for k, v in parsed.items() if v}
    current_alone_complete = REQUIRED_COMPLETENESS_FIELDS.issubset(current_parser_result.keys())

    field_meta: dict = {
        f: {"value": v, "confidence": _DEFAULT_FIELD_CONFIDENCE.get(f, 0.6),
            "is_explicit_label": False, "extraction_method": "single_message_parser"}
        for f, v in parsed.items() if v
    }

    bangla_mother_vessel_candidate: Optional[dict] = None
    if bangla_enabled:
        from modules.conversation_memory.bangla_extraction import extract_labeled_fields, split_trip_suffix

        bangla_fields = extract_labeled_fields(text, has_lighter=False, has_master_mobile=False)
        bangla_mother_vessel_candidate = bangla_fields.pop("mother_vessel", None)
        # Bangla explicit labels take priority over the English parser's
        # guess for every field except mother_vessel (held back for
        # context-sensitive re-derivation once the target thread's prior
        # state is known -- Part 12).
        field_meta.update(bangla_fields)

        # Trip normalization (Section 12) is language-independent -- it
        # applies to whichever lighter_vessel this message ended up with,
        # from either parser.
        if "lighter_vessel" in field_meta:
            clean, trip = split_trip_suffix(field_meta["lighter_vessel"]["value"])
            if clean:
                field_meta["lighter_vessel"]["value"] = clean
            if trip and "trip" not in field_meta:
                field_meta["trip"] = {
                    "value": trip, "confidence": 0.8,
                    "is_explicit_label": True, "extraction_method": "trip_suffix_split",
                }

    grouping_input = {f: v["value"] for f, v in field_meta.items()}
    threads = await cm.get_open_threads(bridge_id=source, counterparty_phone=sender_phone, business_domain="escort")
    naive_stage1_target = threads[0] if threads else None  # what Stage 1's single-most-recent lookup would have picked

    decision = classify_threads_for_message(threads, grouping_input)

    thread: Optional[dict] = None
    fields: dict = {}
    conflicts: list = []

    if decision.action == "merge_existing":
        thread = decision.thread
        fields = dict(thread["fields"])
        conflicts = list(thread.get("conflict_flags") or [])
    elif decision.action == "new_thread":
        thread = await cm.create_thread(
            platform="whatsapp", bridge_id=source, counterparty_phone=sender_phone,
            business_domain="escort", sender_role_at_open=sender_role,
            business_intent="escort_order", first_message_id=message_id,
        )

    if bangla_enabled and bangla_mother_vessel_candidate is not None:
        resolved_candidate = bangla_mother_vessel_candidate
        if (
            bangla_mother_vessel_candidate.get("extraction_method") == "bangla_label_generic_ambiguous"
            and "lighter_vessel" in fields and "master_mobile" in fields
        ):
            # Context-sensitive promotion (Part 12): the thread this
            # message actually landed in already has lighter+master, so
            # the generic "ভেসেল নেম" label can only sensibly mean the
            # missing mother vessel -- re-derive with that context rather
            # than trusting the pre-grouping ambiguous guess.
            from modules.conversation_memory.bangla_extraction import extract_labeled_fields
            promoted = extract_labeled_fields(text, has_lighter=True, has_master_mobile=True)
            resolved_candidate = promoted.get("mother_vessel", bangla_mother_vessel_candidate)
        # A Bangla label was explicitly found for this field -- always
        # replaces whatever the English-only parser guessed (it has no
        # Bangla awareness and is known-unreliable on Bangla-labeled
        # text), even when the Bangla read itself is only low-confidence.
        field_meta["mother_vessel"] = resolved_candidate

    applied_any = False
    field_provenance: dict = {}

    if thread is not None:
        for field_name, meta in field_meta.items():
            value = meta.get("value")
            if not value:
                continue
            fields, result = cm.merge_field(
                fields, field_name, value=value, source_message_id=message_id,
                confidence=meta["confidence"], extraction_method=meta["extraction_method"],
                is_system_generated=False, is_explicit_label=meta.get("is_explicit_label", False),
            )
            field_provenance[field_name] = result.reason
            if result.applied:
                applied_any = True
            if result.conflict:
                conflicts.append({
                    "field": field_name, "incoming_value": value,
                    "existing_value": fields.get(field_name, {}).get("value"),
                    "source_message_id": message_id,
                })

        is_complete = REQUIRED_COMPLETENESS_FIELDS.issubset(fields.keys())
        new_state = thread["state"]
        if conflicts:
            new_state = "conflicted"
        elif is_complete and thread["state"] in ("open", "partial"):
            new_state = "partial"  # Stage 2 also never advances past partial -- shadow only

        if applied_any or new_state != thread["state"] or message_id != thread.get("last_message_id"):
            await cm.update_thread(
                thread["id"], fields=fields, state=new_state,
                last_message_id=message_id, last_system_action="shadow_observed_stage2",
                conflict_flags=conflicts or None,
            )
            log.info(
                "[shadow-v2] thread=%s state=%s complete=%s grouping=%s fields=%s (no reply/draft affected)",
                thread["id"], new_state, is_complete, decision, sorted(fields.keys()),
            )
        completion_state = new_state
    else:
        is_complete = False
        completion_state = "needs_review"

    existing_draft_relation, existing_draft_detail = (
        await check_existing_draft_relation(fields) if fields else ("no_candidate", {"reason": "no_fields_yet"})
    )

    would_create_draft = bool(is_complete and not conflicts and existing_draft_relation != "likely_duplicate")
    parser_missed_candidate = bool(is_complete and not current_alone_complete)
    parser_would_have_merged_incorrectly = bool(
        naive_stage1_target is not None
        and (
            decision.action != "merge_existing"
            or decision.thread["id"] != naive_stage1_target["id"]
        )
    )

    await cm.record_shadow_comparison(
        thread_id=thread["id"] if thread else None,
        bridge_id=source, counterparty_phone=sender_phone, business_domain="escort",
        message_id=message_id,
        current_parser_result=current_parser_result,
        conversation_aware_result=fields,
        field_provenance=field_provenance,
        completion_state=completion_state,
        grouping_action=decision.action,
        grouping_reason=decision.reason,
        conflicts=conflicts,
        would_create_draft=would_create_draft,
        reason=decision.reason,
        existing_draft_relation=existing_draft_relation,
        existing_draft_relation_detail=existing_draft_detail,
        parser_missed_candidate=parser_missed_candidate,
        parser_would_have_merged_incorrectly=parser_would_have_merged_incorrectly,
    )

    return thread["id"] if thread else None
