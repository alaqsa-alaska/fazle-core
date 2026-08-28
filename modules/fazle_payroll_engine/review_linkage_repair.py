"""Stage H.5 (2026-08-29) -- deterministic repair of the review-row link
that Stage H found broken for every one of the 27 pending
fpe_cash_transactions (0/27 joined to a live fpe_unmatched_messages row).

Root cause (H5.1/H5.3, exhaustive read-only search -- confirmed, not
guessed): the operator-submission INSERT path (modules.fazle_payroll_
engine.routes, the "operator/submit" handler) creates a
fpe_unmatched_messages row FIRST, then a linked pending
fpe_cash_transactions row via payment_event_from_operator(pending_id=
new_id), then UPDATEs the fpe_unmatched_messages row's promoted_txn_id
back to the new transaction -- fully bidirectional, atomic-in-effect
linkage at creation time. No DELETE/TRUNCATE against
fpe_unmatched_messages exists anywhere in this repo's modules, scripts,
migrations, or the host crontab (all three searched) -- the 27 rows'
disappearance is data loss from an event outside this codebase (a
manual out-of-band operation, most likely), not a design defect. The
DESIGN itself is sound: Option A (preserve durable review linkage) is
correct, and this module is the narrow repair for the linkage that
design already establishes, not a new competing mechanism.

Recovery is deterministic, never invented (H5.5's explicit rule):
fpe_cash_transactions.source_message_id IS the original
fpe_unmatched_messages.id (verified: it is set to str(pending_id) at
creation, see payment_event.payment_event_from_operator). The original
fpe_wa_messages row (fpe_cash_transactions.fpe_wa_message_id) is
UNCHANGED and still holds the real raw_content that was actually
submitted -- reconstruction uses ONLY: (a) that unmodified raw_content,
and (b) the transaction's own already-recorded detected_amount/
payout_method/txn_date/employee linkage (which were themselves derived
FROM the now-missing row at creation time -- reading them back is not
inventing new data, it is reading the same values forward). Nothing is
guessed.

Test-artifact exclusion (a real finding, H5.1): 4 of the 27 pending
transactions (ids 2548/2551/2553/2555, all amount=1.00, all from
2026-07-02) have raw_content literally reading "REGTEST_... operator
write-path test" / "REGTEST_probe_operator" -- these are regression-
test artifacts that reached production, not real business transactions.
This module never backfills a review row for them; they are reported
separately (TEST_ARTIFACT_SUSPECT) for an Owner decision, never
silently approved or silently deleted.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.database import fetch_all, fetch_one, execute

log = logging.getLogger("fazle.fpe.review_linkage_repair")

# Matches the exact test-artifact markers found live in H5.1 -- narrow and
# explicit, never a broad heuristic that could misclassify a real
# submission whose text happens to contain "test".
_TEST_ARTIFACT_RE = re.compile(r"REGTEST_|operator write-path test|REGTEST_probe", re.IGNORECASE)


async def find_orphaned_operator_transactions() -> list[dict]:
    """READ-only. Every pending, source='operator' fpe_cash_transactions
    row whose source_message_id no longer resolves to a live
    fpe_unmatched_messages row, joined with its still-existing
    fpe_wa_messages evidence (if any)."""
    rows = await fetch_all(
        """
        SELECT t.id AS txn_id, t.txn_ref, t.source_message_id, t.employee_id,
               t.employee_name_raw, t.amount, t.payout_method, t.payout_phone,
               t.txn_category, t.txn_date, t.accounting_period,
               t.fpe_wa_message_id, w.raw_content, w.timestamp_wa,
               u.id AS existing_review_id
        FROM fpe_cash_transactions t
        LEFT JOIN fpe_wa_messages w ON w.id = t.fpe_wa_message_id
        LEFT JOIN fpe_unmatched_messages u ON u.id = t.source_message_id::bigint
        WHERE t.transaction_status = 'pending'
          AND t.source = 'operator'
          AND t.source_message_id IS NOT NULL
        ORDER BY t.created_at ASC
        """
    )
    return [dict(r) for r in rows if r["existing_review_id"] is None]


def classify_orphan(row: dict) -> str:
    """One of TEST_ARTIFACT_SUSPECT / RECOVERABLE / INSUFFICIENT_EVIDENCE.
    Pure function, no I/O -- directly testable."""
    raw = row.get("raw_content") or ""
    if _TEST_ARTIFACT_RE.search(raw):
        return "TEST_ARTIFACT_SUSPECT"
    if not row.get("fpe_wa_message_id") or not raw:
        return "INSUFFICIENT_EVIDENCE"
    if not row.get("employee_id"):
        return "INSUFFICIENT_EVIDENCE"
    return "RECOVERABLE"


async def backfill_orphaned_review_links(dry_run: bool = True, performed_by: str = "system") -> dict:
    """Reconstructs the missing fpe_unmatched_messages row for every
    RECOVERABLE orphan, using ONLY data already durably recorded
    elsewhere (see module docstring) -- never invents a value. Uses the
    ORIGINAL id (source_message_id) as the new row's primary key, so
    every existing reference to it (fpe_cash_transactions.
    source_message_id, any historical log mentioning that id) stays
    correct. review_note transparently marks the row as reconstructed,
    including which txn/wa-message it was derived from -- never presented
    as an original, undisturbed record.

    dry_run=True (default): returns exactly what WOULD be inserted,
    writes nothing. dry_run=False: performs the INSERTs (idempotent --
    skips any id that already exists, so a repeated run is harmless)."""
    orphans = await find_orphaned_operator_transactions()
    recoverable, test_artifacts, insufficient = [], [], []
    for row in orphans:
        cls = classify_orphan(row)
        if cls == "RECOVERABLE":
            recoverable.append(row)
        elif cls == "TEST_ARTIFACT_SUSPECT":
            test_artifacts.append(row)
        else:
            insufficient.append(row)

    repaired = []
    for row in recoverable:
        review_id = int(row["source_message_id"])
        # Idempotency: a prior partial run, or a genuinely coincidental id
        # collision, must never double-insert or clobber an existing row.
        existing = await fetch_one("SELECT id FROM fpe_unmatched_messages WHERE id = $1", review_id)
        if existing:
            continue
        note = (
            f"RECONSTRUCTED (Stage H.5, {performed_by}): original row lost "
            f"(root cause unknown, no deletion code found in this repo); "
            f"rebuilt deterministically from fpe_cash_transactions.id={row['txn_id']} "
            f"(txn_ref={row['txn_ref']}) + fpe_wa_messages.id={row['fpe_wa_message_id']} "
            f"(raw_content unchanged, unmodified since original submission). "
            f"No new data invented."
        )
        if not dry_run:
            await execute(
                """
                INSERT INTO fpe_unmatched_messages
                    (id, fpe_wa_message_id, reason, raw_content,
                     reviewed, detected_amount, detected_payout_phone,
                     detected_employee_name, detected_payout_method,
                     detected_txn_date, parser_confidence, review_status,
                     resolved_employee_id, promoted_txn_id, reviewer, review_note)
                VALUES ($1, $2, 'operator_submission_backfilled', $3,
                        false, $4, $5, $6, $7, $8, 1.0, 'operator_pending',
                        $9, $10, NULL, $11)
                """,
                review_id, row["fpe_wa_message_id"], row["raw_content"],
                row["amount"], row["payout_phone"], row["employee_name_raw"],
                row["payout_method"], row["txn_date"],
                row["employee_id"], row["txn_id"], note,
            )
        repaired.append({"review_id": review_id, "txn_id": row["txn_id"], "txn_ref": row["txn_ref"]})

    return {
        "dry_run": dry_run,
        "orphans_found": len(orphans),
        "recoverable": len(recoverable),
        "test_artifact_suspect": [{"txn_id": r["txn_id"], "txn_ref": r["txn_ref"], "raw_content": r["raw_content"]} for r in test_artifacts],
        "insufficient_evidence": [{"txn_id": r["txn_id"], "txn_ref": r["txn_ref"]} for r in insufficient],
        "repaired": repaired,
    }


# ── H5.6: full classification report over every pending cash transaction,
# not just the operator-sourced orphans above -- a report first, no mass
# update. Categories match the Stage H.5 directive's own enum verbatim.
VALID_PENDING_REVIEWABLE = "VALID_PENDING_REVIEWABLE"
VALID_PENDING_DIRECT = "VALID_PENDING_DIRECT"
ORPHANED = "ORPHANED"
ALREADY_RESOLVED_BUT_STATUS_STALE = "ALREADY_RESOLVED_BUT_STATUS_STALE"
DUPLICATE_SUSPECT = "DUPLICATE_SUSPECT"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
TEST_ARTIFACT_SUSPECT = "TEST_ARTIFACT_SUSPECT"


async def classify_all_pending_cash_transactions() -> list[dict]:
    """READ-only. One row per currently-pending fpe_cash_transactions
    entry, each tagged with exactly one classification -- the report
    H5.6 asks for, before any mass action. Duplicate-suspect detection
    reuses the same (employee_id, amount, txn_date) grouping the H20
    system-observation anomaly check already uses -- one definition, not
    two."""
    rows = await fetch_all(
        """
        SELECT t.id AS txn_id, t.txn_ref, t.source, t.source_message_id,
               t.employee_id, t.employee_name_raw, t.amount, t.payout_method,
               t.txn_date, t.created_at, t.fpe_wa_message_id,
               w.raw_content,
               u.id AS existing_review_id, u.review_status AS existing_review_status
        FROM fpe_cash_transactions t
        LEFT JOIN fpe_wa_messages w ON w.id = t.fpe_wa_message_id
        LEFT JOIN fpe_unmatched_messages u ON u.id = t.source_message_id::bigint
        WHERE t.transaction_status = 'pending'
        ORDER BY t.created_at ASC
        """
    )
    dupes = await fetch_all(
        """
        SELECT employee_id, amount, txn_date, COUNT(*) AS n
        FROM fpe_cash_transactions WHERE transaction_status='pending'
        GROUP BY employee_id, amount, txn_date HAVING COUNT(*) > 1
        """
    )
    dupe_keys = {(d["employee_id"], d["amount"], d["txn_date"]) for d in dupes}

    out = []
    for r in rows:
        row = dict(r)
        key = (row["employee_id"], row["amount"], row["txn_date"])
        is_test_artifact = bool(_TEST_ARTIFACT_RE.search(row.get("raw_content") or ""))
        if is_test_artifact:
            # Checked before duplicate-grouping: four REGTEST rows sharing
            # amount=1.00/no employee/the same test date would otherwise
            # also match the duplicate-amount heuristic -- "this is test
            # data" is the more actionable, specific signal.
            row["classification"] = TEST_ARTIFACT_SUSPECT
        elif key in dupe_keys:
            row["classification"] = DUPLICATE_SUSPECT
        elif row["existing_review_id"] is not None:
            row["classification"] = VALID_PENDING_REVIEWABLE
        elif row["source"] != "operator":
            # Not an operator-workflow transaction at all -- no review row
            # was ever part of its lifecycle by design (e.g. a manual/
            # direct creation path). Direct, not orphaned.
            row["classification"] = VALID_PENDING_DIRECT
        else:
            cls = classify_orphan(row)
            row["classification"] = TEST_ARTIFACT_SUSPECT if cls == "TEST_ARTIFACT_SUSPECT" else (
                ORPHANED if cls == "RECOVERABLE" else INSUFFICIENT_EVIDENCE
            )
        out.append(row)
    return out
