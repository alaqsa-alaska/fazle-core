"""
Import a Google-Contacts-style CSV export into the canonical wbom_contacts
table, reusing modules.contact_sync.import_from_csv_contacts() (insert-only,
never overwrites an existing contact). See
knowledge_base/00_governance/management_decisions.md ("CSV Contact-Book
Rebuild — Owner Decision, 2026-08-04") for the full decision record.

Two modes:
  1. One-time rebuild (--clear-first): clears wbom_contacts +
     fazle_unified_contacts first, EXCLUDING is_protected=true rows and any
     row still FK-referenced by wbom_escort_programs (both preserved,
     per Owner instruction), then imports every valid BD number from the CSV.
  2. Future incremental (no flag, the default and the ongoing workflow):
     imports only phone numbers genuinely not already in wbom_contacts —
     never touches, updates, or deletes an existing contact.

Run:
    cd /home/azim/core && python3 scripts/import_csv_contacts.py <csv_path> [--clear-first] [--dry-run]

--dry-run: parse, normalize, and report counts only — no DB writes at all
(neither clear nor import). Always run this first.
"""

import argparse
import asyncio
import csv
import os
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_ENV_LAYERS = [
    (Path(ROOT) / ".env", os.environ.setdefault),
    (Path("/home/azim/secure-env-backup/runtime-services.env"), os.environ.__setitem__),
]
for env_path, setter in _ENV_LAYERS:
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            setter(k.strip(), v.strip())


def _build_display_name(row: dict) -> str:
    parts = [row.get("First Name", ""), row.get("Middle Name", ""), row.get("Last Name", "")]
    name = " ".join(p.strip() for p in parts if p and p.strip())
    if name:
        return name
    for fallback_key in ("File As", "Organization Name", "Nickname"):
        v = (row.get(fallback_key) or "").strip()
        if v:
            return v
    return ""


def parse_csv(csv_path: str, normalize_phone) -> dict:
    """Returns {"rows": [{"phone", "display_name"}...], "stats": {...}}.
    De-dupes on normalized phone within the CSV itself (first occurrence's
    name wins), splits ' ::: '-separated multi-number cells into separate
    contact rows (same name, each number is its own wbom_contacts row —
    matches the schema's per-number unique constraint)."""
    seen_phones = {}
    stats = {
        "csv_rows_read": 0,
        "rows_with_no_phone": 0,
        "phone_values_seen": 0,
        "invalid_or_non_bd_numbers": 0,
        "duplicate_numbers_in_csv": 0,
        "unique_valid_contacts": 0,
    }
    invalid_samples = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            stats["csv_rows_read"] += 1
            raw_phone_cell = (row.get("Phone 1 - Value") or "").strip()
            if not raw_phone_cell:
                stats["rows_with_no_phone"] += 1
                continue
            name = _build_display_name(row)
            for raw_number in raw_phone_cell.split(":::"):
                raw_number = raw_number.strip()
                if not raw_number:
                    continue
                stats["phone_values_seen"] += 1
                phone = normalize_phone(raw_number)
                if not phone:
                    stats["invalid_or_non_bd_numbers"] += 1
                    if len(invalid_samples) < 15:
                        invalid_samples.append(raw_number)
                    continue
                if phone in seen_phones:
                    stats["duplicate_numbers_in_csv"] += 1
                    continue
                seen_phones[phone] = name

    stats["unique_valid_contacts"] = len(seen_phones)
    rows = [{"phone": p, "display_name": n} for p, n in seen_phones.items()]
    return {"rows": rows, "stats": stats, "invalid_samples": invalid_samples}


async def clear_contact_book():
    """Clears wbom_contacts + fazle_unified_contacts, EXCLUDING is_protected=true
    rows and any row still FK-referenced by wbom_escort_programs. Neither table
    itself is dropped. Returns counts."""
    from app.database import execute, fetch_val

    protected_or_linked_count = await fetch_val(
        """
        SELECT COUNT(*) FROM wbom_contacts
         WHERE is_protected = true
            OR contact_id IN (SELECT DISTINCT contact_id FROM wbom_escort_programs WHERE contact_id IS NOT NULL)
        """
    )
    total_before = await fetch_val("SELECT COUNT(*) FROM wbom_contacts")

    deleted = await execute(
        """
        DELETE FROM wbom_contacts
         WHERE is_protected = false
           AND contact_id NOT IN (SELECT DISTINCT contact_id FROM wbom_escort_programs WHERE contact_id IS NOT NULL)
        """
    )
    # fazle_unified_contacts has no is_protected/escort-link concept of its own
    # (it's phone-keyed, not contact_id-keyed) — clear entries whose phone is
    # NOT one of the preserved wbom_contacts phones, so the two tables stay
    # consistent (an alias table entry for a deleted contact is meaningless).
    preserved_phones_deleted = await execute(
        """
        DELETE FROM fazle_unified_contacts
         WHERE phone NOT IN (SELECT whatsapp_number FROM wbom_contacts)
        """
    )

    total_after = await fetch_val("SELECT COUNT(*) FROM wbom_contacts")
    return {
        "total_before": total_before,
        "preserved_protected_or_escort_linked": protected_or_linked_count,
        "total_after_clear": total_after,
        "wbom_contacts_delete_result": deleted,
        "fazle_unified_contacts_delete_result": preserved_phones_deleted,
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--clear-first", action="store_true",
                         help="One-time rebuild: clear existing non-protected, non-escort-linked contacts first")
    parser.add_argument("--dry-run", action="store_true",
                         help="Parse and report only, no DB writes")
    args = parser.parse_args()

    from modules.phone_normalizer import normalize_phone

    parsed = parse_csv(args.csv_path, normalize_phone)
    print("=== CSV parse stats ===")
    for k, v in parsed["stats"].items():
        print(f"  {k}: {v}")
    if parsed["invalid_samples"]:
        print(f"  sample invalid/non-BD numbers (first {len(parsed['invalid_samples'])}): {parsed['invalid_samples']}")

    if args.dry_run:
        print("\n--dry-run: no DB writes performed.")
        return

    from app.database import init_db, close_db
    await init_db()
    try:
        if args.clear_first:
            print("\n=== Clearing existing contact-book data (preserving protected + escort-linked) ===")
            clear_result = await clear_contact_book()
            for k, v in clear_result.items():
                print(f"  {k}: {v}")

        print("\n=== Importing CSV contacts (insert-only, never overwrites existing) ===")
        from modules.contact_sync import import_from_csv_contacts
        import_result = await import_from_csv_contacts(parsed["rows"])
        for k, v in import_result.items():
            print(f"  {k}: {v}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
