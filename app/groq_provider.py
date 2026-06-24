"""
Fazle Core — Groq AI Client (Secondary Provider)
Uses Groq's OpenAI-compatible inference endpoint.

Acts as the first external fallback after local Ollama.
Free tier: 14,400 req/day, 30 RPM, 6,000 TPM.
Model default: llama-3.1-8b-instant (excellent Bengali support).

Caller: llm.py uses this when GitHub Models fails and OLLAMA_REPLY_DISABLED=true.
"""
import asyncio
import json
import logging
import time
from typing import Optional

from openai import AsyncOpenAI, RateLimitError, APIError
from app.config import get_settings
from shared.reply_policy import (
    build_whatsapp_reply_policy,
    build_whatsapp_recruitment_policy,
)

log = logging.getLogger("fazle.groq")

# Groq free tier: 30 RPM — looser than GitHub Models but still serialise.
_groq_sem = asyncio.Semaphore(2)

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_GROQ_DEFAULT_COOLDOWN_S = 35.0
_VALID_INTENTS = {
    "recruitment", "salary_query", "payment_due", "escort_duty",
    "complaint", "client_order", "leave", "join", "attendance",
    "slip_submission", "voice_note", "greeting", "unknown",
}


def _make_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY not configured")
    return AsyncOpenAI(
        api_key=settings.groq_api_key,
        base_url=_GROQ_BASE_URL,
    )


async def _log_llm(caller: str, model: str, prompt: str, reply: str, latency_ms: int) -> None:
    try:
        from app.database import execute as _execute
        await _execute(
            """INSERT INTO llm_conversation_log
               (caller, provider, model, messages, reply, latency_ms, is_fallback)
               VALUES ($1, 'groq', $2, $3, $4, $5, true)""",
            caller,
            model,
            json.dumps([{"role": "user", "content": prompt[:500]}]),
            reply[:2000],
            latency_ms,
        )
    except Exception:
        pass


async def classify_intent_llm(text: str) -> str:
    """Classify WhatsApp intent through Groq; return unknown on any failure."""
    settings = get_settings()
    model = settings.groq_model_name
    prompt = (
        "Classify this WhatsApp message into one category. "
        "Reply with ONLY the category name.\n\n"
        "Categories: recruitment, salary_query, payment_due, escort_duty, complaint, "
        "client_order, leave, join, attendance, slip_submission, voice_note, greeting, unknown\n\n"
        f"Message: {text[:300]}\n\nCategory:"
    )
    t0 = time.monotonic()
    async with _groq_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=10,
                    temperature=0.0,
                ),
                timeout=15.0,
            )
            result = (resp.choices[0].message.content or "").strip().lower().split()[0]
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("classify_intent_llm", model, prompt, result, latency_ms))
            return result if result in _VALID_INTENTS else "unknown"
        except (RateLimitError, APIError) as e:
            log.warning("[groq] classify rate/API error: %s", e)
        except Exception as e:
            log.warning("[groq] classify error: %s: %s", type(e).__name__, e)
    return "unknown"


async def generate_reply(
    user_message: str,
    intent: str,
    db_context: str = "",
    history: str = "",
    role: str = "new_lead",
    source: str = "unknown",
) -> Optional[str]:
    """Generate WhatsApp reply via Groq. Returns None on failure."""
    settings = get_settings()
    model = settings.groq_model_name
    prompt = build_whatsapp_reply_policy(
        source=source,
        user_message=user_message,
        role=role,
        intent=intent,
        db_context=db_context,
        history=history,
    )
    t0 = time.monotonic()
    async with _groq_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200,
                    temperature=0.4,
                ),
                timeout=25.0,
            )
            reply_text = (resp.choices[0].message.content or "").strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("generate_reply", model, prompt, reply_text, latency_ms))
            log.info("[groq] generate_reply OK latency=%dms intent=%s", latency_ms, intent)
            return reply_text
        except (RateLimitError, APIError) as e:
            log.warning("[groq] generate_reply rate/API error: %s", e)
        except Exception as e:
            log.error("[groq] generate_reply error: %s: %s", type(e).__name__, e)
    return None


async def generate_recruitment_reply(
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
    source: str = "unknown",
) -> Optional[str]:
    """Recruitment reply via Groq. Returns None on failure."""
    settings = get_settings()
    model = settings.groq_model_name
    prompt = build_whatsapp_recruitment_policy(
        source=source,
        user_message=user_message,
        kb_context=kb_context,
        history=history,
        contact_context=contact_context,
    )
    t0 = time.monotonic()
    async with _groq_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200,
                    temperature=0.2,
                ),
                timeout=25.0,
            )
            reply_text = (resp.choices[0].message.content or "").strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("generate_recruitment_reply", model, prompt, reply_text, latency_ms))
            return reply_text
        except (RateLimitError, APIError) as e:
            log.warning("[groq] recruitment rate/API error: %s", e)
        except Exception as e:
            log.error("[groq] recruitment error: %s: %s", type(e).__name__, e)
    return None


async def generate_chat_reply(
    question: str,
    context: str,
    history: list[dict],
    model: str | None = None,
) -> Optional[str]:
    """Chat Lab reply via Groq. Returns None on failure."""
    settings = get_settings()
    use_model = model or settings.groq_model_name

    system_content = (
        "তুমি ফজলে — আল-আকসা সিকিউরিটি অ্যান্ড লজিস্টিকস সার্ভিসেস লিমিটেডের AI সহকারী (Admin Chat Lab)।\n\n"
        "নিয়ম:\n"
        "- বাংলায় উত্তর দাও (প্রশ্ন ইংরেজিতে হলে ইংরেজিতে)\n"
        "- সর্বোচ্চ ৪-৫ বাক্য; সরাসরি ও তথ্যবহুল হও\n"
        "- কোনো markdown, table বা internal label দেবে না\n"
        "- শুধুমাত্র দেওয়া তথ্য ও context ব্যবহার করো; অনুমান করো না"
    )
    if context and context.strip():
        system_content += f"\n\nজ্ঞানভাণ্ডার থেকে তথ্য:\n{context}"

    messages: list[dict] = [{"role": "system", "content": system_content}]
    for h in history[-6:]:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    t0 = time.monotonic()
    async with _groq_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    max_tokens=400,
                    temperature=0.4,
                ),
                timeout=30.0,
            )
            reply_text = (resp.choices[0].message.content or "").strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("generate_chat_reply", use_model, question, reply_text, latency_ms))
            log.info("[groq] chat_reply OK latency=%dms", latency_ms)
            return reply_text
        except (RateLimitError, APIError) as e:
            log.warning("[groq] chat_reply rate/API error: %s", e)
        except Exception as e:
            log.error("[groq] chat_reply error: %s: %s", type(e).__name__, e)
    return None


async def check_health() -> dict:
    settings = get_settings()
    if not settings.groq_api_key:
        return {"status": "disabled", "reason": "GROQ_API_KEY not set"}
    try:
        client = _make_client()
        t0 = time.monotonic()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.groq_model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            ),
            timeout=10.0,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"status": "ok", "model": settings.groq_model_name, "latency_ms": latency_ms}
    except Exception as e:
        return {"status": "error", "error": str(e)}
