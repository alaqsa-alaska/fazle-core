---
title: FAZLE AI PLATFORM
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# FAZLE AI PLATFORM
# PRODUCTION KNOWLEDGE MINING -> KNOWLEDGE ENRICHMENT -> KNOWLEDGE BASE GENERATION PROGRAM

Version: 2.0  
Status: ANALYSIS ONLY  
Company: Al-Aqsa Security and Logistics Services Ltd.  
Date: 2026-06-21

---

## Primary Objective
This document captures enterprise knowledge mining outputs from the production Fazle AI Platform.

Scope of this artifact:
- Discover operational knowledge embedded in production behavior.
- Extract business, workflow, identity, parser, validation, command, audit, and AI behavior rules.
- Classify and normalize knowledge for controlled knowledge-base publication.
- Propose knowledge enrichment and generation actions.

Non-scope:
- No source code refactor.
- No production behavior change.
- No database schema change.
- No deployment/runtime/service change.

---

## Absolute Safety Rule (Analysis Phase)
No changes were made to:
- Backend/frontend code
- APIs/routes
- Database/schema/migrations
- RBAC/security
- Bridge integrations
- Scheduler/deployment/services
- Environment/configuration/runtime

This file is strictly a documentation and planning artifact.

---

## REPORT 1 - Executive Summary
The Fazle platform is a role-aware, identity-first, bilingual operations engine for messaging-led workflows (attendance, escort, payment, payroll, recruitment, admin operations).

Key outcome:
- Production code is the current operational brain.
- Knowledge base can become the organizational brain only after documenting hidden logic currently trapped in code branches, regexes, thresholds, and fallback chains.

High-level findings:
- Strongly deterministic safety gates exist in routing and outbound paths.
- Multiple hidden business rules are not fully represented in current KB files.
- Conflicts exist between some operational formulas and policy-level assumptions.

---

## REPORT 2 - Production Knowledge Inventory
Core knowledge-bearing engines identified:
- Message Router
- Identity Brain
- Bridge Poller
- Escort + Escort Lifecycle
- Attendance + Attendance Parser
- Payment Workflow
- Payroll
- Recruitment Flow
- RBAC
- Scheduler
- RAG Engine
- Admin Command Processor
- Social Auto-Reply subsystem

Primary dependencies:
- PostgreSQL (business persistence)
- Redis (cooldown and resilience patterns)
- SQLite bridge stores (message ingestion)
- LLM providers (GitHub Models, Groq, Ollama)

---

## REPORT 3 - Hidden Knowledge Inventory
Hidden operational knowledge extracted includes:
- Silent-skip contacts and blocked-role behavior
- Draft-always role/name/phone policies
- Complaint and financial phrase guards
- Loop detection and keyword flood suppression
- Prompt-injection detection patterns
- Outbound poison-content suppression
- Identity resolution precedence and confidence sources
- RAG exclusion/safety chunk filtering policies
- OCR release draft constraints and warning thresholds
- Payroll and payment idempotency logic

---

## REPORT 4 - Knowledge Mining Report
Knowledge mined by type:
- Business rules
- Workflow sequencing
- Role and identity resolution
- Command grammar and admin governance
- Parser regex and extraction behavior
- Validation constraints
- Retry/fallback/error behavior
- State transitions and approval gates
- Audit and observability behavior

Mining sources:
- Runtime code paths in production modules
- Inline comments and invariant notes
- SQL interaction patterns
- Scheduler and automation jobs

---

## REPORT 5 - Knowledge Classification Report
Recommended visibility classes:
- Public
- Employee Safe
- Candidate Safe
- Escort Safe
- Supervisor
- Operations
- HR
- Admin
- Accountant
- Management
- Developer
- Internal
- System
- Confidential
- Archived

Classification policy:
- Keep parser internals, command internals, security logic, and DB internals out of public/employee channels.
- Preserve traceability for every knowledge transformation.

---

## REPORT 6 - Duplicate Report
Duplicate and overlapping knowledge zones identified:
- Office/location response text across channels
- Escort keyword recognition in multiple layers
- Similar contact normalization responsibilities
- Multiple attendance intake paths with overlapping intents

Action:
- Merge duplicate policy narratives into single authoritative articles while preserving source references.

---

## REPORT 7 - Conflict Report
Conflict record categories:
- Payment formula assumptions across modules
- Transport allowance table consistency vs policy references
- Food/conveyance estimate semantics (draft vs final)
- Payroll interpretation boundaries (program-rate vs salary-derived logic)

Each conflict requires:
- Conflict ID
- Production evidence
- KB evidence
- Risk assessment
- Management decision

---

## REPORT 8 - Missing Knowledge Report
Missing knowledge domains to be created:
- Full message routing priority map
- Draft enforcement and suppression rules
- Safety filters and anti-leak outbound controls
- Complete admin command catalog and role permissions
- Scheduler operational contract and runbook
- RAG exclusion/safety architecture
- Escort release OCR review constraints

---

## REPORT 9 - Workflow Reconstruction Report
Reconstructed end-to-end workflows:
- Inbound message lifecycle (ingest -> identity -> intent -> route -> send/draft)
- Escort client order flow
- Escort release closure and payment draft flow
- Attendance parsing and approval flow
- Recruitment session progression and scoring flow
- Admin command execution and audit flow

Each workflow includes:
- Actor
- Input
- Validation
- Decision
- Processing
- Persistence
- Notification
- Audit
- Failure/retry/recovery

---

## REPORT 10 - State Machine Report
State-machine candidates reconstructed:
- Payroll run lifecycle
- Escort program lifecycle
- Payment draft lifecycle
- Recruitment session lifecycle
- Attendance draft lifecycle
- Admin actor status lifecycle

Requirement:
- Publish explicit state transition tables with allowed and disallowed transitions.

---

## REPORT 11 - Parser Report
Parser families identified:
- Escort order extraction
- Completed escort draft parsing
- Attendance free-form parsing
- Release confirmation parsing
- Payment ingest parsing
- Intent fallback parsing

Documentation requirement:
- Preserve regex behavior, token handling, and fallback order as-is.

---

## REPORT 12 - Validation Report
Validation rule categories:
- Age bounds
- Shift/date format and range checks
- Numeric/amount positivity checks
- Release-date sanity checks
- Duty-day plausibility checks
- Command dedup windows
- OCR eligibility thresholds

---

## REPORT 13 - Command Report
Admin command surface includes:
- Draft controls (approve/reject/edit/status)
- Payment controls (paid/advance/adjust/reverse)
- Escort controls (confirm/release)
- Payroll controls (compute/submit/approve/lock/paid/cancel/list)
- Reporting controls
- Backup controls
- User and RBAC controls
- Scheduler controls

Command documentation must include:
- Syntax
- Required role
- Side effects
- Audit behavior

---

## REPORT 14 - Business Rule Report
Business rule themes extracted:
- Recruitment eligibility boundaries
- Operational-role exclusion from candidate funnel
- Complaint and legal issue escalation patterns
- Release confirmation authority restrictions
- Financial draft-first policies
- Finalization and idempotency constraints

---

## REPORT 15 - Identity Report
Identity model characteristics:
- Priority-driven role resolution
- Multi-source enrichment (seed rules, employee DB, attendance, cash, escort roster, contact tables, text hints)
- Confidence and source tracking

Requirement:
- Publish an identity decision table and confidence model notes for internal governance.

---

## REPORT 16 - Database Behaviour Report
Database behavior documented by pattern:
- Core write paths and transactional boundaries
- Draft tables and approval pathways
- Idempotency keys and conflict handling
- Cursor and dedup persistence for pollers
- Audit and heartbeat persistence

---

## REPORT 17 - AI Behaviour Report
AI behavior architecture:
- Intent classification with fallback
- Reply generation with provider chain and safe degradation
- Optional safe-mode behavior for uncertain outputs
- Separation of customer-facing generation from internal processing paths

---

## REPORT 18 - Notification Report
Notification surfaces include:
- Admin operational alerts
- Draft review notifications
- Accountant handoff notifications
- Health/DLQ/backup/scheduler alerts

Requirement:
- Document idempotency and suppression semantics for all outbound alerts.

---

## REPORT 19 - Audit Report
Audit layers include:
- Command attempt/allow/deny tracking
- Payroll transition logging
- Scheduler run history
- Reconciliation traces
- Outbound safety incident logs
- Service heartbeat metrics

---

## REPORT 20 - Scheduler Report
Scheduler knowledge domains:
- Job registry and cadence
- Job-level side effects and alerting
- Environment overrides
- Operational observability
- Cleanup and watchdog routines

---

## REPORT 21 - RAG Report
RAG architecture extracted:
- Offline-first BM25 retrieval pipeline
- Bilingual tokenization
- Resource and KB source indexing
- Exclusion lists and safety chunk purging
- Search audit traces

---

## REPORT 22 - Knowledge Relationship Graph
Primary relationship chain examples:
- Attendance -> Payroll -> Cash -> Reports
- Escort -> Roster -> Attendance -> Payment -> Payroll -> Reports
- Candidate -> Recruitment -> Employee -> Attendance -> Payroll -> Exit

Graph publication requirement:
- Publish both role-centric and process-centric relationship maps.

---

## REPORT 23 - Knowledge Coverage Matrix
Coverage dimensions:
- Existing and accurate
- Existing but partial
- Missing
- Contradictory

Observed pattern:
- Core policy and workflow docs exist.
- Hidden operational guards and engine-level internals are under-documented.

---

## REPORT 24 - Proposed Knowledge Articles
High-priority article candidates:
- Router priority and safety gates
- Draft policy and exception matrix
- Admin command and RBAC reference
- Scheduler runbook and operational contracts
- RAG safety and source policy
- Identity decision table
- OCR release handling policy

---

## REPORT 25 - Proposed Knowledge Books
Recommended large-format books:
- attendance_engine.md
- escort_engine.md
- payroll_engine.md
- payment_engine.md
- identity_engine.md
- message_router_engine.md
- parser_engine.md
- rag_engine.md
- scheduler_engine.md
- admin_command_engine.md
- bridge_engine.md
- database_engine.md
- social_engine.md

---

## REPORT 26 - Proposed Folder Changes
Knowledge-only structural proposals (no production impact):
- Expand engine-level docs under internal/developer visibility areas.
- Keep role-centric public/admin separation intact.
- Preserve archive/traceability layer for historical diffs and conflict decisions.

---

## REPORT 27 - Proposed File Changes
Planned changes are knowledge-base documentation updates only:
- Create missing engine-level documents.
- Expand partial workflow/state-machine docs.
- Add conflict records and cross-reference maps.
- Update inventory and gap/duplicate/conflict reports.

No production code edits required for this program phase.

---

## REPORT 28 - Knowledge Base Generation Plan
Execution plan after approval:
1. Freeze current production behavior as baseline evidence.
2. Publish critical hidden-rule articles (safety and routing first).
3. Publish command/RBAC/scheduler operational docs.
4. Publish workflow and state-machine books.
5. Publish parser/validation and RAG internals with restricted visibility.
6. Resolve conflicts with management decisions and revision history.
7. Finalize coverage matrix and certify KB readiness.

Gate:
- STOP after analysis and wait for management approval before KB modifications.

---

## Final Objective
The knowledge base must become the permanent organizational brain by making every production behavior explainable without reading code.

Success criteria:
- Every workflow reproducible from documentation.
- Every business rule documented with ownership and visibility.
- Every parser/validation/decision path documented with traceability.
- No critical operational knowledge left only in source code.

---

## Approval Section
Management decision status: PENDING

Required approvals:
- Proceed with KB file generation and enrichment updates.
- Confirm conflict-resolution protocol and authority.
- Confirm visibility model for sensitive internals.

Sign-off:
- Management: __________________
- Date: __________________
