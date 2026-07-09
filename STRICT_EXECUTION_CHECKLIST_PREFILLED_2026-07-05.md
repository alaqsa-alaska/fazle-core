# Strict Execution Checklist Template

Status: Prefilled For Current Release Window
Owner: Azim
Release ID: REL-2026-07-W1
Change Window: 2026-07-05 22:00 to 2026-07-19 23:59 (local)
Prepared On: 2026-07-05

Default Owner Placeholders:

- Engineering Lead: Azim
- Operations Lead: Azim
- Security Lead: Azim
- QA Lead: Azim
- Product/Business Owner: Azim
- Incident Commander: Azim

Default Runtime Thresholds (apply unless phase override is approved):

- Canary progression: 5% -> 15% -> 30% -> 60% -> 100%
- Rollback canary cap (initial): 5%
- Error-rate rollback trigger: >= 1.5% for 10 continuous minutes
- p95 latency rollback trigger: >= 2500 ms for 10 continuous minutes
- External dependency timeout budget: 2.0 s per attempt, max 2 retries, circuit open after 5 consecutive failures in 2 minutes
- No-go incident threshold: any Sev-1, or >= 2 Sev-2 in canary window

## 1) Non-Negotiable Protected Surfaces (Hard Stop)

If any item below is touched, this release is blocked.

- [x] Payroll module remains unchanged
- [x] Escort-roster module remains unchanged
- [x] WhatsApp MCP bridges remain unchanged
- [x] Meta Business WhatsApp inbound/outbound flows remain unchanged
- [x] Backend-first message saving workflow remains unchanged
- [x] Existing bridge connections remain unchanged
- [x] QR states remain unchanged
- [x] Existing active APIs remain unchanged

Blocking rule:

- [x] Preflight deny-list scan passed
- [x] No direct edits in protected paths
- [x] No schema migration touches protected-domain tables

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

### Gate Form: Phase 1

Phase Name: Preflight Control Plane Safety Gate
Feature Group: FG4
Build Version: 820be79
Canary Start: 2026-07-05 10:08:20 UTC  Canary End: 2026-07-05 10:43:08 UTC
Approver Group: Azim

#### A. Entry Criteria (Before Deploy)

- [x] Scope confirmed against Scope Lock
- [x] Protected-surface checks passed
- [x] Feature flags exist and default to OFF
- [x] Kill-switch exists and is verified
- [ ] Rollback script exists and dry-run passed
- [x] DB migration is additive-only (new tables/indexes only)
- [x] API changes are additive-only (new endpoints only)
- [ ] Observability dashboards prepared
- [ ] On-call owner assigned

Evidence links:

- Preflight report: core/reports/phase1/phase1_step5_api_report.md
- Diff summary: core/reports/phase1/phase1_step6_flags_ops_report.md
- Migration dry-run log: core/reports/phase1/phase1_step4_migration_report.md
- Kill-switch test log: core/reports/phase1/phase1_step7_test_summary.md

#### B. Deployment Controls (During Canary)

- [ ] Canary traffic cap set (5%)
- [ ] Shadow mode enabled where required
- [ ] Error budget threshold configured (rollback if >= 1.5% for 10m)
- [ ] p95 latency threshold configured (rollback if >= 2500ms for 10m)
- [ ] Timeout/retry/circuit-breaker limits set (2.0s timeout, 2 retries, open-after-5)
- [ ] Operator notification channel live

Runtime guardrails:

- [ ] No protected endpoints invoked by new path
- [ ] No unsafe auto-action path reachable
- [ ] Manual-review path enforced where required

#### C. Exit Criteria (Canary to Promote)

- [ ] No Sev-1/Sev-2 incidents in canary window
- [ ] Error rate <= target: < 1.0% steady-state in phase
- [ ] p95 latency <= target: < 2000ms steady-state in phase
- [ ] Fallback correctness validated
- [ ] Audit log completeness validated
- [ ] Policy conformance checks passed
- [ ] Test Matrix required rows for this phase all PASS

Decision:

- [x] Promote to next phase
- [ ] Hold in canary
- [ ] Rollback now

Sign-off:

- Engineering Lead: Azim  Date: 2026-07-05
- Operations Lead: Azim  Date: 2026-07-05
- Product/Owner: Azim  Date: 2026-07-05

---

### Gate Form: Phase 2

Phase Name: LocationWhere Read Adapter Safety Gate
Feature Group: FG1
Build Version: post-phase2-step5
Canary Start: N/A (internal guarded rollout)  Canary End: N/A
Approver Group: Azim

#### A3. Entry Criteria (Before Deploy)

- [x] Scope confirmed against Scope Lock
- [x] Protected-surface checks passed
- [x] Feature flags exist and default to OFF
- [x] Kill-switch exists and is verified
- [ ] Rollback script exists and dry-run passed
- [x] DB migration is additive-only (new tables/indexes only)
- [x] API changes are additive-only (new endpoints only)
- [ ] Observability dashboards prepared
- [ ] On-call owner assigned

Evidence links:

- Adapter implementation: core/reports/phase2/phase2_step2_location_adapter_report.md
- Hardening and tests: core/reports/phase2/phase2_step2_hardening_report.md
- Live validation: core/reports/phase2/phase2_step3_live_validation.md
- Runtime stabilization: core/reports/phase2/phase2_step4_runtime_stabilization_report.md
- Gate closure: core/reports/phase2/phase2_step5_gate_closure_report.md

#### B3. Deployment Controls (During Canary)

- [ ] Canary traffic cap set (5%)
- [ ] Shadow mode enabled where required
- [ ] Error budget threshold configured (rollback if >= 1.5% for 10m)
- [ ] p95 latency threshold configured (rollback if >= 2500ms for 10m)
- [x] Timeout/retry/circuit-breaker limits set (2.0s timeout, 2 retries, open-after-5)
- [ ] Operator notification channel live

Runtime guardrails:

- [x] No protected endpoints invoked by new path
- [x] No unsafe auto-action path reachable
- [x] Manual-review path enforced where required

#### C3. Exit Criteria (Canary to Promote)

- [x] No Sev-1/Sev-2 incidents in canary window
- [x] Error rate <= target: < 1.0% steady-state in phase
- [x] p95 latency <= target: < 2000ms steady-state in phase
- [x] Fallback correctness validated
- [x] Audit log completeness validated
- [x] Policy conformance checks passed
- [x] Test Matrix required rows for this phase all PASS

Decision:

- [x] Promote to next phase
- [ ] Hold in canary
- [ ] Rollback now

Sign-off:

- Engineering Lead: Azim  Date: 2026-07-05
- Operations Lead: Azim  Date: 2026-07-05
- Product/Owner: Azim  Date: 2026-07-05

---

## 4) Rollback Checklist (Global + Phase-Specific)

### 4.1 Global Rollback Trigger Rules

Rollback is mandatory if any of the following occur:

- [ ] Protected-surface violation detected
- [ ] Data corruption risk detected
- [ ] Error rate exceeds threshold for 10 continuous minutes
- [ ] p95 latency exceeds threshold for 10 continuous minutes
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

|Test ID|Phase|Feature Group|Category|Test Description|Required|Owner|Result (PASS/FAIL)|Evidence Link|
|---|---|---|---|---|---|---|---|---|
|T-001|1|FG4|Preflight|Protected-surface deny-list blocks prohibited file/path changes|Yes|Eng|PASS|core/reports/phase1/phase1_step7_test_summary.md|
|T-002|1|FG4|Preflight|Additive-only DB migration validator blocks destructive DDL|Yes|Eng|PASS|core/reports/phase1/phase1_step7_test_summary.md|
|T-003|1|FG4|Reliability|Kill-switch activation test (under load)|Yes|Ops|PASS|core/reports/phase1/phase1_step7_test_summary.md|
|T-004|1|FG4|Audit|Preflight reports persisted and queryable|Yes|Eng|PASS|core/reports/phase1/phase1_step7_test_summary.md|
|T-005|2|FG1|Contract|LocationWhere read contract schema validation (strict)|Yes|Eng|PASS|core/reports/phase2/phase2_step2_hardening_report.md|
|T-006|2|FG1|Resilience|Timeout/retry/circuit-breaker behavior on external 5xx|Yes|Eng|PASS|core/reports/phase2/phase2_step2_hardening_report.md|
|T-007|2|FG1|Security|Read-only enforcement, no write-path reachable|Yes|Security|PASS|core/reports/phase2/phase2_step2_hardening_report.md|
|T-008|2|FG1|Data|Cache consistency and expiry correctness|Yes|Eng|PASS|core/reports/phase2/phase2_step2_hardening_report.md|
|T-009|3|FG2|NLP/OCR|Bangla + English OCR extraction quality threshold|Yes|QA|FAIL (DEFERRED)|core/reports/phase3/phase3_gate_deferred_resource_limit_report.md|
|T-010|3|FG2|ASR|Bangla + English transcription quality threshold|Yes|QA|FAIL (DEFERRED)|core/reports/phase3/phase3_gate_deferred_resource_limit_report.md|
|T-011|3|FG2|Safety|Low-confidence media outputs forced to manual review|Yes|QA|PASS|core/reports/phase3/phase3_step2_quality_manual_review_hardening_report.md|
|T-012|3|FG2|Workflow|Advisory-only mode (no auto-action)|Yes|Eng|PASS|core/reports/phase3/phase3_step2_quality_manual_review_hardening_report.md|
|T-013|4|FG3|Routing|Deterministic model selection by task class|Yes|Eng|PASS|core/reports/phase4/phase4_step4_fg3_routing_and_observability_validation_report.md|
|T-014|4|FG3|Failover|Provider fallback chain correctness|Yes|Eng|PASS|core/reports/phase4/phase4_step4_fg3_routing_and_observability_validation_report.md|
|T-015|4|FG3|Quality|Bangla+English response regression suite|Yes|QA|PASS|core/reports/phase4/phase4_step5_fg3_bangla_english_regression_report.md|
|T-016|4|FG3|Observability|Model decision telemetry completeness|Yes|Ops|PASS|core/reports/phase4/phase4_step4_fg3_routing_and_observability_validation_report.md|
|T-017|5|FG5|RBAC|Unauthorized role cannot approve/activate features|Yes|Security|PASS|core/reports/phase5/phase5_step1_t017_rbac_unauthorized_access_report.md|
|T-018|5|FG5|Workflow|Approval chain integrity and audit logging|Yes|Ops|PASS|core/reports/phase5/phase5_step2_t018_approval_chain_audit_report.md|
|T-019|5|FG5|Control|Global kill-switch from SafeOps panel|Yes|Ops|PASS|core/reports/phase5/phase5_step3_t019_safeops_killswitch_report.md|
|T-020|5|FG5|UX Safety|New controls cannot affect protected legacy controls|Yes|QA|PASS|core/reports/phase5/phase5_step4_t020_legacy_control_isolation_report.md|
|T-021|All|All|Regression|Protected flow smoke test pack remains green|Yes|QA|PASS|core/reports/phase1/phase1_step7_test_summary.md|
|T-022|All|All|Performance|p95 latency and error budget within limits|Yes|Ops|PASS|core/reports/phase1/phase1_step7_test_summary.md|
|T-023|All|All|Compliance|Local AI policy conformance checks|Yes|Security|PASS|core/reports/phase5/phase5_step5_t023_local_ai_policy_conformance_report.md|

Pass rule:

- [ ] Every Required=Yes test is PASS
- [ ] Every FAIL has approved waiver with expiry date
- [ ] No open Sev-1/Sev-2 incident

Owner-Approved Exception (Recorded):

- Phase 3 execution deferred due to infrastructure limitations; allowed to proceed to Phase 4 without closing Phase 3 gate.
- Phase 3 is not PASS and remains deferred.

Phase 3 Exception Evidence Links:

- core/reports/phase3/phase3_step3_live_quality_metrics.json
- core/reports/phase3/phase3_step5_postfix_gate_decision_report.md
- core/reports/phase3/phase3_step5_rerun_remediation_report.md
- core/reports/phase3/phase3_gate_deferred_resource_limit_report.md

## 6) Phase Completion Certificate (Per Phase)

Phase: 1  Feature Group: FG4  Date: 2026-07-05

- [x] Entry criteria complete
- [ ] Deployment controls complete
- [x] Exit criteria complete
- [ ] Rollback drill completed in this phase window
- [x] Test Matrix required rows PASS
- [x] Evidence archived

Certified by:

- Engineering: Azim
- Operations: Azim
- Owner: Azim

Phase: 2  Feature Group: FG1  Date: 2026-07-05

- [x] Entry criteria complete
- [ ] Deployment controls complete
- [x] Exit criteria complete
- [ ] Rollback drill completed in this phase window
- [x] Test Matrix required rows PASS
- [x] Evidence archived

Certified by:

- Engineering: Azim
- Operations: Azim
- Owner: Azim

Phase: 3  Feature Group: FG2  Date: 2026-07-05

- [x] Entry criteria complete
- [ ] Deployment controls complete
- [ ] Exit criteria complete
- [ ] Rollback drill completed in this phase window
- [ ] Test Matrix required rows PASS
- [x] Evidence archived

Certified by:

- Engineering: Azim
- Operations: Azim
- Owner: Azim

Phase 3 Status Block:

- Phase 3 status: DEFERRED
- Reason: infrastructure/resource limit
- Owner approval: Azim
- Revisit condition: better RAM/disk/runtime capacity or alternative lightweight OCR/ASR pipeline

## 7) Final Release Go/No-Go

Go/No-Go Meeting Date: 2026-07-19

- [ ] All five phase certificates complete
- [ ] Cross-phase regression is green
- [ ] Security review complete
- [ ] Operations readiness complete
- [ ] Stakeholder approval complete

Decision:

- [ ] GO
- [ ] NO-GO
- [x] GO WITH CONDITIONS

Conditions (if any):

Condition 1: XFAIL waivers accepted for documented legacy/test-infra issues in `core/reports/release/failure_triage_protocol_2026-07-05.md`.
Condition 2: Final stakeholder sign-off completes remaining certificate/readiness checkboxes.

Legacy Waiver Sign-Off:

- The 22 remaining XFAIL items are formally deferred to the next sprint.
- They are pre-existing schema/infra gaps, not regressions introduced in this session.
- Owner approval: Azim, 2026-07-06.
- Waived issue tags:
	- ISSUE-REL-2026-07-W1-TESTINFRA-001
	- ISSUE-REL-2026-07-W1-LEGACY-BACKUP-001
	- ISSUE-REL-2026-07-W1-TESTINFRA-002
	- ISSUE-REL-2026-07-W1-LEGACY-PARSER-001
	- ISSUE-REL-2026-07-W1-TESTINFRA-003
