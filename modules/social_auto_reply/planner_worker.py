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
            await execute("UPDATE social_inbox_events SET reply_status='ignored' WHERE id = ANY($1::bigint[])", plan.event_ids)
            skipped += 1
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


async def _draft_instead_of_send(
    *, platform: str, target_id: str, reply_text: str, intent: str, sender_role: str,
    ai_polished: bool = False,
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
    """
    context = (
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
        title = (
            "🤖 AI-Polished Reply Pending Approval"
            if ai_polished else
            "📋 Social Auto-Reply Draft Pending Approval"
        )
        await enqueue_outbound(
            admin_phone,
            (
                f"{title}\n"
                f"Platform: {platform}\n"
                f"Sender: {target_id}\n"
                f"Intent: {intent}\n"
                f"Draft ID: {draft_id}\n\n"
                f"{preview}\n\n"
                f"Reply APPROVE {draft_id} to send, REJECT {draft_id} to discard."
            ),
            source_bridge="bridge1",
            fallback_channel="bridge2",
            purpose="social-draft-pending",
            idempotency_key=f"social-draft-notify:{draft_id}",
            meta={"draft_id": draft_id, "platform": platform, "target_id": target_id, "ai_polished": ai_polished},
        )
    return draft_id


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
