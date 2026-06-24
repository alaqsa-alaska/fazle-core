---
title: PKCA Report 12: Admin Command Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 12: Admin Command Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Command System Overview

The Fazle AI platform has 38 admin commands across 8 command groups. Commands are dispatched from admin WhatsApp contacts via bridge1. All commands require RBAC verification.

**Source:** `modules/admin_commands/__init__.py`
**RBAC Source:** `modules/rbac`
**KB Coverage:** `02_admin_knowledge/admin_operations_overview.md` — partial (~25%)

---

## Group 1: Draft Management Commands

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| APPROVE | `APPROVE <id>` or `APPROVE <id> <id> ...` | operator | Load draft → send to recipient → mark sent | 20% — mentioned in admin_operations_overview.md |
| REJECT | `REJECT <id>` | operator | Mark draft rejected | 20% |
| EDIT | `EDIT <id> <new text>` | operator | Replace draft text | 10% |
| STATUS | `STATUS` | viewer | Show pending draft count | 10% |
| DRAFTS | `DRAFTS [page]` | viewer | List pending drafts paginated | 0% |

**Group Coverage: ~12%**

---

## Group 2: Attendance Commands

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| ATTENDANCE | (inbound employee message) → triggers draft | operator | Parse text → build attendance draft | 30% — attendance_workflow.md documents this |
| APPROVE (attendance draft) | `APPROVE <attendance_draft_id>` | operator | Save to wbom_attendance (ON CONFLICT UPDATE) | 30% |

**Group Coverage: 30%**

---

## Group 3: Payment Commands

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| PAID | `PAID <draft_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant | Finalize payment → notify accountant | 20% — payment_workflow.md partial |
| ADVANCE | `ADVANCE <draft_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant | Finalize advance → notify accountant | 20% |
| REJECT (payment) | `REJECT <draft_id>` | operator | Reject payment draft | 20% |

**Group Coverage: 20%**

---

## Group 4: Escort Commands

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| ESCORTCONFIRM | `ESCORTCONFIRM <id>\|<name>\|<mobile>\|<date>\|<shift>` | operator | Assign escort to program (confirmed status) | 25% — escort_workflow.md partial |
| ESCORTCANCEL | `ESCORTCANCEL <program_id> <reason>` | operator | Cancel escort program | 5% |
| ESCORTLIST | `ESCORTLIST [status]` | viewer | List programs by status | 5% |
| ESCORTDETAIL | `ESCORTDETAIL <id>` | viewer | Show program full detail | 0% |

**Group Coverage: 9%**

---

## Group 5: Payroll Commands

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| PAYROLL COMPUTE | `PAYROLL COMPUTE <YYYY-MM> [employee_id]` | accountant | Compute payroll run for period | 0% |
| PAYROLL SUBMIT | `PAYROLL SUBMIT <run_id>` | accountant | draft → reviewed transition | 0% |
| PAYROLL APPROVE | `PAYROLL APPROVE <run_id>` | accountant | reviewed → approved transition | 0% |
| PAYROLL LOCK | `PAYROLL LOCK <run_id>` | accountant | approved → locked transition | 0% |
| PAYROLL PAID | `PAYROLL PAID <run_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant | locked → paid transition | 0% |
| PAYROLL CANCEL | `PAYROLL CANCEL <run_id> <reason>` | accountant | → cancelled from any non-paid state | 0% |
| PAYROLL LIST | `PAYROLL LIST <YYYY-MM> [status]` | viewer | List payroll runs for period | 0% |

**Group Coverage: 0%**

---

## Group 6: Report Commands

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| REPORT DAILY | `REPORT DAILY` | viewer | Daily operations summary | 5% — admin_operations_overview.md mentions reports |
| REPORT PAYROLL | `REPORT PAYROLL <YYYY-MM>` | viewer | Payroll report for month | 5% |
| REPORT CASH | `REPORT CASH [days]` | viewer | Cash flow report | 5% |
| REPORT RECON | `REPORT RECON [days]` | viewer | Payment reconciliation report | 0% |
| REPORT ESCORT | `REPORT ESCORT <start> <end>` | viewer | Escort program report for date range | 0% |
| REPORT LIST | `REPORT LIST` | viewer | List available report types | 0% |

**Group Coverage: 2.5%**

---

## Group 7: Backup Commands

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| BACKUP STATUS | `BACKUP STATUS` | viewer | Show last backup age and status | 0% |
| BACKUP NOW | `BACKUP NOW` | admin | Trigger immediate pg_dump backup | 0% |
| BACKUP LIST | `BACKUP LIST [n]` | viewer | List recent n backups | 0% |

**Group Coverage: 0%**

---

## Group 8: User / RBAC Commands

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| USER ADD | `USER ADD <phone> <name> [role]` | superadmin | Create admin user (default role: viewer) | 5% |
| USER ROLE | `USER ROLE <phone> <role>` | superadmin | Change admin user role | 5% |
| USER REMOVE | `USER REMOVE <phone>` | superadmin | Disable admin user (soft-disable) | 0% |
| USER LIST | `USER LIST` | viewer | List all active admins | 0% |
| USER APIKEY | `USER APIKEY <phone>` | superadmin | Issue API key (SHA-256 stored) | 0% |

**Group Coverage: 2%**

---

## Group 9: Scheduler Commands (via wa_chat_frontend — NEW)

| Command | Syntax | Required Role | Action | KB Coverage |
|---|---|---|---|---|
| SCHEDULE STATUS | `SCHEDULE STATUS` | viewer | Show all 15 scheduled job statuses | 0% |
| RUN JOB | `RUN JOB <job_name>` | admin | Manually trigger a scheduled job | 0% |

**Group Coverage: 0%**

---

## Global Command Features Not in KB

| Feature | Description | KB Coverage |
|---|---|---|
| Deduplication | SHA1(text+phone), 30-second TTL, 256 entries — prevents double-command | 0% |
| Bangla digit support | APPROVE ১৬৫ ১৬৬ is equivalent to APPROVE 165 166 | 0% |
| Multi-ID APPROVE | APPROVE 165 166 167 approves all three in one command | 0% |
| RBAC enforcement | Every command checks COMMAND_ROLE[cmd] ≤ user.role | 0% |
| Audit trail | Every command logged to fazle_admin_audit (phone, cmd, status, error) | 0% |

---

## Command Coverage Summary

| Group | Commands | Coverage |
|---|---|---|
| Draft management | 5 | 12% |
| Attendance | 2 | 30% |
| Payment | 3 | 20% |
| Escort | 4 | 9% |
| Payroll | 7 | 0% |
| Reports | 6 | 2.5% |
| Backup | 3 | 0% |
| User/RBAC | 5 | 2% |
| Scheduler | 2 | 0% |
| **Total** | **37** | **~8%** |

**Average Command KB Coverage: ~8%**

## Enrichment Target

**`02_admin_knowledge/admin_operations_overview.md`** — Add complete command reference table with syntax, required role, and action for all 37 commands. No new article needed; this is the authoritative admin command home.
