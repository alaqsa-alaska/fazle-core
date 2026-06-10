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


def _get_next_token() -> str:
    global _token_idx
    settings = get_settings()
    tokens = [t for t in [settings.github_token, settings.github_token_2, settings.github_token_3] if t]
    if not tokens:
        raise ValueError("No GitHub token configured — set GITHUB_TOKEN in .env")
    token = tokens[_token_idx % len(tokens)]
    _token_idx += 1
    log.debug("[github_models] using token_idx=%d", (_token_idx - 1) % len(tokens))
    return token


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
            log.warning("[github_models] recruitment rate/API error: %s", e)
        except Exception as e:
            log.error("[github_models] recruitment error: %s: %s", type(e).__name__, e)
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
