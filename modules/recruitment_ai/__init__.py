"""
Recruitment AI reply brain.

Builds a focused prompt from approved recruitment KB + recent conversation
history, then asks the configured Ollama model for a production auto-reply.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app import ollama as ai
from app.database import fetch_all, fetch_one

log = logging.getLogger("fazle.recruitment_ai")

_CORE_KEYS = (
    "job",
    "apply",
    "fee",
    "salary",
    "office",
    "security",
    "survey",
    "escort",
    "document",
    "vacancy",
)

_QUESTION_HINTS = (
    "who are you",
    "who r u",
    "আপনি কে",
    "তুমি কে",
    "কেন",
    "why",
    "am i asked for job",
    "asked for job",
    "lok lagbe",
    "লোক লাগবে",
    "কাজ আছে",
    "job ache",
)


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[\s,.;:!?।()\[\]{}<>/\\|\"'`~\-]+", text.lower()) if len(t) >= 2}


def looks_like_recruitment_followup(text: str) -> bool:
    lower = (text or "").strip().lower()
    if not lower:
        return False
    return any(hint in lower for hint in _QUESTION_HINTS)


async def build_recruitment_kb_context(message: str, limit: int = 10) -> str:
    """Return compact approved recruitment KB context for the current message."""
    msg_lower = (message or "").lower()
    msg_tokens = _tokens(msg_lower)
    rows: list[dict] = []
    try:
        rows = await fetch_all(
            """
            SELECT key, trigger_keywords, reply_text, confidence
            FROM fazle_knowledge_base
            WHERE is_active = true
              AND category IN ('recruitment', 'vessel_duty')
            ORDER BY confidence DESC NULLS LAST, key ASC
            """
        )
    except Exception as e:
        log.warning("[recruit_ai] KB load failed: %s", e)

    scored: list[tuple[int, dict]] = []
    for row in rows:
        keywords = [str(k).lower() for k in (row.get("trigger_keywords") or [])]
        key = str(row.get("key") or "").lower()
        reply = str(row.get("reply_text") or "")
        score = 0
        if key and any(core in key for core in _CORE_KEYS):
            score += 2
        for kw in keywords:
            if kw and kw in msg_lower:
                score += 12
            elif kw and _tokens(kw) & msg_tokens:
                score += 4
        if any(core in reply.lower() for core in _CORE_KEYS):
            score += 1
        scored.append((score, row))

    picked = [row for score, row in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0][:limit]
    if len(picked) < 5:
        seen = {row.get("key") for row in picked}
        for _score, row in sorted(scored, key=lambda item: item[0], reverse=True):
            if row.get("key") not in seen:
                picked.append(row)
                seen.add(row.get("key"))
            if len(picked) >= 5:
                break

    lines: list[str] = []
    for row in picked[:limit]:
        key = row.get("key") or "kb"
        reply = re.sub(r"\s+", " ", str(row.get("reply_text") or "")).strip()
        if reply:
            lines.append(f"- {key}: {reply[:420]}")
    return "\n".join(lines)


async def get_recent_conversation(phone: str, limit: int = 12) -> str:
    """Return recent normalized WhatsApp conversation memory, read-only."""
    try:
        rows = await fetch_all(
            """
            SELECT platform, direction, message_body, received_at
            FROM wbom_whatsapp_messages
            WHERE sender_number = $1
              AND platform IN ('bridge1', 'bridge2', 'meta')
            ORDER BY received_at DESC
            LIMIT $2
            """,
            phone,
            limit,
        )
    except Exception as e:
        log.warning("[recruit_ai] history load failed phone=%s: %s", phone, e)
        return ""

    lines: list[str] = []
    for row in reversed(rows):
        direction = "candidate" if row.get("direction") == "inbound" else "fazle"
        platform = row.get("platform") or "bridge"
        body = re.sub(r"\s+", " ", str(row.get("message_body") or "")).strip()
        if body:
            lines.append(f"{platform}/{direction}: {body[:240]}")
    return "\n".join(lines)


async def get_recruitment_session_context(phone: str) -> str:
    """Return active/intake session status as context only; qwen decides wording."""
    try:
        row = await fetch_one(
            """
            SELECT collection_step, funnel_stage, full_name, age, area,
                   job_preference, experience_years, score_bucket
            FROM fazle_recruitment_sessions
            WHERE phone = $1
            LIMIT 1
            """,
            phone,
        )
    except Exception:
        row = None
    if not row:
        return "No stored recruitment session yet."
    parts = [f"{k}={v}" for k, v in row.items() if v not in (None, "")]
    return "Recruitment session: " + ", ".join(parts)


def clean_recruitment_reply(reply: str) -> str:
    """Keep WhatsApp output short and remove common model artifacts."""
    text = (reply or "").strip()
    for marker in (
        "Reply only the WhatsApp message text:",
        "WhatsApp message:",
        "উত্তর:",
        "Reply:",
    ):
        if text.lower().startswith(marker.lower()):
            text = text[len(marker):].strip()
    text = text.replace("```", "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 5:
        lines = lines[:5]
    text = "\n".join(lines)
    if len(text) > 360:
        text = text[:357].rstrip() + "..."
    return text


async def generate_recruitment_reply(
    *,
    phone: str,
    text: str,
    source: str,
    contact_context: str = "",
) -> Optional[str]:
    kb_context = await build_recruitment_kb_context(text)
    history = await get_recent_conversation(phone)
    session_context = await get_recruitment_session_context(phone)
    full_contact_context = "\n".join(
        part for part in (f"source={source}", contact_context, session_context) if part
    )
    reply = await ai.generate_recruitment_reply(
        user_message=text,
        kb_context=kb_context,
        history=history,
        contact_context=full_contact_context,
        source=source,
    )
    reply = clean_recruitment_reply(reply)
    if not reply:
        return None
    log.info("[recruit_ai] reply generated phone=%s source=%s chars=%d", phone, source, len(reply))
    return reply
