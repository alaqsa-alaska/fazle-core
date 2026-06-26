"""
Fazle Core — Scheduler (Batch 16)

APScheduler async scheduler with cron-style jobs:
- daily_payroll_compute (02:00 Asia/Dhaka)
- dlq_alert (every 15 min)
- health_summary (every 6h)
- stale_escort_reminder (daily 09:00)
- payment_reconciliation (hourly)
- backup_staleness_alert (daily 03:00)
- daily_memory_review  (09:00 Asia/Dhaka)  ← Phase 6
- rag_rebuild          (18:00 Asia/Dhaka)  ← Phase 6

Each job records a row in fazle_scheduled_jobs and updates the
'scheduler' service heartbeat.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.database import execute, fetch_one, fetch_all, fetch_val
from app.error_log import record_error

log = logging.getLogger("fazle.scheduler")

_scheduler: Optional[AsyncIOScheduler] = None
_job_registry: dict[str, Callable[[], Awaitable[dict]]] = {}


def _enabled() -> bool:
    return os.getenv("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes")


def _tz() -> str:
    return os.getenv("SCHEDULER_TIMEZONE", "Asia/Dhaka")


def _admin_number() -> Optional[str]:
    raw = os.getenv("ADMIN_NUMBERS", "").strip()
    return (raw.split(",")[0].strip() or None) if raw else None


# ── Job runner wrapper ────────────────────────────────────────────────────────
async def _record_job_run(name: str, status: str, duration_ms: int,
                           error: Optional[str] = None,
                           next_run_at: Optional[datetime] = None) -> None:
    try:
        await execute(
            """INSERT INTO fazle_scheduled_jobs
                  (job_name, last_run_at, last_status, last_duration_ms, last_error, next_run_at, run_count)
               VALUES ($1, NOW(), $2, $3, $4, $5, 1)
               ON CONFLICT (job_name) DO UPDATE
                  SET last_run_at = NOW(),
                      last_status = EXCLUDED.last_status,
                      last_duration_ms = EXCLUDED.last_duration_ms,
                      last_error = EXCLUDED.last_error,
                      next_run_at = EXCLUDED.next_run_at,
                      run_count = fazle_scheduled_jobs.run_count + 1""",
            name, status, duration_ms, (error or None)[:1000] if error else None, next_run_at,
        )
    except Exception as e:
        log.warning(f"[scheduler] record_job_run failed: {e}")


async def _heartbeat_scheduler() -> None:
    try:
        depth = await fetch_val(
            "SELECT COUNT(*) FROM fazle_scheduled_jobs WHERE last_status='running'"
        )
        await execute(
            """INSERT INTO fazle_service_heartbeats (service, last_seen, queue_depth)
               VALUES ('scheduler', NOW(), $1)
               ON CONFLICT (service) DO UPDATE SET last_seen=NOW(), queue_depth=EXCLUDED.queue_depth""",
            int(depth or 0),
        )
    except Exception as e:
        log.warning(f"[scheduler] heartbeat failed: {e}")


def _next_run_for(name: str) -> Optional[datetime]:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(name)
    return job.next_run_time if job else None


async def _run_wrapped(name: str, fn: Callable[[], Awaitable[dict]]) -> dict:
    """Run a job, time it, record outcome. Used by both APScheduler and manual triggers."""
    t0 = time.time()
    try:
        result = await fn()
        dur = int((time.time() - t0) * 1000)
        status = result.get("status", "ok") if isinstance(result, dict) else "ok"
        await _record_job_run(name, status, dur, None, _next_run_for(name))
        await _heartbeat_scheduler()
        return result if isinstance(result, dict) else {"status": status, "result": result}
    except Exception as e:
        dur = int((time.time() - t0) * 1000)
        await _record_job_run(name, "error", dur, str(e), _next_run_for(name))
        await record_error(f"scheduler.{name}", e)
        log.exception(f"[scheduler:{name}] failed: {e}")
        await _heartbeat_scheduler()
        return {"status": "error", "error": str(e)}


def register_job(name: str, fn: Callable[[], Awaitable[dict]]) -> None:
    _job_registry[name] = fn


async def trigger_job(name: str) -> dict:
    """Invoke a registered job immediately (used by admin command + tests)."""
    fn = _job_registry.get(name)
    if not fn:
        return {"status": "error", "error": f"unknown job: {name}"}
    return await _run_wrapped(name, fn)


# ── Job implementations ───────────────────────────────────────────────────────
async def job_daily_payroll() -> dict:
    """B16.2 — compute payroll for current month for all active employees."""
    from modules.payroll import compute_all_for_period
    now = datetime.now()
    r = await compute_all_for_period(now.year, now.month, "scheduler")
    # Notify owner with one-line summary (idempotent per-day key)
    admin = _admin_number()
    if admin and isinstance(r, dict):
        from modules import outbound
        key = f"payroll-daily-{now.strftime('%Y%m%d')}"
        cnt = r.get("count") or r.get("processed") or 0
        msg = f"📊 Payroll auto-compute {now.year}-{now.month:02d}: {cnt} runs processed."
        await outbound.enqueue(admin, msg, source_bridge="bridge1", fallback_channel="bridge2",
                               purpose="payroll-daily", idempotency_key=key)
    return {"status": "ok", "result": r}


async def job_dlq_alert() -> dict:
    """Alert only for recent actionable DLQ; never alert about alert failures."""
    from modules import outbound
    dlq = await outbound.dlq_count()
    actionable = await outbound.actionable_dlq_count()
    pend = await outbound.pending_count()
    if actionable <= 0:
        return {"status": "ok", "dlq": dlq, "actionable": 0, "pending": pend, "alerted": False}
    admin = _admin_number()
    if admin:
        key = f"dlq-alert-actionable-{actionable}-pending-{pend}"
        msg = f"⚠️ Outbound has {actionable} recent actionable DLQ message(s). Pending: {pend}. Investigate."
        await outbound.enqueue(admin, msg, source_bridge="bridge1",
                               purpose="dlq-alert", idempotency_key=key)
    return {"status": "ok", "dlq": dlq, "actionable": actionable, "pending": pend, "alerted": True}


async def job_health_summary() -> dict:
    """B16.3 — every 6h, alert admin if any health probe is non-OK."""
    from app.main import _build_health  # late import (avoid circular)
    h = await _build_health(deep=False)
    if h["status"] == "ok":
        return {"status": "ok", "overall": "ok"}
    admin = _admin_number()
    if admin:
        from modules import outbound
        slot = datetime.utcnow().strftime("%Y%m%d%H")[:9]  # 6h-ish bucket via hour//6
        slot = slot[:8] + str(int(slot[8]) // 6)
        key = f"health-summary-{slot}"
        bad = [k for k, v in h["probes"].items() if v.get("status") != "ok"]
        msg = f"🏥 System {h['status']}. Probes degraded: {', '.join(bad)}"
        await outbound.enqueue(admin, msg, source_bridge="bridge1", fallback_channel="bridge2",
                               purpose="health-summary", idempotency_key=key)
    return {"status": "ok", "overall": h["status"]}


async def job_agent_incident_summary() -> dict:
    """Summarize actionable monitoring-agent incidents with stable deduplication."""
    rows = await fetch_all(
        """SELECT severity, source, title, COUNT(*) AS n
             FROM agent.incidents
            WHERE resolved_at IS NULL
              AND title <> 'ollama_model_evicted'
            GROUP BY severity, source, title
            ORDER BY
              CASE severity WHEN 'critical' THEN 0 WHEN 'error' THEN 1 ELSE 2 END,
              title"""
    )
    if not rows:
        return {"status": "ok", "actionable": 0}

    signature = "|".join(
        f"{r['severity']}:{r['source']}:{r['title']}:{r['n']}" for r in rows
    )
    key = f"agent-incident-summary-{hashlib.sha256(signature.encode()).hexdigest()[:20]}"
    admin = _admin_number()
    if not admin:
        return {"status": "ok", "actionable": len(rows), "alerted": False}

    from modules import outbound
    details = ", ".join(f"{r['title']}×{r['n']}" for r in rows[:8])
    qid = await outbound.enqueue(
        admin,
        f"⚠️ Monitoring incidents: {details}",
        source_bridge="bridge1",
        fallback_channel="bridge2",
        purpose="agent-incident-summary",
        idempotency_key=key,
    )
    return {"status": "ok", "actionable": len(rows), "alerted": bool(qid)}


async def job_stale_escort_reminder() -> dict:
    """B16.4 — escort programs Active >N days → remind owner once per program."""
    days = int(os.getenv("ESCORT_STALE_DAYS", "30"))
    rows = await fetch_all(
        """SELECT program_id, mother_vessel, lighter_vessel, escort_employee_id,
                  COALESCE(start_date, program_date) AS started
             FROM wbom_escort_programs
            WHERE status IN ('Active','Assigned')
              AND COALESCE(start_date, program_date) < CURRENT_DATE - $1::int""",
        days,
    )
    if not rows:
        return {"status": "ok", "stale": 0}
    admin = _admin_number()
    sent = 0
    if admin:
        from modules import outbound
        for r in rows:
            pid = r["program_id"]
            key = f"stale-escort-{pid}"
            # Insert reminder marker; if already sent, skip
            inserted = await execute(
                """INSERT INTO fazle_reminders_sent (reminder_key, topic, ref_id)
                   VALUES ($1, 'stale-escort', $2)
                   ON CONFLICT (reminder_key) DO NOTHING""",
                key, str(pid),
            )
            if not inserted.endswith(" 1"):
                continue
            msg = (f"⏳ Escort program #{pid} ({r['mother_vessel']}/{r['lighter_vessel']}) "
                   f"still Active since {r['started']}. Please close or update.")
            await outbound.enqueue(admin, msg, source_bridge="bridge1", fallback_channel="bridge2",
                                   purpose="stale-escort", idempotency_key=key)
            sent += 1
    return {"status": "ok", "stale": len(rows), "alerted": sent}


async def job_payment_reconciliation() -> dict:
    """B16.5 — hourly: rematch unmatched staging payments against employees."""
    rows = await fetch_all(
        """SELECT staging_id, sender_number, extracted_mobile, extracted_name, amount
             FROM wbom_staging_payments
            WHERE matched_employee_id IS NULL
              AND status = 'pending'
              AND created_at < NOW() - INTERVAL '1 hour'
            ORDER BY staging_id
            LIMIT 50"""
    )
    matched = 0
    unmatched = 0
    for r in rows:
        sid = r["staging_id"]
        # Try mobile match first
        emp = None
        for cand in (r["extracted_mobile"], r["sender_number"]):
            if not cand:
                continue
            digits = "".join(ch for ch in str(cand) if ch.isdigit())
            if len(digits) < 10:
                continue
            tail = digits[-11:]
            emp = await fetch_one(
                "SELECT employee_id FROM wbom_employees "
                "WHERE regexp_replace(employee_mobile,'\\D','','g') LIKE '%'||$1 "
                "   OR regexp_replace(COALESCE(bkash_number,''),'\\D','','g') LIKE '%'||$1 "
                "   OR regexp_replace(COALESCE(nagad_number,''),'\\D','','g') LIKE '%'||$1 "
                "LIMIT 1",
                tail,
            )
            if emp:
                break
        if emp:
            eid = int(emp["employee_id"])
            await execute(
                "UPDATE wbom_staging_payments SET matched_employee_id=$1 WHERE staging_id=$2 AND matched_employee_id IS NULL",
                eid, sid,
            )
            await execute(
                """INSERT INTO fazle_reconciliation_log
                      (source, source_ref, matched, match_method, matched_employee_id, details)
                   VALUES ('staging_payment', $1, TRUE, 'mobile-tail-11', $2, $3)""",
                str(sid), eid, f"amount={r['amount']}",
            )
            matched += 1
        else:
            await execute(
                """INSERT INTO fazle_reconciliation_log
                      (source, source_ref, matched, match_method, details)
                   VALUES ('staging_payment', $1, FALSE, 'mobile-tail-11', $2)""",
                str(sid), f"name={r['extracted_name']!r} mobile={r['extracted_mobile']!r}",
            )
            unmatched += 1
    return {"status": "ok", "scanned": len(rows), "matched": matched, "unmatched": unmatched}


async def job_backup_staleness() -> dict:
    """B16.6 — alert if newest backup is older than N hours."""
    backup_dir = Path(os.getenv("BACKUP_DIR", "/home/azim/backups"))
    stale_hours = int(os.getenv("BACKUP_STALE_HOURS", "48"))
    if not backup_dir.exists():
        return {"status": "ok", "skipped": "no backup dir"}
    newest_age_h: Optional[float] = None
    for p in backup_dir.glob("*.dump"):
        try:
            age_h = (time.time() - p.stat().st_mtime) / 3600
            if newest_age_h is None or age_h < newest_age_h:
                newest_age_h = age_h
        except OSError:
            continue
    if newest_age_h is None:
        msg_status = "no-backups"
    elif newest_age_h > stale_hours:
        msg_status = "stale"
    else:
        return {"status": "ok", "newest_age_h": round(newest_age_h, 1)}
    admin = _admin_number()
    if admin:
        from modules import outbound
        day = datetime.utcnow().strftime("%Y%m%d")
        key = f"backup-stale-{day}"
        if msg_status == "no-backups":
            msg = f"⚠️ No DB backups found in {backup_dir}. Run a backup."
        else:
            msg = f"⚠️ Newest DB backup is {newest_age_h:.1f}h old (threshold {stale_hours}h)."
        await outbound.enqueue(admin, msg, source_bridge="bridge1", fallback_channel="bridge2",
                               purpose="backup-stale", idempotency_key=key)
    return {"status": "ok", "newest_age_h": newest_age_h, "alerted": True}


async def job_combined_draft_cleanup() -> dict:
    """P15-01/P15-02 — hourly: clean draft entries older than 24h."""
    results: dict = {"status": "ok"}

    # General admin-review reply drafts: delete pending rows after 24h if the
    # admin has not acted. Reviewed/sent/approved/rejected rows are preserved.
    try:
        from app.config import get_settings as _get_settings
        from app.database import fetch_val as _fetch_val

        ttl = int(_get_settings().draft_ttl_hours)
        deleted_general: int = await _fetch_val(
            """
            WITH deleted AS (
                DELETE FROM fazle_draft_replies
                 WHERE COALESCE(status, 'pending') = 'pending'
                   AND COALESCE(reviewed, false) = false
                   AND created_at < NOW() - ($1 || ' hours')::INTERVAL
                RETURNING id
            )
            SELECT count(*) FROM deleted
            """,
            str(ttl),
        )
        if deleted_general:
            log.info("[scheduler] combined_draft_cleanup: deleted %d stale reply drafts", deleted_general)
        results["reply_drafts_deleted"] = deleted_general
        results["reply_draft_ttl_hours"] = ttl
    except Exception as e:
        log.warning("[scheduler] combined_draft_cleanup reply-draft error: %s", e)
        results["reply_draft_error"] = str(e)

    # Escort roster drafts
    try:
        from modules.escort_roster.db import expire_stale_drafts
        from app.config import get_settings as _get_settings
        ttl = int(_get_settings().draft_ttl_hours)
        escort_result = await expire_stale_drafts(hours=ttl, actor="scheduler")
        results["escort"] = escort_result
        results["escort_draft_ttl_hours"] = ttl
    except Exception as e:
        log.warning(f"[scheduler] combined_draft_cleanup escort error: {e}")
        results["escort"] = {"error": str(e)}

    # Payment drafts
    try:
        from app.database import fetch_val as _fetch_val
        deleted: int = await _fetch_val(
            """
            WITH expired AS (
                DELETE FROM fazle_payment_drafts
                WHERE status = 'pending'
                  AND created_at < NOW() - INTERVAL '24 hours'
                RETURNING id
            )
            SELECT count(*) FROM expired
            """,
        )
        if deleted:
            log.info("[scheduler] combined_draft_cleanup: deleted %d stale payment drafts", deleted)
        results["payment_deleted"] = deleted
    except Exception as e:
        log.warning("[scheduler] combined_draft_cleanup payment error: %s", e)
        results["payment_error"] = str(e)

    return results


# ── Phase 6: Continuous Learning Loop ─────────────────────────────────────────

async def job_daily_memory_review() -> dict:
    """
    09:00 daily — scan the last 24 h of inbound WhatsApp messages, extract
    structured facts via GitHub Models, and persist them to user_memory.

    Uses a 4-second inter-call sleep to stay within the GitHub Models free-tier
    rate limit of 15 RPM.  Limited to 50 unique senders per run to cap latency.
    """
    rows = await fetch_all(
        """SELECT DISTINCT ON (sender_number) sender_number
           FROM wbom_whatsapp_messages
           WHERE direction = 'inbound'
             AND sender_number IS NOT NULL
             AND received_at > NOW() - INTERVAL '24 hours'
           LIMIT 50"""
    )
    if not rows:
        return {"status": "ok", "processed": 0}

    processed = 0
    skipped = 0

    for row in rows:
        phone = (row.get("sender_number") or "").strip()
        if not phone:
            continue

        # Last 6 messages (3 turns) for this sender, oldest-first
        msgs = await fetch_all(
            """SELECT message_body, direction
               FROM wbom_whatsapp_messages
               WHERE sender_number = $1
               ORDER BY received_at DESC
               LIMIT 6""",
            phone,
        )
        if not msgs:
            skipped += 1
            continue

        conversation = [
            {
                "role": "user" if m["direction"] == "inbound" else "assistant",
                "content": (m["message_body"] or "")[:400],
            }
            for m in reversed(msgs)
            if m.get("message_body")
        ]
        if not conversation:
            skipped += 1
            continue

        try:
            from modules.memory_extractor import extract_and_save_memory
            await extract_and_save_memory(phone=phone, conversation=conversation)
            processed += 1
        except Exception as e:
            log.warning("[scheduler:daily_memory_review] %s failed: %s", phone, e)
            skipped += 1

        # 15 RPM free-tier rate limit → 4 s between calls
        await asyncio.sleep(4)

    log.info("[scheduler] daily_memory_review: processed=%d skipped=%d", processed, skipped)
    return {"status": "ok", "processed": processed, "skipped": skipped}


async def job_rag_rebuild() -> dict:
    """
    18:00 daily — rebuild the BM25 RAG index so knowledge added throughout
    the day (promoted memories, new KB uploads) is queryable by evening.
    """
    try:
        from modules import rag
        stats = await rag.build_index()
        log.info("[scheduler] rag_rebuild done: %s", stats)
        return {"status": "ok", "stats": stats}
    except Exception as e:
        log.warning("[scheduler] rag_rebuild failed: %s", e)
        return {"status": "error", "error": str(e)}


# ── Lifespan API ──────────────────────────────────────────────────────────────
def start_scheduler() -> Optional[AsyncIOScheduler]:
    global _scheduler
    if not _enabled():
        log.info("[scheduler] disabled (SCHEDULER_ENABLED=false)")
        return None
    if _scheduler is not None:
        return _scheduler

    register_job("daily_payroll_compute", job_daily_payroll)
    register_job("dlq_alert", job_dlq_alert)
    register_job("health_summary", job_health_summary)
    register_job("agent_incident_summary", job_agent_incident_summary)
    register_job("stale_escort_reminder", job_stale_escort_reminder)
    register_job("payment_reconciliation", job_payment_reconciliation)
    register_job("backup_staleness_alert", job_backup_staleness)
    register_job("combined_draft_cleanup", job_combined_draft_cleanup)
    register_job("daily_memory_review", job_daily_memory_review)
    register_job("rag_rebuild", job_rag_rebuild)

    # Batch 17 — daily admin digest
    try:
        from modules.reports import job_daily_admin_digest
        register_job("daily_admin_digest", job_daily_admin_digest)
    except Exception as e:
        log.warning(f"[scheduler] reports module unavailable: {e}")

    # Batch 18 — daily DB backup
    try:
        from modules.backup import job_daily_db_backup
        register_job("daily_db_backup", job_daily_db_backup)
    except Exception as e:
        log.warning(f"[scheduler] backup module unavailable: {e}")

    _scheduler = AsyncIOScheduler(timezone=_tz())

    def _wrap(name: str, fn: Callable[[], Awaitable[dict]]):
        """Return a proper async callable for APScheduler (lambda returns unawaited coro)."""
        async def _job():
            return await _run_wrapped(name, fn)
        _job.__name__ = f"sched_{name}"
        return _job

    payroll_hour = int(os.getenv("PAYROLL_AUTO_COMPUTE_HOUR", "2"))
    dlq_min = int(os.getenv("DLQ_ALERT_INTERVAL_MIN", "15"))

    _scheduler.add_job(_wrap("daily_payroll_compute", job_daily_payroll),
                       CronTrigger(hour=payroll_hour, minute=0),
                       id="daily_payroll_compute", replace_existing=True)
    _scheduler.add_job(_wrap("dlq_alert", job_dlq_alert),
                       IntervalTrigger(minutes=dlq_min),
                       id="dlq_alert", replace_existing=True)
    _scheduler.add_job(_wrap("health_summary", job_health_summary),
                       IntervalTrigger(hours=6),
                       id="health_summary", replace_existing=True)
    _scheduler.add_job(_wrap("agent_incident_summary", job_agent_incident_summary),
                       IntervalTrigger(hours=6),
                       id="agent_incident_summary", replace_existing=True)
    _scheduler.add_job(_wrap("stale_escort_reminder", job_stale_escort_reminder),
                       CronTrigger(hour=9, minute=0),
                       id="stale_escort_reminder", replace_existing=True)
    _scheduler.add_job(_wrap("payment_reconciliation", job_payment_reconciliation),
                       IntervalTrigger(hours=1),
                       id="payment_reconciliation", replace_existing=True)
    _scheduler.add_job(_wrap("backup_staleness_alert", job_backup_staleness),
                       CronTrigger(hour=3, minute=0),
                       id="backup_staleness_alert", replace_existing=True)
    _scheduler.add_job(_wrap("combined_draft_cleanup", job_combined_draft_cleanup),
                       IntervalTrigger(hours=1),
                       id="combined_draft_cleanup", replace_existing=True)

    # Phase 6 — Continuous Learning Loop
    memory_review_hour = int(os.getenv("MEMORY_REVIEW_HOUR", "9"))
    rag_rebuild_hour   = int(os.getenv("RAG_REBUILD_HOUR", "18"))
    _scheduler.add_job(_wrap("daily_memory_review", job_daily_memory_review),
                       CronTrigger(hour=memory_review_hour, minute=0),
                       id="daily_memory_review", replace_existing=True)
    _scheduler.add_job(_wrap("rag_rebuild", job_rag_rebuild),
                       CronTrigger(hour=rag_rebuild_hour, minute=0),
                       id="rag_rebuild", replace_existing=True)

    # Shared processing-lock cleanup (Phase 1 unification)
    try:
        from shared.locks import cleanup_expired_locks as _cleanup_locks
        async def job_lock_cleanup() -> dict:
            n = await _cleanup_locks()
            return {"status": "ok", "deleted": n}
        register_job("lock_cleanup", job_lock_cleanup)
        _scheduler.add_job(
            _wrap("lock_cleanup", _job_registry["lock_cleanup"]),
            IntervalTrigger(minutes=5),
            id="lock_cleanup", replace_existing=True,
        )
    except Exception as e:
        log.warning(f"[scheduler] lock_cleanup job unavailable: {e}")

    # Draft TTL expiry — mark pending drafts as 'expired' after 24 h (configurable)
    try:
        from shared.draft import expire_stale_drafts as _expire_drafts
        from app.config import get_settings as _get_settings

        async def job_draft_ttl_cleanup() -> dict:
            ttl = _get_settings().draft_ttl_hours
            n = await _expire_drafts(ttl_hours=ttl)
            return {"status": "ok", "expired": n, "ttl_hours": ttl}

        register_job("draft_ttl_cleanup", job_draft_ttl_cleanup)
        _scheduler.add_job(
            _wrap("draft_ttl_cleanup", _job_registry["draft_ttl_cleanup"]),
            IntervalTrigger(minutes=30),
            id="draft_ttl_cleanup", replace_existing=True,
        )
    except Exception as e:
        log.warning(f"[scheduler] draft_ttl_cleanup job unavailable: {e}")

    # Bridge watchdog — alert admin when a bridge has gone silent
    try:
        from shared.queue import get_stale_bridges as _stale_bridges

        async def job_bridge_watchdog() -> dict:
            stale = await _stale_bridges(stale_minutes=10)
            if stale:
                labels = [f"{b['bridge_id']}({b['seconds_ago']}s)" for b in stale]
                log.warning(f"[watchdog] stale bridges: {', '.join(labels)}")
                # Best-effort: try to send an alert via message_router if available
                try:
                    from modules.message_router import get_primary_admin, send_to_admin
                    admin = get_primary_admin()
                    if admin:
                        msg = "Bridge alert - stale:\n" + "\n".join(
                            f"  {b['bridge_id']}: last seen {b['seconds_ago']//60} min ago"
                            for b in stale
                        )
                        await send_to_admin(msg)
                except Exception:
                    pass  # watchdog must not crash if router is unavailable
            return {"status": "ok", "stale_count": len(stale)}

        register_job("bridge_watchdog", job_bridge_watchdog)
        _scheduler.add_job(
            _wrap("bridge_watchdog", _job_registry["bridge_watchdog"]),
            IntervalTrigger(minutes=5),
            id="bridge_watchdog", replace_existing=True,
        )
    except Exception as e:
        log.warning(f"[scheduler] bridge_watchdog job unavailable: {e}")

    if "daily_admin_digest" in _job_registry:
        digest_hour = int(os.getenv("DAILY_DIGEST_HOUR", "8"))
        _scheduler.add_job(
            _wrap("daily_admin_digest", _job_registry["daily_admin_digest"]),
            CronTrigger(hour=digest_hour, minute=0),
            id="daily_admin_digest", replace_existing=True,
        )

    if "daily_db_backup" in _job_registry:
        b_hour = int(os.getenv("DAILY_BACKUP_HOUR", "2"))
        b_min = int(os.getenv("DAILY_BACKUP_MIN", "30"))
        _scheduler.add_job(
            _wrap("daily_db_backup", _job_registry["daily_db_backup"]),
            CronTrigger(hour=b_hour, minute=b_min),
            id="daily_db_backup", replace_existing=True,
        )

    _scheduler.start()
    log.info(f"[scheduler] started tz={_tz()} jobs={len(_scheduler.get_jobs())}")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception as e:
            log.warning(f"[scheduler] shutdown error: {e}")
        _scheduler = None


async def get_status() -> dict:
    rows = await fetch_all(
        "SELECT job_name, last_run_at, last_status, last_duration_ms, last_error, next_run_at, run_count "
        "FROM fazle_scheduled_jobs ORDER BY job_name"
    )
    # Augment with live next_run_time from APScheduler (more accurate than DB snapshot)
    live: dict[str, Optional[datetime]] = {}
    if _scheduler is not None:
        for j in _scheduler.get_jobs():
            live[j.id] = j.next_run_time
    out_rows = []
    for r in rows:
        nx = live.get(r["job_name"]) or r["next_run_at"]
        out_rows.append({
            "job_name": r["job_name"],
            "last_run_at": r["last_run_at"].isoformat() if r["last_run_at"] else None,
            "last_status": r["last_status"],
            "last_duration_ms": r["last_duration_ms"],
            "last_error": r["last_error"],
            "next_run_at": nx.isoformat() if nx else None,
            "run_count": r["run_count"],
        })
    # Also include registered-but-never-run jobs
    seen = {r["job_name"] for r in rows}
    for name in _job_registry.keys():
        if name in seen:
            continue
        nx = live.get(name)
        out_rows.append({
            "job_name": name,
            "last_run_at": None,
            "last_status": None,
            "last_duration_ms": None,
            "last_error": None,
            "next_run_at": nx.isoformat() if nx else None,
            "run_count": 0,
        })
    return {"enabled": _enabled(), "tz": _tz(), "jobs": sorted(out_rows, key=lambda x: x["job_name"])}


def list_job_names() -> list[str]:
    return sorted(_job_registry.keys())
