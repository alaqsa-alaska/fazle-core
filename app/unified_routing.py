"""Phase 3B model-independent routing for Fazle Core.

This module owns provider/model selection, bounded retry/fallback, compatibility
filtering and safe attempt records. It never sends a message or grants caller
authority. Business callers continue to construct their current prompts and
context; those values are passed only to the selected adapter and are omitted
from attempt audit metadata.
"""
from __future__ import annotations

import asyncio
import contextvars
import re
import time
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Mapping

import yaml
from app.database import fetch_all


class FailureClass(str, Enum):
    AUTHENTICATION_FAILURE = "authentication_failure"
    AUTHORIZATION_FAILURE = "authorization_failure"
    QUOTA_CREDIT_EXHAUSTED = "quota_credit_exhausted"
    RATE_LIMITED = "rate_limited"
    PROVIDER_OR_MODEL_UNAVAILABLE = "provider_or_model_unavailable"
    PROVIDER_OUTAGE_OR_5XX = "provider_outage_or_5xx"
    TIMEOUT = "timeout"
    MALFORMED_PROVIDER_RESPONSE = "malformed_provider_response"
    CONTEXT_LENGTH_EXCEEDED = "context_length_exceeded"
    SAFETY_OR_PROVIDER_REFUSAL = "safety_or_provider_refusal"
    INVALID_REQUEST_OR_POLICY_REJECTION = "invalid_request_or_policy_rejection"
    INTERNAL_APPLICATION_DEFECT = "internal_application_defect"
    UNKNOWN_UNCLASSIFIED = "unknown_unclassified"


_HARD_STOP = frozenset({
    FailureClass.AUTHENTICATION_FAILURE,
    FailureClass.AUTHORIZATION_FAILURE,
    FailureClass.SAFETY_OR_PROVIDER_REFUSAL,
    FailureClass.INVALID_REQUEST_OR_POLICY_REJECTION,
    FailureClass.INTERNAL_APPLICATION_DEFECT,
    FailureClass.UNKNOWN_UNCLASSIFIED,
})
_RETRY = frozenset({
    FailureClass.TIMEOUT,
    FailureClass.PROVIDER_OUTAGE_OR_5XX,
    FailureClass.RATE_LIMITED,
})


@dataclass(frozen=True)
class InferenceRequest:
    workload: str
    required_capabilities: frozenset[str]
    cost_class: str
    latency_class: str
    privacy_class: str
    context_tokens: int
    structured_output_required: bool
    required_modalities: frozenset[str]
    fallback_eligible: bool
    retry_eligible: bool
    correlation_ref: str
    message_ref: str | None
    conversation_ref: str | None
    context_version: str
    payload: Mapping[str, object] | None = None

    @property
    def metadata(self) -> Mapping[str, object]:
        """Compatibility name for adapter-only payload; never placed in audits."""
        return self.payload or {}


@dataclass(frozen=True)
class RouteCandidate:
    route_id: str
    display_name: str
    priority: int
    transport: str
    provider: str
    model: str
    base_url: str
    credential_type: str
    credential_ref: str
    allowed_workloads: frozenset[str]
    capabilities: frozenset[str]
    modalities: frozenset[str]
    privacy_classes: frozenset[str]
    cost_class: str
    latency_classes: frozenset[str]
    context_limit: int
    structured_output: bool
    enabled: bool
    environments: frozenset[str]
    max_attempts: int
    retry_backoff_seconds: float

    def __post_init__(self):
        if not self.route_id or not self.provider or not self.model:
            raise ValueError("route identity fields must not be empty")
        if not self.credential_ref or any(value in self.credential_ref.lower() for value in ("sk-", "bearer ")):
            raise ValueError("credential_ref must be a non-secret reference")
        if not 1 <= self.max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        if self.context_limit <= 0 or self.retry_backoff_seconds < 0:
            raise ValueError("route limits are invalid")
        if self.priority < 0:
            raise ValueError("route priority is invalid")
        if self.credential_type not in {"api_key", "bearer_token", "session_cookie", "local_runtime", "none"}:
            raise ValueError("unsupported credential_type")


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    content: str = ""
    failure_class: FailureClass | None = None
    retry_after_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    safe_diagnostic: str = ""

    @classmethod
    def success(cls, content: str, **usage) -> "ProviderResult":
        if not content or not content.strip():
            raise ValueError("success requires non-empty content")
        return cls(True, content.strip(), **usage)

    @classmethod
    def failure(cls, failure_class: FailureClass, **detail) -> "ProviderResult":
        return cls(False, failure_class=failure_class, **detail)


@dataclass(frozen=True)
class RoutingResult:
    content: str
    final_route_id: str
    final_provider: str
    final_model: str
    final_route_ordinal: int
    attempts: tuple[dict[str, object], ...]


class RoutingExhausted(RuntimeError):
    def __init__(self, failure_class: FailureClass, attempts):
        super().__init__(f"routing exhausted: {failure_class.value}")
        self.failure_class = failure_class
        self.attempts = tuple(attempts)


_COST_RANK = {"low": 0, "standard": 1, "high": 2}


def _compatible(request, route, environment, credentials):
    return (
        route.enabled
        and environment in route.environments
        and request.workload in route.allowed_workloads
        and request.required_capabilities.issubset(route.capabilities)
        and request.required_modalities.issubset(route.modalities)
        and request.privacy_class in route.privacy_classes
        and request.latency_class in route.latency_classes
        and request.context_tokens <= route.context_limit
        and _COST_RANK[route.cost_class] <= _COST_RANK[request.cost_class]
        and (not request.structured_output_required or route.structured_output)
        and route.credential_ref in credentials
    )


class UnifiedRoutingExecutor:
    def __init__(self, routes, *, environment="development", available_credential_refs=frozenset(), sleep=asyncio.sleep):
        self.routes = tuple(routes)
        self.environment = environment
        self.available_credential_refs = available_credential_refs
        self.sleep = sleep

    async def execute(self, request, invoke: Callable[[RouteCandidate, int], Awaitable[ProviderResult]]):
        eligible = [
            route for route in self.routes
            if _compatible(request, route, self.environment, self.available_credential_refs)
        ]
        if not request.fallback_eligible:
            eligible = eligible[:1]
        if not eligible:
            raise RoutingExhausted(FailureClass.INVALID_REQUEST_OR_POLICY_REJECTION, ())

        attempts = []
        last_failure = FailureClass.UNKNOWN_UNCLASSIFIED
        for route_ordinal, route in enumerate(eligible):
            for attempt_ordinal in range(1, route.max_attempts + 1):
                started = time.monotonic()
                try:
                    result = await invoke(route, attempt_ordinal)
                    if not isinstance(result, ProviderResult):
                        result = ProviderResult.failure(FailureClass.INTERNAL_APPLICATION_DEFECT)
                except Exception:
                    result = ProviderResult.failure(FailureClass.INTERNAL_APPLICATION_DEFECT)
                latency_ms = max(0, int((time.monotonic() - started) * 1000))
                if result.ok:
                    attempts.append(_audit(request, route, route_ordinal, attempt_ordinal, latency_ms, result, "final"))
                    return RoutingResult(
                        result.content, route.route_id, route.provider, route.model,
                        route_ordinal, tuple(attempts),
                    )

                last_failure = result.failure_class or FailureClass.UNKNOWN_UNCLASSIFIED
                if last_failure in _HARD_STOP:
                    decision = "hard_stop"
                elif last_failure in _RETRY and request.retry_eligible and attempt_ordinal < route.max_attempts:
                    decision = "retry_same_route"
                else:
                    decision = "next_compatible_route"
                attempts.append(_audit(request, route, route_ordinal, attempt_ordinal, latency_ms, result, decision))
                if decision == "hard_stop":
                    raise RoutingExhausted(last_failure, attempts)
                if decision == "retry_same_route":
                    delay = result.retry_after_seconds if last_failure is FailureClass.RATE_LIMITED else route.retry_backoff_seconds
                    if delay is not None and 0 <= delay <= 30 and delay:
                        await self.sleep(delay)
                    continue
                break
        raise RoutingExhausted(last_failure, attempts)


def _audit(request, route, route_ordinal, attempt_ordinal, latency_ms, result, decision):
    return {
        "event": "ai_routing_attempt",
        "correlation_ref": request.correlation_ref,
        "message_ref": request.message_ref,
        "conversation_ref": request.conversation_ref,
        "workload": request.workload,
        "context_version": request.context_version,
        "route_id": route.route_id,
        "transport": route.transport,
        "provider": route.provider,
        "model": route.model,
        "route_ordinal": route_ordinal,
        "attempt_ordinal": attempt_ordinal,
        "latency_ms": latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "status": "success" if result.ok else "failure",
        "failure_class": result.failure_class.value if result.failure_class else None,
        "fallback_decision": decision,
        "diagnostic": _safe_diagnostic(result.safe_diagnostic),
    }


def _safe_diagnostic(value):
    text = str(value or "")[:500]
    text = re.sub(r"(?i)bearer\s+\S+", "[redacted]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", text)
    return text


@dataclass(frozen=True)
class CoreRoutePolicy:
    workloads: Mapping[str, Mapping[str, object]]
    routes: tuple[RouteCandidate, ...]
    ownership: Mapping[str, str]


def load_core_route_policy(path: str | Path | None = None) -> CoreRoutePolicy:
    policy_path = Path(path) if path else Path(__file__).parents[1] / "config" / "phase3_ai_routes.yaml"
    with policy_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict) or not isinstance(raw.get("workloads"), dict) or not isinstance(raw.get("routes"), list):
        raise ValueError("invalid core route policy")
    routes = []
    required = set(RouteCandidate.__dataclass_fields__)
    for item in raw["routes"]:
        if not isinstance(item, dict) or not required.issubset(item):
            raise ValueError("route is missing explicit fields")
        values = dict(item)
        for name in ("allowed_workloads", "capabilities", "modalities", "privacy_classes", "latency_classes", "environments"):
            values[name] = frozenset(values[name])
        routes.append(RouteCandidate(**values))
    return CoreRoutePolicy(raw["workloads"], tuple(routes), raw.get("ownership") or {})


async def load_database_route_policy() -> CoreRoutePolicy:
    """Load the admin registry as one authoritative ordered route source.

    This intentionally does not merge with YAML. Workload definitions and
    ownership semantics remain the versioned Phase 3B contract, while route
    candidates and their order come wholly from the registry when selected.
    """
    bootstrap = load_core_route_policy()
    rows = await fetch_all(
        """SELECT route_id, display_name, priority, transport, provider, model,
                  base_url, credential_type, credential_ref, allowed_workloads,
                  capabilities, modalities, privacy_classes, cost_class,
                  latency_classes, context_limit, structured_output, enabled,
                  environments, max_attempts, retry_backoff_seconds
             FROM phase3_ai_routes
            WHERE 'core'=ANY(eligible_consumers)
            ORDER BY priority ASC, route_id ASC"""
    )
    if not rows:
        raise ValueError("database route policy has no routes")
    routes = []
    for row in sorted(rows, key=lambda value: (value["priority"], value["route_id"])):
        values = dict(row)
        for name in ("allowed_workloads", "capabilities", "modalities", "privacy_classes", "latency_classes", "environments"):
            values[name] = frozenset(values[name] or ())
        routes.append(RouteCandidate(**values))
    return CoreRoutePolicy(bootstrap.workloads, tuple(routes), bootstrap.ownership)


_LAST_AUDITS = contextvars.ContextVar("core_phase3_routing_audits", default=())
_LAST_SHADOW = contextvars.ContextVar("core_phase3_shadow_decision", default=None)


def get_last_routing_audits():
    return list(_LAST_AUDITS.get())


def get_last_shadow_decision():
    value = _LAST_SHADOW.get()
    return dict(value) if value else None


def workload_enabled(settings, workload):
    if not getattr(settings, "unified_ai_routing_enabled", False):
        return False
    enabled = {value.strip() for value in getattr(settings, "unified_ai_routing_workloads", "").split(",") if value.strip()}
    return workload in enabled


def record_shadow_decision(request, settings, policy=None, environment="production"):
    """Compare route decisions without invoking either policy adapter."""
    if not getattr(settings, "unified_ai_routing_shadow_mode", False):
        _LAST_SHADOW.set(None)
        return None
    policy = policy or load_core_route_policy()
    credentials = {"local_runtime"}
    if getattr(settings, "omniroute_api_key", ""):
        credentials.add("omniroute_api_key")
    if getattr(settings, "groq_api_key", ""):
        credentials.add("groq_api_key")
    eligible = [
        route for route in policy.routes
        if _compatible(request, route, environment, frozenset(credentials))
    ]
    policy_route = eligible[0].route_id if eligible else None
    if request.workload == "simple_intent_classification":
        legacy_route = "core-ollama-local"
    elif getattr(settings, "omniroute_api_key", ""):
        legacy_route = "core-omniroute-default"
    elif getattr(settings, "groq_api_key", ""):
        legacy_route = "core-groq-default"
    elif not getattr(settings, "ollama_reply_disabled", False):
        legacy_route = "core-ollama-local"
    else:
        legacy_route = "core-kimi-ollama-proxy"
    decision = {
        "workload": request.workload,
        "legacy_selected_route": legacy_route,
        "policy_selected_route": policy_route,
        "compatible": policy_route == legacy_route,
        "reason": "same_route" if policy_route == legacy_route else "policy_order_or_compatibility_differs",
        "provider_call_performed": False,
    }
    _LAST_SHADOW.set(decision)
    return decision


def make_request(workload, payload, *, structured=False):
    reference = str(payload.get("message_ref") or payload.get("conversation_ref") or "unreferenced")
    return InferenceRequest(
        workload=workload,
        required_capabilities=frozenset({"text_generation", "structured_output"} if structured else {"text_generation"}),
        cost_class="high",
        latency_class="interactive",
        privacy_class="approved_external",
        context_tokens=max(1, sum(len(str(value)) for value in payload.values()) // 4),
        structured_output_required=structured,
        required_modalities=frozenset({"text"}),
        fallback_eligible=True,
        retry_eligible=True,
        correlation_ref=f"{workload}:{reference}"[:200],
        message_ref=payload.get("message_ref"),
        conversation_ref=payload.get("conversation_ref"),
        context_version="caller-built-v1",
        payload=payload,
    )


async def route_core_workload(request, *, settings=None, policy=None, invoke=None, environment="production"):
    if settings is None:
        from app.config import get_settings
        settings = get_settings()
    if policy is None:
        source = getattr(settings, "unified_ai_routing_policy_source", "bootstrap").strip().lower()
        try:
            if source == "bootstrap":
                policy = load_core_route_policy()
            elif source == "database":
                policy = await load_database_route_policy()
            else:
                raise ValueError("unknown unified routing policy source")
        except (ValueError, TypeError):
            raise RoutingExhausted(FailureClass.INVALID_REQUEST_OR_POLICY_REJECTION, ())
    source = getattr(settings, "unified_ai_routing_policy_source", "bootstrap").strip().lower()
    credentials = {"local_runtime"}
    # An injected adapter is the offline test/shadow seam and must stay I/O-free.
    use_secret_store = source == "database" and invoke is None
    if use_secret_store:
        from modules.system_secret_store import configured_secret_refs
        credentials.update(await configured_secret_refs(consumer="core", workload=request.workload))
    else:
        if getattr(settings, "omniroute_api_key", ""):
            credentials.add("omniroute_api_key")
        if getattr(settings, "groq_api_key", ""):
            credentials.add("groq_api_key")
    resolved_routes = tuple(_resolve_route_model(route, settings) for route in policy.routes)
    adapter = invoke or (lambda route, attempt: _invoke_core_route(route, request, settings, use_secret_store=use_secret_store))
    try:
        result = await UnifiedRoutingExecutor(
            resolved_routes, environment=environment,
            available_credential_refs=frozenset(credentials),
        ).execute(request, adapter)
    except RoutingExhausted as exc:
        _LAST_AUDITS.set(exc.attempts)
        raise
    _LAST_AUDITS.set(result.attempts)
    return result


def _resolve_route_model(route, settings):
    if route.model == "approved_gateway_alias/default":
        return replace(route, model=getattr(settings, "omniroute_model_name", route.model))
    if route.model == "approved_runtime_alias/groq_default":
        return replace(route, model=getattr(settings, "groq_model_name", route.model))
    if route.model == "approved_runtime_alias/local_preferred":
        return replace(route, model=getattr(settings, "ollama_model", route.model))
    if route.model == "approved_runtime_alias/kimi_cloud":
        return replace(route, model=getattr(settings, "kimi_model_name", route.model))
    return route


async def _invoke_core_route(route, request, settings, *, use_secret_store=False):
    payload = dict(request.payload or {})
    try:
        credential_value = None
        if use_secret_store and route.credential_ref != "local_runtime":
            from modules.system_secret_store import resolve_secret_internal
            credential_value = await resolve_secret_internal(
                route.credential_ref, consumer="core", workload=request.workload,
                provider=route.provider,
            )
        if route.provider == "omniroute":
            from app import omniroute_provider as provider
            result = await _call_operation(provider, request.workload, payload, route=route, route_credential=credential_value)
        elif route.provider == "groq":
            from app import groq_provider as provider
            result = await _call_operation(provider, request.workload, payload, route=route, route_credential=credential_value)
        elif route.provider == "ollama-local":
            from app import ollama as provider
            if route.model:
                payload["model"] = route.model
            result = await _call_operation(provider, request.workload, payload)
        else:
            return ProviderResult.failure(FailureClass.INVALID_REQUEST_OR_POLICY_REJECTION)
    except asyncio.TimeoutError:
        return ProviderResult.failure(FailureClass.TIMEOUT)
    except Exception as exc:
        return ProviderResult.failure(_classify_exception(exc), safe_diagnostic=str(exc))
    if not result or (request.workload == "simple_intent_classification" and result == "unknown"):
        return ProviderResult.failure(FailureClass.MALFORMED_PROVIDER_RESPONSE)
    if route.provider == "ollama-local":
        from app import ollama
        if result in {getattr(ollama, "FALLBACK_REPLY_TEXT", object()), getattr(ollama, "RECRUITMENT_FALLBACK_TEXT", object())}:
            return ProviderResult.failure(FailureClass.MALFORMED_PROVIDER_RESPONSE)
    return ProviderResult.success(str(result))


async def _call_operation(provider, workload, payload, *, route=None, route_credential=None):
    route_args = {}
    if route is not None:
        route_args = {"route_model": route.model, "route_base_url": route.base_url,
                      "route_api_key": route_credential, "router_managed": True}
    if workload == "simple_intent_classification":
        return await provider.classify_intent_llm(payload["text"], **route_args)
    if workload == "conversational_reply":
        return await provider.generate_reply(**payload, **route_args)
    if workload == "recruitment_reply":
        return await provider.generate_recruitment_reply(**payload, **route_args)
    if workload == "recruitment_decision":
        return await provider.decision_call(**payload, **route_args)
    raise ValueError("unregistered core workload adapter")


def _classify_exception(exc):
    text = f"{type(exc).__name__}: {exc}".lower()
    status = getattr(exc, "status_code", None)
    if status == 401 or "authentication" in text or "invalid api key" in text:
        return FailureClass.AUTHENTICATION_FAILURE
    if status == 403 or "authorization" in text or "forbidden" in text:
        return FailureClass.AUTHORIZATION_FAILURE
    if "quota" in text or "credit" in text:
        return FailureClass.QUOTA_CREDIT_EXHAUSTED
    if status == 429 or "rate limit" in text:
        return FailureClass.RATE_LIMITED
    if status in {404, 410} or "model_not_found" in text or "unavailable" in text:
        return FailureClass.PROVIDER_OR_MODEL_UNAVAILABLE
    if status and 500 <= status <= 599:
        return FailureClass.PROVIDER_OUTAGE_OR_5XX
    if "context" in text and ("length" in text or "token" in text):
        return FailureClass.CONTEXT_LENGTH_EXCEEDED
    if "safety" in text or "refusal" in text:
        return FailureClass.SAFETY_OR_PROVIDER_REFUSAL
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return FailureClass.INVALID_REQUEST_OR_POLICY_REJECTION
    if isinstance(exc, (ConnectionError, OSError)):
        return FailureClass.PROVIDER_OUTAGE_OR_5XX
    return FailureClass.UNKNOWN_UNCLASSIFIED
