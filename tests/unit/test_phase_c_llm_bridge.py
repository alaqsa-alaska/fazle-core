"""Phase C v2 — WS1 real-LLM bridge tests (2026-08-25).

Covers modules/recruitment_turn_decision/llm_bridge.py and its wiring into
decide.py: Hermes-primary resolution, direct-provider-fallback default-OFF
safety, fail-closed behavior on every Hermes failure mode, and that the
existing (pre-WS1) byte-identical behavior is preserved whenever either
config flag is off.

These tests mock at the module-attribute boundary (modules.hermes_dispatch
.call_decision_llm and app.llm.decision_call) rather than the network, since
llm_bridge.py resolves both via a lazy `from X import Y` inside the closure
— monkeypatching the attribute on the *source* module is what the closure
actually observes.
"""
from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

import app.llm as _app_llm_module
from modules.recruitment_turn_decision import decide
from modules.recruitment_turn_decision.llm_bridge import (
    is_real_llm_enabled,
    make_decision_llm_call,
)


def _fake_settings(**overrides):
    base = dict(
        recruitment_turn_decision_v2_enabled=True,
        recruitment_turn_decision_v2_real_llm=True,
        recruitment_turn_decision_v2_direct_provider_fallback=False,
        recruitment_decision_call_timeout_seconds=12.0,
        hermes_runner_url="http://127.0.0.1:8093",
        hermes_runner_customer_secret="test-secret",
        omniroute_api_key="",
        groq_api_key="",
        ollama_reply_disabled=True,
        kimi_model_name="",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _decide_kwargs(**overrides):
    kwargs = dict(
        text="hello",
        current_step="name",
        session={},
        identity={},
        history=[],
        intake_proposals=[],
        source_of_truth="",
        flag_enabled=True,
    )
    kwargs.update(overrides)
    return kwargs


VALID_DECISION_JSON = (
    '{"semantic_domain": "recruitment", "turn_type": "PURE_QUESTION", '
    '"confidence": 0.9}'
)


# --- Flag independence (section 4 / 16) ---


@pytest.mark.asyncio
async def test_flag_enabled_false_returns_none_without_touching_bridge(monkeypatch):
    """decide()'s own flag_enabled kwarg gates everything, independent of
    the config-level v2_real_llm flag. When False, decide() must return
    None immediately and never import/call the Hermes bridge."""
    hermes_mock = AsyncMock(return_value=VALID_DECISION_JSON)
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr("app.config.get_settings", lambda: _fake_settings())

    result = await decide(**_decide_kwargs(flag_enabled=False))

    assert result is None
    hermes_mock.assert_not_called()


@pytest.mark.asyncio
async def test_real_llm_flag_off_uses_default_no_llm_stub(monkeypatch):
    """v2_enabled True but v2_real_llm False -> decide() must NOT call the
    bridge at all; it falls straight to the pre-WS1 _default_no_llm stub,
    producing the existing AMBIGUOUS fallback."""
    hermes_mock = AsyncMock(return_value=VALID_DECISION_JSON)
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(recruitment_turn_decision_v2_real_llm=False),
    )

    result = await decide(**_decide_kwargs())

    assert result is not None
    assert result.turn_type == "AMBIGUOUS"
    assert "AMBIGUOUS_FALLBACK" in result.reason_codes
    hermes_mock.assert_not_called()


# --- Hermes-primary path (section 15) ---


@pytest.mark.asyncio
async def test_both_flags_on_no_explicit_llm_call_invokes_hermes(monkeypatch):
    """Both config flags on + no caller-supplied llm_call -> decide() must
    resolve the real bridge and the bridge must call Hermes."""
    hermes_mock = AsyncMock(return_value=VALID_DECISION_JSON)
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr("app.config.get_settings", lambda: _fake_settings())

    result = await decide(**_decide_kwargs(text="আমার বেতন কত?"))

    hermes_mock.assert_called_once()
    assert result is not None
    assert result.turn_type == "PURE_QUESTION"
    assert result.semantic_domain == "recruitment"


@pytest.mark.asyncio
async def test_hermes_success_provider_chain_never_called(monkeypatch):
    """Hermes returning a real answer must short-circuit the bridge before
    the provider-chain fallback is even considered — true even if the
    direct-provider-fallback flag happens to be on, proving Hermes success
    always wins over any fallback path."""
    hermes_mock = AsyncMock(return_value=VALID_DECISION_JSON)
    provider_mock = AsyncMock(return_value='{"semantic_domain": "recruitment"}')
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr(_app_llm_module, "decision_call", provider_mock)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(recruitment_turn_decision_v2_direct_provider_fallback=True),
    )

    await decide(**_decide_kwargs())

    hermes_mock.assert_called_once()
    provider_mock.assert_not_called()


# --- Hermes failure modes must fail closed, never bypass (sections 5, 17, 18) ---


@pytest.mark.asyncio
async def test_hermes_configured_but_empty_reply_returns_none_no_bypass(monkeypatch):
    """Hermes IS configured but returns an empty/timeout result (None).
    Even with direct_provider_fallback=True, a *configured* Hermes must
    never be bypassed — only an *unconfigured* Hermes may fall through."""
    hermes_mock = AsyncMock(return_value=None)
    provider_mock = AsyncMock(return_value="should never be reached")
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr(_app_llm_module, "decision_call", provider_mock)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(recruitment_turn_decision_v2_direct_provider_fallback=True),
    )

    result = await decide(**_decide_kwargs())

    hermes_mock.assert_called_once()
    provider_mock.assert_not_called()
    assert result.turn_type == "AMBIGUOUS"


@pytest.mark.asyncio
async def test_hermes_raises_exception_direct_fallback_off_fails_closed(monkeypatch):
    """Hermes call raising (connection failure / timeout exception, not a
    clean None-return) must still fail closed to AMBIGUOUS when the
    production-default direct_provider_fallback is False — never crash the
    customer pipeline, never silently reach the provider chain."""
    async def _boom(**kwargs):
        raise ConnectionError("simulated hermes-runner unreachable")

    provider_mock = AsyncMock(return_value="should never be reached")
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", _boom)
    monkeypatch.setattr(_app_llm_module, "decision_call", provider_mock)
    monkeypatch.setattr("app.config.get_settings", lambda: _fake_settings())

    result = await decide(**_decide_kwargs())

    provider_mock.assert_not_called()
    assert result.turn_type == "AMBIGUOUS"


@pytest.mark.asyncio
async def test_hermes_not_configured_fallback_off_returns_ambiguous(monkeypatch):
    hermes_mock = AsyncMock(return_value=None)
    provider_mock = AsyncMock(return_value="should never be reached")
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr(_app_llm_module, "decision_call", provider_mock)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(
            hermes_runner_url="",
            hermes_runner_customer_secret="",
            # A provider must look "configured" or is_real_llm_enabled()
            # gates the bridge off entirely before it's ever reached —
            # this test wants to actually exercise the closure's own
            # direct_fb=False short-circuit, not the earlier flag gate.
            omniroute_api_key="test-omniroute-key",
        ),
    )

    result = await decide(**_decide_kwargs())

    hermes_mock.assert_called_once()
    provider_mock.assert_not_called()
    assert result.turn_type == "AMBIGUOUS"


# --- Direct-provider fallback: dev/test-only escape hatch (section 18) ---


@pytest.mark.asyncio
async def test_hermes_not_configured_fallback_on_reaches_provider_chain(monkeypatch):
    """Only when Hermes is unconfigured AND the fallback flag is
    explicitly on does the provider chain get reached — the controlled
    dev/test path, never the production default."""
    hermes_mock = AsyncMock(return_value=None)
    provider_mock = AsyncMock(return_value=VALID_DECISION_JSON)
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr(_app_llm_module, "decision_call", provider_mock)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(
            hermes_runner_url="",
            hermes_runner_customer_secret="",
            recruitment_turn_decision_v2_direct_provider_fallback=True,
            # is_real_llm_enabled() requires at least one provider to look
            # "configured" before decide() will resolve the bridge at all
            # (otherwise it correctly stays on the pre-WS1 _default_no_llm
            # stub) — that check is independent of the fallback flag.
            omniroute_api_key="test-omniroute-key",
        ),
    )

    result = await decide(**_decide_kwargs())

    provider_mock.assert_called_once()
    assert result.turn_type == "PURE_QUESTION"


def test_direct_provider_fallback_default_is_false():
    """Section 32's target production value — proven against the real
    Settings class, not a fake."""
    from app.config import Settings

    assert Settings().recruitment_turn_decision_v2_direct_provider_fallback is False


# --- Malformed / schema-invalid Hermes output (sections 9, 13) ---


@pytest.mark.asyncio
async def test_hermes_malformed_json_falls_back_to_ambiguous(monkeypatch):
    hermes_mock = AsyncMock(return_value="not json at all {{{")
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr("app.config.get_settings", lambda: _fake_settings())

    result = await decide(**_decide_kwargs())

    assert result.turn_type == "AMBIGUOUS"
    assert "llm_malformed_json" in result.reason_codes


@pytest.mark.asyncio
async def test_hermes_invalid_enum_value_coerced_to_ambiguous(monkeypatch):
    """An out-of-contract turn_type/domain enum from the model must be
    coerced to AMBIGUOUS, not accepted verbatim or raised as an error."""
    hermes_mock = AsyncMock(
        return_value='{"semantic_domain": "recruitment", '
        '"turn_type": "SOMETHING_NOT_IN_THE_CONTRACT", "confidence": 0.9}'
    )
    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", hermes_mock)
    monkeypatch.setattr("app.config.get_settings", lambda: _fake_settings())

    result = await decide(**_decide_kwargs())

    assert result.turn_type == "AMBIGUOUS"


# --- is_real_llm_enabled() gate logic (section 4) ---


def test_is_real_llm_enabled_requires_both_flags(monkeypatch):
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(recruitment_turn_decision_v2_enabled=False),
    )
    assert is_real_llm_enabled() is False

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(recruitment_turn_decision_v2_real_llm=False),
    )
    assert is_real_llm_enabled() is False

    monkeypatch.setattr("app.config.get_settings", lambda: _fake_settings())
    assert is_real_llm_enabled() is True


def test_is_real_llm_enabled_false_with_no_reachable_provider(monkeypatch):
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(
            hermes_runner_url="",
            hermes_runner_customer_secret="",
            omniroute_api_key="",
            groq_api_key="",
            ollama_reply_disabled=True,
            kimi_model_name="",
        ),
    )
    assert is_real_llm_enabled() is False


# --- Context propagation (section 19) ---


@pytest.mark.asyncio
async def test_hermes_receives_the_turn_text_and_step(monkeypatch):
    """Proves the Hermes call actually receives the current turn's text
    and intake step context, not a stripped/empty payload — without
    asserting on the full prompt-building implementation (owned by
    prompts.build_decision_prompt, not this bridge)."""
    captured = {}

    async def _capture(*, system_prompt, user_payload, sender_phone=""):
        captured["system_prompt"] = system_prompt
        captured["user_payload"] = user_payload
        return VALID_DECISION_JSON

    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", _capture)
    monkeypatch.setattr("app.config.get_settings", lambda: _fake_settings())

    await decide(**_decide_kwargs(text="আর বেতন কত হবে?", current_step="salary_expectation"))

    assert "আর বেতন কত হবে?" in captured["user_payload"]
    assert "salary_expectation" in captured["user_payload"] or "salary_expectation" in captured["system_prompt"]


@pytest.mark.asyncio
async def test_mixed_bengali_english_text_not_mangled_in_payload(monkeypatch):
    """Bengali-English mixed input must reach Hermes unmodified — no
    ASCII-only assumption anywhere in the bridge/prompt layer."""
    captured = {}

    async def _capture(*, system_prompt, user_payload, sender_phone=""):
        captured["user_payload"] = user_payload
        return VALID_DECISION_JSON

    monkeypatch.setattr("modules.hermes_dispatch.call_decision_llm", _capture)
    monkeypatch.setattr("app.config.get_settings", lambda: _fake_settings())

    mixed = "Sir, duty টা কি Chittagong port এ হবে? training fee কত?"
    await decide(**_decide_kwargs(text=mixed))

    assert mixed in captured["user_payload"]


# --- Timeout wiring (section 32 / decide.py resolution) ---


@pytest.mark.asyncio
async def test_configured_timeout_reaches_the_bridge_closure(monkeypatch):
    """decide() must read recruitment_decision_call_timeout_seconds from
    config and pass it through to make_decision_llm_call(), not use a
    hardcoded value silently."""
    seen_timeouts = []
    real_make_call = make_decision_llm_call

    def _spy_make_call(*, timeout=12.0, **kwargs):
        seen_timeouts.append(timeout)
        return real_make_call(timeout=timeout, **kwargs)

    # decide.py resolves `make_decision_llm_call` via a lazy
    # `from .llm_bridge import ...` inside the function body, so the
    # patch target is the *source* module's attribute, not decide's.
    monkeypatch.setattr(
        "modules.recruitment_turn_decision.llm_bridge.make_decision_llm_call", _spy_make_call
    )
    monkeypatch.setattr(
        "modules.hermes_dispatch.call_decision_llm", AsyncMock(return_value=VALID_DECISION_JSON)
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _fake_settings(recruitment_decision_call_timeout_seconds=7.5),
    )

    await decide(**_decide_kwargs())

    assert seen_timeouts == [7.5]
