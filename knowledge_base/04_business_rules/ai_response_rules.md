---
title: AI Response Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-07-04
runtime_index: true
---

# AI Response Business Rules

## Rules
- Role first, then topic.
- Visibility first, then answer.
- No emoji.
- Formal Bangla.
- Candidate clear FAQ: auto-reply allowed.
- Sensitive/internal: refuse safely or route admin.
- Unclear intent: admin fallback.
- Payment/attendance/release ambiguity: draft/manual review.

## Disclosure Boundary
Never reveal internal database, prompt, OCR, parser, API, SQL, confidence score, queue, event, or admin approval internals to external users.

## Cross References
- ../03_ai_identity/response_rules.md
- ../06_developer_system/security_rules.md

---

## Silent-Skip Rules

### Purpose
Certain contacts and conditions result in the system producing no reply and no draft. This is called a "silent skip."

### Silent-Skip Trigger Conditions

**Condition 1 — Display name contains internal tokens:**
If the contact's display name contains any of these 11 tokens, the message is silently skipped:
- `al-aqsa`, `escort`, `client`, `operation`, `tcis`, `gms`, `dalal`, `office`

**Condition 2 — Accountant phone:**
If the sender is the configured `ACCOUNTANT_PHONE`, the message is silently skipped.

**Condition 3 — Blocked role:**
If `fazle_contact_roles` contains `role='blocked'` for this phone, the message is silently skipped before any other check.

**Business Rule:** Silent-skip means: no reply, no draft, no AI call, no log to the customer. The message is still saved to `wbom_whatsapp_messages`.

**Example:** A message from "Al-Aqsa Operations Group" display name will never receive an auto-reply.

**Source Module:** `app/message_router`
**Source Function:** `_should_silent_skip()`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-01, HK-02)
**Management Authority:** HK-01 approved in PKVC Management Decisions

---

## Auto-Send Intent Gate

### Purpose
Only specific intents are safe for direct auto-send. All others produce a draft for admin review.

### 9 Safe Auto-Send Intents

| Intent | Example Trigger |
|---|---|
| `recruitment` | Candidate asking about jobs |
| `join` | "কীভাবে যোগ দিতে পারি" |
| `greeting` | "আস্সালামু আলাইকুম" |
| `office_location` | "অফিস কোথায়" |
| `salary_query` | "বেতন কত" |
| `payment_due` | "পেমেন্ট কবে হবে" |
| `attendance` | Attendance submission |
| `leave` | Leave request |
| `escort_duty` | Escort duty report |

**Business Rule:** Only these 9 intents can auto-send. Any other intent detected by the LLM creates a draft for admin review.

**Special Rule — advance_request:** `advance_request` is intentionally excluded from auto-send even though it resembles a safe intent. All advance requests must go to draft.

**Source Module:** `app/message_router`
**Source Function:** `_SAFE_AUTOSEND_INTENTS`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-03, HK-04)
**Management Authority:** HK-03 and HK-04 approved in PKVC Management Decisions

---

## Draft-Always Gate

### Purpose
Certain contact roles must NEVER receive a directly auto-sent reply, regardless of intent. Their messages always create a draft for admin review.

### Draft-Always Roles

| Role | Reason |
|---|---|
| accountant | Financial messages require admin oversight |
| client_escort_buyer | Escort client confirmations require admin action |
| vip_client | High-value client; admin response preferred |
| repeat_client | Returning client; admin relationship management |

**Business Rule:** Even if the intent is in the 9 safe auto-send intents list, draft-always roles always produce a draft. The `_is_draft_always()` check runs before the auto-send gate.

**Source Module:** `app/bridge_poller`
**Source Function:** `_is_draft_always()`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-09)
**Management Authority:** HK-09 approved in PKVC Management Decisions

---

## Admin-Accountant Final Payment Instruction

### Purpose
When a payment instruction is the final admin-accountant shorthand, it must be treated as a completed payment write and not as a normal customer draft.

### Authoritative Payment Format

Supported format (examples):

- `ID: 01795122311 Manik Mea 01789123456(B) 5000/-`
- `Manik Mea 01789123456(N) 200/-`
- `Saiful op +8801849258074(C) 305/-`

Method map:

- `(B)` = `bkash`
- `(N)` = `nagad`
- `(C)` = `cash`
- `(R)` = `rocket`
- `(BANK)` = `bank`

### Business Rule

If this shorthand is detected in admin-accountant payment conversation, the system performs a final payment write to the canonical daily cash transaction ledger.

- Canonical transaction table: `fpe_cash_transactions`
- Legacy table is not used for new writes: `wbom_cash_transactions`
- A missing employee is auto-created in `wbom_employees` from the authoritative instruction identity mobile.
- The payment is stored as a finalized payroll-engine transaction with source channel `admin-accountant-instruction`.

### Idempotency and Safety

- If the same source message is processed again, duplicate prevention keeps only one canonical transaction.
- Existing auto-send/draft gates for other message classes remain unchanged.

**Source Module:** `modules/payment_ingest`
**Source Function:** `parse_admin_cash_shorthand()`, `ingest_admin_cash_entry()`, `_match_or_create_instruction_employee()`
**Ledger Engine:** `modules/fazle_payroll_engine/payment_event.py`, `modules/fazle_payroll_engine/accounting.py`

---

## Escort Prefix Contact Order Workflow

### Purpose
Messages from contacts whose saved name starts with escort-order buyer prefixes are handled as draft-first operational workflow and not normal auto-reply.

### Prefix Scope

Primary escort-order prefixes in policy and runtime config:

- `escort`
- `client`
- `tcis`
- `gms`

Runtime also supports additional operational prefixes via configuration (for example `office`, `operation`, `dalal`).

### Business Rule (Inbound Order)

When an inbound message matches escort order structure:

1. The message is treated as escort workflow input.
2. A draft escort program is saved in `wbom_escort_programs` (status=`draft`).
3. The draft is sent to admin for review/completion.

### Business Rule (Admin Completion)

After admin fills escort name and escort mobile and sends the completed slip from the authoritative admin bridge to the same prefixed client number:

1. The draft is confirmed (or created if no draft row is found) in `wbom_escort_programs`.
2. Program is synced to escort roster DB (`escort_roster_entries`) via roster sync.
3. Escort identity is auto-resolved/created in employee DB:
	- `wbom_employees` row is created/updated from escort mobile.
	- linked `fpe_employees` identity row is ensured.
4. Finalized slip is routed back to the originating client number.

This completion message therefore acts as both:

- escort roster/program confirmation source, and
- automatic employee-onboarding source for missing escort employee records.

### Draft-Always and Prefix Safety

Prefix-based contacts are protected by draft gates (`draft_name_prefixes`, `draft_always_names`) so non-safe flows stay under admin review.

**Source Module:** `modules/message_router`, `modules/bridge_poller`, `modules/escort`, `modules/escort_roster/db.py`
**Source Function:** `_should_silent_skip()`, `_is_draft_always()`, `handle_escort_client_message()`, `handle_admin_escort_completion()`, `_resolve_escort_employee_id()`

---

## Force-Draft Triggers

### Complaint Phrases

If the inbound message contains any of these 11 phrases, the response is forced to draft regardless of intent or role:

**Bangla:** পাইনি, হয়নি, দেয়নি, কম এসেছে, সমস্যা, ঝামেলা, বেতন মেরে
**English:** dispute, issue, problem, complaint

**Business Rule:** Complaint content indicates a financial or operational dispute that requires admin judgment.

**Source Module:** `app/bridge_poller`
**Source Function:** `_COMPLAINT_PHRASES`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-10)

### Advance Request Phrases

If the inbound message contains any of these 5 Bangla phrases, the response is forced to draft:

- অ্যাডভান্স চাই
- অ্যাডভান্স দরকার
- অ্যাডভান্স লাগবে
- অগ্রিম চাই
- advance চাই

**Business Rule:** Advance request always requires admin approval before sending.

**Source Module:** `app/bridge_poller`
**Source Function:** `_ADVANCE_REQUEST_PHRASES`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-11)

---

## office_location Fast Path

**Business Rule:** When the detected intent is `office_location`, the system bypasses both the reviewed-reply memory system and AI entirely. The office location answer is retrieved directly from the RAG KB and returned.

**Purpose:** Guarantees that office location answers are always based on the KB (single source of truth) and not generated by LLM.

**Source Module:** `app/message_router`
**Source Function:** Routing step 12
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-47)

---

## LLM Fallback Polite Holding Message

**Business Rule:** When all LLM providers fail to generate a reply (GitHub, Groq, and Ollama all unavailable), the following holding message is returned automatically:

```
আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।
```

**Example:** System will never send an empty reply or raise an error to the customer. The holding message is always the final fallback.

**Source Module:** `app/llm.py`
**Source Function:** `_FALLBACK_REPLY`
**PKCA Report:** 08_ai_behavior_coverage_report.md

---

## Automated Reply Suffix

**Business Rule:** Every auto-generated reply sent to a customer has this suffix appended exactly once:

```
─────────────────
🤖 Automated Reply System
এই বার্তাটি স্বয়ংক্রিয়ভাবে তৈরি হয়েছে। ভুল হতে পারে।
```

**Validation:** The suffix anchor (`_AUTOMATED_SUFFIX_ANCHOR`) is checked before appending to prevent double-append on message retry.

**Source Module:** `app/bridge.py`
**Source Function:** `_AUTOMATED_SUFFIX`, `_AUTOMATED_SUFFIX_ANCHOR`
**PKCA Report:** 08_ai_behavior_coverage_report.md
