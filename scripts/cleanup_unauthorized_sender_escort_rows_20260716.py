"""
One-time cleanup — Session 16 (2026-07-16), see:
  knowledge_base/00_governance/escort_sender_validation_audit_2026-07-16.md
  knowledge_base/00_governance/management_decisions.md (S16-01)

Deletes 18 wbom_escort_programs rows created by senders that are neither a
registered admin number nor a registered escort-client number. All 18
predate the 2026-06-27 sender-validation gate (commit 41916b0) and are
verified financially inert (no escort_employee_id, zero
fpe_cash_transactions references) — see the audit doc for the full
verification query.

Explicitly NOT touched: program_id 940 (is_historical=true) and the 5
known-sender expired drafts, per Owner's constraint.

Run once: /home/azim/.venv/bin/python scripts/cleanup_unauthorized_sender_escort_rows_20260716.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import fetch_all, execute, init_db, close_db
from modules.escort_roster.db import log_audit

PROGRAM_IDS = [
    155, 213, 214, 219, 229, 230, 765, 769, 774, 779,
    808, 840, 851, 860, 869, 871, 873, 880,
]


async def main():
    await init_db()

    programs = await fetch_all(
        "SELECT * FROM wbom_escort_programs WHERE program_id = ANY($1::int[]) ORDER BY program_id",
        PROGRAM_IDS,
    )
    roster_entries = await fetch_all(
        "SELECT * FROM escort_roster_entries WHERE program_id = ANY($1::int[]) ORDER BY program_id",
        PROGRAM_IDS,
    )
    roster_by_pid = {r["program_id"]: dict(r) for r in roster_entries}

    found_ids = {p["program_id"] for p in programs}
    missing = set(PROGRAM_IDS) - found_ids
    if missing:
        print(f"WARNING: {len(missing)} program_id(s) not found, skipping: {sorted(missing)}")

    print(f"{len(programs)} rows to delete:")
    print(f"{'S/N':>6}  {'sender_phone':<16}  {'status':<10}  created_at")
    for p in programs:
        p = dict(p)
        import json as _json
        try:
            remarks = _json.loads(p.get("remarks") or "{}")
        except Exception:
            remarks = {}
        sender = remarks.get("sender_phone", "?")
        print(f"{p['program_id']:>6}  {sender:<16}  {p['status']:<10}  {p['assignment_time']}")

    for p in programs:
        p = dict(p)
        pid = p["program_id"]
        merged_old = {**p, "roster_entry": roster_by_pid.get(pid)}
        await log_audit(
            program_id=pid,
            action="manual_unauthorized_sender_cleanup",
            old_data=merged_old,
            new_data={"deleted": True, "reason": "unauthorized sender, predates 2026-06-27 gate, financially inert"},
            performed_by="owner_authorized_cleanup_2026-07-16",
            source="manual_audit",
        )

    await execute(
        "DELETE FROM escort_roster_entries WHERE program_id = ANY($1::int[])",
        list(found_ids),
    )
    await execute(
        "DELETE FROM wbom_escort_programs WHERE program_id = ANY($1::int[])",
        list(found_ids),
    )

    print(f"\nDeleted {len(found_ids)} program(s) from wbom_escort_programs + escort_roster_entries.")
    print("Audit trail: escort_roster_audit_logs, action='manual_unauthorized_sender_cleanup'")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
