"""Internal admin API for social auto-reply."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.database import execute
from shared.auth_deps import require_api_key as _require_api_key

from . import send_queue, state_tracker

router = APIRouter(prefix="/api/social", tags=["social-auto-reply"])


@router.get("/status", dependencies=[Depends(_require_api_key)])
async def social_status():
    return await state_tracker.status()


@router.get("/queue", dependencies=[Depends(_require_api_key)])
async def social_queue(limit: int = 50):
    return {"items": await state_tracker.queue_rows(limit)}


@router.get("/flagged", dependencies=[Depends(_require_api_key)])
async def social_flagged(limit: int = 50):
    return {"items": await state_tracker.flagged_rows(limit)}


@router.post("/pause", dependencies=[Depends(_require_api_key)])
async def social_pause():
    await state_tracker.set_paused(True, "api")
    return {"paused": True}


@router.post("/resume", dependencies=[Depends(_require_api_key)])
async def social_resume():
    await state_tracker.set_paused(False, "api")
    return {"paused": False}


@router.post("/retry", dependencies=[Depends(_require_api_key)])
async def social_retry(queue_id: int | None = None):
    if queue_id is not None:
        await execute(
            "UPDATE social_reply_queue SET status='pending', next_retry_at=NOW(), last_error=NULL WHERE id=$1 AND status IN ('failed','blocked','dlq')",
            queue_id,
        )
    return await send_queue.sweep_once(limit=1)
