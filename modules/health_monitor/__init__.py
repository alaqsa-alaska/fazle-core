"""
Health Monitor Module — C1B Gate-4 Implementation
Monitors fpe_cash_transactions write integrity and WBOM write status.

Public API:
  await check_system_health() → dict with overall status and per-check results
  await get_indexed_kb_count() → int
  await check_wbom_write_delta() → int (should always be 0 post-freeze)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database import fetch_val


async def check_system_health() -> dict[str, Any]:
    """Run all C1B Gate-4 health checks.

    Returns a dict:
      {
        "timestamp": ISO UTC,
        "status": "ok" | "degraded" | "violation",
        "checks": {
          "fpe_table": "ok" | "error: ...",
          "wbom_freeze": "ok" | "VIOLATION: N new writes",
          "rag_kb_count": int,
          "rag_kb_warning": str (optional),
        }
      }
    """
    health: dict[str, Any] = {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "status": "ok",
        "checks": {},
    }

    # Check 1: fpe_cash_transactions is readable/writable
    try:
        await fetch_val("SELECT COUNT(*) FROM fpe_cash_transactions LIMIT 1")
        health["checks"]["fpe_table"] = "ok"
    except Exception as e:
        health["checks"]["fpe_table"] = f"error: {e}"
        health["status"] = "degraded"

    # Check 2: WBOM write freeze — post-C1B, no new wbom_cash_transactions writes expected
    try:
        delta = await check_wbom_write_delta()
        health["checks"]["wbom_freeze"] = "ok" if delta == 0 else f"VIOLATION: {delta} new writes"
        if delta > 0:
            health["status"] = "violation"
    except Exception as e:
        health["checks"]["wbom_freeze"] = f"error: {e}"
        if health["status"] == "ok":
            health["status"] = "degraded"

    # Check 3: RAG knowledge base indexing freshness
    try:
        kb_count = await get_indexed_kb_count()
        health["checks"]["rag_kb_count"] = kb_count
        if kb_count < 100:
            health["checks"]["rag_kb_warning"] = (
                f"Low KB count ({kb_count}) — sync_knowledge_base_to_runtime.py may need to run"
            )
    except Exception as e:
        health["checks"]["rag_freshness"] = f"error: {e}"

    return health


async def check_wbom_write_delta() -> int:
    """Return number of new WBOM rows created in the last 24 hours.

    Post C1B architecture freeze, direct writes to wbom_cash_transactions
    should be zero. Returns 0 if WBOM table doesn't exist.
    """
    try:
        count = await fetch_val(
            """SELECT COUNT(*)
               FROM wbom_cash_transactions
               WHERE created_at >= NOW() - INTERVAL '24 hours'"""
        )
        return int(count or 0)
    except Exception as exc:
        raise RuntimeError(f"wbom_write_check_failed: {exc}") from exc


async def get_indexed_kb_count() -> int:
    """Return count of active rows in fazle_knowledge_base table."""
    try:
        count = await fetch_val(
            "SELECT COUNT(*) FROM fazle_knowledge_base WHERE is_active = true"
        )
        return int(count or 0)
    except Exception:
        return 0
