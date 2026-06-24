"""
Fazle Core — Ollama AI
Role-aware prompts for human-quality Bengali replies.

Key fixes vs v1:
- Semaphore limits Ollama to 1 concurrent request (prevents timeout cascade
  when 2+ messages arrive at same time; tested: 2 concurrent = 1 times out)
- Timeout increased: classify→15s, generate→45s
- Intent-aware system prompts give focused context per message type
- num_predict reduced 200→120 for faster responses

Prompt text lives in shared.reply_policy — a single source of truth for all
WhatsApp channels (bridge1, bridge2, meta). Do not add prompt strings here.
"""
import asyncio
import json
import logging
import time
from app.config import get_settings
from app import ollama_daemon
from shared.reply_policy import (
    build_whatsapp_reply_policy,
    build_whatsapp_recruitment_policy,
)

log = logging.getLogger("fazle.ollama")


async def _log_llm(
    caller: str,
    model: str,
    prompt: str,
    reply: str,
    latency_ms: int,
    is_fallback: bool = False,
) -> None:
    """Fire-and-forget INSERT into llm_conversation_log. Never raises."""
    try:
        from modules.db import execute as _execute
        await _execute(
            """INSERT INTO llm_conversation_log
               (caller, provider, model, messages, reply, latency_ms, is_fallback)
               VALUES ($1, 'ollama', $2, $3, $4, $5, $6)""",
            caller,
            model,
            json.dumps([{"role": "user", "content": prompt[:500]}]),
            reply[:2000],
            latency_ms,
            is_fallback,
        )
    except Exception:
        pass


# Serializes WhatsApp auto-reply Ollama calls — prevents concurrent-request timeout failures.
# Ollama on this hardware takes ~17s per generate; two simultaneous = one times out.
_ollama_sem = asyncio.Semaphore(1)

# Separate semaphore for Web UI chat lab — independent from WhatsApp pipeline so
# admin chat requests don't queue behind automated reply processing.
_rag_sem = asyncio.Semaphore(1)


async def classify_intent_llm(text: str) -> str:
    """
    Use Ollama to classify intent when rule-based engine returns 'unknown'.
    Serialized through semaphore — waits in queue if Ollama is busy.
    """
    settings = get_settings()
    prompt = (
        "Classify this WhatsApp message into one category. "
        "Reply with ONLY the category name, nothing else.\n\n"
        "Categories: recruitment, salary_query, payment_due, escort_duty, complaint, "
        "client_order, leave, join, attendance, slip_submission, voice_note, greeting, unknown\n\n"
        f"Message: {text[:300]}\n\nCategory:"
    )
    async with _ollama_sem:
        try:
            payload = {
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 10, "num_ctx": 256},
            }
            if _is_qwen3(settings.ollama_model):
                payload["think"] = False
            r = await ollama_daemon.post_generate(payload, timeout=30.0)
            if r.status_code == 200:
                result = r.json().get("response", "").strip().lower().split()[0]
                valid = {
                    "recruitment", "salary_query", "payment_due", "escort_duty",
                    "complaint", "client_order", "leave", "join", "attendance",
                    "slip_submission", "voice_note", "greeting", "unknown",
                }
                return result if result in valid else "unknown"
            else:
                log.warning("[ollama] classify_intent_llm non-200: status=%d body=%.100s",
                            r.status_code, r.text)
        except Exception as e:
            log.warning(f"Ollama classify error: {type(e).__name__}: {e}")
    return "unknown"


async def generate_reply(
    user_message: str,
    intent: str,
    db_context: str = "",
    history: str = "",
    role: str = "new_lead",
    source: str = "unknown",
) -> str:
    """
    Generate a short Bengali reply using local Ollama.
    Serialized through semaphore to prevent concurrent timeout failures.
    Prompt is built by shared.reply_policy — identical for bridge1, bridge2, meta.
    """
    settings = get_settings()
    prompt = build_whatsapp_reply_policy(
        source=source,
        user_message=user_message,
        role=role,
        intent=intent,
        db_context=db_context,
        history=history,
    )

    t0 = time.monotonic()
    async with _ollama_sem:
        try:
            payload = {
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.4, "num_predict": 50, "num_ctx": 512},
            }
            if _is_qwen3(settings.ollama_model):
                payload["think"] = False
            r = await ollama_daemon.post_generate(payload, timeout=120.0)
            if r.status_code == 200:
                reply_text = r.json().get("response", "").strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                asyncio.create_task(_log_llm(
                    "generate_reply", settings.ollama_model, prompt, reply_text, latency_ms,
                ))
                return reply_text
            else:
                log.warning("[ollama] generate_reply non-200: status=%d body=%.100s",
                            r.status_code, r.text)
        except Exception as e:
            log.error(f"Ollama generate error: {type(e).__name__}: {e}")

    # Fallback — quality gate matches this EXACTLY → stored as 'rejected_fallback'
    try:
        from modules import observability as _obs
        _obs.inc("llm_fallback_total")
    except Exception:
        pass
    asyncio.create_task(_log_llm(
        "generate_reply", settings.ollama_model, prompt,
        "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।",
        int((time.monotonic() - t0) * 1000), is_fallback=True,
    ))
    return "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।"


async def generate_recruitment_reply(
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
    source: str = "unknown",
) -> str:
    """
    Recruitment-only reply brain.

    This "educates" qwen at runtime with approved recruitment KB + recent
    conversation memory. It does not fine-tune model weights.
    Prompt is built by shared.reply_policy — identical for bridge1, bridge2, meta.
    """
    settings = get_settings()
    prompt = build_whatsapp_recruitment_policy(
        source=source,
        user_message=user_message,
        kb_context=kb_context,
        history=history,
        contact_context=contact_context,
    )

    t0 = time.monotonic()
    async with _ollama_sem:
        try:
            payload = {
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.2,
                    "num_predict": 50,
                    "num_ctx": 512,
                    "repeat_penalty": 1.08,
                },
            }
            if _is_qwen3(settings.ollama_model):
                payload["think"] = False
            r = await ollama_daemon.post_generate(payload, timeout=120.0)
            if r.status_code == 200:
                reply_text = r.json().get("response", "").strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                asyncio.create_task(_log_llm(
                    "generate_recruitment_reply", settings.ollama_model, prompt, reply_text, latency_ms,
                ))
                return reply_text
            log.warning("[ollama] generate_recruitment_reply non-200: status=%d body=%.100s",
                        r.status_code, r.text)
        except Exception as e:
            log.error(f"Ollama recruitment generate error: {type(e).__name__}: {e}")

    try:
        from modules import observability as _obs
        _obs.inc("llm_fallback_total", labels={"path": "recruitment"})
    except Exception:
        pass
    _fallback = (
        "আমি ফজলে — আল-আকসা HR assistant।\n"
        "চাকরির জন্য নাম, বয়স ও জেলা লিখে পাঠান।\n"
        "বিস্তারিত জানতে WhatsApp: 01958 122322"
    )
    asyncio.create_task(_log_llm(
        "generate_recruitment_reply", settings.ollama_model, prompt,
        _fallback, int((time.monotonic() - t0) * 1000), is_fallback=True,
    ))
    return _fallback


def _is_qwen3(model: str) -> bool:
    """qwen3 models require think:false or they output empty responses."""
    return model.startswith("qwen3:")


async def generate_rag_answer(
    question: str,
    context: str,
    model: str | None = None,
) -> str | None:
    """
    Generate a natural Bengali answer for the Web UI chat lab.
    Uses the retrieved RAG context chunks as the sole knowledge source.
    Returns None on failure so the caller can fallback to raw chunks.
    """
    settings = get_settings()
    active_model = model or settings.ollama_model
    prompt = f"""\
তুমি ফজলে — আল-আকসা সিকিউরিটি অ্যান্ড লজিস্টিকস সার্ভিসেস লিমিটেডের সহকারী।

নিচের তথ্য থেকে প্রশ্নের উত্তর দাও। শুধুমাত্র দেওয়া তথ্য ব্যবহার করো।
যদি তথ্যে উত্তর না থাকে, বলো: "এ বিষয়ে নির্দিষ্ট তথ্য আমার কাছে নেই। অফিসে যোগাযোগ করুন।"

নিয়ম:
- বাংলায় উত্তর দাও (প্রশ্ন ইংরেজিতে হলে ইংরেজিতে)
- সর্বোচ্চ ৩-৪ বাক্য
- কোনো markdown, table বা internal label দেবে না
- সম্মানজনক ও সহজ ভাষায় বলো

তথ্য:
{context}

প্রশ্ন: {question[:400]}

উত্তর:"""

    payload: dict = {
        "model": active_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 150,
            "repeat_penalty": 1.05,
        },
    }
    if _is_qwen3(active_model):
        payload["think"] = False

    async with _rag_sem:
        try:
            r = await ollama_daemon.post_generate(payload, timeout=120.0)
            if r.status_code == 200:
                reply = r.json().get("response", "").strip()
                return reply if reply else None
            log.warning("[ollama] generate_rag_answer non-200: status=%d body=%.100s",
                        r.status_code, r.text)
        except Exception as e:
            log.error("Ollama RAG answer error: %s: %s", type(e).__name__, e)
    return None


async def generate_chat_reply(
    question: str,
    context: str,
    history: list[dict],
    model: str | None = None,
) -> str | None:
    """
    Generate a conversational admin chat reply with optional RAG context and turn history.
    history items: [{role: "user"|"assistant", content: str}]
    Returns None on failure.
    """
    settings = get_settings()
    active_model = model or settings.ollama_model

    history_block = ""
    if history:
        lines = []
        for h in history[-6:]:  # last 3 turns max
            role_label = "Admin" if h.get("role") == "user" else "Fazle"
            lines.append(f"{role_label}: {str(h.get('content', ''))[:300]}")
        history_block = "\n\nআগের কথোপকথন:\n" + "\n".join(lines)

    context_block = ""
    if context.strip():
        context_block = f"\n\nজ্ঞানভাণ্ডার থেকে তথ্য:\n{context}"

    prompt = f"""\
তুমি ফজলে — আল-আকসা সিকিউরিটি অ্যান্ড লজিস্টিকস সার্ভিসেস লিমিটেডের AI সহকারী (Admin Chat Lab)।

নিয়ম:
- বাংলায় উত্তর দাও (প্রশ্ন ইংরেজিতে হলে ইংরেজিতে)
- সর্বোচ্চ ৪-৫ বাক্য; সরাসরি ও তথ্যবহুল হও
- কোনো markdown, table বা internal label দেবে না
- শুধুমাত্র দেওয়া তথ্য ও context ব্যবহার করো; অনুমান করো না{history_block}{context_block}

Admin-এর প্রশ্ন: {question[:400]}

উত্তর:"""

    payload: dict = {
        "model": active_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 100,   # 100 tok @ 1.1 tok/s ≈ 91s → fits in 180s window
            "repeat_penalty": 1.05,
        },
    }
    if _is_qwen3(active_model):
        payload["think"] = False

    async with _rag_sem:
        try:
            r = await ollama_daemon.post_generate(payload, timeout=180.0)
            if r.status_code == 200:
                reply = r.json().get("response", "").strip()
                return reply if reply else None
            log.warning("[ollama] generate_chat_reply non-200: status=%d body=%.100s",
                        r.status_code, r.text)
        except Exception as e:
            log.error("Ollama chat reply error: %s: %s", type(e).__name__, e)
    return None


async def check_ollama_health() -> dict:
    """Check if Ollama is reachable and return available models."""
    settings = get_settings()
    try:
        r = await ollama_daemon.get_tags(timeout=5.0)
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            return {
                "status": "ok",
                "models": models,
                "active_model": settings.ollama_model,
                "queue_depth": _ollama_sem._value,
                "daemon": ollama_daemon.diagnostics(),
            }
    except Exception as e:
        return {"status": "error", "error": str(e)}
    return {"status": "error"}
