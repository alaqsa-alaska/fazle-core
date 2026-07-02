---
title: Attendance Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Attendance Business Rules

## Source Of Truth
- Security guard: 12 hours equals one duty day.
- Escort: 24 hours/two shifts equals one duty day.
- Guard attendance may be self-reported or supervisor/operation officer reported.
- Escort attendance starts at duty slip and ends at release slip.

## Validation Rules
- Verify role and phone.
- Check duplicate attendance.
- Require admin approval or authorized workflow before final attendance save.
- Duty selfie may be requested.

## Payroll Impact
- Approved attendance feeds salary, bonus, late, leave, and deduction calculations.
- Unauthorized absence may cause up to 2 days deduction per 1 absent day.
- 3 consecutive late arrivals may cause 1 day salary deduction.

## অ্যাটেন্ডেন্স কীওয়ার্ড তালিকা

### উপস্থিত কীওয়ার্ড (Trigger: PRESENT)
হাজির, উপস্থিত, present, হাজির আছি, ডিউটিতে আছি, আছি, on duty, duty start, ডিউটি শুরু, চেক ইন, check in, checked in

### অনুপস্থিত কীওয়ার্ড (Trigger: ABSENT)
অনুপস্থিত, absent, আসতে পারব না, আসতে পারছি না, অসুস্থ, sick, ছুটি, leave

### কীওয়ার্ড ম্যাচিং নিয়ম
- কেস-ইনসেনসিটিভ (case-insensitive)
- বাংলা এবং ইংরেজি উভয় সাপোর্টেড
- আংশিক ম্যাচ: হ্যাঁ — সাবস্ট্রিং ম্যাচ ব্যবহার করা হয়
- অনুপস্থিত কীওয়ার্ড আগে চেক হয়; না পেলে উপস্থিত ধরে নেওয়া হয়
- লোকেশন এক্সট্র্যাকশন: location, loc, পোস্ট, পোষ্ট, সাইট, site, লোকেশন কীওয়ার্ড দিয়ে

## Cross References
- ../05_workflows/attendance_workflow.md
- ../02_admin_knowledge/admin_attendance_handling.md
