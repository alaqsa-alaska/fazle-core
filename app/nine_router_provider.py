"""Bounded OpenAI-compatible transport for explicit 9router physical models.

There is deliberately no retry or model fallback here.  Phase 3B owns both.
The adapter never logs request bodies, credentials, response reasoning, or
authorization headers.
"""
from __future__ import annotations

import asyncio
import httpx

from shared.reply_policy import build_whatsapp_recruitment_policy, build_whatsapp_reply_policy

_ALIASES = frozenset({"general", "coding", "fast", "auto"})


class NineRouterHTTPError(RuntimeError):
    def __init__(self, status_code: int, safe_message: str):
        super().__init__(safe_message)
        self.status_code = status_code


def validate_physical_model_id(model: str) -> str:
    value = str(model or "").strip()
    if not value or value.lower() in _ALIASES or "/" not in value:
        raise ValueError("9router requires an explicit physical model ID")
    return value


def _response_model_matches(requested: str, returned: object) -> bool:
    if not isinstance(returned, str) or not returned.strip():
        return False
    # 9router returns the upstream model without its connection-provider prefix.
    return returned == requested or returned == requested.split("/", 1)[1]


async def chat_completion(
    *, messages: list[dict[str, str]], route_model: str, route_base_url: str,
    route_api_key: str | None, max_tokens: int, temperature: float,
    timeout: float = 25.0, tools: list[dict] | None = None,
) -> str:
    model = validate_physical_model_id(route_model)
    if not route_api_key:
        raise ValueError("9router credential unavailable")
    if not route_base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
        raise ValueError("9router Core transport must remain on localhost")
    body: dict[str, object] = {
        "model": model,
        "messages": messages,
        "max_tokens": max(1, min(int(max_tokens), 1024)),
        "temperature": max(0.0, min(float(temperature), 1.0)),
        "stream": False,
    }
    if tools:
        body["tools"] = tools
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            headers={"Authorization": f"Bearer {route_api_key}"},
        ) as client:
            response = await client.post(f"{route_base_url.rstrip('/')}/chat/completions", json=body)
    except httpx.TimeoutException as exc:
        raise asyncio.TimeoutError("9router request timed out") from exc
    except httpx.HTTPError as exc:
        raise ConnectionError("9router transport unavailable") from exc
    if response.status_code >= 400:
        raise NineRouterHTTPError(response.status_code, f"9router returned HTTP {response.status_code}")
    try:
        payload = response.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("9router returned a malformed completion") from exc
    if not _response_model_matches(model, payload.get("model")):
        raise ValueError("9router response model did not match the requested physical model")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("9router returned an empty completion")
    return content.strip()


async def classify_intent_llm(text: str, **route) -> str:
    result = await chat_completion(
        messages=[{"role": "user", "content": (
            "Classify this message using only one category: recruitment, salary_query, "
            "payment_due, escort_duty, complaint, client_order, leave, join, attendance, "
            f"slip_submission, voice_note, greeting, unknown. Message: {text[:300]}"
        )}], max_tokens=10, temperature=0, timeout=15, **_route_args(route),
    )
    value = result.lower().split()[0]
    return value if value in {
        "recruitment", "salary_query", "payment_due", "escort_duty", "complaint",
        "client_order", "leave", "join", "attendance", "slip_submission",
        "voice_note", "greeting", "unknown",
    } else "unknown"


async def generate_reply(user_message: str, intent: str, db_context: str = "", history: str = "", role: str = "new_lead", source: str = "unknown", **route) -> str:
    prompt = build_whatsapp_reply_policy(source=source, user_message=user_message, role=role, intent=intent, db_context=db_context, history=history)
    return await chat_completion(messages=[{"role": "user", "content": prompt}], max_tokens=200, temperature=.4, **_route_args(route))


async def generate_recruitment_reply(user_message: str, kb_context: str, history: str = "", contact_context: str = "", source: str = "unknown", **route) -> str:
    prompt = build_whatsapp_recruitment_policy(source=source, user_message=user_message, kb_context=kb_context, history=history, contact_context=contact_context)
    return await chat_completion(messages=[{"role": "user", "content": prompt}], max_tokens=200, temperature=.2, **_route_args(route))


async def decision_call(*, system_prompt: str, user_payload: str, max_tokens: int = 300, temperature: float = .1, timeout: float = 12, **route) -> str:
    return await chat_completion(messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_payload}], max_tokens=max_tokens, temperature=temperature, timeout=timeout, **_route_args(route))


def _route_args(values: dict) -> dict:
    return {
        "route_model": values.get("route_model"),
        "route_base_url": values.get("route_base_url"),
        "route_api_key": values.get("route_api_key"),
    }
