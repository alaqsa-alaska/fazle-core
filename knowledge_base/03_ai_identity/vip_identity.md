---
title: VIP Identity
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# VIP Identity

## Purpose
Identify VIP clients and ensure careful manual or high-quality client-safe replies.

## Detection Signals
- Contact book marker indicating VIP/client.
- Known high-value client history.
- Repeat order or sensitive complaint context.

## Permissions
Allowed:
- Client-safe service replies.
- Escort order draft workflow.
- Complaint acknowledgment.

Routing
- Escort/client orders require admin draft review.
- Sensitive complaints should escalate to admin.

## Cross References
- permission_matrix.md
- ../05_workflows/client_order_workflow.md

## Revision History
- 2026-06-19: Created from VIP/repeat client identity rules.
