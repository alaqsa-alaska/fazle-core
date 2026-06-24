---
title: Role Permissions
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Role Permissions

## Purpose
Backend-enforced permissions for AI and frontend role management.

## Requirements
- Admin can manage operational roles within policy.
- Developer can manage system roles/config with audit.
- Accountant can update payment/accounting states only.
- Supervisor/Operation roles can submit attendance/duty reports, not final financial writes.
- Candidate and employee cannot write internal records directly.

## CRUD Rules
View, add, edit, delete/deactivate, merge, verify, and promote unknown roles must be permission-controlled and audited.

---

## RBAC Hierarchy (Admin Command System)

### Purpose
All 37 WhatsApp admin commands are gated by a five-level role hierarchy. A user must have a role at or above the required level to execute a command.

### Role Level Order (ascending)

```
viewer < operator < accountant < admin < superadmin
```

| Level | Role | Can Execute |
|---|---|---|
| 1 | viewer | Read-only commands: STATUS, DRAFTS, ESCORTLIST, ESCORTDETAIL, REPORT *, PAYROLL LIST, SCHEDULE STATUS, USER LIST, BACKUP STATUS, BACKUP LIST |
| 2 | operator | Draft management: APPROVE, REJECT, EDIT; Escort: ESCORTCONFIRM, ESCORTCANCEL |
| 3 | accountant | Payment finalization: PAID, ADVANCE; Payroll: PAYROLL COMPUTE/SUBMIT/APPROVE/LOCK/PAID/CANCEL; BACKUP NOW |
| 4 | admin | Manual job trigger: RUN JOB |
| 5 | superadmin | User management: USER ADD, USER ROLE, USER REMOVE, USER APIKEY |

**Business Rule:** A user with role=operator can APPROVE and REJECT drafts but cannot execute PAID or any PAYROLL command. The RBAC check is enforced by `modules/rbac` before any command logic runs.

**Validation:** Every command is checked against `COMMAND_ROLE` dict. Insufficient role → command silently rejected with error reply.

**Source Module:** `modules/rbac`
**Source Function:** `COMMAND_ROLE`, `check_permission()`
**PKCA Report:** 12_command_coverage_report.md (HK-40)
**Management Authority:** Production evidence; documented 2026-06-22

---

## Bootstrap Admin Creation

**Business Rule:** On first WhatsApp message from any phone number listed in the `ADMIN_NUMBERS` environment variable, that contact is automatically created as a superadmin in `fazle_admins`.

| Parameter | Value |
|---|---|
| Source | `ADMIN_NUMBERS` env variable (comma-separated) |
| Created role | superadmin |
| Trigger | First seen WhatsApp message from that phone |
| Function | `ensure_bootstrap_admins()` |

**Purpose:** Allows initial system setup without a pre-existing admin in the database.
**Exception:** If the contact already exists in `fazle_admins`, this is a no-op.

**Source Module:** `modules/rbac`
**Source Function:** `ensure_bootstrap_admins()`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-41)

---

## Role Gate Behaviors (Production Enforcement)

The following gate behaviors are enforced at the `bridge_poller` level, before AI runs:

| Role | Draft-Always (never auto-send) | Hard Silent-Skip | Recruiting Blocked |
|---|---|---|---|
| admin | No | No | No |
| family | No | No | No |
| accountant | Yes | If phone matches `ACCOUNTANT_PHONE` | Yes |
| vip_client | Yes | No | Yes |
| client_escort_buyer | Yes | No | Yes |
| repeat_client | Yes | No | Yes |
| vendor | No | No | Yes |
| employee | No | No | No |
| supervisor | No | No | No |
| candidate | No | No | No (this IS recruitment) |
| unknown | No | No | No |
| blocked | N/A | Yes (hard) | N/A |

**Source Module:** `app/bridge_poller`
**Source Function:** `_is_draft_always()`, `_should_silent_skip()`
**PKCA Report:** 11_identity_coverage_report.md
