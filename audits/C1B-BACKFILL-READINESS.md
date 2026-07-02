# C1B Backfill Readiness Report
Date: 2026-07-02
Author: Kilo Agent (automated review)

## Summary

This document captures the readiness state for executing `backfill_wbom_to_fpe()`.
Gate-2 was CONDITIONAL PASS pending execution of this backfill.

## Backfill Function

- **Module:** `modules/payment_ingest/wbom_fpe_sync.py`
- **Function:** `backfill_wbom_to_fpe(since_days: int = 30)`
- **Returns:** `(synced_count, skipped_count)` tuple. Never raises.

## What It Does

Reads all WBOM cash transactions from the last `since_days` days
and syncs each one into the FPE pipeline via `sync_wbom_transaction()`.
- Synced = written to `fpe_cash_transactions`
- Skipped = already exists or no matching employee

## Dry-Run Option

The current implementation does NOT have a built-in `dry_run=True` parameter.
To safely assess impact before running, execute a SELECT-only query:

```sql
SELECT COUNT(*) AS total_wbom_rows
FROM wbom_cash_transactions
WHERE created_at >= NOW() - INTERVAL '30 days';
```

Then compare against `fpe_cash_transactions` to see the gap:

```sql
SELECT
  (SELECT COUNT(*) FROM wbom_cash_transactions WHERE created_at >= NOW() - INTERVAL '30 days') AS wbom_count,
  (SELECT COUNT(*) FROM fpe_cash_transactions WHERE txn_date >= NOW() - INTERVAL '30 days') AS fpe_count;
```

## WBOM Rows Expected to Migrate

Per C1B audit: approximately **1,428 WBOM rows** need migration.
Verify actual count using the SQL above before executing.

## Human Approval Required

**⚠ DO NOT run backfill without explicit owner approval.**

This operation writes to `fpe_cash_transactions` which is the canonical
financial ledger. Incorrect execution could create duplicate transactions.

## Command to Execute (After Approval)

```python
# From core/ directory, with database available:
import asyncio
from modules.payment_ingest.wbom_fpe_sync import backfill_wbom_to_fpe

async def run():
    synced, skipped = await backfill_wbom_to_fpe(since_days=365)
    print(f"Synced: {synced}, Skipped: {skipped}")

asyncio.run(run())
```

Or via test runner if available:
```bash
cd /home/azim/core
python -c "
import asyncio
from modules.payment_ingest.wbom_fpe_sync import backfill_wbom_to_fpe
synced, skipped = asyncio.run(backfill_wbom_to_fpe(since_days=365))
print(f'Synced: {synced}, Skipped: {skipped}')
"
```

## Expected Outcome

After execution: Gate-2 → **FULL PASS**

## Pre-Conditions

1. Database is accessible and healthy
2. `wbom_employees` table has matching employee records
3. No concurrent payroll operations running
4. Owner has reviewed the WBOM-FPE count gap

## Status

| Item | Status |
|------|--------|
| Function located | ✅ `payment_ingest/wbom_fpe_sync.py:197` |
| Dry-run SQL verified | ⏳ Pending human execution |
| Owner approval | ⏳ Pending |
| Execution | ⏳ Pending approval |
| Gate-2 upgrade | ⏳ Pending execution |
