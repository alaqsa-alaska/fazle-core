"""Targeted settings-to-router contracts. Provider calls are offline fakes."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.unified_routing import (
    FailureClass,
    InferenceRequest,
    ProviderResult,
    RoutingExhausted,
    load_core_route_policy,
    load_database_route_policy,
    route_core_workload,
)
from modules.ai_route_registry import RouteConfig


def _request():
    return InferenceRequest(
        workload="conversational_reply",
        required_capabilities=frozenset({"text_generation"}),
        cost_class="high",
        latency_class="interactive",
        privacy_class="approved_external",
        context_tokens=100,
        structured_output_required=False,
        required_modalities=frozenset({"text"}),
        fallback_eligible=True,
        retry_eligible=True,
        correlation_ref="settings-test",
        message_ref="m1",
        conversation_ref="c1",
        context_version="caller-built-v1",
        payload={"user_message": "hello", "intent": "greeting"},
    )


def _row(route_id, priority, **overrides):
    row = {
        "route_id": route_id,
        "display_name": route_id,
        "priority": priority,
        "transport": "direct_provider",
        "provider": "groq",
        "model": f"model-{route_id}",
        "base_url": "https://provider.invalid/v1",
        "credential_type": "api_key",
        "credential_ref": "groq_api_key",
        "allowed_workloads": ["conversational_reply"],
        "capabilities": ["text_generation"],
        "modalities": ["text"],
        "privacy_classes": ["approved_external"],
        "cost_class": "standard",
        "latency_classes": ["interactive"],
        "context_limit": 4096,
        "structured_output": False,
        "enabled": True,
        "environments": ["test", "production"],
        "max_attempts": 1,
        "retry_backoff_seconds": 0,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_database_policy_order_edit_disable_delete_drive_same_executor(monkeypatch):
    rows = [_row("a", 20), _row("disabled", 5, enabled=False), _row("b", 10, model="edited-model")]
    fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr("app.unified_routing.fetch_all", fetch)
    policy = await load_database_route_policy()
    assert [route.route_id for route in policy.routes] == ["disabled", "b", "a"]
    assert policy.routes[1].model == "edited-model"

    calls = []
    async def fake(route, _attempt):
        calls.append(route.route_id)
        if route.route_id == "b":
            return ProviderResult.failure(FailureClass.QUOTA_CREDIT_EXHAUSTED)
        return ProviderResult.success("one final result")

    settings = SimpleNamespace(
        unified_ai_routing_policy_source="database",
        omniroute_api_key="", groq_api_key="configured", ollama_model="local",
        groq_model_name="legacy", omniroute_model_name="legacy", kimi_model_name="legacy",
    )
    result = await route_core_workload(_request(), settings=settings, policy=policy, invoke=fake, environment="test")
    assert result.content == "one final result"
    assert calls == ["b", "a"]


@pytest.mark.asyncio
async def test_database_source_empty_or_invalid_fails_closed_without_bootstrap(monkeypatch):
    monkeypatch.setattr("app.unified_routing.fetch_all", AsyncMock(return_value=[]))
    settings = SimpleNamespace(
        unified_ai_routing_policy_source="database",
        omniroute_api_key="", groq_api_key="", ollama_model="local",
        groq_model_name="legacy", omniroute_model_name="legacy", kimi_model_name="legacy",
    )
    with pytest.raises(RoutingExhausted) as raised:
        await route_core_workload(_request(), settings=settings, environment="test")
    assert raised.value.failure_class is FailureClass.INVALID_REQUEST_OR_POLICY_REJECTION


@pytest.mark.asyncio
async def test_omniroute_selected_model_base_url_and_credential_ref_reach_adapter(monkeypatch):
    from app import omniroute_provider
    from app.unified_routing import RouteCandidate, _invoke_core_route

    called = AsyncMock(return_value="ok")
    monkeypatch.setattr(omniroute_provider, "generate_reply", called)
    route = RouteCandidate(
        route_id="omni-custom", display_name="Omni custom", priority=1,
        transport="omniroute_gateway", provider="omniroute", model="chosen/model",
        base_url="http://omniroute.test/v1", credential_type="api_key",
        credential_ref="omniroute_api_key", allowed_workloads=frozenset({"conversational_reply"}),
        capabilities=frozenset({"text_generation"}), modalities=frozenset({"text"}),
        privacy_classes=frozenset({"approved_external"}), cost_class="standard",
        latency_classes=frozenset({"interactive"}), context_limit=4096,
        structured_output=False, enabled=True, environments=frozenset({"test"}),
        max_attempts=1, retry_backoff_seconds=0,
    )
    result = await _invoke_core_route(route, _request(), SimpleNamespace())
    assert result.ok
    assert called.await_args.kwargs["route_model"] == "chosen/model"
    assert called.await_args.kwargs["route_base_url"] == "http://omniroute.test/v1"


@pytest.mark.asyncio
async def test_database_mode_resolves_selected_credential_from_shared_store(monkeypatch):
    from app import unified_routing
    from app.unified_routing import CoreRoutePolicy, RouteCandidate
    from modules import system_secret_store

    values = _row("db-groq", 1, credential_ref="provider_ref")
    values.pop("priority")
    for field in ("allowed_workloads", "capabilities", "modalities", "privacy_classes", "latency_classes", "environments"):
        values[field] = frozenset(values[field])
    route = RouteCandidate(priority=1, **values)
    policy = CoreRoutePolicy(load_core_route_policy().workloads, (route,), {})
    monkeypatch.setattr(system_secret_store, "configured_secret_refs", AsyncMock(return_value=frozenset({"provider_ref"})))
    resolve = AsyncMock(return_value="write-only-runtime-value")
    monkeypatch.setattr(system_secret_store, "resolve_secret_internal", resolve)
    operation = AsyncMock(return_value="ok")
    monkeypatch.setattr(unified_routing, "_call_operation", operation)
    settings = SimpleNamespace(
        unified_ai_routing_policy_source="database", groq_model_name="legacy",
        omniroute_model_name="legacy", ollama_model="local", kimi_model_name="legacy",
    )
    result = await route_core_workload(_request(), settings=settings, policy=policy, environment="test")
    assert result.content == "ok"
    resolve.assert_awaited_once_with(
        "provider_ref", consumer="core", workload="conversational_reply", provider="groq",
    )
    assert operation.await_args.kwargs["route_credential"] == "write-only-runtime-value"


@pytest.mark.asyncio
async def test_ordered_cloud_failures_reach_eligible_local_once():
    from app.unified_routing import RouteCandidate, UnifiedRoutingExecutor

    rows = [
        _row("a", 1), _row("b", 2), _row("c", 3),
        _row(
            "local", 4, provider="ollama-local", transport="local_ollama",
            credential_type="local_runtime", credential_ref="local_runtime",
            base_url="http://127.0.0.1:11434", model="qwen3:8b",
        ),
    ]
    routes = []
    for row in rows:
        values = {key: value for key, value in row.items() if key != "priority"}
        values["priority"] = row["priority"]
        for field in ("allowed_workloads", "capabilities", "modalities", "privacy_classes", "latency_classes", "environments"):
            values[field] = frozenset(values[field])
        routes.append(RouteCandidate(**values))
    failures = {
        "a": FailureClass.PROVIDER_OR_MODEL_UNAVAILABLE,
        "b": FailureClass.TIMEOUT,
        "c": FailureClass.QUOTA_CREDIT_EXHAUSTED,
    }
    calls = []
    async def fake(route, attempt):
        calls.append((route.route_id, attempt))
        if route.route_id == "local":
            return ProviderResult.success("local final")
        return ProviderResult.failure(failures[route.route_id])

    result = await UnifiedRoutingExecutor(
        routes, environment="test",
        available_credential_refs=frozenset({"groq_api_key", "local_runtime"}),
    ).execute(_request(), fake)
    assert result.content == "local final"
    assert calls == [("a", 1), ("b", 1), ("c", 1), ("local", 1)]


def test_registry_rejects_plaintext_secret_and_mismatched_credential_owner():
    valid = _row("safe", 1)
    with pytest.raises(ValueError):
        RouteConfig(**valid, api_key="must-not-be-stored")
    invalid = RouteConfig(**{**valid, "credential_ref": "omniroute_api_key"})
    with pytest.raises(ValueError, match="not owned"):
        invalid.validate_contract()
    with pytest.raises(ValueError):
        RouteConfig(**{**valid, "base_url": "https://provider.invalid/v1?api_key=secret"})
    unsupported = RouteConfig(**{**valid, "allowed_workloads": ["administrative_reasoning"]})
    with pytest.raises(ValueError, match="no Core runtime adapter"):
        unsupported.validate_contract()
