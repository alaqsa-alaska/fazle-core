"""
Continuous learning — extract structured facts from WhatsApp conversations
and persist them to user_memory + optionally promote to fazle_knowledge_base.

Called as a fire-and-forget background task from the bridge event handler.
"""
import json
import logging

from app.database import execute, fetch_one

log = logging.getLogger("fazle.memory_extractor")

_EXTRACTION_PROMPT_TEMPLATE = """\
নিচের WhatsApp conversation টি বিশ্লেষণ করো।

Conversation:
{conv_text}

এখন JSON format এ গুরুত্বপূর্ণ তথ্য extract করো:

{{
  "name": "ব্যক্তির নাম (যদি উল্লেখ থাকে, নইলে null)",
  "role_hint": "role এর ইঙ্গিত (employee/client/candidate/unknown)",
  "important_facts": [
    {{"type": "work_info", "content": "..."}},
    {{"type": "personal_info", "content": "..."}},
    {{"type": "pending_matter", "content": "..."}}
  ],
  "should_update_kb": false,
  "kb_content": ""
}}

শুধু valid JSON দাও, অন্য কিছু না।"""


async def _ensure_profile(phone: str) -> None:
    """Insert a minimal user_profiles row if one doesn't exist."""
    await execute(
        "INSERT INTO user_profiles (phone_canonical) VALUES ($1) ON CONFLICT (phone_canonical) DO NOTHING",
        phone,
    )


async def extract_and_save_memory(
    phone: str,
    conversation: list[dict],
) -> None:
    """
    Use GitHub Models to extract facts from a conversation turn and persist them.
    Designed to run as asyncio.create_task — never raises.
    """
    if not phone or not conversation:
        return

    try:
        from app import github_models

        conv_text = "\n".join(
            f"{m['role'].upper()}: {str(m.get('content', ''))[:400]}"
            for m in conversation
        )
        prompt = _EXTRACTION_PROMPT_TEMPLATE.format(conv_text=conv_text)

        raw = await github_models.generate_structured_response(prompt)
        if not raw:
            return

        # Strip markdown code fences if model wrapped the JSON
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        data: dict = json.loads(raw)

        await _ensure_profile(phone)

        if data.get("name"):
            await execute(
                """UPDATE user_profiles
                   SET name = $1, updated_at = NOW()
                   WHERE phone_canonical = $2 AND name IS NULL""",
                str(data["name"])[:200],
                phone,
            )

        facts: list[dict] = data.get("important_facts") or []
        for fact in facts:
            ftype = str(fact.get("type") or "conversation_fact")[:50]
            content = str(fact.get("content") or "").strip()
            if content:
                await execute(
                    """INSERT INTO user_memory (phone_canonical, memory_type, content, source)
                       VALUES ($1, $2, $3, 'github_models_extracted')""",
                    phone,
                    ftype,
                    content[:2000],
                )

        if data.get("should_update_kb") and data.get("kb_content"):
            kb_text = str(data["kb_content"]).strip()[:2000]
            if kb_text:
                await execute(
                    """INSERT INTO fazle_knowledge_base
                           (category, key, trigger_keywords, reply_text, confidence, is_active)
                       VALUES ('conversation', $1, '', $2, 0.7, true)
                       ON CONFLICT DO NOTHING""",
                    f"auto_extracted_{phone[:20]}",
                    kb_text,
                )

        n = len(facts)
        log.info("[memory_extractor] %s: saved %d fact(s)", phone, n)

    except json.JSONDecodeError as e:
        log.debug("[memory_extractor] JSON parse failed for %s: %s", phone, e)
    except Exception as e:
        log.warning("[memory_extractor] failed for %s: %s: %s", phone, type(e).__name__, e)
