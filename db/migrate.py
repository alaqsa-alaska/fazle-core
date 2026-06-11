#!/usr/bin/env python3
"""Migration runner. Usage: DATABASE_URL=... python db/migrate.py [--dry-run]"""
import asyncio
import glob
import os
import sys

import asyncpg

DB_URL = os.environ["DATABASE_URL"]
MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")
DRY_RUN = "--dry-run" in sys.argv


async def run():
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name       TEXT        PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )""")
        applied = {r["name"] for r in
                   await conn.fetch("SELECT name FROM schema_migrations")}
        migration_files = sorted(glob.glob(f"{MIGRATIONS_DIR}/*.sql"))
        if not migration_files:
            print("No migration files found in", MIGRATIONS_DIR)
            return
        for path in migration_files:
            name = os.path.basename(path)
            if name in applied:
                print(f"  skip    {name}")
                continue
            if DRY_RUN:
                print(f"  pending {name}  [dry-run, not applied]")
                continue
            sql = open(path, encoding="utf-8").read()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations(name) VALUES($1)", name)
            print(f"  applied {name}")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
