"""
Fazle Core — Database
Reuses existing ai-postgres container.
All operations on the existing `postgres` database (wbom_* tables).
"""
import logging
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Optional, Any
import asyncpg
from app.config import get_settings

log = logging.getLogger("fazle.db")
_pool: Optional[asyncpg.Pool] = None

# ── Shadow-mode write guard (Phase B Stage 4, Section 2) ────────────────────
# Hard runtime assertion, not just an architectural convention: while
# shadow_mode_scope() is active, any write statement (INSERT/UPDATE/DELETE/
# TRUNCATE/ALTER/DROP/CREATE) targeting a table outside the conversation-
# memory engine's own tables raises immediately, instead of silently
# succeeding. Applied at this module's single choke point (every SQL
# statement in the app goes through fetch_one/fetch_all/execute/fetch_val)
# so it can't be bypassed by a future caller forgetting to check a flag.
_shadow_mode_active: ContextVar[bool] = ContextVar("_shadow_mode_active", default=False)

SHADOW_MODE_ALLOWED_WRITE_TABLES = frozenset({
    "fazle_conversation_threads",
    "processed_conversation_messages",
    "fazle_conversation_shadow_comparisons",
})

_WRITE_STMT_RE = re.compile(
    r"^\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?|ALTER\s+TABLE|DROP\s+TABLE|CREATE\s+TABLE)"
    r"\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?\"?([A-Za-z_][A-Za-z0-9_]*)\"?",
    re.IGNORECASE,
)


class ShadowModeWriteViolation(RuntimeError):
    """Raised when code running inside shadow_mode_scope() attempts to
    write to a table outside SHADOW_MODE_ALLOWED_WRITE_TABLES. Caught by
    the same broad exception handler every shadow-mode call site already
    wraps itself in (Part 13's fail-open contract) -- this is a hard
    safety net, not a new failure mode callers need to handle specially."""


def _check_shadow_write_guard(sql: str) -> None:
    if not _shadow_mode_active.get():
        return
    match = _WRITE_STMT_RE.match(sql)
    if not match:
        return
    table = match.group(1).lower()
    if table not in SHADOW_MODE_ALLOWED_WRITE_TABLES:
        raise ShadowModeWriteViolation(
            f"shadow-mode code attempted to write to business table {table!r} "
            f"(sql: {sql[:160]!r})"
        )


@asynccontextmanager
async def shadow_mode_scope():
    """Wrap any observational/shadow code path in this. Every fetch_one/
    fetch_all/execute/fetch_val call made anywhere in the call tree while
    this is active is checked against SHADOW_MODE_ALLOWED_WRITE_TABLES."""
    token = _shadow_mode_active.set(True)
    try:
        yield
    finally:
        _shadow_mode_active.reset(token)


async def init_db():
    global _pool
    settings = get_settings()
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    log.info("Database pool ready")


async def close_db():
    global _pool
    if _pool:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


@asynccontextmanager
async def db_conn():
    if _pool is not None:
        async with _pool.acquire() as conn:
            yield conn
        return

    settings = get_settings()
    conn = await asyncpg.connect(settings.database_url)
    try:
        yield conn
    finally:
        await conn.close()


async def fetch_one(sql: str, *args, conn: Optional[asyncpg.Connection] = None) -> Optional[dict]:
    _check_shadow_write_guard(sql)
    if conn is not None:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None
    async with db_conn() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def fetch_all(sql: str, *args, conn: Optional[asyncpg.Connection] = None) -> list[dict]:
    _check_shadow_write_guard(sql)
    if conn is not None:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]
    async with db_conn() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def execute(sql: str, *args, conn: Optional[asyncpg.Connection] = None) -> str:
    _check_shadow_write_guard(sql)
    if conn is not None:
        return await conn.execute(sql, *args)
    async with db_conn() as conn:
        return await conn.execute(sql, *args)


async def fetch_val(sql: str, *args, conn: Optional[asyncpg.Connection] = None) -> Any:
    _check_shadow_write_guard(sql)
    if conn is not None:
        return await conn.fetchval(sql, *args)
    async with db_conn() as conn:
        return await conn.fetchval(sql, *args)
