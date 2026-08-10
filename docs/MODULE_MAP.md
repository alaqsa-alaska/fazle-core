# Fazle Core — Module Map

**Last updated:** 2026-07-09
**Scope:** the 26 core backend modules named in the developer-documentation task, covering the message/identity/routing critical path, payroll/payment, escort, RBAC/drafts/outbound, AI/RAG, and ops/scheduling domains. For the other ~37 modules under `modules/`, see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 2 (Module Dependency Matrix), which covers all 63. `modules/contact_sync` and `modules/contact_roles` (the client/contact-identity modules) are covered in detail in `docs/CANONICAL_CLIENT_DOMAIN.md` Task 3 rather than here, to avoid duplicating that document's ER-relationship-map format.

**How to read each entry:** Purpose, Key files, Main APIs, DB tables used, Critical dependencies, Risk level (Critical = touches the cash ledger `fpe_cash_transactions`/`fpe_employee_ledger` or sends real outbound WhatsApp messages; High = on the identity/routing critical path; Medium/Low otherwise), and an explicit writes-money/sends-messages/read-only tag.

---

### `modules.message_router`
- **Purpose:** Single source of truth for inbound WhatsApp message routing — decides, per sender identity (family/escort/admin/supervisor/accountant/candidate/employee/known-contact/unknown), which workflow handles the message and returns `(reply_text, admin_notification)`. Used by both the webhook path (`app/main.py`) and the SQLite bridge path (`modules/bridge_poller`).
- **Key files:** `__init__.py` (1029 lines) — only file in the module.
- **Main APIs (no HTTP routes):** `process_message(...)` (`:387`), `route_message_with_timeout(...)` (`:1008`), `get_primary_admin()` (`:868`), `get_recent_history(phone, limit)` (`:892`), `get_contact_context(phone)` (`:908`), `normalize_incoming_message(body)` (`:979`).
- **DB tables used:** `fazle_contact_roles`, `fazle_draft_replies`, `fpe_employees`, `wbom_contacts`, `wbom_employees`, `wbom_relation_types`, `wbom_whatsapp_messages`.
- **Critical dependencies:** `app.database`, `shared.draft_reply`, `shared.reply_policy`, `modules.identity_brain`, `modules.admin_commands` (+`nl_router`, `nl_advance_record`), `modules.admin_directives.router` (new, 2026-07-30), `modules.escort`, `modules.escort_lifecycle`, `modules.payment_workflow`, `modules.payment_ingest`, `modules.attendance`, `modules.attendance_parser`, `modules.recruitment_flow`, `modules.rag`, `modules.intent`, `modules.fazle_payroll_engine.employee/normalizer`.
- **Risk level:** High — sits at the very top of the routing critical path; a bug here mis-routes every inbound message.
- **Writes money / sends messages / read-only:** Read-only / decision-only — returns text and admin-notification dicts; actual send happens downstream.
- **Update (2026-07-30, Unified Admin AI Engine):** admin branch gained one new check, `is_admin_ai_command(text)` (`modules.admin_directives.router`), inserted after `is_nl_admin_query()` and before the hardcoded-help fallback — additive only, every existing branch above and below it is unchanged. See `docs/ADMIN_AI_ENGINE.md`.

### `modules.admin_commands`
- **Purpose:** Parses and executes admin WhatsApp commands (`APPROVE`, `REJECT`, `EDIT`, `PAID`, `ADVANCE`, `APPROVED`, `DREDIT`, `DREJECT`, `STATUS`, `DRAFTS`, plus NL payroll/report/schedule commands). `APPROVE`/`PAID`/`ESCORTCONFIRM` complete the loop by delivering messages directly via the bridge.
- **Key files:** `__init__.py` (1449 lines), `date_parser.py` (179), `nl_advance_record.py` (253), `nl_employee_stats.py` (137), `nl_outbound.py` (180), `nl_payments.py` (342), `nl_router.py` (215), `nl_search.py` (108).
- **Main APIs (no HTTP routes):** `process_admin_command(text, admin_phone)` (`:233`), `is_admin_command(text)` (`:168`), `list_payment_drafts(admin_phone)` (`:1086`); internal handlers `_cmd_approve`, `_cmd_paid`, `_cmd_escort_confirm`, `_cmd_payroll_*`, `_cmd_report_*`, `_cmd_schedule_*`.
- **DB tables used:** `fazle_draft_replies`, `fazle_payment_drafts`, `fpe_cash_transactions`, `fpe_employees`, `wbom_employees`, `wbom_escort_programs`, `wbom_whatsapp_messages`.
- **Critical dependencies:** `app.bridge` (real send), `app.database`, `modules.fazle_payroll_engine.accounting/employee/models/payment_event`, `modules.payment_ingest`, `modules.payment_workflow`, `modules.payroll`, `modules.attendance`, `modules.draft_approval`, `shared.draft_reply`.
- **Risk level:** Critical — calls `bridge.send(...)` directly (`:582,827,937,1077`) to deliver real WhatsApp messages, and `PAID`/`APPROVED` write into `fpe_cash_transactions`. The RBAC guard around these commands was fixed 2026-07-09 to fail closed (previously fell through to execution on any `rbac.check_permission()` exception — see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`).
- **Writes money / sends messages / read-only:** Both.

### `modules.fazle_payroll_engine`
- **Purpose:** The canonical financial processing subsystem ("FPE"). Ingests WhatsApp payment messages, parses them, matches to employees, and writes verified, immutable transactions to `fpe_cash_transactions`/`fpe_employee_ledger`. Runs its own SQL migrations and 5 background workers at startup (`start_fpe()`/`stop_fpe()`).
- **Key files (~8,700 lines total):** `__init__.py` (73), `routes.py` (3172 — ~39 `/api/fpe/*` endpoints), `accounting.py` (433 — `create_transaction`, `reverse_transaction`), `employee.py` (588 — `match_or_create_employee`), `parser.py` (498), `workers.py` (716 — 5 async workers), `ingestion.py` (218), `normalizer.py` (234 — `normalize_bd_phone`), `models.py` (313), `payment_event.py` (208), `reconcile.py` (150), `normalization.py` (472), `ai_enhancer.py` (89, **quarantined per owner directive 2026-07-02**), `diagnostics.py` (401), `gap_scan.py` (281), `historical_sync.py` (362), `checkpoint.py` (73), `replay.py` (160), `validation.py` (227); `migrations/` (14 SQL files, own independent numbering — see `db/MIGRATION_INVENTORY.md`), `README.md`.
- **Main APIs (`routes.py`, prefix `/api/fpe`):** `GET /health`; `POST /ingest`; `GET/POST /transactions`, `GET /transactions/{id}`, `POST /transactions/{id}/reverse`, `POST /transactions/manual`; `GET/POST /employees`, `GET /employees/search`, `/suggest`, `GET/PATCH /employees/{id}`, `GET /employees/{id}/transactions`; `GET /ledger/{id}`; `GET /unmatched`, `POST /unmatched/{id}/mark-reviewed`; `GET /sync/status`, `POST /sync/trigger`; `GET /stats`; `/normalization/summary|review` + POST actions; `GET /admin/reconcile`, `/admin/needs-review`, `/admin/dlq`, `/admin/gap-scan/trigger|runs`, `/admin/review-summary`, `/admin/salary/summary`, `POST /admin/salary/apply-uniform`, `GET /admin/audit`; `GET /cash`, `/cash/{id}`; `GET /staging-payments`; `GET/POST /income`, `GET /income/{id}`; `POST /operator/submit`, `GET /operator/pending`, `POST /operator/{id}/approve|reject`. All protected by `X-Internal-Key`.
- **DB tables used:** `fpe_employees`, `fpe_cash_transactions`, `fpe_employee_ledger`, `fpe_wa_messages`, `fpe_unmatched_messages`, `fpe_accounting_audit_logs`, `fpe_income_transactions`, `fpe_employee_aliases`, `fpe_parser_results`, `fpe_message_processing_state`, `fpe_sync_checkpoints`, `fpe_gap_scan_runs`, `fpe_processing_diagnostics`, `fpe_review_audit_logs`, `fpe_normalization_audit_logs`, `fpe_employee_resolution_links`, `fpe_employee_review_queue`, plus reads/backfills against `wbom_employees`, `wbom_escort_programs`, `wbom_staging_payments`, `wbom_whatsapp_messages`, `escort_roster_entries`, `escort_roster_change_requests`, `fazle_payment_drafts`, `whatsmeow_lid_map`.
- **Critical dependencies:** `app.bridge`, `app.database`, `app.ollama`, `shared.events`, `shared.queue`, `shared.realtime`, `shared.state_version`, `shared.write_router`.
- **Risk level:** Critical — this module IS the cash ledger.
- **Writes money / sends messages / read-only:** Writes money. Does not itself send messages.

### `modules.payroll`
- **Purpose:** Monthly payroll batch engine — aggregates escort-duty/attendance data into `wbom_payroll_runs` with a state machine (`draft → reviewed → approved → locked → paid`, or `cancelled`), idempotent per employee/period. Plus (2026-07-10/11, Office Payroll Hardening) `eligibility.py`'s centralized office-staff classification and `dry_run.py`'s read-only eligibility-preview/dry-run/reconciliation layer.
- **Key files:** `__init__.py` (compute engine + state machine + `compute_all_for_period_office_only`, new), `eligibility.py` (new — `classify_office_staff`, single source of truth, do not duplicate), `dry_run.py` (new — `eligibility_preview`/`payroll_dry_run`/`reconciliation_report`, all non-mutating).
- **Main APIs:** No `routes.py` of its own for the compute engine; HTTP endpoints live in `app/main.py` (`/payroll/compute`, `/payroll/run/{id}/transition`, `/payroll/runs`, `/payroll/runs/{id}`). The new eligibility/dry-run/reconciliation endpoints live in `modules.attendance.routes` instead (`/admin/payroll/eligibility-preview`, `/dry-run`, `/reconciliation`).
- **DB tables used:** `wbom_payroll_runs`, `wbom_payroll_run_items`, `wbom_payroll_approval_log`, `fpe_cash_transactions`, `wbom_employees`, `wbom_escort_programs`, `wbom_attendance` (new, for the dry-run/reconciliation layer), `fazle_draft_replies` (new, for pending/rejected parser-draft counts).
- **Critical dependencies:** `app.database` only for the compute engine; `dry_run.py` additionally depends on `modules.payroll.eligibility`.
- **Risk level:** Critical — `mark_paid` transitions write against `fpe_cash_transactions`. **Certification finding (2026-07-09), still true:** in production, this state machine has never progressed past `draft`. Its `compute_run`'s internal formula (basic_salary + escort-program day-count allowance) has **not yet** been rewritten to the new calendar-days/attendance-based formula — `compute_all_for_period_office_only` filters by eligibility but still calls the old, un-rewritten `compute_run`. **`job_daily_payroll` (modules.scheduler) is guarded behind `settings.office_payroll_authoritative_enabled` (default False, 2026-07-11)** — while off, the daily job runs `dry_run.payroll_dry_run` only and creates no payroll rows.
- **Writes money / sends messages / read-only:** Writes money (compute engine, guarded). `eligibility.py`/`dry_run.py` are pure read/compute, no mutation ever.

### `modules.payment_workflow`
- **Purpose:** Builds payment drafts for two flows — escort duty release payment and advance-payment requests — and finalizes salary payment after admin approval.
- **Key files:** `__init__.py` (442 lines) — only file.
- **Main APIs (no HTTP routes):** `create_escort_payment_draft(...)` (`:46`), `create_advance_request_draft(...)` (`:231`), `finalize_payment(draft_id, approved_amount, method)` (`:315`), `is_advance_request(text)` (`:439`).
- **DB tables used:** `fazle_payment_drafts`, `fpe_cash_transactions`, `wbom_employees`, `wbom_escort_programs`.
- **Critical dependencies:** `app.database`, `modules.fazle_payroll_engine.accounting/employee/models/payment_event`.
- **Risk level:** Critical — `finalize_payment` writes `fpe_cash_transactions` via FPE's canonical transaction creator. **Formula fixed (Owner decision, 2026-07-10):** `create_escort_payment_draft` previously used `basic_salary/30` (a formula meant for office staff, not escort duty) and subtracted conveyance instead of adding it. Now: flat `DEFAULT_DAILY_RATE` (400tk/day, matching `escort_roster/calculations.py`'s shift-rate model) × `duty_days`, with conveyance added. **Certification finding (2026-07-09), historical context:** before this fix, all 47 `escort_payment` drafts ever created were `status='expired'` — real `PAID` commands bypass this draft's suggested amount entirely (admin types a free amount). See `docs/PRODUCTION_CERTIFICATION_v1.0.md` Finance Status.
- **Writes money / sends messages / read-only:** Writes money. Does not send messages itself (produces drafts).

### `modules.payment_ingest`
- **Purpose:** Parses raw bKash/Nagad SMS or admin-pasted cash instructions, matches to employees, and stages/creates canonical FPE transactions. Per owner directive (2026-06-29), `fpe_cash_transactions` is the only canonical table; `wbom_cash_transactions` is legacy/read-only reference.
- **Key files:** `__init__.py` (570 lines), `fpe_bridge.py` (138), `wbom_fpe_sync.py` (231).
- **Main APIs (no HTTP routes):** `parse_payment_sms(text)` (`:135`), `match_employee(...)` (`:192`), `ingest_payment_sms(...)` (`:245`), `parse_admin_cash_shorthand(text)` (`:445`), `ingest_admin_cash_entry(...)` (`:506`), `create_fpe_transaction_from_ingest(...)` (`fpe_bridge.py:84`), `sync_wbom_transaction(id)` (`wbom_fpe_sync.py:72`).
- **DB tables used:** `fazle_payment_drafts`, `fpe_cash_transactions`, `fpe_employees`, `wbom_employees`, `wbom_staging_payments`; reads (not writes) `wbom_cash_transactions`.
- **Critical dependencies:** `app.database`, `modules.fazle_payroll_engine.accounting/employee/models/normalizer/payment_event`, `shared.phone`.
- **Risk level:** Critical — creates canonical `fpe_cash_transactions` rows.
- **Writes money / sends messages / read-only:** Writes money. Does not send messages.

### `modules.billing` (2026-07-09, Phase 2B Commit A)
- **Purpose:** Invoice numbering only (`AQS-YYYY-NNNNNN`). Client billing profile data lives in `modules.client_billing` (below), kept separate per this codebase's one-module-one-responsibility convention.
- **Key files:** `__init__.py` (module docstring only), `invoice_numbering.py` (43 lines).
- **Main APIs (no HTTP routes yet):** `next_invoice_number(year=None, conn=None)` → `AQS-YYYY-NNNNNN` string.
- **DB tables used:** `invoice_number_counters` (`db/migrations/033_invoice_number_counters.sql`) via the `next_invoice_number()` Postgres function. **Not yet applied to production** (validated only, awaiting a separate Owner approval from the Client Billing Profile work below). Does not yet touch `wbom_billing_records`.
- **Critical dependencies:** `app.database`.
- **Risk level:** Low — a pure counter, no money/client data touched.
- **Writes money / sends messages / read-only:** Neither yet — reserves a number only.

### `modules.client_billing` (new, 2026-07-09, Phase 2B — implemented and applied)
- **Purpose:** Client Settings — per-client billing rates (General/24H/12H/Monthly), deployed-employee-wage estimates, VAT/Tax/Other Charges (flat, profit-estimation-only this phase), and commercial contact details (Contact Person/Chairman/Managing Member). The revenue-side counterpart to every cost/payroll module on this page — explicitly disconnected from the real payroll formula.
- **Key files:** `__init__.py` (docstring), `profiles.py` (CRUD, ~185 lines), `routes.py` (FastAPI router, ~130 lines).
- **Main APIs:** `GET/POST /admin/client-billing-profiles`, `GET/PUT/DELETE /admin/client-billing-profiles/{client_id}`, `GET /admin/escort-clients/authorized-numbers` (read-only view of the `ESCORT_CLIENT_PHONES` list, for the Contact Person Mobile dropdown).
- **DB tables used:** `wbom_client_billing_profiles` (new, `db/migrations/034_client_billing_profiles.sql`, **applied to production**), reads `wbom_contacts` (FK validation) and the live `ESCORT_CLIENT_PHONES` `.env` value (via `modules.escort_roster.routes`'s existing helpers, reused not duplicated — never writes to it).
- **Critical dependencies:** `app.database`, `modules.escort_roster.routes` (`_require_key`, `_resolve_actor`, `_normalize_client_phone`, `_escort_client_phone_list`, `_API_KEY_HEADER`).
- **Risk level:** Medium — commercially sensitive rate/wage data, but no money movement (no write path to `fpe_cash_transactions` or any ledger exists here or anywhere downstream yet).
- **Writes money / sends messages / read-only:** Writes commercial configuration data, not money. Does not send messages.
- **RBAC:** Add/Edit/List/View at `admin` tier; Delete (soft — `is_active=false`, never a hard `DELETE`) at `superadmin` tier.

### `modules.admin_transactions`
- **Purpose:** Admin-facing CRUD API (Phase 19) for `fpe_cash_transactions` — add/edit/soft-delete transactions plus smart fuzzy employee matching. Never hard-deletes; uses `deleted_at`/`deleted_by`.
- **Key files:** `__init__.py` (851 lines) — only file.
- **Main APIs (`APIRouter(prefix="/api/admin")`):** `POST /api/admin/login` (`:226`), `GET /api/admin/session` (`:281`), `GET /api/admin/accounting-audit` (`:287`), `POST /api/admin/transactions` (`:572`), `PUT /api/admin/transactions/{txn_id}` (`:620`), `DELETE /api/admin/transactions/{txn_id}` (`:740`), `DELETE /api/admin/income/{income_id}` (`:799`). All mutation routes require `X-Internal-Key`.
- **DB tables used:** `fazle_admins`, `fpe_accounting_audit_logs`, `fpe_cash_transactions`, `fpe_employees`, `fpe_income_transactions`.
- **Critical dependencies:** `app.config`, `app.database`, `modules.fazle_payroll_engine.accounting/employee/models/normalizer/payment_event`.
- **Risk level:** Critical — direct CRUD on `fpe_cash_transactions`. **Note:** the edit (`PUT`, line 698) and soft-delete (`DELETE`, line 764) routes do a direct `UPDATE fpe_cash_transactions` outside `create_transaction()` — flagged as a Constitution boundary case (`SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 3 §1, marked INVESTIGATE, needs an Owner ruling on whether post-creation edit/delete counts as a "path to cash ledger" requiring `BUSINESS-CONSTITUTION.md` Article II documentation).
- **Writes money / sends messages / read-only:** Writes money. Does not send messages.

### `modules.admin_employees`
- **Purpose:** Admin-facing CRUD API (Phase 18B) for `wbom_employees` — safe add/edit/deactivate. `employee_mobile` is immutable; deactivation propagates (non-fatally) to `fpe_employees`.
- **Key files:** `__init__.py` (397 lines) — only file.
- **Main APIs (`APIRouter(prefix="/api/admin")`):** `POST /api/admin/employees` (`:125`), `GET /api/admin/employees` (`:178`), `GET /api/admin/employees/by-phone/{phone}` (`:241`), `GET /api/admin/employees/{emp_id}` (`:257`), `PUT /api/admin/employees/{emp_id}` (`:284`), `PATCH /api/admin/employees/{emp_id}/deactivate` (`:341`), `GET /api/admin/debug/auth-check` (`:384`). Mutations require `X-Internal-Key`.
- **DB tables used:** `fpe_cash_transactions`, `fpe_employees`, `wbom_attendance`, `wbom_employees`, `wbom_escort_programs`, `wbom_salary_records`.
- **Critical dependencies:** `app.config`, `app.database`, `modules.fazle_payroll_engine.employee/normalizer`.
- **Risk level:** High — on the identity/employee-master-data critical path; not itself a primary money-writer.
- **Writes money / sends messages / read-only:** Read/write employee master data; no money, no sends. **Note:** this is one of two employee-creation paths that still write `wbom_employees` as primary rather than `fpe_employees` — see `docs/DEVELOPER_GUIDE.md` §F.

### `modules.escort`
- **Purpose:** Parses inbound vessel-escort client orders (MV/lighter/master mobile), saves draft `wbom_escort_programs` rows, builds admin drafts, and — once the admin completes the draft — finalizes and routes a slip to the client.
- **Key files:** `__init__.py` (1929 lines), `README.md` (121 lines).
- **Main APIs (no HTTP routes):** `parse_escort_message(text)` (`:756`), `build_admin_draft(...)` (`:816`), `is_completed_escort_draft(text)` (`:893`), `parse_completed_draft(text)` (`:907`), `build_final_slip(...)` (`:992`), `save_escort_programs(...)` (`:1024`).
- **DB tables used:** `wbom_escort_programs`, `wbom_employees`, `wbom_contacts`, `fpe_employees`.
- **Critical dependencies:** `app.database`, `modules.escort_roster.db`, `modules.fazle_payroll_engine.normalizer`, `modules.message_router`, `modules.number_identity`.
- **Risk level:** High — on the identity/routing critical path for client order intake; deny-listed as a "protected path" in `modules/preflight_guard`.
- **Writes money / sends messages / read-only:** Read/write program data only — no cash writes, no direct sends.

### `modules.escort_roster`
- **Purpose:** Projection/cache layer over `wbom_escort_programs` for escort roster management — computes shifts/pay, provides dashboard/API, and reconciliation tooling.
- **Key files:** `__init__.py` (22 lines — re-exports), `db.py` (1745), `routes.py` (814 — ~34 endpoints), `history_sync.py` (955), `extractor.py` (403), `calculations.py` (157), `README.md` (173).
- **Main APIs (`APIRouter(prefix="/api/escort-roster")`):** `GET /summary`, `/active`, `/export`, `GET/POST /config`, `GET/POST /escort-clients`, `DELETE /escort-clients/{phone}`, `GET/POST /sync-all`, `POST /match-slip`, `GET/POST ""` (list/create), `POST /sync-history`, `/rebuild-history`, `GET /drafts`, `POST /cleanup-drafts`, `/cleanup-empty-drafts`, `/cleanup-junk-drafts`, `GET /audit-exact-duplicates`, `POST /cleanup-exact-duplicates`, `/reconcile`, `/backfill-sqlite`, `/backfill-files`, `GET /slip-queue`, `POST /slip-queue/{id}/confirm|reject`, `GET/PATCH/DELETE /{program_id}`, `POST /{program_id}/recalculate|sync`, `GET /change-requests`, `POST /change-requests/{id}/approve|reject`. All behind `X-Internal-Key`.
- **DB tables used:** `escort_roster_entries`, `escort_roster_audit_logs`, `escort_roster_change_requests`, `escort_calculation_config`, `escort_order_groups`, `escort_order_lighters`, `escort_release_matches`, `escort_shift_logs`, `escort_slip_extractions`, `fazle_payment_drafts`, `wbom_billing_records`, `wbom_cases`, `wbom_cash_transactions` (read-only), `wbom_employees`, `wbom_escort_programs`, `wbom_whatsapp_messages`.
- **Critical dependencies:** `app.config`, `app.database`, `modules.escort`, `modules.fazle_payroll_engine.normalizer`.
- **Risk level:** High — deny-listed as a protected path in `preflight_guard`; large surface over escort financial-adjacent data, but transactional cash writes flow through FPE, not here. **Note:** own docstring says "DO NOT import from this module" to avoid circular deps, yet `routes.py` itself imports a private symbol from `modules.escort` — see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 1 §3.
- **Writes money / sends messages / read-only:** Read/write roster & program projections; no cash writes, no message sends.

### `modules.escort_lifecycle`
- **Purpose:** Closes the escort duty loop — detects release intent (text/OCR), closes `wbom_escort_programs`, backfills attendance, and creates the escort payment draft. All operations idempotent.
- **Key files:** `__init__.py` (827 lines), `README.md` (44 lines).
- **Main APIs (no HTTP routes):** `is_release_intent(text)` (`:44`), `find_active_program_for_employee(...)` (`:53`), `close_program(...)` (`:88`), `backfill_attendance_for_program(...)` (`:152`), `handle_release_event(...)` (`:220`), `handle_admin_release_confirmation(...)` (`:616`), `handle_ocr_release_slip(...)` (`:736`).
- **DB tables used:** `escort_roster_entries`, `fazle_payment_drafts`, `wbom_attendance`, `wbom_employees`, `wbom_escort_programs`.
- **Critical dependencies:** `app.database`, `modules.escort_roster.calculations`, `modules.payment_workflow`, `shared.draft_reply`.
- **Risk level:** High — deny-listed as protected path in `preflight_guard`. **Two known open issues:** (1) writes `escort_roster_entries` directly via a raw `INSERT ... ON CONFLICT` (`:315-343`) instead of calling `escort_roster`'s own sync API; (2) this bypass means two independent pay-calculation formulas can feed the same table (this module's write uses `payment_workflow`'s formula, `escort_roster.sync_program_to_roster` uses `escort_roster/calculations.py`'s own flat-rate formula) — unresolved which is authoritative, see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 3 §7.
- **Writes money / sends messages / read-only:** Read/write (program + attendance + payment drafts); does not directly write `fpe_cash_transactions` or send messages.

### `modules.attendance`
- **Purpose:** Two things: (1) WhatsApp self-report attendance ("হাজির"/"Present") → admin-approval draft → `wbom_attendance`; (2) the shared attendance-finalization service (`finalize_attendance_draft`, Owner decision 2026-07-11) used by BOTH the WhatsApp `APPROVE <id>` command and the frontend Review Queue's approve button for `intent='attendance'` drafts, so the two channels cannot drift.
- **Key files:** `__init__.py` (attendance-message handling + `finalize_attendance_draft`), `crud.py` (new, 2026-07-11 — manual Office Attendance CRUD + structured `wbom_employees` lookup), `routes.py` (new, 2026-07-11 — `/admin/attendance*` + `/admin/payroll/*` endpoints, admin+/superadmin RBAC).
- **Main APIs:** `GET/POST /admin/attendance`, `GET/PUT/DELETE /admin/attendance/{id}`, `GET /admin/attendance/lookup/by-id/{employee_id}`, `GET /admin/attendance/lookup/suggest`, `GET /admin/payroll/eligibility-preview`, `GET /admin/payroll/dry-run`, `GET /admin/payroll/reconciliation`. Non-HTTP: `is_attendance_message`, `handle_attendance_message`, `save_attendance` (extended with `entry_source`/`approval_channel`/`approved_by`/`source_draft_id`, migration 040), `finalize_attendance_draft`.
- **DB tables used:** `wbom_attendance` (+5 new audit columns, migration 040), `wbom_employees`, `fazle_draft_replies`.
- **Critical dependencies:** `app.database`, `shared.draft_reply`, `modules.escort_lifecycle` (`mark_program_running_on_attendance`), `modules.escort_roster.routes` (`_resolve_actor`, RBAC reuse).
- **Risk level:** Medium — no direct cash-table writes; does gate what counts as office-payroll duty data (Owner Decision 2 governs which `entry_source` values `modules.payroll.dry_run` may read).
- **Writes money / sends messages / read-only:** Read/write attendance records only; no money, no direct sends.
- **Known limitation:** the `confirmed -> running` transition (escort side) has no other trigger besides first accepted attendance — a date-only fallback was explicitly rejected by the Owner, so a guard who never sends/gets an attendance approval never transitions their program to `running`.

### `modules.recruitment_flow`
- **Purpose:** Async recruitment funnel — conversational intake (name → age → area → job preference → experience → phone confirmation), tracked per-phone in `fazle_recruitment_sessions` with a 24h session TTL.
- **Key files:** `__init__.py` (412 lines) — only file.
- **Main APIs (no HTTP routes):** `intake_message(phone, message, source)` (`:203`), `is_recruitment_trigger(text)` (`:305`), `get_active_session(phone)` (`:316`), `recruitment_eligibility(...)` (`:337`), `route_recruitment_message(...)` (`:368`).
- **DB tables used:** `fazle_recruitment_sessions` (falls back gracefully if the table doesn't exist yet).
- **Critical dependencies:** `app.database`, `modules.conversation_layer`, `modules.recruitment_ai`.
- **Risk level:** Low — self-contained conversational state, no money or protected-path involvement.
- **Writes money / sends messages / read-only:** Read/write session state only; no money, no direct sends.

### `modules.identity_brain`
- **Purpose:** Unified identity-detection engine — determines "who is messaging" using a strict priority order (admin → family → accountant → vip_client → client_escort_buyer → repeat_client → vendor → employee → supervisor → candidate → unknown), checking seed rules, employee table, escort roster, contacts, then text-hint heuristics.
- **Key files:** `__init__.py` (438 lines) — only file.
- **Main APIs (no HTTP routes):** `detect_identity(phone, text)` (`:80`), plus internal `_lookup_seed`, `_lookup_employee`, `_lookup_cash_identity`, `_lookup_attendance_identity`, `_lookup_escort_roster`, `_lookup_contact`.
- **DB tables used:** `fazle_contact_roles`, `fpe_cash_transactions` (read-only), `fpe_employees`, `wbom_attendance`, `wbom_contacts`, `wbom_employees`, `wbom_escort_programs`.
- **Critical dependencies:** `app.config`, `app.database` only.
- **Risk level:** High — THE identity/routing critical path; every downstream workflow decision depends on its output.
- **Writes money / sends messages / read-only:** Read-only.

### `modules.rbac`
- **Purpose:** Role-based access control and audit — manages `fazle_roles`/`fazle_admins`/`fazle_admin_roles`, enforces command-level required-role checks via `check_permission`, auto-bootstraps `ADMIN_NUMBERS` env phones as superadmin, and logs every command attempt to `fazle_admin_audit`.
- **Key files:** `__init__.py` (420 lines) — only file.
- **Main APIs (no HTTP routes):** `check_permission(...)` (`:219`), `record_audit(...)` (`:265`), `get_admin_by_phone/api_key/username(...)` (`:128-149`), `add_admin(...)` (`:305`), `set_role(...)` (`:354`), `revoke_role(...)` (`:371`), `disable_admin/enable_admin(...)` (`:382-390`), `issue_api_key(...)` (`:398`).
- **DB tables used:** `fazle_admin_audit`, `fazle_admin_roles`, `fazle_admins`, `fazle_roles`.
- **Critical dependencies:** `app.database`, `modules.phone_normalizer`.
- **Risk level:** High — gates authorization for every admin/financial command. No bug found in this module itself; the fail-open bug found and fixed 2026-07-09 was in one of its callers (`admin_commands`), not here.
- **Writes money / sends messages / read-only:** Read/write auth/audit records only; no money, no sends.

### `modules.drafts`
- **Purpose:** Draft-dashboard API — lists pending drafts, shows stats, and lets an operator approve/edit/soft-delete/block a draft from the web dashboard (distinct from the WhatsApp `APPROVE`/`EDIT` admin commands).
- **Key files:** `routes.py`; `__init__.py` is empty.
- **Main APIs (`APIRouter(tags=["drafts"])`):** `GET /api/drafts/stats` (`:62`), `GET /api/drafts` (`:78`), `POST /api/drafts` (`:134`, create — added 2026-08-10, Hermes Capability Expansion Level 1; external entry point wrapping `shared.draft_reply.create_draft_reply()`, never sends), `POST /api/drafts/{id}/approve` (`:173`), `POST /api/drafts/{id}/edit` (`:261`), `POST /api/drafts/{id}/delete` (`:331`). Protected by `X-Internal-Key`. (`/block` was permanently removed 2026-07-11, migration 042 — this line previously still listed it in error.)
- **DB tables used:** `fazle_contact_roles`, `fazle_draft_replies`, `wbom_contacts`.
- **Critical dependencies:** `app.config`, `app.database`, `modules.outbound` (approve action calls `outbound.enqueue`).
- **Risk level:** Critical — `/approve` directly triggers real outbound sends. **Note:** this router's mounting in `app/main.py` was found dropped by in-flight uncommitted work and restored 2026-07-09 — verify a restart has happened since, or the fix isn't live yet. **Real bug fixed (2026-07-11):** `/approve` was intent-agnostic and never called `save_attendance()` — approving an `intent='attendance'` draft from this dashboard silently marked it approved and sent the reply without ever saving to `wbom_attendance`. Now delegates to `modules.attendance.finalize_attendance_draft()` for that intent specifically (same shared service the WhatsApp `APPROVE <id>` command uses); every other intent's behavior is unchanged.
- **Writes money / sends messages / read-only:** Sends messages (via outbound enqueue on approve); no cash writes.

### `modules.outbound`
- **Purpose:** Persistent outbound message queue — `enqueue()` with idempotency-key dedup, `sweep_once()` polls due rows and sends via the bridge with exponential-backoff retries to a dead-letter queue, plus a circuit breaker that alerts admin when opened.
- **Key files:** `__init__.py` (255 lines) — only file.
- **Main APIs (no HTTP routes):** `enqueue(...)` (`:36`), `pending_count()`/`dlq_count()`/`actionable_dlq_count()` (`:65-83`), `sweep_once(limit)` (`:139`), `start_background_worker(...)`/`stop_background_worker()` (`:237-246`).
- **DB tables used:** `fazle_outbound_queue`.
- **Critical dependencies:** `app.bridge`, `app.database`, `app.error_log`, `modules.social_auto_reply.send_queue` (for meta/messenger channels — a duplicate-infrastructure concern, see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 3 §4).
- **Risk level:** Critical — directly calls `client.send_strict(recipient, body)` delivering real WhatsApp/Meta messages.
- **Writes money / sends messages / read-only:** Sends messages. No money writes.

### `modules.bridge_poller`
- **Purpose:** Polls three bridge SQLite stores every 5s (read-only), resolving LID→phone, deduping, persisting all DM inbound to `wbom_whatsapp_messages` (groups/newsletters/status-broadcasts skipped by policy), then invoking the router/draft/AI pipeline. Uses a Postgres cursor table so restarts resume correctly.
- **Key files:** `__init__.py` (1829 lines) — only file, no README.
- **Main APIs (no HTTP routes):** `start_pollers()` (`:1248`), `process_bridge_inbound(...)` (`:1600`), `_poll_bridge(config)` (`:704`), `init_tables()` (`:115`).
- **DB tables used:** `bridge_poller_cursor`, `fazle_draft_replies`, `fazle_message_queue`, `fazle_service_heartbeats`, `outbound_safety_incidents`, `processed_bridge_messages`, `processed_outgoing_escort_messages`, `wbom_whatsapp_messages`, `whatsmeow_lid_map`.
- **Critical dependencies:** `app.bridge`, `app.config`, `app.database`, `modules.message_router`, `modules.identity_brain`, `modules.escort`, `modules.escort_lifecycle`, `modules.escort_slip_extractor`, `modules.intent`, `modules.ocr_processor`, `modules.voice_processor`, `modules.outbound`, `modules.recruitment_flow`, `modules.social_auto_reply`, `modules.draft_quality`, `shared.draft_reply`, `shared.queue`.
- **Risk level:** Critical — deny-listed as protected path in `preflight_guard`; the entry point that persists every real inbound WhatsApp DM and (via drafts/outbound) can result in real outbound sends.
- **Writes money / sends messages / read-only:** Reads bridge SQLite (never writes to it) and writes/sends via Postgres + downstream outbound queue; no direct cash-ledger writes.
- **Update (2026-07-30):** `_is_draft_always(phone, role, display_name)` is now `async def` (was sync) — it checks a live DB flag (`fazle_runtime_settings` key `phone_block.{phone}`, via `modules.wa_chat_frontend.get_runtime_setting()`) before its pre-existing static `DRAFT_ALWAYS_PHONES`/role/name-prefix checks, so the per-number auto-reply block button (`POST /api/wa/contacts/{phone}/block`) actually takes effect (previously wrote a flag nothing read). **Process note:** this function has a second caller outside this module — `modules.social_auto_reply.planner_worker._is_draft_always_sender()` — found only after a full-suite regression run caught it missing its `await`; see `docs/DEVELOPER_GUIDE.md` §L for the lesson (grep repo-wide, not file-local, before converting any function to async).
- **Update (2026-08-04, superseded later same day):** bridge3 (REC2, 8801805529211) was added to `BRIDGE_CONFIGS` with Admin-reply-only reachability for the `AI <question>`/Hermes capability. **Reversed later the same day**: bridge3 is no longer an Admin identity (its number removed from `ADMIN_NUMBERS`/`admin_bridge3_number` check) — it remains fully wired as a technical bridge (transport, health tracking, outbound dispatch, loop-guard all unchanged) but carries no admin privilege, so the `AI <question>`/Hermes-via-bridge3 path no longer functions as an admin channel. Bridge2 (8801880446111) is the canonical Super Admin identity. See `knowledge_base/00_governance/management_decisions.md` ("Bridge3 → Hermes Integration..." and the later "Bridge3 Admin Reversal + Bridge2 Super Admin + CSV Contact-Book Rebuild" entry, same day).

### `modules.social_auto_reply`
- **Purpose:** Backend extension handling social-media (Meta/Messenger/comments) inbound events end-to-end — classification, employee lookup, risk flagging, reply generation, rate-limited send queue with retry, and a planner/daemon worker loop.
- **Key files (21 files, ~2,323 lines):** `__init__.py` (231), `send_queue.py` (247 — actual sends), `intelligent_generator.py` (288), `reply_rules.py` (213), `salary_flow.py` (282), `employee_lookup.py` (142), `planner_worker.py` (116), `backlog_processor.py` (98), `daemon_worker.py` (82), `conversation_history.py` (81), `risk_flagger.py` (74), `state_tracker.py` (76), `classifier.py` (104), `rate_limiter.py` (50), `reply_generator.py` (70), `routes.py` (65), `message_deduplicator.py` (17), `retry_queue.py` (22), `comment_handler.py` (16), `payment_issue_handler.py` (12), `service_runner.py` (37).
- **Main APIs (`APIRouter(prefix="/api/social")`):** `GET /api/social/status` (`:31`), `GET /queue` (`:36`), `GET /flagged` (`:41`), `POST /pause` (`:46`), `POST /resume` (`:52`), `POST /retry` (`:58`); non-HTTP entry points `ingest_social_event`, `start_social_auto_reply`, `stop_social_auto_reply`.
- **DB tables used:** `fazle_service_heartbeats`, `fpe_cash_transactions` (read-only), `fpe_employees`, `social_backlog_state`, `social_flagged_items`, `social_inbox_events`, `social_rate_limit_state`, `social_reply_queue`, `social_retry_queue`, `social_sent_log`, `social_thread_state`, `wbom_employees`, `wbom_escort_programs`.
- **Critical dependencies:** `app.bridge`, `app.config`, `app.database`, `app.error_log`, `app.ollama`.
- **Risk level:** Critical — `send_queue.py` calls `bridge.send_strict(...)` and makes direct `httpx` POST calls to Meta/Messenger APIs — real outbound sends. **Note:** its own queue/retry/dedup/rate-limiter infrastructure duplicates `modules.outbound`'s — see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 3 §4. Its router mounting was also found dropped and restored 2026-07-09, same as `drafts`.
- **Writes money / sends messages / read-only:** Sends messages. No cash-ledger writes (reads `fpe_cash_transactions` only for salary-flow context).

### `modules.ai_console`
- **Purpose:** "AI Operating Console" — registry-driven orchestration layer routing operator/AI questions to mock or live adapters (business data, runtime health, evidence), with a model router and a pre-production acceptance-test runner.
- **Key files (~2,958 lines across 9 files):** `live_adapters.py` (1103), `acceptance_runner.py` (653), `orchestrator.py` (286), `registry.py` (250), `provider_registry.py` (165), `mocks.py` (165), `model_router.py` (158), `adapters.py` (84), `envelopes.py` (65), `__init__.py` (29).
- **Main APIs (no HTTP routes of its own; consumed by `app/main.py`'s chat/console handler):** `orchestrate_console_request(...)` (`orchestrator.py:178`), `orchestrate_mock_request(...)` (`:82`), `classify_message(message)` (`:50`), `load_all_registry_entries()` (`registry.py:219`), `PreProductionAcceptanceRunner` class.
- **DB tables used:** None found via direct SQL grep — queries through adapters/other modules rather than raw SQL itself. Not verified further beyond grep.
- **Critical dependencies:** `app.config`, `app.database`, `app.main`, `shared.bridge_orchestrator`, `shared.runtime_gateway`.
- **Risk level:** Medium — read/orchestration layer; no direct SQL writes or message sends found. `adapters.py` explicitly declares `prohibited_side_effects` per adapter class (business writes, unrestricted SQL, WhatsApp sends) — enforced by construction, live path only instantiates read-only adapter classes.
- **Writes money / sends messages / read-only:** Read-only.
- **Update (2026-07-30, Unified Admin AI Engine):** 3 new tools added, additive only (0 existing entries/methods modified — confirmed via diff, 0 deletions in either file):
  - `RT-015` (`error_log`, `registry.py`) — reads `fazle_error_log` via the new `modules.observability.db_queries.get_recent_errors()`, handled by `ReadonlyRuntimeAdapter._error_log_lookup()`. **Its `min_role: office_assistant` metadata is descriptive only** — `ReadonlyRuntimeAdapter.fetch()` has no per-tool RBAC check at all (confirmed by signature: no `actor_role_level` param), unlike the business adapter below. All `RT-*` tools currently share this limitation, not something newly introduced.
  - `BUS-015` (`admin_directive_read`, `min_role: admin`) and `BUS-016` (`admin_directive_write`, `min_role: admin`, `authority: admin_directive_service` — deliberately not the `*_readonly_*` naming every other `BUS-*` entry uses, since this one genuinely writes) — both on `ReadonlyBusinessAdapter`, calling `modules.admin_directives`. `BUS-016` is the **first** `ai_console` business-family tool that performs a write — scoped exclusively to the narrow, non-Critical `fazle_admin_directives` table, never any table another `BUS-*` tool touches, and RBAC-gated identically to every other business tool via the existing `fetch()` mechanism (no new enforcement code needed).
  - See `docs/ADMIN_AI_ENGINE.md` and `knowledge_base/06_developer_system/admin_ai_engine.md` for full detail.

### `modules.ai_readonly_tools`
- **Purpose:** Safe, named read-only tool functions letting Ollama/Chat Lab query production data — enforces SELECT-only access through approved `ai_read_*` views, row limits (max 100), and field redaction; uses a dedicated `fazle_ai_reader` DB role when configured.
- **Key files:** `__init__.py` (846 lines — all logic); `attendance.py`, `contacts.py`, `duties.py`, `messages.py`, `payroll.py`, `recruitment.py`, `system_status.py`, `vessels.py` (thin re-export stubs, ~4 lines each).
- **Main APIs (no HTTP routes):** `get_contact_summary(phone)` (`:97`), `get_contacts_list(...)` (`:112`), `get_recent_messages(...)` (`:129`), `get_payment_summary(...)` (`:150`), `get_escort_program_status(...)` (`:183`), `get_attendance_summary(...)` (`:219`), `get_payroll_run_status(...)` (`:245`), `get_daily_payments(question)` (`:282`), `get_employee_month_payments(question)` (`:409`), `get_recruitment_leads(...)` (`:561`), `fetch_web_page(url)` (`:603` — read-only GET only), `run_tool(tool_name, question)` dispatcher (`:752`).
- **DB tables used:** `ai_read_attendance_summary`, `ai_read_contacts`, `ai_read_employees`, `ai_read_escort_programs`, `ai_read_kb_articles`, `ai_read_module_bridge_status`, `ai_read_payroll_runs`, `ai_read_recent_messages`, `ai_read_recruitment_leads` (approved views), plus direct reads of `fpe_cash_transactions`, `fpe_employees`, `wbom_employees`, `wbom_escort_programs`.
- **Critical dependencies:** `app.config`, `app.database`, `modules.admin_commands.date_parser` (a coupling into a Critical business module for a generic utility — minor, noted in the consolidation report).
- **Risk level:** Low — hard-constrained to SELECT-only by its own docstring.
- **Writes money / sends messages / read-only:** Read-only.

### `modules.admin_directives` (new, 2026-07-30, Unified Admin AI Engine)
- **Purpose:** Narrow admin directive/action memory — lets an admin explicitly save/revoke a standing instruction ("remember always confirm advances over 5000tk"), and dispatches the 5 new WhatsApp admin AI commands. Deliberately separate from `modules.ollama_memory` (the AI's own isolated self-memory) — this module stores admin-authored content, never AI-inferred content, and every write here is human-triggered.
- **Key files:** `__init__.py` (132 lines — CRUD: `save_directive`/`list_directives`/`get_directive`/`revoke_directive`), `router.py` (327 lines — WhatsApp command dispatch: `is_admin_ai_command`/`process_admin_ai_command`, the AI-query rate limiter, output sanitizer, phone normalizer). Kept as two files, and kept out of `modules.admin_commands.nl_router`, because `nl_router`'s own docstring is a hard "NO LLM" contract and this module's `AI` command does call one.
- **Main APIs (no HTTP routes of its own beyond `app/main.py`'s thin `/admin/directives*` wrappers — see below):** `save_directive(...)`, `list_directives(...)`, `get_directive(id)`, `revoke_directive(id, revoked_by_phone=...)` (`__init__.py`); `is_admin_ai_command(text)`, `process_admin_ai_command(text, sender_phone)` (`router.py`).
- **DB tables used:** `fazle_admin_directives` (`db/migrations/059_admin_directives.sql`, CHECK-constrained `category`/`created_by_channel`/`status`).
- **Critical dependencies:** `app.database`, `modules.rbac` (RBAC + audit on every WhatsApp command), `modules.wa_chat_frontend` (`get_runtime_setting`/`set_runtime_setting` for `AUTOREPLY`), `modules.ai_console.orchestrator` (`AI <question>` forwards into `orchestrate_console_request()`), `shared.draft_reply` (`SEND` creates a draft only).
- **Risk level:** Medium — write-capable (unlike almost every other AI-Console-adjacent module), but every write is gated by `rbac.check_permission()` (fail-closed) and audited; `SEND` never sends directly, only drafts via the existing human-approval queue.
- **Writes money / sends messages / read-only:** Neither money nor direct sends — writes only `fazle_admin_directives` and `fazle_runtime_settings`; `SEND` writes `fazle_draft_replies` (a draft, not a send).
- **Related HTTP routes:** `GET /admin/directives`, `POST /admin/directives`, `POST /admin/directives/{id}/revoke` (`app/main.py`, `require_command("admin_directive_read"/"admin_directive_write")`).

### `modules.rag`
- **Purpose:** Lightweight offline Retrieval-Augmented Generation layer — bilingual (Bangla/English) BM25 + optional vector hybrid search over `resources/*.txt` files and `fazle_knowledge_base` rows, no external network calls during query.
- **Key files:** `__init__.py` (1214 lines) — only file.
- **Main APIs (no HTTP routes):** `build_index()` (`:717`), `ensure_index()` (`:779`), `search(q, k, min_score, role)` (`:1028`), `answer(q, k, min_score)` (`:1107`), `stats()` (`:1149`), `rebuild_index()` (`:1155`), `recent_searches()` (`:1212`).
- **DB tables used:** `fazle_knowledge_base` (read-only).
- **Critical dependencies:** `app.database` only.
- **Risk level:** Low — read-only knowledge layer. **Note:** runs entirely independently of `modules.knowledge_base`'s exact-match lookup path — the two don't coordinate, see `docs/DEVELOPER_GUIDE.md` §H.
- **Writes money / sends messages / read-only:** Read-only.

### `modules.kb_upload`
- **Purpose:** Knowledge-base upload API — accepts PDF/DOCX/TXT/MD/CSV files, parses and inserts entries into `fazle_knowledge_base`, and provides list/stats/delete admin operations.
- **Key files:** `routes.py` (284 lines), `parser.py` (116 lines); `__init__.py` is empty.
- **Main APIs (`APIRouter(tags=["kb_upload"])`):** `POST /admin/kb/upload` (`:48`), `GET /admin/kb/stats` (`:167`), `GET /admin/kb/list` (`:187`), `POST /admin/kb/delete` (`:246`). Protected by `X-Internal-Key`.
- **DB tables used:** `fazle_knowledge_base`.
- **Critical dependencies:** `app.config`, `app.database`, `modules.kb_upload.parser`, `modules.rag` (rebuilds index after changes).
- **Risk level:** Low — no cash/identity/routing critical-path involvement.
- **Writes money / sends messages / read-only:** Read/write KB content only; no money, no sends.

### `modules.scheduler`
- **Purpose:** APScheduler-based cron job runner — daily payroll compute (02:00 Asia/Dhaka), DLQ alerts (15 min), health summaries (6h), stale-escort reminders (09:00), payment reconciliation (hourly), backup-staleness alerts (03:00), daily memory review (09:00), RAG rebuild (18:00). Every job run is logged to `fazle_scheduled_jobs` and updates the `scheduler` heartbeat.
- **Key files:** `__init__.py` (721 lines) — only file.
- **Main APIs (no HTTP routes):** `start_scheduler()` (`:502`), `stop_scheduler()` (`:670`), `register_job(...)` (`:118`), `trigger_job(name)` (`:122`), `get_status()` (`:680`), `list_job_names()` (`:720`), plus job functions `job_daily_payroll`, `job_dlq_alert`, `job_health_summary`, `job_stale_escort_reminder`, `job_payment_reconciliation`, `job_backup_staleness`, `job_daily_memory_review`, `job_rag_rebuild`.
- **DB tables used:** `fazle_draft_replies`, `fazle_payment_drafts`, `fazle_reconciliation_log`, `fazle_reminders_sent`, `fazle_scheduled_jobs`, `fazle_service_heartbeats`, `wbom_employees`, `wbom_escort_programs`, `wbom_staging_payments`, `wbom_whatsapp_messages`.
- **Critical dependencies:** `app.config`, `app.database`, `app.error_log`, `app.main`, `modules.backup`, `modules.payroll`, `modules.message_router`, `modules.escort_roster.db`, `modules.memory_extractor`, `modules.reports`, `shared.queue`, `shared.locks`.
- **Risk level:** Critical — several jobs call `outbound.enqueue(...)` to send real admin alert messages. **`job_daily_payroll` is now guarded (Owner decision, 2026-07-11):** behind `settings.office_payroll_authoritative_enabled` (default False), it runs `modules.payroll.dry_run.payroll_dry_run` only and creates no `wbom_payroll_runs` rows — it does not call `compute_all_for_period`/`compute_all_for_period_office_only` while the flag is off.
- **Writes money / sends messages / read-only:** Both (indirectly, via payroll compute + alert sends).

### `modules.backup`
- **Purpose:** Database backup/disaster-recovery — runs `pg_dump` inside the `ai-postgres` container, records metadata in `fazle_db_backups`, and rotates old backups (keep N daily + M weekly).
- **Key files:** `__init__.py` (288 lines) — only file.
- **Main APIs (no HTTP routes):** `run_backup()` (`:82`), `rotate_backups(...)` (`:151`), `latest_backup()` (`:204`), `list_backups(limit)` (`:213`), `backup_status()` (`:223`), `job_daily_db_backup()` (`:244`), `render_status_text(s)` (`:266`).
- **DB tables used:** `fazle_db_backups`.
- **Critical dependencies:** `app.database`, `modules.scheduler`.
- **Risk level:** Medium — operationally important but doesn't touch cash tables or send messages. Confirmed actually running: 18+ consecutive daily dumps on disk as of the 2026-07-08 audit.
- **Writes money / sends messages / read-only:** Read/write backup metadata only; no money, no sends (shells out to `pg_dump`).

### `modules.observability`
- **Purpose:** In-memory Prometheus-style metrics store (counters/gauges/histograms) — `obs.snapshot()`/`render_prometheus()` used by `app/main.py`'s `/observability/summary`/`/metrics` routes. Not previously listed in this document (one of the ~37 modules this file's own scope note defers to `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 2 for) — added here now only for its one new file below.
- **Key files:** `__init__.py` (pre-existing, pure in-memory, no DB access at all — confirmed by import inspection); **`db_queries.py` (new, 2026-07-30, 51 lines)** — one function, `get_recent_errors(limit=50)`, a read-only `SELECT` against `fazle_error_log`. Deliberately a separate file, not added to `__init__.py`, since that module's whole design is DB-free in-memory counters and a DB query would be a scope mismatch there.
- **Main APIs relevant here:** `get_recent_errors(limit=50)` (`db_queries.py`) — duplicates the same query `GET /observability/errors` (`app/main.py`) already ran inline in its route handler, so `modules.ai_console.live_adapters`'s new `RT-015` tool has something cleanly importable to call (a FastAPI route handler isn't reusable as a plain function).
- **DB tables used:** `fazle_error_log` (read-only). Confirmed **not** in `modules.preflight_guard.PROTECTED_TABLES` or its prefix list.
- **Critical dependencies:** `app.database` (new, `db_queries.py` only).
- **Risk level:** Low — single read-only SELECT, capped and clamped (`max(1, min(limit, 500))`), no write path.
- **Writes money / sends messages / read-only:** Read-only.

### `modules.preflight_guard`
- **Purpose:** Safety-gate module — deny-lists protected file paths/tables from unreviewed changes, runs pre-release checks, manages feature-flag kill switches, and exposes release-gate + feature-flag + device-location admin endpoints.
- **Key files:** `__init__.py` (235 lines — guard logic), `routes.py` (252 lines — HTTP layer).
- **Main APIs (`APIRouter(tags=["preflight_guard"])`):** `POST /api/assistant/release/preflight` (`:65`), `GET /api/assistant/release/gates/{release_id}` (`:97`), `POST /api/assistant/release/gate/update` (`:115`), `GET /api/assistant/release/preflight/latest` (`:166`), `POST /api/assistant/ops/kill-switch/{feature_name}` (`:198`), `POST /api/assistant/ops/activate/{feature_name}` (`:213`), `GET /api/assistant/ops/flags` (`:228`), `GET /api/assistant/internal/location/employee/{employee_id}` (`:237`), `GET /api/assistant/internal/location/device/{device_id}` (`:243`), `GET /api/assistant/internal/location/health` (`:249`). Protected by `X-Internal-Key`.
- **DB tables used:** `assistant_feature_flags`, `assistant_preflight_reports`, `assistant_release_gates`.
- **Critical dependencies:** `app.config`, `app.database`, `modules.location_adapter`.
- **Risk level:** High — governs the deny-list/kill-switch protecting the actual Critical modules (`fazle_payroll_engine`, `payroll`, `escort_roster`, `escort_lifecycle`, `escort`, `bridge_poller`, `app/main.py` are all in `PROTECTED_PATH_PATTERNS`; `fpe_cash_transactions`/`fpe_employee_ledger`/`wbom_employees`/`wbom_whatsapp_messages` are in `PROTECTED_TABLES`). Its own protected-path list is hardcoded as strings — would silently stop protecting a module if renamed/moved without updating the list.
- **Writes money / sends messages / read-only:** Read/write feature-flag and preflight-report state only; no money, no sends.

---

## Notes on methodology / uncertainty

- Modules `payroll`, `message_router`, `admin_commands`, `payment_workflow`, `payment_ingest`, `escort`, `escort_lifecycle`, `attendance`, `recruitment_flow`, `identity_brain`, `rbac`, `outbound`, `bridge_poller`, `ai_console`, `ai_readonly_tools`, `rag`, `scheduler`, `backup` have **no `routes.py`/router of their own** (confirmed by absence of `APIRouter(` in their directories) — their functions are called either from `app/main.py` directly or from other modules.
- `ai_console`'s own DB table usage was not found via direct SQL grep — it appears to delegate actual data access to adapter modules/`ai_readonly_tools`. Flagged "Not verified beyond grep" rather than asserting zero DB usage.
- All "sends real outbound WhatsApp messages" claims are backed by grep hits for `bridge.send`, `client.send_strict`, or direct `httpx` POST calls to bridge/Meta endpoints; modules that only build draft text/admin-notification dicts are marked read/write-only, not senders.
- Risk levels follow the rubric literally: Critical = touches `fpe_cash_transactions`/`fpe_employee_ledger` directly OR sends real messages; High = identity/routing critical path (including `preflight_guard`'s own protected-path list as corroborating evidence); Medium/Low assigned by elimination with rationale given per module.
- For the other ~37 modules not listed here (recruitment_ai, contact_sync, wa_chat_frontend, reports, observability, etc.), see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 2, which covers all 63 modules in the codebase.
