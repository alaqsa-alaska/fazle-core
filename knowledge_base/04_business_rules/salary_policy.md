---
title: Salary Policy — Al-Aqsa Security & Logistics Services Ltd.
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Salary Policy — Al-Aqsa Security & Logistics Services Ltd.
**Authority:** Management-approved (2026-06-23)
**Visibility:** safe_for_customer=True — candidates may receive salary information
**Applies to:** All roles; all channels (WhatsApp, Facebook, recruitment flow)
**Governed by:** BR-25 (age 18–55), management salary decision 2026-06-23

---

## Management-Approved Salary Schedule

This schedule is the single authoritative source for all salary figures across the AI system. Any hardcoded fallback, LLM prompt, or KB article that quotes salary figures must match this schedule exactly.

### Group 1 — Probation / Training Period

| Role | Period | Monthly Range |
|---|---|---|
| সিকিউরিটি সুপারভাইজার (Security Supervisor) | প্রবেশন / ট্রেনিং | ৳১০,০০০ – ৳১৫,০০০ |
| সহকারী সুপারভাইজার (Assistant Supervisor) | প্রবেশন / ট্রেনিং | ৳১০,০০০ – ৳১৫,০০০ |
| সিকিউরিটি গার্ড (Security Guard) | প্রবেশন / ট্রেনিং | ৳১০,০০০ – ৳১৫,০০০ |

### Group 2 — Operational Roles (Escort / Port / Vessel)

| Role | Monthly Range |
|---|---|
| জাহাজের এসকর্ট (Ship Escort) | ৳১২,০০০ – ৳১৭,০০০ |
| ট্যালিম্যান (Tallyman) | ৳১২,০০০ – ৳১৭,০০০ |
| সিলম্যান (Sealman) | ৳১২,০০০ – ৳১৭,০০০ |
| ঘাট সুপারভাইজার (Ghat Supervisor) | ৳১২,০০০ – ৳১৭,০০০ |
| ঘাট ইন-চার্জ (Ghat In-Charge) | ৳১২,০০০ – ৳১৭,০০০ |

### Group 3 — After Permanent Employment (স্থায়ীকরণ)

| Status | Monthly Package |
|---|---|
| স্থায়ী কর্মী (Permanent Employee) — সকল পদ | ৳১৭,০০০ – ৳২৪,৭০০ |

---

## Salary Rules

### SR-01 — Range-Based Policy
Salaries are range-based. The exact figure within the range is determined by:
- Role / designation
- Duty count and performance
- Duration of service
- Management discretion

No single fixed salary applies to all employees in a role.

### SR-02 — Duty-Based Component (Escort / Group 2 Roles)
Group 2 (escort/port roles) salaries are **ডিউটিভিত্তিক** (duty-based). Final monthly amount depends on number of escort programs completed. The escort pay formula (PAY-01) applies: `৳৪০০/দিন × ডিউটি দিন`.

### SR-03 — Training / Probation Period
- প্রবেশন কাল: ৩–৬ মাস (General roles); ৪৫ দিন (Survey Scout / Escort)
- During training, salary is in Group 1 range (৳১০,০০০ – ৳১৫,০০০)
- After evaluation, role transitions to permanent with Group 3 range

### SR-04 — Age Requirement (BR-25)
Active duty age range: **১৮ – ৫৫ বছর** (management decision 2026-06-22).
This applies to all positions including escort, guard, supervisor, port roles.

### SR-05 — Joining Fee and Form Fee
- জয়েনিং ফি: ৳৩,৫০০ (ঘুষ বা জামানত নয়; ৬ মাস চাকুরি পর ফেরত দেওয়া হয়)
- ফর্ম ফি: ৳৩৩০ (এককালীন)
- জয়েনের সময় কমপক্ষে: ৳১,০০০ + ৳৩৩০ = ৳১,৩৩০
- বাকি টাকা মাসে ৳৫০০ করে বেতন থেকে কাটা হয়

### SR-06 — Proactive Outbound Restriction
Salary complaint messages (`employee_salary_complaint` intent) must ALWAYS route to human review — never auto-replied. This is part of `ESCALATION_INTENTS` in `modules/social_auto_reply/risk_flagger.py`.

---

## What AI Should Reply for "বেতন কত?"

The AI must quote salary ranges per role, not a fixed amount. Standard reply structure:

```
বেতন পদ অনুযায়ী আলাদা:

সার্ভে স্কট / এসকর্ট পদ:
• ট্রেনিং (৪৫ দিন): ৳১০,০০০–১৫,০০০/মাস
• ট্রেনিং পরে (ডিউটিভিত্তিক): ৳১২,০০০–১৭,০০০/মাস

সিকিউরিটি গার্ড পদ:
• প্রবেশন (৩–৬ মাস): ৳১০,০০০–১৫,০০০/মাস
• স্থায়ী হলে: ৳১৭,০০০–৳২৪,৭০০/মাস

আপনি কোন পদে আগ্রহী?
```

---

## Cross-Module Enforcement

This salary schedule must be consistent across all the following:

| Module / File | How Salary Figures Appear | Verified |
|---|---|---|
| `modules/knowledge_base/__init__.py` | `_FALLBACK` hardcoded reply templates | ✅ Updated 2026-06-23 (CR-07, CR-08, CR-09) |
| `modules/social_auto_reply/reply_rules.py` | `AGE_ISSUE_REPLY`, salary-related auto-replies | Verify: read reply_rules.py |
| `fazle_knowledge_base` DB table | Admin-uploaded KB articles | Update via `/admin/kb/upload` if articles have old figures |
| `shared/reply_policy.py` `ROLE_PROMPTS` | Prompt templates (if any salary hints included) | Verify: `grep -n "salary\|বেতন" shared/reply_policy.py` |
| Recruitment flow auto-replies | `_SALARY_OBJECTION_REPLY` constant | Verify: `grep -n "salary_obj\|বেতন" modules/recruitment_flow/__init__.py` |

---

## History

| Date | Change |
|---|---|
| 2026-06-23 | Initial salary policy documented and authorized by management |
| 2026-06-23 | Three conflicts resolved in `modules/knowledge_base/__init__.py` fallback (CR-07: age 45→55; CR-08: escort upper ৳18,000→৳17,000; CR-09: survey scout upper ৳18,000→৳17,000) |
