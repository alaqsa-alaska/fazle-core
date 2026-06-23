#!/usr/bin/env python3
"""Standalone Qdrant + BM25 index rebuild script.

Run from /home/azim/core:
    python scripts/rebuild_qdrant.py

Reads resources/*.txt + fazle_knowledge_base DB rows, rebuilds BM25 in-memory
index and upserts all safe chunks into Qdrant server (fazle_rag_chunks collection).
"""
import asyncio
import logging
import os
import sys

# ── Project root on path ────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ── Load .env ───────────────────────────────────────────────────────────────────
from pathlib import Path

env_path = Path(ROOT) / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

# ── Override with resolved runtime DB URL ───────────────────────────────────────
RUNTIME_ENV = Path("/home/azim/secure-env-backup/runtime-services.env")
if RUNTIME_ENV.exists():
    for line in RUNTIME_ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ[k.strip()] = v.strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("rebuild_qdrant")


async def main() -> None:
    from app.database import init_db, close_db
    from modules.rag import build_index

    log.info("Connecting to PostgreSQL …")
    await init_db()

    log.info("Starting RAG index rebuild (BM25 + Qdrant) …")
    result = await build_index()

    log.info("Build complete:")
    log.info("  docs      = %s", result.get("docs"))
    log.info("  vocab     = %s", result.get("vocab"))
    log.info("  build_ms  = %s", result.get("build_ms"))
    log.info("  qdrant    = %s", result.get("qdrant_points"))

    await close_db()
    log.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
