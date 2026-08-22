"""
Fazle Core — MiniMax AI Client (Isolated)

Pattern: follows app/groq_provider.py for caller-compatible interface,
AsyncOpenAI usage, and error handling.

Isolated: NOT wired into app/llm.py's active generation chain in Phase 2A.
Provider file exists so the implementation, error paths, and unit tests
can be verified without affecting OmniRoute/Groq/local/Kimi behavior.
"""
import asyncio
import json
import logging
import time
from typing import Optional

from openai import AsyncOpenAI, RateLimitError, APIError, APITimeoutError

from app.config import get_settings
from shared.reply_policy import (
    build_whatsapp_reply_policy,
    build_whatsapp_recruitment_policy,
)

log = logging.getLogger("fazle.minimax")

_minimax_sem = asyncio.Semaphore(2)

# Minimum configuration required to even build the client. If any of these
# are missing, the provider refuses to construct — caller treats it like
# any other provider-not-configured path (returns None / no reply) rather
# than crashing the surrounding request.
def _make_client() -> AsyncOpenAI:
    settings = get_settings()
    if not settings.minimax_api_key:
        raise ValueError("MINIMAX_API_KEY not configured")
    if not settings.minimax_base_url:
        raise ValueError("MINIMAX_BASE_URL not configured")
    return AsyncOpenAI(
        api_key=settings.minimax_api_key,
        base_url=settings.minimax_base_url,
    )


async def generate_reply(
    user_message: str,
    intent: str,
    db_context: str = "",
    history: str = "",
    role: str = "new_lead",
    source: str = "unknown",
) -> Optional[str]:
    """Normal WhatsApp reply via MiniMax. Returns None on any failure."""
    settings = get_settings()
    model = settings.minimax_model_name

    prompt = build_whatsapp_reply_policy(
        source=source,
        user_message=user_message,
        role=role,
        intent=intent,
        db_context=db_context,
        history=history,
    )

    t0 = time.monotonic()
    async with _minimax_sem:
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
            log.info("[minimax] generate_reply OK latency=%dms intent=%s", latency_ms, intent)
            return reply_text
        except asyncio.TimeoutError as e:
            log.warning("[minimax] generate_reply timeout: %s", type(e).__name__)
        except APITimeoutError as e:
            log.warning("[minimax] generate_reply api timeout: %s", e)
        except RateLimitError as e:
            log.warning("[minimax] generate_reply rate limit: %s", e)
        except APIError as e:
            log.warning("[minimax] generate_reply api error: %s", e)
        except ValueError as e:
            # _make_client() refused: missing configuration. Log reason category only.
            log.warning("[minimax] generate_reply config missing: %s", type(e).__name__)
        except Exception as e:
            log.error("[minimax] generate_reply unexpected: %s: %s", type(e).__name__, e)
    return None


async def generate_recruitment_reply(
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
    source: str = "unknown",
) -> Optional[str]:
    """Recruitment-specific reply via MiniMax. Returns None on any failure."""
    settings = get_settings()
    model = settings.minimax_model_name

    prompt = build_whatsapp_recruitment_policy(
        source=source,
        user_message=user_message,
        kb_context=kb_context,
        history=history,
        contact_context=contact_context,
    )

    t0 = time.monotonic()
    async with _minimax_sem:
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
            log.info("[minimax] generate_recruitment_reply OK latency=%dms", latency_ms)
            return reply_text
        except asyncio.TimeoutError as e:
            log.warning("[minimax] generate_recruitment_reply timeout: %s", type(e).__name__)
        except APITimeoutError as e:
            log.warning("[minimax] generate_recruitment_reply api timeout: %s", e)
        except RateLimitError as e:
            log.warning("[minimax] generate_recruitment_reply rate limit: %s", e)
        except APIError as e:
            log.warning("[minimax] generate_recruitment_reply api error: %s", e)
        except ValueError as e:
            log.warning("[minimax] generate_recruitment_reply config missing: %s", type(e).__name__)
        except Exception as e:
            log.error("[minimax] generate_recruitment_reply unexpected: %s: %s", type(e).__name__, e)
    return None


async def check_health() -> dict:
    """Lightweight ping. Never raises."""
    settings = get_settings()
    if not settings.minimax_api_key or not settings.minimax_base_url or not settings.minimax_model_name:
        return {"status": "disabled", "reason": "minimax_api_key/base_url/model not configured"}
    try:
        client = _make_client()
        t0 = time.monotonic()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=settings.minimax_model_name,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            ),
            timeout=10.0,
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {"status": "ok", "model": settings.minimax_model_name, "latency_ms": latency_ms}
    except Exception as e:
        return {"status": "error", "error": str(e)}