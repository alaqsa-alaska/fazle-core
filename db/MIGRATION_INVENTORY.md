# Migration Inventory — Fazle Core

**Added:** 2026-07-08, System Consolidation Sprint (Task 4 — migration governance, first safe step)
**Purpose:** a complete, factual list of every schema-creation source in this repository, as it exists today. This document changes nothing — no file listed here has been renamed, renumbered, or altered as part of adding this inventory. See `knowledge_base/06_developer_system/database_rules.md`'s "Migration Governance" section for the canonical-path policy this inventory supports, and `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 4 for the full audit this was derived from.

**Status of each source, going forward:**
- ✅ **Canonical** — `db/migrations/` + `db/migrate.py`. All new schema changes land here.
- 🗄️ **Legacy/historical** — not deleted, not extended with new files, pending a dedicated reconciliation pass.

---

**Updated 2026-07-12** (deployment-restart-gate implementation): added the 15 files below (`029`-`043`) that had accumulated since this inventory was first written 2026-07-08 without a corresponding update — found by `scripts/restart_gate.sh` check 5 on its first real run. No files renamed/renumbered by this update either, same as the original policy.

**Updated again 2026-07-12/13** (Phase 2B Commit D): added `044_billing_payment_recording.sql` and `045_ai_read_billing_outstanding.sql`. Both applied directly to production (not via `db/migrate.py` — see the note below about `schema_migrations` being stale since `028`).

**Updated again 2026-07-14** (Employee Edit approval queue): added `046_employee_edit_requests.sql` (new `fpe_employee_edit_requests` table). Applied directly to production the same way as `044`/`045` (dry-run in a `BEGIN...ROLLBACK` first, then applied for real), and this one *was* also recorded in `schema_migrations` — the `029`-`045` gap noted below is pre-existing and untouched by this change, but there's no reason to let it grow further.

**Known gap found while applying 044/045**: `db/migrate.py`'s own `schema_migrations` tracking table has not recorded anything past `028_assistant_ext_location_audit.sql` — migrations `029` through `045` were all applied to production via direct execution (matching the pattern already used for `029`-`043` per their own "Done" entries in `NEXT_IMPLEMENTATION_QUEUE.md`), bypassing `migrate.py`'s bookkeeping entirely. Running `db/migrate.py` for real (not `--dry-run`) today would attempt to re-apply all of `029`-`045`, which is risky since not every one of those files is idempotent. Not fixed in this pass — tracked under Tier 2 "Cross-directory migration collision cleanup."

**Updated again 2026-07-15** (`chat_history` shadow archive): added `048_chat_history.sql` (new, standalone `chat_history` table — not read or written by any fazle-core application code, exists only as the write target for an out-of-repo read-only sync service). Applied directly against `schema_migrations` for this one file only (**not** via a real `db/migrate.py` run — that would have re-attempted the entire `029`-`045` backlog above, which was out of scope for this task and deliberately not touched). `047` was intentionally skipped — it's reserved in `db/migrations_drafts/` for an unrelated, still-pending Owner decision (see `db/.restart_gate_manifest`).

**Updated again 2026-07-19** (Dispatch Phase 2A.3 Stage 1): added `049_dispatch_2a3_escort_changed_status.sql` (widens `wbom_escort_programs`'s status CHECK constraint to accept a new `'escort_changed'` value) and `050_dispatch_2a3_replacement_linkage.sql` (adds `wbom_escort_programs.replaces_program_id`, a nullable self-referential FK, plus a regular index, a self-reference CHECK, and a partial UNIQUE index enforcing at most one non-terminal replacement per predecessor). Both purely additive, no existing row rewritten, verified on the disposable test DB before production application. See `knowledge_base/00_governance/management_decisions.md` → "Dispatch Phase 2A.3 Stage 1" for full detail.

**Updated again 2026-07-19** (Phase B, Conversation Memory Engine, Stage 2): added `054_conversation_memory_shadow_comparisons.sql` -- new table `fazle_conversation_shadow_comparisons`, one structured row per authorized escort-client message observed while `MULTI_MESSAGE_ESCORT_PARSER_ENABLED` is on, recording the single-message-parser result side by side with the conversation-aware (multi-thread grouping-aware) result plus provenance/completion/conflict/grouping metadata. Aggregate metrics (Section 9's required counts) are computed on demand via `GROUP BY` over this table, not a separate running-counters table. Purely additive, no existing table touched, `thread_id` FK to `fazle_conversation_threads` is nullable (a `needs_review` observation may not resolve to a single thread). Verified on a disposable `postgres:17-alpine` container (idempotent re-run, FK constraint holds) before production application.

**Updated again 2026-07-20** (Phase B Stage 4 shadow metrics): added `055_conversation_memory_shadow_metrics.sql` -- adds `processing_latency_ms` to `fazle_conversation_shadow_comparisons` plus an index on `grouping_action`, supporting Stage 4's required "shadow exceptions and processing latency" metric. Purely additive (`ADD COLUMN IF NOT EXISTS`), verified idempotent on a disposable container before production application.

**Updated again 2026-07-19** (Dispatch Phase 2A.3 Stage 2): added `051_dispatch_2a3_change_request_action_widen.sql` (widens `escort_roster_change_requests`'s own `action` CHECK constraint -- separate from and in addition to the application-level whitelist in `create_roster_change_request()` -- to accept `'manual_review_same_date_escort_change'`). Discovered mid-implementation, not anticipated in the Decision Report. Purely additive, no existing row rewritten.

**Updated again 2026-07-19** (Workstream 2, draft-confirmation invariant, Stage 1): added `052_unmatched_escort_confirmations.sql` -- new table `wbom_unmatched_confirmations`, replacing `handle_admin_escort_completion()`'s silent auto-create-on-no-match with a durable admin-review queue (see `modules/escort/__init__.py::_find_pending_program()`/`_create_confirmed_from_admin()`). Purely additive, no existing table touched. Includes a partial unique index on `(source_bridge, source_message_id)` closing a real cross-path duplicate-admission gap: `processed_bridge_messages` and `processed_outgoing_escort_messages` (both `modules/bridge_poller/__init__.py`) each dedup their own ingestion path independently, so the same physical admin confirmation could otherwise reach this queue twice via two different paths. Verified on a genuinely disposable, throwaway `postgres:17-alpine` Docker container (not the shared test DB, which was unavailable this session) before production application: idempotent re-run, unique-violation on true duplicate, multiple `NULL` message-ids permitted (webhook path lacks a reliable id), status `CHECK` enforced, FK to `wbom_escort_programs` enforced. See `knowledge_base/00_governance/management_decisions.md` → "Draft-Confirmation Invariant, Workstream 2 Stage 1" for full detail.

**Updated again 2026-07-19** (Phase B, Conversation Memory Engine, Stage 1): added `053_conversation_memory_engine.sql` -- new table `fazle_conversation_threads` (generic, JSONB-structured per-field provenance -- value/source_message_id/confidence/extraction_method per accumulated field) plus a dedup table `processed_conversation_messages` following the exact existing `processed_bridge_messages`/`processed_outgoing_escort_messages` pattern. Deliberately reuses `wbom_whatsapp_messages` as the raw message-event source and computes `system_generated` on read via the already-deployed `modules.bridge_poller._is_system_generated_notification()` rather than storing it -- neither is a new table. Owner-approved scope decision: coexists with, does not replace, `fazle_recruitment_sessions`/`intake_message()` (recruitment keeps its own existing, working mechanism). Verified on a genuinely disposable `postgres:17-alpine` container before production application: idempotent re-run, `state` `CHECK` enforced, dedup `(message_id, bridge)` uniqueness enforced, JSONB field structure round-trips correctly. See `knowledge_base/00_governance/management_decisions.md` → "Phase B, Conversation Memory Engine, Stage 1" for full detail.

## ✅ `db/migrations/` (canonical, 53+ files as of this update — see 2026-07-15 note above for a known pre-existing count discrepancy not resolved here) — tracked in `schema_migrations` via `db/migrate.py`

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
044_billing_payment_recording.sql
045_ai_read_billing_outstanding.sql
046_employee_edit_requests.sql
048_chat_history.sql
049_dispatch_2a3_escort_changed_status.sql
050_dispatch_2a3_replacement_linkage.sql
051_dispatch_2a3_change_request_action_widen.sql
052_unmatched_escort_confirmations.sql
053_conversation_memory_engine.sql
054_conversation_memory_shadow_comparisons.sql
055_conversation_memory_shadow_metrics.sql
056_contact_sync_presence_tracking.sql
057_route_b_handoffs.sql
058_rebrand_al_rifai_kb_text.sql
059_admin_directives.sql
```

**Note — no `009`**: never existed in this directory's history, or was removed before it. Intentionally left as a documented gap (see governance section) — do not backfill.

**`056_contact_sync_presence_tracking.sql` (2026-07-22):** adds `fazle_contact_presence`
(per-phone-per-bridge absence-streak tracking) and `fazle_contact_hard_delete_log`
(audit trail written before every hard-delete) — supports the new daily contact-sync
soft-delete-then-hard-delete-after-N-misses policy. Purely additive (`CREATE TABLE IF
NOT EXISTS`), applied directly against production and recorded in `schema_migrations`
(not via a full `db/migrate.py` run — see `management_decisions.md` 2026-07-22 for why:
several much older migrations show as "pending" only because they predate the
`schema_migrations` tracking table, and running the full queue would have attempted to
re-apply all of them). Full record: `management_decisions.md` 2026-07-22.

**`057_route_b_handoffs.sql` (2026-07-22, NOT YET APPLIED to production — see
`management_decisions.md` 2026-07-22 "Route B Conversation Handoff"):** adds
`fazle_route_b_handoffs` — audit/idempotency/cooldown table for the new,
feature-flagged-off Route B conversation-handoff layer (`modules/route_b_handoff`).
Purely additive (`CREATE TABLE IF NOT EXISTS`, two `CREATE INDEX IF NOT EXISTS`, no
`ALTER`/`DROP`, no FK to any other table — unlike `003b`'s `wbom_candidates` FK, this
one cannot fail against a schema that's missing an unrelated table). No data migration,
no column changes to any existing table. To apply: same direct-script +
`schema_migrations` insert method used for `056`, only after Owner deployment approval
(Step 2 of the controlled rollout) — do not run the full `db/migrate.py` queue.

**`058_rebrand_al_rifai_kb_text.sql` (2026-07-22):** content-only `UPDATE` of
`fazle_knowledge_base.reply_text`/`reply_short` — replaces the old company
name(s) ("Al-Manaratayn Skill Solutions"/আল-মানারাতাইন স্কিল সলিউশনস, and the
pre-rebrand-1 "Al-Aqsa..." forms) with the current brand, "Al-Rifa'i
Integrated Services". No schema change, no `ALTER`/`DROP`. Idempotent (each
`REPLACE()` is a no-op once the old string is gone; the `WHERE` clause scopes
to rows that still contain an old name). See `management_decisions.md`
2026-07-22 "Second Rebrand" for the full company-name centralization change
this is part of.

**`059_admin_directives.sql` (2026-07-30):** adds `fazle_admin_directives` — the
Unified Admin AI Engine's narrow admin directive/action memory table
(`directive_text`, DB-CHECK-constrained `category`/`created_by_channel`/`status`,
optional `subject_type`/`subject_key` scoping, soft-revoke fields). Purely
additive (`CREATE TABLE IF NOT EXISTS`, 3 `CREATE INDEX IF NOT EXISTS`, no
`ALTER`/`DROP`, no FK to any other table). Applied directly against production
and recorded in `schema_migrations` (same direct-script + `schema_migrations`
insert method used for `056`/`057`, not a full `db/migrate.py` run, for the same
reason documented there). Full record: `management_decisions.md` 2026-07-30
"Unified Admin AI Engine".

**Next available number for new work: `060`.**

**Known cross-directory `ALTER TABLE` touches into these tables from outside `db/migrations/`** (see Report 4 for full evidence): `fpe_cash_transactions`, `fpe_income_transactions`, `escort_roster_entries`, `wbom_escort_programs`, `fazle_payment_drafts` are each also touched by `fazle_payroll_engine/migrations/` and/or the legacy `migrations/` directory.

**Re-investigated 2026-07-13, table by table (full file:line detail available on request) — good news: no actual data-corrupting conflicts found in any of the 5.** Every cross-directory overlap is one of two harmless shapes:
- **Redundant-but-idempotent**: two directories independently add the *same* column/constraint/index (e.g. `fpe_cash_transactions.deleted_at/deleted_by/updated_at` — added by both `fazle_payroll_engine/migrations/010` and `db/migrations/023_c0d_001`, identical definitions, both `IF NOT EXISTS`-guarded; `fazle_payment_drafts.expires_at` — added by both `fpe/009` and `db/021`, `db/021`'s own comment even says *"already exists from migration 009; ensure it is present for safety"*, i.e. already a known, deliberate duplicate).
- **Additive-but-uncoordinated**: different directories add genuinely *different* columns/indexes to the same table with no overlap at all (e.g. `escort_roster_entries` — `migrations/`'s `expires_at`, `db/018`'s `food_bill`/`advance_deduction`/`net_payable`, `db/020`'s `escort_employee_id`, all disjoint; `wbom_escort_programs` — `db/018`'s `food_bill` vs `fazle_payroll_engine/009`'s `is_historical`, disjoint).

`fazle_payroll_engine/migrations/` is confirmed **not dead code** — `start_fpe()` (`modules/fazle_payroll_engine/__init__.py:25-38`) is called from `app/main.py`'s startup lifespan and re-runs that whole directory's SQL on every restart; its cross-directory touches above are live, not theoretical.

**One separate, unrelated re-run hazard found during this pass** (not a cross-directory collision — a fragility risk *within* `db/migrations/003c_schema_align.sql` alone): that file's 8 `ALTER TABLE ... DROP CONSTRAINT x, ADD CONSTRAINT x ...` blocks (lines 54-120) and its 2 `RENAME COLUMN` statements (lines 11-16) have no idempotency guard — Postgres has no `DROP CONSTRAINT IF EXISTS ... ADD CONSTRAINT` single-statement equivalent, and a second `RENAME COLUMN` on an already-renamed column errors outright. Already applied to production once (migration 003c, long predates this inventory), so this is a *replay* risk, not a current-state risk — it would only bite if `db/migrate.py`'s stale `schema_migrations` tracking (see above) were ever reconciled and the file replayed, or in a from-scratch disaster-recovery rebuild. **Not fixed this pass** — deliberately not editing an already-applied historical migration file without an explicit Owner decision on whether that's an acceptable thing to do in this repo's conventions (vs. treating applied migration files as immutable record and instead writing a fresh idempotent migration if the file ever genuinely needs to be replayed).

**Verdict: the cross-directory "collision" risk is downgraded from "unreconciled, unknown severity" to "confirmed harmless, just messy."** Actually consolidating the 3 directories into one remains worthwhile for maintainability (still tracked as this Tier 2 item), but is no longer an urgent correctness concern.

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
