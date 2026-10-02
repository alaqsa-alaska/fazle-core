"""LLM bridge for RecruitmentTurnDecision (Phase C / WS1, 2026-08-25).

Architectural classification: **B -- ACCEPTABLE LOW-LEVEL PROVIDER
ABSTRACTION USED BY HERMES TOO**.

Provides make_decision_llm_call() — a factory that returns an async
closure matching decide.py's DecisionLLMCall signature, with the
following resolution order (per the 2026-08-25 mandatory check):

  1. Hermes via modules.hermes_dispatch.call_decision_llm()
     -- caller_scope="customer", bearer HERMES_RUNNER_CUSTOMER_SECRET,
        same shape as the existing semantic_gate()'s structured
        classification call. Hermes (hermes-runner) forwards the prompt
        to the OmniRoute local gateway (http://127.0.0.1:20128/v1) --
        the same gateway app.omniroute_provider talks to in fallback
        mode. Same model under the hood; same structured prompt
        protocol. CHOSEN as primary because that is the canonical
        model-resolution abstraction for the recruitment domain.

  2. Provider-chain fallback via app.llm.decision_call()
     -- reached ONLY when hermes-runner is not configured (URL or
        secret missing in app/config), so the system never silently
        loses the capability to make a structured semantic decision.
        Keeps the existing fail-closed semantics: None on every
        failure, never raises.

Gated by app.config.recruitment_turn_decision_v2_real_llm AND
recruitment_turn_decision_v2_enabled — both must be True for the
returned closure to engage a real model. When the real-LLM flag is
False, decide() falls back to its existing _default_no_llm stub
(AMBIGUOUS), keeping production byte-identical until the Owner flips
the flag.

The closure is intentionally injected by decide() (not called directly
in the orchestrator) so every existing unit test that supplies a fake
llm_call keeps working unchanged.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)


# Mirror of decide.DecisionLLMCall so consumers can import either name.
DecisionLLMCall = Callable[[str, str], Awaitable[Optional[str]]]


def make_decision_llm_call(
    *,
    timeout: float = 12.0,
    max_tokens: int = 300,
    temperature: float = 0.1,
    sender_phone: str = "",
) -> DecisionLLMCall:
    """Return an async closure (system_prompt, user_payload) -> Optional[str].

    The closure prefers modules.hermes_dispatch.call_decision_llm()
    (the canonical structured-classification path). When hermes-runner
    is not configured, falls through to app.llm.decision_call()'s
    provider-chain (OmniRoute -> Groq -> local Ollama/Hermes -> Kimi)
    so the system still has a real decision caller in environments
    where hermes-runner is not yet deployed (e.g. a unit-test sandbox,
    a brand-new dev box).

    NEVER raises. NEVER logs the prompt body.
    """
    async def _call(system_prompt: str, user_payload: str) -> Optional[str]:
        # 1. Hermes (canonical structured-classification path)
        try:
            from modules.hermes_dispatch import call_decision_llm as _hermes_call
            text = await _hermes_call(
                system_prompt=system_prompt,
                user_payload=user_payload,
                sender_phone=sender_phone,
            )
            if text:
                return text
            # None from Hermes with hermes-runner CONFIGURED = "no real
            # answer"; never silently substitute the provider chain
            # (the directive forbids creating a parallel stack that
            # bypasses Hermes). When Hermes is NOT configured, _configured
            # _present() returns (False, False) and we proceed to the
            # provider-chain fallback.
            try:
                base_url, secret = _configured_present()
                if base_url and secret:
                    log.debug(
                        "[recruitment_decision_bridge] hermes empty/timeout; "
                        "returning None (not substituting provider chain)"
                    )
                    return None
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001 -- bridge must never raise
            log.warning("[recruitment_decision_bridge] hermes_call failed: %s", exc)
        # 2. Provider-chain fallback (only reached when (a) Hermes is not
        # configured AND (b) recruitment_turn_decision_v2_direct_provider_
        # fallback is explicitly on). Production default for the fallback
        # flag is False, so a configured-but-failing Hermes call returns
        # None the moment Hermes times out -- it never silently bypasses
        # Hermes. Tests/dev/shadow can flip the fallback on to exercise
        # the provider chain independently of Hermes.
        try:
            from app.config import get_settings as _bridge_settings
            direct_fb = bool(_bridge_settings().recruitment_turn_decision_v2_direct_provider_fallback)
        except Exception:  # noqa: BLE001
            direct_fb = False
        if not direct_fb:
            log.debug(
                "[recruitment_decision_bridge] hermes not configured + "
                "direct_provider_fallback=False; returning None (no "
                "production provider chain)"
            )
            return None
        try:
            from app import llm as _llm
            text = await _llm.decision_call(
                system_prompt=system_prompt,
                user_payload=user_payload,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=timeout,
            )
            return text
        except Exception as exc:  # noqa: BLE001 -- bridge must never raise
            log.warning("[recruitment_decision_bridge] provider_chain failed: %s", exc)
            return None

    return _call


def _configured_present() -> tuple[str, str]:
    """Return (base_url, secret) for the hermes-runner customer channel.
    Empty strings mean "not configured". Used by make_decision_llm_call()
    to decide whether an empty/timeout Hermes response means "fall back
    to the provider chain" or "Hermes is configured but didn't answer,
    return None".

    Fails closed (both empty) on any error so decide() then exercises
    its existing _default_no_llm branch -- preserving pre-WS1 behavior
    whenever the feature can't be safely engaged."""
    try:
        from app.config import get_settings
        s = get_settings()
        base_url = (getattr(s, "hermes_runner_url", "") or "").rstrip("/")
        secret = getattr(s, "hermes_runner_customer_secret", "") or ""
        return base_url, secret
    except Exception:  # noqa: BLE001
        return "", ""


def is_real_llm_enabled() -> bool:
    """True iff both V2 gates are on AND either Hermes or any provider
    in the chain is configured.

    Reads app.config lazily and fails closed (returns False) if config
    is unavailable -- decide() then retains its existing _default_no_llm
    branch, byte-identical to the pre-WS1 behavior.
    """
    try:
        from app.config import get_settings
        s = get_settings()
        if not getattr(s, "recruitment_turn_decision_v2_enabled", False):
            return False
        if not getattr(s, "recruitment_turn_decision_v2_real_llm", False):
            return False
        base_url, secret = _configured_present()
        if base_url and secret:
            return True
        return bool(
            getattr(s, "omniroute_api_key", "")
            or getattr(s, "groq_api_key", "")
            or not getattr(s, "ollama_reply_disabled", False)
            or getattr(s, "kimi_model_name", "")
        )
    except Exception:  # noqa: BLE001 -- decision path must never raise
        return False


__all__ = ["DecisionLLMCall", "make_decision_llm_call", "is_real_llm_enabled"]
