# Hybrid Search

## Purpose
Define BM25 plus semantic search behavior.

## Search Fields
title, role, visibility, keywords, body, business rules, workflow steps, examples, cross references.

## Ranking
1. Role and visibility match.
2. Exact business-rule keyword match.
3. Semantic similarity.
4. Recency/source-of-truth marker.
5. Workflow state match.

## Failure Mode
If search returns conflicting rules, do not guess. Use source-of-truth file or route to admin/conflict review.
