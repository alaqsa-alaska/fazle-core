# Admin Operations Overview

## Purpose
Central admin-facing source of truth for operating the Fazle AI Platform without exposing internal logic to employees, candidates, or clients.

## Scope
Visible to Admin, HR, Operation Officer, Supervisor, Accountant, and Management.

## Operating Model
The system is role-routed, not domain-routed. Every incoming message must be resolved to a role or sub-role before the AI decides whether it can answer, draft, verify, escalate, or stay silent.

## Admin Responsibilities
- Review attendance drafts before attendance becomes final.
- Review escort order drafts before client confirmation.
- Handle payment messages and accountant handoff.
- Resolve unclear identity and unknown-number cases.
- Maintain employee, candidate, client, vendor, and role records.
- Approve exceptions for joining fee, advance, payment, and duty cases.

## Business Rules
- Candidate FAQ can auto-send when intent is clear.
- Sensitive/internal or unclear messages must route to admin/manual review.
- Payment completion occurs when admin's payment message reaches accountant WhatsApp.
- Ledger/database update follows accountant handoff.
- Frontend edits to role, permission, employee, client, or workflow records must update the backend source records immediately.

## Cross References
- ../03_ai_identity/permission_matrix.md
- ../04_business_rules/payment_business_rules.md
- ../05_workflows/payment_workflow.md
- ../06_developer_system/database_rules.md

## Revision History
- 2026-06-19: Created as role-centric admin source of truth.
- 2026-06-22: Enriched with complete command reference, RBAC roles, and scheduler commands (Wave-1).

---

## Complete Admin Command Reference

### Purpose
All 37 WhatsApp admin commands, their syntax, required RBAC role, and action.

**RBAC hierarchy:** `viewer < operator < accountant < admin < superadmin`

**Global command features:**
- Bangla digits accepted: `APPROVE ১৬৫` = `APPROVE 165`
- Multi-ID APPROVE: `APPROVE 165 166 167` approves all three in one command
- Command dedup: same command within 30 seconds from same sender is silently ignored
- All commands logged to `fazle_admin_audit`

---

### Group 1: Draft Management

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| APPROVE | `APPROVE <id>` or `APPROVE <id> <id> ...` | operator | Load draft → send to recipient → mark sent |
| REJECT | `REJECT <id>` | operator | Mark draft rejected |
| EDIT | `EDIT <id> <new text>` | operator | Replace draft text |
| STATUS | `STATUS` | viewer | Show pending draft count |
| DRAFTS | `DRAFTS [page]` | viewer | List pending drafts paginated |

---

### Group 2: Payment Commands

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| PAID | `PAID <draft_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant | Finalize payment → notify accountant |
| ADVANCE | `ADVANCE <draft_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant | Finalize advance → notify accountant |
| REJECT | `REJECT <payment_draft_id>` | operator | Reject payment draft |

---

### Group 3: Escort Commands

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| ESCORTCONFIRM | `ESCORTCONFIRM <id>\|<name>\|<mobile>\|<date>\|<shift>` | operator | Assign escort to program |
| ESCORTCANCEL | `ESCORTCANCEL <program_id> <reason>` | operator | Cancel escort program |
| ESCORTLIST | `ESCORTLIST [status]` | viewer | List programs by status |
| ESCORTDETAIL | `ESCORTDETAIL <id>` | viewer | Show program full detail |

---

### Group 4: Payroll Commands

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| PAYROLL COMPUTE | `PAYROLL COMPUTE <YYYY-MM> [employee_id]` | accountant | Compute payroll run |
| PAYROLL SUBMIT | `PAYROLL SUBMIT <run_id>` | accountant | draft → reviewed |
| PAYROLL APPROVE | `PAYROLL APPROVE <run_id>` | accountant | reviewed → approved |
| PAYROLL LOCK | `PAYROLL LOCK <run_id>` | accountant | approved → locked |
| PAYROLL PAID | `PAYROLL PAID <run_id> <amount> bkash\|nagad\|cash [ref=X]` | accountant | locked → paid |
| PAYROLL CANCEL | `PAYROLL CANCEL <run_id> <reason>` | accountant | → cancelled |
| PAYROLL LIST | `PAYROLL LIST <YYYY-MM> [status]` | viewer | List payroll runs for period |

---

### Group 5: Report Commands

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| REPORT DAILY | `REPORT DAILY` | viewer | Daily operations summary |
| REPORT PAYROLL | `REPORT PAYROLL <YYYY-MM>` | viewer | Payroll report for month |
| REPORT CASH | `REPORT CASH [days]` | viewer | Cash flow report |
| REPORT RECON | `REPORT RECON [days]` | viewer | Payment reconciliation report |
| REPORT ESCORT | `REPORT ESCORT <start> <end>` | viewer | Escort program report for date range |
| REPORT LIST | `REPORT LIST` | viewer | List available report types |

---

### Group 6: Backup Commands

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| BACKUP STATUS | `BACKUP STATUS` | viewer | Show last backup age and status |
| BACKUP NOW | `BACKUP NOW` | admin | Trigger immediate pg_dump backup |
| BACKUP LIST | `BACKUP LIST [n]` | viewer | List recent n backups |

---

### Group 7: User / RBAC Commands

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| USER ADD | `USER ADD <phone> <name> [role]` | superadmin | Create admin user (default: viewer) |
| USER ROLE | `USER ROLE <phone> <role>` | superadmin | Change admin user role |
| USER REMOVE | `USER REMOVE <phone>` | superadmin | Disable admin user (soft-disable) |
| USER LIST | `USER LIST` | viewer | List all active admins |
| USER APIKEY | `USER APIKEY <phone>` | superadmin | Issue API key (SHA-256 stored) |

---

### Group 8: Scheduler Commands

| Command | Syntax | Required Role | Action |
|---|---|---|---|
| SCHEDULE STATUS | `SCHEDULE STATUS` | viewer | Show all 15 scheduled job statuses |
| RUN JOB | `RUN JOB <job_name>` | admin | Manually trigger a scheduled job immediately |

**Source Module:** `modules/admin_commands`, `modules/rbac`, `modules/scheduler`
**PKCA Report:** 12_command_coverage_report.md
**Management Authority:** Production evidence; documented 2026-06-22
