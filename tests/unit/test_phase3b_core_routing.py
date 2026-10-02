"""Phase 3B core routing tests. All providers are offline fakes."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import llm
from app.unified_routing import (
    FailureClass,
    InferenceRequest,
    ProviderResult,
    RouteCandidate,
    RoutingExhausted,
    UnifiedRoutingExecutor,
    get_last_shadow_decision,
    load_core_route_policy,
)
from tests.mocks.phase3_provider import OfflineProviderFake


def _request(workload="conversational_reply", **overrides):
    values = dict(
        workload=workload,
        required_capabilities=frozenset({"text_generation"}),
        cost_class="high",
        latency_class="interactive",
        privacy_class="approved_external",
        context_tokens=100,
        structured_output_required=False,
        required_modalities=frozenset({"text"}),
        fallback_eligible=True,
        retry_eligible=True,
        correlation_ref="message:m1",
        message_ref="m1",
        conversation_ref="c1",
        context_version="caller-built-v1",
    )
    values.update(overrides)
    return InferenceRequest(**values)


def _route(route_id, **overrides):
    values = dict(
        route_id=route_id,
        display_name=route_id,
        priority=10,
        transport="omniroute_gateway",
        provider="omniroute",
        model="approved/default",
        base_url="http://omniroute.test/v1",
        credential_type="api_key",
        credential_ref="omniroute_api_key",
        allowed_workloads=frozenset({"conversational_reply"}),
        capabilities=frozenset({"text_generation"}),
        modalities=frozenset({"text"}),
        privacy_classes=frozenset({"approved_external"}),
        cost_class="standard",
        latency_classes=frozenset({"interactive"}),
        context_limit=1000,
        structured_output=True,
        enabled=True,
        environments=frozenset({"development", "test", "production"}),
        max_attempts=2,
        retry_backoff_seconds=0,
    )
    values.update(overrides)
    return RouteCandidate(**values)


@pytest.mark.asyncio
async def test_executor_timeout_retry_then_fallback_returns_one_final_result():
    fake = OfflineProviderFake({
        "a": [ProviderResult.failure(FailureClass.TIMEOUT), ProviderResult.failure(FailureClass.TIMEOUT)],
        "b": [ProviderResult.success("one reply")],
    })

    result = await UnifiedRoutingExecutor(
        (_route("a"), _route("b")), environment="test",
        available_credential_refs=frozenset({"omniroute_api_key"}),
    ).execute(_request(), fake)
    assert result.content == "one reply"
    assert result.final_route_id == "b"
    assert fake.calls == [("a", 1), ("a", 2), ("b", 1)]
    assert len(result.attempts) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        FailureClass.AUTHENTICATION_FAILURE,
        FailureClass.AUTHORIZATION_FAILURE,
        FailureClass.INVALID_REQUEST_OR_POLICY_REJECTION,
        FailureClass.SAFETY_OR_PROVIDER_REFUSAL,
        FailureClass.INTERNAL_APPLICATION_DEFECT,
        FailureClass.UNKNOWN_UNCLASSIFIED,
    ],
)
async def test_executor_hard_failures_never_call_second_route(failure):
    calls = []

    async def fake(route, attempt):
        calls.append(route.route_id)
        return ProviderResult.failure(failure)

    with pytest.raises(RoutingExhausted) as raised:
        await UnifiedRoutingExecutor(
            (_route("a"), _route("b")), environment="test",
            available_credential_refs=frozenset({"omniroute_api_key"}),
        ).execute(_request(), fake)
    assert raised.value.failure_class is failure
    assert calls == ["a"]


@pytest.mark.asyncio
async def test_preflight_filters_capability_privacy_context_and_credentials():
    calls = []
    routes = (
        _route("wrong-cap", capabilities=frozenset({"vision"})),
        _route("wrong-privacy", privacy_classes=frozenset({"local_only"})),
        _route("too-short", context_limit=10),
        _route("missing-credential", credential_ref="missing"),
        _route("eligible"),
    )

    async def fake(route, _attempt):
        calls.append(route.route_id)
        return ProviderResult.success("ok")

    result = await UnifiedRoutingExecutor(
        routes, environment="test",
        available_credential_refs=frozenset({"omniroute_api_key"}),
    ).execute(_request(), fake)
    assert result.final_route_id == "eligible"
    assert calls == ["eligible"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        FailureClass.QUOTA_CREDIT_EXHAUSTED,
        FailureClass.PROVIDER_OR_MODEL_UNAVAILABLE,
        FailureClass.PROVIDER_OUTAGE_OR_5XX,
        FailureClass.MALFORMED_PROVIDER_RESPONSE,
        FailureClass.CONTEXT_LENGTH_EXCEEDED,
    ],
)
async def test_offline_fake_observes_allowed_fallback_classes(failure):
    first_results = [ProviderResult.failure(failure)]
    if failure is FailureClass.PROVIDER_OUTAGE_OR_5XX:
        first_results.append(ProviderResult.failure(failure))
    fake = OfflineProviderFake({"a": first_results, "b": [ProviderResult.success("ok")]})
    result = await UnifiedRoutingExecutor(
        (_route("a"), _route("b")), environment="test",
        available_credential_refs=frozenset({"omniroute_api_key"}),
    ).execute(_request(), fake)
    assert result.content == "ok"
    assert fake.calls[-1] == ("b", 1)


@pytest.mark.asyncio
async def test_rate_limit_retry_is_bounded_and_observable():
    fake = OfflineProviderFake({
        "a": [
            ProviderResult.failure(FailureClass.RATE_LIMITED, retry_after_seconds=0),
            ProviderResult.failure(FailureClass.RATE_LIMITED, retry_after_seconds=0),
        ],
        "b": [ProviderResult.success("ok")],
    })
    await UnifiedRoutingExecutor(
        (_route("a"), _route("b")), environment="test",
        available_credential_refs=frozenset({"omniroute_api_key"}),
    ).execute(_request(), fake)
    assert fake.calls == [("a", 1), ("a", 2), ("b", 1)]


def test_failure_taxonomy_is_exact_and_closed():
    assert {member.value for member in FailureClass} == {
        "authentication_failure", "authorization_failure", "quota_credit_exhausted",
        "rate_limited", "provider_or_model_unavailable", "provider_outage_or_5xx",
        "timeout", "malformed_provider_response", "context_length_exceeded",
        "safety_or_provider_refusal", "invalid_request_or_policy_rejection",
        "internal_application_defect", "unknown_unclassified",
    }


def test_versioned_policy_registers_initial_workloads_and_explicit_routes():
    policy = load_core_route_policy()
    assert {
        "simple_intent_classification",
        "conversational_reply",
        "recruitment_reply",
        "recruitment_decision",
    }.issubset(policy.workloads)
    assert any(route.transport == "omniroute_gateway" for route in policy.routes)
    assert all(route.credential_ref and route.allowed_workloads for route in policy.routes)
    assert all(route.enabled is not None and route.environments for route in policy.routes)


def _settings(*, enabled=False, workloads="conversational_reply,recruitment_reply,recruitment_decision,simple_intent_classification"):
    return SimpleNamespace(
        unified_ai_routing_enabled=enabled,
        unified_ai_routing_shadow_mode=False,
        unified_ai_routing_workloads=workloads,
        omniroute_api_key="",
        omniroute_model_name="legacy-auto",
        groq_api_key="",
        groq_model_name="legacy-groq",
        ollama_reply_disabled=True,
        ollama_model="legacy-local",
        kimi_model_name="legacy-kimi",
        github_token="",
    )


@pytest.mark.asyncio
async def test_generate_reply_flag_enabled_uses_workload_router_once(monkeypatch):
    routed = AsyncMock(return_value=SimpleNamespace(
        content="routed", attempts=(), final_route_id="r", final_provider="fake",
        final_model="fake/model", final_route_ordinal=0,
    ))
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
    monkeypatch.setattr("app.unified_routing.route_core_workload", routed)
    legacy = AsyncMock(side_effect=AssertionError("legacy provider chain must not run"))
    monkeypatch.setattr("app.omniroute_provider.generate_reply", legacy)

    result = await llm.generate_reply("hello", "greeting", history="existing history")
    assert result == "routed"
    routed.assert_awaited_once()
    request = routed.await_args.args[0]
    assert request.workload == "conversational_reply"
    assert request.metadata["history"] == "existing history"
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_exhaustion_returns_one_safe_result_without_legacy_chain(monkeypatch):
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(enabled=True))
    exhausted = RoutingExhausted(FailureClass.QUOTA_CREDIT_EXHAUSTED, ({"status": "failure"},))
    monkeypatch.setattr("app.unified_routing.route_core_workload", AsyncMock(side_effect=exhausted))
    legacy = AsyncMock(side_effect=AssertionError("must not start hidden fallback chain"))
    monkeypatch.setattr("app.omniroute_provider.generate_reply", legacy)
    assert await llm.generate_reply("hello", "greeting") == llm._FALLBACK_REPLY
    legacy.assert_not_awaited()
    assert llm.get_last_attempts() == [{"status": "failure"}]


@pytest.mark.asyncio
async def test_recruitment_decision_and_classifier_use_distinct_workloads(monkeypatch):
    seen = []

    async def routed(request, **_kwargs):
        seen.append(request.workload)
        value = "greeting" if request.workload == "simple_intent_classification" else "ok"
        return SimpleNamespace(
            content=value, attempts=(), final_route_id="r", final_provider="fake",
            final_model="fake/model", final_route_ordinal=0,
        )

    monkeypatch.setattr(llm, "get_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(llm, "_save_to_memory", AsyncMock())
    monkeypatch.setattr("app.unified_routing.route_core_workload", routed)
    assert await llm.generate_recruitment_reply("x", "kb") == "ok"
    assert await llm.decision_call(system_prompt="s", user_payload="u") == "ok"
    assert await llm.classify_intent_llm("hello") == "greeting"
    assert seen == ["recruitment_reply", "recruitment_decision", "simple_intent_classification"]


@pytest.mark.asyncio
async def test_flag_disabled_never_enters_new_router(monkeypatch):
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(enabled=False))
    routed = AsyncMock(side_effect=AssertionError("new router must remain disabled"))
    monkeypatch.setattr("app.unified_routing.route_core_workload", routed)
    monkeypatch.setattr("app.ollama.classify_intent_llm", AsyncMock(return_value="greeting"))
    assert await llm.classify_intent_llm("hello") == "greeting"
    routed.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_mode_records_decision_without_second_provider_call(monkeypatch):
    settings = _settings(enabled=False)
    settings.unified_ai_routing_shadow_mode = True
    settings.groq_api_key = "configured-ref-only"
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    legacy = AsyncMock(return_value="legacy reply")
    monkeypatch.setattr("app.groq_provider.generate_reply", legacy)
    shadow_provider = AsyncMock(side_effect=AssertionError("shadow must not call a provider"))
    monkeypatch.setattr("app.unified_routing.route_core_workload", shadow_provider)

    assert await llm.generate_reply("hello", "greeting") == "legacy reply"
    legacy.assert_awaited_once()
    shadow_provider.assert_not_awaited()
    decision = get_last_shadow_decision()
    assert decision["legacy_selected_route"] == "core-groq-default"
    assert decision["policy_selected_route"] == "core-groq-default"
    assert decision["compatible"] is True
    assert decision["provider_call_performed"] is False
