---
title: Client Order Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-27
runtime_index: true
---

# Client Order Workflow

## Trigger
Client asks for guard, escort, vessel security, service rate, complaint handling, or new service inquiry.

## Flow
Client Message -> Role Detection -> Service/Order Classification -> Draft -> Admin Review if order/sensitive -> Client Reply -> Record Update.

## Escort Buyer Orders
When the client message is an escort-service buying order containing vessel/lighter/master-mobile information, `modules/escort.parse_escort_message()` is the intake parser source of truth for draft extraction only.

Final assignment source of truth is not the client message or the parser result. Final assignment truth comes from the outbound bridge confirmation sent to an approved escort client number.

Canonical rule: a vessel grouped with a mobile number is a Lighter Vessel; a vessel line without mobile and with `MV`/`Mother Vessel`/`এমভি`/`At-O/A` is a Mother Vessel candidate. Full parser rules are documented in `knowledge_base/06_developer_system/escort_order_parser.md`.

## Guard Service Charge
৳13,000 may be used as client billing/service charge where applicable. It is not employee salary.

## Complaints
Acknowledge professionally and escalate. Do not expose internal workflow.

## Cross References
- escort_workflow.md
- ../03_ai_identity/vip_identity.md
