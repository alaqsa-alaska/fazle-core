# Fazle Payroll Engine (FPE)

The FPE is the core financial processing subsystem. It ingests WhatsApp
messages from the admin-accountant conversation, parses payment records,
matches them to employees, and writes verified transactions to an immutable
accounting ledger.

## Architecture

```
WhatsApp messages → ingest → parse → match employee → create transaction → ledger
```

## Components

| Path | Purpose |
|------|---------|
| `__init__.py` | `start_fpe()` — runs all migrations on startup, starts 5 workers |
| `routes.py` | ~2900 lines — all `/api/fpe/*` endpoints (39 endpoints) |
| `accounting.py` | `create_transaction()`, `reverse_transaction()`, `_upsert_ledger()` |
| `employee.py` | `match_or_create_employee()`, `create_employee_manual()`, `_resolve_canonical()` |
| `parser.py` | Deterministic regex parser for payment messages |
| `workers.py` | 5 asyncio background workers (message processor, accounting, sync, gap scan, bridge health) |
| `ingestion.py` | `ingest_message()` — idempotent WhatsApp message store |
| `normalizer.py` | `normalize_bd_phone()` — single source of truth for BD phone normalization |
| `models.py` | Pydantic schemas + enums (PayoutMethod, TxnCategory, TransactionStatus) |
| `payment_event.py` | Normalizes payment events from all sources into TransactionCreateRequest |
| `reconcile.py` | Reconciliation invariant: ledger + unmatched == parser |
| `normalization.py` | Employee alias management, duplicate resolution, review queue |
| `ai_enhancer.py` | Ollama AI fallback parser (quarantined per owner directive 2026-07-02) |
| `migrations/` | 11 ordered SQL migration files (auto-run at startup) |

## Migration System

All files matching `migrations/*.sql` are executed in alphabetical order
by `start_fpe()` at application startup. Each statement is idempotent
(`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).

| File | Purpose |
|------|---------|
| `001_fpe_schema.sql` | Core tables: fpe_wa_messages, fpe_employees, fpe_cash_transactions, fpe_employee_ledger, fpe_unmatched_messages, fpe_accounting_audit_logs |
| `002_fpe_normalization.sql` | Soft-link, review queue, normalization audit |
| `003_fpe_search_indexes.sql` | Search-optimized indexes |
| `004_fpe_zero_loss.sql` | Review queue enrichment, DLQ, gap scan |
| `005_review_audit_logs.sql` | Immutable review audit log |
| `006_fpe_income.sql` | Income transactions table |
| `007_fpe_diagnostics.sql` | Processing diagnostics table |
| `008_unification.sql` | `wbom_employee_id` soft-link, `fazle_processing_locks`, phone-based backfill |
| `009_stabilization.sql` | Draft TTL, historical flag, bridge heartbeats, message queue |
| `010_fpe_cash_transactions_canonical.sql` | Canonical cash transaction columns (source, approval lifecycle, soft-delete) |
| `011_fpe_employees_hr_columns.sql` | HR profile columns (basic_salary, designation, joining_date) + WBOM backfill |

## Key Tables

| Table | Purpose |
|-------|---------|
| `fpe_employees` | **Canonical employee table** — identity + HR profile (basic_salary, designation, joining_date). Soft-linked to `wbom_employees` via `wbom_employee_id`. |
| `fpe_cash_transactions` | **Canonical immutable cash transaction ledger** — all payments end here via `create_transaction()`. Never mutated; corrections use reversal rows. |
| `fpe_employee_ledger` | Running totals per employee per month (total_earned, total_paid, total_advance, closing_balance) |
| `fpe_wa_messages` | Immutable raw WhatsApp message store |
| `fpe_unmatched_messages` | Zero-loss review queue — detected money not yet in ledger |
| `fpe_accounting_audit_logs` | Immutable audit log for every transaction lifecycle event |
| `fpe_income_transactions` | Company income received (separate from payroll) |
| `fpe_employee_aliases` | Multi-phone / multi-name aliases per employee |
| `fpe_parser_results` | Parsed extraction results per message |
| `fpe_message_processing_state` | Per-message FSM (pending→parsing→parsed→accounting→done) |
| `fpe_sync_checkpoints` | Resume-safe sync state per bridge/chat |
| `fpe_gap_scan_runs` | Bridge gap scan execution log |
| `fpe_processing_diagnostics` | Per-message processing outcomes |
| `fpe_review_audit_logs` | Immutable audit log for review-queue actions |
| `fpe_normalization_audit_logs` | Audit log for normalization actions |
| `fpe_employee_resolution_links` | Audit trail of duplicate→canonical decisions |
| `fpe_employee_review_queue` | Ambiguous match review queue |

## Employee Identity (2026-07-02)

`fpe_employees` is now the **single canonical employee table** for the entire
platform. It carries both identity fields (phone, name) and HR profile fields
(basic_salary, designation, joining_date). All employee creation paths
(WhatsApp parser, escort program confirmation, frontend Add Employee) now
create or link `fpe_employees` rows.

Cross-reference with `wbom_employees` is maintained via `wbom_employee_id`
soft-link. Migration 011 backfilled all 178 WBOM employees into FPE.

## Adding a Migration

1. Create `migrations/NNN_description.sql` (next sequential number).
2. All statements must be idempotent (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`).
3. Never drop columns or rename existing columns.
4. Restart `fazle-core.service` — `start_fpe()` will apply it automatically.
