#!/usr/bin/env python3
"""
Fazle Core — Ollama Memory DB Setup (Phase 5)
=============================================
Run once as superuser to create:
  • Database:  fazle_ollama_memory
  • Role:      ollama_memory_owner  (full DDL/DML on memory DB only)
  • Role:      fazle_ai_reader      (SELECT on approved views in production DB)
  • Tables:    ai_memory_facts, ai_memory_questions, ai_memory_tasks,
               ai_kb_embeddings_manifest

Safety:
  • NEVER touches production DB tables or data
  • NEVER grants write access to the production `postgres` database
  • Passwords are auto-generated and written to .env if not already set

Usage:
    python3 scripts/setup_ollama_memory_db.py
    python3 scripts/setup_ollama_memory_db.py --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import os
import secrets
import string
import sys
from pathlib import Path

import asyncpg


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"

PROD_DB_URL_KEY = "DATABASE_URL"
MEMORY_DB_URL_KEY = "OLLAMA_MEMORY_DB_URL"
AI_READER_URL_KEY = "FAZLE_AI_READER_DB_URL"
MEMORY_OWNER_PW_KEY = "OLLAMA_MEMORY_OWNER_PASSWORD"
AI_READER_PW_KEY = "FAZLE_AI_READER_PASSWORD"


def _gen_password(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _read_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    env.update(os.environ)
    return env


def _write_env_key(key: str, value: str) -> None:
    """Append or update a key=value line in .env (never commits secrets)."""
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    print(f"  [env] {key} written to .env")


def _superuser_url(env: dict) -> str:
    """Return superuser connection URL from env."""
    url = env.get(PROD_DB_URL_KEY)
    if url:
        return url
    # Try secure-env-backup
    backup = ROOT / "secure-env-backup" / "runtime-services.env"
    if backup.exists():
        for line in backup.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.partition("=")[2].strip()
    sys.exit("ERROR: DATABASE_URL not found in .env or secure-env-backup/runtime-services.env")


def _host_from_url(url: str) -> str:
    """Extract host from postgres URL."""
    # postgresql://user:pass@host:port/db
    parts = url.split("@")
    if len(parts) >= 2:
        host_port = parts[1].split("/")[0]
        return host_port.split(":")[0]
    return "localhost"


def _port_from_url(url: str) -> int:
    parts = url.split("@")
    if len(parts) >= 2:
        host_port = parts[1].split("/")[0]
        if ":" in host_port:
            return int(host_port.split(":")[1])
    return 5432


async def _run(dry_run: bool) -> None:
    env = _read_env()
    prod_url = _superuser_url(env)
    host = _host_from_url(prod_url)
    port = _port_from_url(prod_url)

    # Resolve or generate passwords
    mem_pw = env.get(MEMORY_OWNER_PW_KEY) or _gen_password()
    reader_pw = env.get(AI_READER_PW_KEY) or _gen_password()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Fazle Core — Ollama Memory DB Setup")
    print(f"  Production DB host: {host}:{port}")

    if dry_run:
        print("\n  Would create:")
        print("    DATABASE   fazle_ollama_memory")
        print("    ROLE       ollama_memory_owner  (full DDL/DML on memory DB)")
        print("    ROLE       fazle_ai_reader      (SELECT on approved views only)")
        print("    TABLES     ai_memory_facts, ai_memory_questions,")
        print("               ai_memory_tasks, ai_kb_embeddings_manifest")
        print("  No changes made (--dry-run).")
        return

    # ── Step 1: Connect to production DB as superuser ─────────────────────────
    print("\nConnecting to production DB as superuser...")
    conn = await asyncpg.connect(prod_url)
    try:
        # ── Step 2: Create roles ───────────────────────────────────────────────
        print("Creating roles...")
        # ollama_memory_owner
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname='ollama_memory_owner'"
        )
        if not exists:
            await conn.execute(
                f"CREATE ROLE ollama_memory_owner WITH LOGIN PASSWORD '{mem_pw}'"
            )
            print("  [ok] ROLE ollama_memory_owner created")
        else:
            await conn.execute(
                f"ALTER ROLE ollama_memory_owner WITH PASSWORD '{mem_pw}'"
            )
            print("  [ok] ROLE ollama_memory_owner password updated")

        # fazle_ai_reader
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname='fazle_ai_reader'"
        )
        if not exists:
            await conn.execute(
                f"CREATE ROLE fazle_ai_reader WITH LOGIN PASSWORD '{reader_pw}'"
            )
            print("  [ok] ROLE fazle_ai_reader created")
        else:
            await conn.execute(
                f"ALTER ROLE fazle_ai_reader WITH PASSWORD '{reader_pw}'"
            )
            print("  [ok] ROLE fazle_ai_reader password updated")

        # ── Step 3: Create fazle_ollama_memory database ───────────────────────
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname='fazle_ollama_memory'"
        )
        if not exists:
            # Must be outside a transaction
            await conn.execute(
                "CREATE DATABASE fazle_ollama_memory OWNER ollama_memory_owner"
            )
            print("  [ok] DATABASE fazle_ollama_memory created")
        else:
            print("  [skip] DATABASE fazle_ollama_memory already exists")

    finally:
        await conn.close()

    # ── Step 4: Connect to fazle_ollama_memory as superuser and create tables ──
    # Replace only the database name (last segment after @...port/)
    import re as _re
    mem_url = _re.sub(r"/[^/]+$", "/fazle_ollama_memory", prod_url)
    print("\nCreating memory tables in fazle_ollama_memory...")
    mem_conn = await asyncpg.connect(mem_url)
    try:
        await mem_conn.execute("""
CREATE TABLE IF NOT EXISTS ai_memory_facts (
    id              BIGSERIAL PRIMARY KEY,
    subject_type    TEXT NOT NULL,
    subject_key     TEXT NOT NULL,
    fact_type       TEXT NOT NULL,
    fact_text       TEXT NOT NULL,
    confidence      NUMERIC(4,2) DEFAULT 0.70,
    source_ref      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_facts_subject
    ON ai_memory_facts (subject_type, subject_key);
""")
        await mem_conn.execute("""
CREATE TABLE IF NOT EXISTS ai_memory_questions (
    id                  BIGSERIAL PRIMARY KEY,
    question            TEXT NOT NULL,
    normalized_question TEXT,
    answer_summary      TEXT,
    source_refs         JSONB DEFAULT '[]',
    asked_at            TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ai_questions_asked
    ON ai_memory_questions (asked_at DESC);
""")
        await mem_conn.execute("""
CREATE TABLE IF NOT EXISTS ai_memory_tasks (
    id          BIGSERIAL PRIMARY KEY,
    task_name   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',
    notes       TEXT,
    source_refs JSONB DEFAULT '[]',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
""")
        await mem_conn.execute("""
CREATE TABLE IF NOT EXISTS ai_kb_embeddings_manifest (
    id          BIGSERIAL PRIMARY KEY,
    kb_path     TEXT NOT NULL UNIQUE,
    kb_hash     TEXT NOT NULL,
    indexed_at  TIMESTAMPTZ DEFAULT NOW(),
    chunk_count INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'indexed'
);
""")
        # Grant ownership to ollama_memory_owner on all tables
        await mem_conn.execute(
            "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ollama_memory_owner"
        )
        await mem_conn.execute(
            "GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ollama_memory_owner"
        )
        print("  [ok] Tables created and privileges granted")
    finally:
        await mem_conn.close()

    # ── Step 5: Write env vars ────────────────────────────────────────────────
    # Build URLs by replacing only the user:pass part and db name
    _user_pass = prod_url.split("@")[0].split("//")[1]   # e.g. postgres:pass
    _host_part = prod_url.split("@")[1]                  # e.g. 172.20.0.6:5432/postgres
    _host_no_db = _host_part.rsplit("/", 1)[0]           # e.g. 172.20.0.6:5432

    mem_db_url = f"postgresql://ollama_memory_owner:{mem_pw}@{_host_no_db}/fazle_ollama_memory"
    reader_db_url = f"postgresql://fazle_ai_reader:{reader_pw}@{_host_no_db}/postgres"

    print("\nWriting credentials to .env...")
    if not env.get(MEMORY_OWNER_PW_KEY):
        _write_env_key(MEMORY_OWNER_PW_KEY, mem_pw)
    if not env.get(AI_READER_PW_KEY):
        _write_env_key(AI_READER_PW_KEY, reader_pw)
    if not env.get(MEMORY_DB_URL_KEY):
        _write_env_key(MEMORY_DB_URL_KEY, mem_db_url)
    if not env.get(AI_READER_URL_KEY):
        _write_env_key(AI_READER_URL_KEY, reader_db_url)

    print("\n✓ Setup complete.")
    print(f"  Memory DB URL key: {MEMORY_DB_URL_KEY}")
    print(f"  AI Reader URL key: {AI_READER_URL_KEY}")
    print("\nNext step: run migrations/012_ai_readonly_views.sql to create production views.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ollama Memory DB setup")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(dry_run=args.dry_run))
