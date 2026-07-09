# Strict Execution Checklist Template

Status: Draft Template
Owner: ____________________
Release ID: ____________________
Change Window: ____________________
Prepared On: ____________________

## 1) Non-Negotiable Protected Surfaces (Hard Stop)

If any item below is touched, this release is blocked.

- [ ] Payroll module remains unchanged
- [ ] Escort-roster module remains unchanged
- [ ] WhatsApp MCP bridges remain unchanged
- [ ] Meta Business WhatsApp inbound/outbound flows remain unchanged
- [ ] Backend-first message saving workflow remains unchanged
- [ ] Existing bridge connections remain unchanged
- [ ] QR states remain unchanged
- [ ] Existing active APIs remain unchanged

Blocking rule:

- [ ] Preflight deny-list scan passed
- [ ] No direct edits in protected paths
- [ ] No schema migration touches protected-domain tables

## 2) Scope Lock (Plan-Aligned)

Allowed release scope only:

- Phase 1: Feature Group 4 (Evidence/Audit/Preflight control plane)
- Phase 2: Feature Group 1 (LocationWhere read adapter, read-only)
- Phase 3: Feature Group 2 (OCR/Audio sidecar, advisory-only)
- Phase 4: Feature Group 3 (Model router governance)
- Phase 5: Feature Group 5 (Admin SafeOps workflow)

Out-of-scope (must stay disabled):

- [ ] Any write-back into external systems from new adapters
- [ ] Any replacement of existing bridge ingest chain
- [ ] Any autonomous action without explicit operator approval flow

## 3) Phase Gate Sheet

Use one gate form per phase. All MUST be checked before promotion.

---

### Gate Form: Phase _____

Phase Name: ___________________________________________
Feature Group: _________________________________________
Build Version: _________________________________________
Canary Start: __________________  Canary End: __________________
Approver Group: ________________________________________

#### A. Entry Criteria (Before Deploy)

- [ ] Scope confirmed against Scope Lock
- [ ] Protected-surface checks passed
- [ ] Feature flags exist and default to OFF
- [ ] Kill-switch exists and is verified
- [ ] Rollback script exists and dry-run passed
- [ ] DB migration is additive-only (new tables/indexes only)
- [ ] API changes are additive-only (new endpoints only)
- [ ] Observability dashboards prepared
- [ ] On-call owner assigned

Evidence links:

- Preflight report: ______________________________
- Diff summary: _________________________________
- Migration dry-run log: _________________________
- Kill-switch test log: __________________________

#### B. Deployment Controls (During Canary)

- [ ] Canary traffic cap set (____%)
- [ ] Shadow mode enabled where required
- [ ] Error budget threshold configured
- [ ] p95 latency threshold configured
- [ ] Timeout/retry/circuit-breaker limits set
- [ ] Operator notification channel live

Runtime guardrails:

- [ ] No protected endpoints invoked by new path
- [ ] No unsafe auto-action path reachable
- [ ] Manual-review path enforced where required

#### C. Exit Criteria (Canary to Promote)

- [ ] No Sev-1/Sev-2 incidents in canary window
- [ ] Error rate <= target: ________
- [ ] p95 latency <= target: ________
- [ ] Fallback correctness validated
- [ ] Audit log completeness validated
- [ ] Policy conformance checks passed
- [ ] Test Matrix required rows for this phase all PASS

Decision:

- [ ] Promote to next phase
- [ ] Hold in canary
- [ ] Rollback now

Sign-off:

- Engineering Lead: ____________________  Date: __________
- Operations Lead: _____________________  Date: __________
- Product/Owner: _______________________  Date: __________

---

## 4) Rollback Checklist (Global + Phase-Specific)

### 4.1 Global Rollback Trigger Rules

Rollback is mandatory if any of the following occur:

- [ ] Protected-surface violation detected
- [ ] Data corruption risk detected
- [ ] Error rate exceeds threshold for ____ minutes
- [ ] p95 latency exceeds threshold for ____ minutes
- [ ] Unauthorized write-path activation detected
- [ ] Incident commander declares rollback

### 4.2 Standard Rollback Steps (Runbook Order)

- [ ] Declare incident and freeze deployments
- [ ] Toggle feature flags OFF
- [ ] Activate phase kill-switch
- [ ] Route traffic to previous stable path
- [ ] Stop new async workers for affected feature
- [ ] Revert release artifact to previous version
- [ ] Execute DB rollback procedure (if applicable)
- [ ] Validate health checks and key endpoints
- [ ] Validate no protected flow regression
- [ ] Publish rollback summary to stakeholders

### 4.3 DB Rollback Safety

- [ ] Confirm rollback plan is backward-compatible
- [ ] Confirm no destructive DDL in this release
- [ ] Preserve newly written audit evidence
- [ ] Validate table-level integrity checks

### 4.4 Post-Rollback Verification

- [ ] Core baseline smoke tests PASS
- [ ] Protected flow smoke tests PASS
- [ ] New feature endpoints inaccessible or safe-disabled
- [ ] Monitoring back to baseline
- [ ] Incident timeline documented

### 4.5 Phase-Specific Rollback Notes

Phase 1 (FG4):

- [ ] Disable preflight enforcement in fail-open audit mode if validator unstable
- [ ] Preserve generated preflight evidence

Phase 2 (FG1):

- [ ] Disable external read adapter
- [ ] Clear adapter cache safely
- [ ] Keep adapter audit trail for RCA

Phase 3 (FG2):

- [ ] Disable media sidecar jobs
- [ ] Mark in-flight jobs cancelled with reason code
- [ ] Keep artifacts read-only for review

Phase 4 (FG3):

- [ ] Disable policy model-decider
- [ ] Revert to existing provider order
- [ ] Preserve decision telemetry for comparison

Phase 5 (FG5):

- [ ] Hide SafeOps UI controls
- [ ] Disable SafeOps APIs
- [ ] Retain operator action logs

## 5) Test Matrix

Use this matrix as a release gate artifact. Every Required row must be PASS.

| Test ID | Phase | Feature Group | Category | Test Description | Required | Owner | Result (PASS/FAIL) | Evidence Link |
|---|---|---|---|---|---|---|---|---|
| T-001 | 1 | FG4 | Preflight | Protected-surface deny-list blocks prohibited file/path changes | Yes | Eng |  |  |
| T-002 | 1 | FG4 | Preflight | Additive-only DB migration validator blocks destructive DDL | Yes | Eng |  |  |
| T-003 | 1 | FG4 | Reliability | Kill-switch activation test (under load) | Yes | Ops |  |  |
| T-004 | 1 | FG4 | Audit | Preflight reports persisted and queryable | Yes | Eng |  |  |
| T-005 | 2 | FG1 | Contract | LocationWhere read contract schema validation (strict) | Yes | Eng |  |  |
| T-006 | 2 | FG1 | Resilience | Timeout/retry/circuit-breaker behavior on external 5xx | Yes | Eng |  |  |
| T-007 | 2 | FG1 | Security | Read-only enforcement, no write-path reachable | Yes | Security |  |  |
| T-008 | 2 | FG1 | Data | Cache consistency and expiry correctness | Yes | Eng |  |  |
| T-009 | 3 | FG2 | NLP/OCR | Bangla + English OCR extraction quality threshold | Yes | QA |  |  |
| T-010 | 3 | FG2 | ASR | Bangla + English transcription quality threshold | Yes | QA |  |  |
| T-011 | 3 | FG2 | Safety | Low-confidence media outputs forced to manual review | Yes | QA |  |  |
| T-012 | 3 | FG2 | Workflow | Advisory-only mode (no auto-action) | Yes | Eng |  |  |
| T-013 | 4 | FG3 | Routing | Deterministic model selection by task class | Yes | Eng |  |  |
| T-014 | 4 | FG3 | Failover | Provider fallback chain correctness | Yes | Eng |  |  |
| T-015 | 4 | FG3 | Quality | Bangla+English response regression suite | Yes | QA |  |  |
| T-016 | 4 | FG3 | Observability | Model decision telemetry completeness | Yes | Ops |  |  |
| T-017 | 5 | FG5 | RBAC | Unauthorized role cannot approve/activate features | Yes | Security |  |  |
| T-018 | 5 | FG5 | Workflow | Approval chain integrity and audit logging | Yes | Ops |  |  |
| T-019 | 5 | FG5 | Control | Global kill-switch from SafeOps panel | Yes | Ops |  |  |
| T-020 | 5 | FG5 | UX Safety | New controls cannot affect protected legacy controls | Yes | QA |  |  |
| T-021 | All | All | Regression | Protected flow smoke test pack remains green | Yes | QA |  |  |
| T-022 | All | All | Performance | p95 latency and error budget within limits | Yes | Ops |  |  |
| T-023 | All | All | Compliance | Local AI policy conformance checks | Yes | Security |  |  |

Pass rule:

- [ ] Every Required=Yes test is PASS
- [ ] Every FAIL has approved waiver with expiry date
- [ ] No open Sev-1/Sev-2 incident

## 6) Phase Completion Certificate (Per Phase)

Phase: _________  Feature Group: _________  Date: _________

- [ ] Entry criteria complete
- [ ] Deployment controls complete
- [ ] Exit criteria complete
- [ ] Rollback drill completed in this phase window
- [ ] Test Matrix required rows PASS
- [ ] Evidence archived

Certified by:

- Engineering: ____________________
- Operations: _____________________
- Owner: _________________________

## 7) Final Release Go/No-Go

Go/No-Go Meeting Date: ____________________

- [ ] All five phase certificates complete
- [ ] Cross-phase regression is green
- [ ] Security review complete
- [ ] Operations readiness complete
- [ ] Stakeholder approval complete

Decision:

- [ ] GO
- [ ] NO-GO
- [ ] GO WITH CONDITIONS

Conditions (if any):

____________________________________________________________________
____________________________________________________________________

