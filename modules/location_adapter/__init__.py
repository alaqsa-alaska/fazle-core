from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import asyncpg
import httpx

from app.config import get_settings
from app.database import execute

log = logging.getLogger("fazle.location_adapter")

_STATE_LOCK = asyncio.Lock()
_CACHE: dict[str, tuple[dict[str, Any], float]] = {}
_FAILURE_TIMESTAMPS: list[float] = []
_RUNTIME_STATE: dict[str, float] = {"circuit_open_until": 0.0}


def _now() -> float:
    return time.monotonic()


def _normalize_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _normalize_path(path: str) -> str:
    value = (path or "").strip()
    if not value:
        return "/"
    if not value.startswith("/"):
        value = f"/{value}"
    return value


def _cache_get(cache_key: str, ttl_s: int) -> dict[str, Any] | None:
    if ttl_s <= 0:
        return None
    entry = _CACHE.get(cache_key)
    if not entry:
        return None
    payload, ts = entry
    if _now() - ts > float(ttl_s):
        _CACHE.pop(cache_key, None)
        return None
    return payload


def _cache_put(cache_key: str, payload: dict[str, Any]) -> None:
    _CACHE[cache_key] = (payload, _now())


def _prune_failures(window_s: int) -> None:
    if window_s <= 0:
        _FAILURE_TIMESTAMPS.clear()
        return
    cutoff = _now() - float(window_s)
    while _FAILURE_TIMESTAMPS and _FAILURE_TIMESTAMPS[0] < cutoff:
        _FAILURE_TIMESTAMPS.pop(0)


def _circuit_state() -> str:
    return "open" if _now() < _RUNTIME_STATE["circuit_open_until"] else "closed"


async def _record_failure() -> None:
    settings = get_settings()
    async with _STATE_LOCK:
        _FAILURE_TIMESTAMPS.append(_now())
        _prune_failures(settings.location_adapter_circuit_window_s)
        if len(_FAILURE_TIMESTAMPS) >= settings.location_adapter_circuit_max_failures:
            _RUNTIME_STATE["circuit_open_until"] = _now() + float(settings.location_adapter_circuit_open_s)


async def _record_success() -> None:
    async with _STATE_LOCK:
        _FAILURE_TIMESTAMPS.clear()
        _RUNTIME_STATE["circuit_open_until"] = 0.0


async def _write_audit(
    *,
    event_type: str,
    target_key: str,
    request_path: str,
    attempts: int,
    cache_hit: bool,
    success: bool,
    http_status: int | None,
    latency_ms: int,
    circuit_state: str,
    error_text: str | None,
    response_payload: dict[str, Any] | None,
) -> None:
    try:
        payload_json = json.dumps(response_payload or {}, ensure_ascii=False)
        await execute(
            """
            INSERT INTO assistant_ext_location_audit (
              event_type,
              target_key,
              request_path,
              attempts,
              cache_hit,
              success,
              http_status,
              latency_ms,
              circuit_state,
              error_text,
              response_payload,
              created_by
            ) VALUES (
              $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12
            )
            """,
            event_type,
            target_key,
            request_path,
            attempts,
            cache_hit,
            success,
            http_status,
            latency_ms,
            circuit_state,
            error_text,
            payload_json,
            "system",
        )
    except (asyncpg.PostgresError, RuntimeError, TypeError, ValueError) as exc:
        log.warning("[location_adapter] audit write failed: %s", exc)


async def _read_get(
    *,
    event_type: str,
    target_key: str,
    request_path: str,
    params: dict[str, Any] | None = None,
    cache_key: str | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    settings = get_settings()

    if use_cache and cache_key:
        cached = _cache_get(cache_key, settings.location_adapter_cache_ttl_s)
        if cached is not None:
            result = {
                "ok": True,
                "source": "locationwhere",
                "event_type": event_type,
                "target_key": target_key,
                "cache_hit": True,
                "attempts": 0,
                "circuit_state": _circuit_state(),
                "status_code": 200,
                "data": cached,
                "error": None,
            }
            await _write_audit(
                event_type=event_type,
                target_key=target_key,
                request_path=request_path,
                attempts=0,
                cache_hit=True,
                success=True,
                http_status=200,
                latency_ms=0,
                circuit_state=_circuit_state(),
                error_text=None,
                response_payload=cached,
            )
            return result

    if _circuit_state() == "open":
        result = {
            "ok": False,
            "source": "locationwhere",
            "event_type": event_type,
            "target_key": target_key,
            "cache_hit": False,
            "attempts": 0,
            "circuit_state": "open",
            "status_code": None,
            "data": None,
            "error": "circuit_open",
        }
        await _write_audit(
            event_type=event_type,
            target_key=target_key,
            request_path=request_path,
            attempts=0,
            cache_hit=False,
            success=False,
            http_status=None,
            latency_ms=0,
            circuit_state="open",
            error_text="circuit_open",
            response_payload=None,
        )
        return result

    base = _normalize_base_url(settings.locationwhere_base_url)
    path = _normalize_path(request_path)
    url = f"{base}{path}"

    headers: dict[str, str] = {"Accept": "application/json"}
    if settings.locationwhere_jwt_token:
        headers["Authorization"] = f"Bearer {settings.locationwhere_jwt_token}"

    max_attempts = max(1, int(settings.location_adapter_retries) + 1)
    timeout_s = max(0.1, float(settings.location_adapter_timeout_s))
    last_error = "request_failed"
    last_status: int | None = None
    started = _now()

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                response = await client.get(url, params=params, headers=headers)

            last_status = int(response.status_code)

            if response.status_code >= 500:
                last_error = f"http_{response.status_code}"
                await _record_failure()
                if attempt < max_attempts:
                    await asyncio.sleep(0.05 * attempt)
                    continue

            if response.status_code >= 400:
                elapsed_ms = int((_now() - started) * 1000)
                result = {
                    "ok": False,
                    "source": "locationwhere",
                    "event_type": event_type,
                    "target_key": target_key,
                    "cache_hit": False,
                    "attempts": attempt,
                    "circuit_state": _circuit_state(),
                    "status_code": response.status_code,
                    "data": None,
                    "error": f"http_{response.status_code}",
                }
                await _write_audit(
                    event_type=event_type,
                    target_key=target_key,
                    request_path=path,
                    attempts=attempt,
                    cache_hit=False,
                    success=False,
                    http_status=response.status_code,
                    latency_ms=elapsed_ms,
                    circuit_state=_circuit_state(),
                    error_text=result["error"],
                    response_payload=None,
                )
                return result

            try:
                payload = response.json()
            except ValueError:
                payload = {"raw": response.text}

            await _record_success()

            if use_cache and cache_key:
                _cache_put(cache_key, payload)

            elapsed_ms = int((_now() - started) * 1000)
            result = {
                "ok": True,
                "source": "locationwhere",
                "event_type": event_type,
                "target_key": target_key,
                "cache_hit": False,
                "attempts": attempt,
                "circuit_state": _circuit_state(),
                "status_code": response.status_code,
                "data": payload,
                "error": None,
            }
            await _write_audit(
                event_type=event_type,
                target_key=target_key,
                request_path=path,
                attempts=attempt,
                cache_hit=False,
                success=True,
                http_status=response.status_code,
                latency_ms=elapsed_ms,
                circuit_state=_circuit_state(),
                error_text=None,
                response_payload=payload,
            )
            return result
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = type(exc).__name__
            await _record_failure()
            if attempt < max_attempts:
                await asyncio.sleep(0.05 * attempt)
                continue

    elapsed_ms = int((_now() - started) * 1000)
    result = {
        "ok": False,
        "source": "locationwhere",
        "event_type": event_type,
        "target_key": target_key,
        "cache_hit": False,
        "attempts": max_attempts,
        "circuit_state": _circuit_state(),
        "status_code": last_status,
        "data": None,
        "error": last_error,
    }
    await _write_audit(
        event_type=event_type,
        target_key=target_key,
        request_path=path,
        attempts=max_attempts,
        cache_hit=False,
        success=False,
        http_status=last_status,
        latency_ms=elapsed_ms,
        circuit_state=_circuit_state(),
        error_text=last_error,
        response_payload=None,
    )
    return result


async def get_employee_location(employee_id: str | int) -> dict[str, Any]:
    settings = get_settings()
    path = _normalize_path(settings.locationwhere_employee_location_endpoint)
    target = str(employee_id).strip()
    if not target:
        return {
            "ok": False,
            "source": "locationwhere",
            "event_type": "employee_location",
            "target_key": "",
            "cache_hit": False,
            "attempts": 0,
            "circuit_state": _circuit_state(),
            "status_code": None,
            "data": None,
            "error": "employee_id_required",
        }
    return await _read_get(
        event_type="employee_location",
        target_key=target,
        request_path=path,
        params={"employeeId": target},
        cache_key=f"employee_location:{target}",
        use_cache=True,
    )


async def get_device_status(device_id: str) -> dict[str, Any]:
    settings = get_settings()
    target = (device_id or "").strip()
    if not target:
        return {
            "ok": False,
            "source": "locationwhere",
            "event_type": "device_status",
            "target_key": "",
            "cache_hit": False,
            "attempts": 0,
            "circuit_state": _circuit_state(),
            "status_code": None,
            "data": None,
            "error": "device_id_required",
        }

    endpoint = settings.locationwhere_device_status_endpoint.format(device_id=target)
    return await _read_get(
        event_type="device_status",
        target_key=target,
        request_path=endpoint,
        params=None,
        cache_key=f"device_status:{target}",
        use_cache=True,
    )


async def check_location_service_health() -> dict[str, Any]:
    settings = get_settings()
    return await _read_get(
        event_type="service_health",
        target_key="locationwhere",
        request_path=settings.locationwhere_health_endpoint,
        params=None,
        cache_key=None,
        use_cache=False,
    )