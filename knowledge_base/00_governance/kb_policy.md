---
title: Knowledge Base Policy
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
tags: [governance, knowledge_base, policy]
---

# Knowledge Base Policy

The Fazle Core Knowledge Base is the authoritative source for business rules, system behavior, workflows, permissions, and AI response guidance.

## Runtime Rules

- Active Markdown files are eligible for runtime indexing unless metadata sets `runtime_index: false`.
- Content under `knowledge_base/07_archived/` is not eligible for runtime indexing.
- Production database schemas and WhatsApp bridge stores must not be modified by KB tooling.
- Secrets, tokens, passwords, API keys, credentials, and private customer data must not be stored in KB files.

## Governance Rules

- Every active KB article should include metadata with title, owner, status, last verification date, runtime indexing intent, and tags.
- Stale content must be reviewed before certification.
- Duplicate content should be merged or explicitly marked as archived.
