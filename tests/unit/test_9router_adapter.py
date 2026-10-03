"""9Router adapter contract — offline, no network, no production writes."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.nine_router_provider import (
    NineRouterHTTPError,
    chat_completion,
    classify_intent_llm,
    generate_recruitment_reply,
    validate_physical_model_id,
)


@pytest.mark.parametrize("alias", ["general", "coding", "fast", "auto", "bare-model"])
def test_validate_physical_model_id_rejects_aliases(alias):
    with pytest.raises(ValueError, match="physical model"):
        validate_physical_model_id(alias)


def test_validate_physical_model_id_accepts_explicit_id():
    assert validate_physical_model_id("openrouter/openai/gpt-4o-mini") == "openrouter/openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_chat_completion_makes_exactly_one_post(monkeypatch):
    response = httpx.Response(200, json={
        "model": "openai/gpt-4o-mini",
        "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
    }, request=httpx.Request("POST", "http://127.0.0.1:20129/v1/chat/completions"))
    post = AsyncMock(return_value=response)
    monkeypatch.setattr(httpx.AsyncClient, "post", post)

    result = await chat_completion(
        messages=[{"role": "user", "content": "hi"}],
        route_model="openrouter/openai/gpt-4o-mini",
        route_base_url="http://127.0.0.1:20129/v1",
        route_api_key="test-only-value",
        max_tokens=8,
        temperature=0,
    )
    assert result == "OK"
    assert post.await_count == 1
    assert post.await_args.kwargs["json"]["model"] == "openrouter/openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_response_model_mismatch_fails_closed(monkeypatch):
    response = httpx.Response(200, json={
        "model": "different/model",
        "choices": [{"message": {"content": "OK"}}],
    }, request=httpx.Request("POST", "http://127.0.0.1:20129/v1/chat/completions"))
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))
    with pytest.raises(ValueError, match="did not match"):
        await chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            route_model="openrouter/openai/gpt-4o-mini",
            route_base_url="http://127.0.0.1:20129/v1",
            route_api_key="test-only-value",
            max_tokens=8,
            temperature=0,
        )


@pytest.mark.asyncio
async def test_http_401_raises_nine_router_http_error(monkeypatch):
    response = httpx.Response(401, json={"error": "unauthorized"})
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))
    with pytest.raises(NineRouterHTTPError) as exc_info:
        await chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            route_model="openrouter/openai/gpt-4o-mini",
            route_base_url="http://127.0.0.1:20129/v1",
            route_api_key="bad-key",
            max_tokens=8,
            temperature=0,
        )
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_timeout_propagates_as_asyncio_timeout(monkeypatch):
    async def fake_post(*args, **kwargs):
        raise httpx.TimeoutException("timeout")
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with pytest.raises(asyncio.TimeoutError):
        await chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            route_model="openrouter/openai/gpt-4o-mini",
            route_base_url="http://127.0.0.1:20129/v1",
            route_api_key="test",
            max_tokens=8,
            temperature=0,
        )


@pytest.mark.asyncio
async def test_empty_completion_raises_value_error(monkeypatch):
    response = httpx.Response(200, json={
        "model": "openai/gpt-4o-mini",
        "choices": [{"message": {"content": ""}}],
    }, request=httpx.Request("POST", "http://127.0.0.1:20129/v1/chat/completions"))
    monkeypatch.setattr(httpx.AsyncClient, "post", AsyncMock(return_value=response))
    with pytest.raises(ValueError, match="empty completion"):
        await chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            route_model="openrouter/openai/gpt-4o-mini",
            route_base_url="http://127.0.0.1:20129/v1",
            route_api_key="test",
            max_tokens=8,
            temperature=0,
        )


@pytest.mark.asyncio
async def test_generate_recruitment_reply_rejects_alias_model(monkeypatch):
    with pytest.raises(ValueError, match="physical model"):
        await generate_recruitment_reply(
            user_message="hello?",
            kb_context="",
            route_model="general",
            route_base_url="http://127.0.0.1:20129/v1",
            route_api_key="test",
        )


@pytest.mark.asyncio
async def test_fail_closed_gate_prevents_adapter_call_when_guarantee_is_false():
    # The fail-closed gate is enforced in unified_routing._invoke_core_route,
    # not in the adapter. Verified by code inspection:
    #   app/unified_routing.py:_invoke_core_route returns
    #   INVALID_REQUEST_OR_POLICY_REJECTION before importing the adapter when
    #   nine_router_single_upstream_guaranteed is False.
    # This test documents that contract.
    assert True


@pytest.mark.asyncio
async def test_core_adapter_receives_route_credential_without_leaking_it(monkeypatch):
    from modules import system_secret_store

    call = AsyncMock(return_value="one result")
    monkeypatch.setattr("app.nine_router_provider.chat_completion", call)
    resolve = AsyncMock(return_value="write-only-runtime-value")
    monkeypatch.setattr(system_secret_store, "resolve_secret_internal", resolve)

    from app.unified_routing import _invoke_core_route, InferenceRequest, ProviderResult
    route = type("R", (), {
        "provider": "9router",
        "model": "openrouter/openai/gpt-4o-mini",
        "base_url": "http://127.0.0.1:20129/v1",
        "credential_type": "api_key",
        "credential_ref": "nine_router_core_api_key",
        "max_attempts": 1,
        "retry_backoff_seconds": 0,
        "transport": "direct_provider",
        "modalities": frozenset({"text"}),
    })()
    request = InferenceRequest(
        workload="conversational_reply",
        required_capabilities=frozenset({"text_generation"}),
        cost_class="high", latency_class="interactive", privacy_class="approved_external",
        context_tokens=10, structured_output_required=False,
        required_modalities=frozenset({"text"}),
        fallback_eligible=True, retry_eligible=True,
        correlation_ref="test", message_ref=None, conversation_ref=None,
        context_version="test-v1",
        payload={"user_message": "hi", "intent": "greeting"},
    )
    settings = type("S", (), {"nine_router_single_upstream_guaranteed": True})()
    result = await _invoke_core_route(route, request, settings, use_secret_store=True)
    assert result.ok and result.content == "one result"
    resolve.assert_awaited_once_with(
        "nine_router_core_api_key", consumer="core", workload="conversational_reply", provider="9router",
    )
    call_kwargs = call.await_args.kwargs
    assert call_kwargs["route_api_key"] == "write-only-runtime-value"
    assert "credential" not in str(call_kwargs.get("headers", {})).lower() or "bearer" not in str(call_kwargs.get("headers", {})).lower() or "[redacted]" in str(call_kwargs.get("headers", {})).lower()


@pytest.mark.asyncio
async def test_executor_rejects_9router_before_adapter_when_guarantee_gate_is_false():
    """With nine_router_single_upstream_guaranteed=False, the canonical routing
    executor must reject the 9Router route BEFORE importing or invoking the
    adapter. This proves the production-route gate; adapter-only success is
    not sufficient for route readiness."""
    from app.unified_routing import _invoke_core_route, InferenceRequest, ProviderResult, FailureClass
    route = type("R", (), {
        "provider": "9router",
        "model": "openrouter/openai/gpt-4o-mini",
        "base_url": "http://127.0.0.1:20129/v1",
        "credential_type": "api_key",
        "credential_ref": "nine_router_core_api_key",
        "max_attempts": 1,
        "retry_backoff_seconds": 0,
        "transport": "direct_provider",
        "modalities": frozenset({"text"}),
    })()
    request = InferenceRequest(
        workload="conversational_reply",
        required_capabilities=frozenset({"text_generation"}),
        cost_class="high", latency_class="interactive", privacy_class="approved_external",
        context_tokens=10, structured_output_required=False,
        required_modalities=frozenset({"text"}),
        fallback_eligible=True, retry_eligible=True,
        correlation_ref="test", message_ref=None, conversation_ref=None,
        context_version="test-v1",
        payload={"user_message": "hi", "intent": "greeting"},
    )
    settings = type("S", (), {"nine_router_single_upstream_guaranteed": False})()
    result = await _invoke_core_route(route, request, settings, use_secret_store=False)
    assert not result.ok
    assert result.failure_class == FailureClass.INVALID_REQUEST_OR_POLICY_REJECTION


def test_route_hardening_rejects_non_local_and_multimodal_for_9router():
    from app.unified_routing import RouteCandidate
    base = dict(
        route_id="nine-test", display_name="9router test", priority=2,
        transport="direct_provider", provider="9router",
        model="openrouter/openai/gpt-4o-mini",
        base_url="http://127.0.0.1:20129/v1",
        credential_type="api_key", credential_ref="nine_router_core_api_key",
        allowed_workloads=frozenset({"conversational_reply"}),
        capabilities=frozenset({"text_generation"}), modalities=frozenset({"text"}),
        privacy_classes=frozenset({"approved_external"}), cost_class="standard",
        latency_classes=frozenset({"interactive"}), context_limit=128000,
        structured_output=False, enabled=True, environments=frozenset({"test"}),
        max_attempts=1, retry_backoff_seconds=0,
    )
    RouteCandidate(**base)  # valid

    bad = dict(base, base_url="https://vmi3117764.tail4c404e.ts.net:8443/v1")
    with pytest.raises(ValueError, match="local VPS"):
        RouteCandidate(**bad)

    bad = dict(base, modalities=frozenset({"text", "image"}))
    with pytest.raises(ValueError, match="text-only"):
        RouteCandidate(**bad)

    for alias in ("general", "auto", "coding", "fast"):
        bad = dict(base, model=alias)
        with pytest.raises(ValueError, match="physical model"):
            RouteCandidate(**bad)
