"""Social reply queue enqueue and send implementation."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from typing import Any

import httpx

from app.config import get_settings
from app.database import execute, fetch_one, fetch_val, get_pool
from modules.identity_role_authorization import QueuedAuthorizationSnapshot
from modules.identity_role_authorization_adapter import authorize
from modules.outbound_authorization import (
    AuthorizationSnapshotError,
    capture_authorization_snapshot,
    issue_transport_authorization,
    is_transport_authorized,
    load_current_authorization_evidence,
)

from .message_deduplicator import reply_key

log = logging.getLogger("fazle.social.queue")


def _outbound_enabled() -> bool:
    return os.getenv("OUTBOUND_ENABLED", "false").lower() in {"1", "true", "yes"}


async def _capture_social_authorization(*, platform: str, target_id: str, intent: str) -> dict:
    """Capture the existing identity-role snapshot before a Social queue insert."""
    settings = get_settings()
    if not _outbound_enabled() or not settings.auto_reply_enabled:
        raise PermissionError("global outbound gate is disabled")
    domain = "recruitment"
    conversation_key = f"{platform}:{target_id}"
    row = {
        "recipient": target_id,
        "source_bridge": platform,
        "purpose": f"customer-reply:{domain}",
        "meta_json": "{}",
    }
    pool = get_pool()
    async with pool.acquire() as conn:
        evidence = await load_current_authorization_evidence(
            conn,
            row,
            channel_identifier=platform,
            conversation_key=conversation_key,
            domain=domain,
            scope_type="global",
            global_outbound_enabled=True,
            purpose_authorized=False,
        )
        snapshot = capture_authorization_snapshot(evidence)
    return {
        "present": True,
        "identity_id": snapshot.identity_id,
        "authorization_version": snapshot.authorization_version,
        "conversation_key": snapshot.conversation_key,
        "domain": snapshot.domain,
        "scope_type": snapshot.scope_type,
        "scope_key": snapshot.scope_key,
        "role_names": list(snapshot.role_names),
        "intent": intent,
    }


async def _authorize_social_row(conn, row: dict):
    """Re-read current identity/role/hold state immediately before transport."""
    meta = row.get("meta") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    context = meta.get("authorization_context") if isinstance(meta, dict) else None
    if not isinstance(context, dict) or not context.get("present"):
        raise PermissionError("social authorization context missing")
    try:
        snapshot = QueuedAuthorizationSnapshot(
            present=True,
            identity_id=str(context["identity_id"]),
            authorization_version=int(context["authorization_version"]),
            role_names=frozenset(str(value) for value in context["role_names"]),
            domain=str(context["domain"]),
            conversation_key=str(context["conversation_key"]),
            scope_type=str(context.get("scope_type") or "global"),
            scope_key=context.get("scope_key"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PermissionError("social authorization context invalid") from exc
    platform = str(row["platform"])
    evidence_row = dict(row)
    evidence_row.update({
        "recipient": row["target_id"],
        "source_bridge": platform,
        "purpose": f"customer-reply:{snapshot.domain}",
        "meta_json": "{}",
    })
    settings = get_settings()
    evidence = await load_current_authorization_evidence(
        conn,
        evidence_row,
        channel_identifier=platform,
        conversation_key=snapshot.conversation_key,
        domain=snapshot.domain,
        scope_type=snapshot.scope_type,
        scope_key=snapshot.scope_key,
        global_outbound_enabled=_outbound_enabled() and settings.auto_reply_enabled,
        purpose_authorized=False,
    )
    decision = authorize(replace(evidence, queue=snapshot))
    if not decision.allowed:
        raise PermissionError(f"authorization denied: {decision.reason_code}")
    return issue_transport_authorization(decision)


async def enqueue_reply(
    *,
    event_id: int,
    platform: str,
    target_id: str,
    reply_text: str,
    intent: str,
    conversation_id: str | None = None,
    reply_to_comment_id: str | None = None,
    source_bridge: str | None = None,
    meta: dict[str, Any] | None = None,
) -> int | None:
    key = reply_key(platform=platform, target_id=target_id, event_id=event_id, reply_text=reply_text)
    try:
        authorization_context = await _capture_social_authorization(
            platform=platform, target_id=target_id, intent=intent,
        )
    except (AuthorizationSnapshotError, PermissionError) as exc:
        await execute(
            "UPDATE social_inbox_events SET reply_status='blocked' WHERE id=$1",
            event_id,
        )
        log.info("[SOCIAL_AUTH_DENIED] platform=%s event_id=%s reason=%s", platform, event_id, type(exc).__name__)
        return None
    meta = {**(meta or {}), "authorization_context": authorization_context}
    row = await fetch_one(
        """
        INSERT INTO social_reply_queue
            (event_id, platform, target_id, conversation_id, reply_to_comment_id, source_bridge,
             reply_text, intent, status, idempotency_key, meta)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending',$9,$10::jsonb)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING id
        """,
        event_id,
        platform,
        target_id,
        conversation_id,
        reply_to_comment_id,
        source_bridge,
        reply_text,
        intent,
        key,
        json.dumps(meta or {}),
    )
    if row:
        await execute("UPDATE social_inbox_events SET reply_status='queued' WHERE id=$1", event_id)
        return int(row["id"])
    return None


async def sweep_once(limit: int = 5) -> dict:
    settings = get_settings()
    from .state_tracker import is_paused
    if await is_paused() or not settings.auto_reply_enabled or not _outbound_enabled():
        return {"picked": 0, "sent": 0, "failed": 0, "blocked": 0}
    pool = get_pool()
    sent = failed = blocked = picked = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH picked AS (
                SELECT id
                FROM social_reply_queue
                WHERE status IN ('pending','failed')
                  AND next_retry_at <= NOW()
                ORDER BY next_retry_at, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT $1
            )
            UPDATE social_reply_queue q
            SET status='sending'
            FROM picked
            WHERE q.id=picked.id
            RETURNING q.*
            """,
            limit,
        )
        picked = len(rows)
        for row in rows:
            qid = row["id"]
            attempts = int(row["attempts"] or 0) + 1
            event_ids = _event_ids(row)
            try:
                authorization = await _authorize_social_row(conn, row)
                external_id = await _send(row, authorization=authorization)
                await conn.execute(
                    """
                    UPDATE social_reply_queue
                    SET status='sent', attempts=$2, sent_at=NOW(), last_error=NULL
                    WHERE id=$1
                    """,
                    qid,
                    attempts,
                )
                await conn.execute(
                    """
                    INSERT INTO social_sent_log
                        (queue_id, event_id, platform, target_id, external_id, reply_text, idempotency_key)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    qid,
                    row["event_id"],
                    row["platform"],
                    row["target_id"],
                    external_id,
                    row["reply_text"],
                    row["idempotency_key"],
                )
                await conn.execute("UPDATE social_inbox_events SET reply_status='sent' WHERE id = ANY($1::bigint[])", event_ids)
                sent += 1
            except PermissionError as exc:
                err = str(exc)[:1000]
                await conn.execute(
                    "UPDATE social_reply_queue SET status='blocked', attempts=$2, last_error=$3 WHERE id=$1",
                    qid,
                    attempts,
                    err,
                )
                await conn.execute("UPDATE social_inbox_events SET reply_status='blocked' WHERE id = ANY($1::bigint[])", event_ids)
                blocked += 1
            except Exception as exc:
                err = str(exc)[:1000]
                if attempts >= int(row["max_attempts"] or 5):
                    await conn.execute(
                        "UPDATE social_reply_queue SET status='dlq', attempts=$2, last_error=$3 WHERE id=$1",
                        qid,
                        attempts,
                        err,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE social_reply_queue
                        SET status='failed', attempts=$2, last_error=$3,
                            next_retry_at = NOW() + (INTERVAL '1 minute' * (2 ^ $2))
                        WHERE id=$1
                        """,
                        qid,
                        attempts,
                        err,
                    )
                    await conn.execute(
                        """
                        INSERT INTO social_retry_queue (queue_id, attempts, next_retry_at, last_error, status)
                        VALUES ($1,$2,NOW() + (INTERVAL '1 minute' * (2 ^ $2)),$3,'pending')
                        ON CONFLICT (queue_id) DO UPDATE SET
                            attempts=EXCLUDED.attempts,
                            next_retry_at=EXCLUDED.next_retry_at,
                            last_error=EXCLUDED.last_error,
                            status='pending',
                            updated_at=NOW()
                        """,
                        qid,
                        attempts,
                        err,
                    )
                failed += 1
    return {"picked": picked, "sent": sent, "failed": failed, "blocked": blocked}


def _event_ids(row: dict) -> list[int]:
    meta = row.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    ids = meta.get("combined_event_ids") if isinstance(meta, dict) else None
    if isinstance(ids, list) and ids:
        return [int(event_id) for event_id in ids]
    return [int(row["event_id"])]


async def _send(row: dict, *, authorization=None) -> str:
    if not is_transport_authorized(authorization):
        raise PermissionError("transport authorization required")
    platform = row["platform"]
    if platform == "messenger":
        return await _send_messenger(row["target_id"], row["reply_text"], authorization=authorization)
    if platform == "facebook_comment":
        return await _send_comment(row["reply_to_comment_id"] or row["target_id"], row["reply_text"], authorization=authorization)
    if platform == "meta_whatsapp":
        return await _send_meta_whatsapp(row["target_id"], row["reply_text"], authorization=authorization)
    if platform in ("bridge1", "bridge2"):
        return await _send_bridge(platform, row["target_id"], row["reply_text"], authorization=authorization)
    raise ValueError(f"unsupported platform: {platform}")


async def _send_bridge(platform: str, recipient_id: str, text: str, *, authorization=None) -> str:
    if not is_transport_authorized(authorization):
        raise PermissionError("transport authorization required")
    from app.bridge import get_bridge1, get_bridge2
    bridge = get_bridge1() if platform == "bridge1" else get_bridge2()
    await bridge.send_strict(recipient_id, text, authorization=authorization)
    return f"{platform}:{recipient_id}"


async def _send_messenger(recipient_id: str, text: str, *, authorization=None) -> str:
    if not is_transport_authorized(authorization):
        raise PermissionError("transport authorization required")
    settings = get_settings()
    if not settings.fb_page_access_token:
        raise PermissionError("fb_page_access_token missing")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.meta_api_url}/me/messages",
            headers={"Authorization": f"Bearer {settings.fb_page_access_token}"},
            json={"recipient": {"id": recipient_id}, "message": {"text": text}, "messaging_type": "RESPONSE"},
        )
    if resp.status_code != 200:
        body = resp.text[:500]
        if resp.status_code in {400, 403} and "pages_messaging" in body:
            raise PermissionError(body)
        raise RuntimeError(f"messenger send failed {resp.status_code}: {body}")
    return str(resp.json().get("message_id") or "")


async def _send_instagram(recipient_id: str, text: str, *, authorization=None) -> str:
    if not is_transport_authorized(authorization):
        raise PermissionError("transport authorization required")
    """Send an Instagram DM reply via the Page-linked Instagram Messaging
    API (Owner directive, 2026-08-27). Uses the SAME /me/messages Send API
    endpoint and Page access token as _send_messenger() above -- for a
    Facebook-Login-linked Instagram Professional Account (the setup this
    integration targets: an existing Page with Instagram connected in
    Meta's dashboard, not the separate Instagram-Login product), Meta
    routes /me/messages to the correct platform automatically based on
    which conversation the recipient_id (an Instagram-scoped ID, IGSID)
    belongs to -- no separate endpoint or token needed. Not yet live-
    verified end-to-end (Instagram account not yet connected); flagged
    for a real-send verification once it is."""
    settings = get_settings()
    if not settings.fb_page_access_token:
        raise PermissionError("fb_page_access_token missing")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.meta_api_url}/me/messages",
            headers={"Authorization": f"Bearer {settings.fb_page_access_token}"},
            json={"recipient": {"id": recipient_id}, "message": {"text": text}, "messaging_type": "RESPONSE"},
        )
    if resp.status_code != 200:
        body = resp.text[:500]
        if resp.status_code in {400, 403} and ("instagram_manage_messages" in body or "pages_messaging" in body):
            raise PermissionError(body)
        raise RuntimeError(f"instagram send failed {resp.status_code}: {body}")
    return str(resp.json().get("message_id") or "")


async def _send_comment(comment_id: str, text: str, *, authorization=None) -> str:
    if not is_transport_authorized(authorization):
        raise PermissionError("transport authorization required")
    settings = get_settings()
    if not settings.fb_page_access_token:
        raise PermissionError("fb_page_access_token missing")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.meta_api_url}/{comment_id}/comments",
            headers={"Authorization": f"Bearer {settings.fb_page_access_token}"},
            json={"message": text},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"comment reply failed {resp.status_code}: {resp.text[:500]}")
    return str(resp.json().get("id") or "")


async def _send_meta_whatsapp(to: str, text: str, *, authorization=None) -> str:
    if not is_transport_authorized(authorization):
        raise PermissionError("transport authorization required")
    settings = get_settings()
    if not settings.meta_api_token or not settings.meta_phone_number_id:
        raise PermissionError("meta whatsapp credentials missing")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.meta_api_url}/{settings.meta_phone_number_id}/messages",
            headers={"Authorization": f"Bearer {settings.meta_api_token}"},
            json={"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text", "text": {"body": text}},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"meta whatsapp send failed {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    messages = data.get("messages") or []
    return str((messages[0] if messages else {}).get("id") or "")


