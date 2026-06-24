"""In-process Ollama daemon helpers.

This keeps persistent HTTP clients alive for Ollama traffic, warms the active
model at startup, and exposes lightweight diagnostics. It is intentionally not
a separate OS service; systemd already owns the real Ollama daemon.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("fazle.ollama_daemon")

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_started_at: float | None = None
_last_warm_at: float | None = None
_last_error: str | None = None
_request_count = 0


async def start() -> None:
    """Initialize the shared Ollama HTTP client."""
    await _get_client()


async def stop() -> None:
    """Close the shared Ollama HTTP client."""
    global _client
    async with _client_lock:
        if _client is not None:
            await _client.aclose()
            _client = None


async def _get_client() -> httpx.AsyncClient:
    global _client, _started_at
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            _client = httpx.AsyncClient(
                limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
                timeout=httpx.Timeout(120.0, connect=5.0),
            )
            _started_at = time.time()
            log.info("[ollama_daemon] shared client started")
    return _client


async def post_generate(payload: dict[str, Any], timeout: float = 120.0) -> httpx.Response:
    """POST /api/generate through the shared client."""
    global _request_count, _last_error
    settings = get_settings()
    client = await _get_client()
    try:
        response = await client.post(
            f"{settings.ollama_url}/api/generate",
            json=payload,
            timeout=timeout,
        )
        _request_count += 1
        _last_error = None
        return response
    except Exception as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        raise


async def get_tags(timeout: float = 5.0) -> httpx.Response:
    """GET /api/tags through the shared client."""
    global _request_count, _last_error
    settings = get_settings()
    client = await _get_client()
    try:
        response = await client.get(f"{settings.ollama_url}/api/tags", timeout=timeout)
        _request_count += 1
        _last_error = None
        return response
    except Exception as exc:
        _last_error = f"{type(exc).__name__}: {exc}"
        raise


async def warm_model(model: str | None = None) -> dict[str, Any]:
    """Warm the active model with a tiny generation request."""
    global _last_warm_at
    settings = get_settings()
    active_model = model or settings.ollama_model
    payload: dict[str, Any] = {
        "model": active_model,
        "prompt": "Reply with OK.",
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 2, "num_ctx": 128},
    }
    if active_model.startswith("qwen3:"):
        payload["think"] = False
    t0 = time.monotonic()
    response = await post_generate(payload, timeout=60.0)
    latency_ms = int((time.monotonic() - t0) * 1000)
    _last_warm_at = time.time()
    return {"status_code": response.status_code, "latency_ms": latency_ms, "model": active_model}


def diagnostics() -> dict[str, Any]:
    return {
        "client_started": _client is not None,
        "started_at": _started_at,
        "last_warm_at": _last_warm_at,
        "last_error": _last_error,
        "request_count": _request_count,
    }
