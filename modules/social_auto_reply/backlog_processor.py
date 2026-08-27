"""Gradual backlog scanner for Meta conversations and comments."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import get_settings
from app.database import execute

from . import ingest_social_event

log = logging.getLogger("fazle.social.backlog")


async def scan_recent(limit: int = 10) -> dict:
    settings = get_settings()
    if not settings.fb_page_access_token or not settings.fb_page_id:
        return {"status": "skipped", "reason": "facebook credentials missing"}
    since = datetime.now(timezone.utc) - timedelta(days=7)
    conversations = comments = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        conv_resp = await client.get(
            f"{settings.meta_api_url}/{settings.fb_page_id}/conversations",
            headers={"Authorization": f"Bearer {settings.fb_page_access_token}"},
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
            headers={"Authorization": f"Bearer {settings.fb_page_access_token}"},
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
