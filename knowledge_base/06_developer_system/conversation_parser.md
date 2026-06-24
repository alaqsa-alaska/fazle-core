---
title: Conversation Parser
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Conversation Parser

## Purpose
Classify conversation intent and state without replacing role-based routing.

## State Signals
- New candidate.
- Existing employee request.
- Escort order.
- Payment request.
- Complaint.
- Unknown/sensitive/internal.
- Admin reply mapping.

## Admin Reply Mapping
Parse:
From: [name], [mobile]
To: [name], [mobile]
Then connect admin answer to original user session.
