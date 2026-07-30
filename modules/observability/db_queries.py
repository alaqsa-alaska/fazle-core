"""
modules.observability.db_queries -- read-only DB-backed queries for
observability data.

Kept separate from modules/observability/__init__.py deliberately: that
module is a purely in-memory Prometheus-style counter/gauge store (no DB
access at all -- confirmed by direct read during Phase 2 planning), so a
DB-backed query does not belong there. This file exists so
modules.ai_console.live_adapters has a properly importable function to
call instead of either duplicating SQL inline in an adapter or importing
from a FastAPI route handler (app/main.py's GET /observability/errors is
a route-internal function, not importable).

get_recent_errors() is the same SELECT already used by that route
(app/main.py:4081-4098) against fazle_error_log -- duplicated here
deliberately rather than having the route import from this module, per
Phase 2 Step 4's scope (no changes to app/main.py in this pass).
"""
from __future__ import annotations

from typing import Any

from app.database import fetch_all


async def get_recent_errors(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent fazle_error_log rows, most recently seen first.

    fazle_error_log is NOT in modules.preflight_guard.PROTECTED_TABLES or
    its _PREFIX_PROTECTED_TABLES prefixes (confirmed by direct read of
    modules/preflight_guard/__init__.py: PROTECTED_TABLES is
    fpe_cash_transactions/fpe_employee_ledger/wbom_employees/
    wbom_whatsapp_messages; _PREFIX_PROTECTED_TABLES is escort_/payroll_)
    -- so this plain read-only SELECT carries no protected-path risk.
    """
    rows = await fetch_all(
        "SELECT module, error_type, message, count, first_seen, last_seen "
        "FROM fazle_error_log ORDER BY last_seen DESC LIMIT $1",
        max(1, min(limit, 500)),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "module": r["module"],
            "error_type": r["error_type"],
            "message": r["message"],
            "count": int(r["count"] or 0),
            "first_seen": r["first_seen"].isoformat() if r["first_seen"] else None,
            "last_seen": r["last_seen"].isoformat() if r["last_seen"] else None,
        })
    return out
