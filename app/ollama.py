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
from typing import Optional

from app.config import get_settings
from app import ollama_daemon
from shared.reply_policy import (
    build_whatsapp_reply_policy,
    build_whatsapp_recruitment_policy,
)

log = logging.getLogger("fazle.ollama")

# Shared with modules.route_b_handoff -- lets it detect "Ollama produced
# the fallback string" (Ollama unreachable/non-200/exception) without
# string-matching a literal in a second place.
FALLBACK_REPLY_TEXT = "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।"

# 2026-08-05: promoted from a local variable to a module constant, same
# reason/pattern as FALLBACK_REPLY_TEXT above -- lets app/llm.py's new
# OmniRoute -> Groq -> local Ollama/Hermes -> Kimi chain detect "this local
# Ollama call itself fell back internally" (vs. a genuine reply) so it can
# correctly continue to the Kimi step, instead of treating Ollama's own
# non-empty fallback string as chain-level success.
RECRUITMENT_FALLBACK_TEXT = (
    "আমি ফজলে — আল-আকসা HR assistant।\n"
    "চাকরির জন্য নাম, বয়স ও জেলা লিখে পাঠান।\n"
    "বিস্তারিত জানতে WhatsApp: 01958 122300"
)


def _bounded_int(value: int, *, minimum: int, maximum: int, default: int) -> int:
    try:
        n = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, n))


def _bounded_float(value: float, *, minimum: float, maximum: float, default: float) -> float:
    try:
        n = float(value)
    except Exception:
        return default
    return max(minimum, min(maximum, n))


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
_settings = get_settings()
_ollama_sem = asyncio.Semaphore(
    _bounded_int(_settings.ollama_infer_concurrency, minimum=1, maximum=4, default=1)
)

# Separate semaphore for Web UI chat lab — independent from WhatsApp pipeline so
# admin chat requests don't queue behind automated reply processing.
_rag_sem = asyncio.Semaphore(
    _bounded_int(_settings.ollama_chat_concurrency, minimum=1, maximum=4, default=1)
)


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
                "options": {
                    "temperature": 0.1,
                    "num_predict": 10,
                    "num_ctx": _bounded_int(
                        settings.ollama_num_ctx_classify,
                        minimum=128,
                        maximum=4096,
                        default=256,
                    ),
                },
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


_ROUTE_B_HANDOFF_CATEGORIES = frozenset({
    "recruitment", "employee_claim", "former_employee_claim", "escort_order", "none",
})


async def classify_route_b_category(text: str, history: str = "") -> str:
    """Route B conversation-handoff hint classifier (2026-07-22).

    Ollama-only, no Groq/GitHub fallback -- same design boundary as the rest
    of modules.route_b. This is a HINT ONLY: modules.route_b_handoff never
    trusts this category directly, it only uses it to decide whether to give
    the existing deterministic checks (recruitment_eligibility(),
    is_order_complete()) a second look with a wider context window. Never
    raises -- returns "none" on any failure, exactly like classify_intent_llm
    above, so a classification failure degrades to "no handoff suggested",
    never an exception that could drop the message.

    Distinct from classify_intent_llm(): that function classifies WHAT a
    single message wants (business intent); this one classifies WHO Route
    B's multi-turn conversation partner appears to be (identity/purpose
    category), using the fuller conversation history Route B already has
    and the single-message classifier doesn't see.
    """
    if not text:
        return "none"
    settings = get_settings()
    convo = f"{history}\n[latest] {text}" if history else text
    prompt = (
        "Read this WhatsApp conversation and classify what the OTHER person "
        "(not us) most likely is. Reply with ONLY the category name, nothing else.\n\n"
        "Categories:\n"
        "recruitment - clearly looking for a job/asking to apply\n"
        "employee_claim - clearly says they currently work for us\n"
        "former_employee_claim - clearly says they used to work for us\n"
        "escort_order - clearly placing a vessel-escort security order\n"
        "none - unclear, or none of the above\n\n"
        f"Conversation:\n{convo[:1200]}\n\nCategory:"
    )
    async with _ollama_sem:
        try:
            payload = {
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 10,
                    "num_ctx": _bounded_int(
                        settings.ollama_num_ctx_classify,
                        minimum=128,
                        maximum=4096,
                        default=256,
                    ),
                },
            }
            if _is_qwen3(settings.ollama_model):
                payload["think"] = False
            r = await ollama_daemon.post_generate(payload, timeout=30.0)
            if r.status_code == 200:
                result = r.json().get("response", "").strip().lower().split()[0]
                return result if result in _ROUTE_B_HANDOFF_CATEGORIES else "none"
            else:
                log.warning("[ollama] classify_route_b_category non-200: status=%d body=%.100s",
                            r.status_code, r.text)
        except Exception as e:
            log.warning(f"Ollama route_b handoff classify error: {type(e).__name__}: {e}")
    return "none"


async def generate_reply(
    user_message: str,
    intent: str,
    db_context: str = "",
    history: str = "",
    role: str = "new_lead",
    source: str = "unknown",
    model: str | None = None,
) -> str:
    """
    Generate a short Bengali reply using local Ollama.
    Serialized through semaphore to prevent concurrent timeout failures.
    Prompt is built by shared.reply_policy — identical for bridge1, bridge2, meta.

    model (2026-08-05, Owner decision): optional override of
    settings.ollama_model, matching the pattern generate_chat_reply()
    already used. Lets app/llm.py request a specific model (a local
    "hermes" model if provisioned, or a kimi-*:cloud model for the Kimi
    fallback step) through this same client instead of a new one.
    Omitting it keeps the exact prior behavior.
    """
    settings = get_settings()
    use_model = model or settings.ollama_model
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
                "model": use_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": _bounded_float(
                        settings.ollama_temperature_reply,
                        minimum=0.0,
                        maximum=1.0,
                        default=0.4,
                    ),
                    "num_predict": _bounded_int(
                        settings.ollama_num_predict_reply,
                        minimum=16,
                        maximum=512,
                        default=50,
                    ),
                    "num_ctx": _bounded_int(
                        settings.ollama_num_ctx_default,
                        minimum=256,
                        maximum=8192,
                        default=512,
                    ),
                },
            }
            if _is_qwen3(use_model):
                payload["think"] = False
            r = await ollama_daemon.post_generate(payload, timeout=120.0)
            if r.status_code == 200:
                reply_text = r.json().get("response", "").strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                asyncio.create_task(_log_llm(
                    "generate_reply", use_model, prompt, reply_text, latency_ms,
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
        "generate_reply", use_model, prompt,
        FALLBACK_REPLY_TEXT,
        int((time.monotonic() - t0) * 1000), is_fallback=True,
    ))
    return FALLBACK_REPLY_TEXT


async def generate_recruitment_reply(
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
    source: str = "unknown",
    model: str | None = None,
) -> str:
    """
    Recruitment-only reply brain.

    This "educates" qwen at runtime with approved recruitment KB + recent
    conversation memory. It does not fine-tune model weights.
    Prompt is built by shared.reply_policy — identical for bridge1, bridge2, meta.

    model (2026-08-05, Owner decision): optional override of
    settings.ollama_model — see generate_reply()'s docstring above for why.
    """
    settings = get_settings()
    use_model = model or settings.ollama_model
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
                "model": use_model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": _bounded_float(
                        settings.ollama_temperature_recruitment,
                        minimum=0.0,
                        maximum=1.0,
                        default=0.2,
                    ),
                    "num_predict": _bounded_int(
                        settings.ollama_num_predict_recruitment,
                        minimum=16,
                        maximum=512,
                        default=50,
                    ),
                    "num_ctx": _bounded_int(
                        settings.ollama_num_ctx_default,
                        minimum=256,
                        maximum=8192,
                        default=512,
                    ),
                    "repeat_penalty": 1.08,
                },
            }
            if _is_qwen3(use_model):
                payload["think"] = False
            r = await ollama_daemon.post_generate(payload, timeout=120.0)
            if r.status_code == 200:
                reply_text = r.json().get("response", "").strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                asyncio.create_task(_log_llm(
                    "generate_recruitment_reply", use_model, prompt, reply_text, latency_ms,
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
    _fallback = RECRUITMENT_FALLBACK_TEXT
    asyncio.create_task(_log_llm(
        "generate_recruitment_reply", use_model, prompt,
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
তুমি ফজলে — {settings.company_name}-এর সহকারী।

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
            "temperature": _bounded_float(
                settings.ollama_temperature_rag,
                minimum=0.0,
                maximum=1.0,
                default=0.3,
            ),
            "num_predict": _bounded_int(
                settings.ollama_num_predict_rag,
                minimum=32,
                maximum=1024,
                default=150,
            ),
            "num_ctx": _bounded_int(
                settings.ollama_num_ctx_default,
                minimum=256,
                maximum=8192,
                default=512,
            ),
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
        context_block = f"\n\nLive DB / জ্ঞানভাণ্ডার থেকে তথ্য:\n{context}"

    prompt = f"""\
তুমি ফজলে — {settings.company_name}-এর AI সহকারী (Admin Chat Lab), শুধুমাত্র প্রতিষ্ঠানের মালিক ও অনুমোদিত ব্যবহারকারীদের জন্য।

নিয়ম:
- বাংলায় উত্তর দাও (প্রশ্ন ইংরেজিতে হলে ইংরেজিতে)
- সর্বোচ্চ ৪-৫ বাক্য; সরাসরি ও তথ্যবহুল হও
- কোনো markdown, table বা internal label দেবে না
- শুধুমাত্র দেওয়া তথ্য ও context ব্যবহার করো; অনুমান করো না
- তোমার কাছে fazle-core-এর live database-এর read-only অ্যাক্সেস আছে; নিচের context-এ সেই live ডেটা থাকবে{history_block}{context_block}

Admin-এর প্রশ্ন: {question[:400]}

উত্তর:"""

    payload: dict = {
        "model": active_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": _bounded_float(
                settings.ollama_temperature_chat,
                minimum=0.0,
                maximum=1.0,
                default=0.3,
            ),
            "num_predict": _bounded_int(
                settings.ollama_num_predict_chat,
                minimum=32,
                maximum=1024,
                default=100,
            ),   # 100 tok @ 1.1 tok/s ≈ 91s → fits in 180s window
            "num_ctx": _bounded_int(
                settings.ollama_num_ctx_default,
                minimum=256,
                maximum=8192,
                default=512,
            ),
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


# ── Raw decision-call shape (2026-08-25, WS1 of the Hermes Owner-Authority
# recovery pass) ──────────────────────────────────────────────────────
# Local-Ollama raw decision call. Same contract as
# omniroute_provider.decision_call / groq_provider.decision_call: takes a
# system+user prompt, returns raw text or None, never raises. The `model=`
# kwarg lets app/llm.decision_call() reach the Kimi cloud-model proxy by
# passing settings.kimi_model_name through to this same client, matching
# how app/llm.py's existing reply chain already reuses this same local
# client for Kimi (see app/llm.py "Kimi: reached through the SAME local
# Ollama client" note).
async def decision_call(
    *,
    system_prompt: str,
    user_payload: str,
    max_tokens: int = 300,
    temperature: float = 0.1,
    timeout: float = 12.0,
    model: Optional[str] = None,
) -> Optional[str]:
    """Raw local-Ollama decision call. Returns raw model text or None on
    any failure. Used only via app/llm.decision_call()."""
    settings = get_settings()
    use_model = model or settings.ollama_model
    if settings.ollama_reply_disabled:
        # Decision path mirrors app/llm.py's customer-facing policy: when
        # the Owner's "Ollama disabled" toggle is on, we skip the local
        # provider entirely (so the chain correctly continues to Kimi).
        return None
    t0 = time.monotonic()
    # Same bounded-reply settings as existing reply generation paths.
    bounded_num_predict = max(64, min(max_tokens, 1024))
    bounded_temperature = max(0.0, min(temperature, 1.0))
    payload = {
        "model": use_model,
        "system": system_prompt,
        "prompt": user_payload,
        "stream": False,
        "options": {
            "temperature": bounded_temperature,
            "num_predict": bounded_num_predict,
            "num_ctx": _bounded_int(
                settings.ollama_num_ctx_default,
                minimum=256,
                maximum=8192,
                default=512,
            ),
        },
    }
    if _is_qwen3(use_model):
        payload["think"] = False
    async with _ollama_sem:
        try:
            r = await ollama_daemon.post_generate(payload, timeout=max(timeout, 1.0))
            if r.status_code == 200:
                text = (r.json().get("response", "") or "").strip()
                latency_ms = int((time.monotonic() - t0) * 1000)
                log.info("[ollama] decision_call OK latency=%dms model=%s", latency_ms, use_model)
                return text or None
            log.warning("[ollama] decision_call non-200: status=%d body=%.100s",
                        r.status_code, r.text)
        except Exception as e:
            log.warning("[ollama] decision_call error: %s: %s", type(e).__name__, e)
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
