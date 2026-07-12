# Migration Inventory — Fazle Core

**Added:** 2026-07-08, System Consolidation Sprint (Task 4 — migration governance, first safe step)
**Purpose:** a complete, factual list of every schema-creation source in this repository, as it exists today. This document changes nothing — no file listed here has been renamed, renumbered, or altered as part of adding this inventory. See `knowledge_base/06_developer_system/database_rules.md`'s "Migration Governance" section for the canonical-path policy this inventory supports, and `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 4 for the full audit this was derived from.

**Status of each source, going forward:**
- ✅ **Canonical** — `db/migrations/` + `db/migrate.py`. All new schema changes land here.
- 🗄️ **Legacy/historical** — not deleted, not extended with new files, pending a dedicated reconciliation pass.

---

**Updated 2026-07-12** (deployment-restart-gate implementation): added the 15 files below (`029`-`043`) that had accumulated since this inventory was first written 2026-07-08 without a corresponding update — found by `scripts/restart_gate.sh` check 5 on its first real run. No files renamed/renumbered by this update either, same as the original policy.

## ✅ `db/migrations/` (canonical, 50 files) — tracked in `schema_migrations` via `db/migrate.py`

```
001_safe_mode_and_escort_extractor.sql
002_align_tables.sql
003_kb_schema_fix_and_recruitment.sql
003b_recruitment_sessions_fix.sql
003c_schema_align.sql
004_identity_brain.sql
005_unified_draft_approval.sql
006_critical_contact_zero_loss.sql
007_reviewed_reply_memory.sql
008_payment_correction.sql
010_state_version.sql
011_runtime_nodes.sql
012_ai_readonly_views.sql          } duplicate leading number 012 — filename is the real key, see below
012_queue_arbiter.sql              }
013_social_auto_reply.sql
014_career_hr_dataset.sql
015_llm_learning_memory.sql
016_user_profiles_memory.sql
017_memory_promotion.sql
018_atomic_release_and_outbound.sql
019_central_message_fields.sql
020_escort_roster_employee_fk.sql  } duplicate leading number 020 — filename is the real key, see below
020_preflight_guard_tables.sql     }
021_sprint3a_employee_conversation_drafts.sql
022_sprint3b_draft_approval.sql
023_c0d_001_fpe_soft_delete_columns.sql      }
023_c0d_002_deprecate_approved_columns.sql   } 023 fragmented into 5 files (Sprint-C0D pattern) —
023_c0d_003_fpe_missing_indexes.sql          } this is the correct precedent for future reconciliation work
023_c0d_004_income_check_constraint.sql      }
023_model_router_tables.sql                  }
024_escort_history_fk.sql
025_escort_confirmed_exact_unique.sql
026_escort_roster_change_requests.sql
027_assistant_feature_flags.sql
028_assistant_ext_location_audit.sql
029_bootstrap_core_tables_schema_record.sql
030_reconcile_drift_columns_payment_drafts_replies.sql
031_client_domain_reconciliation.sql
032_client_tier_status_model.sql
033_invoice_number_counters.sql
034_client_billing_profiles.sql
035_client_billing_wage_general.sql
036_billing_records_program_uniqueness.sql
037_billing_payment_status_constraint.sql
038_escort_program_status_lowercase.sql
039_schema_reconciliation_active_tables.sql
040_attendance_entry_source_and_approval.sql
041_identity_conflict_anomalies.sql
042_remove_blocked_number_concept.sql
043_office_assistant_role.sql
```

**Note — no `009`**: never existed in this directory's history, or was removed before it. Intentionally left as a documented gap (see governance section) — do not backfill.

**Next available number for new work: `044`.**

**Known cross-directory `ALTER TABLE` touches into these tables from outside `db/migrations/`** (see Report 4 for full evidence): `fpe_cash_transactions`, `fpe_income_transactions`, `escort_roster_entries`, `wbom_escort_programs`, `fazle_payment_drafts` are each also touched by `fazle_payroll_engine/migrations/` and/or the legacy `migrations/` directory. Not yet reconciled.

---

## 🗄️ `migrations/` (legacy/historical, 6 files) — no automated runner, historically applied via manual `psql -f`

```
002_add_hr_officers.sql
003_add_fpe_indexes.sql
004_add_accounting_period_constraint.sql
005_add_admin_login_auth.sql
escort_history_schema.sql
escort_roster_schema.sql
```

No `001` file exists. Not tracked in any `schema_migrations` table. Do not add new files here — port to `db/migrations/` during the reconciliation pass instead.

---

## 🗄️ `modules/fazle_payroll_engine/migrations/` (legacy/historical, 14 files) — re-run in full at every app startup, not tracked

```
001_fpe_schema.sql
002_fpe_normalization.sql
003_fpe_search_indexes.sql
004_fpe_zero_loss.sql
005_review_audit_logs.sql
006_fpe_income.sql
007_fpe_diagnostics.sql
008_unification.sql
009_stabilization.sql
010_fpe_cash_transactions_canonical.sql
011_fpe_employees_hr_columns.sql
012_fpe_ledger_total_earned_backfill.sql
013_fpe_uniform_salary_10000.sql
014_fpe_income_soft_delete.sql
```

Own independent, internally-clean `001`–`014` sequence (no gaps/duplicates within this directory). Executed statement-by-statement on every `start_fpe()` call (`modules/fazle_payroll_engine/__init__.py:25-38`), with per-statement exceptions swallowed as warnings — a partially-failed `ALTER` is silently skipped, not blocking startup. Do not add new files here — port to `db/migrations/` during the reconciliation pass instead, after diffing shared-table statements against actual production columns.

---

## 🗄️ Inline Python DDL (legacy/historical, 4 modules, 17 tables) — `CREATE TABLE IF NOT EXISTS` re-executed at every startup, genuinely idempotent

| Module | Tables created |
|---|---|
| `modules/social_auto_reply/__init__.py` | `social_inbox_events`, `social_reply_queue`, `social_sent_log`, `social_retry_queue`, `social_flagged_items`, `social_backlog_state`, `social_rate_limit_state`, `social_thread_state` |
| `modules/contact_sync/__init__.py` | `fazle_contact_sync_log`, `fazle_unified_contacts`, `fazle_contact_aliases` |
| `modules/wa_chat_frontend/__init__.py` | `fazle_runtime_settings`, `wa_chat_groups` |
| `modules/bridge_poller/__init__.py` | `bridge_poller_cursor`, `processed_bridge_messages`, `processed_outgoing_escort_messages`, `outbound_safety_incidents` |

**Known duplicate definition:** all 8 `social_auto_reply` tables above are *also* defined, identically (verified byte-for-byte as of 2026-07-08), in `db/migrations/013_social_auto_reply.sql`. Confirmed in sync today; this is the largest duplicated-schema-definition surface in the repo, with zero drift detection between the two copies. Not resolved by this inventory — tracked as a checklist item in `database_rules.md`'s Migration Governance section.

**Known duplicate definition (byte-for-byte identical as of 2026-07-08):** `fazle_contact_aliases` is *also* created independently in `db/migrations/006_critical_contact_zero_loss.sql:29`.

Treated as a deliberate idempotent-bootstrap pattern for now, not a restructuring target — do not add new inline DDL to new modules going forward; use `db/migrations/` instead.

---

## 🗄️ Test-fixture-only schema (`tests/conftest.py`) — NOT a production schema source, listed here only as a known gap

24 tables have **no `CREATE TABLE` anywhere in this repository** except a guessed re-creation inside `tests/conftest.py`, including two confirmed live production writers with genuinely zero real schema definition anywhere: **`fazle_error_log`**, **`llm_conversation_log`**. Full list of all 24 tables is in `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 4 §7 and Report 9. `conftest.py`'s guessed schema is already known to disagree with production (per the Sprint-C0D audit). **This is the most urgent unresolved item** — a clean environment cannot currently be rebuilt from this repository alone. Tracked as a checklist item in `database_rules.md`'s Migration Governance section; not resolved by this inventory.

---

## Orphaned / non-migration SQL files (not part of any runner)

- **`db/merge_preview.sql`** — pure read-only inspection script (`SELECT`/`\d` only), explicitly self-labeled "Nothing is dropped." Harmless; `db/migrate.py`'s glob only scans `db/migrations/`, never `db/` itself.
- **`db/new_tables.sql`** — a standalone "run once, safe to re-run" bootstrap script that `CREATE TABLE IF NOT EXISTS`'s 3 tables also created by numbered migrations, then does an `INSERT ... ON CONFLICT (key) DO UPDATE` KB seed. **Risk if re-run today:** the `ON CONFLICT DO UPDATE` would silently overwrite any `fazle_knowledge_base` rows edited since. Not deleted by this task — flagged here so nobody re-runs it by accident.
- **`scripts/backfill_skipped_failed_to_review.sql`** (found 2026-07-12) — one-off, idempotent (`WHERE NOT EXISTS` guarded) backfill of skipped/failed messages into `fpe_unmatched_messages`. Never touches the ledger. Not a schema migration.
- **`scripts/seed_recruitment_kb.sql`** (found 2026-07-12) — idempotent (`delete + insert by key`) KB seed for recruitment triggers/replies. Not a schema migration.
