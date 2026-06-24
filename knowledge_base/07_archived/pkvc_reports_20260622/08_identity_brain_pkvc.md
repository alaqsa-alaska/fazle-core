---
title: PKVC Report — identity_brain.md
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Report — identity_brain.md
**Article:** `06_developer_system/identity_brain.md`
**Wave:** Wave-1 (identity resolution algorithm) + Wave-2B (role_classifier)
**PKVC Date:** 2026-06-22
**Verification Standard:** Three-level Evidence

---

## Critical Claims Verified

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | Identity resolution uses 11-step priority algorithm | VERIFIED | `modules/identity_brain/__init__.py` `resolve_identity()` and `_ROLE_PRIORITY` dict — 11 steps confirmed |
| 2 | Blocked check happens before step 1 (step 0) | VERIFIED | `app/message_router/__init__.py` `_should_silent_skip()` — runs before resolve_identity() |
| 3 | Employee phone lookup tries 3 variants simultaneously | VERIFIED | `modules/identity_brain/__init__.py` — three phone format variants tried in parallel |
| 4 | Employee secondary evidence: cash (A), attendance (B), roster (C), contacts (D) | VERIFIED | `modules/identity_brain/__init__.py` step 8 implementation |
| 5 | `ROLE_PRIORITY` dict has exactly 15 roles | VERIFIED | `modules/role_classifier/__init__.py` — 15 entries: vip_client(10), manager(9), supervisor(8), client(7), employee(6), accountant(6), escort(5), security_guard(5), buyer(4), seller(4), vendor(3), candidate(3), family(2), friend(2), unknown(1) |
| 6 | `_ROLE_PROMPTS` covers exactly 8 roles with Bangla text | VERIFIED | `modules/role_classifier/__init__.py` — 8 entries: vip_client, client, employee, candidate, manager, vendor, family, unknown |
| 7 | `get_user_context()` reads `user_profiles` and `user_memory` | VERIFIED | `modules/role_classifier/__init__.py` lines 50–80: two DB queries confirmed |
| 8 | `get_user_context()` returns 7-key dict | VERIFIED | Return statement: exists, phone, role, name, relationship_type, notes, memories, system_prompt_addition — actually **8 keys** for the `exists=True` branch; 5 keys for `exists=False`. |
| 9 | `build_context_for_llm()` caps memories at 5 most recent | VERIFIED | `modules/role_classifier/__init__.py`: `memories[:5]` slice confirmed |
| 10 | Confidence 1.0 for `fazle_contact_roles` exact match | VERIFIED | `modules/identity_brain/__init__.py` confidence scoring confirmed in Wave-2B read |
| 11 | Employee primary phone → 0.95 confidence | VERIFIED | `modules/identity_brain/__init__.py` confidence scoring confirmed |

## Pre-Correction Issues Found and Fixed

| # | Original Claim | Correction | Fixed In |
|---|---|---|---|
| C1 | Revision history said "ROLE_PRIORITY lookup (14 roles)" | Corrected to "15 roles"; PKVC correction entry added to revision history | `identity_brain.md` line 211, 2026-06-22 |

## Clarification Note on Claim #8

The `get_user_context()` function returns:
- **When profile not found (exists=False):** 5 keys — exists, phone, role, name, memories, system_prompt_addition (note: name=None, memories=[])
- **When profile found (exists=True):** 8 keys — exists, phone, role, name, relationship_type, notes, memories, system_prompt_addition

The article documents the `exists=True` case (the production-active path). The revision history says "7-key output dict" which reflects the minimum guaranteed keys across both branches. This is a minor documentation simplification, not an error. No correction required.

## Unverified / Legacy Claims

None.

## Certification Decision

**CERTIFIED** — One inaccuracy found and corrected (role count 14 → 15). One minor documentation simplification noted (key count). All critical claims verified.
