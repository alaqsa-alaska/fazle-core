"""Build safe queue items from pending social inbox events."""
from __future__ import annotations

import logging
import os

from app.config import get_settings
from app.database import execute, fetch_val
from modules.outbound import enqueue as enqueue_outbound
from shared.draft_reply import create_draft_reply

from .classifier import classify_comment, classify_message
from .conversation_history import pending_events, pending_threads
from .employee_lookup import find_by_mobile
from .intelligent_generator import plan_reply
from .risk_flagger import is_recruiting_intent
from .send_queue import enqueue_reply

_COOLDOWN_MINUTES = int(__import__("os").getenv("SOCIAL_REPLY_COOLDOWN_M", "15"))

log = logging.getLogger("fazle.social.planner")


async def process_due_threads(limit: int = 10) -> dict:
    compression_seconds = int(os.getenv("SOCIAL_REPLY_COMPRESSION_S", "25"))
    planned = flagged = skipped = 0
    for thread in await pending_threads(compression_seconds, limit=limit):
        platform = thread["platform"]
        target_id = thread["target_id"]
        events = await pending_events(platform, target_id)
        if not events:
            skipped += 1
            continue
        event_ids = [int(e["id"]) for e in events]
        # PATCH 3: employee role detection before intent gate
        employee = await find_by_mobile(target_id)
        sender_role = "EMPLOYEE" if employee else "UNKNOWN"
        classifications = [_classify_event(e).intent for e in events]
        await _refresh_classifications(events, classifications)
        if not all(is_recruiting_intent(intent) for intent in classifications):
            await execute("UPDATE social_inbox_events SET reply_status='ignored' WHERE id = ANY($1::bigint[])", event_ids)
            skipped += 1
            continue
        try:
            plan = await plan_reply(platform, target_id, events)
        except Exception:
            await execute("UPDATE social_inbox_events SET reply_status='pending' WHERE id = ANY($1::bigint[])", event_ids)
            raise
        if not plan:
            await execute("UPDATE social_inbox_events SET reply_status='ignored' WHERE id = ANY($1::bigint[])", event_ids)
            skipped += 1
            continue
        if not plan.auto_send:
            # 2026-08-04 (Owner decision): never silently drop a message
            # Hermes couldn't confidently answer (risk-flagged, or no safe
            # rule-based reply matched any classified intent) — previously
            # this branch marked the event 'ignored' with zero admin
            # visibility. Now flags it (existing social_flagged_items
            # mechanism, previously written but never actually called) and
            # sends the Admin a decision request with full context.
            await _flag_events(plan.event_ids, platform, target_id, plan.flag_reason or "unspecified", events)
            await _notify_admin_flagged(
                platform=platform, target_id=target_id, events=events,
                reason=plan.flag_reason or "unspecified", sender_role=sender_role,
            )
            flagged += 1
            continue
        # PATCH 2: per-sender intent cooldown — skip duplicate within cooldown window
        recent_dupe = await fetch_val(
            """
            SELECT id FROM social_reply_queue
            WHERE target_id = $1
              AND intent = $2
              AND created_at >= NOW() - ($3 * INTERVAL '1 minute')
            LIMIT 1
            """,
            target_id,
            plan.intent,
            _COOLDOWN_MINUTES,
        )
        if recent_dupe:
            log.info("SKIPPED_DUPLICATE sender=%s intent=%s cooldown=%dm", target_id, plan.intent, _COOLDOWN_MINUTES)
            await execute("UPDATE social_inbox_events SET reply_status='ignored' WHERE id = ANY($1::bigint[])", plan.event_ids)
            skipped += 1
            continue
        # Selective draft policy (2026-07-15, Owner-confirmed -- see
        # docs/TASK_BRIEF_chat_sync_and_reply_gap_2026-07-15.md Phase 4): only
        # senders matching the existing DRAFT_ALWAYS_ROLES/PHONES/NAMES config
        # (modules.bridge_poller._is_draft_always(), the same mechanism the
        # legacy bridge_poller path already uses) get routed to an admin-
        # approval draft. Everyone else gets a real auto-send, matching
        # pre-2026-06-23 behavior, once the underlying send-worker bug is fixed.
        # 2026-08-03 (Owner decision, Phase 4): AI-polished replies
        # (plan.needs_review) are routed here too, reusing this exact same
        # existing draft/admin-approval mechanism — no new queue, no new
        # UI, no new WhatsApp command. Admin already has APPROVE/REJECT.
        if await _is_draft_always_sender(target_id, events[-1]) or plan.needs_review:
            draft_id = await _draft_instead_of_send(
                platform=platform,
                target_id=target_id,
                reply_text=plan.reply_text,
                intent=plan.intent,
                sender_role=sender_role,
                ai_polished=plan.needs_review,
                validation_reason=plan.validation_reason,
                rejected_ai_reply=plan.rejected_ai_reply,
                provider_attempts=plan.provider_attempts,
                events=events,
            )
            if draft_id:
                await execute("UPDATE social_inbox_events SET reply_status='drafted' WHERE id = ANY($1::bigint[])", plan.event_ids)
                planned += 1
            continue
        queue_id = await enqueue_reply(
            event_id=plan.event_ids[-1],
            platform=platform,
            target_id=target_id,
            reply_text=plan.reply_text,
            intent=plan.intent,
            conversation_id=events[-1].get("conversation_id"),
            reply_to_comment_id=plan.reply_to_comment_id,
            source_bridge=None,
            meta={"combined_event_ids": plan.event_ids, "planner": "context_v1", "sender_role": sender_role},
        )
        if queue_id:
            await execute("UPDATE social_inbox_events SET reply_status='queued' WHERE id = ANY($1::bigint[])", plan.event_ids)
            planned += 1
    return {"planned": planned, "flagged": flagged, "skipped": skipped}


async def _is_draft_always_sender(target_id: str, latest_event: dict) -> bool:
    """
    Reuses modules.bridge_poller._is_draft_always() (phone/role/name-prefix
    match against DRAFT_ALWAYS_PHONES/ROLES/NAMES) rather than re-implementing
    the match rules -- same identity source (modules.identity_brain.detect_identity)
    the legacy bridge_poller path already uses for this exact check.

    target_id is not always a phone number (e.g. Facebook comment/PSID
    identifiers) -- detect_identity() safely falls back to role="unknown" for
    anything that doesn't normalize as a phone, which never matches
    DRAFT_ALWAYS_ROLES, so non-WhatsApp senders correctly fall through to the
    real-send path unless separately matched by name.
    """
    from modules.bridge_poller import _is_draft_always
    from modules.identity_brain import detect_identity

    text = str(latest_event.get("message_text") or "")
    identity = await detect_identity(target_id, text)
    role = identity.get("role") or "unknown"
    display_name = (identity.get("display_name") or "").strip()
    return await _is_draft_always(target_id, role, display_name)


def _primary_admin_number() -> str:
    settings = get_settings()
    admins = settings.admin_number_list
    return admins[0] if admins else settings.admin_meta_number


async def _sender_display_name(target_id: str, latest_event: dict) -> str:
    """Best-effort sender display name via the same identity_brain lookup
    _is_draft_always_sender already uses — never raises, "Unknown" on any
    failure (identity resolution must never block a notification)."""
    try:
        from modules.identity_brain import detect_identity
        text = str(latest_event.get("message_text") or "")
        identity = await detect_identity(target_id, text)
        return (identity.get("display_name") or "").strip() or "Unknown"
    except Exception:
        return "Unknown"


async def _draft_instead_of_send(
    *, platform: str, target_id: str, reply_text: str, intent: str, sender_role: str,
    ai_polished: bool = False, validation_reason: str | None = None,
    rejected_ai_reply: str | None = None, provider_attempts: list[dict] | None = None,
    events: list[dict] | None = None,
) -> int | None:
    """Create a fazle_draft_replies row instead of enqueueing a real send,
    and notify the admin that a draft is waiting. Reuses the same shared
    draft mechanism modules/bridge_poller._save_draft() uses, so this is a
    routing change only -- no new draft/notification infrastructure.

    ai_polished=True (2026-08-03, Owner decision, Phase 4): this draft came
    from the AI-polish step (identity/persona/KB-enriched text), not from
    a DRAFT_ALWAYS_* sender match — labelled distinctly in the admin
    notification so reviewers know why this one needs review (AI-generated
    text, not a policy-based routing).

    validation_reason (2026-08-04, Owner decision): set when this draft
    exists because _safe_polish() rejected the AI-polished text -- the
    reply_text being drafted here is the SAFE rule-based fallback (never
    the rejected AI text), but the Admin still needs to see *why* AI-polish
    was rejected, plus full sender/message context, per the same "never a
    silent failure" decision that added _notify_admin_flagged() below.

    rejected_ai_reply / provider_attempts (2026-08-05, Owner decision,
    provider-chain change): the actual rejected AI text, and which
    providers were tried and their outcome -- so a reviewer can judge the
    rejection itself, not just see that one happened.
    """
    context = (
        f"social_auto_reply planner — AI-polish validation failed, safe fallback used — "
        f"platform={platform} sender_role={sender_role} reason={validation_reason}"
        if validation_reason else
        f"social_auto_reply planner — AI-polished reply pending review — "
        f"platform={platform} sender_role={sender_role}"
        if ai_polished else
        f"social_auto_reply planner — platform={platform} sender_role={sender_role}"
    )
    draft_id = await create_draft_reply(
        sender=target_id,
        bridge=platform,
        draft_text=reply_text,
        role="social_lead",
        intent=intent,
        context=context,
        source_module="social_auto_reply.planner_worker",
    )
    if not draft_id:
        return None
    admin_phone = _primary_admin_number()
    if admin_phone:
        preview = reply_text if len(reply_text) <= 300 else reply_text[:300] + "…"
        if validation_reason:
            title = "⚠️ AI-Polish Rejected — Safe Fallback Drafted"
        elif ai_polished:
            title = "🤖 AI-Polished Reply Pending Approval"
        else:
            title = "📋 Social Auto-Reply Draft Pending Approval"
        latest = events[-1] if events else {}
        extra_lines = ""
        if validation_reason:
            sender_name = await _sender_display_name(target_id, latest)
            original_message = str(latest.get("message_text") or "")[:500]
            ai_reply_line = (
                f"Generated AI Reply: {rejected_ai_reply[:500]}\n" if rejected_ai_reply else ""
            )
            providers_line = ""
            if provider_attempts:
                providers_summary = "; ".join(
                    f"{a.get('provider')}({a.get('model')}): {a.get('outcome')}" for a in provider_attempts
                )
                providers_line = f"Providers Attempted: {providers_summary}\n"
            extra_lines = (
                f"Message ID: {latest.get('id')}\n"
                f"Sender Name: {sender_name}\n"
                f"Sender Mobile: {target_id}\n"
                f"Original Message: {original_message}\n"
                f"{ai_reply_line}"
                f"{providers_line}"
                f"Reason: {validation_reason}\n"
                f"Recommended Action: review the generated reply (if any) and either "
                f"APPROVE the safe fallback below, EDIT it, or REJECT and reply manually.\n"
            )
        await enqueue_outbound(
            admin_phone,
            (
                f"{title}\n"
                f"Platform: {platform}\n"
                f"Sender: {target_id}\n"
                f"Intent: {intent}\n"
                f"Draft ID: {draft_id}\n"
                f"{extra_lines}\n"
                f"{preview}\n\n"
                f"Reply APPROVE {draft_id} to send, REJECT {draft_id} to discard."
            ),
            source_bridge="bridge1",
            fallback_channel="bridge2",
            purpose="social-draft-pending",
            idempotency_key=f"social-draft-notify:{draft_id}",
            meta={
                "draft_id": draft_id, "platform": platform, "target_id": target_id,
                "ai_polished": ai_polished, "validation_reason": validation_reason,
                "provider_attempts": provider_attempts,
            },
        )
    return draft_id


async def _notify_admin_flagged(
    *, platform: str, target_id: str, events: list[dict], reason: str, sender_role: str,
) -> None:
    """Admin decision request for a message Hermes couldn't safely handle
    (risk-flagged, or no rule-based reply matched any classified intent) —
    2026-08-04 Owner decision: this must never be a silent drop. Includes
    Message ID, Sender Name, Sender Mobile, Original Message, and the exact
    reason, matching the same rich-context requirement as the AI-polish
    rejection path in _draft_instead_of_send above."""
    admin_phone = _primary_admin_number()
    if not admin_phone:
        return
    latest = events[-1] if events else {}
    sender_name = await _sender_display_name(target_id, latest)
    original_message = str(latest.get("message_text") or "")[:500]
    message_id = latest.get("id")
    await enqueue_outbound(
        admin_phone,
        (
            "⚠️ Hermes Needs a Decision\n"
            f"Platform: {platform}\n"
            f"Message ID: {message_id}\n"
            f"Sender Name: {sender_name}\n"
            f"Sender Mobile: {target_id}\n"
            f"Sender Role: {sender_role}\n"
            f"Original Message: {original_message}\n"
            f"Reason: {reason}\n\n"
            "No automated reply was sent. Reply with REMEMBER: <instruction> to "
            "record how to handle this going forward, or "
            f"SEND {target_id} | <message> to reply directly."
        ),
        source_bridge="bridge1",
        fallback_channel="bridge2",
        purpose="social-auto-reply-needs-decision",
        idempotency_key=f"social-flag-notify:{message_id}",
        meta={"platform": platform, "target_id": target_id, "reason": reason, "message_id": message_id},
    )


async def _flag_events(event_ids: list[int], platform: str, target_id: str, reason: str, events: list[dict]) -> None:
    text = "\n".join(str(e.get("message_text") or "") for e in events)
    await execute("UPDATE social_inbox_events SET reply_status='flagged' WHERE id = ANY($1::bigint[])", event_ids)
    await execute(
        """
        INSERT INTO social_flagged_items (event_id, platform, target_id, reason, message_text, details)
        VALUES ($1,$2,$3,$4,$5,jsonb_build_object('combined_event_ids', $6::bigint[]))
        """,
        event_ids[-1],
        platform,
        target_id,
        reason,
        text,
        event_ids,
    )


def _classify_event(event: dict):
    text = str(event.get("message_text") or "")
    if event.get("platform") == "facebook_comment":
        return classify_comment(text)
    return classify_message(text, media_flag=bool(event.get("media_flag")), platform=str(event.get("platform") or ""))


async def _refresh_classifications(events: list[dict], classifications: list[str]) -> None:
    for event, intent in zip(events, classifications):
        if event.get("classification") != intent:
            await execute("UPDATE social_inbox_events SET classification=$2 WHERE id=$1", int(event["id"]), intent)
