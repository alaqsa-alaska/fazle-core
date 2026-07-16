"""
One-time cleanup — Phase 2, Session 17 (2026-07-16), see:
  knowledge_base/00_governance/escort_sender_validation_audit_phase2_2026-07-16.md
  knowledge_base/00_governance/management_decisions.md (S17-01)

Deletes 10 legacy status='expired' wbom_escort_programs rows from a
*registered* client/admin sender (not the unauthorized-sender class handled
by S16) that were abandoned mid-order pre-2026-06-27 and never confirmed.
Includes S/N 777, whose "Mv:/mobile: label leakage" was confirmed
non-reproducible against the current parser (Phase 2 §2) before being
folded into this batch.

All 10 verified financially inert: escort_employee_id IS NULL,
net_payable = 0.00, zero fpe_cash_transactions references.

Run once: /home/azim/.venv/bin/python scripts/cleanup_legacy_expired_escort_rows_20260716.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import fetch_all, execute, init_db, close_db
from modules.escort_roster.db import log_audit

PROGRAM_IDS = [141, 759, 760, 761, 763, 764, 768, 770, 771, 777]


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
    print(f"{'S/N':>6}  {'sender_phone':<16}  {'status':<10}  {'mother_vessel':<28}  created_at")
    for p in programs:
        p = dict(p)
        try:
            remarks = json.loads(p.get("remarks") or "{}")
        except Exception:
            remarks = {}
        sender = remarks.get("sender_phone", "?")
        print(f"{p['program_id']:>6}  {sender:<16}  {p['status']:<10}  {p['mother_vessel']:<28}  {p['assignment_time']}")

    for p in programs:
        p = dict(p)
        pid = p["program_id"]
        merged_old = {**p, "roster_entry": roster_by_pid.get(pid)}
        await log_audit(
            program_id=pid,
            action="manual_legacy_expired_cleanup",
            old_data=merged_old,
            new_data={"deleted": True, "reason": "legacy pre-2026-06-27 abandoned draft from a registered sender, financially inert"},
            performed_by="owner_authorized_cleanup_2026-07-16_phase2",
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
    print("Audit trail: escort_roster_audit_logs, action='manual_legacy_expired_cleanup'")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
