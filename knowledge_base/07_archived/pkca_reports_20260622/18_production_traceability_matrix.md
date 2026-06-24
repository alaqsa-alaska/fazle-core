---
title: PKCA Report 18: Production Traceability Matrix
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 18: Production Traceability Matrix

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Format

KB Article → Production Module → Key Function → Database Table → Coverage

---

## 01_employee_knowledge/

| KB Article | Production Module | Key Function | DB Table | Coverage |
|---|---|---|---|---|
| attendance_policy.md | `modules/attendance` | `save_attendance()` | `wbom_attendance` | 40% |
| company_identity.md | `app/config.py` | — | — | 30% |
| faq_employee.md | `modules/employee_verification` | `run_verification_step()` | `fazle_draft_replies` | 15% |
| leave_policy.md | `modules/attendance` | Derived from attendance rules | `wbom_attendance` | 20% |
| recruitment_policy.md | `modules/recruitment_flow` | `_parse_age`, `VALID_POSITIONS` | `fazle_recruitment_sessions` | 30% — CONFLICT BR-25 |
| release_slip.md | `modules/escort_slip_extractor` | `extract_slip()` | N/A (OCR) | 20% |
| salary_policy.md | `modules/payroll`, `modules/payment_workflow` | `get_payroll_summary()`, `create_escort_payment_draft()` | `wbom_payroll_runs`, `fazle_payment_drafts` | 25% |
| transport_allowance.md | `modules/escort_lifecycle` | `_TRANSPORT_RATES` | `wbom_escort_programs` | 5% |

---

## 02_admin_knowledge/

| KB Article | Production Module | Key Function | DB Table | Coverage |
|---|---|---|---|---|
| admin_attendance_handling.md | `modules/admin_commands` | `_cmd_approve()`, `_cmd_reject()` | `wbom_attendance`, `fazle_draft_replies` | 30% |
| admin_operations_overview.md | `modules/admin_commands`, `modules/rbac` | `dispatch_command()` | `fazle_admin_audit`, `fazle_admins` | 25% |
| admin_payment_handling.md | `modules/admin_commands`, `modules/payment_workflow` | `_cmd_paid()`, `finalize_payment()` | `fazle_payment_drafts`, `wbom_cash_transactions` | 25% |
| admin_role_management.md | `modules/rbac` | `ensure_bootstrap_admins()`, `add_admin()` | `fazle_admins`, `fazle_roles`, `fazle_admin_roles` | 20% |

---

## 03_ai_identity/

| KB Article | Production Module | Key Function | DB Table | Coverage |
|---|---|---|---|---|
| accountant_identity.md | `modules/identity_brain` | Resolution step 3 | `fazle_contact_roles` | 30% |
| admin_identity.md | `modules/identity_brain`, `modules/rbac` | Resolution step 1 | `fazle_admins`, `fazle_contact_roles` | 40% |
| candidate_identity.md | `modules/recruitment_flow` | `SESSION_TTL`, session lookup | `fazle_recruitment_sessions` | 30% |
| employee_identity.md | `modules/identity_brain` | Resolution step 8 + 4 secondary | `wbom_employees`, `wbom_cash_transactions`, `wbom_attendance`, `escort_roster_entries`, `wbom_contacts` | 40% |
| escort_identity.md | `modules/identity_brain`, `modules/escort` | Escort sub-role of employee | `escort_roster_entries` | 30% |
| family_identity.md | `modules/identity_brain` | Resolution step 2 | `fazle_contact_roles` | 30% |
| identity_overview.md | `modules/identity_brain` | Full resolution algorithm | All identity tables | 15% |
| permission_matrix.md | `modules/rbac`, `modules/bridge_poller` | COMMAND_ROLE, _is_draft_always | All | 10% |
| response_rules.md | `app/message_router` | 15-step routing | N/A | 15% |
| vip_identity.md | `modules/identity_brain` | Resolution step 4 | `fazle_contact_roles` | 30% |

---

## 04_business_rules/

| KB Article | Production Module | Key Function | DB Table | Coverage |
|---|---|---|---|---|
| ai_response_rules.md | `app/message_router`, `app/bridge_poller` | `_should_silent_skip()`, `_SAFE_AUTOSEND_INTENTS`, `_is_draft_always()` | — | 10% |
| attendance_business_rules.md | `modules/attendance_parser`, `modules/attendance` | `parse_attendance()`, `save_attendance()` | `wbom_attendance` | 40% — best covered article |
| cash_business_rules.md | `modules/payment_ingest`, `modules/accountant_summary` | `looks_like_payment_sms()`, `detect_accounting_summary()` | `wbom_cash_transactions` | 20% |
| escort_business_rules.md | `modules/escort_lifecycle` | `_TRANSPORT_RATES`, `_calc_duty_days()`, `build_release_draft()` | `wbom_escort_programs` | 21% |
| joining_business_rules.md | `modules/recruitment_flow` | `_compute_score()` | `fazle_recruitment_sessions` | 25% |
| payment_business_rules.md | `modules/payment_workflow` | `create_escort_payment_draft()`, `ADVANCE_KEYWORDS` | `fazle_payment_drafts`, `wbom_cash_transactions` | 27% |
| recruitment_business_rules.md | `modules/recruitment_flow`, `modules/recruitment_ai` | `_parse_age()`, `VALID_POSITIONS`, `_looks_like_fee_question()` | `fazle_recruitment_sessions` | 44% — but BR-25 conflict |
| salary_business_rules.md | `modules/payroll`, `modules/payroll_logic` | `compute_run()`, `get_payroll_summary()` | `wbom_payroll_runs`, `wbom_payroll_run_items` | 20% |

---

## 05_workflows/

| KB Article | Production Module | Key Function | DB Table | Coverage |
|---|---|---|---|---|
| attendance_workflow.md | `modules/attendance_parser`, `modules/attendance`, `modules/admin_commands` | parse→draft→approve | `fazle_draft_replies`, `wbom_attendance` | 40% |
| cash_workflow.md | `modules/payment_ingest`, `modules/accountant_summary` | `looks_like_payment_sms()` | `wbom_cash_transactions` | 20% |
| client_order_workflow.md | `modules/escort` | `parse_escort_order()` | `wbom_escort_programs` | 25% |
| escort_workflow.md | `modules/escort`, `modules/escort_lifecycle` | `parse_escort_order()`, `build_release_draft()` | `wbom_escort_programs`, `escort_roster_entries`, `fazle_payment_drafts` | 30% |
| payment_workflow.md | `modules/payment_workflow`, `modules/admin_commands` | `create_escort_payment_draft()`, `_cmd_paid()` | `fazle_payment_drafts`, `wbom_cash_transactions` | 25% |
| recruitment_workflow.md | `modules/recruitment_flow`, `modules/recruitment_ai` | Full 7-step session | `fazle_recruitment_sessions` | 30% |
| release_slip_workflow.md | `modules/escort_slip_extractor`, `modules/escort_lifecycle` | `extract_slip()`, `parse_release_confirmation()` | — | 20% |
| salary_workflow.md | `modules/payroll`, `modules/admin_commands` | `compute_run()`, PAYROLL commands | `wbom_payroll_runs`, `wbom_payroll_approval_log` | 20% |

---

## 06_developer_system/

| KB Article | Production Module | Key Function | DB Table | Coverage |
|---|---|---|---|---|
| automation_pipeline.md | `app/bridge_poller`, `app/message_router`, `modules/outbound` | Full pipeline | All | 10% |
| conversation_parser.md | `modules/intent`, `modules/attendance_parser` | Intent + attendance parse | — | 10% |
| database_rules.md | All modules | Schema rules | All 43 tables | <1% |
| developer_notes.md | `app/config.py` | Config settings | — | 15% |
| event_pipeline.md | `app/bridge_poller`, `modules/social_auto_reply` | Bridge poll + social pipeline | — | 10% |
| hybrid_search.md | `modules/rag` | BM25 search | `fazle_knowledge_base` | 10% |
| identity_brain.md | `modules/identity_brain`, `modules/role_classifier` | Resolution + prompt injection | All identity tables | 20% |
| ocr_engine.md | `modules/escort_slip_extractor`, `modules/media_normalization` | OCR + slip extraction | — | 15% |
| parser_engine.md | All parser modules | 15 parsers | — | 10% |
| rag_strategy.md | `modules/rag` | BM25 algorithm + chunking | `fazle_knowledge_base` | 15% |
| role_permissions.md | `modules/rbac`, `modules/admin_commands` | RBAC + command roles | `fazle_roles`, `fazle_admin_roles`, `fazle_admins` | 15% |
| security_rules.md | `app/bridge_poller`, `modules/rbac` | All security gates | `outbound_safety_incidents` | 10% |
| system_prompt.md | `modules/role_classifier`, `app/llm.py` | System prompt injection | — | 5% |
| visibility_rules.md | `modules/rbac` | Visibility levels | — | 15% |
| workflow_engine.md | `app/message_router`, `modules/drafts` | Routing + draft engine | `fazle_draft_replies` | 10% |

---

## Traceability Gaps (Knowledge Without KB Coverage)

| Knowledge Area | Production Module | No KB Article | Action |
|---|---|---|---|
| Scheduler (15 jobs) | `modules/scheduler` | No scheduler article | Add to automation_pipeline.md |
| FPE (5 workers) | `modules/fazle_payroll_engine` | No FPE article | Create fpe_overview.md |
| wa_chat_frontend (25 endpoints) | `modules/wa_chat_frontend` | No frontend article | Add to developer_notes.md |
| Social auto-reply (20 files) | `modules/social_auto_reply` | No social article | Create social_auto_reply_system.md |
| Outbound queue | `modules/outbound` | No outbound article | Add to automation_pipeline.md |
| Memory extractor | `modules/memory_extractor` | No memory article | Add to automation_pipeline.md |
| Contact sync | `modules/contact_sync` | No contact article | Add to database_rules.md |
| Backup system | `modules/backup` | No backup article | Add to developer_notes.md |
| Observability | `modules/observability` | No observability article | Add to developer_notes.md |
| Payment correction (DORMANT) | `modules/payment_correction` | No article (dormant) | Note in developer_notes.md |
