"""Gradual backlog scanner for Meta conversations and comments."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings
from app.database import execute

from . import ingest_social_event

log = logging.getLogger("fazle.social.backlog")


async def _get_page_scoped_token(client: httpx.AsyncClient, settings) -> str | None:
    """Derive a genuine Page-scoped access token from the configured
    System User token (Owner directive, 2026-08-27, backlog-scanner #190
    fix). Root cause, confirmed live and read-only, not guessed from the
    error string: FB_PAGE_ACCESS_TOKEN is a System User token (debug_token
    reports type=SYSTEM_USER) -- it works for every other Meta call this
    app makes (Send API POST /me/messages, comment replies POST
    /{comment_id}/comments, basic GET /{page-id} lookups), but Meta's
    legacy Page Inbox/Conversations API (GET /{page-id}/conversations)
    and the Posts read edge (GET /{page-id}/posts) specifically reject a
    System User token with (#190) "This method must be called with a Page
    Access Token", even though it carries the identical permission scopes
    a real Page token would. A token derived via this exact
    GET /{page-id}?fields=access_token exchange (a token exchange Meta's
    own API supports for a System User with page-asset access) resolved
    both edges immediately in live verification -- confirmed via
    conv_resp.status_code == 200 and post_resp.status_code == 200 with
    the SAME query shape this function already used, only the token
    swapped. No other call site in this codebase is affected; this
    derivation is local to the backlog scanner only. Returns None (the
    caller skips the scan for this run, not crashes) on any failure.
    Never logs the derived token value."""
    try:
        resp = await client.get(
            f"{settings.meta_api_url}/{settings.fb_page_id}",
            headers={"Authorization": f"Bearer {settings.fb_page_access_token}"},
            params={"fields": "access_token"},
        )
        if resp.status_code != 200:
            log.warning(f"[social.backlog] page-token derivation failed: HTTP {resp.status_code}")
            return None
        token = resp.json().get("access_token")
        if not token:
            log.warning("[social.backlog] page-token derivation returned no access_token field")
            return None
        return token
    except Exception as exc:
        log.warning(f"[social.backlog] page-token derivation error: {exc}")
        return None


async def scan_recent(limit: int = 10) -> dict:
    settings = get_settings()
    if not settings.fb_page_access_token or not settings.fb_page_id:
        return {"status": "skipped", "reason": "facebook credentials missing"}
    since = datetime.now(timezone.utc) - timedelta(days=7)
    conversations = comments = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        page_token = await _get_page_scoped_token(client, settings)
        if not page_token:
            return {"status": "skipped", "reason": "page-token derivation failed"}
        conv_resp = await client.get(
            f"{settings.meta_api_url}/{settings.fb_page_id}/conversations",
            headers={"Authorization": f"Bearer {page_token}"},
            params={
                "limit": limit,
                "fields": "id,updated_time,participants{id,name},messages.limit(3){id,from{id,name},message,created_time,attachments}",
            },
        )
        if conv_resp.status_code == 200:
            for conv in conv_resp.json().get("data", []):
                for msg in conv.get("messages", {}).get("data", []):
                    created = _parse_meta_time(msg.get("created_time"))
                    if created and created < since:
                        continue
                    sender = msg.get("from") or {}
                    if sender.get("id") == settings.fb_page_id:
                        continue
                    await ingest_social_event(
                        platform="messenger",
                        event_type="message",
                        sender_id=sender.get("id") or "",
                        sender_name=sender.get("name") or "",
                        text=msg.get("message") or "",
                        conversation_id=conv.get("id"),
                        message_id=msg.get("id"),
                        media_flag=bool((msg.get("attachments") or {}).get("data")),
                        raw_payload=msg,
                    )
                    conversations += 1
        post_resp = await client.get(
            f"{settings.meta_api_url}/{settings.fb_page_id}/posts",
            headers={"Authorization": f"Bearer {page_token}"},
            params={
                "limit": 5,
                "fields": "id,created_time,comments.limit(10){id,from{id,name},message,created_time,comment_count,comments.limit(5){from{id,name},message,created_time}}",
            },
        )
        if post_resp.status_code == 200:
            for post in post_resp.json().get("data", []):
                for comment in post.get("comments", {}).get("data", []):
                    replies = comment.get("comments", {}).get("data", []) or []
                    if any((r.get("from") or {}).get("name", "").startswith("Al Aqsa") for r in replies):
                        continue
                    created = _parse_meta_time(comment.get("created_time"))
                    if created and created < since:
                        continue
                    sender = comment.get("from") or {}
                    await ingest_social_event(
                        platform="facebook_comment",
                        event_type="comment",
                        sender_id=sender.get("id") or "",
                        sender_name=sender.get("name") or "",
                        text=comment.get("message") or "",
                        conversation_id=post.get("id"),
                        comment_id=comment.get("id"),
                        media_flag=False,
                        raw_payload=comment,
                    )
                    comments += 1
    await execute(
        """
        INSERT INTO social_backlog_state (state_key, platform, last_checked_at, metadata)
        VALUES ('recent_scan', 'facebook', NOW(), jsonb_build_object('conversations', $1::int, 'comments', $2::int))
        ON CONFLICT (state_key) DO UPDATE SET last_checked_at=NOW(), metadata=EXCLUDED.metadata
        """,
        conversations,
        comments,
    )
    return {"status": "ok", "conversation_events": conversations, "comment_events": comments}


def _parse_meta_time(value: str | None):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
