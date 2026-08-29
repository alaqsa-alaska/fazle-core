---
title: Recruitment Flow System
owner: Fazle Core Admin
status: active
last_verified: 2026-08-12
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
| **Funnel** | `INTAKE_KEYWORDS` or `RECRUITMENT_TRIGGER_PHRASES` match in message | `recruitment_flow.intake_message()` | `True` — sent immediately |
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

### `INTAKE_KEYWORDS` (bare substring match, ~20 unique terms)

Explicit signals that a sender is seeking employment. Match triggers the funnel (path 1) regardless of session state.

```
"চাকরি", "vacancy", "apply", "hire", "recruit", "নিয়োগ", "কাজের", "পদ", "পারব", "নেবেন", "জয়েন", "cv", "joining", "office location", "office address", "contact number", "whatsapp number", "অফিস কোথায়", "অফিস কোথায়", "অফিসের ঠিকানা", "যোগাযোগ নম্বর"
```

**2026-08-06 fix:** `"job"`, `"interested"`, and their Bangla equivalent `"আগ্রহী"` were removed from this bare set — a bare substring match on any of the three fired on ordinary non-recruitment text. Confirmed live: an internal test message ("run job health_summary") and a generic business inquiry ("I am interested in learning more about your business") both silently opened a recruitment funnel session on a real WhatsApp number. All three now only count as a trigger inside a job-shaped phrase — see `RECRUITMENT_TRIGGER_PHRASES` below.

### `RECRUITMENT_TRIGGER_PHRASES` (contextual, 31 phrases)

`"job"`, `"interested"`, and `"আগ্রহী"` still trigger the funnel, but only inside one of these job-shaped phrases (requires an accompanying work/post/apply/join word, not standing alone):

```
"কাজ চাই", "কাজ করতে চাই", "কাজ আছে", "কাজ পাব", "কাজ করতে আগ্রহী", "looking for work", "need a job", "job available", "job interest", "a job", "any job", "job vacancy", "job apply", "apply for job", "job korte chai", "job er jonno", "job ache", "job lagbe", "job pete chai", "job dorkar", "job chai", "interested in this job", "interested in the job", "interested to apply", "interested in working", "interested in joining", "job e interested", "interested for the post", "interested in this post", "interested in recruitment", "interested in the vacancy"
```

Note: `INTAKE_KEYWORDS` is a Python `set` — duplicate entries are automatically deduplicated. `RECRUITMENT_TRIGGER_PHRASES` is a `tuple` (order-preserving, not deduplicated).

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

After the operational-role check, any of these 6 intents skips recruitment:

```python
OPERATIONAL_INTENTS = frozenset({
    "attendance", "leave", "payment_due", "advance_request",
    "client_order", "slip_submission",
})
```

`salary_query` and `escort_duty` are intentionally excluded. For
`unknown`/`candidate`/`new_lead` senders, informational questions for those
topics proceed to recruitment detection and the KB/RAG reply path. Operational
roles remain blocked by `OPERATIONAL_ROLES`, while order-shaped escort text
continues to the escort-order route when recruitment detection does not match.

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
**Relationship to `fazle_recruitment_sessions`:** `fazle_recruitment_sessions` is the **temporary** intake session (expires after SESSION_TTL=24h). `wbom_candidates` is the **permanent** candidate record created (or recovered by unique phone) when intake starts, then linked through `fazle_recruitment_sessions.candidate_id`. Collected fields and the final score are copied into the permanent record when scoring completes. FK: `fazle_recruitment_sessions.candidate_id → wbom_candidates.candidate_id ON DELETE SET NULL`.

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
| `notes` | TEXT | — | NULL | Admin notes; also holds the marker-delimited `[EXPERIENCE_CLAIM]` block for responsible roles |
| `post_intake_status` | TEXT | — | NULL | Migration 072, Owner-locked set: `awaiting_agreement / agreed / joining_ready / salary_objection / not_ready_now / declined / future_contact`. NULL = not yet reached. **Frozen vocabulary** — never extended. |
| `linked_employee_id` | BIGINT | — | NULL | Migration 077 (2026-08-29). FK → `wbom_employees(employee_id)` ON DELETE SET NULL. Set exactly once by `link_candidate_to_employee()` when this person becomes an employee. Durable candidate→employee link that survives a later phone change. Partial UNIQUE index enforces 1:1. |
| `created_at` | TIMESTAMPTZ | NOT NULL | NOW() | Immutable |
| `updated_at` | TIMESTAMPTZ | NOT NULL | NOW() | Updated on each stage change |

**Indexes:** UNIQUE on `phone`; btree on `score_bucket`, `next_follow_up_at` (partial WHERE NOT NULL), `funnel_stage`, `assigned_recruiter`, `post_intake_status` (partial WHERE NOT NULL); partial UNIQUE on `linked_employee_id` (WHERE NOT NULL).

**Referenced by:** `fazle_recruitment_sessions` (FK, SET NULL on delete), `wbom_candidate_conversations` (FK, CASCADE), `wbom_recruitment_reminders` (FK, CASCADE).

---

## `wbom_candidate_conversations` — Candidate Intake Audit (S5)

Every inbound recruitment trigger or step answer is appended before the session advances. The row stores the canonical `candidate_id`, the pre-transition `step`, direction `inbound`, exact message text, and collection timestamp. Migration 062 adds nullable `source_bridge` and `message_id` columns plus a partial unique index on `(source_bridge, message_id)` where `message_id IS NOT NULL`.

When a bridge message ID is present, a conflict proves the message is a retry: the audit insert is suppressed and the intake state does not advance. Messages without an ID remain append-only and cannot be safely deduplicated. Audit persistence is fail-open, but session and permanent-candidate mutations share a transaction in both audited and audit-outage paths. A mutation failure rolls back the transition, retains the current-step reply, and leaves the message retryable.

### Lifecycle events on this table (2026-08-29 closure)

The same append-only table also carries **post-intake lifecycle events** — `_audit_candidate_conversation()` gained a `direction` parameter (`'inbound'` default, `'outbound'` for a message we sent, `'event'` for a fact with no WhatsApp message). `record_candidate_lifecycle_event(candidate_id, event, evidence, …)` writes them; `event` must be in `LIFECYCLE_EVENT_STEPS`:

`invited · invitation_acknowledged · phone_confirmed · travelling · arrived_office · joined · deferred_after_invitation · did_not_arrive · phone_call_note · readiness_confirmed`

These are **events, not states** — the frozen `post_intake_status` vocabulary is unchanged. Events are evidence-backed only (candidate message, authenticated Admin note, or authoritative employee creation); never inferred from silence. `did_not_arrive` is only written from an explicit record, never from absence of a reply.

---

## Recruitment lifecycle closure functions (2026-08-29, Owner directive)

| Function | Purpose |
|---|---|
| `link_candidate_to_employee(employee_mobile, employee_id, …)` | GAP 1. Last-9-digit phone match (candidate `phone` and employee `employee_mobile` use inconsistent 01…/8801… forms). Links `WHERE linked_employee_id IS NULL` only; never repoints an already-linked candidate (→ `needs_admin_review`); ambiguous multi-match fails closed. Records a `joined` event. Called best-effort (non-fatal) from `admin_employees.create_employee()` and `create_and_link_wbom()`. |
| `record_candidate_lifecycle_event(...)` | GAP 2. Append one `LIFECYCLE_EVENT_STEPS` row. |
| `get_willing_candidates(include_interested=False, limit=None)` | Shortlist. Default: `post_intake_status IN ('joining_ready','agreed')` and not already an employee — never merely `scored`. `include_interested=True` adds the softer `_INTERESTED_STATUSES` with honest labels. Deterministic order: `joining_ready` before `agreed`, then earliest `readiness_confirmed` (else `updated_at`), then `candidate_id`. Score never used for ordering. |
| `resolve_invite_batch(candidate_ids, limit=None)` | GAP 3. Deterministically orders + **freezes** a recipient set (same ordering as above). Drops candidates already linked to an employee / `declined` / no resolvable phone — never silently substitutes. `limit` = the N in "প্রথম ৫ জন". |
| `send_invite_batch(recipients, office_date, message_body, authorized_by, …)` | Sends each recipient individually via `outbound.enqueue` with `idempotency_key=f"recruit_invite:{cid}:{office_date}"` (exact-once; replay never re-sends a success; a partial failure isolates). `office_date` must already be an absolute date. One trusted-Admin authorization covers the whole bounded batch — no per-message re-approval. Records an `invited` event per successful send. |
| `record_admin_phone_call_note(candidate_ref, note, admin_phone, readiness=None)` | GAP 4. Records an authenticated Admin's offline/phone-call fact as a `phone_call_note` event, `source='admin:<phone>'` (never "Hermes heard the call"). Only updates `post_intake_status` if the Admin explicitly supplied `readiness` (must be in the frozen set). |
| `classify_post_invitation_signal(text) → step\|None` | G. Returns `travelling` / `arrived_office` / `invitation_acknowledged` on explicit markers (e.g. "রওনা দিয়েছি", "অফিসের পথে", "আচ্ছা আসব"), else None. Used by `_route_post_intake_message` so an invited candidate's "I'm on the way" continues from invitation context and never restarts intake. Fail-closed on ambiguity. |

**Personality layer** (`shared/reply_policy.py`, `POLICY_VERSION = "structured_v3"`): the recruitment reply prompt (`build_whatsapp_recruitment_policy`) now frames Fazle as a warm, competent human recruitment officer — natural conversational Bangla, light regional flavor, mild playfulness, humor within a hard safety boundary (`_HUMOR_SAFETY_NOTE` — never about hardship/poverty/illness/disability/age/religion/ethnicity/appearance/rejection). Anti-repetition rules: no per-turn greeting, no restating the question, no "আর কিছু জানতে চান?" loop, history-aware (don't re-ask known fields, don't re-send office contact unless needed), and a low-pressure acknowledgement for "চিন্তা করে দেখি". **Tone only** — business facts, salary figures, readiness classification, authority, privacy, and all DB/action decisions are unchanged and still governed by the Business Rules section + the code around the prompt.

---

## Key Functions

### `intake_message(phone, text, source, role, message_id) → dict`

Main handler for the intake funnel. Returns `{reply, action, session_id}`.

**Flow:**
1. Load active session via `get_active_session(phone)`
2. If no session: check intake keywords → create session and permanent candidate, link them, audit the trigger as step `intake`, then return the `name` question
3. If session exists: audit the inbound message against the current pre-transition `collection_step`
4. If an ID-bearing audit conflict proves a retry, return the current question without advancing state
5. Validate input for each step (`_parse_age()` for age, `VALID_POSITIONS` lookup for job_preference)
6. Advance the session; after `phone_confirm`, compute score and finalize the linked permanent candidate
7. Return `{reply: <step_question>, action: created|collecting|scored, session_id: ...}`

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
| No unsolicited fee mention | `modules/hermes_dispatch.py`'s `build_recruitment_context()` (Hermes path, see below) — **correction, 2026-08-12**: this row previously credited `recruitment_ai`'s `_FEE_PHRASES` guard, but `recruitment_ai` has zero live callers (see `recruitment_ai_detail.md`, now marked superseded) — that guard was never actually wired into any production path until the 2026-08-12 fix below. The pre-existing `conversation_layer/recruitment.py` path (live when `hermes_customer_recruitment` is off) still has **no** equivalent guard today. |

---

## Hermes customer-dispatch guardrails (2026-08-12, Owner-directed "Finding E")

When `hermes_customer_recruitment` is active (see `runtime_gateway_flags.md`), both call sites above (`route_recruitment_message()`'s session-continuation path and its main informational-reply path) route through `modules/hermes_dispatch.generate_recruitment_reply()` instead of `conversation_layer.generate_recruitment_reply()`. Two guardrails were ported into `modules/hermes_dispatch.py` from the dead `modules/recruitment_ai/__init__.py` (which had them but was never live) — neither guardrail exists in the `conversation_layer/recruitment.py` path today:

1. **Conditional fee-section inclusion** (`build_recruitment_context(message)`): `resources/ops/recruitment_source_of_truth.txt` is read whole and cached, but its "ভর্তি ফি / ডিপোজিট" section is stripped from the context unless the inbound message itself looks like a fee/deposit question (`_looks_like_fee_question()`) — matching the source file's own instruction not to volunteer fee figures unsolicited. Ported from `recruitment_ai._looks_like_fee_question()` / `build_recruitment_source_context()`'s fee-stripping regex.
2. **Post-generation fact-check** (`_recruitment_reply_matches_source()`): before `generate_recruitment_reply()` returns a Hermes reply, every numeric token and Bangla place-name-shaped token in the reply must appear in the `kb_context` that was given to Hermes — any reply introducing an unsupported number (invented salary/fee/age figure) or unsupported place name (invented vacancy location) is rejected (returns `None`, which callers already fall through to the safe `conversation_layer` path for, per this module's existing fail-closed contract). Ported from `recruitment_ai.enforce_recruitment_reply_policy()`. This is a conservative check (catches fabricated numbers/places specifically), not a full faithfulness validator.

**Known limitation carried over from the original**: the fact-check compares reply-numbers against `kb_context` only, not against the candidate's own message — a reply that legitimately echoes back a number the candidate stated (e.g. their own age) can be blocked as "unsupported" if that number doesn't independently appear in the source file. Accepted as low-risk (the funnel mostly asks forward questions rather than restating candidate-supplied figures), matching the original module's own design.

---

## Dead-module audit (2026-08-12, Owner-directed)

Two modules once considered candidates for feature loss on deletion were checked feature-by-feature against the live app; **neither has any missing coverage** — safe to delete when convenient:

- **`modules/user_role/__init__.py`** (`detect_role()`): zero live callers. Every capability it had (admin/accountant/employee/contact role resolution) is covered, and in most cases more comprehensively, by `identity_brain.detect_identity()` (see `identity_brain.md`) plus `message_router`'s own direct `settings.accountant_phone` check (Tier 6). Its `relation_name == "partner"` mapping has no live equivalent in `identity_brain`, but a live-DB check (2026-08-12) confirmed `wbom_contacts.relation` has never contained `"partner"` in production — purely theoretical, not a real gap.
- **`modules/reply_templates/__init__.py`**: only reachable via `conversation_layer/recruitment.py`'s `simulate_current_core_reply()`, itself a shadow/simulation function, not the live `generate_recruitment_reply()`. Its topics (recruitment/greeting/salary_query/complaint/leave/join) are all covered live elsewhere with more detail: recruitment → `recruitment_source_of_truth.txt`; salary_query → `salary_policy.md` + Tier 10 RAG enrichment (2026-08-12 fix, see `hybrid_search.md`); leave → `knowledge_base/01_employee_knowledge/leave_policy.md`, RAG-reachable via Tier 15's existing generic enrichment; join → `recruitment_source_of_truth.txt`'s "আবেদন ও যোগদান" section; complaint always routes to human/admin review (never auto-sent) so no auto-reply content is needed.

---

## Cross-References

- `identity_brain.md` — candidate/new_lead detection upstream of recruitment routing
- `identity_integration.md` — dimension 3 (workflow availability per sender type)
- `recruitment_ai_detail.md` — dead/superseded module; see its own status note
- `visibility_rules.md` — KB categories accessible to candidate role
- `automation_pipeline.md` — session expiry scheduler job
- `runtime_gateway_flags.md` — `hermes_customer_recruitment` and sibling feature flags
