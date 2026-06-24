---
title: PKMA Report 09 — Knowledge Maturity Heatmap
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 09 — Knowledge Maturity Heatmap

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Visual representation of maturity levels across all domains and knowledge dimensions. Higher maturity = more trustworthy knowledge.

Legend: ██████ = L5 | █████░ = L4 | ████░░ = L3 | ███░░░ = L2 | ██░░░░ = L1 | █░░░░░ = L0

---

## Primary Domain Heatmap

```
DOMAIN                   │ L0  L1  L2  L3  L4  L5 │ SCORE │ STATUS
─────────────────────────┼───────────────────────────┼───────┼──────────────────
Attendance               │                ████░░     │  3.0  │ Management Approved
Escort                   │                ████░░     │  3.0  │ Management Approved
Escort Payment           │                ████░░     │  3.0  │ Management Approved
Payroll                  │                ████░░     │  3.0  │ Management Approved
Recruitment              │                ████░░     │  3.0  │ Management Approved
Message Router           │                ████░░     │  3.0  │ Management Approved
Security Rules           │                ████░░     │  3.0  │ Management Approved
Business Rules           │                ████░░     │  3.0  │ Management Approved
Workflow                 │                ████░░     │  3.0  │ Management Approved
Knowledge Governance     │                ████░░     │  3.0  │ Management Approved
─────────────────────────┼───────────────────────────┼───────┼──────────────────
Identity Brain           │            ███░░░         │  2.0  │ Production Verified
AI Behavior              │            ███░░░         │  2.0  │ Production Verified
Scheduler                │            ███░░░         │  2.0  │ Production Verified
Notification / Outbound  │            ███░░░         │  2.0  │ Production Verified
Admin Commands           │            ███░░░         │  2.0  │ Production Verified
RBAC                     │            ███░░░         │  2.0  │ Production Verified
WhatsApp Channel         │            ███░░░         │  2.0  │ Production Verified
Bridge                   │            ███░░░         │  2.0  │ Production Verified
Automation Pipeline      │            ███░░░         │  2.0  │ Production Verified
Developer System         │            ███░░░         │  2.0  │ Production Verified
State Machines           │            ███░░░         │  2.0  │ Production Verified
─────────────────────────┼───────────────────────────┼───────┼──────────────────
Cash / FPE               │        ██░░░░             │  1.0  │ Documented
RAG                      │        ██░░░░             │  1.0  │ Documented
OCR Engine               │        ██░░░░             │  1.0  │ Documented
Parser Engine            │        ██░░░░             │  1.0  │ Documented
Database Behavior        │        ██░░░░             │  1.0  │ Documented (abstract)
─────────────────────────┼───────────────────────────┼───────┼──────────────────
Social Auto Reply        │ █░░░░░                    │  0.0  │ Unknown
Messenger                │ █░░░░░                    │  0.0  │ Unknown
Facebook                 │ █░░░░░                    │  0.0  │ Unknown
─────────────────────────┼───────────────────────────┼───────┼──────────────────
Voice                    │      N/A                  │  N/A  │ Not Implemented
─────────────────────────┼───────────────────────────┼───────┼──────────────────
PLATFORM AVERAGE         │                           │  1.97 │ Approaching L2
```

---

## Heatmap by Knowledge Folder

```
FOLDER                        │ DOMAINS  │ AVG LEVEL │ HEAT
──────────────────────────────┼──────────┼───────────┼───────────────────────
04_business_rules/            │ BR+Escort│   3.0     │ ████░░  WARM
05_workflows/                 │ 6 wflows │   3.0     │ ████░░  WARM
02_admin_knowledge/           │ Cmds+RBAC│   2.5     │ ████░░  WARM-MEDIUM
03_ai_identity/               │ Identity │   2.5     │ ████░░  WARM-MEDIUM
06_developer_system/ (5/7)    │ 5 articles│  2.0     │ ███░░░  MEDIUM
01_employee_knowledge/        │ Recruit. │   2.5     │ ███░░░  MEDIUM
06_developer_system/ (2/7)    │ DB+RAG   │   1.0     │ ██░░░░  COOL
Social/Messenger/Facebook      │ 0 arts   │   0.0     │ █░░░░░  COLD
FPE                           │ 0 arts   │   0.0     │ █░░░░░  COLD
```

---

## Heatmap by Management Decision Coverage

```
DOMAIN                   │ Has Mgmt Decision? │ # Decisions │ MATURITY GATE
─────────────────────────┼────────────────────┼─────────────┼──────────────────
Escort                   │ YES                │ 4 (CON1-4)  │ L3 passed
Recruitment              │ YES                │ 4 (BR25+HK) │ L3 passed
Message Router           │ YES                │ 5 (HK)      │ L3 passed
Security Rules           │ YES (partial)      │ 2 (HK13,44) │ L3 partial
Payroll                  │ YES                │ 1 (CON-01)  │ L3 passed
Attendance               │ YES                │ 1 (DUP-05)  │ L3 passed
Business Rules           │ YES                │ 10+         │ L3 passed
RBAC                     │ YES (partial)      │ 1 (HK-41)   │ L2 only
AI Behavior              │ NO                 │ 0           │ L2 ceiling
Identity Brain           │ NO                 │ 0           │ L2 ceiling
Scheduler                │ NO                 │ 0           │ L2 ceiling
Notification             │ NO                 │ 0           │ L2 ceiling
Admin Commands           │ NO                 │ 0           │ L2 ceiling
Database                 │ NO                 │ 0           │ L0-1 only
RAG                      │ NO                 │ 0           │ L1 only
FPE                      │ NO                 │ 0           │ Not started
Social                   │ NO                 │ 0           │ Not started
```

---

## Heatmap by Risk Level

```
DOMAIN                   │ RISK    │ Notes
─────────────────────────┼─────────┼──────────────────────────────────────
FPE Workers              │ CRIT ██ │ 5 workers running; zero KB coverage
Social Auto Reply        │ CRIT ██ │ 20-file system; zero KB coverage
Database Schema          │ CRIT ██ │ 43 tables; zero KB documentation
OCR TypedDict            │ HIGH ▓▓ │ 12 of 18 fields missing from KB
RAG Parameters           │ HIGH ▓▓ │ BM25 k1/b/chunk/tokenizer not in KB
DUP Conflicts (3 open)   │ HIGH ▓▓ │ May cause inconsistent AI answers
Parser Regex             │ HIGH ▓▓ │ 12 of 15 parsers undocumented
PKVC Post-Wave-1         │ MED  ░░ │ New conflicts may exist undetected
Draft Quality Gate       │ MED  ░░ │ Admins confused by unexplained drafts
Pre-Wave-1 Accuracy      │ MED  ░░ │ 44 articles not re-verified
Memory Extractor         │ MED  ░░ │ Fire-and-forget not in KB
Identity Algo Approval   │ LOW  ·· │ No management decision for priority order
RBAC Ratification        │ LOW  ·· │ No formal RBAC-level approval
```

---

## Progress Heatmap (Before vs After Wave-1)

```
DIMENSION           │ PRE-WAVE-1 │ POST-WAVE-1 │ CHANGE
────────────────────┼────────────┼─────────────┼─────────────────
Overall coverage    │ 14%        │ ~40%        │ ████████████████ +26pp
Workflow coverage   │ 21%        │ ~65%        │ ████████████████████████████ +44pp
Business rules      │ 27%        │ ~72%        │ ████████████████████████████████████████ +45pp
State machines      │ 3.5%       │ ~55%        │ ████████████████████████████████████████████████ +52pp
Hidden rules (HK)   │ 4%         │ ~52%        │ ████████████████████████████████████████████████ +48pp
AI behavior         │ 1.7%       │ ~60%        │ ██████████████████████████████████████████████████ +58pp
Scheduler           │ 0%         │ ~90%        │ ████████████████████████████████████████████████████████████████████████████████████ +90pp
Admin commands      │ 8%         │ ~75%        │ ████████████████████████████████████████████████████████████████ +67pp
Identity            │ 8%         │ ~70%        │ ████████████████████████████████████████████████████████████ +62pp
Database            │ <1%        │ ~3%         │ ▓ +2pp (not targeted)
```

---

## Heatmap Key Insight

**Hot Zone (Level 3+):** Business logic, workflows, and operational rules — everything that has a management decision. These are the platform's strongest knowledge assets.

**Medium Zone (Level 2):** Technical infrastructure — scheduler, automation pipeline, admin commands, identity engine. Fully working but not formally ratified.

**Cold Zone (Level 0–1):** Integration channels (social, Messenger, Facebook), backend infrastructure (database, RAG, OCR), and the FPE system. These represent the largest knowledge risk.

**The KB has a "hot core and cold edges" pattern** — core business rules are well-documented; peripheral/technical systems are not.

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
