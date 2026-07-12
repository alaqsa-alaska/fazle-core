#!/usr/bin/env python3
"""
One-time backfill: resolve wbom_escort_programs.contact_id from the
client phone stashed in each row's remarks JSON (key "sender_phone"),
for rows created before the 2026-07-12 intake fix that started setting
it consistently at creation time.

Per docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md Decision 6: "Existing rows
stay nullable, backfilled over time — not retroactively enforced." This
is best-effort, not a guarantee of 100% coverage — remarks.sender_phone
is empty on some rows (notably ones created via the admin-direct-confirm
path before contact_id resolution existed there too).

Never overwrites a row that already has a contact_id. Safe to re-run.

Usage:
    python scripts/backfill_escort_program_contact_ids.py [--dry-run]
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db, close_db, fetch_all, execute  # noqa: E402
from modules.escort import _get_contact_id  # noqa: E402


async def main(dry_run: bool) -> None:
    await init_db()
    try:
        # remarks is a free-text column, not always valid JSON (older rows
        # predate the JSON convention) — parse client-side and skip
        # anything malformed rather than letting one bad row's ::jsonb
        # cast abort the whole query.
        raw_rows = await fetch_all(
            "SELECT program_id, status, remarks FROM wbom_escort_programs WHERE contact_id IS NULL AND remarks IS NOT NULL"
        )
        rows = []
        skipped_malformed = 0
        for r in raw_rows:
            try:
                data = json.loads(r["remarks"])
            except (json.JSONDecodeError, TypeError):
                skipped_malformed += 1
                continue
            phone = (data.get("sender_phone") or "").strip() if isinstance(data, dict) else ""
            if phone:
                rows.append({"program_id": r["program_id"], "status": r["status"], "sender_phone": phone})

        print(f"Found {len(rows)} contact_id-less program(s) with a stored sender_phone "
              f"({skipped_malformed} row(s) had non-JSON remarks, skipped).")

        resolved = 0
        unresolved = 0
        by_status: dict[str, int] = {}

        for row in rows:
            contact_id = await _get_contact_id(row["sender_phone"])
            if contact_id:
                resolved += 1
                by_status[row["status"]] = by_status.get(row["status"], 0) + 1
                if not dry_run:
                    await execute(
                        "UPDATE wbom_escort_programs SET contact_id = $2 "
                        "WHERE program_id = $1 AND contact_id IS NULL",
                        row["program_id"],
                        contact_id,
                    )
            else:
                unresolved += 1

        verb = "Would resolve" if dry_run else "Resolved"
        print(f"{verb} {resolved} program(s); {unresolved} sender_phone value(s) had no matching contact.")
        print(f"By status: {by_status}")
    finally:
        await close_db()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes.")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
