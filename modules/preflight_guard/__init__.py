from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

# Hard deny-list for protected surfaces.
PROTECTED_PATH_PATTERNS: tuple[str, ...] = (
    "modules/fazle_payroll_engine",
    "modules/payroll",
    "modules/escort_roster",
    "modules/escort_lifecycle",
    "modules/escort",
    "modules/bridge_poller",
    "whatsapp",
    "mcp_bridge",
    "meta_webhook",
    "app/main.py",
)

PROTECTED_TABLES: tuple[str, ...] = (
    "fpe_cash_transactions",
    "fpe_employee_ledger",
    "wbom_employees",
    "wbom_whatsapp_messages",
)

_PREFIX_PROTECTED_TABLES: tuple[str, ...] = (
    "escort_",
    "payroll_",
)

FEATURE_FLAG_CACHE: dict[str, dict[str, Any]] = {}


def _normalize_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip().lower()


def check_protected_paths(paths: list[str]) -> dict[str, Any]:
    """Validate that changed file paths do not touch protected surfaces."""
    blocked: list[str] = []

    for original in paths or []:
        path = _normalize_path(original)
        if not path:
            continue

        for pattern in PROTECTED_PATH_PATTERNS:
            p = pattern.lower()
            if path.startswith(p) or p in path:
                blocked.append(original)
                break

    return {
        "status": "PASS" if not blocked else "FAIL",
        "blocked_paths": blocked,
    }


def _extract_alter_drop_tables(sql_text: str) -> set[str]:
    text = sql_text or ""

    # Match statements that are explicitly ALTER TABLE or DROP TABLE.
    matches = re.findall(
        r"(?is)\b(?:alter\s+table|drop\s+table(?:\s+if\s+exists)?)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        text,
    )
    return {m.lower() for m in matches}


def _is_protected_table(table_name: str) -> bool:
    table = (table_name or "").lower()
    if table in PROTECTED_TABLES:
        return True
    return table.startswith(_PREFIX_PROTECTED_TABLES)


def check_protected_tables(sql_migration: str) -> dict[str, Any]:
    """Block ALTER/DROP statements that target protected table names."""
    touched = _extract_alter_drop_tables(sql_migration)
    blocked = sorted([t for t in touched if _is_protected_table(t)])

    return {
        "status": "PASS" if not blocked else "FAIL",
        "blocked_tables": blocked,
    }


def run_preflight(
    paths: list[str],
    sql_migrations: list[str],
    release_id: str = "unknown-release",
) -> dict[str, Any]:
    """Run path and SQL checks and return a single preflight report."""
    path_result = check_protected_paths(paths)

    blocked_tables: set[str] = set()
    table_status = "PASS"
    for sql in sql_migrations or []:
        result = check_protected_tables(sql)
        if result["status"] == "FAIL":
            table_status = "FAIL"
            blocked_tables.update(result["blocked_tables"])

    overall = "PASS" if path_result["status"] == "PASS" and table_status == "PASS" else "FAIL"

    return {
        "status": overall,
        "path_check": path_result["status"],
        "table_check": table_status,
        "blocked_paths": path_result["blocked_paths"],
        "blocked_tables": sorted(blocked_tables),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "release_id": release_id,
    }


def _normalize_feature_name(feature_name: str) -> str:
    return (feature_name or "").strip().lower()


def _row_to_flag_state(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "feature_name": row.get("feature_name"),
        "is_active": bool(row.get("is_active", False)),
        "kill_switch_active": bool(row.get("kill_switch_active", False)),
        "effective_enabled": bool(row.get("is_active", False)) and not bool(row.get("kill_switch_active", False)),
        "updated_by": row.get("updated_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def load_feature_flags_from_db() -> dict[str, dict[str, Any]]:
    """Load feature flags from DB into in-memory cache during startup."""
    from app.database import fetch_all

    rows = await fetch_all(
        """
        SELECT feature_name, is_active, kill_switch_active, updated_by, created_at, updated_at
        FROM assistant_feature_flags
        ORDER BY feature_name ASC
        """
    )

    FEATURE_FLAG_CACHE.clear()
    for row in rows:
        normalized = _normalize_feature_name(row.get("feature_name", ""))
        if not normalized:
            continue
        FEATURE_FLAG_CACHE[normalized] = _row_to_flag_state(row)

    return FEATURE_FLAG_CACHE.copy()


def get_feature_flags_snapshot() -> list[dict[str, Any]]:
    """Return cached feature flag states."""
    return [FEATURE_FLAG_CACHE[name] for name in sorted(FEATURE_FLAG_CACHE.keys())]


def is_feature_active(feature_name: str, default: bool = False) -> bool:
    """Effective enablement check. Kill-switch always wins over active flag."""
    normalized = _normalize_feature_name(feature_name)
    if not normalized:
        return default
    flag = FEATURE_FLAG_CACHE.get(normalized)
    if not flag:
        return default
    return bool(flag.get("effective_enabled", False))


async def _upsert_feature_flag(
    feature_name: str,
    *,
    is_active: bool,
    kill_switch_active: bool,
    updated_by: str,
) -> dict[str, Any]:
    from app.database import fetch_one

    normalized = _normalize_feature_name(feature_name)
    if not normalized:
        raise ValueError("feature_name is required")

    row = await fetch_one(
        """
        INSERT INTO assistant_feature_flags
          (feature_name, is_active, kill_switch_active, updated_by, updated_at)
        VALUES
          ($1, $2, $3, $4, NOW())
        ON CONFLICT (feature_name)
        DO UPDATE
        SET
          is_active = EXCLUDED.is_active,
          kill_switch_active = EXCLUDED.kill_switch_active,
          updated_by = EXCLUDED.updated_by,
          updated_at = NOW()
        RETURNING feature_name, is_active, kill_switch_active, updated_by, created_at, updated_at
        """,
        normalized,
        is_active,
        kill_switch_active,
        updated_by or "system",
    )

    state = _row_to_flag_state(row or {})
    FEATURE_FLAG_CACHE[normalized] = state
    return state


async def activate_feature(feature_name: str, updated_by: str = "system") -> dict[str, Any]:
    """Activate a feature and clear kill-switch."""
    return await _upsert_feature_flag(
        feature_name,
        is_active=True,
        kill_switch_active=False,
        updated_by=updated_by,
    )


async def kill_switch_feature(feature_name: str, updated_by: str = "system") -> dict[str, Any]:
    """Activate kill-switch and force feature disabled."""
    return await _upsert_feature_flag(
        feature_name,
        is_active=False,
        kill_switch_active=True,
        updated_by=updated_by,
    )


async def list_feature_flags() -> list[dict[str, Any]]:
    """Return latest flags from DB and refresh in-memory cache."""
    await load_feature_flags_from_db()
    return get_feature_flags_snapshot()
