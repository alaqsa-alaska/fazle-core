"""
Fazle Core — GitHub Models AI Client
Uses GitHub Models OpenAI-compatible inference endpoint.

Token rotation: GITHUB_TOKEN → GITHUB_TOKEN_2 → GITHUB_TOKEN_3 (round-robin).
Rate limit on free tier: 15 RPM, 1000 TPM — semaphore keeps us serialized.
Fallback: if all tokens fail, caller receives None and llm.py falls back to Ollama.
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

log = logging.getLogger("fazle.github_models")

# Serializes calls to stay within GitHub Models free-tier rate limit (15 RPM).
_github_sem = asyncio.Semaphore(1)

# Round-robin token index (module-level, in-process rotation).
_token_idx: int = 0
import time as _time
_token_cooldown: dict[int, float] = {}   # {token_index: cooldown_until_monotonic}
_TOKEN_DEFAULT_COOLDOWN_S = 65.0         # GitHub free-tier window ~60 s + buffer


def _get_next_token() -> str:
    global _token_idx
    settings = get_settings()
    tokens = [t for t in [
        settings.github_token,
        settings.github_token_2,
        settings.github_token_3,
    ] if t]
    if not tokens:
        raise ValueError("No GitHub token configured — set GITHUB_TOKEN in .env")
    now = _time.monotonic()
    for _ in range(len(tokens)):
        idx = _token_idx % len(tokens)
        _token_idx += 1
        if _token_cooldown.get(idx, 0.0) <= now:
            log.debug("[github_models] using token_idx=%d", idx)
            return tokens[idx]
        log.debug(
            "[github_models] token_idx=%d in cooldown (%.0fs remaining)",
            idx, _token_cooldown[idx] - now,
        )
    raise ValueError("All GitHub tokens in cooldown — falling back to Ollama")


def _make_client() -> AsyncOpenAI:
    settings = get_settings()
    return AsyncOpenAI(
        api_key=_get_next_token(),
        base_url=settings.github_model_endpoint,
    )


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
        from app.database import execute as _execute
        await _execute(
            """INSERT INTO llm_conversation_log
               (caller, provider, model, messages, reply, latency_ms, is_fallback)
               VALUES ($1, 'github_models', $2, $3, $4, $5, $6)""",
            caller,
            model,
            json.dumps([{"role": "user", "content": prompt[:500]}]),
            reply[:2000],
            latency_ms,
            is_fallback,
        )
    except Exception:
        pass


async def classify_intent_llm(text: str) -> str:
    """Classify intent using GitHub Models when rule-based engine returns 'unknown'."""
    settings = get_settings()
    model = settings.github_model_name
    prompt = (
        "Classify this WhatsApp message into one category. "
        "Reply with ONLY the category name, nothing else.\n\n"
        "Categories: recruitment, salary_query, payment_due, escort_duty, complaint, "
        "client_order, leave, join, attendance, slip_submission, voice_note, greeting, unknown\n\n"
        f"Message: {text[:300]}\n\nCategory:"
    )
    valid = {
        "recruitment", "salary_query", "payment_due", "escort_duty",
        "complaint", "client_order", "leave", "join", "attendance",
        "slip_submission", "voice_note", "greeting", "unknown",
    }
    t0 = time.monotonic()
    async with _github_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=10,
                    temperature=0.1,
                ),
                timeout=15.0,
            )
            result = (resp.choices[0].message.content or "").strip().lower().split()[0]
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("classify_intent_llm", model, prompt, result, latency_ms))
            return result if result in valid else "unknown"
        except (RateLimitError, APIError) as e:
            _ra = getattr(getattr(e, "response", None), "headers", {}).get("retry-after")
            _cd = float(_ra) if _ra else _TOKEN_DEFAULT_COOLDOWN_S
            _nt = len([t for t in [settings.github_token, settings.github_token_2, settings.github_token_3] if t])
            _token_cooldown[(_token_idx - 1) % _nt] = _time.monotonic() + _cd
            log.warning("[github_models] classify rate/API error: %s", e)
        except Exception as e:
            log.warning("[github_models] classify error: %s: %s", type(e).__name__, e)
    return "unknown"


async def generate_reply(
    user_message: str,
    intent: str,
    db_context: str = "",
    history: str = "",
    role: str = "new_lead",
    source: str = "unknown",
) -> Optional[str]:
    """
    Generate a short Bengali WhatsApp reply using GitHub Models gpt-4o-mini.
    Returns None on failure so llm.py can fall back to Ollama.
    """
    settings = get_settings()
    model = settings.github_model_name
    prompt = build_whatsapp_reply_policy(
        source=source,
        user_message=user_message,
        role=role,
        intent=intent,
        db_context=db_context,
        history=history,
    )
    t0 = time.monotonic()
    async with _github_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                    temperature=0.4,
                ),
                timeout=30.0,
            )
            reply_text = (resp.choices[0].message.content or "").strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("generate_reply", model, prompt, reply_text, latency_ms))
            log.info("[github_models] generate_reply OK latency=%dms intent=%s role=%s", latency_ms, intent, role)
            return reply_text
        except (RateLimitError, APIError) as e:
            _ra = getattr(getattr(e, "response", None), "headers", {}).get("retry-after")
            _cd = float(_ra) if _ra else _TOKEN_DEFAULT_COOLDOWN_S
            _nt = len([t for t in [settings.github_token, settings.github_token_2, settings.github_token_3] if t])
            _token_cooldown[(_token_idx - 1) % _nt] = _time.monotonic() + _cd
            log.warning("[github_models] generate_reply rate/API error: %s", e)
        except Exception as e:
            log.error("[github_models] generate_reply error: %s: %s", type(e).__name__, e)
    return None


async def generate_recruitment_reply(
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
    source: str = "unknown",
) -> Optional[str]:
    """Recruitment-specific reply via GitHub Models. Returns None on failure."""
    settings = get_settings()
    model = settings.github_model_name
    prompt = build_whatsapp_recruitment_policy(
        source=source,
        user_message=user_message,
        kb_context=kb_context,
        history=history,
        contact_context=contact_context,
    )
    t0 = time.monotonic()
    async with _github_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=150,
                    temperature=0.2,
                ),
                timeout=30.0,
            )
            reply_text = (resp.choices[0].message.content or "").strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("generate_recruitment_reply", model, prompt, reply_text, latency_ms))
            return reply_text
        except (RateLimitError, APIError) as e:
            _ra = getattr(getattr(e, "response", None), "headers", {}).get("retry-after")
            _cd = float(_ra) if _ra else _TOKEN_DEFAULT_COOLDOWN_S
            _nt = len([t for t in [settings.github_token, settings.github_token_2, settings.github_token_3] if t])
            _token_cooldown[(_token_idx - 1) % _nt] = _time.monotonic() + _cd
            log.warning("[github_models] recruitment rate/API error: %s", e)
        except Exception as e:
            log.error("[github_models] recruitment error: %s: %s", type(e).__name__, e)
    return None


async def generate_structured_response(
    prompt: str,
    model: str | None = None,
) -> Optional[str]:
    """
    Call GitHub Models with an arbitrary prompt and return the raw text.
    Intended for structured-output tasks (JSON extraction, classification).
    Returns None on failure.
    """
    settings = get_settings()
    use_model = model or settings.github_model_name
    t0 = time.monotonic()
    async with _github_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=use_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=400,
                    temperature=0.1,
                ),
                timeout=30.0,
            )
            raw = (resp.choices[0].message.content or "").strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("generate_structured_response", use_model, prompt[:200], raw, latency_ms))
            return raw
        except (RateLimitError, APIError) as e:
            _ra = getattr(getattr(e, "response", None), "headers", {}).get("retry-after")
            _cd = float(_ra) if _ra else _TOKEN_DEFAULT_COOLDOWN_S
            _nt = len([t for t in [settings.github_token, settings.github_token_2, settings.github_token_3] if t])
            _token_cooldown[(_token_idx - 1) % _nt] = _time.monotonic() + _cd
            log.warning("[github_models] structured response rate/API error: %s", e)
        except Exception as e:
            log.error("[github_models] structured response error: %s: %s", type(e).__name__, e)
    return None


async def generate_chat_reply(
    question: str,
    context: str,
    history: list[dict],
    model: str | None = None,
) -> Optional[str]:
    """
    Chat Lab reply via GitHub Models.
    Matches ollama.generate_chat_reply() signature exactly.
    Returns None on failure so llm.py can fall back to Ollama.
    """
    settings = get_settings()
    use_model = model or settings.github_model_name

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
            messages.append({"role": role, "content": str(content)[:300]})
    messages.append({"role": "user", "content": question[:400]})

    t0 = time.monotonic()
    async with _github_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    max_tokens=200,
                    temperature=0.3,
                ),
                timeout=30.0,
            )
            reply_text = (resp.choices[0].message.content or "").strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            asyncio.create_task(_log_llm("generate_chat_reply", use_model, question, reply_text, latency_ms))
            log.info("[github_models] generate_chat_reply OK latency=%dms", latency_ms)
            return reply_text
        except (RateLimitError, APIError) as e:
            _ra = getattr(getattr(e, "response", None), "headers", {}).get("retry-after")
            _cd = float(_ra) if _ra else _TOKEN_DEFAULT_COOLDOWN_S
            _nt = len([t for t in [settings.github_token, settings.github_token_2, settings.github_token_3] if t])
            _token_cooldown[(_token_idx - 1) % _nt] = _time.monotonic() + _cd
            log.warning("[github_models] generate_chat_reply rate/API error: %s", e)
        except Exception as e:
            log.error("[github_models] generate_chat_reply error: %s: %s", type(e).__name__, e)
    return None


async def check_health() -> dict:
    """Verify GitHub Models token and endpoint are reachable."""
    settings = get_settings()
    try:
        client = _make_client()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.github_model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            ),
            timeout=10.0,
        )
        return {
            "status": "ok",
            "model": settings.github_model_name,
            "endpoint": settings.github_model_endpoint,
            "response": (resp.choices[0].message.content or "").strip()[:30],
        }
    except Exception as e:
        return {
            "status": "error",
            "model": settings.github_model_name,
            "error": str(e),
        }
