---
title: PKMA Report 08 — Knowledge Risk Assessment
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 08 — Knowledge Risk Assessment

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Identify and quantify knowledge risks: situations where gaps in the KB could lead to incorrect AI responses, business errors, audit failures, or operational damage.

---

## Risk Framework

Risk Rating = Likelihood × Impact

| Rating | Likelihood | Impact | Description |
|---|---|---|---|
| CRITICAL | High | High | Can cause immediate business damage or compliance failure |
| HIGH | High/Medium | High | Can cause operational error or significant AI misbehavior |
| MEDIUM | Medium | Medium | Can cause inconsistency or delayed resolution |
| LOW | Low | Low/Medium | Minor operational friction |

---

## CRITICAL Risks

### RISK-01 — FPE Workers Invisible to KB

| Attribute | Value |
|---|---|
| Domain | Cash / FPE |
| Description | 5 production workers (message_processor_worker, accounting_worker, historical_sync_loop, gap_scan_loop, bridge_health_loop) run continuously but have NO KB article |
| Likelihood | MEDIUM (developers need to work on these) |
| Impact | CRITICAL (misunderstanding worker behavior could cause double-accounting, missed payments, or broken bridge) |
| Risk Rating | CRITICAL |
| Evidence | Workers verified ACTIVE in `modules/fazle_payroll_engine/workers.py` `start_workers()` |
| Mitigation | Create `fpe_overview.md` in Wave-2 |

### RISK-02 — Social Auto Reply Behavior Invisible

| Attribute | Value |
|---|---|
| Domain | Social Auto Reply / Messenger / Facebook |
| Description | 20-file system for Facebook/Messenger/Meta WhatsApp comment replies has 0% KB coverage |
| Likelihood | HIGH (this system responds to customers daily) |
| Impact | CRITICAL (if rate limiter, risk flagger, or reply rules are unknown, a misconfiguration could flood or block customer channels) |
| Risk Rating | CRITICAL |
| Evidence | System identified in PKCA (Report 08); ~4% coverage, effectively 0% useful |
| Mitigation | Create `social_auto_reply_system.md` in Wave-2 |

### RISK-03 — Database Schema Drift Undetectable

| Attribute | Value |
|---|---|
| Domain | Database Behavior |
| Description | 43 production tables identified; 0 documented in KB. Schema changes cannot be validated against KB |
| Likelihood | MEDIUM (schema changes happen in development cycle) |
| Impact | CRITICAL (undetected schema changes can silently break workflows, parsers, and state machines) |
| Risk Rating | CRITICAL |
| Evidence | PKCA Report 06 identified 43 tables; database_rules.md is abstract only |
| Mitigation | Enrich `database_rules.md` with full table inventory in Wave-2 |

---

## HIGH Risks

### RISK-04 — OCR TypedDict Partial Documentation

| Attribute | Value |
|---|---|
| Domain | OCR Engine / Release Slip |
| Description | Only 6 of 18 EscortSlipResult fields documented. If someone modifies the OCR extractor, they may miss 12 undocumented fields |
| Likelihood | MEDIUM |
| Impact | HIGH (missing fields = incorrect release slip processing = wrong payment) |
| Risk Rating | HIGH |
| Mitigation | Enrich `ocr_engine.md` with full TypedDict in Wave-2 |

### RISK-05 — RAG Parameter Gap

| Attribute | Value |
|---|---|
| Domain | RAG |
| Description | BM25 k1=1.5, b=0.75, chunk 320/60, bilingual tokenizer known but not in KB. If someone rebuilds or migrates the RAG engine, they may use wrong parameters |
| Likelihood | MEDIUM |
| Impact | HIGH (wrong BM25 params = degraded AI answer quality platform-wide) |
| Risk Rating | HIGH |
| Mitigation | Enrich `rag_strategy.md` in Wave-2 |

### RISK-06 — DUP Conflicts (3 Open)

| Attribute | Value |
|---|---|
| Domain | Salary, Identity, Recruitment |
| Description | DUP-03, DUP-04, DUP-06 still pending. AI may serve inconsistent answers depending on which source it retrieves |
| Likelihood | MEDIUM (RAG can retrieve either source) |
| Impact | HIGH (customer-facing inconsistency; payroll disputes; wrong phone format advice) |
| Risk Rating | HIGH |
| Mitigation | Management decision session to resolve all 3 |

### RISK-07 — Parser Regex Not Documented

| Attribute | Value |
|---|---|
| Domain | Parser Engine |
| Description | 12 of 15 parsers have no KB documentation. Escort order parser, release confirmation parser documented but regex patterns not captured |
| Likelihood | HIGH (parsers are used constantly) |
| Impact | HIGH (if parsers are modified without understanding regex, data ingestion breaks) |
| Risk Rating | HIGH |
| Mitigation | Enrich `parser_engine.md` with all 15 parsers and regex patterns |

### RISK-08 — HK-12, HK-14, HK-15 Not Formally Approved

| Attribute | Value |
|---|---|
| Domain | Security Rules |
| Description | Poison filter (HK-12), keyword flood (HK-14), prompt injection (HK-15) documented in KB but NOT in formal management decision log |
| Likelihood | LOW (these work correctly) |
| Impact | HIGH (if challenged, no formal authority exists; compliance risk) |
| Risk Rating | MEDIUM-HIGH |
| Mitigation | Request formal ratification of HK-12, HK-14, HK-15 |

---

## MEDIUM Risks

### RISK-09 — PKVC Not Run Post-Wave-1

| Attribute | Value |
|---|---|
| Domain | All Level 3 domains |
| Description | Wave-1 made 21 enrichments. PKVC has not been re-run to verify no new conflicts were introduced |
| Likelihood | LOW (Wave-1 was carefully production-verified) |
| Impact | MEDIUM (if a conflict exists and is not detected, it could be used by AI to give wrong answers) |
| Risk Rating | MEDIUM |
| Mitigation | Run PKVC after Wave-2 for all enriched domains |

### RISK-10 — Draft Quality Gate Not Documented

| Attribute | Value |
|---|---|
| Domain | AI Behavior / Automation Pipeline |
| Description | Draft quality gate (4 criteria) mentioned in code but not in KB. Admins may not understand why some responses go to draft |
| Likelihood | MEDIUM (admins see draft behavior daily) |
| Impact | MEDIUM (confusion about why drafts are created; incorrect operator intervention) |
| Risk Rating | MEDIUM |
| Mitigation | Document quality gate in `automation_pipeline.md` Wave-2 |

### RISK-11 — Pre-Wave-1 Articles Accuracy Unknown

| Attribute | Value |
|---|---|
| Domain | 44 non-enriched articles |
| Description | 44 articles were not enriched in Wave-1. Their accuracy against current production has not been verified |
| Likelihood | LOW-MEDIUM (some may be outdated) |
| Impact | MEDIUM (stale KB articles → incorrect AI responses for those topics) |
| Risk Rating | MEDIUM |
| Mitigation | Wave-2 should include PKVC-style re-verification of all non-enriched articles |

### RISK-12 — Memory Extractor Fire-and-Forget Not Documented

| Attribute | Value |
|---|---|
| Domain | AI Behavior |
| Description | Memory extractor runs as fire-and-forget; behavior not in KB |
| Likelihood | LOW |
| Impact | MEDIUM (developers may not know memory extractor exists; could conflict with AI behavior assumptions) |
| Risk Rating | MEDIUM |
| Mitigation | Document in `automation_pipeline.md` Wave-2 |

---

## LOW Risks

### RISK-13 — Identity Algorithm Not Management-Approved

| Risk | Identity algorithm priority order has no formal management approval |
|---|---|
| Mitigation | Request ratification in Wave-2 management session |

### RISK-14 — RBAC Hierarchy Not Formally Ratified

| Risk | 5-level RBAC hierarchy has no explicit management decision; HK-41 covers only bootstrap |
|---|---|
| Mitigation | Request ratification in Wave-2 management session |

### RISK-15 — LLM Provider Order Not Management-Approved

| Risk | GitHub→Groq→Ollama (reply) / Groq→GitHub→Ollama (intent) is engineering decision only |
|---|---|
| Mitigation | Low business risk; accept at Level 2 unless a different order is desired |

---

## Risk Register Summary

| Risk ID | Domain | Rating | Status |
|---|---|---|---|
| RISK-01 | FPE Workers | CRITICAL | Open |
| RISK-02 | Social Auto Reply | CRITICAL | Open |
| RISK-03 | Database Schema | CRITICAL | Open |
| RISK-04 | OCR TypedDict | HIGH | Open |
| RISK-05 | RAG Parameters | HIGH | Open |
| RISK-06 | DUP Conflicts (3) | HIGH | Open |
| RISK-07 | Parser Regex | HIGH | Open |
| RISK-08 | HK-12/14/15 Not Approved | MEDIUM-HIGH | Open |
| RISK-09 | PKVC Not Run Post-Wave-1 | MEDIUM | Open |
| RISK-10 | Draft Quality Gate | MEDIUM | Open |
| RISK-11 | Pre-Wave-1 Article Accuracy | MEDIUM | Open |
| RISK-12 | Memory Extractor | MEDIUM | Open |
| RISK-13 | Identity Algorithm Approval | LOW | Open |
| RISK-14 | RBAC Formal Ratification | LOW | Open |
| RISK-15 | LLM Provider Order | LOW | Accept |

**Total Open Risks: 15 (3 CRITICAL, 4 HIGH, 5 MEDIUM, 3 LOW)**

---

## Risk-to-Freeze Decision

The 3 CRITICAL risks alone are sufficient to block a KNOWLEDGE BASE FREEZE. A KB freeze with CRITICAL risks means the AI could:
1. Serve incorrect payment or accounting information (RISK-01 — FPE invisible)
2. Malfunction on social channels (RISK-02 — social auto reply invisible)
3. Fail to detect schema drift affecting core workflows (RISK-03 — database schema missing)

**Risk Verdict: NOT READY FOR FREEZE. 3 CRITICAL risks must be resolved first.**

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
