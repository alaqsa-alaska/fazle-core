---
title: Permission Matrix
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Permission Matrix

## Purpose
Define what each role may access, answer, edit, or trigger.

## Matrix
| Role | Level 1 | Level 2 | Level 3 | Auto Reply | Draft Required | Backend Write |
|---|---:|---:|---:|---:|---:|---:|
| Public | Yes | No | No | Safe FAQ only | When unclear | No |
| Candidate | Yes recruitment only | No | No | Yes if clear | Sensitive/unclear | Candidate lead only |
| Employee | Yes employee only | No | No | Yes if safe | Payment/identity risk | No direct write |
| Escort | Yes escort-safe | No | No | Yes if safe | Payment/release risk | No direct write |
| Supervisor | Limited employee ops | Yes assigned ops | No | Limited | Yes for reports | Attendance draft only |
| Operation Officer | Limited ops | Yes | No | Limited | Yes | Operational drafts |
| Female Operation Officer | Recruitment ops | Yes assigned | No | Limited | Yes | Candidate/field records |
| Recruitment Officer | Recruitment | Yes assigned | No | Limited | Yes | Candidate records |
| Accountant | Payment-safe | Yes finance | No | Limited | Payment handoff | Ledger/payment state |
| Admin | Yes | Yes | Limited by permission | Yes | Controls review | Yes with audit |
| Developer | Yes | Yes | Yes | No public direct | N/A | Schema/config with audit |
| System/AI | By role context | By role context | Internal only | Policy-bound | Policy-bound | Only authorized services |

## Role CRUD Rules
- Frontend role changes must update backend immediately.
- Backend must enforce permission checks.
- Every change needs audit log.
- Delete should normally be deactivate/archive unless management approves hard delete.

## Cross References
- ../06_developer_system/role_permissions.md
- response_rules.md

## Revision History
- 2026-06-19: Created from AI access and role-management requirements.
- 2026-06-22: Enriched with production gate behaviors and operational rules (Wave-1).

---

## Production Role Gate Behaviors

### Purpose
These gate behaviors are enforced at the bridge_poller level before any AI or LLM call. They override the matrix above for runtime decisions.

| Role | Draft-Always (never auto-send) | Silent-Skip | Recruiting Blocked | Auto-Reply Gate |
|---|---|---|---|---|
| admin | No | No | No | All admin commands; no customer auto-reply |
| family | No | No | No | Yes (safe intents) |
| accountant | Yes | Yes (if accountant phone) | Yes | Draft-only; no auto-send |
| vip_client | Yes | No | Yes | Draft-only; no auto-send |
| client_escort_buyer | Yes | No | Yes | Draft-only; no auto-send |
| repeat_client | Yes | No | Yes | Draft-only; no auto-send |
| vendor | No | No | Yes | Yes (safe intents only) |
| employee | No | No | No | Yes (safe intents) |
| supervisor | No | No | No | Limited (safe intents) |
| candidate | No | No | No | Yes (recruitment safe) |
| unknown | No | No | No | Yes (greeting + recruitment safe) |
| blocked | N/A | Hard-yes (no log) | N/A | None |

**Business Rule — draft-always:** Roles marked draft-always will NEVER auto-send regardless of intent. Admin must APPROVE each response.

**Business Rule — silent-skip:** Silent-skip means no reply, no draft, no admin notification. The message is received and stored but no output is generated.

**Source Module:** `app/bridge_poller`, `app/message_router`
**Source Function:** `_is_draft_always()`, `_should_silent_skip()`
**PKCA Report:** 11_identity_coverage_report.md, 10_hidden_rule_coverage_report.md (HK-09)
**Management Authority:** HK-09 approved in PKVC Management Decisions; production evidence documented 2026-06-22

---

## Safe Auto-Send Intent Gate

**Business Rule:** Even for roles that allow auto-reply, only these 9 intents can auto-send:

| Intent | Description |
|---|---|
| `recruitment` | Candidate job inquiries |
| `join` | How to join |
| `greeting` | Standard greetings |
| `office_location` | Office address (KB-only fast path — no LLM) |
| `salary_query` | Salary questions |
| `payment_due` | Payment status query |
| `attendance` | Attendance submission |
| `leave` | Leave request |
| `escort_duty` | Escort duty report |

All other intents produce a draft. `advance_request` is specifically excluded even though it resembles a payment intent.

**Source Module:** `app/message_router`
**Source Function:** `_SAFE_AUTOSEND_INTENTS`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-03, HK-04)
