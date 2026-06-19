"""
Fazle Core — Unified LLM Interface

Reply chain:  GitHub Models (primary) → Groq (secondary) → Ollama (fallback)
              When OLLAMA_REPLY_DISABLED=true the chain is:
              GitHub Models → Groq → polite holding message

Provider selection: PRIMARY_AI_PROVIDER env var ("github_models" | "ollama").
Groq is always tried as a secondary when GROQ_API_KEY is set.

message_router imports this as `ai`:
    from app import llm as ai
"""
import asyncio
import logging
from typing import Optional

from app.config import get_settings

log = logging.getLogger("fazle.llm")

_FALLBACK_REPLY = "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।"


async def _save_to_memory(
    provider: str,
    model: str,
    trigger_text: str,
    reply_text: str,
    intent: str = "",
    role: str = "",
    source: str = "",
    context_used: str = "",
    is_fallback: bool = False,
) -> None:
    """Fire-and-forget INSERT into llm_learning_memory. Never raises."""
    try:
        from app.database import execute as _execute
        await _execute(
            """INSERT INTO llm_learning_memory
               (provider, model, trigger_text, intent, role, source,
                context_used, reply_text, is_fallback)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
            provider, model,
            trigger_text[:1000], intent or None, role or None, source or None,
            context_used[:2000] if context_used else None,
            reply_text[:2000],
            is_fallback,
        )
    except Exception:
        pass


async def classify_intent_llm(text: str) -> str:
    """
    Classify intent via Groq → GitHub Models → Ollama.
    """
    settings = get_settings()

    if settings.groq_api_key:
        try:
            from app import groq_provider
            result = await groq_provider.classify_intent_llm(text)
            if result != "unknown":
                return result
        except Exception as e:
            log.warning("[llm] groq classify failed, falling back to github_models: %s", e)

    if settings.github_token:
        try:
            from app import github_models
            result = await github_models.classify_intent_llm(text)
            if result != "unknown":
                return result
        except Exception as e:
            log.warning("[llm] github_models classify failed, falling back to ollama: %s", e)

    try:
        from app import ollama
        return await ollama.classify_intent_llm(text)
    except Exception as e:
        log.error("[llm] ollama classify also failed: %s", e)
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
    Generate a WhatsApp reply.  GitHub Models → Ollama fallback.
    Every successful reply is logged to llm_learning_memory.
    """
    settings = get_settings()
    provider = settings.primary_ai_provider

    if provider == "github_models":
        try:
            from app import github_models
            reply = await github_models.generate_reply(
                user_message=user_message,
                intent=intent,
                db_context=db_context,
                history=history,
                role=role,
                source=source,
            )
            if reply:
                asyncio.create_task(_save_to_memory(
                    provider="github_models",
                    model=settings.github_model_name,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent=intent,
                    role=role,
                    source=source,
                    context_used=db_context,
                ))
                return reply
            log.warning("[llm] github_models returned empty reply, falling back to groq/ollama")
        except Exception as e:
            log.warning("[llm] github_models generate failed, falling back to groq/ollama: %s", e)

    # Groq secondary (tried whenever GitHub Models fails, regardless of provider setting)
    if settings.groq_api_key:
        try:
            from app import groq_provider
            reply = await groq_provider.generate_reply(
                user_message=user_message,
                intent=intent,
                db_context=db_context,
                history=history,
                role=role,
                source=source,
            )
            if reply:
                asyncio.create_task(_save_to_memory(
                    provider="groq",
                    model=settings.groq_model_name,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent=intent,
                    role=role,
                    source=source,
                    context_used=db_context,
                    is_fallback=True,
                ))
                return reply
            log.warning("[llm] groq returned empty reply, falling through")
        except Exception as e:
            log.warning("[llm] groq generate failed: %s", e)

    # Ollama fallback (or primary if provider=ollama)
    if settings.ollama_reply_disabled:
        log.info("[llm] ollama_reply_disabled=True — returning holding message")
        return "আপাতত সার্ভিস একটু ব্যস্ত। কিছুক্ষণ পর আবার চেষ্টা করুন অথবা সরাসরি অফিসে যোগাযোগ করুন।"
    try:
        from app import ollama
        reply = await ollama.generate_reply(
            user_message=user_message,
            intent=intent,
            db_context=db_context,
            history=history,
            role=role,
            source=source,
        )
        asyncio.create_task(_save_to_memory(
            provider="ollama",
            model=settings.ollama_model,
            trigger_text=user_message,
            reply_text=reply,
            intent=intent,
            role=role,
            source=source,
            context_used=db_context,
            is_fallback=(provider == "github_models"),  # True when ollama is the fallback
        ))
        return reply
    except Exception as e:
        log.error("[llm] ollama generate also failed: %s", e)

    return _FALLBACK_REPLY


async def generate_recruitment_reply(
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
    source: str = "unknown",
) -> str:
    """Recruitment-specific reply. Groq → GitHub Models → Ollama → safe fallback."""
    settings = get_settings()

    if settings.groq_api_key:
        try:
            from app import groq_provider
            reply = await groq_provider.generate_recruitment_reply(
                user_message=user_message,
                kb_context=kb_context,
                history=history,
                contact_context=contact_context,
                source=source,
            )
            if reply:
                asyncio.create_task(_save_to_memory(
                    provider="groq",
                    model=settings.groq_model_name,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent="recruitment",
                    source=source,
                    context_used=kb_context,
                ))
                return reply
            log.warning("[llm] groq recruitment returned empty, falling back to github_models")
        except Exception as e:
            log.warning("[llm] groq recruitment failed, falling back to github_models: %s", e)

    if settings.github_token:
        try:
            from app import github_models
            reply = await github_models.generate_recruitment_reply(
                user_message=user_message,
                kb_context=kb_context,
                history=history,
                contact_context=contact_context,
                source=source,
            )
            if reply:
                asyncio.create_task(_save_to_memory(
                    provider="github_models",
                    model=settings.github_model_name,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent="recruitment",
                    source=source,
                    context_used=kb_context,
                    is_fallback=True,
                ))
                return reply
            log.warning("[llm] github_models recruitment returned empty, falling back to ollama")
        except Exception as e:
            log.warning("[llm] github_models recruitment failed: %s", e)

    if not settings.ollama_reply_disabled:
        try:
            from app import ollama
            reply = await ollama.generate_recruitment_reply(
                user_message=user_message,
                kb_context=kb_context,
                history=history,
                contact_context=contact_context,
                source=source,
            )
            if reply:
                asyncio.create_task(_save_to_memory(
                    provider="ollama",
                    model=settings.ollama_model,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent="recruitment",
                    source=source,
                    context_used=kb_context,
                    is_fallback=True,
                ))
                return reply
        except Exception as e:
            log.warning("[llm] ollama recruitment failed: %s", e)

    return "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"


async def generate_chat_reply(
    question: str,
    context: str,
    history: list[dict],
    model: str | None = None,
) -> Optional[str]:
    """
    Unified Chat Lab reply router. GitHub Models → Groq → Ollama fallback.
    """
    settings = get_settings()
    provider = settings.primary_ai_provider

    if provider == "github_models":
        try:
            from app import github_models
            reply = await github_models.generate_chat_reply(
                question=question,
                context=context,
                history=history,
                model=model,
            )
            if reply:
                return reply
            log.warning("[llm] github_models chat reply empty, falling back to groq/ollama")
        except Exception as e:
            log.warning("[llm] github_models chat reply failed, falling back to groq/ollama: %s", e)

    # Groq secondary
    if settings.groq_api_key:
        try:
            from app import groq_provider
            reply = await groq_provider.generate_chat_reply(
                question=question,
                context=context,
                history=history,
                model=None,  # always use groq's own model
            )
            if reply:
                return reply
            log.warning("[llm] groq chat reply empty, falling through")
        except Exception as e:
            log.warning("[llm] groq chat reply failed: %s", e)

    if settings.ollama_reply_disabled:
        log.info("[llm] ollama_reply_disabled=True — skipping ollama chat fallback")
        return None
    try:
        from app import ollama
        return await ollama.generate_chat_reply(
            question=question,
            context=context,
            history=history,
            model=model,
        )
    except Exception as e:
        log.error("[llm] ollama chat reply also failed: %s", e)
    return None


async def check_ollama_health() -> dict:
    """Pass-through so callers using `llm as ai` still work."""
    from app import ollama
    return await ollama.check_ollama_health()


async def generate_rag_answer(
    question: str,
    context: str,
    model: str | None = None,
) -> Optional[str]:
    """Pass-through so callers using `llm as ai` still work."""
    from app import ollama
    return await ollama.generate_rag_answer(question=question, context=context, model=model)


async def check_health() -> dict:
    """Health check for all providers."""
    settings = get_settings()
    result = {
        "primary_provider": settings.primary_ai_provider,
        "ollama_reply_disabled": settings.ollama_reply_disabled,
        "groq_configured": bool(settings.groq_api_key),
    }
    try:
        from app import github_models
        result["github_models"] = await github_models.check_health()
    except Exception as e:
        result["github_models"] = {"status": "error", "error": str(e)}
    if settings.groq_api_key:
        try:
            from app import groq_provider
            result["groq"] = await groq_provider.check_health()
        except Exception as e:
            result["groq"] = {"status": "error", "error": str(e)}
    else:
        result["groq"] = {"status": "disabled", "reason": "GROQ_API_KEY not set"}
    try:
        from app import ollama
        result["ollama"] = await ollama.check_ollama_health()
    except Exception as e:
        result["ollama"] = {"status": "error", "error": str(e)}
    return result
