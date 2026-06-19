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
