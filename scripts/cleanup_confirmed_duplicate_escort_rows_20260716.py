"""
One-time cleanup — Phase 2 follow-up, Session 18 (2026-07-16), see:
  knowledge_base/00_governance/escort_sender_validation_audit_phase2_2026-07-16.md
  knowledge_base/00_governance/management_decisions.md (S18-01)

Deletes 3 rows confirmed as genuine duplicates in the Phase 2 duplicate
review, all explicitly signed off by Owner:
  - 933 (duplicate of 934 -- 933's source was the system's own internal
    admin-review alert text, accidentally backfilled as a real confirmation)
  - 1   (duplicate of 9  -- same 2026-04-18 bulk-seed batch, identical fields)
  - 3   (duplicate of 10 -- same 2026-04-18 bulk-seed batch, identical fields)

Pre-delete verification (see audit doc): zero references in
fpe_cash_transactions, wbom_cash_transactions, wbom_billing_records,
wbom_cases, wbom_whatsapp_messages.related_program_id,
fazle_payment_drafts.escort_program_id, escort_order_lighters for all three
program_ids. escort_employee_id IS NULL on all three. escort_shift_logs has
release-log entries for 1 and 3 (no FK constraint) -- deleted alongside so
no orphaned log rows reference a deleted program_id.

Run once: /home/azim/.venv/bin/python scripts/cleanup_confirmed_duplicate_escort_rows_20260716.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import fetch_all, fetch_one, execute, init_db, close_db
from modules.escort_roster.db import log_audit

# (delete_id, keep_id)
PAIRS = [(933, 934), (1, 9), (3, 10)]
DELETE_IDS = [p[0] for p in PAIRS]


async def main():
    await init_db()

    programs = await fetch_all(
        "SELECT * FROM wbom_escort_programs WHERE program_id = ANY($1::int[]) ORDER BY program_id",
        DELETE_IDS,
    )
    roster_by_pid = {
        r["program_id"]: dict(r)
        for r in await fetch_all(
            "SELECT * FROM escort_roster_entries WHERE program_id = ANY($1::int[])", DELETE_IDS
        )
    }
    shift_logs_by_pid: dict[int, list[dict]] = {}
    for r in await fetch_all(
        "SELECT * FROM escort_shift_logs WHERE program_id = ANY($1::int[])", DELETE_IDS
    ):
        shift_logs_by_pid.setdefault(r["program_id"], []).append(dict(r))

    found_ids = {p["program_id"] for p in programs}
    missing = set(DELETE_IDS) - found_ids
    if missing:
        print(f"WARNING: not found, skipping: {sorted(missing)}")

    print("Deleting (delete_id -> kept_id):")
    for delete_id, keep_id in PAIRS:
        if delete_id not in found_ids:
            continue
        p = dict(next(p for p in programs if p["program_id"] == delete_id))
        print(f"  {delete_id} -> {keep_id}: mv={p['mother_vessel']!r} lv={p['lighter_vessel']!r} "
              f"status={p['status']} shift_logs={len(shift_logs_by_pid.get(delete_id, []))}")

    for p in programs:
        p = dict(p)
        pid = p["program_id"]
        keep_id = next(k for d, k in PAIRS if d == pid)
        merged_old = {
            **p,
            "roster_entry": roster_by_pid.get(pid),
            "shift_logs": shift_logs_by_pid.get(pid, []),
        }
        await log_audit(
            program_id=pid,
            action="manual_duplicate_cleanup",
            old_data=merged_old,
            new_data={"deleted": True, "reason": f"duplicate of program_id={keep_id}, kept as the richer/cleaner record"},
            performed_by="owner_authorized_cleanup_2026-07-16_phase2_followup",
            source="manual_audit",
        )

    await execute("DELETE FROM escort_shift_logs WHERE program_id = ANY($1::int[])", list(found_ids))
    await execute("DELETE FROM escort_roster_entries WHERE program_id = ANY($1::int[])", list(found_ids))
    await execute("DELETE FROM wbom_escort_programs WHERE program_id = ANY($1::int[])", list(found_ids))

    print(f"\nDeleted {len(found_ids)} duplicate program(s) + their roster entries + shift logs.")
    print("Audit trail: escort_roster_audit_logs, action='manual_duplicate_cleanup'")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
