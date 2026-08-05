"""
Fazle Core — OmniRoute AI Client (Primary Provider, 2026-08-05)
Uses OmniRoute's OpenAI-compatible local gateway.

Owner decision 2026-08-05: fazle-core's AI generation/polish chain is now
OmniRoute -> Groq -> local Ollama/Hermes -> Kimi. This is the first
OmniRoute-facing code in fazle-core; the network path itself is not new --
hermes-runner's own ~/.hermes/config.yaml already points the real Hermes CLI
at this exact same local gateway (http://127.0.0.1:20128/v1), on the same
host. Mirrors app/groq_provider.py's shape/size exactly, matching this
codebase's existing one-module-per-provider convention rather than either
inlining a provider client in app/llm.py (the orchestrator) or overloading
an unrelated existing provider file.

Caller: app/llm.py uses this as the first attempt in generate_reply/
generate_recruitment_reply, before Groq.
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

log = logging.getLogger("fazle.omniroute")

# OmniRoute is a local gateway (not a rate-limited third-party API like
# Groq/GitHub Models), but calls are still serialized to avoid piling up
# concurrent requests against the single local instance under load.
_omniroute_sem = asyncio.Semaphore(3)


def _make_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.omniroute_api_key:
        raise ValueError("OMNIROUTE_API_KEY not configured")
    return AsyncOpenAI(
        api_key=settings.omniroute_api_key,
        base_url=settings.omniroute_url,
    )


async def _log_llm(caller: str, model: str, prompt: str, reply: str, latency_ms: int) -> None:
    try:
        from app.database import execute as _execute
        await _execute(
            """INSERT INTO llm_conversation_log
               (caller, provider, model, messages, reply, latency_ms, is_fallback)
               VALUES ($1, 'omniroute', $2, $3, $4, $5, false)""",
            caller,
            model,
            json.dumps([{"role": "user", "content": prompt[:500]}]),
            reply[:2000],
            latency_ms,
        )
    except Exception:
        pass


async def generate_reply(
    user_message: str,
    intent: str,
    db_context: str = "",
    history: str = "",
    role: str = "new_lead",
    source: str = "unknown",
) -> Optional[str]:
    """Generate WhatsApp reply via OmniRoute. Returns None on failure."""
    settings = get_settings()
    model = settings.omniroute_model_name
    prompt = build_whatsapp_reply_policy(
        source=source,
        user_message=user_message,
        role=role,
        intent=intent,
        db_context=db_context,
        history=history,
    )
    t0 = time.monotonic()
    async with _omniroute_sem:
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
            log.info("[omniroute] generate_reply OK latency=%dms intent=%s", latency_ms, intent)
            return reply_text
        except (RateLimitError, APIError) as e:
            log.warning("[omniroute] generate_reply rate/API error: %s", e)
        except Exception as e:
            log.error("[omniroute] generate_reply error: %s: %s", type(e).__name__, e)
    return None


async def generate_recruitment_reply(
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
    source: str = "unknown",
) -> Optional[str]:
    """Recruitment reply via OmniRoute. Returns None on failure."""
    settings = get_settings()
    model = settings.omniroute_model_name
    prompt = build_whatsapp_recruitment_policy(
        source=source,
        user_message=user_message,
        kb_context=kb_context,
        history=history,
        contact_context=contact_context,
    )
    t0 = time.monotonic()
    async with _omniroute_sem:
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
            log.warning("[omniroute] recruitment rate/API error: %s", e)
        except Exception as e:
            log.error("[omniroute] recruitment error: %s: %s", type(e).__name__, e)
    return None


async def check_health() -> dict:
    settings = get_settings()
    if not settings.omniroute_api_key:
        return {"status": "disabled", "reason": "OMNIROUTE_API_KEY not set"}
    try:
        client = _make_client()
        t0 = time.monotonic()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.omniroute_model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            ),
            timeout=10.0,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"status": "ok", "model": settings.omniroute_model_name, "latency_ms": latency_ms}
    except Exception as e:
        return {"status": "error", "error": str(e)}
