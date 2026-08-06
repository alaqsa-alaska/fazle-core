---
title: WhatsApp Reply Workflow — Route A / Route B
owner: Fazle Core Admin
status: active
last_verified: 2026-07-21
runtime_index: true
---

# WhatsApp Reply Workflow — Route A / Route B

**Status:** Implemented
**Scope:** Shared WhatsApp DM routing for `bridge1`, `bridge2`, and `meta`
**Policy Type:** Active operational workflow
**Language:** Bangla + system identifiers in English
**Source modules:** `modules.message_router`, `modules.identity_brain`, `modules.route_b`

---

## 1. উদ্দেশ্য

এই ডকুমেন্টে WhatsApp inbound message-এর জন্য অ্যাপের চূড়ান্ত reply workflow, routing policy, identity rules, context rules, phone normalization policy, এবং approved office/fee text নির্ধারণ করা হলো।

এই workflow-এর মূল লক্ষ্য:

- keyword-based ভুল/অপ্রাসঙ্গিক reply বন্ধ করা
- sender identity/role ভিত্তিক routing চালু করা
- WhatsApp DM-এর জন্য parallel reply engine বাদ দেওয়া
- known business contacts বনাম unknown/escort/guard/job-seeker conversations আলাদা করা
- Route B-তে শুধুমাত্র local Ollama ব্যবহার করা
- money/advance conversation-এ admin notification নিশ্চিত করা

---

## 2. মূল নীতি

### 2.1 Routing keyword-based হবে না

Routing-এর ভিত্তি হবে:

1. **phone normalization**
2. **identity resolution**
3. **conversation context**
4. **sender role/lifecycle**
5. **message intent**

শুধু keyword দেখে reply engine নির্বাচন করা যাবে না।

---

## 3. Final Routing Policy

| বিষয় / Sender Type | Route |
|---|---|
| Admin ↔ Accountant / Client / Family / Buyer / Staff / Internal Coordinator | **Route A** |
| Active Escort | **Route B** |
| Former Escort | **Route B** |
| On-leave / unclear-status Escort | **Route B** |
| Active Security Guard | **Route B** |
| Former Security Guard | **Route B** |
| On-leave / unclear-status Security Guard | **Route B** |
| টাকা / অগ্রিম / salary advance / জরুরি অর্থ সহায়তা | **Route B + Admin Notification** |
| চাকরি খুঁজছে / আবেদন করতে চায় | **Route B** |
| অফিসের তথ্য জানতে চায় | **Route B** |
| ফি / ডিপোজিট / joining / processing cost জানতে চায় | **Route B** |
| Context দেখে Escort/Guard candidate মনে হয় | **Route B** |
| Unknown sender | **Route B** |

---

## 4. Route Definitions

### 4.1 Route A

**Engine:** Existing mature Python / KB / existing business logic (`modules.message_router.process_message()`)
**Used for:**

- Admin
- Accountant
- Client
- Family
- Buyer
- Staff
- Internal Coordinator

**Notes:**

- Existing stable Python codepath অক্ষুণ্ণ থাকবে
- Existing safety gates বজায় থাকবে
- Customer-facing structured business flow এখানেই চলবে

**Universal AI Responder — Phase 2 (2026-08-06, Owner-approved, SHADOW MODE
ONLY — see `knowledge_base/00_governance/management_decisions.md` "Universal
AI Responder — Phase 2" for the full decision record):** the final tier of
this route — `modules.message_router.process_message()`'s Tier 15 (AI
FALLBACK), reached by any Route A role not claimed by an earlier, more
specific tier (e.g. `employee`, `supervisor`, `vendor`, `known_contact`,
`internal_coordinator`) — now computes a second, upgraded reply
(`modules.conversation_layer.generate_universal_reply()`: KB → RAG
(`rag.safe_answer()`) → the same multi-provider `ai.generate_reply()` chain
→ a faithfulness validator grounding every number in the reply against the
retrieved facts) in parallel with the existing bare `ai.generate_reply()`
call, and logs both side by side for comparison. **Which one is actually
returned/sent is gated by `app.config.Settings.tier15_universal_responder_live`
(default `False`)** — while off, Tier 15's live output is unchanged
pipeline-wise. Flipping that flag is a separate, later Owner decision.

### 4.2 Route B

**Engine:** **Ollama only** (`modules.route_b.handle_route_b_message()` → `app.ollama.generate_reply()`)
**No fallback:** Groq / GitHub Models / keyword-template engine ব্যবহার করা যাবে না

**Used for:**

- Escort
- Security Guard
- Former / inactive operational workers
- Unknown sender
- Job seeker / applicant
- Office info seeker
- Fee/deposit seeker
- Money/advance requester
- Contextual candidate-like conversations

---

## 5. Shared Channel Policy

নিচের তিনটি channel একই WhatsApp DM routing policy follow করবে:

- `bridge1`
- `bridge2`
- `meta`

অর্থাৎ routing behavior channelভেদে আলাদা হবে না।

---

## 6. Parallel Reply Engine Policy

WhatsApp DM-এর জন্য দুইটি parallel reply engine রাখা যাবে না।

### Final Decision

- **Keep:** `message_router`-based unified routing
- **Retire/disable for WhatsApp DM:** `social_auto_reply`

`social_auto_reply` WhatsApp direct-message reply generator হিসেবে ব্যবহৃত হবে না।

**Scope note:** `social_auto_reply` (classifier/reply_generator/intelligent_generator) মডিউলটি নিজে delete করা হয়নি — এটি এখনও Facebook Messenger DM এবং Facebook Page comment-এর জন্য জীবিত/live engine হিসেবে ব্যবহৃত হচ্ছে (`app.main._handle_messenger_message`, `_handle_fb_comment`)। শুধুমাত্র WhatsApp (bridge1/bridge2/meta) থেকে এই মডিউলে message পাঠানোর gate সরানো হয়েছে।

---

## 7. Identity Policy

### 7.1 Approved roles

System-level routing-এ নিচের role-গুলো বিবেচনায় থাকবে (source: `modules.identity_brain._ROLE_PRIORITY`):

- `admin`
- `accountant`
- `client_escort_buyer` / `vip_client` / `repeat_client` (client/buyer)
- `family`
- `employee` (staff)
- `internal_coordinator`
- `escort`
- `security_guard`
- `candidate`
- `unknown`

### 7.2 Internal Coordinator

`internal_coordinator` approved role।

**Routing:** `internal_coordinator` → **Route A**

**Example:** `+8801817201596` (Alim) → `internal_coordinator` (seeded in `fazle_contact_roles`, 2026-07-21 — overrides a bogus auto-created `wbom_employees` stub without modifying that table).

### 7.3 Former / Inactive Policy

**Strict rule:** `Inactive = former`. এটি hard rule।

### 7.4 On-leave / unclear lifecycle handling

যদি exact lifecycle table/field থেকে স্পষ্ট না হয়, তাহলে Route B local AI context ও follow-up question ব্যবহার করে বুঝবে।

**Allowed contextual questions (examples):**

- ভাই, আপনার নাম কী?
- আপনি যে পিকচার পাঠিয়েছেন তা পরিষ্কার পড়া যাচ্ছে না। আপনার নাম ও মোবাইল নাম্বার লিখে দিন।
- আপনি কখন join করেছেন?
- কত তারিখ থেকে আপনার duty শুরু হয়েছে?
- duty কি এখনও চলছে, নাকি শেষ?
- শেষ হলে কখন শেষ হয়েছে?
- আপনি এখন কোথায় আছেন?

**Important:** এই প্রশ্নগুলো rigid script হিসেবে না, **context-aware conversational logic** হিসেবে ব্যবহার হবে।

**Implementation note:** lifecycle (active/former/on_leave) একটি read-only heuristic (`wbom_employees.status` + `wbom_attendance` recency — কোনো নতুন column ছাড়া), Route B prompt-এর context হিসেবে carry হয়, routing সিদ্ধান্ত হিসেবে নয় — active/former/on-leave escort/guard সবাই একইভাবে Route B-তে যায়।

---

## 8. Unknown Sender Handling

Unknown sender-এর জন্য **hardcoded blind greeting** ব্যবহার করা যাবে না।

**NOT allowed:**

- message না পড়ে fixed reply
- context ছাড়া "আপনি কে?"
- robotic opening

**Required behavior:** Local AI reply দেওয়ার আগে:

1. incoming message পড়বে
2. sender number দেখবে
3. WhatsApp profile name দেখবে
4. name matching করবে (first / middle / last token)
5. linked system features/context দেখবে
6. past message history থাকলে তা পড়বে
7. তারপর natural contextual reply দেবে

**Principle:** Unknown sender routing হবে role-based, কিন্তু reply হবে **context-aware**।

---

## 9. Money / Advance Workflow

যদি conversation-এ টাকা / advance / salary advance / emergency financial need detect হয়:

**Route:** Route B

**Additional action:** Admin notification required

**Write boundary:** এই flow-এ write সীমাবদ্ধ থাকবে approved draft path-এ:

- `fazle_draft_replies`
- `fazle_payment_drafts`

**Not allowed:**

- direct transaction creation
- payroll write
- HR table mutation
- restricted operational table update

**2026-07-22 update — role scoping fix:** `is_advance_request()` (keyword/
distress-word match, no identity awareness of its own) previously fired
identically for all four `ROUTE_B_ROLES`, so a candidate/unknown sender's
generic distress word could be framed as a MONEY/ADVANCE REQUEST admin
draft with no employee/payroll relationship for the admin to act on. Now
scoped in `modules.route_b.handle_route_b_message()` to `escort`/
`security_guard` roles only — the two `ROUTE_B_ROLES` that are real,
on-duty operational workers. Candidate/unknown senders still receive a
normal, identity-aware Route B reply for a money-shaped message, just
without the admin-notification framing. Separately, a known EMPLOYEE's
informational salary question (role `"employee"`, Route A/Tier 10) is
answered directly from `get_payroll_summary()` — checked ahead of the
Sprint-3A payment-request trigger scan so the bare keyword "বেতন" no longer
intercepts an informational question before the real payroll answer.

---

## 10. Phone Number Normalization Policy

### 10.1 Goal

একটি **single, simple, centralized** phone normalization rule থাকবে।

### 10.2 Reason

আগে multiple normalization logic থাকার ঝুঁকি ছিল। বিশেষ করে:

- leading `0` হারিয়ে যাওয়া
- WhatsApp-format বনাম user-typed format mismatch
- DB comparison error

### 10.3 Final policy

Normalization rule:

- Bangladesh mobile number-কেন্দ্রিক
- simple হবে
- over-engineered হবে না
- missing leading `0` issue ঠিক করবে
- same number-এর সব variant এক normalized form-এ আনা হবে

**Implementation:** two documented canonical roots — `modules.phone_normalizer.normalize_phone()` (8801XXXXXXXXX, WBOM/RBAC domain) এবং `modules.fazle_payroll_engine.normalizer.normalize_bd_phone()` (01XXXXXXXXX, FPE domain), সেতু হিসেবে `to_880()`/`from_880()`। দুইটি ভিন্ন domain-এর জন্য দুইটি root, কোনোটাই duplicate reimplementation না — নতুন normalizer তৈরি করা যাবে না।

### 10.4 Coverage

Normalization apply করতে হবে:

- inbound sender number
- outbound target number
- message body-তে user-typed phone number
- DB lookup comparison
- identity matching
- role matching

---

## 11. Local AI Context Read Boundary

Route B local AI **read-only** access পাবে approved context sources-এ।

### 11.1 Allowed read sources

**1) `wbom_whatsapp_messages`** — Allowed fields: `sender_number`, `receiver_number`, `direction`, `message_body`, `ai_response`, `source_timestamp`, `platform`, `identity_role`, `identity_confidence`, `classification`, `intent_detected`, `extracted_text`. Purpose: recent conversation history, both inbound and outbound.

**Correction (2026-08-06, Universal AI Responder Phase 2):** "both inbound
and outbound" was already true for Route B's own separate history function
(`modules.route_b._get_conversation_history()`) but was NOT true for Route
A's shared `modules.message_router.get_recent_history()` — that function
was inbound-only until this date. Fixed as part of Phase 2 to actually
match what this line always documented. **This is a live behavior change,
not gated by `tier15_universal_responder_live`** — every existing caller
of `get_recent_history()` (recruitment's live reply path, Tier 15's
existing bare-AI fallback, `recruitment_detector`) now receives bidirectional
history immediately, regardless of the shadow flag's state, since the flag
only controls which *pipeline* generates Tier 15's reply, not what history
context is built for either pipeline.

**2) `wbom_contacts`** — Allowed fields: `display_name`, `relation`, `company_name`, `notes`, `personality_hint`, `last_seen`, `interaction_count`. Purpose: saved contact metadata.

**3) Per-bridge WhatsApp SQLite contact store (`whatsmeow_contacts`)** — Read-only mode only. Allowed fields: `push_name`, `first_name`, `full_name`, `business_name`. Purpose: actual WhatsApp-side profile identity hints.

**4) `identity_brain.detect_identity()` output** — Allowed context: `role`, `confidence`, `source`, `employee_id`, `employee_name`, `designation`, lifecycle/context tags. Purpose: restricted tables-এ direct query ছাড়া pre-computed identity signal পাওয়া.

**5) `modules.knowledge_base.get_reply()` (2026-07-22 addition)** — Allowed as best-effort, read-only, exception-safe grounding context only. Before 2026-07-22, `modules.route_b` never consulted KB/RAG at all (documented explicitly in its own module docstring), which left the system prompt's existing "only use given Knowledge, never invent a figure" rule with nothing to actually ground itself on. A KB/RAG hit (if any) is now folded into the same prompt context as sources 1-4 — it never short-circuits or replaces the actual `app.ollama.generate_reply()` call, and a lookup failure degrades silently (reply proceeds exactly as before).

### 11.2 Explicitly out of bounds

Route B handler নিচের table-এ direct query করবে না:

- `wbom_employees`
- `fpe_employees`
- `fpe_cash_transactions`
- `wbom_escort_programs`
- `escort_roster_entries`
- `wbom_attendance`

এই table-গুলোর signal শুধুমাত্র existing approved abstraction (`identity_brain`) এর মাধ্যমে context হিসেবে আসতে পারবে।

---

## 12. Safety Rules

নতুন routing চালু হলেও existing safety net ভাঙা যাবে না।

**Must preserve** (all enforced inside `modules.bridge_poller.process_bridge_inbound()`, shared by every entry point):

- cooldown
- keyword-flood protection
- prompt-injection protection
- `DRAFT_ALWAYS`
- financial-draft forcing
- loop-detection
- `AI_SAFE_MODE`

---

## 13. Office Info — Approved Canonical Text

```text
আমাদের অফিস:
Al-Rifa'i Integrated Services
আগ্রপাড়া, ভিক্টোরিয়া গেইট নং ১, খোকনের বিল্ডিং (২য় তলা)
পোস্ট অফিস: উত্তর কাট্টলি (4217), থানা: পাহাড়তলী, চট্টগ্রাম সিটি কর্পোরেশন

অফিস সময়: সকাল ১০টা – বিকাল ৫টা (শুক্রবার বন্ধ)
যোগাযোগ: 01958 122300
সাথে আনুন: NID ফটোকপি (নিজের ও অভিভাবকের) + ৪ কপি পাসপোর্ট ও ২ কপি স্ট্যাম্প সাইজ ছবি + প্রয়োজনীয় কাপড়-চোপড়, কাঁথা, মশারি, বিছানার চাদর (১ টি করে)।
```

**Source of truth:** `fazle_knowledge_base` row `b11_office_address`, kept in sync with `modules.knowledge_base._FALLBACK`, `modules.social_auto_reply.reply_rules.LOCATION_REPLY`, and `modules.conversation_layer.playbooks.CANONICAL_FACTS["office_location"]`. Company name itself now centralized in `Settings.company_name` (`app/config.py`) — see management_decisions.md 2026-07-22 "Second Rebrand".

**RESOLVED 2026-08-04 (Owner Decision):** the office-contact-number inconsistency flagged below is closed. Authoritative list: 01958-122300, 01958-122301, 01958-122302, 01958-122303 (supersedes C-08 and every one of the conflicting values found below). Corrected in `modules/social_auto_reply/reply_rules.py` (`OFFICE_NUMBERS`), `modules/social_auto_reply/salary_flow.py` (`_OFFICE_CONTACT`), `modules/knowledge_base/__init__.py`, `modules/recruitment_flow/__init__.py`, `modules/recruitment_ai/__init__.py`, and `knowledge_base/01_employee_knowledge/company_identity.md`. Full record: `management_decisions.md` → "Office Contact Numbers — C-08 Conflict Resolved" (2026-08-04). A dynamic lookup (`reply_rules.get_effective_office_numbers()`) also now exists as the foundation for the planned Admin UI at `/escort-roster` to manage these going forward.

**Original issue (kept for record, no longer current):** office contact numbers were inconsistent across sources — 01958-122300 (this canonical text, `resources/office_info.txt`), 01958-122322 (`resources/ops/recruitment_source_of_truth.txt`), and other desk-specific numbers (122311, 122327) appeared in various KB fallback entries without it being clear which was authoritative for which purpose.

## 13.1 Deposit/Fee — Approved Canonical Text

```text
চাকরির জন্য কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না।

যদিও প্রসেসিং ফি, জয়েনিং ফি ইত্যাদি অন্যান্য প্রতিষ্ঠানের মত আমাদের এখানেও আছে। তবুও আমরা অগ্রীম গ্রহনের পরিবর্তে মাসিক বেতনের সাথে অর্থ্যাৎ বেতন থেকে কিস্তিতে কাটার ব্যবস্থা রেখেছি।

আর্থিক সমস্যার বিষয়ে আলোচনা করা যায়। টাকা নেই - এজন্য কারো চাকরিতে জয়েন বাঁধা হবে না।
প্রতিষ্ঠানের নির্দিষ্ট মানি-রিসিট বা টাকা জমা দেয়ার রশিদ ছাড়া কেউ টাকা চাইলে তা প্রতারণা — সরাসরি অফিসে যাচাই করুন বা কল দিন 01958 122300 নাম্বারে।
```

**Source of truth:** `fazle_knowledge_base` row `b11_no_fee`, kept in sync with `modules.knowledge_base._FALLBACK`, `modules.social_auto_reply.reply_rules.FEES_REPLY`, and `modules.conversation_layer.playbooks.CANONICAL_FACTS["fee"]`. Supersedes the older "pay ~৳1,330 at joining, refund after 6 months" framing everywhere it previously appeared.

## 14. Route A — No Auto Reply (Contact-Name-Based Gate)

**Owner decision, 2026-07-22.** Independent of the role-based Route A/B split
in sections 3–4 above: if the **sender's saved contact name** (canonical
authority — see §7.1/§10 — the name in the admin's own phone contact book,
never the WhatsApp profile/display name) contains any of the following
words **anywhere** — start, middle, or end, substring match, not prefix-only:

```
Client · Escort · Office · Operation · Accountant · Security Guard
Al-Aqsa / আল-আকসা · Al-Manaratayn / আল-মানারাতাইন · Al-Rifa'i / al rifai
+ tcis · gms · dalal (Owner-confirmed 2026-07-22, kept as-is)
+ any other .env-configured no-reply name/role/phone (DRAFT_ALWAYS_NAMES,
  DRAFT_ALWAYS_ROLES non-role tokens, DRAFT_ALWAYS_PHONES)
```
All three brand-name generations kept simultaneously (augment-don't-replace
rule) since older saved contacts may still carry an earlier name.

**Not the only brand-name gate in this codebase — don't assume this one being
current means all of them are:** `modules/escort/__init__.py`'s
`is_completed_escort_draft()` has its own, separate, message-*body*-text
brand check (`_AL_AQSA_RE`) gating admin escort-confirmation detection. It
was missed during this same 2026-07-22 rebrand and silently broke every
admin confirmation from that day until fixed 2026-08-01 — see
`escort_source_of_truth_workflow.md`'s "Enforcement" section for the
incident. When a future rebrand happens, grep for every hardcoded brand-name
literal repo-wide, not just this one.

Examples (all match): "Escort Masud WA", "Md Karim Escort", "Client Nizam
Office", "Operation Murad", "Al Aqsa Accountant", "Rahim Office Chattogram".

**General behavior:** no AI auto-reply, no generic reply, no recruitment
reply — the sender gets `""` (nothing sent). This governs the Route B
population specifically (`escort`/`security_guard`/`candidate`/`unknown` —
Route A's own mature per-role tiers, e.g. verified `client_escort_buyer`,
`accountant`, `employee`, `admin`, already never risk an AI-generated reply
regardless of name, so this gate does not need to re-check them). Family
(§7 personal-reply Tier 1) is unaffected — that reply is a fixed, non-AI,
non-generic, non-recruitment greeting, not one of the three prohibited
categories, and was left unchanged.

**Escort exception:** if the name contains "Escort" specifically, and the
message contains a mother vessel, a lighter vessel, and at least one mobile
number (i.e. it parses as a genuine order — reuses the same strict-order
parser as the escort-order safety net, §9), it is treated as an escort-order
candidate: Message Parser extracts structured fields, a draft (not a final
row) is created and sent to the admin number for review — same as any other
escort order. **No auto-reply is sent to the sender** in this case either;
only the destination differs (admin review vs. silence). Any other message
from an Escort-named contact gets full silence, same as the general rule.
An order-shaped message from a **non**-Escort-named contact (e.g. "Client
Nizam Office") does not get this name-keyed exception — it is still routed
to a human review draft, but via the pre-existing, role-agnostic escort-order
safety net (§9's `intent`+shape gate), not this gate.

**Implementation:** two enforcement points, both in `modules/message_router/__init__.py`:
1. `_should_silent_skip()` — early gate keyed off `wbom_contacts.display_name`
   (fires before identity resolution). Fixed 2026-07-22: previously fell
   into an either/or with `.env`-configured names and silently dropped the
   static keyword list whenever `DRAFT_ALWAYS_NAMES`/`DRAFT_ALWAYS_ROLES` had
   any custom tokens configured (true in production) — now a union of both.
2. Route B branch backstop (§4.2) — keyed off `identity_brain`'s own resolved
   `display_name`, for a contact not yet synced into `wbom_contacts`
   (see `knowledge_base/06_developer_system/contact_sync.md`).

**Registered-employee exemption (2026-07-22 addition):** a phone verified in
the canonical employee tables (`wbom_employees`/`fpe_employees`, active or
inactive — checked via the new `identity_brain.is_registered_employee_phone()`)
now bypasses `_should_silent_skip()`'s name-token match entirely, checked
*before* the token match runs. Previously a genuine employee saved under a
contact name like "Escort Rahim" would be silenced by this gate exactly like
a stranger, even though their phone is on file — they now continue to their
normal identity-based routing (Route A employee/Tier 10 for active
employees, verification/admin-review for inactive) instead. The exemption is
phone-specific, not name-specific: a *different*, non-employee phone saved
under the identical name text is still silenced as before.

**Known open item (not resolved this pass):** the live token list also
includes `tcis`, `gms`, `dalal` alongside the 8 documented terms above —
left unchanged pending Owner confirmation these were separately approved,
rather than silently removed or kept.

## 15. Route B Conversation Handoff (Built 2026-07-22 — NOT YET ACTIVE)

**Status: `ROUTE_B_HANDOFF_ENABLED=false` (default). Implemented, tested,
not yet enabled in production — pending a separate Owner review of the
implementation report.** This section describes the built behavior for when
the flag is turned on; until then, Route B behaves exactly as in §4.2, with
zero change.

**Purpose:** let Route B (Ollama-only chat for escort/security_guard/
candidate/unknown senders) transfer a conversation to an existing canonical
workflow once identity/intent becomes sufficiently clear — without Route B
ever gaining write access to any business table itself, and without
changing which senders reach Route B in the first place (§4.2's role split
is unchanged; `employee` was explicitly **not** added to `ROUTE_B_ROLES`).

**Non-negotiable boundary:** Ollama may only ever *recommend* a category
(`app.ollama.classify_route_b_category()` — Ollama-only, no Groq/GitHub
fallback, same design boundary as the rest of Route B). It never creates a
session, assigns a role, activates an employee, links a phone number, or
writes a final business row. Every state-changing call is an existing,
unmodified canonical function (`modules.recruitment_flow`,
`modules.employee_conversation`, `modules.escort`) — `modules/route_b_handoff`
only decides *when* to call them and *with what corroboration*.

**Three handoff types, all audited in `fazle_route_b_handoffs`:**

1. **Recruitment (Stage 1, flag: `ROUTE_B_HANDOFF_ENABLED`).** An active
   `fazle_recruitment_sessions` row for the sender is continued
   unconditionally via `intake_message()` — no Ollama call, no keyword
   re-check (an existing session's own answer-collection has no keyword
   gate). With no active session, `recruitment_eligibility()` (deterministic,
   unmodified) runs first exactly as message_router's own Tier 7/8 already
   does; only when that says no *and* Ollama's category is `recruitment`
   does the same deterministic function get a second, wider look — the
   current message plus the immediately preceding turn — as corroboration.
   Ollama's raw category is never sufficient by itself.
2. **Employee/former-employee claim (Stage 2, same flag, built + tested,
   never live-enabling access).** Because identity_brain's Step 3 phone
   lookup matches `wbom_employees` regardless of Active/Inactive status,
   any sender reaching Route B by construction has a phone that did **not**
   match — so a claim here can never be resolved to live Tier 10 access
   this way. `resolve_employee_identity()` (phone/employee_id-based, name-
   only match is `verified=False`) runs against a best-effort, non-Ollama,
   regex-extracted `employee_id`/name hint; the result — verified or not —
   always produces an admin-review draft only (`fazle_draft_replies`,
   `intent='route_b_employee_claim_review'`), reply `""` to the sender
   either way. Genuine access is only ever (re-)granted the existing way:
   an admin corrects/links the phone on `wbom_employees`, after which
   identity_brain recognizes it automatically on the sender's next message.
   Cooldown (`ROUTE_B_HANDOFF_COOLDOWN_HOURS`, default 6h) suppresses a
   second draft for a repeated claim from the same phone.
3. **Escort/security_guard order (Stage 3, separate flag:
   `ROUTE_B_ESCORT_HANDOFF_ENABLED`, default false — "prepared, not
   activated" per Owner decision, independent of the Stage 1/2 flag even
   after that one is turned on).** Reuses `modules.escort.is_order_complete()`
   (§9's own gate, extracted to a public function, behavior-preserving) —
   first against the current message alone, then, if incomplete, against
   the governed conversation history (`wbom_whatsapp_messages`, never
   Ollama's own output) concatenated with the current message, for fields
   spread across turns. Complete → `handle_escort_client_message(..., is_verified=False)`,
   the same unverified-sender structured-draft path §9 already uses.
   Incomplete → no handoff, Route B's own clarification reply stands.

**Ollama-failure protection:** `app.ollama.generate_reply()` already never
raises — any failure (unreachable/non-200/exception) degrades to a fixed
`FALLBACK_REPLY_TEXT` string, so the sender is never left without a reply.
Route B detects that exact string and additionally alerts an admin once per
cooldown window (`record_ollama_failure()`), so a prolonged outage doesn't
go unnoticed — the sender-facing reply is unchanged either way.

**Advance-request precedence:** the existing, protected
`is_advance_request()` path in `handle_route_b_message()` is evaluated and
returned *before* the handoff layer runs at all — the handoff feature
cannot alter or race that behavior regardless of its own flag state.

**Rollout:** Stage 1+2 code exists behind one master flag; Stage 3 requires
that flag *and* its own, so it can be reviewed and enabled independently
and later than Stage 1/2. Purely additive DB change (`fazle_route_b_handoffs`,
migration 057) — safe to leave in place even if never enabled.

## Cross References

- ../04_business_rules/recruitment_business_rules.md
- ../04_business_rules/escort_business_rules.md
- ../00_governance/CANONICAL_BUSINESS_RULES.md
- ../00_governance/management_decisions.md
</content>
