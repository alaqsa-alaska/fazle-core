---
title: PKCA Report 10: Hidden Rule Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 10: Hidden Rule Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Hidden Rule Audit (HK-01 to HK-47)

The 47 hidden rules (HK) were catalogued in the PKVC audit. This report measures which ones are documented in the KB.

### Approved by Management (from PKVC Management Decisions):
HK-01, HK-03, HK-04, HK-09, HK-13, HK-19, HK-24, HK-33, HK-34, HK-44

---

## Silent-Skip and Draft-Gate Rules (HK-01 to HK-12)

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-01 | Silent-skip token list: accountant + 11 display-name tokens (al-aqsa, escort, client, operation, tcis, gms, dalal, office) = NO reply, NO draft | `message_router._should_silent_skip` | 0% — No KB article documents silent-skip tokens |
| HK-02 | role='blocked' in fazle_contact_roles → hard silent skip | `message_router._should_silent_skip` | 0% |
| HK-03 | Safe auto-send intents: only recruitment, join, greeting, office_location, salary_query, payment_due, attendance, leave, escort_duty | `message_router._SAFE_AUTOSEND_INTENTS` | 0% |
| HK-04 | advance_request excluded from auto-send even though it looks like a safe intent | `message_router` comment | 0% |
| HK-05 | Recruitment BLOCKED for any non-candidate/unknown/new_lead role regardless of message text | `message_router` step 5 | 0% |
| HK-06 | Identity resolution priority: admin > family > accountant > vip_client > client_escort_buyer > repeat_client > vendor > employee > supervisor > candidate > unknown | `identity_brain._ROLE_PRIORITY` | 0% |
| HK-07 | Employee resolution evidence: cash transactions + attendance records + escort roster + contact DB (4 secondary sources) | `identity_brain` steps 3–4 | 0% |
| HK-08 | Escort content trigger keywords: m.v., mother vessel, lighter, escort lagbe, m.t., এমভি, destination, lighter vessel | `message_router._looks_like_escort_order` | 0% |
| HK-09 | Draft-always roles: accountant, client_escort_buyer, vip_client, repeat_client — never auto-send | `bridge_poller._is_draft_always` | 0% |
| HK-10 | Complaint phrases force draft: পাইনি, হয়নি, দেয়নি, কম এসেছে, সমস্যা, ঝামেলা, বেতন মেরে, dispute, issue, problem | `bridge_poller._COMPLAINT_PHRASES` | 0% |
| HK-11 | Advance request phrases force draft: অ্যাডভান্স চাই, অ্যাডভান্স দরকার, অগ্রিম চাই, advance চাই | `bridge_poller._ADVANCE_REQUEST_PHRASES` | 0% |
| HK-12 | Outbound poison filter: 16 internal marker strings blocked from customer messages | `bridge_poller._OUTBOUND_POISON` | 0% |

---

## Loop Protection and Flood Rules (HK-13 to HK-18)

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-13 | Loop detection: 3 replies within 120s → pause auto-reply 600s | `bridge_poller._LOOP_MAX_REPLIES/WINDOW/PAUSE` | 0% |
| HK-14 | Keyword flood: same keyword >3 times in 5 min → blocked 15 min | `bridge_poller._KW_FLOOD_*` | 0% |
| HK-15 | Prompt injection: 18 patterns blocked and logged to outbound_safety_incidents | `bridge_poller._PROMPT_INJECTION_PATTERNS` | 0% |
| HK-16 | Groups (@g.us), newsletters, status@broadcast silently skipped at SQL level | `bridge_poller._fetch_new_messages` | 0% |
| HK-17 | LID-unresolved DMs → phone='unresolved:\<lid\>' — never dropped | `bridge_poller._fetch_new_messages` | 0% |
| HK-18 | On fresh start (no cursor) → poller starts from NOW()-5 minutes | `bridge_poller._get_cursor` | 0% |

---

## Escort Financial Rules (HK-19 to HK-25)

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-19 | Transport rates: Dhaka/Narayanganj=600, Faridpur=700, Mongla=800, Barishal/coastal=900, Khulna/Jessore=1000, default=600 | `escort_lifecycle._TRANSPORT_RATES` | 5% — rate table referenced but not exact BDT values |
| HK-20 | Food = duty_days × 150 BDT (hardcoded) — release slip draft only | `escort_lifecycle._calc_duty_days` | 0% |
| HK-21 | Release slip draft estimate is DRAFT ONLY — rates not synced to escort_calculation_config DB table | `escort_lifecycle` comment | 0% |
| HK-22 | Release date validation: future dates and dates >1 year old rejected | `escort_lifecycle._validate_release_date` | 0% |
| HK-23 | Duty days >90 → SUSPICIOUS warning in draft | `escort_lifecycle.build_release_draft` | 0% |
| HK-24 | Payroll default rate: 800 BDT/day per escort program (legacy; now overridden by formula) | `payroll.DEFAULT_PER_PROGRAM_RATE` | 0% |
| HK-25 | Payment workflow rate: basic_salary / 30 per day (not 800) | `payment_workflow.create_escort_payment_draft` | 0% |

---

## Payroll State Machine Rules (HK-26 to HK-27)

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-26 | Payroll state machine: draft → reviewed → approved → locked → paid; cancelled from any non-paid state | `payroll.ALLOWED_TRANSITIONS` | 0% |
| HK-27 | Payroll compute idempotent on UNIQUE(employee_id, period_year, period_month) WHERE status != 'cancelled' | `payroll.compute_run` | 0% |

---

## RAG Engine Rules (HK-28 to HK-32)

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-28 | BM25 chunk: 320 chars, overlap 60, k1=1.5, b=0.75 | `rag` constants | 15% — rag_strategy.md mentions BM25 but not params |
| HK-29 | RAG excluded directories: 11 dir patterns (\_internal\_archived, tests, etc.) | `rag._EXCLUDED_DIRS` | 0% |
| HK-30 | RAG excluded filenames: 11 patterns (analysis, prompt, intent, debug, test, etc.) | `rag._EXCLUDED_NAME_KEYWORDS` | 0% |
| HK-31 | RAG chunk safety filter: 30+ internal marker patterns purged from chunks | `rag._CHUNK_UNSAFE_PATTERNS` | 0% |
| HK-32 | RAG stop words: 80+ Bangla+English function words excluded | `rag._STOP_WORDS` | 0% |

---

## Recruitment Rules (HK-33 to HK-36) — CONFLICT PRESENT

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-33 | Recruitment session TTL: 24 hours | `recruitment_flow.SESSION_TTL` | 0% |
| HK-34 | Recruitment scoring: experience ≥6yr=60pts, ≥3yr=40pts, ≥1yr=20pts; target position=20pts; all fields=20pts | `recruitment_flow._compute_score` | 0% |
| HK-35 | **CONFLICT BR-25:** Candidate age range: **18–55 years** (code) vs **18–45 years** (KB) | `recruitment_flow._parse_age` | CONFLICT — awaiting management decision |
| HK-36 | Valid positions (9): Escort, Survey Scout, Security Guard, Security Supervisor, Assistant Supervisor, Operation Officer, Security In-Charge, Marketing Officer, Ghat Supervisor | `recruitment_flow.VALID_POSITIONS` | 0% |

---

## Admin Command Rules (HK-37 to HK-42)

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-37 | Admin command dedup: SHA1(text+phone), 30s TTL, 256-entry cache | `admin_commands._dedup_seen` | 0% |
| HK-38 | APPROVE supports multiple IDs: APPROVE 165 166 167 | `admin_commands._APPROVE_RE` | 0% |
| HK-39 | Bangla digits accepted in commands: APPROVE ১৬৫ works | `admin_commands._BN_DIGITS` | 0% |
| HK-40 | RBAC role levels: viewer < operator < accountant < admin < superadmin | `rbac.COMMAND_ROLE` | 10% — role_permissions.md names roles but not hierarchy levels |
| HK-41 | Bootstrap: ADMIN_NUMBERS from .env auto-created as superadmin on first sight | `rbac.ensure_bootstrap_admins` | 0% |
| HK-42 | API keys stored as SHA-256 hash | `rbac.hash_api_key` | 0% |

---

## Advance and Payment Rules (HK-43)

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-43 | Advance trigger phrases (18): includes emergency/medical/family crisis variants | `payment_workflow.ADVANCE_KEYWORDS` | 0% |

---

## Rate Limiting and System Rules (HK-44 to HK-47)

| HK | Rule | Source | KB Coverage |
|---|---|---|---|
| HK-44 | Reply cooldown: 60 seconds (Redis primary, in-memory fallback) | `bridge_poller.REPLY_COOLDOWN` | 0% |
| HK-45 | Bridge poll interval: adaptive 1s → 30s backoff (1.5× per idle) | `bridge_poller.BRIDGE_POLL_MIN/MAX_S` | 0% |
| HK-46 | OCR image criteria: JPG/JPEG/PNG/WEBP, 1KB–8MB range | `bridge_poller._IMAGE_OCR_MIN/MAX_BYTES` | 0% |
| HK-47 | office_location intent bypasses reviewed-reply memory AND AI → KB-only fast path | `message_router` step 12 | 0% |

---

## Hidden Rule Coverage Summary

| Domain | HK Range | Rules | Documented | Coverage |
|---|---|---|---|---|
| Silent-skip / Draft gate | HK-01 to HK-12 | 12 | 0 | 0% |
| Loop / Flood / Injection | HK-13 to HK-18 | 6 | 0 | 0% |
| Escort financial | HK-19 to HK-25 | 7 | 0.35 | 5% |
| Payroll state machine | HK-26 to HK-27 | 2 | 0 | 0% |
| RAG engine | HK-28 to HK-32 | 5 | 0.75 | 15% |
| Recruitment | HK-33 to HK-36 | 4 | 0 | 0% (BR-25 conflict) |
| Admin commands | HK-37 to HK-42 | 6 | 0.6 | 10% |
| Advance/payment | HK-43 | 1 | 0 | 0% |
| System/rate limits | HK-44 to HK-47 | 4 | 0 | 0% |
| **Total** | HK-01 to HK-47 | **47** | **~1.7** | **~4%** |

**Average Hidden Rule KB Coverage: ~4%**

## Priority Enrichment Targets

1. **`06_developer_system/security_rules.md`** — HK-12 (poison filter), HK-15 (prompt injection), HK-37 (dedup), HK-42 (API key hashing)
2. **`06_developer_system/automation_pipeline.md`** — HK-13 (loop detect), HK-14 (flood), HK-16 (group skip), HK-45 (backoff)
3. **`04_business_rules/ai_response_rules.md`** — HK-01 (silent skip), HK-02 (blocked), HK-03/04/05 (auto-send intents), HK-09 (draft-always), HK-10/11 (force-draft)
4. **`04_business_rules/escort_business_rules.md`** — HK-19 to HK-25 (transport/food/duty rules)
5. **`04_business_rules/recruitment_business_rules.md`** — HK-33/34/35/36 (session TTL, scoring, age, positions)
6. **`06_developer_system/role_permissions.md`** — HK-40/41/42 (RBAC hierarchy, bootstrap, API keys)
7. **`06_developer_system/rag_strategy.md`** — HK-28 to HK-32 (all RAG params and filters)
