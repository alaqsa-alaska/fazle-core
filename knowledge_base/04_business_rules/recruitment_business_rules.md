---
title: Recruitment Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-07-21
runtime_index: true
---

# Recruitment Business Rules

## Candidate Eligibility

**Management Decision (BR-25 RESOLVED 2026-06-22):**

- **Age: 18–55 years** (Authoritative — production code `recruitment_flow._parse_age()`)
- Physically fit.
- Minimum class 8 preferred; certificate not mandatory if candidate can read/write Bangla.
- No prior experience required for security guard or escort.

## Auto-Reply Rule
Clear candidate FAQ may auto-send. Sensitive/internal/unclear requests route to admin.

## Required Candidate Data
- Name.
- Age.
- Education.
- Current address.
- Desired role.
- Phone number.

## Same-Day Joining Guidance
Do not guarantee same-day duty. Say possible if candidate comes prepared before 2 PM and work is available.

## Cross References
- ../05_workflows/recruitment_workflow.md
- ../05_workflows/whatsapp_reply_workflow.md
- ../03_ai_identity/candidate_identity.md
- ../00_governance/CANONICAL_BUSINESS_RULES.md

---

## Valid Recruitment Positions

### Purpose
Only these 9 positions are valid for the recruitment funnel. Candidates selecting a position outside this list are prompted to choose again.

| # | Position Name |
|---|---|
| 1 | Escort |
| 2 | Survey Scout |
| 3 | Security Guard |
| 4 | Security Supervisor |
| 5 | Assistant Supervisor |
| 6 | Operation Officer |
| 7 | Security In-Charge |
| 8 | Marketing Officer |
| 9 | Ghat Supervisor |

**Business Rule:** The system validates the candidate's stated desired role against this list. Partial matches and common aliases are mapped by the parser.

**Source Module:** `modules/recruitment_flow`
**Source Function:** `VALID_POSITIONS`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-36)
**Management Authority:** Production evidence; documented 2026-06-22

---

## Recruitment Scoring Algorithm

### Purpose
After a candidate completes the data collection session, a score is computed to prioritize candidates for follow-up.

### Scoring Rules

| Criterion | Score |
|---|---|
| Experience ≥ 6 years | 60 points |
| Experience ≥ 3 years (but < 6) | 40 points |
| Experience ≥ 1 year (but < 3) | 20 points |
| Experience < 1 year | 0 points |
| Target position matches one of 9 valid positions | 20 points |
| All required fields complete (name, age, area, position, experience, phone) | 20 points |
| **Maximum score** | **100 points** |

**Business Rule:** Score is computed at the end of the 7-step session and stored with the candidate record. Higher scores are surfaced first in admin review lists.

**Source Module:** `modules/recruitment_flow`
**Source Function:** `_compute_score()`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-34)
**Management Authority:** HK-34 approved in PKVC Management Decisions

---

## Recruitment Session TTL

**Business Rule:** A recruitment data-collection session expires after **24 hours** of inactivity.

**Example:** If a candidate starts providing data but does not complete the session within 24 hours, the session is marked expired. The candidate must restart from step 1 on their next message.

**Source Module:** `modules/recruitment_flow`
**Source Function:** `SESSION_TTL`
**PKCA Report:** 09_state_machine_coverage_report.md (SM-04), 10_hidden_rule_coverage_report.md (HK-33)
**Management Authority:** HK-33 approved in PKVC Management Decisions

---

## Recruitment AI Deterministic Fast-Replies

**Corrected 2026-07-21 — this section was stale.** `modules/recruitment_ai` (named below as the source module) has carried a docstring since **2026-07-11** stating it is "fully superseded, zero live callers" — its `_looks_like_fee_question()`/`_looks_like_contact_question()` fast-reply logic described here is **not called by the live router**. This document was never updated when that supersession happened; correcting it now rather than leaving it silently wrong, per this file's own house rule ("living document," see file header).

**Current live path:** `modules/conversation_layer/recruitment.py::generate_recruitment_reply()` (KB → RAG → Ollama → safety filter), called via `modules/recruitment_flow::route_recruitment_message()`. Deterministic fee/office/age facts now live in `modules/conversation_layer/playbooks.py::CANONICAL_FACTS`, checked via `analyze_recruitment_signals()`/`build_rule_reply()` before any LLM call — same "deterministic first, LLM only if nothing matches" principle the table below originally described, just relocated.

### Fast-Reply Triggers (No LLM Used) — historical table, module reference outdated (see correction above)

| Question Type | Trigger Detection | Response |
|---|---|---|
| Fee / joining fee | 12 fee-related Bangla phrases | Factual fee answer from source_of_truth.txt |
| Contact number | Contact query phrases | Official contact number from source_of_truth.txt |
| Office location | Office location phrases | Office location from source_of_truth.txt |
| Age limit | Age-related phrases | "সাধারণ বয়সসীমা ১৮–৫৫ বছর।" |

**Safe Fallback (for unclear questions):**
```
এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।
```

**Business Rule:** If the question is unclear or falls outside the 4 fast-reply categories, the safe fallback message is returned rather than using LLM.

**Source of Truth File:** `resources/ops/recruitment_source_of_truth.txt`

**Source Module (historical, superseded 2026-07-11):** `modules/recruitment_ai`
**Source Function:** `_looks_like_fee_question()`, `_looks_like_contact_question()`
**Live Source Module:** `modules/conversation_layer/playbooks.py`, `modules/conversation_layer/recruitment.py`
**PKCA Report:** 08_ai_behavior_coverage_report.md
**Management Authority:** Production evidence; documented 2026-06-22; correction documented 2026-07-21

---

## WhatsApp Route B Routing for Candidates/Job Seekers (2026-07-21)

**Business Purpose:** Define what a job-seeking/candidate sender gets on WhatsApp, and how it relates to the recruitment funnel above.

**Routing:** A `candidate`/`unknown`/`new_lead` sender's message first gets a chance to be claimed by `modules/recruitment_flow::recruitment_eligibility()` (the funnel described above) — if eligible, `route_recruitment_message()` handles it exactly as documented above. If **not** eligible, the same sender falls through to **Route B** (`modules/route_b`, Ollama-only, no KB/template machinery) as a general-purpose fallback rather than being silently dropped. Full routing policy: `../05_workflows/whatsapp_reply_workflow.md`.

**Known open gap (confirmed live 2026-07-21, not yet fixed):** `recruitment_eligibility()`'s `OPERATIONAL_INTENTS` set includes `salary_query` and `escort_duty` — a message classified with either intent is rejected as `operational_intent` **before** the 3-layer recruitment detector runs, regardless of the sender's role. Confirmed live: an unidentified sender asking *"how much salary for escort, monthly"* gets intent `salary_query` from both the deterministic and live-Ollama classifiers, is rejected by this gate, and falls through to Route B's generic handler instead of the funnel's KB-grounded salary reply. Not a silent drop (Route B still answers, asking who the sender is) but a misroute — a genuine candidate's pay question doesn't reach the answer that already exists for it. Recommended fix: don't hard-reject on intent alone for `unknown`/`candidate`/`new_lead` roles. See `../00_governance/CANONICAL_BUSINESS_RULES.md` Recruitment section and `../00_governance/management_decisions.md` 2026-07-21 entry.

**Approved Office Info canonical text** (also `fazle_knowledge_base` row `b11_office_address`):
```
আমাদের অফিস:
আল-মানারাতাইন স্কিল সলিউশনস (Al-Manaratayn Skill Solutions)
আগ্রপাড়া, ভিক্টোরিয়া গেইট নং ১, খোকনের বিল্ডিং (২য় তলা)
পোস্ট অফিস: উত্তর কাট্টলি (4217), থানা: পাহাড়তলী, চট্টগ্রাম সিটি কর্পোরেশন

অফিস সময়: সকাল ১০টা – বিকাল ৫টা (শুক্রবার বন্ধ)
যোগাযোগ: 01958 122300
সাথে আনুন: NID ফটোকপি (নিজের ও অভিভাবকের) + ৪ কপি পাসপোর্ট ও ২ কপি স্ট্যাম্প সাইজ ছবি + প্রয়োজনীয় কাপড়-চোপড়, কাঁথা, মশারি, বিছানার চাদর (১ টি করে)।
```

**Approved Deposit/Fee canonical text** (also `fazle_knowledge_base` row `b11_no_fee`; supersedes the older "pay ~৳1,330 upfront, refund after 6 months" framing everywhere it previously appeared):
```
চাকরির জন্য কোনো ঘুষ বা অবৈধ জামানত নেওয়া হয় না।

যদিও প্রসেসিং ফি, জয়েনিং ফি ইত্যাদি অন্যান্য প্রতিষ্ঠানের মত আমাদের এখানেও আছে। তবুও আমরা অগ্রীম গ্রহনের পরিবর্তে মাসিক বেতনের সাথে অর্থ্যাৎ বেতন থেকে কিস্তিতে কাটার ব্যবস্থা রেখেছি।

আর্থিক সমস্যার বিষয়ে আলোচনা করা যায়। টাকা নেই - এজন্য কারো চাকরিতে জয়েন বাঁধা হবে না।
প্রতিষ্ঠানের নির্দিষ্ট মানি-রিসিট বা টাকা জমা দেয়ার রশিদ ছাড়া কেউ টাকা চাইলে তা প্রতারণা — সরাসরি অফিসে যাচাই করুন বা কল দিন 01958 122300 নাম্বারে।
```

**Related Modules:** `modules/route_b`, `modules/identity_brain`, `modules/recruitment_flow`.
**Related Documents:** `../05_workflows/whatsapp_reply_workflow.md`, `../00_governance/CANONICAL_BUSINESS_RULES.md`, `../00_governance/management_decisions.md`.
