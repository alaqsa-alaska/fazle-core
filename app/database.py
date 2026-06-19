"""
Fazle Core — Database
Reuses existing ai-postgres container.
All operations on the existing `postgres` database (wbom_* tables).
"""
import logging
from contextlib import asynccontextmanager
from typing import Optional, Any
import asyncpg
from app.config import get_settings

log = logging.getLogger("fazle.db")
_pool: Optional[asyncpg.Pool] = None


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
    async with get_pool().acquire() as conn:
        yield conn


async def fetch_one(sql: str, *args, conn: Optional[asyncpg.Connection] = None) -> Optional[dict]:
    if conn is not None:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None
    async with db_conn() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def fetch_all(sql: str, *args, conn: Optional[asyncpg.Connection] = None) -> list[dict]:
    if conn is not None:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]
    async with db_conn() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def execute(sql: str, *args, conn: Optional[asyncpg.Connection] = None) -> str:
    if conn is not None:
        return await conn.execute(sql, *args)
    async with db_conn() as conn:
        return await conn.execute(sql, *args)


async def fetch_val(sql: str, *args, conn: Optional[asyncpg.Connection] = None) -> Any:
    if conn is not None:
        return await conn.fetchval(sql, *args)
    async with db_conn() as conn:
        return await conn.fetchval(sql, *args)
