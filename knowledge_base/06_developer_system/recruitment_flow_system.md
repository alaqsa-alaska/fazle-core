---
title: Recruitment Flow System
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Recruitment Flow System
**KB Article ID:** DEV-06-RECRUITMENT-FLOW-SYSTEM
**Source:** `modules/recruitment_flow/__init__.py` (365 lines — read 2026-06-23)
**Visibility:** Developer / Admin
**Certified:** 2026-06-23 (Wave-4, W4-AUTH)

---

## Purpose

Manages the 6-step WhatsApp intake funnel for job candidates. Triggers only for non-operational senders (unknown / candidate / new_lead roles) who send recruitment-trigger messages. Operational employees and clients are excluded — they have their own workflows.

**Scope boundary:** This module handles only the structured intake funnel (question-by-question form) and the routing eligibility decision. The LLM-based free-text recruitment chat (`recruitment_ai`) is a separate module.

---

## Two-Path Recruitment Architecture

When `recruitment_eligibility()` returns `eligible=True`, the router chooses one of two paths:

| Path | Trigger | Handler | `autosend` |
|---|---|---|---|
| **Funnel** | `INTAKE_KEYWORDS` match in message | `recruitment_flow.intake_message()` | `True` — sent immediately |
| **LLM Chat** | Active session + `_QUESTION_HINTS` match | `recruitment_ai.generate_recruitment_reply()` | `False` — goes to admin draft |
| **Not eligible** | `OPERATIONAL_ROLES` or `OPERATIONAL_INTENTS` detected | Route normally (not recruitment) | — |

**Neither path is triggered if:** sender is in `OPERATIONAL_ROLES`, OR detected intent is in `OPERATIONAL_INTENTS`.

---

## Constants

### `SESSION_TTL`

```python
SESSION_TTL = timedelta(hours=24)
```

An open intake session expires after 24 hours of inactivity. Expired sessions are detected in `get_active_session()` and marked stale (not deleted immediately).

---

### `INTAKE_KEYWORDS` (~23 unique terms)

Explicit signals that a sender is seeking employment. Match triggers the funnel (path 1) regardless of session state.

```
"job", "চাকরি", "vacancy", "apply", "hire", "recruit",
"নিয়োগ", "কাজের", "interested", "আগ্রহী", "পদ", "পারব",
"নেবেন", "জয়েন", "cv", "joining", "office location",
"office address", "contact number", "whatsapp number",
"অফিস কোথায়", "অফিসের ঠিকানা", "যোগাযোগ নম্বর"
```

Note: `INTAKE_KEYWORDS` is a Python `set` — duplicate entries are automatically deduplicated.

---

### `OPERATIONAL_ROLES` — Excluded from Recruitment Funnel

These 11 roles are operational — senders with these roles are never routed to recruitment:

```python
OPERATIONAL_ROLES = frozenset({
    "admin", "accountant", "employee", "supervisor", "family",
    "escort_client", "client_escort_buyer", "vip_client",
    "repeat_client", "vendor", "known_contact",
})
```

---

### `OPERATIONAL_INTENTS` — Block Recruitment on These Intents

If the router resolves any of these 8 intents for a sender, recruitment is skipped:

```python
OPERATIONAL_INTENTS = frozenset({
    "attendance", "leave", "salary_query", "payment_due",
    "advance_request", "escort_duty", "client_order", "slip_submission",
})
```

---

### `VALID_POSITIONS` — 9 Job Positions

Accepted values for the `job_preference` intake step:

| Canonical | Common Aliases |
|---|---|
| Escort | guard escort, মেয়ে এসকর্ট, গার্ড |
| Survey Scout | survey, সার্ভে |
| Security Guard | security, নিরাপত্তা, গার্ড, security guard |
| Security Supervisor | supervisor, সুপারভাইজার |
| Assistant Supervisor | assistant, asst supervisor |
| Operation Officer | operation, officer, অপারেশন |
| Security In-Charge | in charge, incharge, দায়িত্বে |
| Marketing Officer | marketing, মার্কেটিং |
| Ghat Supervisor | ghat, ঘাট |

---

## 6-Step Intake Funnel

### `COLLECTION_STEPS`

```python
COLLECTION_STEPS = ["name", "age", "area", "job_preference", "experience", "phone_confirm"]
```

### `STEP_QUESTIONS` — Bengali Questions per Step

| Step | Prompt (Bengali) |
|---|---|
| `name` | Welcome message + "আপনার নাম কি?" |
| `age` | "আপনার বয়স কত?" |
| `area` | "আপনি কোন এলাকায় বাস করেন?" |
| `job_preference` | "কোন পদে আগ্রহী?" |
| `experience` | "পূর্বে নিরাপত্তা/এসকর্ট কাজের অভিজ্ঞতা আছে কি?" |
| `phone_confirm` | "আপনার সাথে যোগাযোগের জন্য ফোন নম্বর নিশ্চিত করুন।" |

### `INTAKE_COMPLETE_MSG`

Sent after `phone_confirm` completes:
> Bengali completion message acknowledging the application and stating that an officer will contact the applicant.

---

## Scoring — `_compute_score()`

After all steps collected, a score (0–100) is computed and a bucket is assigned.

| Component | Points |
|---|---|
| Experience: "yes" / confirmed | +60 |
| Experience: "some" / partial | +40 |
| Experience: "no" / none | +20 |
| Preferred position is in `VALID_POSITIONS` | +20 |
| All 6 steps completed (completeness bonus) | +20 |
| Maximum | 100 |

**Score buckets:**

| Bucket | Score |
|---|---|
| `hot` | ≥ 70 |
| `warm` | ≥ 40 |
| `cold` | < 40 |

Stored in `fazle_recruitment_sessions.score_bucket`.

---

## `fazle_recruitment_sessions` — Full Schema (Wave-4)

**Source:** `db/migrations/003b_recruitment_sessions_fix.sql` (supersedes 003)
**UNIQUE index:** `(phone) WHERE funnel_stage IN ('collecting', 'new')` — one active session per phone

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `id` | BIGSERIAL | NOT NULL | auto | PK |
| `phone` | TEXT | NOT NULL | — | Sender phone (canonical 8801XXXXXXXXX) |
| `source_bridge` | TEXT | NOT NULL | `'bridge1'` | Bridge where session was initiated |
| `source_message` | TEXT | — | NULL | The text message that triggered the session |
| `collection_step` | TEXT | NOT NULL | `'name'` | Current step: `name\|age\|area\|job_preference\|experience\|phone_confirm` |
| `funnel_stage` | TEXT | NOT NULL | `'collecting'` | Session lifecycle: `collecting\|new\|scored\|abandoned` |
| `full_name` | TEXT | — | NULL | Collected at `name` step |
| `age` | INTEGER | — | NULL | Collected at `age` step; validated 18–55 (BR-25) |
| `area` | TEXT | — | NULL | Collected at `area` step |
| `job_preference` | TEXT | — | NULL | Collected at `job_preference` step; normalized to `VALID_POSITIONS` |
| `experience_years` | INTEGER | — | 0 | Collected at `experience` step |
| `confirmed_phone` | TEXT | — | NULL | Collected at `phone_confirm` step |
| `score` | INTEGER | — | NULL | 0–100; computed after `phone_confirm` completes |
| `score_bucket` | TEXT | — | NULL | `hot\|warm\|cold` (set with `score`) |
| `candidate_id` | INTEGER | — | NULL | FK → `wbom_candidates(candidate_id)` ON DELETE SET NULL |
| `created_at` | TIMESTAMPTZ | NOT NULL | NOW() | Session creation time |
| `updated_at` | TIMESTAMPTZ | NOT NULL | NOW() | Last step advance time |

**Note:** Migration 003 created this table with incorrect column names (it had `step` instead of `collection_step`, `name` instead of `full_name`, etc.). Migration 003b dropped and recreated it with the correct schema. The current authoritative schema is from 003b.

**Session TTL:** `SESSION_TTL = timedelta(hours=24)` — sessions in `collecting` or `new` stage older than 24 hours are treated as expired by `get_active_session()`.

**Indexes:** phone (btree), funnel_stage (btree), unique partial on phone where active.

---

## `wbom_candidates` — Permanent Candidate Record (U-01, verified 2026-06-23)

**Source:** Verified via `\d wbom_candidates` on production DB.
**Relationship to `fazle_recruitment_sessions`:** `fazle_recruitment_sessions` is the **temporary** intake session (expires after SESSION_TTL=24h). `wbom_candidates` is the **permanent** candidate record created when a session completes scoring. FK: `fazle_recruitment_sessions.candidate_id → wbom_candidates.candidate_id ON DELETE SET NULL`.

| Column | Type | Nullable | Default | Notes |
|---|---|---|---|---|
| `candidate_id` | BIGSERIAL | NOT NULL | auto | PK |
| `phone` | VARCHAR(20) | NOT NULL | — | UNIQUE — one permanent record per phone |
| `full_name` | VARCHAR(100) | — | NULL | From intake `name` step |
| `age` | INTEGER | — | NULL | Validated 18–55 (BR-25) |
| `area` | VARCHAR(100) | — | NULL | From intake `area` step |
| `job_preference` | VARCHAR(50) | — | NULL | Normalised to `VALID_POSITIONS` |
| `experience_years` | INTEGER | — | NULL | From intake `experience` step |
| `available_join_date` | DATE | — | NULL | Set downstream (not in intake funnel) |
| `funnel_stage` | VARCHAR(30) | NOT NULL | `'new'` | CHECK: new / collecting / scored / assigned / contacted / interviewed / hired / rejected / dropped |
| `collection_step` | VARCHAR(30) | — | `'name'` | Current or last completed step |
| `score` | INTEGER | NOT NULL | 0 | 0–100 CHECK (from `_compute_score()`) |
| `score_bucket` | VARCHAR(10) | NOT NULL | `'cold'` | CHECK: hot / warm / cold |
| `assigned_recruiter` | VARCHAR(80) | — | NULL | Admin who owns follow-up |
| `assigned_at` | TIMESTAMPTZ | — | NULL | When recruiter assigned |
| `last_contact_at` | TIMESTAMPTZ | — | NULL | Updated on each contact |
| `next_follow_up_at` | TIMESTAMPTZ | — | NULL | Indexed for scheduler follow-up sweep |
| `source` | VARCHAR(30) | — | `'whatsapp'` | Channel where candidate first appeared |
| `source_message` | TEXT | — | NULL | Original trigger message |
| `notes` | TEXT | — | NULL | Admin notes |
| `created_at` | TIMESTAMPTZ | NOT NULL | NOW() | Immutable |
| `updated_at` | TIMESTAMPTZ | NOT NULL | NOW() | Updated on each stage change |

**Indexes:** UNIQUE on `phone`; btree on `score_bucket`, `next_follow_up_at` (partial WHERE NOT NULL), `funnel_stage`, `assigned_recruiter`.

**Referenced by:** `fazle_recruitment_sessions` (FK, SET NULL on delete), `wbom_candidate_conversations` (FK, CASCADE), `wbom_recruitment_reminders` (FK, CASCADE).

---

## Key Functions

### `intake_message(phone, text, source) → dict`

Main handler for the intake funnel. Returns `{reply, action, session_id}`.

**Flow:**
1. Load active session via `get_active_session(phone)`
2. If no session: check `INTAKE_KEYWORDS` → if match, create session → return `name` question
3. If session exists: advance to next step based on current `collection_step`
4. Validate input for each step (`_parse_age()` for age, `VALID_POSITIONS` lookup for job_preference)
5. After `phone_confirm`: compute score → return `INTAKE_COMPLETE_MSG`
6. Return `{reply: <step_question>, action: "intake", session_id: ...}`

### `get_active_session(phone) → Optional[dict]`

Checks `fazle_recruitment_sessions` for an open session. Returns `None` if:
- No session found
- Session older than `SESSION_TTL` (24 hours)

Stale sessions are marked expired but not immediately deleted (for audit purposes).

### `recruitment_eligibility(phone, text, intent) → dict`

Routing decision function called by `message_router`. Returns:

```python
# Path 1 — Explicit keyword match → funnel (autosend)
{"eligible": True, "autosend": True, "reason": "explicit_recruitment", "active_session": False}

# Path 2 — Active session + session followup → LLM draft
{"eligible": True, "autosend": False, "reason": "session_followup_draft", "active_session": True}

# Not eligible
{"eligible": False, "autosend": False, "reason": "...", "active_session": False}
```

**Not eligible reasons:**
- `"operational_role"` — sender in `OPERATIONAL_ROLES`
- `"operational_intent"` — detected intent in `OPERATIONAL_INTENTS`
- `"no_trigger"` — no keyword match and no active session

### `_parse_age(text) → Optional[int]`

Validates age input at the `age` step. Returns `None` (step rejected) if:
- Age < 18 or > 55 (enforces **BR-25**: 18–55 active duty range)
- Non-numeric input

### `looks_like_recruitment_followup(text) → bool`

Used in path 2 routing. Checks for `_QUESTION_HINTS`:
```
"who are you", "who r u", "আপনি কে", "তুমি কে", "কেন", "why",
"am i asked for job", "asked for job", "lok lagbe", "লোক লাগবে",
"কাজ আছে", "job ache"
```

---

## Business Rule Enforcement

| Rule | Where Enforced |
|---|---|
| **BR-25** (age 18–55) | `_parse_age()` — rejects ages outside 18–55 |
| Session TTL 24h | `get_active_session()` — marks sessions older than SESSION_TTL as expired |
| Operational exclusion | `recruitment_eligibility()` — OPERATIONAL_ROLES + OPERATIONAL_INTENTS block |
| No fee collection | `recruitment_ai` module (sister module) enforces via `_FEE_PHRASES` guard |

---

## Cross-References

- `identity_brain.md` — candidate/new_lead detection upstream of recruitment routing
- `identity_integration.md` — dimension 3 (workflow availability per sender type)
- `recruitment_ai_detail.md` — LLM-based path 2 handler
- `visibility_rules.md` — KB categories accessible to candidate role
- `automation_pipeline.md` — session expiry scheduler job
