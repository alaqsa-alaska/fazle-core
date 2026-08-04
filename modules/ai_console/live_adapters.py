"""Live-safe readonly adapters for the Fazle AI Operating Console."""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

log = logging.getLogger("fazle.ai_console.live_adapters")

from app import llm as ai
from app.config import get_settings
from app.database import get_pool
from modules import ai_readonly_tools, dispatch, ollama_memory, rag, rbac
from modules.ai_console.adapters import BusinessAdapter, EvidenceAdapter, MemoryAdapter, RuntimeAdapter
from modules.ai_console.envelopes import EvidenceEnvelope, ToolResultEnvelope, utc_now_iso
from modules.ai_console.registry import RegistryLoader, build_registry_loader
from shared.bridge_orchestrator import get_bridge_diagnostics
from shared.runtime_gateway import get_current_runtime_nodes


# RBAC for business tools (Stage E). Role levels come from the live
# fazle_roles table (never hardcoded), cached per-process since the
# hierarchy itself changes rarely -- a stale cache only ever fails toward
# requiring MORE access, never less, because an unmapped/unresolvable role
# name falls back to "admin" at the registry level (registry.py's
# _BUS_MIN_ROLE.get(rid, "admin")).
_role_level_cache: dict[str, int] = {}


async def _resolve_min_role_level(role_name: str) -> int:
    if role_name not in _role_level_cache:
        try:
            _role_level_cache[role_name] = await rbac.get_role_level(role_name)
        except Exception:
            # Fail closed: an unresolvable role name requires the highest
            # level rather than granting access.
            return 10_000
    return _role_level_cache[role_name]


_ISO_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def _extract_date(text: str) -> str | None:
    match = _ISO_DATE_RE.search(text or "")
    return match.group(1) if match else None


def _extract_shift_hint(text: str) -> str | None:
    q = (text or "").lower()
    if "night" in q or "রাত" in (text or ""):
        return "N"
    if "day" in q or "দিন" in (text or ""):
        return "D"
    return None


CANONICAL_RUNTIME_INVENTORY_PATH = "/api/runtime/nodes/current"

_DEFAULT_SYSTEMD_KEYWORDS = (
    "fazle",
    "nginx",
    "ollama",
    "redis",
    "postgres",
    "docker",
    "pm2",
    "qdrant",
)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _safe_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}"
    text = re.sub(r"(token|key|secret|password)=\\S+", r"\\1=[redacted]", text, flags=re.IGNORECASE)
    return text[:240]


def _redact_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    host = parsed.hostname or "unknown"
    port = parsed.port
    scheme = parsed.scheme or "http"
    path = parsed.path or ""
    port_part = f":{port}" if port else ""
    return f"{scheme}://{host}{port_part}{path}"


# Bangla digits → Arabic digits mapping for normalization.
_BANGLA_TO_ARABIC = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def _normalize_bangla_digits(text: str) -> str:
    """Convert Bengali numerals to ASCII numerals."""
    return text.translate(_BANGLA_TO_ARABIC)


def _normalize_question(payload: dict[str, Any]) -> str:
    return str(
        payload.get("question")
        or payload.get("query")
        or payload.get("message")
        or ""
    ).strip()


def _extract_phone(text: str) -> str | None:
    # First normalize Bangla digits to ASCII, then strip non-digits.
    normalized = _normalize_bangla_digits(text or "")
    digits = re.sub(r"\D+", "", normalized)
    return digits if len(digits) >= 8 else None


def _extract_name_hint(text: str) -> str | None:
    # Normalize Bangla digits first so numbers are treated as ASCII digits and removed.
    text = _normalize_bangla_digits(text or "")
    cleaned = re.sub(r"\d+", " ", text)
    cleaned = re.sub(
        r"(employee|mobile|phone|payroll|attendance|escort|ledger|cash|summary|record|details|search|find|name|number|salary|history|কর্মচারী|কর্মচারি|মোবাইল|নাম্বার|রেকর্ড|খুঁজে|বেতন|হাজিরা|অ্যাটেন্ডেন্স|এসকর্ট|লেজার|ক্যাশ|নিয়োগ|নামে|কতজন|আছে|ডাটাবেসে|ডাটাবেস|কে|কত|দিন|মাসে|বছরে|তালিকা|দেখাও|বলো)",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,:;?।")
    return cleaned or None


def _extract_month_year(payload: dict[str, Any], question: str) -> tuple[int | None, int | None]:
    month = payload.get("month")
    year = payload.get("year")
    if isinstance(month, int) and isinstance(year, int):
        return month, year
    iso = re.search(r"(20\\d{2})[-/](\\d{1,2})", question)
    if iso:
        return int(iso.group(2)), int(iso.group(1))
    year_match = re.search(r"(20\\d{2})", question)
    found_year = int(year_match.group(1)) if year_match else None
    month_words = {
        "jan": 1, "january": 1,
        "feb": 2, "february": 2,
        "mar": 3, "march": 3,
        "apr": 4, "april": 4,
        "may": 5,
        "jun": 6, "june": 6,
        "jul": 7, "july": 7,
        "aug": 8, "august": 8,
        "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10,
        "nov": 11, "november": 11,
        "dec": 12, "december": 12,
    }
    q_lower = question.lower()
    for token, value in month_words.items():
        if token in q_lower:
            return value, found_year
    return (month if isinstance(month, int) else None, found_year)


async def _run_command(args: list[str], timeout: float = 5.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 124, "", "timeout"
    return proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")


async def _http_probe(url: str, timeout: float = 3.0) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for candidate in (f"{url.rstrip('/')}/health", url):
            try:
                response = await client.get(candidate)
                return {
                    "ok": response.status_code < 500,
                    "url": _redact_url(str(response.request.url)),
                    "status_code": response.status_code,
                }
            except Exception:
                continue
    return {"ok": False, "url": _redact_url(url), "status_code": None}


def _envelope(
    *,
    entry,
    status: str,
    data: dict[str, Any],
    source: str,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    confidence: str | None = None,
    traceability: list[str] | None = None,
) -> ToolResultEnvelope:
    return ToolResultEnvelope(
        tool_id=entry.id,
        tool_name=entry.name,
        status=status,
        confidence=confidence or entry.confidence_default,
        source=source,
        timestamp=utc_now_iso(),
        data=data,
        authority=entry.authority,
        warnings=warnings or [],
        errors=errors or [],
        traceability=_dedupe((traceability or []) + entry.traceability_refs + [entry.id, "EVID-011"]),
    )


class ReadonlyRuntimeAdapter(RuntimeAdapter):
    """Readonly runtime facts from structured commands and approved helpers."""

    def __init__(self, registry: RegistryLoader | None = None) -> None:
        self.registry = registry or build_registry_loader()

    async def fetch(self, tool_id: str, payload: dict[str, Any]) -> ToolResultEnvelope:
        entry = self.registry.get(tool_id)
        handlers: dict[str, Callable[[Any, dict[str, Any]], Any]] = {
            "RT-001": self._systemd_status,
            "RT-002": self._docker_status,
            "RT-003": self._pm2_status,
            "RT-004": self._nginx_status,
            "RT-005": self._ports_status,
            "RT-006": self._disk_status,
            "RT-007": self._ram_status,
            "RT-008": self._cpu_status,
            "RT-009": self._postgres_status,
            "RT-010": self._redis_status,
            "RT-011": self._qdrant_status,
            "RT-012": self._ollama_status,
            "RT-013": self._bridges_status,
            "RT-014": self._media_processor_status,
            "RT-015": self._error_log_lookup,
        }
        handler = handlers.get(tool_id)
        if handler is None:
            return _envelope(
                entry=entry,
                status="not_implemented",
                data={"message": "runtime tool is not implemented"},
                source="ai_console.runtime",
                confidence="Unknown",
            )
        try:
            return await handler(entry, payload)
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"message": "runtime adapter failed"},
                source="ai_console.runtime",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )

    async def _systemd_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        code, stdout, stderr = await _run_command(
            ["systemctl", "list-units", "--type=service", "--all", "--no-pager", "--no-legend", "--plain"],
            timeout=6.0,
        )
        if code != 0:
            return _envelope(
                entry=entry,
                status="unavailable",
                data={"services": []},
                source="systemctl",
                errors=[stderr.strip()[:200] or "systemctl unavailable"],
                confidence="Unknown",
            )
        requested = [str(item).lower() for item in payload.get("services", []) if str(item).strip()]
        filtered: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            unit, load, active, sub, description = parts
            unit_lower = unit.lower()
            desc_lower = description.lower()
            if requested:
                if not any(item in unit_lower or item in desc_lower for item in requested):
                    continue
            elif not any(token in unit_lower or token in desc_lower for token in _DEFAULT_SYSTEMD_KEYWORDS):
                continue
            filtered.append(
                {
                    "unit": unit,
                    "load": load,
                    "active": active,
                    "sub": sub,
                    "description": description[:120],
                }
            )
        status = "ok" if filtered else "partial"
        return _envelope(
            entry=entry,
            status=status,
            data={"services": filtered, "count": len(filtered)},
            source="systemctl",
        )

    async def _docker_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        code, stdout, stderr = await _run_command(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            timeout=8.0,
        )
        if code != 0:
            return _envelope(
                entry=entry,
                status="unavailable",
                data={"containers": []},
                source="docker",
                errors=[stderr.strip()[:200] or "docker unavailable"],
                confidence="Unknown",
            )
        containers: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            containers.append(
                {
                    "name": row.get("Names"),
                    "image": row.get("Image"),
                    "state": row.get("State"),
                    "status": row.get("Status"),
                    "ports": row.get("Ports"),
                }
            )
        return _envelope(
            entry=entry,
            status="ok",
            data={"containers": containers, "count": len(containers)},
            source="docker",
        )

    async def _pm2_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        code, stdout, stderr = await _run_command(["pm2", "jlist"], timeout=8.0)
        if code != 0:
            return _envelope(
                entry=entry,
                status="unavailable",
                data={"processes": []},
                source="pm2",
                errors=[stderr.strip()[:200] or "pm2 unavailable"],
                confidence="Unknown",
            )
        try:
            # pm2 jlist may output JSON to stderr on some versions; strip whitespace
            raw = stdout.strip() or stderr.strip() or "[]"
            items = json.loads(raw)
        except json.JSONDecodeError as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"processes": []},
                source="pm2",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )
        processes = [
            {
                "name": item.get("name"),
                "pid": item.get("pid"),
                "status": (item.get("pm2_env") or {}).get("status"),
                "cpu": ((item.get("monit") or {}).get("cpu")),
                "memory": ((item.get("monit") or {}).get("memory")),
            }
            for item in items
        ]
        return _envelope(
            entry=entry,
            status="ok",
            data={"processes": processes, "count": len(processes)},
            source="pm2",
        )

    async def _nginx_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        code, stdout, stderr = await _run_command(
            ["systemctl", "show", "nginx", "--property=ActiveState,SubState,UnitFileState,FragmentPath"],
            timeout=4.0,
        )
        if code != 0:
            return _envelope(
                entry=entry,
                status="unavailable",
                data={"service": "nginx"},
                source="systemctl",
                errors=[stderr.strip()[:200] or "nginx service unavailable"],
                confidence="Unknown",
            )
        values: dict[str, str] = {}
        for line in stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        status = "ok" if values.get("ActiveState") == "active" else "partial"
        return _envelope(
            entry=entry,
            status=status,
            data={
                "service": "nginx",
                "active_state": values.get("ActiveState"),
                "sub_state": values.get("SubState"),
                "unit_file_state": values.get("UnitFileState"),
                "fragment_path": values.get("FragmentPath"),
            },
            source="systemctl",
        )

    async def _ports_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        code, stdout, stderr = await _run_command(["ss", "-tulpnH"], timeout=6.0)
        if code != 0:
            return _envelope(
                entry=entry,
                status="unavailable",
                data={"listeners": []},
                source="ss",
                errors=[stderr.strip()[:200] or "ss unavailable"],
                confidence="Unknown",
            )
        listeners: list[dict[str, Any]] = []
        for line in stdout.splitlines()[:100]:
            parts = line.split(None, 6)
            if len(parts) < 6:
                continue
            proto, state, recv_q, send_q, local_addr, peer_addr = parts[:6]
            process = parts[6] if len(parts) > 6 else ""
            port = local_addr.rsplit(":", 1)[-1] if ":" in local_addr else local_addr
            listeners.append(
                {
                    "protocol": proto,
                    "state": state,
                    "local_address": local_addr,
                    "port": port,
                    "process": process[:120],
                }
            )
        return _envelope(
            entry=entry,
            status="ok",
            data={"listeners": listeners, "count": len(listeners)},
            source="ss",
        )

    async def _disk_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        target = Path(str(payload.get("path") or "/"))
        usage = shutil.disk_usage(target)
        used = usage.total - usage.free
        used_pct = round((used / usage.total) * 100, 2) if usage.total else 0.0
        return _envelope(
            entry=entry,
            status="ok",
            data={
                "path": str(target),
                "total_bytes": usage.total,
                "used_bytes": used,
                "free_bytes": usage.free,
                "used_percent": used_pct,
            },
            source="python",
        )

    async def _ram_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        meminfo: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            meminfo[key] = int(value.strip().split()[0]) * 1024
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used = max(total - available, 0)
        used_pct = round((used / total) * 100, 2) if total else 0.0
        return _envelope(
            entry=entry,
            status="ok",
            data={
                "total_bytes": total,
                "available_bytes": available,
                "used_bytes": used,
                "used_percent": used_pct,
                "swap_total_bytes": meminfo.get("SwapTotal", 0),
                "swap_free_bytes": meminfo.get("SwapFree", 0),
            },
            source="procfs",
        )

    async def _cpu_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        load_1, load_5, load_15 = os.getloadavg()
        cpu_count = os.cpu_count() or 1
        uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
        return _envelope(
            entry=entry,
            status="ok",
            data={
                "cpu_count": cpu_count,
                "load_1": load_1,
                "load_5": load_5,
                "load_15": load_15,
                "load_per_cpu_1": round(load_1 / cpu_count, 3),
                "uptime_seconds": int(uptime_seconds),
            },
            source="python",
        )

    async def _postgres_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        import asyncpg

        settings = get_settings()
        # Create a direct one-off connection so this check works both inside the
        # FastAPI lifespan (pool initialized) AND in the acceptance runner script
        # (pool never initialized).  Read-only SELECT 1 — no business writes.
        try:
            conn = await asyncio.wait_for(
                asyncpg.connect(settings.database_url, timeout=5.0),
                timeout=8.0,
            )
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"message": "postgres connection failed"},
                source="asyncpg",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )
        try:
            value = await conn.fetchval("SELECT 1")
            version = await conn.fetchval("SELECT current_setting('server_version')")
        finally:
            await conn.close()
        return _envelope(
            entry=entry,
            status="ok" if value == 1 else "partial",
            data={"reachable": value == 1, "server_version": str(version)},
            source="asyncpg",
        )

    @staticmethod
    async def _resolve_redis_url() -> str:
        """Return the best available Redis URL, resolving REDIS_URL_TEMPLATE when
        needed.  Never prints or logs the password."""
        settings = get_settings()
        redis_url = settings.redis_url

        # If the configured URL has no auth component try to resolve the template.
        # Default redis://localhost:6379/9 has no '@', so it lacks auth.
        if "@" not in redis_url:
            template = os.environ.get("REDIS_URL_TEMPLATE", "")
            if template and "__HOST__" in template:
                # Try to resolve __HOST__ via docker inspect (async, no subprocess import).
                host = ""
                try:
                    code, stdout, _stderr = await _run_command(
                        ["docker", "inspect", "ai-redis"], timeout=5.0
                    )
                    if code == 0 and stdout.strip():
                        data = json.loads(stdout)
                        nets = data[0]["NetworkSettings"]["Networks"]
                        host = (
                            nets.get("db-network", {}).get("IPAddress", "")
                            or next(iter(nets.values()), {}).get("IPAddress", "")
                        )
                except Exception:
                    pass
                if not host:
                    # Redis port is bound to 127.0.0.1:6379 on the host
                    host = "127.0.0.1"
                redis_url = template.replace("__HOST__", host)
        return redis_url

    async def _redis_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        from redis import asyncio as aioredis

        redis_url = await self._resolve_redis_url()
        has_auth = "@" in redis_url
        parsed = urlparse(redis_url)
        client = aioredis.from_url(redis_url, socket_connect_timeout=2)
        try:
            ping = await client.ping()
            return _envelope(
                entry=entry,
                status="ok" if ping else "partial",
                data={
                    "reachable": bool(ping),
                    "host": parsed.hostname,
                    "port": parsed.port,
                    "db": (parsed.path or "/0").lstrip("/"),
                },
                source="redis",
            )
        except Exception as exc:
            err_text = _safe_error(exc)
            if not has_auth and ("NOAUTH" in str(exc) or "Authentication" in str(exc)):
                return _envelope(
                    entry=entry,
                    status="unavailable",
                    data={
                        "reachable": False,
                        "host": parsed.hostname,
                        "port": parsed.port,
                    },
                    source="redis",
                    errors=[err_text],
                    warnings=[
                        "REDIS_URL has no auth credentials. "
                        "Add REDIS_URL to runtime-services.env with password, "
                        "or ensure generate_runtime_services_env.sh resolves REDIS_URL_TEMPLATE."
                    ],
                    confidence="Unknown",
                )
            return _envelope(
                entry=entry,
                status="failed",
                data={"reachable": False, "host": parsed.hostname, "port": parsed.port},
                source="redis",
                errors=[err_text],
                confidence="Unknown",
            )
        finally:
            await client.aclose()

    async def _qdrant_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        data = await rag.qdrant_status()
        status = "ok" if data.get("status") == "ok" else "partial"
        confidence = "Verified" if data.get("status") == "ok" else "Partially Verified"
        return _envelope(
            entry=entry,
            status=status,
            data=data,
            source="modules.rag",
            confidence=confidence,
        )

    async def _ollama_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        data = await ai.check_ollama_health()
        status = "ok" if data.get("status") == "ok" else "partial"
        confidence = "Verified" if data.get("status") == "ok" else "Partially Verified"
        return _envelope(
            entry=entry,
            status=status,
            data=data,
            source="app.llm",
            confidence=confidence,
        )

    async def _bridges_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        diagnostics = await get_bridge_diagnostics()
        nodes = await get_current_runtime_nodes()
        return _envelope(
            entry=entry,
            status="ok",
            data={
                "diagnostics": diagnostics,
                "current_runtime_nodes": nodes,
                "canonical_runtime_inventory_path": CANONICAL_RUNTIME_INVENTORY_PATH,
            },
            source="bridge_orchestrator",
        )

    async def _media_processor_status(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        settings = get_settings()
        data = await _http_probe(settings.media_processor_url)
        status = "ok" if data.get("ok") else "partial"
        confidence = "Verified" if data.get("ok") else "Partially Verified"
        return _envelope(
            entry=entry,
            status=status,
            data=data,
            source="http_probe",
            confidence=confidence,
        )

    async def _error_log_lookup(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        from modules.observability.db_queries import get_recent_errors

        try:
            limit = int(payload.get("limit") or 50)
        except (TypeError, ValueError):
            limit = 50
        try:
            rows = await get_recent_errors(limit=limit)
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"rows": []},
                source="modules.observability.db_queries",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )
        return _envelope(
            entry=entry,
            status="ok" if rows else "partial",
            data={"rows": rows, "count": len(rows)},
            source="modules.observability.db_queries",
        )


class ReadonlyBusinessAdapter(BusinessAdapter):
    """Readonly business adapters backed by approved business read modules."""

    def __init__(self, registry: RegistryLoader | None = None) -> None:
        self.registry = registry or build_registry_loader()

    async def fetch(
        self, tool_id: str, payload: dict[str, Any], *, actor_role_level: int = 100,
    ) -> ToolResultEnvelope:
        entry = self.registry.get(tool_id)
        min_role = str(entry.metadata.get("min_role") or "admin")
        required_level = await _resolve_min_role_level(min_role)
        if actor_role_level < required_level:
            log.info(
                "[ai_console.business.rbac] denied tool=%s min_role=%s required_level=%d actor_level=%d",
                tool_id, min_role, required_level, actor_role_level,
            )
            return _envelope(
                entry=entry,
                status="permission_denied",
                data={"message": f"requires {min_role} role or above", "min_role": min_role},
                source="ai_console.business",
                warnings=[f"Tool {tool_id} requires {min_role} role or above."],
                confidence="Unknown",
            )
        handlers: dict[str, Callable[[Any, dict[str, Any]], Any]] = {
            "BUS-001": self._employee_search,
            "BUS-002": self._employee_summary,
            "BUS-003": self._payroll_summary,
            "BUS-004": self._attendance_summary,
            "BUS-005": self._escort_summary,
            "BUS-006": self._cash_ledger_summary,
            "BUS-007": self._recruitment_summary,
            "BUS-008": self._knowledge_search,
            "BUS-009": self._conversation_history,
            "BUS-010": self._dispatch_availability,
            "BUS-011": self._dispatch_conflict_check,
            "BUS-012": self._dispatch_suggestions,
            "BUS-013": self._missing_end_date_programs,
            "BUS-014": self._employee_advances,
            "BUS-015": self._admin_directive_read,
            "BUS-016": self._admin_directive_write,
            "BUS-017": self._payroll_page_summary,
            "BUS-018": self._escort_roster_page_summary,
        }
        handler = handlers.get(tool_id)
        if handler is None:
            return _envelope(
                entry=entry,
                status="not_implemented",
                data={"message": "business tool is not implemented"},
                source="ai_console.business",
                confidence="Unknown",
            )
        try:
            return await handler(entry, payload)
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"message": "business adapter failed"},
                source="ai_console.business",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )

    async def _employee_search(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        # Normalize Bangla digits in the full question so embedded numbers are usable.
        question = _normalize_bangla_digits(question)
        query = str(payload.get("employee_name") or payload.get("query") or _extract_name_hint(question) or "").strip()
        phone = str(payload.get("employee_mobile") or _extract_phone(question) or "").strip()
        # Safe debug logging without secrets.
        log.info(
            "[ai_console.employee_search] original_question=%r normalized_question=%r extracted_phone=%r query=%r",
            _normalize_question(payload),
            question,
            phone,
            query,
        )
        status_value = str(payload.get("status") or "active")
        # The DB view stores status as title-case 'Active'/'Inactive'.
        status_value = status_value.capitalize()
        employees = await ai_readonly_tools.get_employee_list(status=status_value)
        filtered = employees
        if phone:
            filtered = [row for row in filtered if phone[-8:] in re.sub(r"\\D+", "", str(row.get("employee_mobile") or ""))]
        elif query:
            q_lower = query.lower()
            filtered = [row for row in filtered if q_lower in str(row.get("employee_name") or "").lower()]
        filtered = filtered[:10]
        suggestions: list[str] = []
        if not filtered and query:
            names = [str(row.get("employee_name") or "") for row in employees if row.get("employee_name")]
            suggestions = difflib.get_close_matches(query, names, n=5, cutoff=0.5)
        status = "ok" if filtered else "partial"
        confidence = "Verified" if filtered else "Partially Verified"
        log.info(
            "[ai_console.employee_search] result_count=%d suggestions_count=%d",
            len(filtered),
            len(suggestions),
        )
        return _envelope(
            entry=entry,
            status=status,
            data={
                "query": query or phone,
                "matches": filtered,
                "count": len(filtered),
                "suggestions": suggestions,
            },
            source="modules.ai_readonly_tools",
            confidence=confidence,
        )

    async def _employee_summary(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        search_result = await self._employee_search(self.registry.get("BUS-001"), payload)
        matches = search_result.data.get("matches", [])
        if not matches:
            return _envelope(
                entry=entry,
                status="partial",
                data={"employee": None, "suggestions": search_result.data.get("suggestions", [])},
                source="modules.ai_readonly_tools",
                confidence="Partially Verified",
            )
        employee = matches[0]
        name = str(employee.get("employee_name") or "")
        payroll = await ai_readonly_tools.get_payment_summary(employee_name=name)
        attendance = await ai_readonly_tools.get_attendance_summary(employee_name=name)
        contact = {}
        if employee.get("employee_mobile"):
            contact = await ai_readonly_tools.get_contact_summary(str(employee["employee_mobile"]))
        return _envelope(
            entry=entry,
            status="ok",
            data={
                "employee": employee,
                "payroll_recent": payroll[:5],
                "attendance_recent": attendance[:10],
                "contact": contact,
            },
            source="modules.ai_readonly_tools",
        )

    async def _payroll_summary(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        month, year = _extract_month_year(payload, question)
        rows = await ai_readonly_tools.get_payment_summary(
            employee_name=payload.get("employee_name") or _extract_name_hint(question),
            month=month,
            year=year,
        )
        return _envelope(
            entry=entry,
            status="ok",
            data={"rows": rows, "count": len(rows), "month": month, "year": year},
            source="modules.ai_readonly_tools",
        )

    async def _attendance_summary(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        month, _ = _extract_month_year(payload, question)
        rows = await ai_readonly_tools.get_attendance_summary(
            employee_name=payload.get("employee_name") or _extract_name_hint(question),
            month=month,
        )
        return _envelope(
            entry=entry,
            status="ok",
            data={"rows": rows, "count": len(rows), "month": month},
            source="modules.ai_readonly_tools",
        )

    async def _escort_summary(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        rows = await ai_readonly_tools.get_escort_program_status(
            vessel=payload.get("vessel") or _extract_name_hint(question),
            date_str=payload.get("date"),
            status=payload.get("status"),
        )
        return _envelope(
            entry=entry,
            status="ok",
            data={"rows": rows, "count": len(rows)},
            source="modules.ai_readonly_tools",
        )

    async def _cash_ledger_summary(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        if payload.get("employee_name") or payload.get("employee_mobile"):
            named_question = question or f"{payload.get('employee_name') or payload.get('employee_mobile')} payment summary"
            data = await ai_readonly_tools.get_employee_month_payments(named_question)
        else:
            data = await ai_readonly_tools.get_daily_payments(question or "today payments")
        return _envelope(
            entry=entry,
            status="ok",
            data=data,
            source="modules.ai_readonly_tools",
        )

    async def _recruitment_summary(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        limit = int(payload.get("limit") or 20)
        rows = await ai_readonly_tools.get_recruitment_leads(limit=limit)
        return _envelope(
            entry=entry,
            status="ok",
            data={"rows": rows, "count": len(rows)},
            source="modules.ai_readonly_tools",
        )

    async def _knowledge_search(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        rows = await ai_readonly_tools.get_kb_articles(
            category=payload.get("category"),
            search=payload.get("search") or question,
            limit=int(payload.get("limit") or 10),
        )
        confidence = "Partially Verified" if rows else "Unknown"
        status = "ok" if rows else "partial"
        return _envelope(
            entry=entry,
            status=status,
            data={"rows": rows, "count": len(rows)},
            source="modules.ai_readonly_tools",
            confidence=confidence,
        )

    async def _conversation_history(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        rows = await ai_readonly_tools.get_recent_messages(
            phone=payload.get("phone") or _extract_phone(question),
            limit=min(int(payload.get("limit") or 10), 20),
        )
        confidence = "Partially Verified" if rows else "Unknown"
        status = "ok" if rows else "partial"
        return _envelope(
            entry=entry,
            status=status,
            data={"rows": rows, "count": len(rows)},
            source="modules.ai_readonly_tools",
            confidence=confidence,
        )

    async def _dispatch_availability(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        employee_id = payload.get("employee_id")
        try:
            employee_id = int(employee_id) if employee_id is not None else None
        except (TypeError, ValueError):
            employee_id = None
        result = await dispatch.list_escort_workers(
            active_only=True, employee_id=employee_id, page=1,
            page_size=min(int(payload.get("limit") or 20), 50),
        )
        return _envelope(
            entry=entry,
            status="ok",
            data=result,
            source="modules.dispatch",
        )

    async def _dispatch_conflict_check(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        employee_id = payload.get("employee_id")
        start_date_s = payload.get("start_date") or _extract_date(question)
        end_date_s = payload.get("end_date") or start_date_s
        start_shift = payload.get("start_shift") or _extract_shift_hint(question) or "D"
        end_shift = payload.get("end_shift") or start_shift
        if not employee_id or not start_date_s:
            return _envelope(
                entry=entry,
                status="partial",
                data={"message": "insufficient parameters — need employee_id and a date (YYYY-MM-DD)"},
                source="modules.dispatch",
                warnings=["Could not determine employee_id/date from the question; ask with an explicit date and employee."],
                confidence="Unknown",
            )
        from datetime import date as _date
        try:
            result = await dispatch.check_conflict(
                escort_employee_id=int(employee_id),
                start_date=_date.fromisoformat(start_date_s),
                start_shift=start_shift,
                end_date=_date.fromisoformat(end_date_s),
                end_shift=end_shift,
            )
        except ValueError as exc:
            return _envelope(
                entry=entry,
                status="partial",
                data={"message": "invalid date/employee_id"},
                source="modules.dispatch",
                warnings=[_safe_error(exc)],
                confidence="Unknown",
            )
        return _envelope(entry=entry, status="ok", data=result, source="modules.dispatch")

    async def _dispatch_suggestions(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        start_date_s = payload.get("start_date") or _extract_date(question)
        end_date_s = payload.get("end_date") or start_date_s
        start_shift = payload.get("start_shift") or _extract_shift_hint(question) or "D"
        end_shift = payload.get("end_shift") or start_shift
        if not start_date_s:
            return _envelope(
                entry=entry,
                status="partial",
                data={"message": "insufficient parameters — need a date (YYYY-MM-DD)"},
                source="modules.dispatch",
                warnings=["Could not determine a date from the question; ask with an explicit date."],
                confidence="Unknown",
            )
        from datetime import date as _date
        try:
            result = await dispatch.get_suggestions(
                start_date=_date.fromisoformat(start_date_s),
                start_shift=start_shift,
                end_date=_date.fromisoformat(end_date_s),
                end_shift=end_shift,
                destination=payload.get("destination"),
                limit=min(int(payload.get("limit") or 10), 20),
            )
        except ValueError as exc:
            return _envelope(
                entry=entry,
                status="partial",
                data={"message": "invalid date"},
                source="modules.dispatch",
                warnings=[_safe_error(exc)],
                confidence="Unknown",
            )
        return _envelope(entry=entry, status="ok", data=result, source="modules.dispatch")

    async def _missing_end_date_programs(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        result = await dispatch.get_missing_end_date_programs(limit=min(int(payload.get("limit") or 50), 100))
        return _envelope(
            entry=entry,
            status="ok" if result["items"] else "partial",
            data=result,
            source="modules.dispatch",
            confidence="Partially Verified",
        )

    async def _employee_advances(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        question = _normalize_question(payload)
        rows = await ai_readonly_tools.get_employee_advances(
            employee_name=payload.get("employee_name") or _extract_name_hint(question),
            limit=min(int(payload.get("limit") or 20), 50),
        )
        confidence = "Verified" if rows else "Partially Verified"
        return _envelope(
            entry=entry,
            status="ok" if rows else "partial",
            data={"rows": rows, "count": len(rows)},
            source="modules.ai_readonly_tools",
            confidence=confidence,
        )

    async def _admin_directive_read(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        from modules.admin_directives import list_directives

        subject_key = payload.get("subject_key")
        category = payload.get("category")
        status_filter = str(payload.get("status") or "active")
        limit = min(int(payload.get("limit") or 50), 200)
        try:
            rows = await list_directives(subject_key=subject_key, status=status_filter, limit=limit)
            # list_directives() itself has no category filter (Step 2's
            # signature is subject_key/status/limit only) -- filtered here
            # in-handler instead of extending that already-verified module,
            # since this is additive-only to live_adapters.py in this step.
            if category:
                rows = [row for row in rows if row.get("category") == category]
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"rows": []},
                source="modules.admin_directives",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )
        return _envelope(
            entry=entry,
            status="ok" if rows else "partial",
            data={"rows": rows, "count": len(rows)},
            source="modules.admin_directives",
        )

    async def _admin_directive_write(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        from modules.admin_directives import revoke_directive, save_directive

        # First check, unguarded -- must raise immediately, before any DB
        # call or try/except, so an invalid action is never silently
        # swallowed into a "failed" envelope.
        action = str(payload.get("action") or "")
        if action not in ("save", "revoke"):
            raise ValueError(f"Invalid action: {action!r}")

        try:
            if action == "save":
                directive_id = await save_directive(
                    str(payload.get("directive_text") or ""),
                    created_by_phone=str(payload.get("created_by_phone") or ""),
                    created_by_channel=str(payload.get("created_by_channel") or ""),
                    category=str(payload.get("category") or "general"),
                    subject_type=payload.get("subject_type"),
                    subject_key=payload.get("subject_key"),
                    source_ref=payload.get("source_ref"),
                )
                data: dict[str, Any] = {"action": "save", "id": directive_id}
            else:
                directive_id = int(payload.get("directive_id") or 0)
                revoked = await revoke_directive(
                    directive_id, revoked_by_phone=str(payload.get("revoked_by_phone") or "")
                )
                data = {"action": "revoke", "id": directive_id, "revoked": revoked}
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"action": action},
                source="modules.admin_directives",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )
        return _envelope(
            entry=entry,
            status="ok",
            data=data,
            source="modules.admin_directives",
        )

    async def _payroll_page_summary(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        """Reads the same source payroll.html's Overview tab uses
        (fpe_cash_transactions via modules.fazle_payroll_engine.routes),
        not the empty/draft-only wbom_payroll_runs view BUS-003 reads.
        Both imported functions are plain async functions with no
        Request/Depends usage in their bodies -- safe to call directly,
        provided every list_transactions() parameter is passed explicitly
        by keyword (its defaults are FastAPI Query(...) sentinel objects,
        not real values -- confirmed truthy even when "unset")."""
        from modules.fazle_payroll_engine.routes import health as fpe_health
        from modules.fazle_payroll_engine.routes import list_transactions as fpe_list_transactions

        question = _normalize_question(payload)
        month, year = _extract_month_year(payload, question)
        period = f"{year:04d}-{month:02d}" if month and year else None

        # Owner decision 2026-08-04 (Phase E Part 6 fix): when no explicit
        # month/year is mentioned (e.g. "গতকাল কয়জনকে payment করা হয়েছে?"),
        # this previously left `period=None` and skipped the transaction
        # query entirely, silently returning an empty rows=[] that the
        # exact_answer formatter then reported as "কোনো পেমেন্ট লেনদেন পাওয়া
        # যায়নি" (no payments found) -- a confidently WRONG answer when
        # relative-date payments genuinely existed (confirmed against real
        # data: 9 transactions/৳2,000 for "yesterday" on the date this was
        # found). Reuse the existing date_from/date_to params on
        # list_transactions (already supported, previously always passed
        # as None) via the same parse_date_range() helper get_daily_payments
        # already relies on for relative-date phrases.
        date_from = date_to = None
        if not period:
            from datetime import timedelta as _timedelta
            from modules.admin_commands.date_parser import parse_date_range
            # default_days=None: only act on a genuinely recognized date
            # phrase (আজ/গতকাল/this week/etc.) -- a question with no date
            # signal at all (e.g. "payroll status") must keep returning
            # the unscoped total, not get silently narrowed to a default
            # window.
            rng = parse_date_range(question, default_days=None)
            if rng is not None:
                start_dt, end_dt, range_label = rng
                date_from = start_dt.date()
                date_to = end_dt.date() - _timedelta(days=1)  # list_transactions' date_to is inclusive
                period = range_label

        try:
            health_result = await fpe_health()
            rows: list[dict[str, Any]] = []
            if (month and year) or date_from or date_to:
                txn_result = await fpe_list_transactions(
                    employee_id=None,
                    period=period if (month and year) else None,
                    method=None,
                    txn_category=None,
                    date_from=date_from,
                    date_to=date_to,
                    page=None,
                    page_size=None,
                    limit=20,
                    offset=0,
                )
                rows = [
                    {
                        "employee_name": t.get("employee_display_name"),
                        "amount": t.get("amount"),
                        "txn_date": str(t.get("txn_date")),
                        "payout_method": t.get("payout_method"),
                        "txn_category": t.get("txn_category"),
                    }
                    for t in txn_result.get("transactions", [])
                ]
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"period": period or "all", "source": "fpe_cash_transactions"},
                source="modules.fazle_payroll_engine.routes",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )

        return _envelope(
            entry=entry,
            status="ok" if (rows or health_result.get("total_transactions")) else "partial",
            data={
                "total_transactions": health_result.get("total_transactions", 0),
                "period": period or "all",
                "rows": rows,
                "source": "fpe_cash_transactions",
            },
            source="modules.fazle_payroll_engine.routes",
        )

    async def _escort_roster_page_summary(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        """Reads the same source escort-roster.html's summary widget uses
        (escort_roster_entries via modules.escort_roster.db), not raw
        wbom_escort_programs data BUS-005 reads. get_roster_summary() and
        get_roster_list() both have plain Python defaults (no FastAPI
        Query(...) sentinel objects -- confirmed by direct signature read,
        unlike modules.fazle_payroll_engine.routes.list_transactions in
        BUS-017), so no explicit-kwargs workaround is required here, but
        every kwarg is still passed explicitly for clarity/consistency."""
        from modules.escort_roster.db import get_roster_list, get_roster_summary

        question = _normalize_question(payload)
        hint = str(payload.get("search") or _extract_name_hint(question) or "").strip()

        try:
            summary = await get_roster_summary()
            rows: list[dict[str, Any]] = []
            if hint:
                list_result = await get_roster_list(
                    page=1,
                    page_size=20,
                    search=hint,
                    status="active",
                    start_from=None,
                    start_to=None,
                    sort_by="start_date",
                    sort_dir="desc",
                )
                rows = list_result.get("items", [])
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"source": "escort_roster_entries"},
                source="modules.escort_roster.db",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )

        return _envelope(
            entry=entry,
            status="ok" if (rows or summary.get("grand_total")) else "partial",
            data={
                "by_status": summary.get("by_status", {}),
                "total_count": summary.get("grand_total", 0),
                "total_amount": summary.get("grand_total_amount", 0),
                "net_payable_total": summary.get("net_payable_total", 0),
                "rows": rows,
                "source": "escort_roster_entries",
            },
            source="modules.escort_roster.db",
        )


class AIMemoryAdapter(MemoryAdapter):
    """AI memory adapter that can write only to the AI memory module."""

    def __init__(self, registry: RegistryLoader | None = None) -> None:
        self.registry = registry or build_registry_loader()

    async def fetch(self, tool_id: str, payload: dict[str, Any]) -> ToolResultEnvelope:
        entry = self.registry.get(tool_id)
        handlers: dict[str, Callable[[Any, dict[str, Any]], Any]] = {
            "MEM-001": self._owner_instruction,
            "MEM-004": self._troubleshooting_note,
            "MEM-005": self._task_memory,
            "MEM-007": self._conversation_memory,
        }
        handler = handlers.get(tool_id, self._generic_memory)
        try:
            return await handler(entry, payload)
        except Exception as exc:
            return _envelope(
                entry=entry,
                status="failed",
                data={"message": "memory adapter failed"},
                source="modules.ollama_memory",
                errors=[_safe_error(exc)],
                confidence="Unknown",
            )

    async def _owner_instruction(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        action = str(payload.get("action") or "recall")
        subject_key = str(payload.get("subject_key") or "global")
        if action == "save":
            fact_id = await ollama_memory.remember_fact(
                "owner",
                subject_key,
                "instruction",
                str(payload.get("text") or payload.get("question") or "")[:1000],
                source_ref=str(payload.get("source_ref") or "ai_console"),
            )
            data = {"saved": True, "fact_id": fact_id, "subject_key": subject_key}
            return _envelope(entry=entry, status="ok", data=data, source="modules.ollama_memory")
        records = await ollama_memory.recall_facts("owner", subject_key, "instruction", limit=min(int(payload.get("limit") or 10), 20))
        confidence = "Partially Verified" if records else "Unknown"
        status = "ok" if records else "partial"
        return _envelope(
            entry=entry,
            status=status,
            data={"records": records, "count": len(records), "subject_key": subject_key},
            source="modules.ollama_memory",
            confidence=confidence,
        )

    async def _troubleshooting_note(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        action = str(payload.get("action") or "recall")
        subject_key = str(payload.get("subject_key") or "global")
        if action == "save":
            fact_id = await ollama_memory.remember_fact(
                "runtime",
                subject_key,
                "troubleshooting_note",
                str(payload.get("text") or payload.get("question") or "")[:1000],
                source_ref=str(payload.get("source_ref") or "ai_console"),
            )
            return _envelope(
                entry=entry,
                status="ok",
                data={"saved": True, "fact_id": fact_id, "subject_key": subject_key},
                source="modules.ollama_memory",
            )
        records = await ollama_memory.recall_facts("runtime", subject_key, "troubleshooting_note", limit=min(int(payload.get("limit") or 10), 20))
        confidence = "Partially Verified" if records else "Unknown"
        status = "ok" if records else "partial"
        return _envelope(
            entry=entry,
            status=status,
            data={"records": records, "count": len(records), "subject_key": subject_key},
            source="modules.ollama_memory",
            confidence=confidence,
        )

    async def _task_memory(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        action = str(payload.get("action") or "summary")
        if action == "save":
            task_id = await ollama_memory.add_task(
                str(payload.get("task_name") or payload.get("text") or "Untitled task")[:500],
                notes=str(payload.get("notes") or "")[:2000],
                source_refs=list(payload.get("source_refs") or []),
            )
            return _envelope(
                entry=entry,
                status="ok",
                data={"saved": True, "task_id": task_id},
                source="modules.ollama_memory",
            )
        if action == "set_status":
            await ollama_memory.update_task_status(
                int(payload["task_id"]),
                str(payload["status"]),
                notes=payload.get("notes"),
            )
            return _envelope(
                entry=entry,
                status="ok",
                data={"updated": True, "task_id": int(payload["task_id"]), "status": str(payload["status"])},
                source="modules.ollama_memory",
            )
        stats = await ollama_memory.list_memory_stats()
        recent = await ollama_memory.get_recent_questions(limit=min(int(payload.get("limit") or 5), 10))
        return _envelope(
            entry=entry,
            status="ok",
            data={"stats": stats, "recent_questions": recent},
            source="modules.ollama_memory",
            confidence="Partially Verified",
        )

    async def _conversation_memory(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        recent = await ollama_memory.get_recent_questions(limit=min(int(payload.get("limit") or 10), 20))
        confidence = "Partially Verified" if recent else "Unknown"
        status = "ok" if recent else "partial"
        return _envelope(
            entry=entry,
            status=status,
            data={"records": recent, "count": len(recent)},
            source="modules.ollama_memory",
            confidence=confidence,
        )

    async def _generic_memory(self, entry, payload: dict[str, Any]) -> ToolResultEnvelope:
        stats = await ollama_memory.list_memory_stats()
        return _envelope(
            entry=entry,
            status="ok",
            data={"stats": stats},
            source="modules.ollama_memory",
            confidence="Partially Verified",
        )


class UnifiedEvidenceBuilder(EvidenceAdapter):
    """Canonical evidence builder for runtime, business, memory, and knowledge."""

    def assemble(
        self,
        *,
        request_id: str,
        mode: str,
        classification: dict[str, Any],
        runtime: list[ToolResultEnvelope],
        business: list[ToolResultEnvelope],
        memory: list[ToolResultEnvelope],
        knowledge: list[ToolResultEnvelope],
        provider: dict[str, Any],
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        traceability: list[str] | None = None,
    ) -> EvidenceEnvelope:
        runtime_dicts = [item.to_dict() for item in runtime]
        business_dicts = [item.to_dict() for item in business]
        memory_dicts = [item.to_dict() for item in memory]
        knowledge_dicts = [item.to_dict() for item in knowledge]
        all_items = runtime + business + memory + knowledge
        all_statuses = [item.status for item in all_items]
        all_confidence = [item.confidence for item in all_items]
        merged_warnings = list(warnings or [])
        merged_errors = list(errors or [])
        merged_traceability = list(traceability or [])
        for item in all_items:
            merged_warnings.extend(item.warnings)
            merged_errors.extend(item.errors)
            merged_traceability.extend(item.traceability)
        confidence = "Verified"
        if not all_items:
            confidence = "Unknown"
        elif merged_errors or any(status in {"failed", "unavailable", "partial"} for status in all_statuses):
            confidence = "Partially Verified"
        elif any(level in {"Inference", "Unknown"} for level in all_confidence):
            confidence = "Partially Verified"
        envelope = EvidenceEnvelope(
            request_id=request_id,
            mode=mode,
            classification=classification,
            runtime=runtime_dicts,
            business=business_dicts,
            memory=memory_dicts,
            knowledge=knowledge_dicts,
            provider=provider,
            confidence=confidence,
            warnings=_dedupe(merged_warnings),
            errors=_dedupe(merged_errors),
            traceability=_dedupe(merged_traceability + ["EVID-001", "ORCH-005"]),
        )
        return envelope


async def build_knowledge_result(
    question: str,
    *,
    registry: RegistryLoader | None = None,
    limit: int = 5,
) -> ToolResultEnvelope:
    loader = registry or build_registry_loader()
    entry = loader.get("BUS-008")
    try:
        rows = await rag.search(question, k=limit, min_score=0.0, role="admin")
        status = "ok" if rows else "partial"
        confidence = "Partially Verified" if rows else "Unknown"
        return _envelope(
            entry=entry,
            status=status,
            data={"rows": rows, "count": len(rows), "search_query": question},
            source="modules.rag",
            confidence=confidence,
            traceability=["ORCH-032"],
        )
    except Exception as exc:
        return _envelope(
            entry=entry,
            status="failed",
            data={"rows": [], "search_query": question},
            source="modules.rag",
            errors=[_safe_error(exc)],
            confidence="Unknown",
            traceability=["ORCH-032"],
        )
