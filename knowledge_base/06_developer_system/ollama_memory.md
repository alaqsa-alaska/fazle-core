---
owner: Fazle Core Admin
kb_id: DEV-OLLAMA-MEMORY
title: Ollama Memory Database
status: active
visibility: developer
source_modules:
  - modules/ollama_memory/__init__.py
source_tables:
  - ai_memory_facts
  - ai_memory_questions
  - ai_memory_tasks
  - ai_kb_embeddings_manifest
runtime_index: true
last_verified: 2026-06-24
---

# Ollama Memory Database

## Purpose

`fazle_ollama_memory` is an independent PostgreSQL database owned exclusively by
the AI layer. The production `fazle`/`postgres` database is **never** written to
by AI.

## Architecture

| Item | Value |
|---|---|
| Database | `fazle_ollama_memory` |
| Owner role | `ollama_memory_owner` |
| Connection env var | `OLLAMA_MEMORY_DB_URL` |
| Module | `modules/ollama_memory/__init__.py` |

## Tables

### ai_memory_facts
AI-stored facts about subjects (employees, contacts, modules, escort programs).
- `subject_type`: category (employee, contact, module, vessel)
- `subject_key`: identifier (phone, name, module_name)
- `fact_type`: type of fact (summary, status, note, anomaly)
- `fact_text`: the stored fact
- `confidence`: 0.0–1.0 score
- `source_ref`: KB path, DB view, or URL that sourced this fact

### ai_memory_questions
History of Q&A sessions from Chat Lab — Admin Knowledge Q&A.
- `question`: admin's original question
- `normalized_question`: lowercase normalized form for deduplication
- `answer_summary`: first 2000 chars of Ollama's answer
- `source_refs`: JSON array of citations (KB paths, DB tool names, URLs)

### ai_memory_tasks
AI-tracked tasks and pending actions identified during conversations.
- `task_name`, `status` (open/in_progress/done/cancelled), `notes`

### ai_kb_embeddings_manifest
Record of which KB articles have been indexed into runtime RAG.
- `kb_path`, `kb_hash`, `chunk_count`, `status` (indexed/stale/removed)

## Permission Boundaries

| Action | Production DB | Memory DB |
|---|---|---|
| SELECT | Via approved views only (`ai_read_*`) | ✓ |
| INSERT/UPDATE/DELETE | ❌ FORBIDDEN | ✓ (memory tables only) |
| CREATE/ALTER/DROP table | ❌ FORBIDDEN | ✓ (memory DB only) |
| Reading secrets/tokens | ❌ FORBIDDEN | ❌ Never stored |

## API (modules/ollama_memory)

```python
await remember_fact(subject_type, subject_key, fact_type, fact_text, source_ref)
await recall_facts(subject_type, subject_key, fact_type=None)
await record_question(question, answer_summary, source_refs)
await get_recent_questions(limit=20)
await add_task(task_name, notes, source_refs)
await update_task_status(task_id, status)
await record_kb_indexed(kb_path, kb_hash, chunk_count)
await get_kb_manifest()
await list_memory_stats()
```

## Setup

Run once after deployment:
```bash
DATABASE_URL=<superuser_url> python3 scripts/setup_ollama_memory_db.py
```

Then apply production read-only views:
```bash
psql $DATABASE_URL -f db/migrations/012_ai_readonly_views.sql
```
