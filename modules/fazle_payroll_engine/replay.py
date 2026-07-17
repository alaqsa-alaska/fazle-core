"""
Bridge1 DM Historical Replay
=============================
Replays bridge1 messages stored in wbom_whatsapp_messages (via bridge_poller)
that have never been ingested into fpe_wa_messages.

Usage:
    cd /home/azim/core
    python -m modules.fazle_payroll_engine.replay [--since YYYY-MM-DD] [--dry-run]

The script:
1. Reads wbom_whatsapp_messages WHERE platform='bridge1'
   AND (optionally) timestamp >= since
2. For each message, calls ingest_message() — idempotent, safe to re-run
3. Prints a summary at the end

Default --since: 2026-05-07 (last known good bridge1 FPE checkpoint)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# Ensure parent package is importable when run directly
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))
# Also load runtime-services.env which contains the resolved DATABASE_URL
_RUNTIME_ENV = os.path.join(os.path.expanduser("~"), "secure-env-backup", "runtime-services.env")
if os.path.exists(_RUNTIME_ENV):
    load_dotenv(_RUNTIME_ENV, override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("fpe.replay")


async def _run(since: Optional[str], dry_run: bool) -> None:
    from app.database import init_db, close_db, fetch_all as _fetch
    from modules.fazle_payroll_engine.ingestion import ingest_message
    from modules.fazle_payroll_engine.models import IngestionRequest

    await init_db()
    try:
        await _do_replay(since, dry_run, _fetch, ingest_message, IngestionRequest)
    finally:
        await close_db()


async def _do_replay(since, dry_run, _fetch, ingest_message, IngestionRequest) -> None:
    from app.config import get_settings
    _admin_bridge1_number = get_settings().admin_bridge1_number

    since_dt = datetime.fromisoformat(since) if since else datetime(2026, 5, 7, tzinfo=timezone.utc)

    log.info("Replay bridge1 DMs since=%s  dry_run=%s", since_dt.isoformat(), dry_run)

    # wbom_whatsapp_messages schema: message_id (PK), sender_number, message_body,
    # direction, platform, received_at — no raw WA msg ID, no chat_jid stored.
    # We derive: wa_message_id = 'wbom_' + message_id (stable, avoids collision
    # with historical-sync which uses raw SQLite IDs).
    rows = await _fetch(
        """
        SELECT
            message_id,
            sender_number,
            message_body,
            direction,
            received_at
        FROM wbom_whatsapp_messages
        WHERE platform = 'bridge1'
          AND direction = 'inbound'
          AND received_at >= $1
          AND message_body IS NOT NULL
          AND trim(message_body) != ''
        ORDER BY received_at ASC
        """,
        since_dt,
    )

    log.info("Found %d bridge1 inbound messages to replay", len(rows))

    ingested = 0
    skipped = 0
    errors = 0

    for row in rows:
        db_id = row["message_id"]
        wa_msg_id = f"wbom_{db_id}"
        sender = row.get("sender_number") or ""
        content = row.get("message_body") or ""
        ts = row["received_at"]
        # Build JID from phone (bridge_poller strips @s.whatsapp.net on ingest)
        chat_jid = f"{sender}@s.whatsapp.net" if sender and not sender.startswith("unresolved:") else ""

        if not content.strip():
            skipped += 1
            continue

        if dry_run:
            log.info("[DRY] would ingest db_id=%d sender=%s jid=%s body=%r",
                     db_id, sender, chat_jid, content[:60])
            ingested += 1
            continue

        try:
            req = IngestionRequest(
                wa_message_id=wa_msg_id,
                source="bridge1",
                source_number=_admin_bridge1_number,
                chat_jid=chat_jid,
                sender_phone=sender if sender else None,
                is_from_me=False,
                raw_content=content,
                media_type=None,
                timestamp_wa=ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts)),
            )
            fpe_id = await ingest_message(req)
            if fpe_id is None:
                skipped += 1  # already existed
            else:
                ingested += 1
                log.debug("ingested db_id=%d → fpe_id=%d", db_id, fpe_id)
        except Exception as exc:
            log.error("Error ingesting db_id=%d: %s", db_id, exc)
            errors += 1

    log.info(
        "Replay done: ingested=%d  already_existed(skip)=%d  errors=%d",
        ingested, skipped, errors,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay bridge1 DMs into FPE")
    parser.add_argument(
        "--since",
        default="2026-05-07",
        help="ISO date (YYYY-MM-DD) to replay from (default: 2026-05-07)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be ingested without writing",
    )
    args = parser.parse_args()

    asyncio.run(_run(args.since, args.dry_run))


if __name__ == "__main__":
    main()
