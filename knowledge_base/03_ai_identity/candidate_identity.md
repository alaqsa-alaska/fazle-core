---
title: Candidate Identity
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Candidate Identity

## Purpose
Identify job seekers and route them to recruitment-safe answers.

## Detection Signals
- Unknown number asks about job, work, salary, joining, training, office, duty, vacancy, documents, age, education, or Facebook ad.
- Uses words such as চাকরি, job, apply, কাজ, ডিউটি, বেতন, জয়েনিং, ট্রেনিং, অফিস.
- Asks candidate-style questions, not client service questions.

## Permissions
Allowed:
- Company identity.
- Recruitment information.
- Candidate documents.
- Candidate-safe fee answer.
- Role salary ranges intended for recruitment.

Not allowed:
- Internal system rules.
- Database names.
- Admin workflows.
- Employee-specific payment/attendance data.

## Default Action
Auto-reply if intent is clear and non-sensitive. Route to admin if unclear, abusive, sensitive, or fraud-risk.

## Cross References
- ../01_employee_knowledge/recruitment_policy.md
- response_rules.md
- permission_matrix.md

## Revision History
- 2026-06-19: Created from candidate identification rules.
