"""
Fazle Core — Unified LLM Interface

Reply chain (2026-08-05, Owner decision — supersedes the prior Ollama-first
order): OmniRoute -> Groq -> local Ollama/Hermes -> Kimi.
  - OmniRoute: fazle-core's first use of the same local AI gateway
    hermes-runner's own ~/.hermes/config.yaml already points the real
    Hermes CLI at (http://127.0.0.1:20128/v1) -- see app/omniroute_provider.py.
  - Groq: unchanged provider, moved to 2nd step.
  - local Ollama: prefers a locally-provisioned Hermes-family model if
    available (checked live, cached briefly -- see
    _resolve_local_ollama_model(); candidates in smallest-first order:
    hermes3:3b, hermes3, hermes3:latest, hermes, hermes:latest -- "hermes3"
    is the real Ollama-library family name, "hermes"/"hermes:latest" kept
    as aliases in case a model is ever tagged that way manually), otherwise
    settings.ollama_model unchanged (currently qwen3:8b). Skipped entirely
    if OLLAMA_REPLY_DISABLED=true, same as before. 2026-08-05 server check:
    no Hermes-family model is provisioned yet -- see the Owner-facing report
    for the exact `ollama pull hermes3:3b` command, not run automatically.
  - Kimi: reached through the SAME local Ollama client, using
    settings.kimi_model_name (default kimi-k2.6:cloud, Ollama's own
    cloud-model proxy -- confirmed already provisioned on this server).
    Not a separate HTTP provider.
GitHub Models is intentionally no longer part of this chain (2026-08-05 —
see app/github_models.py's own confirmed-dead-endpoint note; classify_intent_llm()
and generate_chat_reply() below are a different, unaffected scope and still
reference it).

If every step fails, the existing per-caller fallback text is returned
(_FALLBACK_REPLY / app.ollama.RECRUITMENT_FALLBACK_TEXT) -- callers that
need to know a total-failure occurred (vs. a genuine reply) can call
get_last_attempts() right after awaiting generate_reply()/
generate_recruitment_reply() for the full per-provider attempt trace,
used by modules.social_auto_reply for its admin-review notification.

Provider selection: PRIMARY_AI_PROVIDER remains accepted for compatibility
but is not read by the functions below (retained only for check_health()'s
reporting and any external readers of the setting).

message_router imports this as `ai`:
    from app import llm as ai
"""
import asyncio
import contextvars
import logging
import time
from typing import Optional

from app.config import get_settings

log = logging.getLogger("fazle.llm")

_FALLBACK_REPLY = "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।"

# ── Per-call provider-attempt trace (2026-08-05) ───────────────────────────
# A contextvar (not a plain module list) so concurrent calls in different
# asyncio tasks never see each other's attempts -- reset at the start of
# generate_reply()/generate_recruitment_reply(), readable immediately after
# by the same task via get_last_attempts(). Purely additive: no existing
# caller's signature or return type changes; callers that don't need the
# trace (the large majority) are completely unaffected.
_LAST_ATTEMPTS: contextvars.ContextVar[list] = contextvars.ContextVar("_LAST_ATTEMPTS", default=[])


def get_last_attempts() -> list[dict]:
    """Provider-attempt trace (provider, model, outcome, detail) from the
    most recent generate_reply()/generate_recruitment_reply() call in the
    CURRENT asyncio task. Empty list if none recorded (e.g. called from a
    different task, or before any generate_* call this task)."""
    return list(_LAST_ATTEMPTS.get())


def _record_attempt(provider: str, model: str, outcome: str, detail: str = "") -> None:
    attempts = list(_LAST_ATTEMPTS.get())
    attempts.append({"provider": provider, "model": model, "outcome": outcome, "detail": detail[:200]})
    _LAST_ATTEMPTS.set(attempts)


# ── Local Hermes-family model preference (2026-08-05) ──────────────────────
# Cached briefly so a live GET /api/tags isn't made on every single reply --
# model provisioning on this server doesn't change often, and the Owner's
# own instruction is explicit that this must never break production if the
# model isn't provisioned (falls back to settings.ollama_model unchanged).
_hermes_model_cache: dict = {"model": None, "checked_at": 0.0}
_HERMES_MODEL_CACHE_TTL_S = 300  # 5 minutes


_HERMES_MODEL_CANDIDATES = (
    # Smallest-first: prefer the smallest Hermes-family model actually
    # provisioned. "hermes3" is Nous Research's real Ollama-library family
    # name (confirmed via ollama.com/library/hermes3, 2026-08-05: 3b/8b/70b/
    # 405b tags exist) -- bare "hermes"/"hermes:latest" kept as aliases in
    # case a model is ever pulled/tagged that way manually instead.
    "hermes3:3b",
    "hermes3",
    "hermes3:latest",
    "hermes",
    "hermes:latest",
)


async def _resolve_local_ollama_model() -> str:
    """Prefer the smallest locally-provisioned Hermes-family model (checked
    live via GET /api/tags, matching the Owner's explicit "you MUST inspect
    the actual currently available Ollama models first" instruction) --
    otherwise settings.ollama_model, completely unchanged. Never raises; any
    failure to check availability silently falls back to the existing,
    already-working configured model. No model is ever pulled/installed by
    this function -- if none of _HERMES_MODEL_CANDIDATES is provisioned,
    production behavior is unchanged."""
    settings = get_settings()
    now = time.monotonic()
    cached = _hermes_model_cache["model"]
    if cached and (now - _hermes_model_cache["checked_at"]) < _HERMES_MODEL_CACHE_TTL_S:
        return cached
    resolved = settings.ollama_model
    try:
        from app import ollama_daemon
        resp = await ollama_daemon.get_tags(timeout=5.0)
        if resp.status_code == 200:
            names = {m.get("name") for m in resp.json().get("models", [])}
            for candidate in _HERMES_MODEL_CANDIDATES:
                if candidate in names:
                    resolved = candidate
                    break
    except Exception as e:
        log.debug("[llm] hermes-model availability check failed, using settings.ollama_model: %s", e)
    _hermes_model_cache["model"] = resolved
    _hermes_model_cache["checked_at"] = now
    return resolved


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
    Classify intent via Ollama → Groq → GitHub Models.

    2026-08-05: intent classification is out of scope for the reply-
    generation/polish chain change — left untouched deliberately, still
    references github_models (which itself still functions if reached;
    only its health-check ping was disabled, see app/github_models.py).
    """
    settings = get_settings()

    try:
        from app import ollama
        result = await ollama.classify_intent_llm(text)
        if result != "unknown":
            return result
    except Exception as e:
        log.warning("[llm] ollama classify failed, falling back to groq/github_models: %s", e)

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
            log.warning("[llm] github_models classify failed: %s", e)

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
    Generate a WhatsApp reply. OmniRoute → Groq → local Ollama/Hermes → Kimi.
    Every successful reply is logged to llm_learning_memory. Every attempt
    (success, empty, error, or skipped) is recorded — see get_last_attempts().
    """
    settings = get_settings()
    _LAST_ATTEMPTS.set([])

    if settings.omniroute_api_key:
        try:
            from app import omniroute_provider
            reply = await omniroute_provider.generate_reply(
                user_message=user_message,
                intent=intent,
                db_context=db_context,
                history=history,
                role=role,
                source=source,
            )
            if reply:
                _record_attempt("omniroute", settings.omniroute_model_name, "success")
                asyncio.create_task(_save_to_memory(
                    provider="omniroute",
                    model=settings.omniroute_model_name,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent=intent,
                    role=role,
                    source=source,
                    context_used=db_context,
                    is_fallback=False,
                ))
                return reply
            _record_attempt("omniroute", settings.omniroute_model_name, "empty_reply")
            log.warning("[llm] omniroute returned empty reply, falling back to groq")
        except Exception as e:
            _record_attempt("omniroute", settings.omniroute_model_name, "error", str(e))
            log.warning("[llm] omniroute generate failed, falling back to groq: %s: %s", type(e).__name__, e)
    else:
        _record_attempt("omniroute", settings.omniroute_model_name, "skipped_not_configured")

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
                _record_attempt("groq", settings.groq_model_name, "success")
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
            _record_attempt("groq", settings.groq_model_name, "empty_reply")
            log.warning("[llm] groq returned empty reply, falling back to local ollama/hermes")
        except Exception as e:
            _record_attempt("groq", settings.groq_model_name, "error", str(e))
            log.warning("[llm] groq generate failed, falling back to local ollama/hermes: %s", e)
    else:
        _record_attempt("groq", settings.groq_model_name, "skipped_not_configured")

    if not settings.ollama_reply_disabled:
        try:
            from app import ollama
            local_model = await _resolve_local_ollama_model()
            reply = await ollama.generate_reply(
                user_message=user_message,
                intent=intent,
                db_context=db_context,
                history=history,
                role=role,
                source=source,
                model=local_model,
            )
            # ollama.generate_reply() always returns non-empty text (even on
            # failure it returns its own FALLBACK_REPLY_TEXT) — compare
            # against that exact constant to detect a real failure vs. a
            # genuine reply, so the chain correctly continues to Kimi
            # instead of treating Ollama's own internal fallback as success.
            if reply and reply != ollama.FALLBACK_REPLY_TEXT:
                _record_attempt("ollama", local_model, "success")
                asyncio.create_task(_save_to_memory(
                    provider="ollama",
                    model=local_model,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent=intent,
                    role=role,
                    source=source,
                    context_used=db_context,
                    is_fallback=False,
                ))
                return reply
            _record_attempt("ollama", local_model, "empty_or_internal_fallback")
            log.warning("[llm] local ollama/hermes returned no real reply, falling back to kimi")
        except Exception as e:
            _record_attempt("ollama", settings.ollama_model, "error", str(e))
            log.error("[llm] local ollama/hermes generate failed, falling back to kimi: %s", e)
    else:
        _record_attempt("ollama", settings.ollama_model, "skipped_disabled")

    try:
        from app import ollama
        reply = await ollama.generate_reply(
            user_message=user_message,
            intent=intent,
            db_context=db_context,
            history=history,
            role=role,
            source=source,
            model=settings.kimi_model_name,
        )
        if reply and reply != ollama.FALLBACK_REPLY_TEXT:
            _record_attempt("kimi", settings.kimi_model_name, "success")
            asyncio.create_task(_save_to_memory(
                provider="kimi",
                model=settings.kimi_model_name,
                trigger_text=user_message,
                reply_text=reply,
                intent=intent,
                role=role,
                source=source,
                context_used=db_context,
                is_fallback=True,
            ))
            return reply
        _record_attempt("kimi", settings.kimi_model_name, "empty_or_internal_fallback")
        log.warning("[llm] kimi returned no real reply — all providers exhausted")
    except Exception as e:
        _record_attempt("kimi", settings.kimi_model_name, "error", str(e))
        log.error("[llm] kimi generate failed — all providers exhausted: %s", e)

    return _FALLBACK_REPLY


async def generate_recruitment_reply(
    user_message: str,
    kb_context: str,
    history: str = "",
    contact_context: str = "",
    source: str = "unknown",
) -> str:
    """Recruitment-specific reply. OmniRoute → Groq → local Ollama/Hermes → Kimi → safe fallback."""
    settings = get_settings()
    _LAST_ATTEMPTS.set([])

    if settings.omniroute_api_key:
        try:
            from app import omniroute_provider
            reply = await omniroute_provider.generate_recruitment_reply(
                user_message=user_message,
                kb_context=kb_context,
                history=history,
                contact_context=contact_context,
                source=source,
            )
            if reply:
                _record_attempt("omniroute", settings.omniroute_model_name, "success")
                asyncio.create_task(_save_to_memory(
                    provider="omniroute",
                    model=settings.omniroute_model_name,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent="recruitment",
                    source=source,
                    context_used=kb_context,
                    is_fallback=False,
                ))
                return reply
            _record_attempt("omniroute", settings.omniroute_model_name, "empty_reply")
            log.warning("[llm] omniroute recruitment returned empty, falling back to groq")
        except Exception as e:
            _record_attempt("omniroute", settings.omniroute_model_name, "error", str(e))
            log.warning("[llm] omniroute recruitment failed, falling back to groq: %s", e)
    else:
        _record_attempt("omniroute", settings.omniroute_model_name, "skipped_not_configured")

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
                _record_attempt("groq", settings.groq_model_name, "success")
                asyncio.create_task(_save_to_memory(
                    provider="groq",
                    model=settings.groq_model_name,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent="recruitment",
                    source=source,
                    context_used=kb_context,
                    is_fallback=True,
                ))
                return reply
            _record_attempt("groq", settings.groq_model_name, "empty_reply")
            log.warning("[llm] groq recruitment returned empty, falling back to local ollama/hermes")
        except Exception as e:
            _record_attempt("groq", settings.groq_model_name, "error", str(e))
            log.warning("[llm] groq recruitment failed, falling back to local ollama/hermes: %s", e)
    else:
        _record_attempt("groq", settings.groq_model_name, "skipped_not_configured")

    if not settings.ollama_reply_disabled:
        try:
            from app import ollama
            local_model = await _resolve_local_ollama_model()
            reply = await ollama.generate_recruitment_reply(
                user_message=user_message,
                kb_context=kb_context,
                history=history,
                contact_context=contact_context,
                source=source,
                model=local_model,
            )
            if reply and reply != ollama.RECRUITMENT_FALLBACK_TEXT:
                _record_attempt("ollama", local_model, "success")
                asyncio.create_task(_save_to_memory(
                    provider="ollama",
                    model=local_model,
                    trigger_text=user_message,
                    reply_text=reply,
                    intent="recruitment",
                    source=source,
                    context_used=kb_context,
                    is_fallback=False,
                ))
                return reply
            _record_attempt("ollama", local_model, "empty_or_internal_fallback")
            log.warning("[llm] local ollama/hermes recruitment returned no real reply, falling back to kimi")
        except Exception as e:
            _record_attempt("ollama", settings.ollama_model, "error", str(e))
            log.warning("[llm] local ollama/hermes recruitment failed, falling back to kimi: %s", e)
    else:
        _record_attempt("ollama", settings.ollama_model, "skipped_disabled")

    try:
        from app import ollama
        reply = await ollama.generate_recruitment_reply(
            user_message=user_message,
            kb_context=kb_context,
            history=history,
            contact_context=contact_context,
            source=source,
            model=settings.kimi_model_name,
        )
        if reply and reply != ollama.RECRUITMENT_FALLBACK_TEXT:
            _record_attempt("kimi", settings.kimi_model_name, "success")
            asyncio.create_task(_save_to_memory(
                provider="kimi",
                model=settings.kimi_model_name,
                trigger_text=user_message,
                reply_text=reply,
                intent="recruitment",
                source=source,
                context_used=kb_context,
                is_fallback=True,
            ))
            return reply
        _record_attempt("kimi", settings.kimi_model_name, "empty_or_internal_fallback")
        log.warning("[llm] kimi recruitment returned no real reply — all providers exhausted")
    except Exception as e:
        _record_attempt("kimi", settings.kimi_model_name, "error", str(e))
        log.error("[llm] kimi recruitment failed — all providers exhausted: %s", e)

    return "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"


async def generate_chat_reply(
    question: str,
    context: str,
    history: list[dict],
    model: str | None = None,
) -> Optional[str]:
    """
    Unified Chat Lab reply router. Ollama → Groq → GitHub Models fallback.

    2026-08-05: the admin Chat Lab path is out of scope for the reply-
    generation/polish chain change — left untouched deliberately.
    """
    settings = get_settings()

    if not settings.ollama_reply_disabled:
        try:
            from app import ollama
            reply = await ollama.generate_chat_reply(
                question=question,
                context=context,
                history=history,
                model=model,
            )
            if reply:
                return reply
            log.warning("[llm] ollama chat reply empty, falling back to groq/github_models")
        except Exception as e:
            log.warning("[llm] ollama chat reply failed, falling back to groq/github_models: %s", e)
    else:
        log.info("[llm] ollama_reply_disabled=True — skipping ollama chat primary")

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

    if settings.github_token:
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
            log.warning("[llm] github_models chat reply empty")
        except Exception as e:
            log.warning("[llm] github_models chat reply failed: %s", e)
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
        "omniroute_configured": bool(settings.omniroute_api_key),
    }
    try:
        from app import omniroute_provider
        result["omniroute"] = await omniroute_provider.check_health()
    except Exception as e:
        result["omniroute"] = {"status": "error", "error": str(e)}
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
