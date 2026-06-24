---
title: Workflow Engine — Message Router
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Workflow Engine — Message Router
**KB Article ID:** DEV-06-WORKFLOW
**Source:** `modules/message_router/__init__.py` (598 lines — read 2026-06-23)
**Visibility:** Developer / Admin only — do NOT set `safe_for_customer=True`
**Certified:** 2026-06-23 (Wave-3, W3-AUTH)

---

## Overview

`modules/message_router/__init__.py` is the single entry point for all inbound WhatsApp message routing. It is imported by both `app/main.py` (webhook path) and `modules/bridge_poller` (SQLite polling path).

**Return contract:** Every execution returns `(reply_text: str, admin_notification: dict | None)`. The router does NOT send anything — callers handle delivery.

**Admin notification shape:**
```python
{"admin_phone": str, "text": str, "bridge": str}
```

---

## Key Constants

### `_ESCORT_ROLES` frozenset (4 roles)

```python
_ESCORT_ROLES = frozenset({"escort_client", "client_escort_buyer", "vip_client", "repeat_client"})
```

Any sender whose resolved identity role is in this set triggers the escort client flow at Step 2, subject to content check.

---

### `_SILENT_SKIP_NAME_TOKENS` (8 tokens)

```python
_SILENT_SKIP_NAME_TOKENS: tuple[str, ...]  # 8 tokens — values developer-only
```

If a sender's `wbom_contacts.display_name` contains any of these 8 lowercase tokens, the sender receives **no reply, no draft, no queue entry** (silent drop). Values are intentionally omitted from this KB article — they are internal guard strings that must not be exposed. Read directly from `modules/message_router/__init__.py` line 66.

---

### `_SAFE_AUTOSEND_INTENTS` frozenset (9 intents)

```python
_SAFE_AUTOSEND_INTENTS = frozenset({
    "recruitment",     # job queries, vacancy, requirements, joining process
    "join",            # joining date, first-duty scheduling
    "greeting",        # menu / welcome / first contact
    "office_location", # office address — safe for ALL roles
    "salary_query",    # salary schedule, payroll cycle info (complaint guard still active)
    "payment_due",     # payment date queries (complaint guard still active)
    "attendance",      # attendance rules, absence policy
    "leave",           # leave policy, resignation rules
    "escort_duty",     # duty schedule, transport/food policy info
})
```

Used by `_is_safe_autosend_intent(intent, role)`. An intent in this set is cleared for auto-send without manual review, **unless** the sender's role is in `DRAFT_ALWAYS_ROLES` (see Step 5 note below) — with the sole exception of `office_location`, which is safe for all roles.

`advance_request` is intentionally excluded: only informational advance policy answers are safe; actual money requests must always go to draft.

---

### `DRAFT_ALWAYS_ROLES` (from code comment, line 71)

Roles: `accountant`, `client_escort_buyer`, `vip_client`, `repeat_client`

Replies to these roles are always drafted, regardless of intent — except `office_location` which bypasses the draft even for these roles (Step 12 fast path returns directly).

---

## Pre-Step: Silent Skip Gate

**Executes before any routing step.** Three conditions, any one triggers silent skip:

| Condition | Rule |
|---|---|
| Sender == `ACCOUNTANT_PHONE` setting | Skip — accountant messages handled by separate accountant flow when role is resolved |
| `wbom_contacts.display_name` contains any of the 8 name tokens | Skip — internal office contacts must not receive AI replies |
| `fazle_contact_roles.role == "blocked"` | Skip — admin-blocked numbers |

On silent skip: `log.info("[SILENT_SKIP] ...")` then `return "", None`. No draft created, no notification sent.

---

## 15-Step Routing Priority Table

Steps execute in this order. Once a step returns a value, routing stops.

| Step | Trigger Condition | Action | Returns Draft to Admin? |
|---|---|---|---|
| **1. FAMILY** | `role_str == "family"` | Personal safe reply (no business workflow); includes emoji (only place router uses emoji) | No |
| **2. ESCORT CLIENT ROLES** | `role_str in _ESCORT_ROLES` AND (intent in `{client_order, escort_duty}` OR `_looks_like_escort_order(text)` matches) | `handle_escort_client_message()` — extracts vessel data, creates draft, never replies direct to client | Yes |
| **3. ADMIN** | `role_str == "admin"` | Sub-flow: escort completion → structured commands → NL queries → keyword shortcuts → fallback help text | No (commands respond directly) |
| **4. ATTENDANCE** | `is_supervisor_attendance(text)` OR (`role_str != "admin"` AND `is_attendance_message(text)`) | `parse_attendance()` → `create_attendance_draft()` → notify admin | Yes |
| **5. INTENT CLASSIFY** | Always (non-branching step) | `ai.classify_intent_llm(text)` → if `"unknown"`: fall back to `classify(text)` (deterministic). Result stored in `intent` variable. | — |
| **5a. RECRUITMENT BLOCK** | `intent == "recruitment"` AND `role_str not in ("candidate", "new_lead", "unknown")` | Silent return `("", None)` — operational roles must never enter recruitment funnel | No |
| **6. ACCOUNTANT** | `role_str == "accountant"` | Sub-flow: accountant summary → advance record query → payment SMS ingest → admin cash shorthand → KB → AI (no recruitment, no draft) | No |
| **7/8. CANDIDATE RECRUITMENT** | `role_str in ("candidate", "new_lead", "unknown")` | `recruitment_eligibility()` → if eligible: `generate_recruitment_reply()` | No (auto-send) |
| **9. ESCORT ORDER (intent)** | `intent in ("client_order", "escort_duty")` | `handle_escort_client_message()` — for unknown/non-registered senders who send escort content | Yes |
| **10. EMPLOYEE** | `role_str == "employee"` | Sub-flow: identity mismatch → verification session → attendance → slip → release request → complaint → advance → salary | Depends on sub-path |
| **11. ADVANCE / PAYMENT** | `is_advance_request(text)` AND `role_str != "admin"` AND no existing session | `start_advance_verification()` | No (verification flow) |
| **12. OFFICE LOCATION** | `intent == "office_location"` | `kb_get_reply(text, intent)` only — no LLM, no reviewed_reply_memory. Hardcoded fallback if KB unavailable. | No |
| **13. KNOWLEDGE BASE** | All roles / all other intents | `kb_get_reply(text, intent)` — deterministic KB match | No (auto-send) |
| **14. REVIEWED REPLY MEMORY** | After KB miss | `reviewed_reply_memory.lookup_reviewed_reply(sender_phone, intent, role)` — admin-approved cached replies | No (auto-send) |
| **15. AI FALLBACK** | After all prior misses | RAG enrichment → `ai.generate_reply()` → `clean_general_reply()` | Per role/intent rules |

---

## Step-by-Step Detail

---

### Step 3 — Admin Flow

Executes when `role_str == "admin"`. Four sub-checks in order:

1. `is_completed_escort_draft(text)` → `handle_admin_escort_completion()` (admin sent `[RELEASE CONFIRMED]`)
2. `is_admin_command(text)` → `process_admin_command(text, sender)` (APPROVE / REJECT / PAID / ADVANCE / ESCORTCONFIRM)
3. `is_nl_admin_query(text)` → `process_nl_admin_query(text, sender)` — natural language queries ("show last 10 chats of 01..."), implemented without LLM
4. Keyword shortcuts: `"draft"/"পেন্ডিং"/"list"` → draft list; `"payment"/"পেমেন্ট"/"paid"` → payment drafts; `"attendance"/"হাজিরা"` → attendance summary
5. Fallback: inline help text with command syntax (does NOT fall through to LLM — prevents garbage apology drafts)

Bangla numeral commands work: `APPROVE ১৬৫` is accepted alongside `APPROVE 165`.

---

### Step 5a — Recruitment Block for Operational Roles

```python
if intent == "recruitment" and role_str not in ("candidate", "new_lead", "unknown"):
    log.info("[RECRUITMENT_BLOCKED_OPERATIONAL] ...")
    return "", None
```

Employees, supervisors, accountants, and known clients who send recruitment-sounding messages get a silent drop. This prevents the recruitment funnel from activating for operational identities.

---

### Step 10 — Employee Sub-Flow

Executes in this priority order:

1. No `employee_id` resolved → `check_identity_mismatch(sender)` — name-mismatch warning
2. `get_verification_session(sender)` → if active: `advance_verification()` (continue multi-step flow)
3. `intent == "attendance"` OR `is_attendance_message(text)` → `handle_attendance_message()`
4. `intent == "slip_submission"` → `start_slip_verification()`
5. Employee has `emp_id` AND `is_release_intent(text)` → return acknowledgement + admin notification with `[RELEASE CONFIRMED]` instructions
6. `intent in ("employee_salary_complaint", "legal_issue", "payment_issue")` → `create_draft_reply(draft_type="complaint")` + return acknowledgement string
7. `is_advance_request(text)` → `start_advance_verification()`
8. `intent in ("salary_query", "payment_due")` AND `emp_id` → `get_payroll_summary(emp_id)` + `ai.generate_reply()`

---

### Step 12 — Office Location Fast Path

```python
if intent == "office_location":
    office_reply = await kb_get_reply(text, intent)
    if office_reply:
        return office_reply, None
    # Hardcoded fallback:
    return (
        "আমাদের অফিস:\n"
        "আল-আকসা সিকিউরিটি অ্যান্ড লজিস্টিকস সার্ভিসেস লিমিটেড\n"
        "আগ্রপাড়া, ভিক্টোরিয়া গেইট নং ১, খোকনের বিল্ডিং (২য় তলা)\n"
        "পাহাড়তলী, চট্টগ্রাম সিটি কর্পোরেশন\n\n"
        "সকাল ৯টা – বিকাল ৫টা (শুক্রবার বন্ধ)\n"
        "WhatsApp: 01958 122322"
    ), None
```

This step **skips** reviewed_reply_memory lookup and AI entirely. It applies to ALL roles (office address is always safe to auto-send). Log marker: `[OFFICE_FAST]`.

---

### Step 14 — Reviewed Reply Memory

```python
_reviewed = await _rrm.lookup_reviewed_reply(
    sender_phone=sender,
    intent=intent,
    role=role_str,
)
```

Returns a dict: `{"reply_text": str, "match_scope": str, "id": int}` or `None`.

Position: After KB miss (step 13), before LLM (step 15). This is step 14 in the routing chain — admin-curated replies are preferred over LLM output.

**Fail-safe:** Wrapped in `try/except`. Any exception logs at DEBUG level and routing continues to step 15. The module is also kill-switch protected: if `REVIEWED_REPLY_MEMORY_ENABLED=false`, `lookup_reviewed_reply()` returns `None` immediately.

---

### Step 15 — AI Fallback with RAG Enrichment

Three sub-steps, each fail-safe:

**Sub-step 15-A — Build contact context:**
```python
db_ctx = await get_contact_context(sender)
```
Queries `wbom_contacts` and `wbom_employees` for sender name, relation, designation, salary, status. Returns empty string if no match.

**Sub-step 15-B — RAG enrichment:**
```python
_rag_hits = await _rag_search(text, k=3)
```
Top 3 RAG hits appended to `db_ctx` as:
```
KB Context:
- <title>: <text[:300]>
- <title>: <text[:300]>
...
```
Wrapped in try/except — exception skips enrichment without aborting routing.

**Sub-step 15-C — LLM + clean:**
```python
reply = await ai.generate_reply(text, intent, db_ctx, role=role_str, source=source)
return clean_general_reply(reply), None
```
`clean_general_reply()` strips artifact prefixes from structured prompt output (see `system_prompt.md`).

---

## Complaint-Phrase Guard

The complaint-phrase guard is enforced **upstream in `modules/bridge_poller`**, before messages reach `process_message()`. Financial complaints (specific Bengali phrases) trigger forced draft regardless of intent or role. See `bridge_poller.md` (Phase 4 article, TBD) for the guard implementation.

Within `message_router` itself, the complaint handling lives at Step 10 for employees: intents `employee_salary_complaint`, `legal_issue`, `payment_issue` force `create_draft_reply()` with `draft_type="complaint"`.

---

## `get_recent_history(phone, limit=5)`

Returns the last 5 inbound message bodies for a sender phone number, in chronological order.

- Table: `wbom_whatsapp_messages`
- Filter: `direction='inbound'`
- Order: `received_at DESC LIMIT 5`, then reversed before return (chronological)
- Used by step 7/8 to pass conversation history to `generate_recruitment_reply()`
- Returns empty list on any exception

---

## `get_contact_context(phone)`

Builds a 1–2 line string with contact info:
- Line 1 (if found): `"<relation_name>: <display_name> (<company_name>)"` from `wbom_contacts`
- Line 2 (if found): `"কর্মী: <name>, <designation>, বেতন: ৳<basic_salary>, স্ট্যাটাস: <status>"` from `wbom_employees`

Phone lookup uses all variants from `phone_normalizer`. Returns empty string on exception.

---

## `_looks_like_escort_order(text)` — Step 2 Content Check

Regex pattern (case-insensitive) matching vessel/escort keywords in message text:
```
\b(m\.?v\.?|mother\s*vessel|lighter|escort\s*lagbe|m\.?t\.|এমভি|destination|lighter\s*vessel|master\s*number)\b
```
Used at Step 2 to decide whether an escort-role sender's message should trigger the escort flow. Non-escort messages from escort roles fall through to normal routing.

---

## `DRAFT_ALWAYS_PHONES` (Security — Not in KB)

A phone number list that forces draft mode regardless of intent. This list must **not** appear in any KB article. Its existence and location are noted for developer awareness only. Read from production `.env` or settings — never from KB.

---

## Process Flow Summary

```
inbound message
    │
    ├── [pre] Silent skip gate (accountant phone / display_name tokens / blocked role)
    │
    ├── 1. role == family → personal reply
    ├── 2. role in _ESCORT_ROLES + content check → escort flow
    ├── 3. role == admin → command sub-flow
    ├── 4. attendance text detected → attendance draft
    ├── 5. intent classify (AI → deterministic fallback)
    │   └── 5a. recruitment block for operational roles → silent drop
    ├── 6. role == accountant → accountant finance sub-flow
    ├── 7/8. candidate/new_lead/unknown → recruitment eligibility → recruitment reply
    ├── 9. intent in client_order/escort_duty → escort flow
    ├── 10. role == employee → employee sub-flow
    ├── 11. advance request (non-admin, no session) → advance verification
    ├── 12. intent == office_location → KB direct return (no LLM)
    ├── 13. KB lookup → return if hit
    ├── 14. reviewed_reply_memory lookup → return if hit
    └── 15. AI fallback (contact ctx → RAG enrichment → LLM → clean_general_reply)
```

---

## Visibility

This article is **Developer/Admin only**. Do not set `safe_for_customer=True`.

Reason: Contains routing priority logic, silent skip triggers, complaint handling, and AI fallback chain — all internal system behavior that must not be disclosed to candidates or external contacts.
