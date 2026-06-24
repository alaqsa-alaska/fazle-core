---
title: Parser Engine
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Parser Engine

## Purpose
Parse structured and semi-structured operational messages.

## Parsers
- Escort order parser.
- Payment message parser.
- Attendance parser.
- Candidate inquiry parser.
- Admin fallback parser.

## Rules
Use deterministic extraction where possible. Use LLM only for ambiguity handling, not final approval.
