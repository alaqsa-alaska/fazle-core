# Phase 1 Execution Sheet (FG4)

Status: Ready To Fill
Release ID: REL-2026-07-W1
Phase: 1
Feature Group: FG4 (Evidence/Audit/Preflight Control Plane)
Change Window: 2026-07-05 22:00 to 2026-07-19 23:59 (local)
Execution Date: ____________________

## 0) Team Assignment (Fill First)

- Engineering Lead: OWNER_ENG_LEAD
- Operations Lead: OWNER_OPS_LEAD
- Security Lead: OWNER_SECURITY_LEAD
- QA Lead: OWNER_QA_LEAD
- Product/Business Owner: OWNER_PRODUCT
- Incident Commander: OWNER_IC
- Scribe/Recorder: OWNER_SCRIBE

## 1) Hard Safety Boundary (Must Be True Before Any Action)

- [ ] Payroll module untouched
- [ ] Escort-roster module untouched
- [ ] WhatsApp MCP bridges untouched
- [ ] Meta Business WhatsApp in/out flows untouched
- [ ] Backend-first message saving workflow untouched
- [ ] Existing bridge connections untouched
- [ ] QR states untouched
- [ ] Existing active APIs untouched

Protected-surface verification evidence:

- Protected paths deny-list config file: ____________________
- Preflight deny-list scan output: ____________________
- Reviewer confirmation (name/date): ____________________

## 2) FG4 Scope Lock (Phase 1 Only)

Allowed objective:

- Build/enable preflight + evidence control plane only (no behavior change in protected runtime)

Explicitly disallowed this phase:

- Any adapter integration to external systems
- Any OCR/audio runtime path changes
- Any model router behavior changes
- Any new operator action that can impact protected legacy controls

Scope evidence:

- Planned work item list: ____________________
- Out-of-scope checklist reviewed by: ____________________

## 3) Entry Criteria Gate (Complete Before Deploy)

### 3.1 Preflight Controls

- [ ] Preflight deny-list rules implemented/configured
- [ ] Additive-only DB migration validation in place
- [ ] Additive-only API change validation in place
- [ ] Protected-surface block test PASS

Evidence fields (exact):

- E-01 Preflight Rules File: ____________________
- E-02 Preflight Execution Log: ____________________
- E-03 Protected-Surface Block Test Log: ____________________
- E-04 API Additive Validator Output: ____________________
- E-05 DB Additive Validator Output: ____________________

### 3.2 Deployment Safety

- [ ] Feature flag created and default OFF
- [ ] Kill-switch exists and tested
- [ ] Rollback script exists and dry-run PASS
- [ ] Monitoring dashboard prepared
- [ ] On-call + escalation tree confirmed

Evidence fields (exact):

- E-06 Feature Flag Registry Entry: ____________________
- E-07 Kill-Switch Test Evidence: ____________________
- E-08 Rollback Dry-Run Log: ____________________
- E-09 Dashboard Link: ____________________
- E-10 On-call Roster Snapshot: ____________________

### 3.3 Change Provenance

- [ ] Diff summary produced
- [ ] Release note draft produced
- [ ] Risk acceptance reviewed by Product/Owner

Evidence fields (exact):

- E-11 Diff Summary Link: ____________________
- E-12 Release Notes Draft Link: ____________________
- E-13 Risk Acceptance Note: ____________________

Entry Gate Decision:

- [ ] PASS (proceed to canary)
- [ ] FAIL (stop and remediate)

Signed:

- Engineering Lead: ____________________  Date: __________
- Security Lead: _______________________  Date: __________
- Operations Lead: _____________________  Date: __________

## 4) Canary Plan (FG4)

Canary progression default:

- Step A: 5% for 60 min
- Step B: 15% for 60 min
- Step C: 30% for 90 min
- Step D: 60% for 90 min
- Step E: 100% after final sign-off

Runtime rollback triggers:

- Error rate >= 1.5% for 10 continuous minutes
- p95 latency >= 2500 ms for 10 continuous minutes
- Any Sev-1 incident
- Two or more Sev-2 incidents in canary window
- Any protected-surface violation signal

Canary run evidence:

- E-14 Canary Step A Metrics: ____________________
- E-15 Canary Step B Metrics: ____________________
- E-16 Canary Step C Metrics: ____________________
- E-17 Canary Step D Metrics: ____________________
- E-18 Incident Timeline (if any): ____________________

## 5) FG4 Test Matrix (Required for Phase 1 Exit)

|Test ID|Required|Owner|Status (PASS/FAIL)|Evidence|
|---|---|---|---|---|
|T-001 Protected deny-list blocks prohibited path changes|Yes|OWNER_ENG_LEAD|||
|T-002 Additive-only DB validator blocks destructive DDL|Yes|OWNER_ENG_LEAD|||
|T-003 Kill-switch activation under load|Yes|OWNER_OPS_LEAD|||
|T-004 Preflight reports persisted and queryable|Yes|OWNER_ENG_LEAD|||
|T-021 Protected flow smoke tests remain green|Yes|OWNER_QA_LEAD|||
|T-022 p95 latency and error budget within limits|Yes|OWNER_OPS_LEAD|||
|T-023 Local AI policy conformance checks|Yes|OWNER_SECURITY_LEAD|||

Pass rule:

- [ ] All required tests PASS
- [ ] No open Sev-1/Sev-2 unresolved
- [ ] Any waived item has written expiry and approver

## 6) Rollback Runbook (FG4-Specific)

### 6.1 Trigger Check

- [ ] Trigger condition confirmed
- [ ] Incident Commander informed
- [ ] Deploy freeze announced

### 6.2 Execution Steps

- [ ] Feature flag OFF
- [ ] FG4 kill-switch ON
- [ ] Revert to prior stable artifact
- [ ] Validate health checks
- [ ] Validate protected flow smoke tests
- [ ] Capture rollback summary and timeline

Rollback evidence fields:

- E-19 Incident Declaration Log: ____________________
- E-20 Rollback Command/Action Log: ____________________
- E-21 Post-Rollback Health Report: ____________________
- E-22 Protected Flow Smoke Test Report: ____________________
- E-23 Stakeholder Notification Proof: ____________________

## 7) Exit Criteria Gate (Phase 1 Completion)

- [ ] Entry criteria were fully satisfied before canary
- [ ] Canary run completed per plan
- [ ] Required FG4 test matrix rows all PASS
- [ ] Rollback drill completed or rollback execution complete
- [ ] Evidence package complete and archived

Evidence package index:

- E-01 through E-23 location index: ____________________
- Archive folder/checksum reference: ____________________

Exit Gate Decision:

- [ ] PROMOTE to Phase 2 (FG1)
- [ ] HOLD in Phase 1
- [ ] ROLLBACK and rework

Final sign-off:

- Engineering Lead: OWNER_ENG_LEAD  Date: __________
- Operations Lead: OWNER_OPS_LEAD  Date: __________
- Security Lead: OWNER_SECURITY_LEAD  Date: __________
- QA Lead: OWNER_QA_LEAD  Date: __________
- Product/Owner: OWNER_PRODUCT  Date: __________

## 8) Handover to Phase 2 (FG1)

Required handover outputs:

- [ ] Phase 1 completion certificate attached
- [ ] Open risks list with owners and due dates
- [ ] Lessons learned summary
- [ ] Approved go-forward constraints unchanged

Handover evidence:

- E-24 Completion Certificate: ____________________
- E-25 Open Risks Register: ____________________
- E-26 Lessons Learned Note: ____________________
