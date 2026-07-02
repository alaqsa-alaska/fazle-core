---
title: RAG Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# RAG Rules

## Purpose
Define retrieval, context loading, and knowledge visibility rules for RAG and hybrid search.

## Scope
Level 3 developer/system knowledge. Never expose to external users.

## Retrieval Sources
- Employee knowledge for employee-visible policy replies.
- Admin system knowledge for admin, HR, operation, supervisor, accountant, and management workflows.
- Developer system knowledge for internal AI, RAG, automation, identity, parser, and system prompt behavior.

## Retrieval Rules
- Use role and security level before retrieving answer content.
- Do not retrieve Level 2 or Level 3 content for Level 1 users.
- Load conversation history to preserve context.
- Use keyword/BM25 and semantic retrieval together where available.
- Select the most relevant knowledge rows/articles before response generation.

## Context Builder
The context builder should include:
- role/sub-role;
- conversation history;
- channel source;
- safe knowledge articles;
- active workflow state;
- pending admin decision state if any.

## Vector Database Note
Qdrant may be used as the vector database for RAG. Existing implementation and chunking may require more seeded data before production use.

## Business Rules
- Retrieval must respect visibility level.
- If retrieved context conflicts, prefer authoritative/current policy; otherwise route to conflict/manual review.
- Do not expose source file names, paths, hidden categories, or retrieval mechanics to end users.

## Cross References
- identity_brain.md
- security_rules.md
- ai_system_prompt.md

## Revision History
- 2026-06-19: Created from RAG and context rules.
