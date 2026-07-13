---
title: Cleanup Candidates (2026-07-13 audit)
owner: Fazle Core Admin
status: active
last_verified: 2026-07-13
---

# Cleanup Candidates — 2026-07-13

**This is a list only. No file listed below has been deleted, moved, or edited by
this pass.** ARCHIVE means "safe to move to an archive location later, on
Owner approval." DELETE means "content is fully superseded/dead; safe to
remove later, on Owner approval — nothing currently reads it." Verified
against the live server on 2026-07-13: grep of `app/main.py` for router
mounts, KB frontmatter (`status:`/`last_verified:`), and git file dates.
Scope: `/home/azim/core` root, `docs/`, `knowledge_base/`.

## Category A — Stale Audit/Status Reports (root)

One-time, dated snapshots superseded by `docs/SYSTEM_STATE_SUMMARY_2026-07-11.md`,
`docs/NEXT_IMPLEMENTATION_QUEUE.md`, and `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`.

- ARCHIVE: `AUDIT_REPORT.md` — 2026-07-08 audit snapshot; findings since actioned or superseded by the queue doc.
- ARCHIVE: `audit-report-2026-06-01.md` — superseded by later audits.
- ARCHIVE: `audit-report-2026-06-10.md` — superseded by later audits.
- ARCHIVE: `CHAOS_TEST_PLAN.md` — one-time chaos-testing exercise plan, exercise already run.
- ARCHIVE: `PRE_CHAOS_CHECKLIST.md` — pre-check for the same completed exercise.
- ARCHIVE: `POST_CHAOS_VALIDATION.md` — post-check for the same completed exercise.
- ARCHIVE: `DEPLOYMENT_SUMMARY.md` — 2026-07-08 point-in-time deployment snapshot.
- ARCHIVE: `TESTING_STATUS.md` — 2026-07-08 point-in-time snapshot; current testing conventions now live in `docs/DEVELOPER_GUIDE.md` §K.
- ARCHIVE: `SPRINT-3A-OWNER-REPORT.md` — historical sprint report.
- ARCHIVE: `SPRINT-3B-OWNER-REPORT.md` — historical sprint report.
- ARCHIVE: `PHASE_1_FG4_EXECUTION_SHEET_2026-07-05.md` — one-time execution sheet, phase closed.
- ARCHIVE: `STRICT_EXECUTION_CHECKLIST_PREFILLED_2026-07-05.md` — filled instance of a one-time checklist.
- ARCHIVE: `STRICT_EXECUTION_CHECKLIST_TEMPLATE.md` — template only used for the above; no current consumer found via grep.
- ARCHIVE: `payroll-escort-workflow-audit-2026-06-13.md` — superseded by 2026-07-04 version below.
- ARCHIVE: `payroll-escort-workflow-audit-2026-07-04.md` — audit findings already actioned (Phase 2B Commit D, escort fixes this session).
- ARCHIVE: `payroll-escort-workflow-fix-plan-2026-07-04.md` — fix plan for the above; fixes already shipped.
- ARCHIVE: `recruitment-audit-2026-06-15.md` — one-time audit of a module now confirmed dead (see Category B).
- ARCHIVE: `GIT_SNAPSHOT.md` — point-in-time git state snapshot, git log is the live source of truth.
- ARCHIVE: `RECOVERY.md` — check content before archiving: if this documents a **generic recovery procedure** (not tied to one incident), move to KEEP instead — verify manually.
- ARCHIVE: `BACKUP.md` — check content before archiving: if this documents the **current backup procedure**, move to KEEP instead — verify manually.
- ARCHIVE: `MASTER_PLAN_KNOWLEDGE_BASE_BRAIN.md` — superseded by `knowledge_base/00_governance/kb_certification.md` (KB v2.0 CERTIFIED).

## Category A — Stale Audit/Status Reports (docs/)

- ARCHIVE: `docs/FAZLE_CORE_DEEP_RESEARCH_REPORT.md` — dated 2026-05-06, full-codebase audit long since superseded.
- ARCHIVE: `docs/RELEASE_STATUS_SINGLE_SOURCE.md` — dated 2026-07-04; superseded by `docs/SYSTEM_STATE_SUMMARY_2026-07-11.md` and this session's queue updates. **Caution:** filename claims "single source" — confirm nothing still links to it as canonical before archiving.
- ARCHIVE: `docs/WORKSPACE_REVIEW_2026-04-29.md` — dated April 29, oldest doc in the chain below.
- ARCHIVE: `docs/WORKSPACE_IMPLEMENTATION_PLAN_2026-04-29.md` — plan derived from the above review, execution complete.
- ARCHIVE: `docs/WORKSPACE_P0_EXECUTION_BACKLOG_2026-04-29.md` — backlog derived from the above plan, execution complete (batch B26 shipped, see next item).
- ARCHIVE: `docs/B26_REVIEWED_REPLY_MEMORY_DESIGN.md` — design doc for `reviewed_reply_memory`, feature confirmed live (`knowledge_base/06_developer_system/reviewed_reply_memory.md` status: active).
- ARCHIVE: `docs/B26_REVIEWED_REPLY_MEMORY_IMPLEMENTATION_CHECKLIST.md` — checklist for the same shipped feature.
- ARCHIVE: `docs/V1_LAUNCH_CHECKLIST.md` — v1.0 already launched (git tag `v1.0-consolidation-baseline` cut 2026-07-09); checklist's job is done.

## Category B — Dead Code Docs

- ARCHIVE (or DELETE on Owner call): `knowledge_base/06_developer_system/recruitment_ai_detail.md` — verified `modules/recruitment_ai` is **not imported anywhere in `app/main.py`** (only `modules.recruitment_flow` is live, confirmed via grep). This doc describes a module with no live route/import. Note: do not touch `knowledge_base/06_developer_system/recruitment_flow_system.md` — that describes the live `recruitment_flow` module and should stay KEEP.
- Note (not a file to move): `modules/recruitment_ai/` itself and `tests/unit/test_recruitment_ai_restricted.py` are dead code too, but this task is docs-only — flagging here for a future dedicated dead-code-removal pass, not acting on it now.

## Category C — Duplicate/Redundant Files

- ARCHIVE: `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md` — its own header already says "Status, updated 2026-07-09: fully implemented, applied, and verified live." A shipped-feature's plan doc kept only for history.
- ARCHIVE: `docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md` — companion plan doc to the above, same status.
- REVIEW (not auto-categorized): `docs/README.md` vs root `README.md` — two differently-scoped README files exist (`docs/README.md` and root `README.md`). Confirm `docs/README.md` isn't a stale duplicate entry point before the next doc pass; root `README.md` is confirmed canonical (this task refreshed it).
- REVIEW: `docs/DASHBOARD_IMPLEMENTATION_BACKLOG.md` / `docs/DASHBOARD_URL_SPEC.md` — `app/static/dashboard.html` already exists and is live (referenced in this session's README auth-mechanism audit), so these planning docs may already be superseded by the shipped page. Not marked ARCHIVE outright because backlog ordering may still be tracking unbuilt sub-pages — needs a content read to confirm, out of scope for this pass.

## Category D — Safe to Archive (knowledge_base/ governance artifacts)

The KB has its own archive directory (`knowledge_base/07_archived/`) already in active use and well organized — no action needed there. The items below are **top-level `knowledge_base/*.md` files** (not inside any numbered section), all dated `last_verified: 2026-06-24`, all one-time process/audit artifacts from the KB v1→v2 certification effort that produced `knowledge_base/00_governance/kb_certification.md` (KB v2.0 CERTIFIED — see project memory). Now that certification is the authoritative outcome record, these intermediate working reports are historical:

- ARCHIVE: `knowledge_base/duplicate_report.md`
- ARCHIVE: `knowledge_base/missing_report.md`
- ARCHIVE: `knowledge_base/gap_report.md`
- ARCHIVE: `knowledge_base/enrichment_report.md`
- ARCHIVE: `knowledge_base/conflict_resolution_record.md`
- ARCHIVE: `knowledge_base/ai_access_matrix.md`
- ARCHIVE: `knowledge_base/knowledge_inventory.md`
- ARCHIVE: `knowledge_base/production_knowledge_report.md`
- ARCHIVE: `knowledge_base/PKM_KE_KBG_Program_v2_Analysis_2026-06-21.md`
- ARCHIVE: `knowledge_base/PKM_KE_KBG_Program_v2_Analysis_2026-06-21_DETAILED.md`
- ARCHIVE: `knowledge_base/PKM_KE_KBG_Program_v2_Analysis_2026-06-21_BOARD_READY.md`
- ARCHIVE: `knowledge_base/PKVC_Program_v1_Analysis_2026-06-21.md`
- ARCHIVE: `knowledge_base/KBTI_v1_PreExecution_Deliverables_20260621.md`

Same reasoning applies to these `knowledge_base/00_governance/*.md` process/audit docs (the certification and canonical rules are the outcome; these are the intermediate working papers that produced it):

- ARCHIVE: `knowledge_base/00_governance/claude_session3_audit_report.md`
- ARCHIVE: `knowledge_base/00_governance/session4_audit_report_2026_06_23.md`
- ARCHIVE: `knowledge_base/00_governance/organizational_brain_gap_report.md`
- ARCHIVE: `knowledge_base/00_governance/module_alignment_report.md`
- ARCHIVE: `knowledge_base/00_governance/kb_enrichment_plan_v2.md`
- ARCHIVE: `knowledge_base/00_governance/master_execution_plan_v2.md`
- ARCHIVE: `knowledge_base/00_governance/phase1_rag_analysis.md`
- ARCHIVE: `knowledge_base/00_governance/phase1_5_hybrid_rag_design.md`

**KEEP** (do not touch) from `00_governance/`: `kb_certification.md`, `final_management_directive.md`, `management_decisions.md`, `active_development_plan.md`, `project_context.md`, `kb_policy.md`, `README.md`, `CANONICAL_BUSINESS_RULES.md` — these are the live governance/canonical outcome docs, not working papers.

**Frontmatter inconsistency noted, no action needed:** files under `knowledge_base/07_archived/legacy_02_admin_system/` and `knowledge_base/07_archived/legacy_03_developer_system/` carry `status: active` in their own frontmatter despite physically living in the archive tree. They are already out of the live KB's numbered-section structure, so no runtime impact — flagging only in case a future frontmatter-driven tool trusts the in-file `status:` field over directory location.

## Confirmed KEEP (no action, listed for completeness)

Root: `README.md`, `SYSTEM_ATLAS.md`, `ARCHITECTURE.md`, `BUSINESS-CONSTITUTION.md`,
`PHONE_NORMALIZER_CONVENTION.md`, `HR_OFFICER_SETUP.md`,
`SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` (current, owner-approved Phase 1 roadmap
per [[project_fazle_core_os_roadmap]]).

`docs/`: `API.md`, `docs/ARCHITECTURE.md`, `CANONICAL_CLIENT_DOMAIN.md`,
`CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md`, `DEPLOYMENT_RESTART_GATE.md`,
`DEVELOPER_GUIDE.md`, `MODULE_MAP.md`, `NEXT_IMPLEMENTATION_QUEUE.md`,
`OPERATIONS.md`, `ROADMAP.md`, `SYSTEM_STATE_SUMMARY_2026-07-11.md`,
`BILL_REVISION_POLICY_DESIGN.md` (still open, no Owner decision yet),
`PHASE2B_READINESS_REPORT.md`, `PHASE2_ENTRY_GATE.md`,
`PRODUCTION_CERTIFICATION_v1.0.md`.

`knowledge_base/`: everything under `01_employee_knowledge/`, `02_admin_knowledge/`,
`02_admin_system/`, `03_ai_identity/`, `04_business_rules/`, `05_workflows/`,
`06_developer_system/` (except `recruitment_ai_detail.md`, see Category B), and
the `00_governance/` KEEP list above. All of `07_archived/` is already correctly
archived and needs no further action.

## Summary Counts

| Category | Count |
|---|---|
| A — stale audits/status (root) | 20 |
| A — stale audits/status (docs/) | 8 |
| B — dead code docs | 1 |
| C — duplicate/redundant | 2 confirmed + 2 flagged for review |
| D — KB governance working papers | 21 |
| **Total flagged** | **~52** |

Nothing in this list has been moved or deleted. Next step, if approved: create
`docs/archive/` (or reuse `knowledge_base/07_archived/` convention for a root-level
equivalent) and move Category A/D items there in a single reviewable commit.
