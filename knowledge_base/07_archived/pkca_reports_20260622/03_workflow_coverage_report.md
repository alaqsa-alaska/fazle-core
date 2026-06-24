---
title: PKCA Report 03: Workflow Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 03: Workflow Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Workflow Inventory and Coverage

### WF-01: Inbound Message Full Routing Workflow — 10% Covered

**Source:** `modules/bridge_poller` + `modules/message_router`

**Production Workflow:**
```
INBOUND → Bridge Poll (1–30s adaptive) → LID Resolution → Dedup
→ OCR/Voice branch → Persist to wbom_whatsapp_messages → process_message()
→ Silent-Skip Check → Identity Resolution → 15-Step Role Routing
→ Reply Delivery (cooldown→loop→flood→injection→poison checks → SEND or DRAFT)
```

**KB Documentation:** `05_workflows/attendance_workflow.md`, `05_workflows/escort_workflow.md`, `06_developer_system/workflow_engine.md`

**What's Covered:** Basic workflow event names (message.received, identity.resolved, draft.created). Each sub-workflow (attendance, escort, payment) is partially covered. General "role first, topic second" principle is documented.

**What's Missing:**
- Full 15-step routing priority order
- Silent-skip logic and trigger conditions
- Draft-always gate (4 roles always drafted)
- Complaint phrase protection (10 Bangla phrases)
- Advance request phrase protection (5 phrases)
- Loop detection (3/120s → 600s pause)
- Keyword flood protection (3/5m → 15m block)
- 18 prompt injection detection patterns
- 16 outbound poison filter patterns
- Reply cooldown (60s)
- Adaptive poll interval (1s → 30s, 1.5× per idle)
- Bridge cursor and dedup mechanisms
- office_location fast path (bypasses AI)
- LID resolution and unresolved persistence

**Coverage Score: 10%**

---

### WF-02: Escort Client Order Workflow — 30% Covered

**Source:** `modules/escort` (1047 lines)

**Production Workflow:**
```
CLIENT MESSAGE (escort/vessel keywords) → parse_escort_message()
→ Extract: MV name, lighter vessel(s), master mobile(s), importer, cargo, destination
→ Save to wbom_escort_programs (status='draft', extras in remarks JSON)
→ Build admin draft(s) — one per lighter vessel
→ Return ("", admin_note) — NO reply to client
→ Admin fills escort name/mobile → is_completed_escort_draft()
→ handle_admin_escort_completion() → Send slip to client, update DB to 'confirmed'
```

**KB Documentation:** `05_workflows/escort_workflow.md`, `04_business_rules/escort_business_rules.md`, `05_workflows/client_order_workflow.md`

**What's Covered:** Client order → draft → admin review → confirm → assignment. Required order fields listed. Admin review is mandatory.

**What's Missing:**
- 4 parser formats (labeled, inline, MV-block, numbered)
- is_completed_escort_draft() detection (looks for [ESCORT NAME: / ESCORT MOBILE:])
- Client NEVER receives direct reply (always admin draft only)
- remarks JSON structure (sender_phone, source_bridge)
- Parser confidence and fallback behavior
- ESCORTCONFIRM command as alternative to completing admin draft

**Coverage Score: 30%**

---

### WF-03: Escort Release + Payment Workflow — 25% Covered

**Source:** `modules/escort_lifecycle` (716 lines)

**Production Workflow:**
```
EMPLOYEE sends release intent (text) OR image (release slip)
→ Text: "Release request received, admin will send [RELEASE CONFIRMED]"
→ Image: OCR → handle_ocr_release_slip() → build_release_draft() → admin bridge
→ ADMIN sends [RELEASE CONFIRMED] outbound message
→ parse_release_confirmation() → find_active_program_for_employee()
→ close_program() (status→Completed, idempotent)
→ backfill_attendance_for_program() (per day, ON CONFLICT DO NOTHING)
→ create_escort_payment_draft() → Formula: (12000/30 × duty_days) - food - conv - advances
→ Write escort_roster_entries
→ Return draft text for admin → accountant notification
```

**KB Documentation:** `05_workflows/release_slip_workflow.md`, `01_employee_knowledge/release_slip.md`

**What's Covered:** Release slip photo submission, required fields, manual review triggers. High-level payment calculation mentioned.

**What's Missing:**
- Approved payment formula (CON-01: 12000/30×days)
- Food policy (CON-04: 150/day with time exceptions — release before 10AM, board after 3PM)
- Approved transport rates (CON-03: Dhaka/Narayanganj=600, Faridpur=700, Mongla=800, Barishal=900, Khulna=1000, default=600)
- OCR confidence <40% triggers warning in draft
- Duty days >90 triggers SUSPICIOUS flag
- Release date future/past (>1 year) validation
- Attendance backfill mechanism (one row per day, ON CONFLICT DO NOTHING)
- idempotent close_program() behavior

**Coverage Score: 25%**

---

### WF-04: Attendance Workflow — 40% Covered

**Source:** `modules/attendance` (246L) + `modules/attendance_parser` (280L)

**Production Workflow:**
```
SUPERVISOR or EMPLOYEE sends attendance message
→ is_supervisor_attendance() OR is_attendance_message() → true
→ parse_attendance() — extract name, mobile, shift (D/N), date
   Date patterns: DD-MM-YYYY, YYYY-MM-DD
   Shift: D(ay) / N(ight) (default D if invalid)
   Name: label extraction → bare name heuristic
→ create_attendance_draft() — lookup employee by mobile or name
→ Build draft with employee_id, date, shift, status=Present
→ Save to fazle_draft_replies
→ Return confirmation + admin_note
→ Admin APPROVE <draft_id>
→ save_attendance() → wbom_attendance (ON CONFLICT UPDATE)
```

**KB Documentation:** `05_workflows/attendance_workflow.md`, `04_business_rules/attendance_business_rules.md`, `02_admin_knowledge/admin_attendance_handling.md`

**What's Covered:** Guard=12h/1 day, escort=24h/1 day, shift times, admin APPROVE required, duplicate check, confirmation.

**What's Missing:**
- attendance vs attendance_parser module distinction (guard=simple present/absent; attendance_parser=structured escort-style with date/shift/mobile)
- parse_attendance() exact field extraction logic
- wbom_attendance ON CONFLICT UPDATE behavior
- Attendance backfill via escort lifecycle (separate from manual attendance)
- 2-day deduction rule for 1 absent day

**Coverage Score: 40%**

---

### WF-05: Recruitment Funnel Workflow — 30% Covered

**Source:** `modules/recruitment_flow` (365L) + `modules/recruitment_ai` (228L)

**Production Workflow:**
```
UNKNOWN sends message with job keyword
→ recruitment_eligibility() check
   Role must be unknown/candidate/new_lead (operational identities blocked)
   Text must contain INTAKE_KEYWORDS (10 terms)
→ Create fazle_recruitment_sessions (step=name)
→ Collect 6 steps: name → age (18-55) → area → position (1-9) → experience → phone
→ Score: experience(60/40/20) + position(20) + completeness(20) → Hot/Warm/Cold
   Hot≥70, Warm≥40, Cold<40
→ Reply INTAKE_COMPLETE_MSG + session → stage='scored'
→ Session TTL: 24 hours
```

**KB Documentation:** `05_workflows/recruitment_workflow.md`, `04_business_rules/recruitment_business_rules.md`, `03_ai_identity/candidate_identity.md`

**What's Covered:** 6-step collection, auto-reply allowed, manual review triggers, required candidate data.

**What's Missing:**
- VALID_POSITIONS (9 exact: Escort, Survey Scout, Security Guard, Security Supervisor, Assistant Supervisor, Operation Officer, Security In-Charge, Marketing Officer, Ghat Supervisor)
- _compute_score formula (exact points)
- Hot/Warm/Cold band thresholds
- INTAKE_KEYWORDS (10 terms)
- OPERATIONAL_ROLES blocked from recruitment
- SESSION_TTL = 24h

**Coverage Score: 30%**

---

### WF-06: Admin Command Workflow — 5% Covered

**Source:** `modules/admin_commands` (1297L) + `modules/rbac` (341L)

**Production Workflow:**
```
ADMIN sends command via WhatsApp
→ is_admin_command() — regex match
→ Dedup check (30s SHA1 window, 256 entries)
→ RBAC check via rbac.check_permission()
   Lookup admin by phone → get role level → compare to command requirement
→ Execute command (38 commands across 7 categories)
→ Record in fazle_admin_audit
```

**KB Documentation:** `02_admin_knowledge/admin_operations_overview.md` (approval/reject/payment mentioned abstractly)

**What's Covered:** Admin responsibilities listed (review drafts, handle payments). 4 operational responsibilities mentioned.

**What's Missing:**
- All 38 command syntaxes
- RBAC role levels (viewer < operator < accountant < admin < superadmin)
- Command-to-role mapping (38 commands, 5 roles)
- Bangla digit normalization
- Multi-ID APPROVE support
- SHA1 dedup mechanism
- fazle_admin_audit schema

**Coverage Score: 5%**

---

### WF-07: Payroll Workflow — 20% Covered

**Source:** `modules/payroll` (338L) + `modules/scheduler` (690L)

**Production Workflow:**
```
SCHEDULER (02:00 Asia/Dhaka daily)
→ compute_all_for_period(year, month)
   For each Active employee:
   → compute_run(employee_id, year, month)
     Idempotency: skip if existing active run exists
     Count completed escort programs in month
     Calculate: 12000/30 × duty_days - advances
     Write wbom_payroll_runs (status='draft')
     Write wbom_payroll_run_items (basic, programs, advances)
→ State transitions (all require actor + audit log):
   draft → reviewed (submit_run)
   reviewed → approved (approve_run)
   approved → locked (lock_run)
   locked → paid (mark_paid with method + reference)
   any → cancelled (cancel_run + reason)
→ Each transition writes wbom_payroll_approval_log
```

**KB Documentation:** `05_workflows/salary_workflow.md` (high-level only)

**What's Covered:** Salary inputs (attendance, leave, advance, bonus, transport). High-level payment flow.

**What's Missing:**
- 6-state machine with exact transitions
- Approved formula (CON-02: 12000/30×days)
- Idempotency behavior (UNIQUE on employee_id+period+month WHERE status!='cancelled')
- compute_all_for_period() trigger at 02:00
- PAYROLL COMPUTE/SUBMIT/APPROVE/LOCK/PAID/CANCEL commands
- wbom_payroll_runs and wbom_payroll_run_items tables
- wbom_payroll_approval_log audit trail

**Coverage Score: 20%**

---

### WF-08: Employee Verification Workflow — 10% Covered

**Source:** `modules/employee_verification` (385L)

**Production Workflow:**
```
EMPLOYEE requests advance or mentions release slip
→ Check for active verification session
→ If no session: detect request type (advance vs release)
→ Create session with step=STEP_SELFIE
→ Step 1 (STEP_SELFIE): Ask for selfie from duty location
→ Step 2 (STEP_SLIP): Employee sends image → ask for duty/release slip
→ Step 3 (STEP_METHOD): Employee sends image → ask for bKash/Nagad/cash number
→ Step 4 (STEP_DONE): Employee confirms payment method → create draft → send to admin
→ Identity mismatch: sender not in wbom_employees but matches master_mobile → inform to use registered number
```

**KB Documentation:** `01_employee_knowledge/faq_employee.md` mentions "duty selfie" and "bKash/Nagad number" requirement.

**What's Covered:** Employee advance request requires selfie + duty slip + payment method. Mentioned in FAQ.

**What's Missing:**
- 5-step verification session architecture
- Step names (STEP_SELFIE, STEP_SLIP, STEP_METHOD, STEP_DONE)
- Session persistence in fazle_draft_replies (intent='verification')
- Identity mismatch detection and response
- Session cleanup (close stale open sessions)
- Release slip flow shortcut (if image already sent)

**Coverage Score: 10%**

---

### WF-09: Outbound Message Delivery Workflow — 0% Covered

**Source:** `modules/outbound` (255L)

**Production Workflow:**
```
enqueue(recipient, body, source_bridge, idempotency_key)
→ INSERT INTO fazle_outbound_queue ON CONFLICT (idempotency_key) DO NOTHING
→ sweep_once() polls due rows
→ Send via source_bridge channel (bridge1/bridge2/meta/messenger/comment)
→ On failure: exponential backoff retry (max_attempts times)
→ On max_attempts exceeded: status='dlq' (Dead Letter Queue)
→ Circuit breaker integration: bridge failure → enqueue alert to admin
→ OUTBOUND_ENABLED env controls activation
```

**KB Documentation:** None.

**What's Covered:** Nothing.

**Missing:**
- Persistent outbound queue architecture
- DLQ concept and actionable_dlq_count metric
- Retry with exponential backoff
- Multi-channel send (bridge1, bridge2, meta, messenger, facebook_comment)
- Circuit breaker integration
- idempotency_key deduplication
- OUTBOUND_ENABLED flag

**Coverage Score: 0%**

---

### WF-10: Contact Sync Workflow — 0% Covered

**Source:** `modules/contact_sync` (356L)

**Production Workflow:**
```
Sources: bridge1 (whatsapp1/store/whatsapp.db), bridge2 (bridges/bridge2/store/whatsapp.db), Meta webhook
→ Canonical phone: 8801XXXXXXXXXX (13-digit BD mobile)
→ One row per phone in wbom_contacts (UNIQUE on whatsapp_number + platform)
→ display_name = longest of (full_name, push_name, saved_name)
→ Merge: update display_name only if new name is longer
→ Dedup: ON CONFLICT DO UPDATE
→ Sync triggers:
   a) Full sync on startup
   b) Incremental sync every bridge poll (last 10-min window)
   c) On-demand via sync_all_contacts()
→ Skip: non-BD phones, LID JIDs (@lid)
```

**KB Documentation:** None.

**Coverage Score: 0%**

---

## Workflow Coverage Summary

| Workflow | Coverage % | Priority for Documentation |
|---|---|---|
| WF-01: Inbound Message Routing | 10% | CRITICAL |
| WF-02: Escort Client Order | 30% | HIGH |
| WF-03: Escort Release + Payment | 25% | CRITICAL |
| WF-04: Attendance | 40% | MEDIUM |
| WF-05: Recruitment Funnel | 30% | MEDIUM |
| WF-06: Admin Command | 5% | CRITICAL |
| WF-07: Payroll | 20% | HIGH |
| WF-08: Employee Verification | 10% | HIGH |
| WF-09: Outbound Message Delivery | 0% | HIGH |
| WF-10: Contact Sync | 0% | MEDIUM |

**Average Workflow Coverage: 21%**
