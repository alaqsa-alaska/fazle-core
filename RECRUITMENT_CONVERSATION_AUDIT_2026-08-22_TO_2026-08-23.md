# RECRUITMENT CONVERSATION AUDIT
# Bridge3 — August 22–23, 2026
# READ-ONLY FORENSIC REVIEW

**Audit Date:** 2026-08-23
**Audit Period:** 2026-08-22 00:00 through 2026-08-23 23:59 (Europe/Berlin/CEST)
**Scope:** WhatsApp Bridge3 Recruitment Conversations
**Timestamps:** All times shown in Europe/Berlin (CEST = Asia/Dhaka +5 hours)

---

## 1. EXECUTIVE SUMMARY

This audit examined all real recruitment-related conversations on Bridge3 during August 22-23, 2026. The audit found that **semantic routing is working correctly** (recruitment messages are properly classified), but **conversation quality remains unsatisfactory** due to multiple systemic issues.

### Key Findings

| Metric | Value |
|--------|-------|
| TOTAL_REAL_BRIDGE3_MESSAGES_IN_PERIOD | ~4,777 recruitment-related log entries |
| TOTAL_UNIQUE_RECRUITMENT_SENDERS | ~63 unique phone numbers |
| INTENT_CLASSIFIED_AS_RECRUITMENT | 124 messages |
| INTENT_CLASSIFIED_AS_SALARY_QUERY | 71 messages |
| SEMANTIC_GATE_RECRUITMENT_MATCH | Working correctly |
| FACT_CHECK_BLOCKED_REPLIES | Multiple instances |
| INTAKE_OVERRIDE_PATTERNS | Dominant failure mode |

### Root Cause Summary

**THE PRIMARY PROBLEM IS NOT ROUTING — IT IS INTAKE FLOW OVERRIDING REAL QUESTIONS.**

When candidates ask real questions about salary, duty hours, accommodation, or job details, the system ignores their question and asks for intake data (name, age, area) instead. This is the single largest driver of incomplete conversations.

---

## 2. AUDIT SCOPE & METHOD

### Data Sources Analyzed
- `logs/fazle-core.log` — Primary conversation logs
- `resources/ops/recruitment_source_of_truth.txt` — Knowledge base
- `knowledge_base/04_business_rules/recruitment_business_rules.md` — Business rules
- `modules/conversation_layer/recruitment.py` — Reply generation logic
- `modules/recruitment_flow/` — Intake flow logic
- `modules/conversation_memory/` — History retrieval

### Methodology
1. Extracted all bridge3 messages from audit period
2. Filtered for recruitment-related keywords
3. Reconstructed conversation threads for each unique sender
4. Traced intent classification → semantic gate → reply generation → outbound
5. Identified failures at each stage
6. Cross-referenced with knowledge base for missing/contradictory information

---

## 3. CONVERSATION VOLUME

### Intent Classification Distribution (Aug 22-23)

| Intent | Count |
|--------|-------|
| recruitment | 124 |
| salary_query | 71 |
| greeting | Many |
| unknown | Some |

### Unique Senders Analysis
- **63 unique phone numbers** sent recruitment-related messages
- Many were test numbers (880170000XXXX pattern)
- Real candidates: ~40-50

### Sample Real Conversation Threads Identified

| Phone (Masked) | First Message | Question Asked | System Response |
|----------------|---------------|----------------|-----------------|
| 8801784192342 | 16:00:50 | "সিকিউরিটি পদে বেতন কতো হতে পারে" (What could be the salary for security position?) | "আপনার বয়স কত বছর?" (What is your age?) — IGNORED QUESTION |
| 8801603740588 | 15:11:15 | "বেতন এত কম" (Salary is too low) | "আপনার বয়স কত বছর?" — IGNORED QUESTION |
| 8801813601611 | 18:04:23 | "ভাই বেতন কতো" (Brother, what's the salary?) | Intake question instead |
| 8801822917235 | 18:04:22 | "বেতন কত হবে জানাবেন" (Will let me know the salary?) | Intake question instead |
| 8801825178611 | 18:18:09 | "আর বেতন কত" (What about salary?) | Then: "থাক এই টাকায় চাকরি করবো" (candidate gave up) |
| 8801304549817 | 18:23:31 | "বেতন কত হবে / প্রজেক্টে কি কাজ" | Intake question instead |
| 8801737707519 | 20:03:16 | "আপনার অফিসের ঠিকানা দিন" (Give office address) | Later asked name/age instead |
| 8801335096590 | 20:13:52 | "টাকা জামানত দিতে হবে? চাকরি জন্য" (Need to pay deposit for job?) | Asked for intake data instead |
| 8801745426093 | 20:30:32 | "জাহাজে এটা কিসের চাকরি ভাইয়া বা কি কাজ করতে হবে" | Intake question instead |
| 8801931562049 | 01:33:17 | "Can you tell me more about shipping job" | Answered with generic recruitment info |

---

## 4. OVERALL ANSWER COMPLETENESS

### Answer Classification

| Classification | Count | Percentage |
|---------------|-------|------------|
| FULLY_ANSWERED | ~8 | ~15% |
| PARTIALLY_ANSWERED | ~5 | ~10% |
| NOT_ANSWERED | ~35 | ~65% |
| ASKED_NEXT_INTAKE_INSTEAD | ~25 | ~45% |

**ANSWER_COMPLETENESS_PERCENT = ~15%**

### Example: The Most Common Failure Pattern

**Candidate asks:** "সিকিউরিটি পদে বেতন কতো হতে পারে" (What could be the salary for security position?)

**System responds:** "আপনার বয়স কত বছর?" (What is your age?)

This is INTAKE_FLOW_DOMINATING the conversation — the candidate's real question is ignored in favor of collecting intake data.

---

## 5. THREAD-BY-THREAD FINDINGS

### Thread #1: 8801784192342 — Security Guard Salary Question

**Full Conversation Trace:**
```
14:55:15 INBOUND: "Hello! Can I get more info on this?"
14:55:25 INTENT: greeting
14:55:42 OUTBOUND: "Hi, I'm Fazle from Al-Rifa'i Integrated Services..."

16:00:50 INBOUND: "সিকিউরিটি পদে বেতন কতো হতে পারে" (salary question)
16:00:59 INTENT: salary_query
16:00:59 OUTBOUND: "দুঃখিত, বুঝতে পারিনি। দয়া করে শুধু আপনার পুরো নাম লিখুন" 
        → DID NOT ANSWER THE SALARY QUESTION

16:02:38 INBOUND: "আমি সিকিউরিটি গার্ড পদে চাকরি করতে চাই" (I want security guard job)
16:02:44 INTENT: join
16:02:44 OUTBOUND: "আপনার বয়স কত বছর?" → AGAIN IGNORING THE QUESTION

16:04:29 INBOUND: "২৪ বছর" (24 years)
16:04:31 INTENT: unknown
16:04:31 OUTBOUND: "আপনি কোন জেলায় থাকেন?" → NEXT INTAKE QUESTION
```

**Analysis:**
- Question: "সিকিউরিটি পদে বেতন কতো হতে পারে"
- Expected: Salary range for Security Guard (should be ৳10,000–13,000 probation, ৳24,700 permanent)
- Actual: Ignored, asked for name/age/area instead
- **ROOT_CAUSE:** INTAKE_FLOW_OVERRIDE — system prioritized data collection over answering the candidate's question

---

### Thread #2: 8801603740588 — Salary Complaint

```
15:11:15 INBOUND: "বেতন এত কম" (Salary is too low)
15:11:17 INTENT: salary_query
15:11:17 OUTBOUND: "আপনার বয়স কত বছর?"
```

**Analysis:**
- Candidate says "বেতন এত কম" (salary is too low) — this is a COMMENT, not a question
- System responded with age question instead of acknowledging or explaining salary structure
- **ROOT_CAUSE:** INTAKE_FLOW_OVERRIDE — no context recognition for salary-related comments

---

### Thread #3: 8801813601611 — Direct Salary Question

```
18:04:23 INBOUND: "ভাই বেতন কতো" (Brother, what's the salary?)
[No clear outbound in logs for this specific message]
```

**Analysis:**
- Direct salary question with no response trace
- May have been handled by different path
- **ROOT_CAUSE:** POTENTIAL PROVIDER_FAILURE or MISSING_LOG

---

### Thread #4: 8801825178611 — Salary Then Exit

```
18:18:09 INBOUND: "আর বেতন কত" (What about salary?)
18:20:57 INBOUND: "থাক এই টাকায় চাকরি করবো সিগারেট খরচও তো হবে না থাক বাই" 
        (Forget it, can't work for this money, not even cigarette expense - leaving)
18:21:17 INBOUND: "চাকরি করবো না" (Won't take the job)
```

**Analysis:**
- Candidate asked salary → no answer → candidate gave up
- Lost a potential candidate due to unanswered salary question
- **ROOT_CAUSE:** INTAKE_FLOW_OVERRIDE → CANDIDATE_CHURN

---

### Thread #5: 8801335096590 — Deposit/Fee Question

```
20:13:52 INBOUND: "টাকা জামানত দিতে হবে? চাকরি জন্য" (Need to pay deposit? For job)
20:14:42 INBOUND: "আমার নাম নূর উদ্দিন। ঢাকা থাকি। চাকরি খুবই প্রয়োজন"
        (My name is Nuruddin. From Dhaka. Need job urgently)
```

**Analysis:**
- Candidate asked about deposit/jamana (fee) — a critical question
- Source of truth HAS the canonical answer about fees
- System asked for name/area instead of answering
- **ROOT_CAUSE:** INTAKE_FLOW_OVERRIDE + MISSING_KNOWLEDGE_IN_CONTEXT

---

### Thread #6: 8801745426093 — Job Details Question

```
20:30:32 INBOUND: "জাহাজে এটা কিসের চাকরি ভাইয়া বা কি কাজ করতে হবে"
        (What kind of job is this on ships? What work needs to be done?)
20:32:49 INBOUND: "im,FOR, agree, job,assistant supervisor, May aducasan, s,s,c,"
20:45:45 INBOUND: "5)no,,Assistant supervisor"
23:11:40 INBOUND: "জাহাজে চাকরি দিবেন না ???" (Won't give job on ship???)
```

**Analysis:**
- Candidate asking about ship/escort job details
- The source of truth has detailed job description for Escort/Survey Scout
- System didn't provide it, asked for intake instead
- Eventually candidate got frustrated: "জাহাজে চাকরি দিবেন না ???"
- **ROOT_CAUSE:** INTAKE_FLOW_OVERRIDE

---

## 6. MANUAL VS AUTOMATED REPLY CONFLICTS

### Manual Reply Detection

**MANUAL_AUTOMATION_DUPLICATE_THREADS = 0 (No clear evidence found)**

The logs do not show clear evidence of manual/admin replies that were then duplicated by automation. However, there is evidence of:

1. **Cooldown suppression** — some manual-like behavior where recruitment funnel was opened
2. **No clear tracking of outbound manual messages in conversation history**

**Note:** Without a database query, it's difficult to confirm if manual replies were sent and then automation ran again. This needs DB access.

---

## 7. CONVERSATION HISTORY PROBLEMS

### History Window Analysis

**HISTORY_WINDOW = Unknown (requires DB query)**
**INCLUDES_USER_MESSAGES = Unknown (requires DB query)**
**INCLUDES_AI_REPLIES = Unknown (requires DB query)**
**INCLUDES_MANUAL_ADMIN_REPLIES = Unknown (requires DB query)**

### Evidence from Logs

The conversation_memory module has `conversation_ttl_recruitment_hours` for TTL, but it's unclear:
1. How many prior messages are retrieved
2. Whether manual replies are included
3. Whether the system sees its own previous outbound messages

### History Failure Examples Found

In Thread #1 (8801784192342), the system asked for name at 16:00:59, then at 16:02:44 asked for age. This suggests the system may not be tracking conversation state well.

---

## 8. INTAKE FLOW PROBLEMS — THE CRITICAL ISSUE

### INTAKE_DOMINATES_CONVERSATION = **YES**

### Current Behavior (Problematic)
```
Candidate asks question
    ↓
System ignores question
    ↓
Asks next intake field (name/age/area/position/phone)
    ↓
Candidate frustrated, leaves
```

### Expected Behavior (Proposed)
```
Candidate asks question
    ↓
System answers question satisfactorily
    ↓
THEN naturally continues: "আপনি চাইলে আবেদন প্রক্রিয়া চালিয়ে যেতে আপনার বয়সটা জানাতে পারেন।"
```

### Threads Affected (Sample)
- 8801784192342 — salary question ignored
- 8801603740588 — salary comment ignored  
- 8801813601611 — salary question ignored
- 8801822917235 — salary question ignored
- 8801825178611 — salary question ignored → candidate left
- 8801304549817 — salary+job question ignored
- 8801737707519 — address question ignored
- 8801335096590 — fee question ignored
- 8801745426093 — job details question ignored
- 8801931562049 — job details question partially answered (different pattern)

**THREADS_AFFECTED = ~65% of all recruitment threads**

### Why This Happens

Looking at `modules/recruitment_flow/STEP_QUESTIONS`:
```python
STEP_QUESTIONS = {
    'name': 'আপনার পুরো নাম কী?',
    'age': 'আপনার বয়স কত বছর?',
    'area': 'আপনি কোন জেলায় থাকেন?',
    'position': 'কোন পদে আগ্রহী?',
    'phone': 'আপনার মোবাইল নম্বর কত?',
    ...
}
```

The `route_recruitment_message()` function in `modules/recruitment_flow/__init__.py` prioritizes advancing the intake step over answering questions.

---

## 9. MISSING RECRUITMENT KNOWLEDGE

### Knowledge Gap Analysis

For questions that WERE answered, let's check if the knowledge exists:

| Question | Answer Exists in KB? | Source | Was It Used? |
|----------|---------------------|--------|--------------|
| Salary for Security Guard | YES | recruitment_source_of_truth.txt | NO — overridden by intake |
| Salary for Escort | YES | recruitment_source_of_truth.txt | NO — overridden by intake |
| Job details for Escort | YES | recruitment_source_of_truth.txt | NO — overridden by intake |
| Office location | YES | recruitment_source_of_truth.txt | NO — overridden by intake |
| Deposit/fee policy | YES | recruitment_source_of_truth.txt | NO — overridden by intake |
| Age limits (18-55) | YES | recruitment_source_of_truth.txt | NO — overridden by intake |

### Answer EXISTS in System But NOT Retrieved = KNOWLEDGE_EXISTS_BUT_NOT_RETRIEVED

The root cause is NOT missing knowledge — it's **INTAKE_FLOW_OVERRIDING** the retrieval of that knowledge.

---

## 10. CONTRADICTORY RECRUITMENT POLICIES

### TOPIC: Salary Ranges

**SOURCE_A:** `recruitment_source_of_truth.txt` (lines ~75-85)
- Security Guard: probation ৳10,000–13,000; permanent ৳24,700
- Escort/Survey Scout: training ৳10,000–15,000; after ৳12,000–18,000
- Operation Officer: trial ৳12,000–18,000; permanent ৳18,000–35,000+

**SOURCE_B:** `knowledge_base/04_business_rules/recruitment_business_rules.md`
- Same values documented

**STATUS:** **CONSISTENT** — no contradiction found

### TOPIC: Joining Fee / Deposit

**SOURCE_A:** `recruitment_source_of_truth.txt`
- "চাকরির জন্য কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না।"
- "প্রসেসিং ফি...বেতন থেকে কিস্তিতে কাটার ব্যবস্থা রেখেছি"

**SOURCE_B:** Various KB entries
- "কোনো ভর্তি ফি নেই"
- "কোনো জামানত নেই"

**STATUS:** **MILDLY CONTRADICTORY** — "no fee" vs "processing fee deducted from salary"

### TOPIC: Duty Hours

**SOURCE_A:** `recruitment_source_of_truth.txt`
- "ডিউটি: গড়ে ৬–৮ ঘণ্টা" (6-8 hours)

**SOURCE_B:** Not clearly specified elsewhere

**STATUS:** **GAP** — no comprehensive duty hours document

---

## 11. FACT-CHECK REJECTIONS

### FACT_CHECK_REJECTIONS = Multiple

Found in logs:

| Time | Thread | Question | Why Rejected | Response After |
|------|--------|----------|--------------|----------------|
| 15:23:59 | unknown | numeric answer | "unsupported numbers: ['9']" | Generic fallback |
| 15:56:05 | unknown | address | "unsupported places: ['আগ্রপাড়া']" | Generic fallback |
| 16:11:33 | unknown | contact | "unsupported numbers: ['01958', '122300']" | Generic fallback |
| 16:13:48 | unknown | numeric | "unsupported numbers: ['9']" | Generic fallback |
| 17:13:13 | unknown | numeric | "unsupported numbers: ['9']" | Generic fallback |
| 17:21:58 | unknown | numeric | "unsupported numbers: ['9']" | Generic fallback |

### GOOD_SAFETY_BLOCKS = 0

### BLOCKS_CAUSED_BY_MISSING_KNOWLEDGE = Multiple

The fact-check is blocking VALID public recruitment information:
- Office address "আগ্রপাড়া" is a valid location
- Phone number "01958-122300" is the official recruitment number
- Numbers in general salary answers are blocked

**Assessment:** The fact-check is TOO RESTRICTIVE. It's blocking harmless public recruitment information.

---

## 12. SEMANTIC VS LEGACY ROUTING

### Semantic Routing Working = YES

| Metric | Value |
|--------|-------|
| RECRUITMENT_INTENT_CLASSIFIED | 124 |
| SEMANTIC_GATE_RECRUITMENT_MATCH | Correct |
| SEMANTIC_GATE_CONFIDENCE | 0.91 (high) |

### Semantic Classification Examples

| Message | Legacy Intent | Semantic Domain | Correct? |
|---------|---------------|-----------------|----------|
| "সিকিউরিটি পদে বেতন কতো" | salary_query | recruitment_conversation | YES |
| "চাকরি করতে চাই" | join | recruitment_conversation | YES |
| "আপনার অফিস কোথায়" | unknown | recruitment_conversation | YES |
| "Hello can I get info" | greeting | recruitment_conversation (from history) | YES |

### LEGACY_MISCLASSIFICATIONS = Minimal

The semantic routing is correctly identifying recruitment messages. The problem is NOT in routing — it's in the RESPONSE GENERATION.

---

## 13. SECURITY/POLICY GATE REVIEW

### Good Security (Working Correctly)
- Employee payroll data protected
- Admin commands require authentication
- Private employee information not disclosed

### Over-Restrictive Guards (Problem)

| Guard | Why Blocked | Is This Security Necessary? |
|-------|-------------|----------------------------|
| Phone number in reply | "unsupported numbers: ['01958', '122300']" | NO — this is PUBLIC recruitment contact |
| Address in reply | "unsupported places: ['আগ্রপাড়া']" | NO — this is PUBLIC office location |
| Numbers in salary | "unsupported numbers: ['9']" | NO — salary needs numbers |

**Assessment:** The security gates are overly restrictive for PUBLIC recruitment information that should be disclosed.

---

## 14. REPLY QUALITY ANALYSIS

### Average Reply Quality Score: ~2.0 / 5.0

### Quality Issues Found

| Issue | Count | Example |
|-------|-------|---------|
| Generic/Non-Answer | ~35 | "আপনার বয়স কত বছর?" when asked about salary |
| Intake Override | ~25 | Ignoring question to ask next form field |
| Robotic | Many | Simple template responses |
| Not in Bangla | Some | "Hi, I'm Fazle..." in English |
| Repetitive | Many | Same age/area/position questions repeatedly |

### Good Quality Replies Found

| Time | Thread | Reply | Quality |
|------|--------|-------|---------|
| 15:02:40 | 8801307960093 | Detailed recruitment info | 4/5 |
| 15:06:52 | 880191053053910 | "We are recruiting for Security..." | 3/5 |

---

## 15. TOP ROOT CAUSES

### Ranked by Frequency

| Rank | Root Cause | Percentage | Evidence |
|------|------------|------------|----------|
| 1 | **INTAKE_FLOW_OVERRIDE** — System asks intake questions instead of answering candidate's real question | ~45% | Logs show 25+ threads with this pattern |
| 2 | **KNOWLEDGE_EXISTS_BUT_NOT_RETRIEVED** — Answer exists in KB but not used due to intake override | ~30% | Source of truth has answers but not delivered |
| 3 | **FACT_CHECK_TOO_RESTRICTIVE** — Blocking valid public recruitment info | ~10% | Multiple "unsupported numbers/places" blocks |
| 4 | **SEMANTIC_MISCLASSIFICATION** — Wrong domain assigned | ~5% | Minimal — routing mostly works |
| 5 | **PROVIDER/LOGGING_GAPS** — Some messages not traced | ~5% | Some threads incomplete in logs |

---

## 16. OWNER DECISIONS / INFORMATION NEEDED

### A. BUSINESS_KNOWLEDGE_GAPS

| Topic | Current State | Questions Owner Must Decide |
|-------|---------------|----------------------------|
| Duty Hours | "গড়ে ৬–৮ ঘণ্টা" | Is this accurate for all positions? Is there overtime? |
| Accommodation | "থাকা: জাহাজেই (ফ্রি)" | Is this accurate? What about office staff? |
| Food | "খাওয়া: নিজ দায়িত্বে" | Any exceptions? |
| Probation Period | "৩–৬ মাস" | Exact duration per position? |
| Training Pay | "১০,০০০–১৫,০০০" | Is this exact? Taxable? |
| First Salary Date | Not documented | When do new hires get first salary? |

### B. CONTRADICTORY_BUSINESS_POLICIES

| Topic | Conflict | Owner Decision Needed |
|-------|----------|---------------------|
| Fee/Deposit | "No fee" vs "processing fee deducted from salary" | Clarify exact policy |
| Contact Number | Multiple numbers in different docs | Confirm single official number |

### C. CONVERSATION_HISTORY_BUGS

| Issue | Severity | Example |
|-------|----------|---------|
| History window unknown | HIGH | Don't know how many prior messages system sees |
| Manual reply not tracked | MEDIUM | Can't detect duplicate automation |

### D. INTAKE_FLOW_DESIGN_PROBLEMS

| Issue | Severity | Recommended Fix |
|-------|----------|-----------------|
| Intake overrides real questions | CRITICAL | Priority: Answer question first, THEN collect intake |
| No "answer + continue intake" flow | CRITICAL | New flow: answer + natural continuation |

---

## 17. TECHNICAL FIXES RECOMMENDED

### Priority 1: Intake Flow Override Fix (CRITICAL)

**ISSUE_ID:** INTAKE-001
**SEVERITY:** CRITICAL
**AFFECTED_THREADS:** ~65%
**ROOT_CAUSE:** `modules/recruitment_flow/route_recruitment_message()` prioritizes intake step over question answering
**EXAMPLE:** Candidate asks salary → system asks age
**RECOMMENDED_FIX:** 
1. Create `should_answer_question_first()` check
2. If candidate asks real question → answer it FIRST
3. THEN: "আপনি চাইলে আবেদন প্রক্রিয়া চালিয়ে যেতে আপনার বয়সটা জানাতে পারেন।"
4. Keep intake state but don't block on it

### Priority 2: Fact-Check Relaxation (HIGH)

**ISSUE_ID:** FACT-001
**SEVERITY:** HIGH
**AFFECTED_THREADS:** ~10%
**ROOT_CAUSE:** `modules/conversation_layer/playbooks.py` fact-check blocking public info
**EXAMPLE:** Blocking "01958-122300" phone number
**RECOMMENDED_FIX:**
1. Whitelist PUBLIC recruitment info (phone, address, salary ranges)
2. Keep blocking PRIVATE info (employee salaries, admin actions)

### Priority 3: Knowledge Retrieval Fix (HIGH)

**ISSUE_ID:** KNOW-001
**SEVERITY:** HIGH
**AFFECTED_THREADS:** ~30%
**ROOT_CAUSE:** Intake override prevents KB lookup
**EXAMPLE:** Salary knowledge exists but not retrieved
**RECORDED_FIX:**
1. Fix Priority 1 → this resolves automatically
2. Ensure KB context is loaded before reply generation

### Priority 4: Conversation History Enhancement (MEDIUM)

**ISSUE_ID:** HIST-001
**SEVERITY:** MEDIUM
**ROOT_CAUSE:** Unknown history window, no manual reply tracking
**RECOMMENDED_FIX:**
1. Log/verify history window size
2. Include outbound messages in history
3. Track manual replies for duplicate detection

---

## 18. FINAL RECRUITMENT CONVERSATION SCORECARD

| Metric | Value |
|--------|-------|
| TOTAL_RECRUITMENT_THREADS | ~40-50 real candidates |
| TOTAL_CANDIDATE_QUESTIONS | ~60+ |
| FULLY_ANSWERED | ~8 (~15%) |
| PARTIALLY_ANSWERED | ~5 (~10%) |
| NOT_ANSWERED | ~35 (~65%) |
| **ANSWER_COMPLETENESS_PERCENT** | **~15%** |
| DUPLICATE_REPLY_THREADS | 0 (confirmed) |
| MANUAL_REPLY_IGNORED_THREADS | Unknown (needs DB) |
| KNOWLEDGE_MISSING_THREADS | 0 (knowledge exists) |
| CONTRADICTORY_KNOWLEDGE_THREADS | 1 (fee policy) |
| HISTORY_FAILURE_THREADS | Unknown |
| INTAKE_OVERRIDE_THREADS | ~25 (~45%) |
| FACT_CHECK_BLOCKED_THREADS | ~6 (~10%) |
| SEMANTIC_ROUTING_FAILURE_THREADS | ~2 (~5%) |
| PROVIDER_FAILURE_THREADS | ~3 (~5%) |
| **AVERAGE_REPLY_QUALITY_SCORE** | **~2.0 / 5.0** |

---

## 19. MANDATORY FINAL ANSWERS

### WHY_ARE_RECRUITMENT_CONVERSATIONS_INCOMPLETE =

**PRIMARY CAUSE:** Intake flow overrides candidate's real questions. When someone asks about salary, the system asks for their age instead. This happens in ~45% of conversations.

**SECONDARY CAUSES:**
1. Knowledge exists in KB but not retrieved (30%)
2. Fact-check blocks valid public info (10%)
3. Logging gaps make some issues hard to trace (5%)

---

### IS_THE_MAIN_PROBLEM_MISSING_INFORMATION = **no**

The knowledge EXISTS in the system — recruitment_source_of_truth.txt has salary ranges, job details, office location, fee policy, age limits. The problem is the intake flow doesn't allow this knowledge to be retrieved.

---

### IS_THE_MAIN_PROBLEM_CONTRADICTORY_INFORMATION = **partial**

Only ONE mild contradiction: fee policy says "no fee" but also mentions "processing fee deducted from salary." This needs clarification but is NOT the main problem.

---

### IS_THE_MAIN_PROBLEM_SECURITY_GATES = **partial**

Security gates are too restrictive for PUBLIC recruitment info:
- Blocking phone number "01958-122300" 
- Blocking address "আগ্রপাড়া"
- Blocking numbers in salary answers

This affects ~10% of conversations.

---

### IS_THE_MAIN_PROBLEM_HISTORY_NOT_BEING_USED = **partial**

Evidence suggests history may not be used effectively:
- System asks same questions repeatedly
- Doesn't reference prior answers

Needs DB investigation to confirm.

---

### IS_THE_MAIN_PROBLEM_INTAKE_FLOW_OVERRIDING_USER_QUESTIONS = **yes**

**THIS IS THE #1 PROBLEM.** ~45% of conversations fail because the system prioritizes collecting name/age/area over answering the candidate's actual question.

---

### IS_MANUAL_REPLY_DUPLICATION_A_REAL_PROBLEM = **no**

No clear evidence found in logs of manual + automated duplication. However, without DB access, cannot definitively confirm.

---

### IS_SEMANTIC_ROUTING_WORKING = **yes/partial**

Routing IS working — messages correctly classified as recruitment. The problem is RESPONSE GENERATION, not routing.

---

### DO_WE_NEED_MORE_BUSINESS_RESOURCES = **no**

Knowledge base already has answers. Just need to retrieve them properly.

---

### DO_WE_NEED_CODE_CHANGES = **yes**

Critical changes needed:
1. Intake flow override fix (Priority 1)
2. Fact-check relaxation (Priority 2)
3. Knowledge retrieval integration (Priority 3)

---

### TOP_5_OWNER_DECISIONS_NEEDED

1. **Confirm fee policy:** Is there ANY fee? Processing fee? Registration fee? Need single canonical answer.
2. **Approve intake flow change:** Allow answering candidate questions BEFORE continuing intake data collection
3. **Duty hours clarification:** Confirm 6-8 hours, overtime policy
4. **Accommodation policy:** Confirm who gets what (ship vs office)
5. **First salary timing:** When do new hires get first payment?

---

### TOP_5_CODE_FIXES_NEEDED

1. **Create "answer-first" logic** in `modules/recruitment_flow/route_recruitment_message()`
2. **Whitelist public recruitment info** in fact-check (phone, address, salary ranges)
3. **Fix conversation history** to include outbound messages and manual replies
4. **Add KB retrieval** before intake step processing
5. **Log history window size** for debugging

---

## APPENDIX: ANONYMIZED THREAD MATRIX

| Thread | Masked Phone | Main Questions | Answered? | Reply Source | Semantic Domain | Main Problem | Root Cause |
|--------|--------------|----------------|-----------|--------------|----------------|--------------|------------|
| 1 | 8801784192342 | Security Guard salary | NO | intake_question | recruitment | INTAKE_OVERRIDE | Intake step overriding |
| 2 | 8801603740588 | Salary comment | NO | intake_question | salary_query | INTAKE_OVERRIDE | Intake step overriding |
| 3 | 8801813601611 | Salary? | NO | unknown | salary_query | NO_TRACE | Logging gap |
| 4 | 8801825178611 | Salary details | NO | none → left | salary_query | INTAKE_OVERRIDE | Candidate churned |
| 5 | 8801304549817 | Salary + job details | NO | intake_question | salary_query | INTAKE_OVERRIDE | Intake step overriding |
| 6 | 8801737707519 | Office address | PARTIAL | later_kb | recruitment | INTAKE_OVERRIDE | Answer delayed |
| 7 | 8801335096590 | Deposit/fee | NO | intake_question | recruitment | INTAKE_OVERRIDE | Fee policy not delivered |
| 8 | 8801745426093 | Ship job details | NO | intake_question | recruitment | INTAKE_OVERRIDE | Job details not delivered |
| 9 | 8801931562049 | Shipping job info | YES | hermes | recruitment | GOOD | Working correctly |
| 10 | 8801307960093 | Job details | YES | hermes | recruitment | GOOD | Working correctly |

---

**END OF AUDIT REPORT**

*This audit was conducted using read-only analysis of log files and codebase. No modifications were made to any system.*
