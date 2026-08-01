---
title: Identity Brain
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Identity Brain
**KB Article ID:** DEV-06-IDENTITY-BRAIN
**Sources:** `modules/identity_brain/__init__.py` (393 lines — read 2026-06-23), `modules/role_classifier/__init__.py`
**Visibility:** Developer / Admin only — do NOT set `safe_for_customer=True`
**Certified:** 2026-06-23 (Wave-3, W3P2-AUTH)

---

## Purpose
Implementation-facing identity engine rules. Every inbound message must resolve to a role before any AI answer, draft creation, or backend write.

## Runtime Contract
Every message must resolve to role/sub-role before answer generation, draft creation, or backend write.

---

## Identity Resolution Algorithm

### Purpose
The identity brain resolves every contact to exactly one role using an ordered, priority-based algorithm. The first matching step wins.

### 11-Step Resolution Order

| Step | Role | Primary Source | Secondary Evidence |
|---|---|---|---|
| 1 | admin | `fazle_admins` + `fazle_contact_roles` (role=admin) | — |
| 2 | family | `fazle_contact_roles` (role=family) | — |
| 3 | accountant | `fazle_contact_roles` (role=accountant) | — |
| 4 | vip_client | `fazle_contact_roles` (role=vip_client) | — |
| 5 | client_escort_buyer | `fazle_contact_roles` (role=client_escort_buyer) | — |
| 6 | repeat_client | `fpe_cash_transactions` history | — |
| 7 | vendor | `fazle_contact_roles` (role=vendor) | — |
| 8 | employee | `wbom_employees` (3 phone variants) | A: cash transactions, B: attendance records, C: escort roster, D: contact DB |
| 9 | supervisor | `fazle_contact_roles` (role=supervisor) | — |
| 10 | candidate | `fazle_recruitment_sessions` (active session) | — |
| 11 | unknown | fallback (no match) | — |

**Business Rule:** The first step to match wins. Lower-step roles always take precedence over higher-step roles.

**Removed (Owner decision, 2026-07-11):** A "blocked" special case previously existed here (hard-silent-skip before step 1). It has been permanently removed along with all related code, routes, UI, and the short-lived `fpe_identity_anomalies` tracking table (migrations 041/042). There is no "blocked number" concept in this system — a sender is either a known role or unknown; unknown senders trigger no business action but are not tracked or specially skipped.

**Employee secondary evidence (Step 8):**
When the primary phone lookup in `wbom_employees` fails, the system tries four secondary evidence sources in order:
- A: `fpe_cash_transactions` — any payment record with this phone
- B: `wbom_attendance` — any attendance record with this phone
- C: `escort_roster_entries` — any roster entry
- D: `wbom_contacts` — contact DB match

**Validation:** Phone lookup always tries three variants simultaneously: `01XXXXXXXXX`, `880XXXXXXXXX`, `+880XXXXXXXXX`

**Source Module:** `modules/identity_brain/__init__.py`
**Source Function:** `detect_identity(phone, text="")` (393 lines — read 2026-06-23)
**PKCA Report:** 11_identity_coverage_report.md
**Management Authority:** Production evidence; documented 2026-06-22

**CORRECTION — Admin Detection:** Step 1 in the table above references `fazle_admins`. The actual code uses `app.config.get_settings()` — reads admin phone lists from environment settings (`ADMIN_NUMBERS`, bridge admin numbers). No `fazle_admins` table is queried. The `fazle_contact_roles` seed rule (Step 2) can also grant admin role.

**CORRECTION — Step 10 (Candidate):** The table above cites `fazle_recruitment_sessions`. In the actual `identity_brain` module, candidate detection runs at Step 5 via `_is_candidate_text()` using `_CANDIDATE_KEYWORDS` on the message text. There is no `fazle_recruitment_sessions` lookup in `detect_identity()`. The session table is managed by `modules/recruitment_flow` — separate from identity detection.

**CORRECTION — Step order between `client_escort_buyer` and `employee`'s "secondary evidence" (2026-08-01, real incident):** The 11-step table above predates the Phase 1B.2 escort-client-identity work (2026-07-17 onward) and does not reflect the actual current step order or a real bug found and fixed in it. As of 2026-08-01, `detect_identity()`'s real order between seed rules and `db_contact` is:

1. `settings` (admin)
2. `seed_rule` (`fazle_contact_roles`)
3. `db_employee` (`wbom_employees` phone match — a *genuine* employee record)
4. **`cash_payment` / `attendance`** (`fpe_cash_transactions` / `wbom_attendance` — incidental "operational evidence," NOT a genuine employee record) — **as of 2026-08-01, skipped entirely for any phone on the `ESCORT_CLIENT_PHONES` allowlist**, see fix below
5. `escort_roster` (active `wbom_escort_programs` duty)
6. `env_phone` (explicit `ESCORT_CLIENT_PHONES` match → `client_escort_buyer`)
7. `db_contact` (`wbom_contacts` relation / contact-name "escort" marker)
8. `text_hint` (candidate keywords, then unverified escort-content keywords)
9. `unknown`

**Real incident and fix:** a phone explicitly configured in `ESCORT_CLIENT_PHONES` received a single incidental cash payment months into an ongoing client relationship (`fpe_cash_transactions` auto-creates an employee stub for any WhatsApp-shorthand payout recipient regardless of their other roles — see `05_workflows/cash_workflow.md`'s missing-name-handling section). From that day forward `_lookup_cash_identity()` matched this phone on every message and returned role `employee` (source `cash_payment`, confidence 86) **before step 6 (`env_phone`) ever ran** — permanently overriding the explicit allowlist entry with no way to self-correct. Fixed by having steps 4's two lookups (`_lookup_cash_identity`, `_lookup_attendance_identity`) skip entirely when the phone matches `ESCORT_CLIENT_PHONES`; step 3's *genuine* `db_employee` record is intentionally still allowed to override, since that's a materially stronger signal than an incidental payment/attendance row.

A second, independent bug compounded this and would have blocked the fix above on its own: `modules.escort._escort_client_phone_list()` returns canonical `01XXXXXXXXX` form (changed from `880`-form in commit `6aca81e`, 2026-07-20, to fix that function's *other* two callers), but `identity_brain._normalize_phone()` always produces `8801XXXXXXXXX` form — so step 6's direct string-equality check could never have matched any configured client from 2026-07-20 onward, regardless of step ordering. The 2026-07-20 commit's own audit explicitly claimed identity_brain "already compares self-consistently" — that claim was wrong for this one call site. Fixed by comparing against every canonical phone variant (`_phone_variants()`) instead of raw equality.

---

## Identity Brain Module Constants

Source: `modules/identity_brain/__init__.py` — all values read directly from code 2026-06-23

### `_ROLE_PRIORITY` — Numeric Priority Weights

Used by callers to compare signals when resolving ambiguous identities. Higher value = higher authority.

```python
_ROLE_PRIORITY: dict[str, int] = {
    "admin":               200,
    "family":              100,
    "accountant":           95,
    "vip_client":           92,
    "client_escort_buyer":  90,
    "employee":             88,
    "supervisor":           80,
    "repeat_client":        75,
    "vendor":               70,
    "candidate":            30,
    "unknown":               0,
}
```

**Note:** This is the `identity_brain` module's priority table. It is NOT the same as the `role_classifier` module's `ROLE_PRIORITY` dict (which has a different scale and set of roles). The `role_classifier` dict is documented in the Role Classifier section below.

---

### `_CANDIDATE_KEYWORDS` — Text-Hint Candidate Triggers

10 Bengali/English terms that trigger candidate classification when no DB identity is found:

```python
_CANDIDATE_KEYWORDS = [
    "চাকরি",      # job (Bengali)
    "চাকরী",      # job (variant spelling)
    "কাজ চাই",    # I want work
    "job",         # English
    "apply",       # English
    "বেতন কত",    # how much is the salary
    "নিয়োগ",     # recruitment/hiring
    "recruitment", # English
    "vacancy",     # English
    "আবেদন",      # application/apply
]
```

**Trigger logic:** `_is_candidate_text(text)` returns `True` if ANY keyword is found (case-insensitive substring match). Used ONLY when no other identity evidence exists — never overrides DB match.

**Step in resolution:** Step 5 — only reached if Steps 1–4 (admin, seed, employee, contact) all produced no match.

---

### `_ESCORT_CONTENT_RE` — Escort-Buyer Content Pattern

Regex pattern that identifies vessel/escort order content from an unknown sender, assigning `repeat_client`:

```python
_ESCORT_CONTENT_RE = re.compile(
    r"\b(m\.?v\.?|mother\s*vessel|lighter|escort\s*lagbe|m\.?t\.|এমভি|"
    r"destination|lighter\s*vessel)\b",
    re.IGNORECASE,
)
```

**Matched patterns (8 tokens):** `m.v.` / `mv`, `mother vessel`, `lighter`, `escort lagbe`, `m.t.` / `mt`, `এমভি`, `destination`, `lighter vessel`

**Trigger condition:** Step 6 — reached only after Step 5 (candidate keyword check) fails. Unknown senders sending vessel/escort keywords are assigned `repeat_client` (confidence=40) rather than `candidate`.

**Overlap with message_router:** `modules/message_router/_ESCORT_CONTENT_RE` uses the same pattern for routing decisions at Step 2. The two patterns serve different purposes: identity_brain assigns a role; message_router decides which workflow to invoke.

---

## Confidence Scoring

Each resolved identity carries two confidence fields in the returned dict:
- `identity_confidence`: raw integer (0–100)
- `confidence`: float (0.0–1.0) = `identity_confidence / 100`

Actual values from code (source: `detect_identity()` call sites in `modules/identity_brain/__init__.py`):

| Resolution Path | `identity_confidence` | `confidence` float | Source |
|---|---|---|---|
| Admin via settings | 100 | 1.00 | `_check_admin_settings()` |
| Seed rule (fazle_contact_roles) | from DB `confidence` column | varies | `_lookup_seed()` |
| Employee primary phone match | 88 | 0.88 | `_lookup_employee()` |
| Employee via cash payment records | 86 | 0.86 | `_lookup_cash_identity()` |
| Employee via attendance records | 86 | 0.86 | `_lookup_attendance_identity()` |
| Employee via escort roster | 85 | 0.85 | `_lookup_escort_roster()` |
| Contact DB match | 80 | 0.80 | `_lookup_contact()` |
| Text-hint candidate (keyword match) | 50 | 0.50 | `_is_candidate_text()` |
| Text-hint escort (vessel content) | 40 | 0.40 | `_ESCORT_CONTENT_RE` |
| Unknown (no match) | 0 | 0.00 | `_unknown()` |

**CORRECTION:** Prior versions of this article stated confidence values of 1.0/0.95/0.7/0.5. These were incorrect. The table above reflects actual integer constants in the code.

**Purpose:** Confidence affects routing decisions. Low-confidence identities (≤ 50) may trigger draft-instead-of-autosend behavior when `AI_SAFE_MODE=true` (identity confidence < 50 → forced draft in `bridge_poller`; see `runtime_gateway_flags.md`).

---

## Blocked Role Behavior — REMOVED (Owner decision, 2026-07-11)

This section previously documented a `role='blocked'` hard-silent-skip step. The "blocked number" concept has been permanently removed from this system's business logic — there is no scenario in which a phone number should be blocked at the pipeline level. A sender is either a known role or unknown; unknown senders simply do not trigger business actions. See migration 042 for the corresponding DB cleanup and `modules/message_router/__init__.py::_should_silent_skip()` for the current (accountant-phone-routing + draft-always-name-match only) behavior.

---

## Backend Requirements
- Normalize phone numbers.
- Match seeded contacts, employee records, payroll records, escort roster, contact book, and conversation memory.
- Allow admin frontend to add/edit/delete/deactivate/merge roles.
- Audit every role mutation.
- Reload role cache after mutation.

## 8 Evidence Sources

| Source | Table | Used For |
|---|---|---|
| Contact roles | `fazle_contact_roles` | Steps 1–5, 7, 9 |
| Admin registry | `fazle_admins` | Step 1 (admin) |
| Employee registry | `wbom_employees` | Step 8 (primary) |
| Cash transactions | `wbom_cash_transactions` | Step 8 (secondary A) |
| Attendance records | `wbom_attendance` | Step 8 (secondary B) |
| Escort roster | `escort_roster_entries` | Step 8 (secondary C) |
| Contact book | `wbom_contacts` | Step 8 (secondary D) |
| Recruitment sessions | `fazle_recruitment_sessions` | Step 10 (candidate) |

---

## Role Classifier — Bangla Prompt Injection

**Visibility:** Developer
**Source Module:** `modules/role_classifier/__init__.py`
**Purpose:** After the identity_brain resolves a contact's role, the role_classifier enriches the LLM system prompt with role-specific context and Bangla tone instructions. It also loads memory facts from `user_profiles` and `user_memory` for personalized replies.

**Relationship to identity_brain:**
- `identity_brain.resolve_identity()` → answers **who is this person?** (resolves role from DB tables)
- `role_classifier.get_user_context()` → answers **how should the AI talk to them?** (loads Bangla tone + memories)
- Both are consumed by `message_router` before LLM call

### Role Priority Lookup (for tie-breaking when multiple role signals exist)

| Role | Priority Score |
|---|---|
| vip_client | 10 |
| manager | 9 |
| supervisor | 8 |
| client | 7 |
| employee | 6 |
| accountant | 6 |
| escort | 5 |
| security_guard | 5 |
| buyer | 4 |
| seller | 4 |
| vendor | 3 |
| candidate | 3 |
| family | 2 |
| friend | 2 |
| unknown | 1 |

**Source Constant:** `ROLE_PRIORITY` dict in `modules/role_classifier/__init__.py`
**Usage:** Higher score = higher priority when resolving ambiguous role signals from `user_profiles`.

### Per-Role Bangla Prompt Injection (_ROLE_PROMPTS)

Each resolved role injects a Bangla language-style directive into the LLM system prompt before reply generation:

| Role | Bangla Prompt Injected (Translation) |
|---|---|
| `vip_client` | "এই ব্যক্তি একজন VIP ক্লায়েন্ট। অত্যন্ত সম্মানজনক ও সহায়ক ভাষায় কথা বলো।" — *VIP client. Speak with utmost respect and helpfulness.* |
| `client` | "এই ব্যক্তি একজন ক্লায়েন্ট। পেশাদার ও সহায়ক ভাষায় কথা বলো।" — *Client. Speak professionally and helpfully.* |
| `employee` | "এই ব্যক্তি একজন কর্মচারী। অফিসিয়াল ও সহায়ক ভাষায় কথা বলো।" — *Employee. Speak officially and helpfully.* |
| `candidate` | "এই ব্যক্তি একজন চাকরিপ্রার্থী। উৎসাহব্যঞ্জক ও তথ্যপূর্ণ ভাষায় কথা বলো।" — *Job candidate. Speak encouragingly and informatively.* |
| `manager` | "এই ব্যক্তি একজন ম্যানেজার। সংক্ষিপ্ত ও সরাসরি উত্তর দাও।" — *Manager. Give concise, direct answers.* |
| `vendor` | "এই ব্যক্তি একজন ভেন্ডর। ব্যবসায়িক ভাষায় কথা বলো।" — *Vendor. Speak in business language.* |
| `family` | "এই ব্যক্তি পরিবারের সদস্য। ব্যক্তিগত ও উষ্ণ ভাষায় কথা বলো।" — *Family member. Speak personally and warmly.* |
| `unknown` | "এই ব্যক্তি অপরিচিত। সংক্ষিপ্ত পরিচয় নিয়ে সহায়তার প্রস্তাব দাও।" — *Unknown. Briefly introduce and offer assistance.* |

**Source Constant:** `_ROLE_PROMPTS` in `modules/role_classifier/__init__.py`
**Default:** If role has no mapped prompt, `_DEFAULT_PROMPT` (unknown prompt) is used.
**Business Rule:** These prompts are injected into the LLM system context and are NEVER shown to the contact. They control tone and language style only.

### get_user_context() Function

**Source Function:** `get_user_context(phone_canonical)` in `modules/role_classifier/__init__.py`
**Input:** Canonical phone number (normalized to `880XXXXXXXXX` format)
**Output:** Dict with keys:

| Key | Type | Description |
|---|---|---|
| `exists` | bool | True if profile found in `user_profiles` |
| `phone` | str | Canonical phone number |
| `role` | str | Role string from `user_profiles` (or `unknown` if no profile) |
| `name` | Optional[str] | Display name from `user_profiles` |
| `relationship_type` | Optional[str] | Relationship type from `user_profiles` |
| `notes` | Optional[str] | Admin notes from `user_profiles` |
| `memories` | list[dict] | Last 10 memory facts from `user_memory` (type + content) |
| `system_prompt_addition` | str | Bangla role prompt to inject into LLM system context |

**DB Tables Read:** `user_profiles` (AI domain), `user_memory` (AI domain)

### build_context_for_llm() Function

**Source Function:** `build_context_for_llm(user_context, kb_context)` in `modules/role_classifier/__init__.py`
**Purpose:** Merges user_context dict and RAG knowledge base context into a single string for LLM injection.

**Assembled context format (in order):**
1. `ব্যক্তির নাম: {name}` — if name is known
2. `Role: {role}` — always included
3. `সম্পর্ক: {relationship_type}` — if set
4. `নোট: {notes}` — if admin notes exist
5. `পূর্বের তথ্য:` — up to 5 memory facts, formatted as `- [{type}] {content}`
6. `Knowledge Base:` + RAG context — if RAG answer was found

**Business Rule:** The assembled context is injected as a system-level prefix to the LLM prompt. It is never shown to the contact. Memory facts are capped at 5 (most recent) to stay within LLM context limits.

---

## Visibility and AI Exposure Rules

**Visibility:** Developer (all implementation details), Admin (role resolution behavior)
**AI Exposure:** The role resolution algorithm, priority steps, confidence scores, Bangla prompt injection text, memory facts stored in `user_memory`, and the `role_classifier` module internals must NEVER be disclosed to candidates, employees, or clients. A contact should never learn they are being classified before their message is answered.

---

## Cross References

- `03_ai_identity/identity_overview.md`
- `06_developer_system/database_rules.md` — Domain 9 (AI): `user_profiles`, `user_memory`, `role_reply_styles`
- `06_developer_system/automation_pipeline.md` — memory_extractor that populates `user_memory`
- `role_permissions.md`

---

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-06-22 | Wave-2B: Added role_classifier module documentation — ROLE_PRIORITY lookup (15 roles), _ROLE_PROMPTS (8 roles, Bangla prompt injection), get_user_context() function (7-key output dict), build_context_for_llm() function, visibility rules, cross references. Previous article was 103 lines (identity_brain resolution algorithm only). | KSP Wave-2B |
| 2026-06-22 | PKVC correction: "14 roles" corrected to "15 roles" in revision history. Production `ROLE_PRIORITY` dict has 15 entries. | KSP PKVC |
| 2026-06-23 | Wave-3 Phase 2 (W3P2-AUTH): Added Identity Brain Module Constants section — `_ROLE_PRIORITY` numeric table (11 entries), `_CANDIDATE_KEYWORDS` exact list (10 terms), `_ESCORT_CONTENT_RE` description; corrected confidence score table (actual code values); corrected admin detection mechanism (settings-based, not fazle_admins); corrected candidate step number (Step 5 text_hint, not Step 10 recruitment_sessions). Source: `modules/identity_brain/__init__.py` read directly. | Wave-3 Agent |
