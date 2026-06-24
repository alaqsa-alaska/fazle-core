---
title: PKCA Report 19: Coverage Score
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 19: Coverage Score

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Overall Production Knowledge Coverage

**Overall Score: 14%**

This means 86% of production knowledge is NOT documented in the KB.

---

## Score by KB Folder

| Folder | Articles | Avg Coverage | Notes |
|---|---|---|---|
| 01_employee_knowledge/ | 8 | 23% | Best non-developer folder; attendance policy well-covered |
| 02_admin_knowledge/ | 4 | 25% | Partial — missing PAYROLL/BACKUP/SCHEDULE commands |
| 03_ai_identity/ | 10 | 22% | Role articles exist but resolution algorithm missing |
| 04_business_rules/ | 8 | 26% | Best overall; recruitment 44% but has BR-25 conflict |
| 05_workflows/ | 8 | 26% | Workflow docs exist but missing state machines |
| 06_developer_system/ | 15 | 11% | Most critical gaps; scheduler/FPE/outbound all 0% |

**Weighted average: 14% (fewer developer articles pulling the average down)**

---

## Score by Module Category

| Module Category | Modules | Coverage |
|---|---|---|
| Message routing / bridge_poller | 3 | 8% |
| Admin commands | 2 | 9% |
| AI / LLM | 5 | 2% |
| Escort / release slip | 4 | 18% |
| Payroll | 3 | 7% |
| Recruitment | 2 | 38% |
| Payment | 3 | 22% |
| Attendance | 2 | 33% |
| Database / infrastructure | 6 | 3% |
| Scheduler | 1 | 0% |
| Social auto-reply | 1 | 4% |
| Frontend | 1 | 0% |
| FPE | 1 | 0% |
| RAG | 1 | 15% |
| Identity | 2 | 20% |

---

## Score by Workflow

| Workflow | Coverage |
|---|---|
| WF-01: Message routing (15 steps) | 10% |
| WF-02: Escort order → assignment | 30% |
| WF-03: Release + payment | 25% |
| WF-04: Attendance reporting | 40% |
| WF-05: Recruitment funnel | 30% |
| WF-06: Admin command processing | 5% |
| WF-07: Payroll compute → approval | 20% |
| WF-08: Employee payment verification | 10% |
| WF-09: Outbound message delivery | 0% |
| WF-10: Contact synchronization | 0% |
| **Average** | **21%** |

---

## Score by Business Rule Domain

| Domain | Rules | Coverage |
|---|---|---|
| Attendance | 6 | 83% — best covered domain |
| Recruitment | 8 | 44% — but BR-25 conflict active |
| Payment | 12 | 27% |
| Escort | 14 | 21% |
| Identity / Routing | 10 | 15% |
| AI / System | 8 | 0% |
| **Average** | 58 rules | **27%** |

---

## Score by Parser

| Parser | Coverage |
|---|---|
| Escort Order Parser | 20% |
| Attendance Parser | 20% |
| Intent Classifier | 10% |
| Payment SMS Parser | 10% |
| RAG Tokenizer | 5% |
| Completed Draft Detector | 5% |
| All other parsers (9) | 0% |
| **Average** | **5%** |

---

## Score by State Machine

| State Machine | Coverage |
|---|---|
| SM-02: Escort Program | 30% |
| SM-07: Draft Reply | 5% |
| All other state machines (8) | 0% |
| **Average** | **3.5%** |

---

## Score by Hidden Rule

| Category | Coverage |
|---|---|
| RAG parameters | 15% |
| Admin command RBAC | 10% |
| Escort financial rules | 5% |
| All other hidden rules | 0% |
| **Average (47 rules)** | **~4%** |

---

## Score by AI Behavior

| AI Component | Coverage |
|---|---|
| RAG engine | 15% |
| Reviewed reply memory | 5% |
| All other AI components (10) | 0% |
| **Average** | **1.7%** |

---

## Score by Scheduler

| Metric | Score |
|---|---|
| Scheduler KB coverage | 0% |
| Jobs documented | 0 of 15 |

---

## Score by Database

| Metric | Score |
|---|---|
| Tables documented by name | 0 of 43 |
| Idempotency patterns | 0% |
| Behavioral patterns | <1% |

---

## Consolidated Score Card

| Dimension | Score |
|---|---|
| Overall production coverage | **14%** |
| By folder (avg) | **19%** |
| By workflow | **21%** |
| By business rule | **27%** |
| By parser | **5%** |
| By state machine | **3.5%** |
| By hidden rule | **4%** |
| By AI behavior | **1.7%** |
| By scheduler | **0%** |
| By database | **<1%** |

---

## Score Explanation

The gap between the PKVC score (42/100 accuracy) and PKCA score (14% coverage) is explained by measurement method:

- **PKVC measured ACCURACY** — of the things documented, how accurate were they? Answer: 42/100
- **PKCA measures COMPLETENESS** — of all production knowledge, how much is documented? Answer: 14%

These are complementary measures. High accuracy on a small % is not sufficient KB coverage for system reliability.

---

## Target Coverage (Aspirational)

| Dimension | Current | Target After KBTI | Priority |
|---|---|---|---|
| Business rules | 27% | 80% | P1 |
| Workflows | 21% | 75% | P1 |
| AI behavior | 1.7% | 60% | P1 |
| State machines | 3.5% | 70% | P1 |
| Hidden rules | 4% | 65% | P1 |
| Parsers | 5% | 50% | P2 |
| Scheduler | 0% | 90% | P1 |
| Database | <1% | 50% | P2 |
| **Overall** | **14%** | **~65%** | **Program goal** |
