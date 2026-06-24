---
title: Admin Transactions Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Admin Transactions Rules
**KB Article ID:** ADMIN-02-ADMIN-TRANSACTIONS-RULES
**Source:** `modules/admin_transactions/__init__.py` (551 lines — read 2026-06-23)
**Visibility:** Developer / Admin only
**Certified:** 2026-06-23 (Wave-4, W4-AUTH)

---

## Purpose

Provides REST API endpoints for creating, editing, and soft-deleting cash transactions in `fpe_cash_transactions`. Used by admin UI and `wa_chat_frontend` to manually record payments without going through the WhatsApp intake flow.

**Finance table:** `fpe_cash_transactions` (FPE/CASH domain — see C-01 in `management_decisions.md`).

---

## Core Constraint — Hard Delete Never Allowed

```python
# NEVER hard-delete financial history
# Soft delete via deleted_at / deleted_by columns
```

Physical deletion of `fpe_cash_transactions` rows is forbidden. All deletes are soft (set `deleted_at` / `deleted_by`). Audit log entry created on every soft delete.

---

## API Endpoints

All endpoints require `X-Internal-Key` header (internal API key or active admin key from `rbac.get_admin_by_api_key()`).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/admin/transactions` | Create a new transaction |
| `PUT` | `/api/admin/transactions/{txn_id}` | Edit an existing transaction |
| `DELETE` | `/api/admin/transactions/{txn_id}` | Soft-delete a transaction |

---

## `AdminTxnCreate` — Request Model

| Field | Type | Default | Allowed Values |
|---|---|---|---|
| `employee_name` | str | required | Non-empty after strip |
| `employee_id_phone` | Optional[str] | None | Phone identity anchor (7–20 digits with optional +/-/spaces) |
| `payout_phone` | Optional[str] | None | Actual payment phone (may differ from identity phone) |
| `amount` | Decimal | required | Must be > 0 |
| `payout_method` | str | `"cash"` | `bkash`, `nagad`, `cash`, `bank`, `unknown` |
| `txn_date` | date | required | Calendar date of transaction |
| `txn_category` | str | `"salary"` | `salary`, `advance`, `bonus`, `deduction`, `other` |
| `notes` | Optional[str] | None | Stored as `source_message_text` |

**Phone identity anchor distinction:**
- `employee_id_phone` — immutable identity key; used to find or create the `fpe_employees` record
- `payout_phone` — where the money actually went; may be bKash/Nagad number different from identity phone

---

## Smart Employee Matching — `resolve_or_create_employee()`

Called on every transaction creation. Tries 4 resolution rules in order:

| Rule | Match Strategy | Action |
|---|---|---|
| **A** | `employee_id_phone` exact match vs `fpe_employees.employee_id_phone` | Return existing employee |
| **B** | `payout_phone` exact match vs `fpe_employees.primary_phone` | Return existing employee |
| **C** | No phones provided — fuzzy name match via `pg_trgm` similarity ≥ 0.95 | Return existing employee |
| **D** | No match found | Auto-create new `fpe_employees` row |

**Phone normalization in matching:** Uses `fazle_payroll_engine.normalizer.normalize_bd_phone()` (11-digit FPE format) — not the canonical 13-digit `phone_normalizer`.

**Auto-create (Rule D) sets:**
- `employee_code` = `EMP-{id:05d}` (sequential, assigned after INSERT)
- `created_source` = `"admin_manual"`
- `resolution_status` = `"auto_created"`
- `confidence_score` = `1.0`

---

## Transaction Creation (`POST /api/admin/transactions`)

**Sequence:**
1. Resolve employee via `resolve_or_create_employee()`
2. Compute `accounting_period` = `YYYY-MM` from `txn_date`
3. Build deterministic `txn_ref` = `"fpe-admin-"` + SHA256 of `{emp_id}|{amount}|{date}|{uuid4}` (first 16 hex chars)
4. INSERT into `fpe_cash_transactions`
5. INSERT audit log row into `fpe_accounting_audit_logs` (action=`"admin_create"`)
6. Call `_adjust_ledger()` to increment bucket for employee + period + category

---

## Transaction Edit (`PUT /api/admin/transactions/{txn_id}`)

**Optimistic locking:** If `X-If-Match-Updated-At` header is sent, the current `updated_at` is compared (±1 second tolerance). Mismatch → HTTP 409 Conflict.

**Cannot edit soft-deleted rows** → HTTP 409 if `deleted_at` is set.

**Ledger adjustment on financial change:** If `amount` or `accounting_period` (= `txn_date` → YYYY-MM) changes:
1. Reverse old: `_adjust_ledger(employee_id, old_period, -old_amount, old_cat)`
2. Apply new: `_adjust_ledger(employee_id, new_period, new_amount, new_cat)`

Audit log written with `before_state` + `after_state` JSON (amount, period, category).

---

## Soft Delete (`DELETE /api/admin/transactions/{txn_id}`)

1. Verify row exists and is not already deleted
2. Set `deleted_at = NOW()`, `deleted_by = <deleted_by param>` (default: `"admin_manual"`)
3. Write audit log: action = `"admin_soft_delete"`
4. Reverse ledger: `_adjust_ledger(employee_id, period, -amount, category)`

**The row is NEVER physically removed.**

---

## Ledger Adjustment — `_adjust_ledger()`

Wraps `fazle_payroll_engine.accounting._upsert_ledger()`. Increments or decrements the ledger bucket for a given employee + period + category. Pass negative `amount` to reverse (undo).

Uses `TxnCategory` enum from `modules.fazle_payroll_engine.models`; defaults to `TxnCategory.salary` on invalid category.

---

## Audit Log — `fpe_accounting_audit_logs`

Every mutation creates a row with:

| Field | Values |
|---|---|
| `entity_type` | `"transaction"` |
| `entity_id` | Transaction `id` |
| `action` | `"admin_create"` / `"admin_edit"` / `"admin_soft_delete"` |
| `before_state` | JSON snapshot of changed fields (on edit/delete) |
| `after_state` | JSON snapshot of new values (on create/edit) |
| `performed_by` | `"admin_manual"` |
| `reason` | Description string |

---

## Authentication

`X-Internal-Key` header required. Two valid forms:
1. Matches `settings.internal_api_key` (system-level key)
2. Valid active admin API key in `rbac.get_admin_by_api_key()`

Missing or invalid key → HTTP 403 Unauthorized.

---

## Cross-References

- `payment_business_rules.md` — PAY-01 formula, rates, categories
- `database_rules.md` — FPE domain tables, `fpe_cash_transactions` schema
- `admin_ui.md` — wa_chat_frontend endpoints that call these routes
- `management_decisions.md` — C-01 (fpe_cash_transactions assigned to CASH/FPE domain)
