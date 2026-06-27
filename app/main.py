"""
Fazle Core — Main FastAPI Application
Handles: Meta WhatsApp webhook, Bridge1, Bridge2, Send APIs, Dashboard

All message routing is delegated to modules.message_router.process_message().
This file only handles HTTP transport, signature verification, and delivery.

SAFE MODE: AUTO_REPLY_ENABLED=false suppresses customer replies except the
explicit RECRUITMENT_AUTOREPLY_ENABLED candidate-intake bypass.
Admin command confirmations are still logged (but not sent) in safe mode.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.database import init_db, close_db, fetch_one, fetch_all, fetch_val, execute
from app.bridge import get_bridge1, get_bridge2
from app.logging_setup import setup_logging
from app import llm as ai
from modules.intent import classify
from modules.bridge_poller import start_pollers
from modules.escort_slip_extractor import extract_escort_slip, test_report as escort_test_report
from modules.payment_workflow import create_escort_payment_draft, finalize_payment, create_advance_request_draft
from modules.message_router import process_message, get_primary_admin
from modules import outbound as outbound_queue
from modules import scheduler as fazle_scheduler
from shared.queue import record_heartbeat
from modules.fazle_payroll_engine import start_fpe, stop_fpe
from modules.fazle_payroll_engine.routes import router as fpe_router
from modules.escort_roster.routes import router as escort_roster_router
from modules.drafts.routes import router as drafts_router
from modules.kb_upload.routes import router as kb_upload_router
from modules.admin_employees import router as admin_employees_router
from modules.admin_transactions import router as admin_transactions_router
from modules.social_auto_reply import ingest_social_event, start_social_auto_reply, stop_social_auto_reply
from modules.social_auto_reply.routes import router as social_auto_reply_router
from modules.contact_roles.routes import router as contact_roles_router

setup_logging()
log = logging.getLogger("fazle.app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

settings = get_settings()

# ── Concurrency caps (B15.6) ───────────────────────────────────────────────────
OCR_SEMAPHORE = asyncio.Semaphore(int(os.getenv("OCR_CONCURRENCY", "2")))
BULK_COMPUTE_SEMAPHORE = asyncio.Semaphore(int(os.getenv("PAYROLL_BULK_CONCURRENCY", "1")))

# ── Feature flag: queue-based outbound (B15.9) ─────────────────────────────────
def _use_outbound_queue() -> bool:
    return os.getenv("USE_OUTBOUND_QUEUE", "true").lower() in ("1", "true", "yes")


def _social_auto_reply_single_engine() -> bool:
    return os.getenv("SOCIAL_AUTO_REPLY_SINGLE_ENGINE", "true").lower() in ("1", "true", "yes")


async def _force_draft_by_saved_contact_name(sender_clean: str) -> bool:
    """Hard safety gate: if saved contact display_name matches configured tokens, force draft."""
    try:
        if sender_clean in settings.draft_always_phone_set:
            return True
        # Match against saved display_name in wbom_contacts (whatsapp platform).
        # Token rules:
        # - contains any `draft_always_names` substring OR
        # - startswith any `draft_name_prefixes`
        from app.database import fetch_one

        variants = [sender_clean]
        if sender_clean.startswith("880") and len(sender_clean) >= 13:
            variants.append("0" + sender_clean[3:])
        elif sender_clean.startswith("01") and len(sender_clean) == 11:
            variants.append("880" + sender_clean[1:])

        contact = None
        for v in variants:
            contact = await fetch_one(
                "SELECT display_name FROM wbom_contacts WHERE whatsapp_number = $1 AND platform='whatsapp' LIMIT 1",
                v,
            )
            if contact:
                break
        if not contact:
            return False
        name_lower = (contact.get("display_name") or "").lower()
        for token in settings.draft_always_name_list:
            if token and token in name_lower:
                return True
        for prefix in settings.draft_name_prefix_list:
            if prefix and name_lower.startswith(prefix):
                return True
        return False
    except Exception:
        return False

# ── API Key dependency ─────────────────────────────────────────────────────────
API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def require_api_key(key: str = Depends(API_KEY_HEADER)):
    # Legacy single-key (env INTERNAL_API_KEY) — always accepted
    if key and key == settings.internal_api_key:
        return key
    # Batch 19 — per-admin API keys (sha256 lookup)
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin and admin.get("status") == "active":
                return key
        except Exception as e:  # rbac unavailable → fail closed below
            log.warning(f"[auth] rbac key lookup failed: {e}")
    raise HTTPException(status_code=403, detail="Unauthorized")


async def _rbac_actor_from_key(key: str) -> dict[str, Any]:
    if key == settings.internal_api_key:
        return {"id": None, "phone": "", "name": "owner", "status": "active"}
    from modules import rbac
    admin = await rbac.get_admin_by_api_key(key)
    return admin or {"id": None, "phone": "", "name": "", "status": "unknown"}


def require_command(command: str):
    async def _dep(key: str = Depends(require_api_key)):
        if key == settings.internal_api_key:
            return key
        from modules import rbac
        perm = await rbac.check_permission(api_key=key, command=command)
        if not perm.get("allowed"):
            actor_admin = perm.get("admin")
            if actor_admin:
                await rbac.record_audit(
                    channel="http",
                    command=command,
                    actor_phone=actor_admin.get("phone"),
                    actor_admin=actor_admin,
                    allowed=False,
                    required_role=perm.get("required_role"),
                    denied_reason=perm.get("reason"),
                )
            raise HTTPException(status_code=403, detail=perm.get("reason") or "Forbidden")
        return key
    return _dep


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Batch 19 — bootstrap admin users from ADMIN_NUMBERS env
    try:
        from modules import rbac
        n = await rbac.ensure_bootstrap_admins()
        log.info(f"[rbac] bootstrap complete (created={n})")
    except Exception as e:
        log.warning(f"[rbac] bootstrap failed: {e}")
    # WhatsApp Chat UI — ensure runtime settings + groups tables exist
    try:
        await ensure_wa_chat_tables()
        log.info("[wa_chat] tables ready")
    except Exception as e:
        log.warning(f"[wa_chat] table init failed (non-fatal): {e}")
    await start_pollers()
    outbound_queue.start_background_worker()
    fazle_scheduler.start_scheduler()
    # Ollama daemon client — persistent keep-alive client + async model warmup.
    try:
        from app import ollama_daemon
        await ollama_daemon.start()
        asyncio.create_task(ollama_daemon.warm_model())
        log.info("[ollama_daemon] started and warmup scheduled")
    except Exception as e:
        log.warning(f"[ollama_daemon] startup failed (non-fatal): {e}")
    # Validate Groq API key at startup
    try:
        from config import get_settings
        settings = get_settings()
        if not settings.groq_api_key or not str(settings.groq_api_key).strip():
            log.error("[groq] Groq API key not configured - Groq services will not work")
        else:
            log.info("[groq] Groq API key validated")
    except Exception as e:
        log.warning(f"[groq] API key validation failed: {e}")
    # Batch 21 — build RAG index in background (non-fatal)
    try:
        from modules import rag
        asyncio.create_task(rag.build_index())
        log.info("[rag] index build scheduled")
    except Exception as e:
        log.warning(f"[rag] startup build failed to schedule: {e}")
    # FPE — Fazle Payroll Engine (start after DB is ready)
    try:
        await start_fpe()
    except Exception as e:
        log.warning(f"[fpe] startup failed (non-fatal): {e}")
    # Social auto-reply backend: schema + worker. Worker stays paused unless enabled by env/API.
    try:
        await start_social_auto_reply()
    except Exception as e:
        log.warning(f"[social] startup failed (non-fatal): {e}")
    # Phase 12 — Unified Request Coordination Layer
    try:
        from shared.realtime import start_event_bridge
        start_event_bridge()
        log.info("[realtime] coordination layer started")
    except Exception as e:
        log.warning(f"[realtime] coordination layer startup failed (non-fatal): {e}")
    # Phase 13A — Distributed Runtime Gateway
    _gw_node_id = None
    try:
        from shared.runtime_gateway import start_gateway as _start_gw
        import importlib.metadata as _imeta
        try:
            _ver = _imeta.version("fazle-system-agent")
        except Exception:
            _ver = os.getenv("APP_VERSION", "1.1.0")
        _gw_node_id = await _start_gw(
            "fazle-core",
            role="orchestrator",
            version=_ver,
            metadata={"host": os.getenv("HOSTNAME", ""), "port": 8200},
        )
        log.info("[gateway] registered node=%s", _gw_node_id)
    except Exception as e:
        log.warning(f"[gateway] startup failed (non-fatal): {e}")
    # Phase 13B — Global Queue Arbitration recovery loop
    try:
        from shared.queue_arbiter import start_arbiter_recovery as _start_arbiter
        await _start_arbiter()
        log.info("[arbiter] recovery loop started")
    except Exception as e:
        log.warning(f"[arbiter] startup failed (non-fatal): {e}")
    # Phase 13C — Unified Frontend Synchronization monitoring
    try:
        from shared.frontend_sync import start_sync_monitoring as _start_sync
        await _start_sync()
        log.info("[sync] frontend sync monitor started")
    except Exception as e:
        log.warning(f"[sync] startup failed (non-fatal): {e}")
    # Phase 13D — Multi-Bridge Orchestration layer
    try:
        from shared.bridge_orchestrator import start_orchestrator as _start_orch
        await _start_orch()
        log.info("[orchestrator] bridge orchestration layer started")
    except Exception as e:
        log.warning(f"[orchestrator] startup failed (non-fatal): {e}")
    # Phase 13E — Self-Healing Runtime
    try:
        from shared.self_heal import start_self_healer as _start_sh
        await _start_sh()
        log.info("[self_heal] runtime self-healer started")
    except Exception as e:
        log.warning(f"[self_heal] startup failed (non-fatal): {e}")
    # Escort draft cleanup — purge empty junk rows created by failed extractions
    try:
        from modules.escort_roster.db import cleanup_empty_drafts, cleanup_junk_drafts
        _cleanup_result = await cleanup_empty_drafts(min_age_hours=0, actor="startup")
        if _cleanup_result["deleted"]:
            log.info(f"[escort_cleanup] startup purged {_cleanup_result['deleted']} empty draft programs")
        _junk_result = await cleanup_junk_drafts(actor="startup")
        if _junk_result["deleted"]:
            log.info(f"[escort_cleanup] startup purged {_junk_result['deleted']} junk draft programs")
    except Exception as e:
        log.warning(f"[escort_cleanup] startup cleanup failed (non-fatal): {e}")
    log.info("Fazle Core started")
    yield
    fazle_scheduler.stop_scheduler()
    await outbound_queue.stop_background_worker()
    # Phase 13E — stop self-healing layer
    try:
        from shared.self_heal import stop_self_healer as _stop_sh
        await _stop_sh()
    except Exception as e:
        log.warning(f"[self_heal] shutdown error: {e}")
    # Phase 13D — stop bridge orchestration layer
    try:
        from shared.bridge_orchestrator import stop_orchestrator as _stop_orch
        await _stop_orch()
    except Exception as e:
        log.warning(f"[orchestrator] shutdown error: {e}")
    # Phase 13C — stop frontend sync monitor
    try:
        from shared.frontend_sync import stop_sync_monitoring as _stop_sync
        await _stop_sync()
    except Exception as e:
        log.warning(f"[sync] shutdown error: {e}")
    # Phase 13B — stop arbiter recovery loop
    try:
        from shared.queue_arbiter import stop_arbiter_recovery as _stop_arbiter
        await _stop_arbiter()
    except Exception as e:
        log.warning(f"[arbiter] shutdown error: {e}")
    # Phase 13A — deregister gateway node on clean shutdown
    try:
        from shared.runtime_gateway import stop_gateway as _stop_gw
        await _stop_gw()
    except Exception as e:
        log.warning(f"[gateway] shutdown error: {e}")
    try:
        await stop_fpe()
    except Exception as e:
        log.warning(f"[fpe] shutdown error: {e}")
    try:
        await stop_social_auto_reply()
    except Exception as e:
        log.warning(f"[social] shutdown error: {e}")
    try:
        from app import ollama_daemon
        await ollama_daemon.stop()
    except Exception as e:
        log.warning(f"[ollama_daemon] shutdown error: {e}")
    await close_db()
    log.info("Fazle Core stopped")


app = FastAPI(title="Fazle Core", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
app.include_router(fpe_router)
app.include_router(escort_roster_router)
app.include_router(drafts_router)
app.include_router(kb_upload_router)
app.include_router(admin_employees_router)   # Phase 18B — Admin Employee CRUD
app.include_router(admin_transactions_router) # Phase 19 — Admin Transaction CRUD
app.include_router(social_auto_reply_router)  # Backend social auto-reply queue/admin API
app.include_router(contact_roles_router)      # P2-4 — Contact roles CRUD

# WhatsApp Chat UI — 3-panel frontend at /wa-chat
from modules.wa_chat_frontend import router as wa_chat_router, ensure_wa_chat_tables
app.include_router(wa_chat_router)

# Phase 12C — Realtime WebSocket endpoint
from shared.realtime import router as _realtime_router
app.include_router(_realtime_router)

# Phase 13C — X-State-Version header middleware (additive, non-breaking)
try:
    from shared.frontend_sync import StateVersionMiddleware as _SVM
    app.add_middleware(_SVM)
except Exception as _e:
    log.warning("[sync] StateVersionMiddleware not loaded: %s", _e)


# ── Batch 22 — observability middleware ───────────────────────────────────────
from modules import observability as obs
from starlette.requests import Request as _Req


def _route_template(request: _Req) -> str:
    """Return the matched route template (e.g. /admin/users/{phone}/role)
    so cardinality stays bounded. Falls back to raw path."""
    try:
        route = request.scope.get("route")
        if route is not None and getattr(route, "path", None):
            return route.path
    except Exception:
        pass
    return request.url.path


@app.middleware("http")
async def metrics_middleware(request: _Req, call_next):
    t0 = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        dur_ms = (time.perf_counter() - t0) * 1000.0
        path = _route_template(request)
        # Skip the metrics endpoint itself to avoid feedback loops
        if path not in ("/metrics", "/metrics/json"):
            obs.inc(
                "fazle_http_requests_total",
                labels={"method": request.method, "path": path, "status": str(status)},
            )
            obs.observe(
                "fazle_http_request_duration_ms",
                dur_ms,
                labels={"method": request.method, "path": path},
            )


# ── Security headers — all text/html responses ────────────────────────────────
@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    if "text/html" in response.headers.get("content-type", ""):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'"
        )
    return response


# ── Batch 11 — recruitment-only auto-reply gate ────────────────────────────────
def _admin_numbers_set() -> set[str]:
    from modules.phone_normalizer import normalize_phone
    return {
        normalized
        for phone in {
        settings.admin_meta_number,
        settings.admin_bridge1_number,
        settings.admin_bridge2_number,
        *settings.admin_number_list,
        }
        if (normalized := normalize_phone(phone))
    }


async def _should_recruitment_autoreply(sender_clean: str, text: str) -> bool:
    """Return True only for a common-decision explicit recruitment message.

    Identity detection failure falls back to role='unknown' so explicit-keyword
    messages (চাকরি, job, etc.) are never silently dropped due to a DB error.
    """
    if not settings.recruitment_autoreply_enabled:
        return False
    from modules.phone_normalizer import normalize_phone
    sender_clean = normalize_phone(sender_clean) or sender_clean
    if sender_clean in _admin_numbers_set():
        return False

    # Step 1: identity lookup — degrade gracefully to 'unknown' on any error.
    role = "unknown"
    try:
        from modules.identity_brain import detect_identity
        identity = await detect_identity(sender_clean, text)
        role = identity.get("identity_role") or identity.get("role") or "unknown"
    except Exception as e:
        log.warning(
            "[RECRUIT-GATE] identity check failed for %s, using role='unknown': %s",
            sender_clean, e,
        )

    # Step 2: eligibility decision — explicit keywords must still fire even when
    # identity is unknown (e.g. 'চাকরি দরকার', 'is there any job').
    try:
        from modules.intent import classify as classify_intent
        from modules.recruitment_flow import recruitment_eligibility
        decision = await recruitment_eligibility(
            sender_clean, text, role=role, intent=classify_intent(text),
        )
        return bool(decision["autosend"])
    except Exception as e:
        log.warning(
            "[RECRUIT-GATE] eligibility check failed for %s: %s", sender_clean, e,
        )
        return False


# ── Health (B15.5 expanded) ───────────────────────────────────────────────────
async def _probe_db() -> dict:
    try:
        v = await asyncio.wait_for(fetch_val("SELECT 1"), timeout=2.0)
        return {"status": "ok", "value": int(v or 0)}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


def _probe_file_age(path: str, label: str) -> dict:
    """Best-effort liveness via mtime. Bridge DBs only write on new inbound msgs,
    so quiet hours are normal — we never escalate to 'critical' here."""
    try:
        st = os.stat(path)
        age = int(time.time() - st.st_mtime)
        # Only mark degraded after long idle; never critical (bridges may simply be quiet)
        status = "ok" if age < 3600 else "degraded"
        return {"status": status, "mtime_age_s": age}
    except FileNotFoundError:
        return {"status": "critical", "error": f"{label} db not found"}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


async def _probe_heartbeat(service: str, stale_after_s: int = 120) -> dict:
    try:
        row = await fetch_one(
            "SELECT EXTRACT(EPOCH FROM (NOW() - last_seen))::INT AS age, last_message_id, queue_depth "
            "FROM fazle_service_heartbeats WHERE service = $1",
            service,
        )
        if not row:
            return {"status": "critical", "error": "no heartbeat ever"}
        age = int(row["age"])
        status = "ok" if age < stale_after_s else ("degraded" if age < stale_after_s * 3 else "critical")
        return {"status": status, "age_s": age, "last_message_id": row["last_message_id"], "queue_depth": row["queue_depth"]}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


async def _probe_outbound() -> dict:
    try:
        pend = await outbound_queue.pending_count()
        dlq = await outbound_queue.dlq_count()
        actionable_dlq = await outbound_queue.actionable_dlq_count()
        status = "ok"
        if pend > 0:
            status = "degraded"
        if pend > 200:
            status = "critical"
        return {"status": status, "pending": pend, "dlq": dlq, "actionable_dlq": actionable_dlq}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


async def _probe_qdrant() -> dict:
    """Endpoint/client-based Qdrant probe; does not depend on systemd names."""
    try:
        from modules import rag
        status = await rag.qdrant_status()
        if status.get("status") == "ok" and not status.get("collection_exists", True):
            status["status"] = "degraded"
            status["error"] = f"collection {status.get('collection')} missing"
        return status
    except Exception as e:
        return {"status": "degraded", "error": str(e)[:200]}


async def _first_existing_column(table: str, candidates: tuple[str, ...]) -> Optional[str]:
    rows = await fetch_all(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=$1 AND column_name = ANY($2::text[])
        """,
        table,
        list(candidates),
    )
    found = {r["column_name"] for r in rows}
    for column in candidates:
        if column in found:
            return column
    return None


async def _whatsapp_message_time_expr() -> tuple[str, str]:
    """
    Return a safe timestamp expression for wbom_whatsapp_messages reports.

    Production uses received_at; older/generated code sometimes assumed
    created_at. This helper makes dashboard/report queries schema-aware.
    """
    column = await _first_existing_column(
        "wbom_whatsapp_messages",
        ("received_at", "source_timestamp", "processed_at", "created_at"),
    )
    if not column:
        return "NOW()", "none"
    return column, column


def _probe_disk() -> dict:
    try:
        usage = shutil.disk_usage("/")
        pct = int(usage.used * 100 / usage.total)
        warn = int(os.getenv("HEALTH_DISK_WARN_PCT", "80"))
        crit = int(os.getenv("HEALTH_DISK_CRIT_PCT", "90"))
        status = "ok" if pct < warn else ("degraded" if pct < crit else "critical")
        return {"status": status, "used_pct": pct, "free_gb": round(usage.free / 1e9, 1)}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


def _probe_mem() -> dict:
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                k, _, rest = line.partition(":")
                info[k.strip()] = int(rest.strip().split()[0])  # kB
        avail_mb = info.get("MemAvailable", 0) // 1024
        crit = int(os.getenv("HEALTH_MEM_CRIT_MB", "200"))
        status = "ok" if avail_mb > crit * 2 else ("degraded" if avail_mb > crit else "critical")
        return {"status": status, "available_mb": avail_mb}
    except Exception as e:
        return {"status": "critical", "error": str(e)[:200]}


async def _build_health(deep: bool = False) -> dict:
    db_p, ollama_p, qdrant_p, ob_p, hb1_p, hb2_p = await asyncio.gather(
        _probe_db(),
        _safe_ollama(),
        _probe_qdrant(),
        _probe_outbound(),
        _probe_heartbeat("bridge_poller:bridge1"),
        _probe_heartbeat("bridge_poller:bridge2"),
        return_exceptions=False,
    )
    llm_p = await _safe_llm() if deep else {"status": "skipped", "reason": "deep health only"}
    bridge1_db = _probe_file_age("/home/azim/whatsapp1/store/messages.db", "bridge1")
    bridge2_db = _probe_file_age("/home/azim/whatsapp2/store/messages.db", "bridge2")
    # SQLite mtime is activity, not liveness. A fresh poller heartbeat proves
    # the quiet bridge is healthy while preserving the age as diagnostics.
    if hb1_p.get("status") == "ok" and bridge1_db.get("status") == "degraded":
        bridge1_db["status"] = "ok"
        bridge1_db["quiet_but_poller_healthy"] = True
    if hb2_p.get("status") == "ok" and bridge2_db.get("status") == "degraded":
        bridge2_db["status"] = "ok"
        bridge2_db["quiet_but_poller_healthy"] = True
    disk_p = _probe_disk()
    mem_p = _probe_mem()

    probes = {
        "db": db_p,
        "bridge1_db": bridge1_db,
        "bridge2_db": bridge2_db,
        "bridge_poller_b1": hb1_p,
        "bridge_poller_b2": hb2_p,
        "outbound": ob_p,
        "disk": disk_p,
        "mem": mem_p,
        "ollama": ollama_p,
        "qdrant": qdrant_p,
        "llm": llm_p,
    }
    statuses = [p.get("status", "ok") for p in probes.values()]
    if "critical" in statuses:
        overall = "critical"
    elif "degraded" in statuses:
        overall = "degraded"
    else:
        overall = "ok"

    out: dict[str, Any] = {"status": overall, "probes": probes, "ts": int(time.time())}
    if deep:
        try:
            out["bridges"] = {
                "bridge1": await get_bridge1().status(),
                "bridge2": await get_bridge2().status(),
            }
        except Exception as e:
            out["bridges"] = {"error": str(e)[:200]}
    return out


async def _safe_ollama() -> dict:
    try:
        s = await ai.check_ollama_health()
        if isinstance(s, dict):
            s.setdefault("status", "ok")
            return s
        return {"status": "ok", "raw": str(s)[:200]}
    except Exception as e:
        return {"status": "degraded", "error": str(e)[:200]}


async def _safe_llm() -> dict:
    try:
        from app.llm import check_health as _llm_health
        return await _llm_health()
    except Exception as e:
        return {"status": "degraded", "error": str(e)[:200]}


@app.get("/health")
async def health():
    out = await _build_health(deep=False)
    if out["status"] == "critical":
        return JSONResponse(status_code=503, content=out)
    return out


@app.get("/health/deep", dependencies=[Depends(require_api_key)])
async def health_deep():
    out = await _build_health(deep=True)
    if out["status"] == "critical":
        return JSONResponse(status_code=503, content=out)
    return out


# ── Phase 13A — Runtime node registry ───────────────────────────────────────
@app.get("/api/runtime/nodes", dependencies=[Depends(require_api_key)])
async def get_runtime_nodes():
    """
    List all registered runtime nodes (fazle-core, payroll-engine, escort-roster).

    Returns each node's status, last heartbeat age, active_requests, queue_depth,
    version, and metadata_json.  Stale nodes (age_s > STALE_THRESHOLD_S) are
    returned with status='offline' so the dashboard can highlight them.

    No auth required — diagnostic data only, no secrets exposed.
    """
    try:
        from shared.runtime_gateway import get_active_nodes
        nodes = await get_active_nodes()
    except Exception as exc:
        log.warning("[gateway] /api/runtime/nodes error: %s", exc)
        nodes = []
    return {
        "nodes": nodes,
        "count": len(nodes),
        "online": sum(1 for n in nodes if n.get("status") == "online"),
        "ts":     time.time(),
    }


# ── Phase 13B — Dead-letter inspection ───────────────────────────────────────
@app.get("/api/queue/dead-letters", dependencies=[Depends(require_api_key)])
async def get_dead_letter_queue(limit: int = 50, offset: int = 0):
    """
    Return dead-letter lease entries for diagnosis.

    Each item contains the lease metadata and the linked message content
    from fazle_message_queue.  Sorted by most-recent failure first.
    Use offset for pagination.
    """
    try:
        from shared.queue_arbiter import get_dead_letters, get_dead_letter_count
        items = await get_dead_letters(limit=limit, offset=offset)
        total = await get_dead_letter_count()
    except Exception as exc:
        log.warning("[arbiter] /api/queue/dead-letters error: %s", exc)
        items, total = [], 0
    return {
        "items":  items,
        "count":  len(items),
        "total":  total,
        "limit":  limit,
        "offset": offset,
        "ts":     time.time(),
    }


@app.get("/api/queue/arbiter-metrics", dependencies=[Depends(require_api_key)])
async def get_arbiter_metrics_endpoint():
    """
    Return in-process arbitration metrics: lease counts, conflicts,
    dead-letters, retry counts, processing latency (p50/p95).
    """
    try:
        from shared.queue_arbiter import get_arbiter_metrics
        metrics = get_arbiter_metrics()
    except Exception as exc:
        log.warning("[arbiter] /api/queue/arbiter-metrics error: %s", exc)
        metrics = {}
    return {"metrics": metrics, "ts": time.time()}


# ── Phase 13C — Frontend Synchronization endpoints ───────────────────────────

@app.post("/api/frontend/heartbeat")
async def frontend_heartbeat(request: Request):
    """
    Client reports its current state_version + reconnect_count.

    Body: {client_id: str, state_version: int, reconnect_count?: int}

    Response: {stale: bool, current_version: int, lag: int,
               backoff_hint_s: int, ts: float}

    Frontend JS should call this every 30 s.  If stale=true the client must
    re-fetch its data set.  backoff_hint_s tells the client how long to wait
    before reconnecting a dropped WebSocket connection.
    """
    try:
        body = await request.json()
        client_id       = str(body.get("client_id") or "anonymous")
        state_version   = int(body.get("state_version", 0))
        reconnect_count = int(body.get("reconnect_count", 0))
        from shared.frontend_sync import register_heartbeat
        result = await register_heartbeat(client_id, state_version, reconnect_count)
        return result
    except Exception as exc:
        log.warning("[sync] /api/frontend/heartbeat error: %s", exc)
        return {"stale": False, "current_version": 0, "lag": 0,
                "backoff_hint_s": 3, "ts": time.time()}


@app.get("/api/frontend/sync-stats", dependencies=[Depends(require_api_key)])
async def frontend_sync_stats():
    """
    Diagnostics for the unified frontend synchronization layer.

    Response: {registered_clients, active_clients, stale_clients,
               total_reconnects_seen, avg_propagation_latency_ms,
               propagation_samples, heartbeats_received, events_observed,
               stale_detected_total, ws: {...}}
    """
    try:
        from shared.frontend_sync import get_sync_diagnostics
        diag = get_sync_diagnostics()
    except Exception as exc:
        log.warning("[sync] /api/frontend/sync-stats error: %s", exc)
        diag = {}
    return {**diag, "ts": time.time()}


# ── Phase 13D — Bridge orchestration diagnostics ──────────────────────────────
@app.get("/api/bridges/diagnostics", dependencies=[Depends(require_api_key)])
async def bridge_diagnostics():
    """
    Health, lag, deduplication, and failover diagnostics for all bridges.

    Response: {bridges: {bridge1: {...}, bridge2: {...}}, orchestrator: {...}}
    No secrets or credentials are exposed.
    """
    try:
        from shared.bridge_orchestrator import get_bridge_diagnostics
        diag = await get_bridge_diagnostics()
    except Exception as exc:
        log.warning("[orchestrator] /api/bridges/diagnostics error: %s", exc)
        diag = {}
    return {**diag, "ts": time.time()}


@app.post("/api/bridges/probe")
async def bridge_probe(_key: str = Depends(require_api_key)):
    """
    Trigger an immediate health probe on all bridges (internal use only).
    Requires X-Internal-Key header.
    """
    try:
        from shared.bridge_orchestrator import probe_all_bridges
        await probe_all_bridges()
        from shared.bridge_orchestrator import get_bridge_diagnostics
        diag = await get_bridge_diagnostics()
    except Exception as exc:
        log.warning("[orchestrator] /api/bridges/probe error: %s", exc)
        diag = {}
    return {"probed": True, **diag, "ts": time.time()}


# ── Phase 13E — Self-Healing Runtime diagnostics ──────────────────────────────
@app.get("/api/self-heal/diagnostics", dependencies=[Depends(require_api_key)])
async def self_heal_diagnostics():
    """
    Runtime self-heal status: pressure score, panic mode, signal breakdown,
    recovery counts, and last 20 audit log entries.

    No auth required — no secrets exposed.
    """
    try:
        from shared.self_heal import get_self_heal_diagnostics
        return await get_self_heal_diagnostics()
    except Exception as exc:
        log.warning("[self_heal] /api/self-heal/diagnostics error: %s", exc)
        return {"error": str(exc), "ts": time.time()}


# ── PATCH 6: RAG index rebuild endpoint ───────────────────────────────────────
@app.post("/api/rag/rebuild")
async def rag_rebuild(_key: str = Depends(require_api_key)):
    """
    Force a full RAG index rebuild from scratch.
    Clears the current (potentially poisoned) in-memory index and rebuilds
    from approved files only (internal/archived files automatically excluded).
    Requires X-Internal-Key header.
    """
    try:
        from modules import rag
        qdrant_before = await rag.qdrant_status()
        stats = await rag.rebuild_index()
        qdrant_after = await rag.qdrant_status()
        return {
            "status": "rebuilt",
            "stats": stats,
            "qdrant_before": qdrant_before,
            "qdrant_after": qdrant_after,
            "ts": time.time(),
        }
    except Exception as exc:
        log.error("[rag] /api/rag/rebuild failed: %s", exc)
        return {"status": "error", "error": str(exc), "ts": time.time()}


@app.get("/api/rag/qdrant-status")
async def rag_qdrant_status(_key: str = Depends(require_api_key)):
    """Configured Qdrant status via API/client, independent of systemd service names."""
    try:
        from modules import rag
        return {"status": "ok", "qdrant": await rag.qdrant_status(), "ts": time.time()}
    except Exception as exc:
        log.error("[rag] /api/rag/qdrant-status failed: %s", exc)
        return {"status": "error", "error": str(exc), "ts": time.time()}


@app.get("/api/rag/stats")
async def rag_stats(_key: str = Depends(require_api_key)):
    """
    RAG index stats: doc count, safe/unsafe split, vocab size, source breakdown.
    Requires X-Internal-Key header.
    """
    try:
        from modules import rag
        return {
            "status": "ok",
            "stats": await rag.stats(),
            "qdrant": await rag.qdrant_status(),
            "ts": time.time(),
        }
    except Exception as exc:
        log.error("[rag] /api/rag/stats failed: %s", exc)
        return {"status": "error", "error": str(exc), "ts": time.time()}


@app.get("/api/rag/recent-searches")
async def rag_recent_searches(_key: str = Depends(require_api_key)):
    """
    STEP 2: Last 50 RAG search audit records.
    Returns: query, matched source, score, chunk preview, timestamp, safe flag.
    Requires X-Internal-Key header. No data persisted — in-memory ring buffer.
    """
    try:
        from modules import rag
        searches = await rag.recent_searches()
        return {"status": "ok", "count": len(searches), "searches": searches, "ts": time.time()}
    except Exception as exc:
        log.error("[rag] /api/rag/recent-searches failed: %s", exc)
        return {"status": "error", "error": str(exc), "ts": time.time()}


@app.post("/api/self-heal/check")
async def self_heal_trigger(_key: str = Depends(require_api_key)):
    """
    Trigger an immediate self-heal check cycle (internal use only).
    Returns the full diagnostics snapshot after checks complete.
    Requires X-Internal-Key header.
    """
    try:
        from shared.self_heal import trigger_check_cycle
        return await trigger_check_cycle()
    except Exception as exc:
        log.warning("[self_heal] /api/self-heal/check error: %s", exc)
        return {"error": str(exc), "ts": time.time()}


# ── Phase 12G — Global state version (frontend poll) ─────────────────────────
@app.get("/api/state-version")
async def get_state_version_endpoint():
    """
    Returns the current global state version counter.

    Dashboard JS polls this every few seconds and refreshes the current tab
    only when the version increases — no wasted API calls.

    Response: {"version": 42, "ts": 1234567890.1}
    """
    try:
        from shared.state_version import get_state_version
        v = await get_state_version()
    except Exception:
        v = 0
    return {"version": v, "ts": time.time()}


# ── Meta Webhook Verification (GET) ───────────────────────────────────────────
@app.get("/webhook/meta")
async def meta_verify(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and token == settings.meta_verify_token:
        log.info("Meta webhook verified")
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


# ── Meta Webhook Events (POST) ────────────────────────────────────────────────
@app.post("/webhook/meta")
async def meta_webhook(request: Request):
    """Receive Meta events: WhatsApp Cloud API, Facebook Messenger, and Page comments."""
    body_bytes = await request.body()

    # Verify signature
    sig = request.headers.get("X-Hub-Signature-256", "")
    if sig and settings.meta_app_secret:
        expected = "sha256=" + hmac.new(
            settings.meta_app_secret.encode(),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(status_code=403, detail="Invalid signature")
    elif settings.meta_webhook_signature_enforcement:
        raise HTTPException(status_code=403, detail="Missing Meta webhook signature")
    else:
        log.warning("[WEBHOOK_AUTH_AUDIT] unsigned Meta webhook accepted; enforcement disabled")

    try:
        payload: dict[str, Any] = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")

    for entry in payload.get("entry", []):
        # ── Facebook Messenger messages ─────────────────────────────────────
        for messaging in entry.get("messaging", []):
            if "message" in messaging and not messaging["message"].get("is_echo"):
                await _handle_messenger_message(messaging)

        # ── WhatsApp Cloud API + Facebook Page feed (comments) ──────────────
        for change in entry.get("changes", []):
            field = change.get("field", "")
            value = change.get("value", {})

            if field == "messages":
                # WhatsApp Cloud API inbound messages
                for msg in value.get("messages", []):
                    await _handle_meta_message(msg, value)

            elif field == "feed":
                # Facebook Page feed: new public comments
                if value.get("item") == "comment" and value.get("verb") == "add":
                    await _handle_fb_comment(value)

    return {"status": "ok"}


async def _handle_meta_message(msg: dict, value: dict):
    from modules.phone_normalizer import normalize_phone
    sender_raw = msg.get("from", "")
    sender = normalize_phone(sender_raw) or sender_raw
    message_id = str(msg.get("id") or "")
    queue_key = (
        f"meta-recruit:{hashlib.sha256(message_id.encode()).hexdigest()[:48]}"
        if message_id else None
    )
    msg_type = msg.get("type", "")
    text = ""
    extracted_text = ""

    if msg_type == "text":
        text = msg.get("text", {}).get("body", "").strip()
    elif msg_type in ("image", "audio", "document", "video"):
        try:
            from modules.media_normalization import normalize_meta_media_message
            normalized_media = await normalize_meta_media_message(msg)
            text = normalized_media["text"]
            if normalized_media["meta"].get("normalized"):
                extracted_text = text
        except Exception as media_err:
            log.warning("[META] media normalization failed type=%s error=%s", msg_type, media_err)
            text = f"[{msg_type} message]"

    log.info(f"[META] from={sender} type={msg_type} text={text[:60]!r}")

    if not text:
        return

    if message_id:
        existing = await fetch_one(
            """SELECT 1 FROM wbom_whatsapp_messages
               WHERE platform='meta' AND direction='inbound' AND source_message_ref=$1
               LIMIT 1""",
            message_id,
        )
        if existing:
            log.info("[META] duplicate webhook safely acknowledged inbound_id=%s", message_id)
            return

    await _save_message(
        "meta",
        sender,
        text,
        direction="inbound",
        msg_type=msg_type or "text",
        extracted_text=extracted_text,
        source_message_ref=message_id,
        metadata={"delivery_state": "received", "meta_message_id": message_id},
    )

    try:
        await record_heartbeat(
            bridge_id="meta",
            last_msg_id=msg.get("id"),
            extra={"sender": sender, "msg_type": msg_type},
        )
    except Exception as hb_err:
        log.debug(f"[META] bridge_heartbeats write failed: {hb_err}")

    if _social_auto_reply_single_engine():
        try:
            await ingest_social_event(
                platform="meta_whatsapp",
                event_type="message",
                sender_id=sender,
                text=text,
                message_id=msg.get("id"),
                media_flag=msg_type in ("image", "audio", "document", "video"),
                raw_payload=msg,
            )
        except Exception as e:
            log.warning(f"[social] meta ingest failed for {sender}: {e}")
        log.debug(f"[META] social daemon is single reply engine, legacy reply path skipped for {sender}")
        return

    # Sync-only: Meta is not in auto_reply_sources — save message, skip reply/draft.
    if "meta" not in settings.auto_reply_source_list:
        log.debug(f"[META] sync-only, skipping reply/draft for {sender}")
        return

    reply, send_to_admin = await _process_message(sender, text, "meta")

    if reply:
        await _record_meta_delivery_state(message_id, "generated")
        recruit_gate = (not settings.auto_reply_enabled
                        and await _should_recruitment_autoreply(sender, text))
        if settings.auto_reply_enabled or recruit_gate:
            if await _force_draft_by_saved_contact_name(sender):
                log.info(f"[META] forced draft by saved contact name policy for {sender}")
                intent = classify(text)
                await _save_draft("meta", sender, reply, intent)
                await _record_meta_delivery_state(message_id, "drafted")
            else:
                if recruit_gate:
                    log.info(f"[RECRUIT-AUTOREPLY] sending to {sender} (meta) despite SAFE MODE")
                from modules.outbound import enqueue as enqueue_outbound
                try:
                    qid = await enqueue_outbound(
                        sender,
                        reply,
                        source_bridge="meta",
                        purpose="customer-reply:recruitment",
                        idempotency_key=queue_key,
                        meta={"customer_reply": True, "intent": "recruitment", "inbound_id": message_id},
                    )
                except Exception as enqueue_err:
                    log.exception("[META] recruitment enqueue failed sender=%s inbound_id=%s", sender, message_id)
                    await _record_meta_delivery_state(message_id, "failed", str(enqueue_err))
                    await _save_draft("meta", sender, reply, "recruitment")
                    return
                if qid:
                    await _save_message("meta", sender, reply, direction="outbound")
                    await _record_meta_delivery_state(message_id, "queued")
                    log.info("[META] queued recruitment reply sender=%s qid=%s", sender, qid)
                else:
                    await _record_meta_delivery_state(message_id, "queued_deduplicated")
                    log.info("[META] recruitment reply deduplicated sender=%s inbound_id=%s", sender, message_id)
        else:
            from modules.recruitment_flow import is_recruitment_trigger
            if is_recruitment_trigger(text):
                log.warning(
                    "[RECRUIT-GATE] explicit recruitment message not queued — "
                    "recruit_gate=False, saving draft. sender=%s", sender,
                )
            else:
                log.warning(f"SAFE MODE: reply suppressed for {sender} (meta). Saving draft.")
            intent = classify(text)
            await _save_draft("meta", sender, reply, intent)
            await _record_meta_delivery_state(message_id, "drafted")
    else:
        await _record_meta_delivery_state(message_id, "suppressed")

    # send_to_admin is a dict: {admin_phone, text, bridge} — used for payment drafts
    if send_to_admin:
        if send_to_admin.get("purpose") == "draft-only-contact-review":
            await _save_draft(
                "meta",
                sender,
                send_to_admin.get("text") or text,
                classify(text) or "draft_only_contact",
            )
        await _notify_admin(send_to_admin)


async def _handle_messenger_message(messaging: dict):
    """Handle Facebook Messenger inbound message — save + recruitment autoreply."""
    sender_id = messaging.get("sender", {}).get("id", "")
    text = messaging.get("message", {}).get("text", "").strip()
    if not sender_id or not text:
        return

    log.info(f"[MESSENGER] from={sender_id} text={text[:60]!r}")
    await _save_message("messenger", sender_id, text, direction="inbound")

    try:
        await ingest_social_event(
            platform="messenger",
            event_type="message",
            sender_id=sender_id,
            text=text,
            conversation_id=messaging.get("conversation", {}).get("id"),
            message_id=messaging.get("message", {}).get("mid"),
            media_flag=bool(messaging.get("message", {}).get("attachments")),
            raw_payload=messaging,
        )
    except Exception as e:
        log.warning(f"[social] messenger ingest failed for {sender_id}: {e}")
    if _social_auto_reply_single_engine():
        log.debug(f"[MESSENGER] social daemon is single reply engine, legacy reply path skipped for {sender_id}")
        return

    if "messenger" not in settings.auto_reply_source_list:
        log.debug(f"[MESSENGER] sync-only, skipping reply for {sender_id}")
        return

    reply, _ = await _process_message(sender_id, text, "messenger")

    if reply:
        recruit_gate = (not settings.auto_reply_enabled
                        and await _should_recruitment_autoreply(sender_id, text))
        if settings.auto_reply_enabled or recruit_gate:
            if recruit_gate:
                log.info(f"[RECRUIT-AUTOREPLY] sending Messenger reply to {sender_id} (SAFE MODE bypass)")
            ok = await _send_messenger(sender_id, reply)
            if ok:
                await _save_message("messenger", sender_id, reply, direction="outbound")
        else:
            log.info(f"[MESSENGER] SAFE MODE: reply suppressed for {sender_id}")


async def _handle_fb_comment(value: dict):
    """Handle new Facebook Page comment — save + send a standard recruitment reply."""
    comment_id = value.get("comment_id", "")
    sender_name = value.get("sender_name", "Unknown")
    sender_id = str(value.get("sender_id", ""))
    text = value.get("message", "").strip()
    if not comment_id or not text:
        return

    log.info(f"[FB-COMMENT] from={sender_name}({sender_id}) comment_id={comment_id} text={text[:60]!r}")
    await _save_message("fb_comment", sender_id or comment_id, text, direction="inbound")

    try:
        await ingest_social_event(
            platform="facebook_comment",
            event_type="comment",
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            conversation_id=str(value.get("post_id") or ""),
            comment_id=comment_id,
            parent_id=str(value.get("parent_id") or ""),
            raw_payload=value,
        )
    except Exception as e:
        log.warning(f"[social] comment ingest failed for {comment_id}: {e}")
    if _social_auto_reply_single_engine():
        log.debug(f"[FB-COMMENT] social daemon is single reply engine, legacy reply path skipped for {comment_id}")
        return

    if "fb_comment" not in settings.auto_reply_source_list:
        log.debug(f"[FB-COMMENT] sync-only, skipping reply for {comment_id}")
        return

    if not settings.auto_reply_enabled:
        log.info(f"[FB-COMMENT] SAFE MODE: reply suppressed for comment_id={comment_id}")
        return

    # Use a standard recruitment invite reply for page comments
    recruit_reply = (
        f"ধন্যবাদ আপনার মন্তব্যের জন্য! আমাদের সিকিউরিটি গার্ড পদে আবেদন করতে "
        f"অনুগ্রহ করে আমাদের WhatsApp-এ মেসেজ করুন: wa.me/8801958122300"
    )
    ok = await _send_fb_comment_reply(comment_id, recruit_reply)
    if ok:
        await _save_message("fb_comment", sender_id or comment_id, recruit_reply, direction="outbound")


# ── Bridge 1 Webhook ───────────────────────────────────────────────────────────
def _verify_bridge_webhook(body: bytes, signature: str, source: str) -> None:
    """Audit or enforce bridge HMAC without touching bridge QR/session state."""
    if signature and settings.bridge_webhook_secret:
        expected = "sha256=" + hmac.new(
            settings.bridge_webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(signature, expected):
            return
        raise HTTPException(status_code=403, detail="Invalid bridge webhook signature")
    if settings.webhook_signature_enforcement:
        raise HTTPException(status_code=403, detail="Missing bridge webhook signature")
    log.warning("[WEBHOOK_AUTH_AUDIT] unsigned %s webhook accepted; enforcement disabled", source)


@app.post("/webhook/mcp1")
async def bridge1_webhook(request: Request):
    """Receive events from Bridge 1 (HR number)."""
    body = await request.body()
    _verify_bridge_webhook(body, request.headers.get("X-Fazle-Signature-256", ""), "bridge1")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")
    await _handle_bridge_event(payload, source="bridge1")
    return {"status": "ok"}


# ── Bridge 2 Webhook ───────────────────────────────────────────────────────────
@app.post("/webhook/mcp2")
async def bridge2_webhook(request: Request):
    """Receive events from Bridge 2 (OPS number)."""
    body = await request.body()
    _verify_bridge_webhook(body, request.headers.get("X-Fazle-Signature-256", ""), "bridge2")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Bad JSON")
    await _handle_bridge_event(payload, source="bridge2")
    return {"status": "ok"}


async def _handle_bridge_event(payload: dict, source: str):
    events = payload if isinstance(payload, list) else [payload]
    for event in events:
        sender = (
            event.get("sender")
            or event.get("from")
            or event.get("chat_jid")
            or event.get("chatId")
            or ""
        )
        text = (
            event.get("text")
            or event.get("message")
            or event.get("content")
            or event.get("processed_text")
            or ""
        )
        sender = str(sender or "").strip()
        text = str(text or "").strip()
        media_type = str(event.get("media_type") or event.get("type") or "").strip().lower()
        if not sender:
            continue
        if not text and media_type:
            text = f"[media:{media_type}]"
        if not text:
            continue

        sender_clean = sender.replace("@s.whatsapp.net", "").replace("+", "")

        # P15-03: Dedup guard — prevent double-processing when webhook fires
        # alongside the SQLite poller (both paths call process_message).
        # Keep pre-mark for race protection, but roll back on save failure to avoid no-loss gaps.
        msg_id = event.get("id") or event.get("message_id", "")
        pre_marked = False
        if msg_id:
            try:
                from modules.bridge_poller import _is_processed, _mark_processed
                if await _is_processed(msg_id, source):
                    log.debug(f"[{source.upper()}] webhook dedup: already processed msg_id={msg_id}")
                    continue
                await _mark_processed(msg_id, source, sender_clean)
                pre_marked = True
            except Exception as _dedup_err:
                log.debug(f"[{source.upper()}] dedup check unavailable: {_dedup_err}")

        log.info(f"[{source.upper()}] from={sender_clean} text={text[:60]!r}")

        # Detect identity before saving so DB record has role/confidence metadata
        try:
            from modules.identity_brain import detect_identity as _detect_id
            _identity = await _detect_id(sender_clean, text)
            _id_role = _identity["identity_role"]
            _id_conf = _identity["identity_confidence"]
            _id_name = (_identity.get("display_name") or "").strip()
        except Exception as _id_err:
            log.debug(f"[{source.upper()}] identity detection failed: {_id_err}")
            _id_role, _id_conf, _id_name = "unknown", 0, ""

        try:
            await _save_message(
                source, sender_clean, text, direction="inbound",
                identity_role=_id_role, identity_confidence=_id_conf,
                msg_type=media_type if media_type else "text",
            )
        except Exception as _save_err:
            if msg_id and pre_marked:
                try:
                    await execute(
                        "DELETE FROM processed_bridge_messages WHERE message_id = $1 AND bridge = $2",
                        msg_id,
                        source,
                    )
                except Exception as _rollback_err:
                    log.error(
                        f"[{source.upper()}] dedup rollback failed msg_id={msg_id}: {_rollback_err}"
                    )
            log.error(f"[{source.upper()}] inbound save failed for {sender_clean}: {_save_err}")
            continue

        _forward_to_agent_if_admin(sender_clean, text, source)

        if _social_auto_reply_single_engine():
            try:
                await ingest_social_event(
                    platform=source,
                    event_type="message",
                    sender_id=sender_clean,
                    text=text,
                    message_id=msg_id or None,
                    media_flag=bool(media_type),
                    raw_payload=event,
                )
            except Exception as e:
                log.warning(f"[social] bridge ingest failed source={source} sender={sender_clean}: {e}")
            log.debug(f"[{source.upper()}] social daemon is single reply engine, legacy reply path skipped for {sender_clean}")
            continue

        # Sync-only sources: message is saved, no reply, no draft, no admin notify.
        if source not in settings.auto_reply_source_list:
            log.debug(f"[{source.upper()}] sync-only, skipping reply/draft for {sender_clean}")
            continue

        # Full production pipeline — identical to bridge_poller path
        from modules.bridge_poller import process_bridge_inbound
        await process_bridge_inbound(
            source, sender_clean, text,
            id_role=_id_role, id_conf=_id_conf, id_name=_id_name,
        )

        # Background: extract structured facts from inbound message
        try:
            from modules.memory_extractor import extract_and_save_memory
            asyncio.create_task(
                extract_and_save_memory(
                    phone=sender_clean,
                    conversation=[{"role": "user", "content": text}],
                )
            )
        except Exception as _me_err:
            log.debug("[memory_extractor] task spawn failed: %s", _me_err)


# ── Unified message processor — delegates to modules.message_router ────────────

# Phones forwarded to fazle-agent /admin/inbox for Tier-1 NL handling.
_AGENT_FORWARD_PHONES = {"8801880446111", "8801958122300"}
_AGENT_INBOX_URL = "http://127.0.0.1:8300/admin/inbox"


def _forward_to_agent_if_admin(sender_clean: str, text: str, source: str) -> None:
    """Fire-and-forget forward to fazle-agent. Never raises, never blocks."""
    if os.getenv("AGENT_ADMIN_FORWARD_ENABLED", "false").lower() not in ("1", "true", "yes"):
        return
    if sender_clean not in _AGENT_FORWARD_PHONES:
        return
    bridge = "1" if source == "bridge1" else "2"
    payload = {"from": sender_clean, "text": text, "bridge": bridge}

    async def _send():
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                await client.post(_AGENT_INBOX_URL, json=payload)
        except Exception as e:
            log.debug(f"agent forward failed (shadow mode): {e}")

    try:
        asyncio.create_task(_send())
    except Exception as e:
        log.debug(f"agent forward task spawn failed: {e}")


async def _process_message(sender: str, text: str, source: str) -> tuple[str, dict | None]:
    return await process_message(sender, text, source)


# ── Send APIs (protected) ──────────────────────────────────────────────────────
@app.post("/send/meta", dependencies=[Depends(require_api_key)])
async def send_meta(body: dict):
    to = body.get("to", "")
    text = body.get("text", "")
    if not to or not text:
        raise HTTPException(status_code=400, detail="Missing to/text")
    ok = await _send_meta(to, text)
    return {"sent": ok}


@app.post("/send/mcp1", dependencies=[Depends(require_api_key)])
async def send_mcp1(body: dict):
    to = body.get("to", "")
    text = body.get("text", "")
    if not to or not text:
        raise HTTPException(status_code=400, detail="Missing to/text")
    proactive_enabled = os.getenv("AGENT_PROACTIVE_OUTBOUND_ENABLED", "false").lower() in (
        "1", "true", "yes",
    )
    if not proactive_enabled and text.lstrip().startswith("🤖 [proactive]"):
        log.warning("[send_mcp1] suppressed disabled agent proactive message to=%s", to)
        return {"sent": False, "suppressed": True, "reason": "agent_proactive_disabled"}
    ok = await get_bridge1().send(to, text)
    return {"sent": ok}


@app.post("/send/mcp2", dependencies=[Depends(require_api_key)])
async def send_mcp2(body: dict):
    to = body.get("to", "")
    text = body.get("text", "")
    if not to or not text:
        raise HTTPException(status_code=400, detail="Missing to/text")
    ok = await get_bridge2().send(to, text)
    return {"sent": ok}


# ── Payment draft API (for manual trigger) ─────────────────────────────────────
@app.post("/payment/escort-draft", dependencies=[Depends(require_api_key)])
async def api_create_escort_draft(body: dict):
    """Manually trigger escort payment draft creation."""
    employee_id = body.get("employee_id")
    program_id  = body.get("escort_program_id")
    days        = body.get("duty_days")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Missing employee_id")
    result = await create_escort_payment_draft(employee_id, program_id, days)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@app.post("/payment/ingest", dependencies=[Depends(require_api_key)])
async def api_payment_ingest(body: dict):
    """Batch 12: Ingest a bKash/Nagad/Rocket SMS or admin paste.
    Body: {text: str, sender_number?: str, message_id?: int, auto_finalize?: bool}
    """
    from modules.payment_ingest import ingest_payment_sms
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")
    res = await ingest_payment_sms(
        text,
        sender_number=body.get("sender_number"),
        message_id=body.get("message_id"),
        auto_finalize=bool(body.get("auto_finalize", True)),
    )
    if not res.get("ok"):
        raise HTTPException(status_code=422, detail=res)
    return res


@app.post("/payment/advance-draft", dependencies=[Depends(require_api_key)])
async def api_create_advance_draft(body: dict):
    """Manually trigger advance payment draft creation."""
    employee_id = body.get("employee_id")
    amount      = body.get("amount")
    if not employee_id:
        raise HTTPException(status_code=400, detail="Missing employee_id")
    result = await create_advance_request_draft(employee_id, amount)
    if result.get("error"):
        raise HTTPException(status_code=422, detail=result["error"])
    return result


# ── Simple Dashboard (legacy server-rendered) ─────────────────────────────────
@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_spa():
    """Batch 20 — multi-tab admin SPA shell. JS prompts for API key."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "dashboard.html"))


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Serve company logo as favicon for payroll/dashboard branding."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "assets", "company-logo.png"))


@app.get("/apple-touch-icon.png", include_in_schema=False)
async def apple_touch_icon():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "assets", "company-logo.png"))


@app.get("/manifest.json", include_in_schema=False)
async def payroll_manifest():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "manifest.json"))


@app.get("/service-worker.js", include_in_schema=False)
async def payroll_service_worker():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "service-worker.js"), media_type="application/javascript")


@app.get("/payroll", response_class=HTMLResponse)
async def payroll_spa():
    """FPE Payroll Engine dashboard SPA."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "payroll.html"))


@app.get("/payroll/overview", response_class=HTMLResponse)
@app.get("/payroll/transactions", response_class=HTMLResponse)
@app.get("/payroll/search", response_class=HTMLResponse)
@app.get("/payroll/employees", response_class=HTMLResponse)
@app.get("/payroll/addemployee", response_class=HTMLResponse)
@app.get("/payroll/unmatched", response_class=HTMLResponse)
@app.get("/payroll/review", response_class=HTMLResponse)
@app.get("/payroll/sync", response_class=HTMLResponse)
@app.get("/payroll/manual", response_class=HTMLResponse)
@app.get("/payroll/cash", response_class=HTMLResponse)
@app.get("/payroll/income", response_class=HTMLResponse)
@app.get("/payroll/admin", response_class=HTMLResponse)
async def payroll_spa_tab():
    """Explicit SPA deep-link routes to avoid colliding with payroll API endpoints (e.g. /payroll/runs)."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "payroll.html"))


@app.get("/escort-roster", response_class=HTMLResponse)
@app.get("/escort-roster.html", response_class=HTMLResponse)
async def escort_roster_spa():
    """Escort Roster management SPA."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "escort-roster.html"))


@app.get("/drafts", response_class=HTMLResponse)
async def drafts_spa():
    """Draft Messages dashboard SPA."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "drafts.html"))


@app.get("/kb", response_class=HTMLResponse)
async def kb_spa():
    """Knowledge Base management SPA."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "kb.html"))


@app.get("/chat-lab", response_class=HTMLResponse)
@app.get("/open-chat", response_class=HTMLResponse)
async def chat_lab_spa():
    """Chat Lab — Admin Knowledge Q&A powered by Ollama + KB + read-only DB + internet."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "open-chat.html"))


@app.get("/wa-chat", response_class=HTMLResponse)
async def wa_chat_spa():
    """WhatsApp-like 3-panel chat frontend SPA."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "wa_chat.html"))


@app.get("/dashboard/wa-chat", response_class=HTMLResponse)
async def wa_chat_spa_dashboard():
    """WhatsApp-like chat SPA — public URL alias at /dashboard/wa-chat."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "wa_chat.html"))


@app.get("/dashboard/legacy", response_class=HTMLResponse)
async def dashboard_legacy():
    try:
        emp_count     = await fetch_one("SELECT COUNT(*) as n FROM wbom_employees WHERE status='active'")
        contact_count = await fetch_one("SELECT COUNT(*) as n FROM wbom_contacts")
        msg_count     = await fetch_one("SELECT COUNT(*) as n FROM wbom_whatsapp_messages")
        escort_count  = await fetch_one("SELECT COUNT(*) as n FROM wbom_escort_programs")
        try:
            recruit_count = await fetch_one("SELECT COUNT(*) as n FROM fazle_recruitment_sessions")
            draft_count   = await fetch_one("SELECT COUNT(*) as n FROM fazle_draft_replies WHERE status='pending' OR status IS NULL")
            pay_draft_count = await fetch_one("SELECT COUNT(*) as n FROM fazle_payment_drafts WHERE status='pending'")
        except Exception:
            recruit_count = draft_count = pay_draft_count = {"n": "?"}
    except Exception:
        emp_count = contact_count = msg_count = escort_count = recruit_count = draft_count = pay_draft_count = {"n": "?"}

    b1 = await get_bridge1().status()
    b2 = await get_bridge2().status()

    safe_banner = "" if settings.auto_reply_enabled else (
        "<div style='background:#e74c3c;color:#fff;padding:12px;border-radius:6px;margin:10px 0;font-weight:bold'>"
        "🔒 SAFE MODE ACTIVE — No outgoing messages will be sent.</div>"
    )

    html = f"""<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fazle Core Dashboard</title>
<style>
body{{font-family:Arial,sans-serif;background:#f5f5f5;margin:0;padding:20px}}
h1{{color:#1a472a;font-size:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin:20px 0}}
.card{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 2px 6px rgba(0,0,0,.1);text-align:center}}
.card .num{{font-size:36px;font-weight:bold;color:#2d6a4f}}
.card .label{{color:#555;font-size:13px;margin-top:4px}}
.bridge{{background:#fff;border-radius:8px;padding:12px;margin:8px 0;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
.ok{{color:green}} .err{{color:red}}
</style>
</head>
<body>
<h1>🌿 Fazle Core — Al-Aqsa Security</h1>
{safe_banner}
<div class="grid">
  <div class="card"><div class="num">{emp_count['n']}</div><div class="label">Active Employees</div></div>
  <div class="card"><div class="num">{contact_count['n']}</div><div class="label">Contacts</div></div>
  <div class="card"><div class="num">{msg_count['n']}</div><div class="label">Messages</div></div>
  <div class="card"><div class="num">{escort_count['n']}</div><div class="label">Escort Programs</div></div>
  <div class="card"><div class="num">{recruit_count['n']}</div><div class="label">Recruitment Leads</div></div>
  <div class="card"><div class="num">{draft_count['n']}</div><div class="label">Pending Drafts</div></div>
  <div class="card"><div class="num">{pay_draft_count['n']}</div><div class="label">Payment Drafts</div></div>
</div>
<h2>Bridge Status</h2>
<div class="bridge"><b>Bridge 1 (HR — {settings.bridge1_number}):</b>
  <span class="{'ok' if not b1.get('error') else 'err'}">{b1}</span>
</div>
<div class="bridge"><b>Bridge 2 (OPS — {settings.bridge2_number}):</b>
  <span class="{'ok' if not b2.get('error') else 'err'}">{b2}</span>
</div>
<h2>Admin Numbers</h2>
<div class="bridge">Meta Admin: <b>{settings.admin_meta_number}</b></div>
<div class="bridge">Bridge1 Admin: <b>{settings.bridge1_number}</b></div>
<div class="bridge">Bridge2 Admin: <b>{settings.bridge2_number}</b></div>
<div class="bridge">Accountant: <b>{settings.accountant_phone}</b></div>
<p style="color:#999;font-size:12px;margin-top:30px">Fazle Core v1.0 | Port {settings.app_port} | Safe Mode: {'OFF' if settings.auto_reply_enabled else 'ON'}</p>
</body></html>"""
    return HTMLResponse(html)


# ── Helpers ────────────────────────────────────────────────────────────────────
async def _send_meta(to: str, text: str) -> bool:
    url = f"{settings.meta_api_url}/{settings.meta_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.meta_api_token}"},
            )
            return r.status_code == 200
    except Exception as e:
        log.error(f"Meta send error: {e}")
        return False


async def _send_messenger(recipient_id: str, text: str) -> bool:
    """Send a Facebook Messenger reply via Page Access Token."""
    if not settings.fb_page_access_token:
        log.error("[MESSENGER] fb_page_access_token not configured")
        return False
    url = f"{settings.meta_api_url}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "messaging_type": "RESPONSE",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                json=payload,
                params={"access_token": settings.fb_page_access_token},
            )
            if r.status_code != 200:
                log.error(f"[MESSENGER] send failed {r.status_code}: {r.text[:200]}")
            return r.status_code == 200
    except Exception as e:
        log.error(f"[MESSENGER] send error: {e}")
        return False


async def _send_fb_comment_reply(comment_id: str, text: str) -> bool:
    """Reply to a Facebook Page comment via Page Access Token."""
    if not settings.fb_page_access_token:
        log.error("[FB-COMMENT] fb_page_access_token not configured")
        return False
    url = f"{settings.meta_api_url}/{comment_id}/comments"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                params={"access_token": settings.fb_page_access_token},
                json={"message": text},
            )
            if r.status_code != 200:
                log.error(f"[FB-COMMENT] reply failed {r.status_code}: {r.text[:200]}")
            return r.status_code == 200
    except Exception as e:
        log.error(f"[FB-COMMENT] reply error: {e}")
        return False


async def _save_message(
    source: str,
    sender: str,
    text: str,
    direction: str,
    identity_role: str = "",
    identity_confidence: int = 0,
    receiver_number: str = "",
    intent_detected: str = "",
    extracted_text: str = "",
    msg_type: str = "text",
    source_message_ref: str = "",
    metadata: Optional[dict] = None,
):
    _receiver = receiver_number or _source_to_receiver(source)
    _conv_key = f"{sender}:{source}" if sender else None
    try:
        await execute(
            """
            INSERT INTO wbom_whatsapp_messages
                (sender_number, message_body, message_type, direction, platform, is_processed,
                 contact_identifier, identity_role, identity_confidence,
                 receiver_number, conversation_key, intent_detected, extracted_text,
                 source_message_ref, metadata_json)
            VALUES ($1, $2, $3, $4, $5, true, $1, $6, $7, $8, $9, $10, $11, $12, $13::jsonb)
            """,
            sender, text, msg_type, direction, source,
            identity_role or None, identity_confidence or None,
            _receiver or None, _conv_key,
            intent_detected or None, extracted_text or None,
            source_message_ref or None, json.dumps(metadata or {}),
        )
    except Exception as e:
        log.warning(f"Message save error: {e}")


async def _record_meta_delivery_state(message_id: str, state: str, error: str = "") -> None:
    if not message_id:
        return
    try:
        await execute(
            """UPDATE wbom_whatsapp_messages
               SET metadata_json=COALESCE(metadata_json, '{}'::jsonb) ||
                   jsonb_build_object('delivery_state', $2::text, 'delivery_error', $3::text)
               WHERE platform='meta' AND direction='inbound' AND source_message_ref=$1""",
            message_id, state, error[:500],
        )
    except Exception as exc:
        log.warning("[META] delivery state update failed inbound_id=%s error=%s", message_id, exc)


def _source_to_receiver(source: str) -> str:
    """Map bridge source name to the phone number that received the message."""
    _map = {
        "bridge1": settings.bridge1_number,
        "bridge2": settings.bridge2_number,
        "meta":    settings.admin_meta_number,
        "whatsapp": settings.admin_meta_number,
    }
    return _map.get(source, "")


async def _save_draft(source: str, recipient: str, reply_text: str, intent: str):
    # Draft creation kill-switch — when disabled, silently skip.
    if not settings.draft_creation_enabled:
        log.debug(f"[draft] creation disabled, skipping source={source} recipient={recipient}")
        return
    reply_body = reply_text or ""
    # Suppress tight-loop duplicates for the same source+recipient+reply text.
    try:
        dup = await fetch_one(
            """
            SELECT id
            FROM fazle_draft_replies
            WHERE source = $1
              AND recipient = $2
              AND reply_text = $3
              AND created_at >= NOW() - INTERVAL '120 seconds'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            source,
            recipient,
            reply_body,
        )
        if dup:
            log.info(f"[draft] duplicate suppressed source={source} recipient={recipient} recent_id={dup.get('id')}")
            return
    except Exception as e:
        log.warning(f"[draft] duplicate check error: {e}")
    # B25 hotfix: quality gate — reject path leaks / LLM fallbacks before queueing.
    from modules.draft_quality import check_draft_quality, strip_reply_emoji
    from modules import observability as _obs
    reply_body = strip_reply_emoji(reply_body) if reply_body else reply_body
    ok, reason = check_draft_quality(reply_body)
    if not ok:
        _obs.inc("drafts_rejected_total", labels={"reason": reason or "unknown", "source": source})
        log.warning(f"[draft_quality] rejected source={source} recipient={recipient} reason={reason}")
        try:
            await execute(
                """
                INSERT INTO fazle_draft_replies
                    (source, recipient, reply_text, intent, draft_only, status, created_at, meta)
                VALUES ($1, $2, $3, $4, true, $5, NOW(),
                        jsonb_build_object('quality_reason', $6, 'gate', 'b25'))
                """,
                source, recipient, reply_body, intent,
                "rejected_fallback" if reason == "llm_fallback" else "rejected_quality",
                reason or "unknown",
            )
        except Exception as e:
            log.warning(f"Draft save (rejected) error: {e}")
        return
    try:
        await execute(
            """
            INSERT INTO fazle_draft_replies
                (source, recipient, reply_text, intent, draft_only, status, created_at)
            VALUES ($1, $2, $3, $4, true, 'pending', NOW())
            """,
            source, recipient, reply_body, intent,
        )
    except Exception as e:
        log.warning(f"Draft save error: {e}")


async def _notify_admin(notification: dict):
    """
    Send a notification to admin (or accountant) via the appropriate bridge.
    Respects SAFE MODE — logs only when disabled.
    """
    admin_phone = notification.get("admin_phone", "")
    text        = notification.get("text", "")
    bridge_src  = notification.get("bridge", "bridge2")

    if not admin_phone or not text:
        return

    log.info(f"[notify_admin] to={admin_phone} bridge={bridge_src} text={text[:60]!r}")

    if not settings.auto_reply_enabled:
        log.warning(f"SAFE MODE: admin notification suppressed → {admin_phone}: {text[:100]}")
        return

    # B15.9: route through outbound queue when enabled
    if _use_outbound_queue() and bridge_src in ("bridge1", "bridge2"):
        try:
            idem = notification.get("idempotency_key")
            await outbound_queue.enqueue(
                admin_phone, text,
                source_bridge=bridge_src,
                purpose=notification.get("purpose", "admin-notify"),
                idempotency_key=idem,
                meta={"caller": "notify_admin"},
            )
            return
        except Exception as e:
            log.error(f"[notify_admin] enqueue error, falling back to direct: {e}")

    try:
        if bridge_src == "meta" or admin_phone == settings.admin_meta_number:
            await _send_meta(admin_phone, text)
        elif bridge_src == "bridge1":
            await get_bridge1().send(f"{admin_phone}@s.whatsapp.net", text)
        else:
            await get_bridge2().send(f"{admin_phone}@s.whatsapp.net", text)
    except Exception as e:
        log.error(f"[notify_admin] send error: {e}")



# ── Escort Slip Extractor API ──────────────────────────────────────────────────
@app.post("/escort-slip/extract", dependencies=[Depends(require_api_key)])
async def api_extract_escort_slip(body: dict):
    file_path    = body.get("file_path", "")
    source_label = body.get("source_label", "api_upload")
    # OCR extraction never finalizes a release. Only [RELEASE CONFIRMED] does.
    auto_close   = False
    if not file_path:
        raise HTTPException(status_code=400, detail="Missing file_path")
    # B15.6 — cap concurrent OCR + 30s timeout
    try:
        await asyncio.wait_for(OCR_SEMAPHORE.acquire(), timeout=30.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=429, detail="ocr busy, retry later")
    try:
        result = await extract_escort_slip(file_path, source_label=source_label, save_to_db=True)
    finally:
        OCR_SEMAPHORE.release()

    # Batch 13: if extraction confident enough and auto_close=true, run lifecycle
    if auto_close and isinstance(result, dict):
        mobile = result.get("mobile") or result.get("escort_mobile")
        if mobile:
            mob_norm = "".join(ch for ch in str(mobile) if ch.isdigit())[-11:]
            emp = await fetch_one(
                "SELECT employee_id FROM wbom_employees "
                "WHERE regexp_replace(employee_mobile,'\\D','','g') LIKE '%'||$1 LIMIT 1",
                mob_norm,
            )
            if emp:
                from modules.escort_lifecycle import handle_release_event
                rel = await handle_release_event(
                    int(emp["employee_id"]), extracted=result, source="escort-slip-ocr",
                )
                result["lifecycle"] = rel
    return result


@app.post("/escort/release", dependencies=[Depends(require_api_key)])
async def api_escort_release(body: dict):
    """Batch 13: manual escort lifecycle close + draft creation.
    Body: {employee_id, end_date?, end_shift?, release_point?, day_count?}
    """
    raise HTTPException(
        status_code=409,
        detail="Direct release finalization is disabled; send an admin [RELEASE CONFIRMED] message.",
    )


# ── Payroll API (Batch 14) ─────────────────────────────────────────────────────
@app.post("/payroll/compute", dependencies=[Depends(require_api_key)])
async def api_payroll_compute(body: dict):
    """Body: {period_year, period_month, employee_id?, computed_by?}.
    If employee_id missing, compute for all Active employees.
    """
    from modules.payroll import compute_run, compute_all_for_period
    y = int(body.get("period_year") or 0)
    m = int(body.get("period_month") or 0)
    actor = body.get("computed_by") or "api"
    if not (1 <= m <= 12) or y < 2020:
        raise HTTPException(status_code=400, detail="invalid period")
    eid = body.get("employee_id")
    if eid:
        r = await compute_run(int(eid), y, m, actor)
    else:
        # B15.6 — serialize bulk computes
        async with BULK_COMPUTE_SEMAPHORE:
            r = await compute_all_for_period(y, m, actor)
    if not r.get("ok"):
        raise HTTPException(status_code=422, detail=r)
    return r


@app.post("/payroll/run/{run_id}/transition", dependencies=[Depends(require_api_key)])
async def api_payroll_transition(run_id: int, body: dict):
    """Body: {action: submit|approve|lock|paid|cancel, actor, ...}"""
    from modules.payroll import (submit_run, approve_run, lock_run,
                                  mark_paid, cancel_run)
    action = (body.get("action") or "").lower()
    actor  = body.get("actor") or "api"
    if action == "submit":
        r = await submit_run(run_id, actor)
    elif action == "approve":
        r = await approve_run(run_id, actor)
    elif action == "lock":
        r = await lock_run(run_id, actor)
    elif action == "paid":
        amount = float(body.get("amount") or 0)
        method = body.get("method") or "cash"
        ref    = body.get("reference")
        r = await mark_paid(run_id, actor, amount, method, ref)
    elif action == "cancel":
        reason = body.get("reason") or "no reason"
        r = await cancel_run(run_id, actor, reason)
    else:
        raise HTTPException(status_code=400, detail="invalid action")
    if not r.get("ok"):
        raise HTTPException(status_code=422, detail=r)
    return r


@app.get("/payroll/runs", dependencies=[Depends(require_api_key)])
async def api_payroll_list(period: str, status: str | None = None):
    """period=YYYY-MM"""
    from modules.payroll import list_runs
    try:
        y, m = period.split("-")
        y = int(y); m = int(m)
    except Exception:
        raise HTTPException(status_code=400, detail="period must be YYYY-MM")
    rows = await list_runs(y, m, status)
    return {"period": period, "status": status, "count": len(rows), "runs": rows}


@app.get("/payroll/runs/{run_id}", dependencies=[Depends(require_api_key)])
async def api_payroll_get(run_id: int):
    from modules.payroll import get_run
    r = await get_run(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="not found")
    items = await fetch_all(
        "SELECT item_id, component_type, component_label, amount, sign, "
        "source_table, source_id, notes FROM wbom_payroll_run_items "
        "WHERE run_id=$1 ORDER BY item_id", run_id,
    )
    return {"run": r, "items": [dict(i) for i in items]}


# ── Scheduler API (Batch 16) ──────────────────────────────────────────────────
@app.get("/scheduler/status", dependencies=[Depends(require_api_key)])
async def api_scheduler_status():
    return await fazle_scheduler.get_status()


@app.post("/scheduler/run/{job_name}", dependencies=[Depends(require_api_key)])
async def api_scheduler_run(job_name: str):
    if job_name not in fazle_scheduler.list_job_names():
        raise HTTPException(status_code=404,
                            detail=f"unknown job; available: {fazle_scheduler.list_job_names()}")
    result = await fazle_scheduler.trigger_job(job_name)
    return {"job": job_name, "result": result}


# ── Reports API (Batch 17) ────────────────────────────────────────────────────
from modules import reports as fazle_reports  # noqa: E402

@app.get("/reports", dependencies=[Depends(require_api_key)])
async def api_reports_list():
    return {"available": fazle_reports.list_reports()}


@app.get("/reports/{name}", dependencies=[Depends(require_api_key)])
async def api_reports_run(name: str, request: Request,
                          fmt: str = "json", no_cache: bool = False):
    args = {k: v for k, v in request.query_params.items()
            if k not in ("fmt", "no_cache")}
    try:
        payload = await fazle_reports.run_report(
            name, args, requested_by="api", use_cache=not no_cache,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"report failed: {e}")
    if fmt == "csv":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(fazle_reports.render_csv(payload),
                                 media_type="text/csv")
    if fmt == "text":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(fazle_reports.render_text(payload))
    return payload


# ── BACKUP (Batch 18) ────────────────────────────────────────────────────────
from modules import backup as fazle_backup  # noqa: E402


@app.get("/backup/status", dependencies=[Depends(require_api_key)])
async def api_backup_status():
    return await fazle_backup.backup_status()


@app.get("/backup/list", dependencies=[Depends(require_api_key)])
async def api_backup_list(limit: int = 20):
    return {"backups": await fazle_backup.list_backups(limit=limit)}


@app.post("/backup/run", dependencies=[Depends(require_api_key)])
async def api_backup_run(rotate: bool = True):
    res = await fazle_backup.run_backup()
    out: dict = {"backup": res}
    if rotate and res.get("status") == "ok":
        out["rotate"] = await fazle_backup.rotate_backups()
    return out


@app.post("/backup/rotate", dependencies=[Depends(require_api_key)])
async def api_backup_rotate():
    return await fazle_backup.rotate_backups()


@app.post("/escort-slip/test-report", dependencies=[Depends(require_api_key)])
async def api_escort_test_report(body: dict):
    file_path = body.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="Missing file_path")
    report = await escort_test_report(file_path)
    return {"report": report}


@app.get("/escort-slip/extractions", dependencies=[Depends(require_api_key)])
async def list_escort_extractions(limit: int = 20):
    rows = await fetch_all(
        "SELECT id, created_at, document_type, mother_vessel, lighter_vessel, "
        "escort_name, start_date, completion_date, confidence "
        "FROM escort_slip_extractions ORDER BY created_at DESC LIMIT $1",
        limit,
    )
    return {"count": len(rows), "extractions": rows}


# ── Admin APIs ─────────────────────────────────────────────────────────────────
@app.get("/admin/safe-mode", dependencies=[Depends(require_api_key)])
async def safe_mode_status():
    return {
        "auto_reply_enabled": settings.auto_reply_enabled,
        "safe_mode_active": not settings.auto_reply_enabled,
        "note": "Change AUTO_REPLY_ENABLED in .env and restart to toggle.",
    }


@app.get("/admin/drafts", dependencies=[Depends(require_api_key)])
async def list_drafts(limit: int = 50):
    try:
        rows = await fetch_all(
            "SELECT * FROM fazle_draft_replies WHERE draft_only=true ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return {"count": len(rows), "drafts": rows}
    except Exception as e:
        return {"error": str(e), "drafts": []}


@app.get("/admin/payment-drafts", dependencies=[Depends(require_api_key)])
async def list_payment_draft_api(limit: int = 20):
    try:
        rows = await fetch_all(
            "SELECT * FROM fazle_payment_drafts ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return {"count": len(rows), "payment_drafts": rows}
    except Exception as e:
        return {"error": str(e), "payment_drafts": []}


@app.get("/admin/approvals", dependencies=[Depends(require_api_key)])
async def unified_approvals_queue(
    limit: int = 50,
    offset: int = 0,
    source: Optional[str] = None,
):
    """Unified pending-approvals queue. source: 'draft' | 'payment' | None (both)."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    if source and source not in ("draft", "payment"):
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=400, detail="source must be 'draft' or 'payment'")
    try:
        if source == "draft":
            rows = await fetch_all(
                """SELECT 'draft' AS source_table, id, created_at, status,
                          recipient AS phone, NULL::NUMERIC AS amount
                   FROM fazle_draft_replies
                   WHERE status IN ('pending', 'pending_selfie', 'edited')
                   ORDER BY created_at DESC LIMIT $1 OFFSET $2""",
                limit, offset,
            )
        elif source == "payment":
            rows = await fetch_all(
                """SELECT 'payment' AS source_table, id, created_at, status,
                          phone, amount
                   FROM fazle_payment_drafts
                   WHERE status = 'pending'
                   ORDER BY created_at DESC LIMIT $1 OFFSET $2""",
                limit, offset,
            )
        else:
            rows = await fetch_all(
                """
                SELECT 'draft'   AS source_table,
                       id, created_at, status,
                       recipient AS phone,
                       NULL::NUMERIC AS amount
                FROM fazle_draft_replies
                WHERE status IN ('pending', 'pending_selfie', 'edited')
                UNION ALL
                SELECT 'payment' AS source_table,
                       id, created_at, status,
                       phone, amount
                FROM fazle_payment_drafts
                WHERE status = 'pending'
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit, offset,
            )
        return {"items": [dict(r) for r in rows], "count": len(rows),
                "limit": limit, "offset": offset, "source": source}
    except Exception as e:
        return {"items": [], "count": 0, "error": str(e)}


@app.get("/admin/recruitment", dependencies=[Depends(require_api_key)])
async def list_recruitment(limit: int = 50):
    try:
        rows = await fetch_all(
            "SELECT id, phone, full_name, age, area, job_preference, score, score_bucket, funnel_stage, created_at "
            "FROM fazle_recruitment_sessions ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        return {"count": len(rows), "leads": rows}
    except Exception as e:
        return {"error": str(e), "leads": []}


# ── Memory manager — reviewed reply memory CRUD ─────────────────────────────────
@app.get("/admin/memory/reviewed-replies", dependencies=[Depends(require_api_key)])
async def list_reviewed_replies(
    active_only: int = 0,
    limit: int = 50,
    offset: int = 0,
):
    """List reviewed-reply memory entries."""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    if active_only:
        rows = await fetch_all(
            """SELECT id, source, intent, draft_type, role, recipient_phone,
                      match_scope, reply_text, status, priority, usage_count,
                      last_used_at, created_at, updated_at
               FROM fazle_reviewed_replies
               WHERE status = 'active'
               ORDER BY priority DESC, usage_count DESC
               LIMIT $1 OFFSET $2""",
            limit, offset,
        )
    else:
        rows = await fetch_all(
            """SELECT id, source, intent, draft_type, role, recipient_phone,
                      match_scope, reply_text, status, priority, usage_count,
                      last_used_at, created_at, updated_at
               FROM fazle_reviewed_replies
               ORDER BY created_at DESC
               LIMIT $1 OFFSET $2""",
            limit, offset,
        )
    return {"items": [dict(r) for r in rows], "count": len(rows),
            "limit": limit, "offset": offset, "active_only": bool(active_only)}


@app.patch("/admin/memory/reviewed-replies/{entry_id}/toggle", dependencies=[Depends(require_api_key)])
async def toggle_reviewed_reply(entry_id: int):
    """Toggle a reviewed-reply memory entry between active and disabled."""
    row = await fetch_one(
        """UPDATE fazle_reviewed_replies
           SET status     = CASE WHEN status = 'active' THEN 'disabled' ELSE 'active' END,
               updated_at = NOW()
           WHERE id = $1
           RETURNING id, status, intent, updated_at""",
        entry_id,
    )
    if row is None:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=404, detail=f"reviewed_reply id={entry_id} not found")
    return {"ok": True, **dict(row)}


@app.delete("/admin/memory/reviewed-replies/{entry_id}", dependencies=[Depends(require_api_key)])
async def delete_reviewed_reply(entry_id: int):
    """Permanently delete a reviewed-reply memory entry."""
    row = await fetch_one(
        "DELETE FROM fazle_reviewed_replies WHERE id = $1 RETURNING id",
        entry_id,
    )
    if row is None:
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=404, detail=f"reviewed_reply id={entry_id} not found")
    return {"ok": True, "deleted_id": entry_id}


# ── Batch 19 — RBAC admin endpoints ────────────────────────────────────────────
@app.get("/admin/users", dependencies=[Depends(require_command("user_list"))])
async def b19_list_users():
    from modules import rbac
    rows = await rbac.list_admins()
    return JSONResponse(
        jsonable_encoder({"count": len(rows), "users": rows}),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


@app.post("/admin/users", dependencies=[Depends(require_command("user_add"))])
async def b19_add_user(payload: dict, key: str = Depends(require_api_key)):
    from modules import rbac
    phone = (payload or {}).get("phone")
    name  = (payload or {}).get("name")
    role  = (payload or {}).get("role", "viewer")
    granted_by = (payload or {}).get("granted_by", "http")
    username = (payload or {}).get("username")
    password = (payload or {}).get("password")
    if not phone or not name:
        raise HTTPException(400, "phone and name required")
    try:
        result = await rbac.add_admin(
            phone,
            name,
            role=role,
            granted_by=granted_by,
            username=username,
            password=password,
        )
        actor = await _rbac_actor_from_key(key)
        await rbac.record_audit(
            channel="http",
            command="user_add",
            actor_phone=actor.get("phone"),
            actor_admin=actor,
            args=json.dumps({"phone": phone, "name": name, "role": role, "username": username}),
            allowed=True,
            required_role="superadmin",
            result_summary=json.dumps(result),
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/admin/users/{phone}/role", dependencies=[Depends(require_command("user_role"))])
async def b19_set_role(phone: str, payload: dict, key: str = Depends(require_api_key)):
    from modules import rbac
    role = (payload or {}).get("role")
    if not role:
        raise HTTPException(400, "role required")
    try:
        result = await rbac.set_role(phone, role, granted_by=(payload or {}).get("granted_by", "http"))
        actor = await _rbac_actor_from_key(key)
        await rbac.record_audit(
            channel="http",
            command="user_role",
            actor_phone=actor.get("phone"),
            actor_admin=actor,
            args=json.dumps({"phone": phone, "role": role}),
            allowed=True,
            required_role="superadmin",
            result_summary=json.dumps(result),
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/admin/users/{phone}/role/{role}", dependencies=[Depends(require_command("user_role"))])
async def b19_revoke_role(phone: str, role: str, key: str = Depends(require_api_key)):
    from modules import rbac
    try:
        result = await rbac.revoke_role(phone, role)
        actor = await _rbac_actor_from_key(key)
        await rbac.record_audit(
            channel="http",
            command="user_role",
            actor_phone=actor.get("phone"),
            actor_admin=actor,
            args=json.dumps({"phone": phone, "role": role, "operation": "revoke"}),
            allowed=True,
            required_role="superadmin",
            result_summary=json.dumps(result),
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/admin/users/{phone}/disable", dependencies=[Depends(require_command("user_remove"))])
async def b19_disable_user(phone: str, key: str = Depends(require_api_key)):
    from modules import rbac
    try:
        result = await rbac.disable_admin(phone)
        actor = await _rbac_actor_from_key(key)
        await rbac.record_audit(
            channel="http",
            command="user_remove",
            actor_phone=actor.get("phone"),
            actor_admin=actor,
            args=json.dumps({"phone": phone, "operation": "disable"}),
            allowed=True,
            required_role="superadmin",
            result_summary=json.dumps(result),
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/admin/users/{phone}/apikey", dependencies=[Depends(require_command("user_apikey"))])
async def b19_issue_apikey(phone: str, key: str = Depends(require_api_key)):
    from modules import rbac
    try:
        result = await rbac.issue_api_key(phone)
        actor = await _rbac_actor_from_key(key)
        await rbac.record_audit(
            channel="http",
            command="user_apikey",
            actor_phone=actor.get("phone"),
            actor_admin=actor,
            args=json.dumps({"phone": phone, "operation": "rotate_api_key"}),
            allowed=True,
            required_role="superadmin",
            result_summary=json.dumps({"status": result.get("status"), "admin_id": result.get("admin_id")}),
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/admin/audit", dependencies=[Depends(require_command("user_list"))])
async def b19_audit(limit: int = 50, command: Optional[str] = None):
    from modules import rbac
    rows = await rbac.list_audit(limit=limit, command=command)
    return JSONResponse(
        jsonable_encoder({"count": len(rows), "audit": rows}),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


# ── Batch 20 — Admin Dashboard aggregate endpoint ─────────────────────────────
@app.get("/admin/overview", dependencies=[Depends(require_api_key)])
async def b20_overview():
    """Single-roundtrip overview for the dashboard SPA."""
    from datetime import datetime, timedelta, timezone
    out: dict = {"now": datetime.now(timezone.utc).isoformat(), "errors": []}

    # safe-mode
    out["safe_mode"] = {
        "auto_reply_enabled": settings.auto_reply_enabled,
        "safe_mode_active": not settings.auto_reply_enabled,
    }

    # bridges
    try:
        b1 = await get_bridge1().status()
        b2 = await get_bridge2().status()
        out["bridges"] = {"bridge1": b1, "bridge2": b2}
    except Exception as e:
        out["bridges"] = {"error": str(e)}

    # counts
    try:
        async def _n(sql):
            r = await fetch_one(sql)
            return int(r["n"]) if r and r.get("n") is not None else 0
        msg_time_expr, msg_time_column = await _whatsapp_message_time_expr()
        out["counts"] = {
            "active_employees": await _n("SELECT COUNT(*) AS n FROM wbom_employees WHERE status='active'"),
            "contacts": await _n("SELECT COUNT(*) AS n FROM wbom_contacts"),
            "messages": await _n("SELECT COUNT(*) AS n FROM wbom_whatsapp_messages"),
            "messages_last_24h": await _n(
                f"SELECT COUNT(*) AS n FROM wbom_whatsapp_messages WHERE {msg_time_expr} > NOW() - INTERVAL '24 hours'"
            ),
            "escort_programs": await _n("SELECT COUNT(*) AS n FROM wbom_escort_programs"),
            "recruitment_leads": await _n("SELECT COUNT(*) AS n FROM fazle_recruitment_sessions"),
            "pending_drafts": await _n("SELECT COUNT(*) AS n FROM fazle_draft_replies WHERE status='pending' OR status IS NULL"),
            "pending_payment_drafts": await _n("SELECT COUNT(*) AS n FROM fazle_payment_drafts WHERE status='pending'"),
            "admin_users": await _n("SELECT COUNT(*) AS n FROM fazle_admins WHERE status='active'"),
        }
        out["message_timestamp_column"] = msg_time_column
    except Exception as e:
        out["counts"] = {}
        out["errors"].append(f"counts: {e}")

    # scheduler
    try:
        from modules import scheduler as fazle_scheduler
        sched = await fazle_scheduler.get_status()
        out["scheduler"] = {
            "running": bool(sched.get("enabled")),
            "tz": sched.get("tz"),
            "jobs": len(sched.get("jobs", [])),
            "next_jobs": [
                {"id": j.get("job_name"), "next_run": j.get("next_run_at")}
                for j in sched.get("jobs", [])[:8]
            ],
        }
    except Exception as e:
        out["scheduler"] = {"error": str(e)}

    # backup
    try:
        from modules import backup as db_backup
        st = await db_backup.backup_status()
        # normalise keys for the dashboard
        out["backup"] = {
            "latest": st.get("latest"),
            "files": st.get("files_on_disk"),
            "total_bytes": st.get("total_bytes"),
            "age_hours": st.get("newest_age_h"),
            "dir": st.get("dir"),
        }
    except Exception as e:
        out["backup"] = {"error": str(e)}

    # audit (24h count + last 5)
    try:
        c = await fetch_val(
            "SELECT COUNT(*) FROM fazle_admin_audit WHERE created_at > now() - interval '24 hours'"
        )
        denied = await fetch_val(
            "SELECT COUNT(*) FROM fazle_admin_audit WHERE allowed=false AND created_at > now() - interval '24 hours'"
        )
        out["audit"] = {
            "last_24h_total": int(c or 0),
            "last_24h_denied": int(denied or 0),
        }
    except Exception as e:
        out["audit"] = {"error": str(e)}

    # rag (B21) — quick stats, non-fatal
    try:
        from modules import rag
        out["rag"] = await rag.stats()
    except Exception as e:
        out["rag"] = {"error": str(e)}

    return out


# ── Batch 21 — RAG endpoints ──────────────────────────────────────────────────
@app.get("/rag/stats", dependencies=[Depends(require_api_key)])
async def rag_stats():
    from modules import rag
    return {"stats": await rag.stats(), "qdrant": await rag.qdrant_status(), "ts": time.time()}


@app.get("/rag/search", dependencies=[Depends(require_api_key)])
async def rag_search(q: str, k: int = 5, min_score: float = 0.0):
    from modules import rag
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="q required")
    hits = await rag.search(q, k=max(1, min(k, 25)), min_score=min_score)
    return {"q": q, "k": k, "hits": hits, "count": len(hits)}


@app.get("/rag/answer", dependencies=[Depends(require_api_key)])
async def rag_answer(q: str, k: int = 3, min_score: float = 1.0, llm: bool = True):
    """
    RAG answer endpoint.
    llm=true (default): BM25 search → Ollama generation → natural Bengali answer.
    llm=false: BM25 search → raw chunk concatenation (fast, no LLM).
    Falls back to raw chunks if Ollama is unavailable or times out.
    """
    from modules import rag
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="q required")
    res = await rag.answer(q, k=max(1, min(k, 10)), min_score=min_score)
    if res is None:
        return {"q": q, "answer": None, "citations": [], "llm_used": False}

    if llm:
        # Chunks are in res["answer"] as "[1] text\n\n[2] text…"; use as LLM context
        raw_answer = res.get("answer", "")
        llm_answer = await ai.generate_rag_answer(q, raw_answer)
        if llm_answer:
            return {
                "q": q,
                "answer": llm_answer,
                "raw_chunks": raw_answer,
                "citations": res.get("citations", []),
                "top_score": res.get("top_score"),
                "llm_used": True,
            }
        # Fallback: Ollama unavailable or timed out
        log.warning("[rag/answer] LLM generation failed, returning raw chunks")

    return {"q": q, **res, "llm_used": False}


@app.post("/rag/reindex", dependencies=[Depends(require_api_key)])
async def rag_reindex():
    from modules import rag
    qdrant_before = await rag.qdrant_status()
    s = await rag.build_index()
    qdrant_after = await rag.qdrant_status()
    return {"ok": True, "stats": s, "qdrant_before": qdrant_before, "qdrant_after": qdrant_after}


# ── Chat Lab — Admin Knowledge Q&A ────────────────────────────────────────────
# Full Ollama brain: KB (RAG) + read-only production DB tools + internet fetch
# + Ollama memory DB (writable). Production DB is NEVER written by AI.
_CHAT_ALLOWED_MODELS = {"qwen3:14b", "qwen3:8b", "qwen2.5:3b"}

_CHAT_TIMEOUT_S = 240

_EXACT_DB_ANSWER_TOOLS = {
    "get_daily_payments",
    "get_employee_month_payments",
    "get_lighter_assignment",
}


@app.get("/chat/models", dependencies=[Depends(require_api_key)])
async def chat_models():
    """Return available models for the Chat Lab selector."""
    settings = get_settings()
    ollama_health = await ai.check_ollama_health()
    installed = set(ollama_health.get("models", []))
    models = []
    for m in ("qwen3:14b", "qwen3:8b", "qwen2.5:3b"):
        models.append({
            "id": m,
            "available": m in installed,
            "active": m == settings.ollama_model,
        })
    return {"models": models, "default": settings.ollama_model}


@app.get("/chat/memory/stats", dependencies=[Depends(require_api_key)])
async def chat_memory_stats():
    """Return Ollama memory DB statistics."""
    try:
        from modules import ollama_memory
        stats = await ollama_memory.list_memory_stats()
        return {"ok": True, "stats": stats}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/chat/memory/history", dependencies=[Depends(require_api_key)])
async def chat_memory_history(limit: int = 20):
    """Return recent Q&A history from Ollama memory DB."""
    try:
        from modules import ollama_memory
        rows = await ollama_memory.get_recent_questions(limit=min(limit, 50))
        return {"ok": True, "history": rows, "count": len(rows)}
    except Exception as e:
        return {"ok": False, "error": str(e), "history": []}


@app.post("/chat/message", dependencies=[Depends(require_api_key)])
async def chat_message(request: Request):
    """
    Chat Lab — Admin Knowledge Q&A (Phase 4+5 enhanced).

    Context pipeline (in order):
      1. Ollama memory — past Q&A facts relevant to this question
      2. RAG — knowledge_base KB articles
      3. Read-only DB tools — employees, contacts, escort, payroll, attendance,
         messages, bridge status (auto-detected from question)
      4. Internet fetch — if question asks for external info

    Body JSON:
      q           str   — admin question (required)
      model       str   — model override (optional)
      history     list  — [{role, content}] last N turns (optional)
      app_context bool  — use KB/RAG (default true)
      db_context  bool  — use read-only DB tools (default true)
      web_context bool  — allow internet fetch if URL in question (default true)
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    q = (body.get("q") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="q required")

    requested_model = (body.get("model") or "").strip() or None
    if requested_model and requested_model not in _CHAT_ALLOWED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"model not allowed; choose from {sorted(_CHAT_ALLOWED_MODELS)}",
        )

    history = body.get("history") or []
    if not isinstance(history, list):
        history = []

    app_context: bool = body.get("app_context", True)
    db_context: bool = body.get("db_context", True)
    web_context: bool = body.get("web_context", True)

    settings = get_settings()
    active_model = requested_model or settings.ollama_model

    # ── 1. Ollama memory context (past relevant facts) ──────────────────────
    memory_context = ""
    try:
        from modules import ollama_memory
        recent_qs = await ollama_memory.get_recent_questions(limit=5)
        if recent_qs:
            mem_lines = [
                f"[Memory Q{i+1}] {rq['question'][:120]}: {rq['answer_summary'][:200]}"
                for i, rq in enumerate(recent_qs)
                if rq.get("answer_summary")
            ]
            if mem_lines:
                memory_context = "আগের প্রশ্নের স্মৃতি:\n" + "\n".join(mem_lines)
    except Exception as e:
        log.debug("ollama_memory read failed: %s", e)

    # ── 2. RAG / KB context ─────────────────────────────────────────────────
    rag_context = ""
    citations: list = []
    top_score = None
    if app_context:
        try:
            from modules import rag
            res = await rag.answer(q, k=4, min_score=0.8)
            if res:
                rag_context = res.get("answer", "")
                citations = res.get("citations", [])
                top_score = res.get("top_score")
        except Exception as e:
            log.debug("RAG search failed: %s", e)

    # ── 3. Read-only production DB tools ────────────────────────────────────
    db_tool_results: list[dict] = []
    db_tools_used: list[str] = []
    exact_tools_needed: list[str] = []
    try:
        from modules import ai_readonly_tools
        tools_needed = ai_readonly_tools.detect_tools_needed(q)
        exact_tools_needed = [t for t in tools_needed if t in _EXACT_DB_ANSWER_TOOLS]

        # Exact operational questions are database facts, not model opinions.
        # Force DB tools even if the caller turns db_context off.
        if db_context or exact_tools_needed:
            for tool_name in tools_needed[:3]:  # max 3 tools per query
                result = await ai_readonly_tools.run_tool(tool_name, q)
                if result.get("data") is not None:
                    db_tool_results.append(result)
                    db_tools_used.append(tool_name)
    except Exception as e:
        log.debug("ai_readonly_tools failed: %s", e)

    # Exact-data operational questions should not be reinterpreted by the LLM.
    # Return the tool answer directly so qwen cannot invent names/amounts when
    # RAG has weak context or no live DB evidence.
    for tool_result in db_tool_results:
        if tool_result.get("tool") in _EXACT_DB_ANSWER_TOOLS and tool_result.get("answer"):
            answer = str(tool_result["answer"])

            async def _save_exact_memory() -> None:
                try:
                    from modules import ollama_memory
                    await ollama_memory.record_question(
                        question=q,
                        answer_summary=answer[:500],
                        source_refs=[str(tool_result.get("tool"))],
                    )
                except Exception:
                    pass
            asyncio.create_task(_save_exact_memory())

            return {
                "answer": answer,
                "model": "deterministic-db-tool",
                "rag_used": bool(rag_context),
                "db_tools_used": db_tools_used,
                "web_fetched": False,
                "citations": citations,
                "top_score": top_score,
                "memory_context_used": bool(memory_context),
            }
    if exact_tools_needed:
        return {
            "answer": "এই প্রশ্নের উত্তর live database থেকে দিতে হবে, কিন্তু এখন DB tool থেকে নির্ভরযোগ্য ফল পাওয়া যায়নি। তাই ভুল তথ্য না দিয়ে উত্তর বন্ধ রাখা হলো।",
            "model": "deterministic-db-tool",
            "rag_used": bool(rag_context),
            "db_tools_used": db_tools_used,
            "web_fetched": False,
            "citations": citations,
            "top_score": top_score,
            "memory_context_used": bool(memory_context),
            "error": "exact_db_tool_unavailable",
        }

    # ── 4. Internet fetch (if URL in question and web_context enabled) ──────
    web_result: dict | None = None
    if web_context:
        import re as _re
        url_match = _re.search(r"https?://[^\s\"'<>]+", q)
        if url_match:
            try:
                from modules import ai_readonly_tools
                web_result = await ai_readonly_tools.fetch_web_page(url_match.group())
            except Exception as e:
                log.debug("web fetch failed: %s", e)

    # ── Build combined context block ─────────────────────────────────────────
    context_parts: list[str] = []
    if memory_context:
        context_parts.append(memory_context)
    if rag_context:
        context_parts.append("জ্ঞানভাণ্ডার (KB):\n" + rag_context)
    for tool_result in db_tool_results:
        tool_name = tool_result["tool"]
        data = tool_result["data"]
        if isinstance(data, list) and data:
            # Format as compact text table
            keys = list(data[0].keys())[:6]
            lines = [", ".join(str(row.get(k, "")) for k in keys) for row in data[:15]]
            context_parts.append(f"[{tool_name}]:\n" + "\n".join(lines))
        elif isinstance(data, dict):
            context_parts.append(f"[{tool_name}]: " + ", ".join(f"{k}={v}" for k, v in list(data.items())[:8]))
    if web_result and web_result.get("ok") and web_result.get("content"):
        context_parts.append("ওয়েব তথ্য:\n" + web_result["content"][:1500])

    combined_context = "\n\n".join(context_parts)

    # ── Generate answer ──────────────────────────────────────────────────────
    try:
        answer = await asyncio.wait_for(
            ai.generate_chat_reply(q, combined_context, history, model=active_model),
            timeout=_CHAT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("[chat/message] LLM timeout after %ss model=%s", _CHAT_TIMEOUT_S, active_model)
        return JSONResponse(
            status_code=504,
            content={
                "error": "llm_timeout",
                "answer": "মডেলটি সময়মতো উত্তর দিতে পারেনি। একটু পরে আবার চেষ্টা করুন।",
                "model": active_model,
                "rag_used": bool(rag_context),
                "timeout_s": _CHAT_TIMEOUT_S,
            },
        )

    if not answer:
        answer = combined_context[:500] or "উত্তর পাওয়া যায়নি। অফিসে যোগাযোগ করুন।"

    # ── 5. Save Q&A to Ollama memory DB (fire and forget) ───────────────────
    async def _save_memory() -> None:
        try:
            from modules import ollama_memory
            src_refs = citations + db_tools_used
            if web_result and web_result.get("url"):
                src_refs.append(web_result["url"])
            await ollama_memory.record_question(
                question=q,
                answer_summary=answer[:500] if answer else "",
                source_refs=src_refs,
            )
        except Exception:
            pass
    asyncio.create_task(_save_memory())

    return {
        "answer": answer,
        "model": active_model,
        "rag_used": bool(rag_context),
        "db_tools_used": db_tools_used,
        "web_fetched": bool(web_result and web_result.get("ok")),
        "citations": citations,
        "top_score": top_score,
        "memory_context_used": bool(memory_context),
    }


# ── User Management APIs ──────────────────────────────────────────────────────

@app.get("/api/users/search", dependencies=[Depends(require_api_key)])
async def api_users_search(q: str, limit: int = 10):
    """Search user_profiles by canonical phone or name."""
    from modules.phone_normalizer import normalize_phone
    limit = max(1, min(limit, 100))
    canonical = normalize_phone(q)
    if canonical:
        rows = await fetch_all(
            "SELECT * FROM user_profiles WHERE phone_canonical = $1",
            canonical,
        )
    else:
        pattern = f"%{q}%"
        rows = await fetch_all(
            "SELECT * FROM user_profiles WHERE name ILIKE $1 OR phone_canonical LIKE $2 LIMIT $3",
            pattern, pattern, limit,
        )
    return {"users": rows, "count": len(rows)}


@app.get("/api/users/{phone}", dependencies=[Depends(require_api_key)])
async def api_get_user(phone: str):
    """Return full user context (profile + memories) for a phone number."""
    from modules.phone_normalizer import normalize_phone
    from modules.role_classifier import get_user_context
    canonical = normalize_phone(phone) or phone
    return await get_user_context(canonical)


@app.put("/api/users/{phone}", dependencies=[Depends(require_api_key)])
async def api_update_user(phone: str, data: dict):
    """Upsert a user_profiles row."""
    from modules.phone_normalizer import normalize_phone
    canonical = normalize_phone(phone) or phone
    await execute(
        """INSERT INTO user_profiles
               (phone_canonical, phone_raw, name, role, relationship_type, notes)
           VALUES ($1, $2, $3, $4, $5, $6)
           ON CONFLICT (phone_canonical) DO UPDATE SET
               name              = COALESCE(EXCLUDED.name, user_profiles.name),
               role              = COALESCE(EXCLUDED.role, user_profiles.role),
               relationship_type = COALESCE(EXCLUDED.relationship_type, user_profiles.relationship_type),
               notes             = COALESCE(EXCLUDED.notes, user_profiles.notes),
               updated_at        = NOW()""",
        canonical,
        phone if phone != canonical else None,
        data.get("name"),
        data.get("role", "unknown"),
        data.get("relationship_type"),
        data.get("notes"),
    )
    return {"status": "updated", "phone": canonical}


@app.post("/api/users/{phone}/memory", dependencies=[Depends(require_api_key)])
async def api_add_user_memory(phone: str, data: dict):
    """Manually add a fact to user_memory."""
    from modules.phone_normalizer import normalize_phone
    from modules.memory_extractor import _ensure_profile
    canonical = normalize_phone(phone) or phone
    memory_type = (data.get("type") or data.get("memory_type") or "conversation_fact")[:50]
    content = (data.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="content required")
    await _ensure_profile(canonical)
    await execute(
        """INSERT INTO user_memory (phone_canonical, memory_type, content, source)
           VALUES ($1, $2, $3, 'admin')""",
        canonical, memory_type, content[:2000],
    )
    return {"status": "saved", "phone": canonical, "type": memory_type}


# ── Memory Review APIs ────────────────────────────────────────────────────────

@app.get("/api/memory/pending", dependencies=[Depends(require_api_key)])
async def api_memory_pending(limit: int = 50):
    """LLM replies pending KB promotion review."""
    limit = max(1, min(limit, 200))
    rows = await fetch_all(
        """SELECT id, provider, model, trigger_text, reply_text, intent,
                  role, source, is_fallback, created_at
           FROM llm_learning_memory
           WHERE promoted_to_kb = FALSE AND dismissed = FALSE
           ORDER BY created_at DESC
           LIMIT $1""",
        limit,
    )
    return {"memories": rows, "count": len(rows)}


@app.post("/api/memory/{memory_id}/promote", dependencies=[Depends(require_api_key)])
async def api_memory_promote(memory_id: int, data: dict = {}):
    """Promote an LLM reply to the knowledge base."""
    row = await fetch_one(
        "SELECT * FROM llm_learning_memory WHERE id = $1",
        memory_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="memory not found")

    edited = (data.get("content") or "").strip()
    kb_text = edited or f"প্রশ্ন: {row['trigger_text']}\nউত্তর: {row['reply_text']}"
    category = (data.get("category") or "conversation")[:50]

    await execute(
        """INSERT INTO fazle_knowledge_base
               (category, key, trigger_keywords, reply_text, confidence, is_active)
           VALUES ($1, $2, '', $3, 0.9, true)
           ON CONFLICT DO NOTHING""",
        category,
        f"llm_promoted_{memory_id}",
        kb_text[:2000],
    )
    await execute(
        "UPDATE llm_learning_memory SET promoted_to_kb = TRUE, promoted_at = NOW() WHERE id = $1",
        memory_id,
    )
    return {"status": "promoted", "memory_id": memory_id, "category": category}


@app.post("/api/memory/{memory_id}/dismiss", dependencies=[Depends(require_api_key)])
async def api_memory_dismiss(memory_id: int):
    """Dismiss an LLM reply from the review queue."""
    result = await execute(
        "UPDATE llm_learning_memory SET dismissed = TRUE WHERE id = $1",
        memory_id,
    )
    if result == "UPDATE 0":
        raise HTTPException(status_code=404, detail="memory not found")
    return {"status": "dismissed", "memory_id": memory_id}


# ── LLM Usage Stats ───────────────────────────────────────────────────────────

@app.get("/api/stats/llm", dependencies=[Depends(require_api_key)])
async def api_llm_stats():
    """GitHub Models + Ollama usage stats from llm_learning_memory."""
    try:
        rows = await fetch_all(
            """SELECT
                   provider,
                   COUNT(*)                                                      AS total,
                   COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 day') AS today,
                   COUNT(*) FILTER (WHERE is_fallback = TRUE)                   AS fallback_total
               FROM llm_learning_memory
               GROUP BY provider
               ORDER BY total DESC"""
        )
        return {
            "providers": rows,
            "note": "GitHub Models free tier: 15 RPM",
            "ts": time.time(),
        }
    except Exception as e:
        log.warning("[api/stats/llm] query failed: %s", e)
        return {"providers": [], "error": str(e), "ts": time.time()}


# ── Batch 22 — Observability endpoints ────────────────────────────────────────
@app.get("/metrics")
async def metrics_prometheus():
    """Prometheus text exposition (unauthenticated for scraping; bind 127.0.0.1)."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(obs.render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/metrics/json", dependencies=[Depends(require_api_key)])
async def metrics_json():
    return obs.snapshot()


@app.get("/observability/errors", dependencies=[Depends(require_api_key)])
async def observability_errors(limit: int = 50):
    rows = await fetch_all(
        "SELECT module, error_type, message, count, first_seen, last_seen "
        "FROM fazle_error_log ORDER BY last_seen DESC LIMIT $1",
        max(1, min(limit, 500)),
    )
    out = []
    for r in rows:
        out.append({
            "module": r["module"],
            "error_type": r["error_type"],
            "message": r["message"],
            "count": int(r["count"] or 0),
            "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        })
    return {"rows": out, "count": len(out)}


@app.get("/observability/summary", dependencies=[Depends(require_api_key)])
async def observability_summary():
    snap = obs.snapshot()
    # rollup http counters
    http_total = 0
    by_status: dict[str, int] = {}
    by_path: dict[str, int] = {}
    for entry in snap["counters"].get("fazle_http_requests_total", []):
        http_total += int(entry["value"])
        st = entry["labels"].get("status", "0")
        by_status[st] = by_status.get(st, 0) + int(entry["value"])
        p = entry["labels"].get("path", "?")
        by_path[p] = by_path.get(p, 0) + int(entry["value"])
    # latency overall
    durs = snap["histograms"].get("fazle_http_request_duration_ms", [])
    total_count = sum(d["count"] for d in durs)
    total_sum = sum(d["sum_ms"] for d in durs)
    avg_ms = round(total_sum / total_count, 2) if total_count else 0
    # error log totals (last 24h)
    err_24h = 0
    try:
        err_24h = int(await fetch_val(
            "SELECT COALESCE(SUM(count),0) FROM fazle_error_log WHERE last_seen > now() - interval '24 hours'"
        ) or 0)
    except Exception:
        pass
    top_paths = sorted(by_path.items(), key=lambda x: -x[1])[:10]
    return {
        "uptime_s": snap["uptime_s"],
        "http_total": http_total,
        "http_by_status": by_status,
        "http_avg_ms": avg_ms,
        "top_paths": [{"path": p, "count": c} for p, c in top_paths],
        "errors_24h": err_24h,
    }
