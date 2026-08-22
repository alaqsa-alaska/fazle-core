"""
Fazle Core — MiniMax AI Client

Pattern: follows app/groq_provider.py for caller-compatible interface,
AsyncOpenAI usage, and error handling.

Phase 2A (2026-08-19..21): isolated, NOT wired into app/llm.py's active
generation chain -- provider file existed so the implementation, error
paths, and unit tests could be verified without affecting
OmniRoute/Groq/local/Kimi behavior.

Task 13 MiniMax-primary promotion (2026-08-22, BUILD+VERIFY, flag-gated):
now called from modules/hermes_dispatch.py's semantic_gate()/generate_reply()/
generate_recruitment_reply(), gated behind the `minimax_customer_primary`
feature flag (default OFF) -- see that module for the primary-then-fallback
wiring. Still not wired into app/llm.py's own chain; that remains
out of scope. This file's own functions are unchanged in their own
call/return contract.
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


async def generate_raw_completion(
    prompt: str,
    *,
    max_tokens: int = 300,
    temperature: float = 0.0,
) -> Optional[str]:
    """Task 13 (2026-08-22, MiniMax-primary promotion): a raw, unwrapped
    single-turn completion -- no shared.reply_policy prompt-building, the
    caller supplies the complete prompt text itself (e.g. hermes_dispatch's
    own _SEMANTIC_GATE_POLICY + evidence JSON for the semantic classifier,
    which is not a "customer reply" and must not be run through
    build_whatsapp_reply_policy()/build_whatsapp_recruitment_policy()).

    Same client construction, semaphore, timeout, and error-handling shape
    as generate_reply()/generate_recruitment_reply() above -- this is
    still the isolated, tool-less MiniMax client; it gains no tool access,
    no admin/system prompt, no privileged session just because the prompt
    text differs. Returns None on any failure (never raises), exactly like
    every other function in this module -- callers must fall through to
    their own existing fallback path unchanged."""
    settings = get_settings()
    model = settings.minimax_model_name

    t0 = time.monotonic()
    async with _minimax_sem:
        try:
            client = _make_client()
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                timeout=25.0,
            )
            reply_text = (resp.choices[0].message.content or "").strip()
            latency_ms = int((time.monotonic() - t0) * 1000)
            log.info("[minimax] generate_raw_completion OK latency=%dms", latency_ms)
            return reply_text
        except asyncio.TimeoutError as e:
            log.warning("[minimax] generate_raw_completion timeout: %s", type(e).__name__)
        except APITimeoutError as e:
            log.warning("[minimax] generate_raw_completion api timeout: %s", e)
        except RateLimitError as e:
            log.warning("[minimax] generate_raw_completion rate limit: %s", e)
        except APIError as e:
            log.warning("[minimax] generate_raw_completion api error: %s", e)
        except ValueError as e:
            log.warning("[minimax] generate_raw_completion config missing: %s", type(e).__name__)
        except Exception as e:
            log.error("[minimax] generate_raw_completion unexpected: %s: %s", type(e).__name__, e)
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