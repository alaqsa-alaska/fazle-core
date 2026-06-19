# RAG Strategy

## Purpose
Define retrieval strategy for the role-centric knowledge base.

## Rules
- Determine role and visibility before retrieval.
- Retrieve from allowed folders only.
- Use hybrid BM25 + semantic retrieval.
- Prefer source-of-truth business rules when article and workflow disagree.
- Use conflict_resolution_record.md as historical decision reference.

## Chunking Guidance
Chunk by heading and rule block. Keep role, visibility, source, and related workflow metadata attached to each chunk.

## Cross References
- hybrid_search.md
- visibility_rules.md
