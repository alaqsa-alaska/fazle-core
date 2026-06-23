# Identity Brain

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
| 6 | repeat_client | `wbom_cash_transactions` history | — |
| 7 | vendor | `fazle_contact_roles` (role=vendor) | — |
| 8 | employee | `wbom_employees` (3 phone variants) | A: cash transactions, B: attendance records, C: escort roster, D: contact DB |
| 9 | supervisor | `fazle_contact_roles` (role=supervisor) | — |
| 10 | candidate | `fazle_recruitment_sessions` (active session) | — |
| 11 | unknown | fallback (no match) | — |

**Business Rule:** The first step to match wins. Lower-step roles always take precedence over higher-step roles.

**Special Case — blocked:** Contacts with `role='blocked'` in `fazle_contact_roles` are hard-silently-skipped before step 1. No reply, no draft, no log entry.

**Employee secondary evidence (Step 8):**
When the primary phone lookup in `wbom_employees` fails, the system tries four secondary evidence sources in order:
- A: `wbom_cash_transactions` — any payment record with this phone
- B: `wbom_attendance` — any attendance record with this phone
- C: `escort_roster_entries` — any roster entry
- D: `wbom_contacts` — contact DB match

**Validation:** Phone lookup always tries three variants simultaneously: `01XXXXXXXXX`, `880XXXXXXXXX`, `+880XXXXXXXXX`

**Source Module:** `modules/identity_brain`
**Source Function:** `resolve_identity()`, `_ROLE_PRIORITY`
**PKCA Report:** 11_identity_coverage_report.md
**Management Authority:** Production evidence; documented 2026-06-22

---

## Confidence Scoring

Each resolved identity carries a confidence float (0.0–1.0):

| Evidence Type | Confidence |
|---|---|
| `fazle_contact_roles` exact match | 1.0 |
| `wbom_employees` primary phone match | 0.95 |
| Secondary evidence (cash / attendance / roster) | 0.7 |
| Contact DB match only | 0.5 |
| Fallback / no match | 0.0 (unknown) |

**Purpose:** Confidence affects routing decisions. Low-confidence identities may trigger draft-instead-of-autosend behavior.

---

## Blocked Role Behavior

**Business Rule:** Contacts with `role='blocked'` in `fazle_contact_roles` receive no reply, no draft, and no audit log entry. The message is silently discarded at step 0 before any routing occurs.

**Validation:** Blocked check runs before the 11-step algorithm and before any LLM call.

**Source Module:** `app/message_router`
**Source Function:** `_should_silent_skip()`
**PKCA Report:** 10_hidden_rule_coverage_report.md (HK-02)

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

**Visibility:** Developer (all implementation details), Admin (role resolution behavior, blocked contact behavior)
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
