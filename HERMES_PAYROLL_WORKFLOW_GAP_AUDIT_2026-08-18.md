---
title: Fazle-Core Feature, Code–KB Gap & Hermes Capability Audit — Employee Payment Workflow
status: investigation only — READ-ONLY. No code, data, flags, or services touched.
date: 2026-08-18
scope: fazle-core (/home/azim/core), hermes-runner, fazle-mcp, assistant-platform
builds on: HERMES_BUSINESS_CAPABILITY_MODULE_AUDIT_2026-08-17.md, KNOWLEDGE_EXTRACTION_AUDIT_PHASE1_2026-08-17.md,
           HERMES_PAYROLL_RECONCILIATION_PROPOSAL_2026-08-18.md, HERMES_PAYROLL_PHASE1-4_FINALIZATION_2026-08-18.md
---

# Fazle-Core Feature, Code–KB Gap & Hermes Capability Audit — 2026-08-18

**No production code, data, feature flags, or services were touched in producing this report.**
All example figures in the brief (phone numbers, IDs) were ignored per instruction; nothing below
was searched for or matched against them. This report **builds on**, rather than repeats, three
same-week audits already in this repo (linked above) which independently covered the module
inventory, KB fragmentation, and payroll-calculation-formula questions in depth — those findings
are cited, not re-derived. This report's own primary-source verification work covers the ground
those three did **not**: the payment message parser/validator grammar, the employee
match-or-create logic, the request→draft→approval→transaction chain end-to-end, the unmatched-
message review queue, and — the single most important new finding — **which of these Hermes can
actually reach through a registered tool today.**

---

## 1. Executive Summary

Fazle-core's payment pipeline is **two genuinely separate, correctly-separated systems**, exactly
as the KB (`knowledge_base/05_workflows/cash_workflow.md`) describes and as code confirms:

1. **Employee request → draft** (`modules/payment_workflow.py`, `fazle_payment_drafts` table) —
   never writes a final ledger row by itself.
2. **Admin decision → accountant instruction → parse → canonical transaction → ledger**
   (`modules/admin_commands` → either `payment_workflow.finalize_payment()` **or**
   `modules/draft_approval.approve_draft()` → both converge on the one real write path,
   `modules/fazle_payroll_engine/accounting.py::create_transaction()`).

Verified against code line-by-line: the parser (`ID:` name-vs-phone disambiguation, payment-method
codes, amount-suffix grammar), the validator (per-message-type required fields), the employee
matcher (phone-first, fuzzy-name-second, phone-anchored auto-create, ambiguity routed to review),
and the idempotency key (`txn_ref` from message identity, not wall-clock time) **all match what
the KB document claims**, with one small terminology imprecision (§8). This is a well-built,
already-hardened pipeline with a real incident history (the 2026-08-01 `ID:`-name bug) and real
fixes on record.

**The central, new finding of this audit**: none of this is reachable by Hermes today for the
*write* half of the workflow. Hermes has read tools for cash transactions, employees, and drafts,
and can *draft* a WhatsApp reply — but **no registered `fazle-mcp` tool calls
`payment_workflow.finalize_payment()`, `draft_approval.approve_draft()/edit_draft()/reject_draft()`,
or the unmatched-message review-queue endpoints.** Hermes's only tool literally named
`approve_draft` calls a *different* table (`fazle_draft_replies`, generic WhatsApp reply drafts,
via `POST /api/drafts/{id}/approve`) — confirmed by reading both the tool's implementation and the
route it calls. **If Hermes is asked today to "approve Karim's advance draft," there is no tool
that can do that safely; the only technically-possible path would be misusing
`send_whatsapp_message` to emit literal `PAID <id> <amount> <method>` text — which is not what
that tool is documented or scoped for, and would be indistinguishable from Hermes hallucinating an
admin command.**

**Answer to §27 (repeated in full at the end): PARTIALLY.** Hermes can safely execute the read/
identify/draft portion of the workflow; it cannot safely execute the approve→instruct→transact
portion at all, because the tool does not exist yet, not because of an authority/safety gap in an
existing tool.

---

## 2. Current Architecture Map

```
WhatsApp/Meta ─▶ bridge (webhook/poller) ─▶ modules/message_router (1417 lines, 9-tier routing)
                                                 │
                        ┌────────────────────────┼───────────────────────────────┐
                        ▼                         ▼                               ▼
              modules/identity_brain      modules/payment_workflow        modules/admin_commands
              (726 lines, 7-tier trust-   (employee-request → draft;      (WhatsApp admin command
              tiered resolution)          fazle_payment_drafts)           grammar: PAID/ADVANCE/
                        │                         │                       APPROVE/EDIT/REJECT/DREJECT)
                        ▼                         ▼                               │
              app.llm / hermes_dispatch   admin decides (approve/edit/reject)      │
              (customer-facing reply)             │         ┌──────────────────────┘
                                                   ▼         ▼
                                    payment_workflow          modules/draft_approval
                                    .finalize_payment()        .approve_draft()/edit_draft()
                                    (PAID/ADVANCE cmds)        (APPROVE/EDIT/DREJECT cmds — Sprint-3B)
                                                   │         │
                                                   └────┬────┘
                                                        ▼
                                    modules/fazle_payroll_engine/accounting.py
                                    ::create_transaction()  →  fpe_cash_transactions
                                                        │           │
                                                        ▼           ▼
                                              _upsert_ledger()   fpe_accounting_audit_logs
                                              → fpe_employee_ledger

Independent path — admin→accountant WhatsApp instruction (no draft involved):
Admin → Accountant (WhatsApp) → modules/fazle_payroll_engine/{ingestion,workers}.py
  → parser.py::parse_message() → validation.py::validate_for_accounting()
  → employee.py::match_or_create_employee() → accounting.py::create_transaction()
  (same canonical write function as above)
  → on validation/match failure → fpe_unmatched_messages (review queue) + admin WhatsApp push
```

Four codebases, per the parent audit's §2 (unchanged, re-confirmed): **fazle-core**
(`/home/azim/core`, system of record), **hermes-runner** (`/home/azim/hermes-runner`, mode-gated
dispatcher), **fazle-mcp** (`/home/azim/fazle-mcp`, Hermes's registered tool surface, 46 `@mcp.tool()`
functions confirmed by direct count this session, up from "40+" in the 08-17 audit — see §13),
**assistant-platform** (web UI / admin console, not re-audited here).

---

## 3–4. Feature Inventory & Code Location Map

### A. Employee identity

| Capability | Module / function | DB table(s) | Notes |
|---|---|---|---|
| Mobile normalization | `modules/fazle_payroll_engine/normalizer.py::normalize_bd_phone()` | — | Canonical `01XXXXXXXXX` form per [[feedback_phone_format_canonical]]; also `PHONE_NORMALIZER_CONVENTION.md` at repo root |
| Employee lookup (payment context) | `modules/fazle_payroll_engine/employee.py::match_or_create_employee()` | `fpe_employees`, `fpe_employee_aliases`, `wbom_employees` (cross-system bridge) | 6-tier priority, verified §11 below |
| Identity resolution (conversation context) | `modules/identity_brain/__init__.py::detect_identity()` | `fazle_contacts`, `wbom_employees`, `fazle_contact_roles`, seed data | 7-tier trust-ordered (admin→family→accountant→vip_client→escort_buyer→repeat_client→vendor→employee→supervisor→candidate→unknown), confirmed in parent audit §5.5 |
| Duplicate employee detection | `employee.py` fuzzy match (rapidfuzz, threshold 96.0, ambiguity margin 5.0) + `enqueue_review()` in `normalization.py` | `fpe_employees.canonical_employee_id` soft-link | Verified this session — see §11 |
| Employee creation | `employee.py::_auto_create_employee()` (automatic, advisory-lock-guarded) and `::create_employee_manual()` (admin-UI-triggered) | `fpe_employees` | **No approval gate on the automatic path** — see §11/§17 finding |
| Employee merge | `canonical_employee_id` soft-link, resolved transparently by `_resolve_canonical()` | `fpe_employees` | Never deletes/mutates the duplicate row |
| Employee status (active/leave/resigned/released) | `identity_brain/__init__.py::_lookup_lifecycle()` | `wbom_employees.status`, `wbom_attendance` | Read-only heuristic: `status != 'active'` → `"former"`; else recent-attendance-gap → `"on_leave"`/`"active"`. **Fails open to `"active"` on DB error** — explicitly documented as "context, not a gate" |
| Conversation identity vs. WhatsApp identity | `identity_brain` treats phone as the join key throughout; no separate "conversation identity" abstraction found | `fazle_contacts.phone` | — |

### B. Employee conversation trace

```
Inbound WhatsApp
  → modules/message_router (9-tier priority: family→escort→admin→supervisor→
    accountant→candidate→employee→known contacts→unknown)
  → modules/identity_brain.detect_identity() (who/role/trust-tier)
  → intent detection: keyword-based per module (e.g. payment_workflow.is_advance_request(),
    recruitment_flow's own detectors) — NOT a centralized intent classifier for this path
    (app.llm.classify_intent_llm() exists but is used for the admin-console/social path,
    per PRE_RESTART_SNAPSHOT_HERMES_ONLY_ARCHITECTURE_2026-08-17.md, not this one)
  → business-rule evaluation inline in the relevant module (payment_workflow, escort_lifecycle,
    recruitment_flow, payroll_logic)
  → response generation: rule-based template (reply_rules.py) as the guaranteed-safe floor,
    Hermes/app.llm layered on top per feature flag, both gated through
    intelligent_generator.py::_safe_polish() before send (parent audit §5.3 — re-confirmed
    unchanged this session, not re-verified live)
  → draft/approval/auto-send decision: modules/draft_approval + modules/drafts (generic reply
    drafts) for anything not auto-sendable
  → outbound: modules/outbound (queue/retry/DLQ/circuit breaker)
```

### C. Payment/advance request workflow (§4 of the brief)

| Question | Answer | Evidence |
|---|---|---|
| 1. Identity established how? | `payment_workflow` functions take an already-resolved `employee_id` (`wbom_employees.employee_id`) as a parameter — identity resolution happens *upstream*, in `message_router`/`identity_brain`, before these functions are called | `payment_workflow/__init__.py:46,253` signatures |
| 2. Status checked how? | **Not checked inside `payment_workflow.py` itself** — `create_escort_payment_draft()`/`create_advance_request_draft()` query `wbom_employees` by ID with no `status='active'` filter | Read in full this session — confirmed absence |
| 3. Duty context checked how? | For escort payment: yes, via `escort_program_id` → `wbom_escort_programs` lookup, duty-days computed from real timestamps or a date-delta fallback | `payment_workflow/__init__.py:73-98` |
| 4. Amount extraction | Escort: computed (`duty_days × ৳400/day`, formula now known stale per the same-day payroll reconciliation proposal — see §7). Advance: caller-supplied, no computation | `payment_workflow/__init__.py:41-143`, `HERMES_PAYROLL_RECONCILIATION_PROPOSAL_2026-08-18.md` |
| 5. Payment method | Not decided at draft time; admin specifies it at approval (`PAID <id> <amount> <method>`) | `payment_workflow/__init__.py:348` |
| 6. Eligibility | Escort: only that duty's food/conveyance/advance figures netted in; no independent "is this employee eligible for an advance at all" check found | — |
| 7. Draft created? | **Yes, always** — `fazle_payment_drafts` row, `status='pending'` | §C, above |
| 8. Draft table | `fazle_payment_drafts` | confirmed |
| 9. Can system message the employee? | Not from `payment_workflow.py` itself — draft text is built for the **admin**, not the employee; employee-facing messaging is a `message_router`/`outbound` concern upstream | — |
| 10. Admin approval required? | **Yes, always** — no code path in `payment_workflow.py` writes `fpe_cash_transactions` without an admin command first | confirmed |
| 11. Approve/Edit/Reject representation | Two parallel WhatsApp command grammars, **both converging on the same canonical write** — see §9 | `admin_commands/__init__.py`, `draft_approval/__init__.py` |
| 12. After approval | `finalize_payment()`/`approve_draft()` → `create_transaction()` → ledger upsert → accountant-facing message text generated (notification only, never re-parsed) | `accounting.py`, `draft_approval.py:307-311`'s own "Constitutional Rule" comment |
| 13. After rejection | `status='rejected'`, no transaction, no ledger (`draft_approval::reject_draft()`) / `DREJECT` in `admin_commands` | confirmed by function name/docstring; not read line-by-line this session |
| 14. After edit | Version increment, before/after state saved (`fazle_draft_audit_log`), then re-approve | `draft_approval/__init__.py` header docstring, `EVT_DRAFT_EDITED` |
| 15. Auditable? | Yes — `fpe_accounting_audit_logs` (every transaction create/reverse) + `fazle_draft_audit_log` (every draft lifecycle event) | `accounting.py:98-107`, `draft_approval.py:157-181` |

**Request → proposal/draft → approval → execution is correctly kept as four distinct stages in
code**, not conflated — this matches both the KB doc and the module's own docstring ("This path
never writes a final cash transaction").

### D. Admin → Accountant instruction path (§5 of the brief)

| Stage | Module |
|---|---|
| Message generation | `draft_approval::build_accountant_message()` (draft path) — formats to be parser-compatible; OR the admin's own free-text WhatsApp message (independent instruction path, no draft) |
| Message transport | Standard WhatsApp bridge (`modules/outbound`, `modules/bridge_poller`) |
| Accountant identity/role | RBAC role check upstream in `message_router`/`admin_commands` (role hierarchy in `fazle_roles` per `.claude/CLAUDE.md`) — not re-traced line-by-line this session |
| Parser | `modules/fazle_payroll_engine/parser.py::parse_message()` — verified §6 below |
| Canonical transaction creation | `accounting.py::create_transaction()` |
| Transaction validation | `validation.py::validate_for_accounting()` — verified §6 |
| Duplicate prevention | `accounting.py::_build_txn_ref()` — verified §8 |
| Unmatched-message handling | `modules/fazle_payroll_engine/ingestion.py::store_unmatched()` → `fpe_unmatched_messages` + `_notify_admin_unmatched()` (since 2026-08-01) |
| Review queue | `modules/fazle_payroll_engine/routes.py`: `GET /unmatched` (viewer+), `POST /unmatched/{id}/mark-reviewed` (accountant+) — confirmed this session |
| Admin notification | `_notify_admin_unmatched()`, purpose tag `fpe-unmatched-review`, pushes to every `ADMIN_NUMBERS` entry |
| Ledger update | `accounting.py::_upsert_ledger()` |
| Audit logging | `fpe_accounting_audit_logs` |

---

## 5–6. KB Location Map & Code↔KB Matrix

The parent audits already produced a thorough treatment of the general knowledge-fragmentation
question (office facts, recruitment scripts — Category B/F findings, see
`HERMES_BUSINESS_CAPABILITY_MODULE_AUDIT_2026-08-17.md` §5–7,
`KNOWLEDGE_EXTRACTION_AUDIT_PHASE1_2026-08-17.md`). This session's own verification, scoped to the
payment pipeline specifically:

| Feature / Rule | Code Location | KB Location | Implemented? | Documented? | Match? | Gap | Risk | Class |
|---|---|---|---|---|---|---|---|---|
| `ID:` name-vs-phone disambiguation | `parser.py::_extract_name()`, lines 473-526 | `cash_workflow.md` §"Parsing rules", incident note | Yes | Yes | **✅ Match**, verified line-by-line | none | — | A |
| Payment-method codes (B/N/C/R/BANK + Bengali + bare word) | `parser.py::_RE_METHOD`, `_RE_METHOD_BARE` | `cash_workflow.md` | Yes | Yes | ✅ Match | none | — | A |
| Amount grammar (`/-`, `/`, `/+`, bare `taka`/`tk`/`টাকা`) | `parser.py::_RE_AMOUNT` | `cash_workflow.md` | Yes | Yes | ✅ Match | none | — | A |
| Missing-name → review queue (not fabrication) | `validation.py` `payment` rule requires `employee_name_raw` | `cash_workflow.md` §"Missing-Name Handling" | Yes | Yes | ✅ Match | none | — | A |
| Non-transaction summary detection | `parser.py::_is_balance_summary()`, `_parse_balance_summary()` | `cash_workflow.md` §"Non-Transaction Summary Rule" | Yes | Yes | ✅ Match | none | — | A |
| Duplicate rule | `accounting.py::_build_txn_ref()` | `cash_workflow.md` §"Duplicate Rule": *"WhatsApp message identity/timestamp is the duplicate boundary"* | Yes | Yes | **⚠️ Partial** — code keys on `source_message_id`/`fpe_wa_message_id` + amount + period + method + source; **not** wall-clock timestamp. Two genuinely distinct messages produce distinct refs regardless of timestamp; the KB's own wording ("identity/timestamp") is imprecise, not wrong in effect | KB doc's wording could be tightened to "message identity" only | Low (cosmetic doc precision, not a behavior gap) | D — minor doc drift |
| Auto-create-on-lookup-failure | `employee.py::_auto_create_employee()` | `cash_workflow.md`: *"Missing employee is auto-created using existing wbom_employees columns only"* | Yes | Yes | ✅ Match on mechanism | **KB doc does not mention there is no approval gate on this** — see §11 finding | **Medium** — a wrong/typo'd phone number in a real accountant message silently becomes a new permanent employee record, no review | C-adjacent: implemented, under-documented risk |
| Employee-request draft never becomes a final transaction by itself | `payment_workflow.py` (no `create_transaction()` call outside `finalize_payment()`) | `cash_workflow.md` §"Source Of Truth" | Yes | Yes | ✅ Match | none | — | A |
| Unmatched-message admin push (since 2026-08-01) | `ingestion.py::store_unmatched()`/`_notify_admin_unmatched()` | `cash_workflow.md` §"Missing-Name Handling And The Review Queue" | Yes | Yes | ✅ Match | none | — | A |
| Two admin command grammars converge on one write | `admin_commands.py` (`PAID`/`ADVANCE` → `finalize_payment()`) + `draft_approval.py` (`APPROVE`/`EDIT`/`DREJECT` → `create_canonical_transaction()`, confirmed this session to call the *same* `create_transaction()`, not a parallel implementation) | **Not documented anywhere found** — neither `cash_workflow.md` nor `payment_workflow.md` mentions two coexisting command grammars | Yes (both) | **No** | N/A | A human/Hermes reading only `cash_workflow.md` would not know `APPROVE <id>` exists alongside `PAID <id>` | Low-medium — functionally safe (same sink), but a documentation gap that could confuse an operator or an LLM reasoning about "the" approval command | **F — fragmented, undocumented** |
| **Hermes `approve_draft` tool ↔ payment-draft approval** | `fazle-mcp/send_whatsapp_tools.py::approve_draft()` calls `POST /api/drafts/{id}/approve` → `modules/drafts/routes.py`, which operates on `fazle_draft_replies` (generic WhatsApp reply drafts), **not** `fazle_payment_drafts` | Nothing in the KB claims otherwise (correctly silent) | Tool exists but for a different resource | N/A | **N/A — this is a capability gap, not a drift** | **Hermes has no way to approve/reject/edit a payment draft or finalize a payment at all** | **P0/P1 — see §13/§17** | **E — capability missing for Hermes specifically; the human-operated capability is fine** |

---

## 7. Canonical Source-of-Truth Analysis

For the payment pipeline specifically, the authority hierarchy is unambiguous and not in conflict:

- **Business authority**: `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md` (per
  `.claude/CLAUDE.md`'s own instruction: "business rules that override anything else if they
  conflict") — not re-read line-by-line this session; no conflict was found between it and
  `cash_workflow.md` in the areas this audit touched.
- **Operational/parsing detail**: `knowledge_base/05_workflows/cash_workflow.md` — verified
  accurate against code this session (§6 table).
- **Technical implementation authority**: the code itself (`parser.py`, `validation.py`,
  `employee.py`, `accounting.py`) — is the actual behavior; KB doc tracks it well.
- **Draft/approval governance**: `draft_approval.py`'s own header comment cites "Business
  Constitution §1.3" as the source for "the Draft is the Source of Truth" rule — this reference
  was not independently checked against `BUSINESS-CONSTITUTION.md`'s actual §1.3 this session
  (**NOT VERIFIED** — flagged as a quick follow-up, low risk given the code's behavior already
  matches the *stated* rule regardless of the citation's exact accuracy).

No contradiction found between `BUSINESS-CONSTITUTION.md` / `SYSTEM_ATLAS.md` / sprint reports /
this year's Hermes proposals / actual code / tests for the payment-parsing and draft-approval
rules specifically. The one same-week Sprint-3B document that describes this exact area
(`draft_approval.py`'s own header, effectively a spec-as-comment) matches production code because
it *is* the production code's own design doc, checked in alongside it — the lowest-drift-risk
documentation shape possible, better than a separate `.md` file in this respect.

---

## 8. Documentation Drift Findings

1. **Confirmed, minor**: `cash_workflow.md`'s "WhatsApp message identity/**timestamp**" phrasing
   overstates what the code actually keys on (message identity only) — see §6 table. Fix: reword
   to "WhatsApp message identity is the duplicate boundary; two distinct messages for the same
   employee/amount/day both post."
2. **Confirmed, real gap**: no KB document mentions the `APPROVE`/`EDIT`/`DREJECT` (Sprint-3B)
   command grammar exists alongside the older `PAID`/`ADVANCE` grammar — an operator or an LLM
   reading only `cash_workflow.md`/`payment_workflow.md` would not learn `APPROVE <id>` is valid.
   Both are live (confirmed via `admin_commands/__init__.py` dispatch table, §9).
3. **Carried forward from the 08-17 audits** (not re-verified this session, cited for
   completeness): the office-hours 5-way duplication with one stale dead-module copy; the
   `reply_rules.py` recruitment-script library missing from KB entirely; the
   `_static_prompt_facts_text()` role-scope drift against `salary_business_rules.md`.
4. **New from the 2026-08-18 payroll proposal** (already self-documented, not a new find here):
   the *old* per-diem PAY-01 formula (৳400/day flat) that `payment_workflow.py` still runs live is
   now confirmed, by the Owner's own same-day worked examples, to under-pay Escort staff by
   roughly half versus the finalized package policy — this is a **known, self-flagged, not-yet-
   deployed drift** (`unified_calculation.py` exists, flag OFF, per
   `HERMES_PAYROLL_PHASE1-4_FINALIZATION_2026-08-18.md`) — not a new finding, cited here because it
   directly affects §C's "amount extraction" answer above: **the amount `payment_workflow.py`
   computes today is not the amount the finalized policy says is owed.**

---

## 9–10. Detailed Workflow Traces

Already given in full in §3–4 above (C and D). Repeated summary of the one structural nuance worth
calling out explicitly: **`finalize_payment()` and `draft_approval.approve_draft()` are not
duplicate implementations of the same thing** — they are two different WhatsApp command surfaces
(`PAID`/`ADVANCE` vs. `APPROVE`/`EDIT`/`DREJECT`) that both terminate in the one real write
function, `create_transaction()`. This was worth verifying directly rather than assuming from
naming alone, since a genuine duplicate-write-path bug of this shape was exactly what the
2026-08-17 audit found one layer over (the `ai_readonly_tools.py`/`fazleBridge.js` duplicate SQL
bug) — here, the pattern turned out to be safe (single sink), not a repeat of that bug.

---

## 11. Employee Identity/Creation/Merge Analysis

Verified directly against `modules/fazle_payroll_engine/employee.py::match_or_create_employee()`
(this session):

1. **Match priority** (all confirmed in code): exact `employee_id_phone` → exact `primary_phone` →
   alias phone → WBOM cross-system phone bridge → exact normalized name → fuzzy name
   (rapidfuzz ≥ 96.0, ambiguity margin 5.0) → auto-create.
2. **Ambiguity handling**: a fuzzy match that's both above threshold *and* too close to the
   second-best candidate is treated as ambiguous and **not** auto-linked; the code proceeds to
   auto-create instead and simultaneously enqueues a review row
   (`normalization.py::enqueue_review()`) flagging the near-miss — a real "create tentatively, flag
   for human reconciliation" pattern, not a silent guess.
3. **Phantom-record guard**: when there is *no phone evidence at all* (no `employee_id_phone`, no
   `payout_phone`) and the name match is weak/absent, `match_or_create_employee()` returns `None`
   rather than creating a record — the caller (`workers.py`, via `validation.py`'s required-fields
   check) routes this to the unmatched-message review queue. **This guard is real and correctly
   implemented** — it directly satisfies the brief's §10 guardrail ("must not... invent an employee
   name or silently create an unsafe final transaction") for the name-only case.
4. **The gap**: the guard above only fires when there is *no phone at all*. **When there is phone
   evidence (even a single typo'd or wrong digit that happens to still look phone-shaped) but no
   name match, `_auto_create_employee()` runs immediately, with no human approval step and no
   confidence threshold on the phone itself.** The module's own docstring states this plainly:
   *"Auto-create policy: Creates employee immediately — no human approval gate."* This is a
   deliberate design choice, not an oversight (concurrency-safe via advisory lock, links to an
   existing WBOM record if the phone matches one, never fabricates a WBOM stub) — but it means a
   single mistyped phone number in a real accountant WhatsApp message can create a **permanent**
   new employee record with no review step, distinct from the (correctly guarded) name-only case.
   This is the brief's §14 question answered directly: **yes, the system can auto-create an
   employee on lookup failure, without administrator approval, whenever any phone number is
   present** — this is real behavior, not a hypothetical.
5. **Merge**: `canonical_employee_id` soft-link, resolved transparently at read time
   (`_resolve_canonical()`, bounded 8-hop loop guard against cycles); the duplicate row is never
   deleted or mutated. No code path found that *automates* merge-decision-making — merging appears
   to be a human/admin action that sets the soft-link (not traced to its own endpoint this session,
   **NOT VERIFIED** where that write happens).

---

## 12. Employee Status/Conversation Analysis

`identity_brain::_lookup_lifecycle()` (verified this session): a **read-only heuristic**, not a
gate. `status != 'active'` → `"former"`; `'active'` with no recent attendance in `on_leave_days`
(default 14) → `"on_leave"`; otherwise `"active"`. **Explicitly fails open to `"active"` on any DB
error**, with an in-code comment: *"this is context, not a gate."* This directly answers the
brief's §15 distinction: the code treats "inactive/former/on-leave" as **operational context for
downstream logic to weigh, not as "do not communicate."** No code path found in `identity_brain`
or `message_router` that blocks a former/on-leave employee from being identified, verified, or
replied to — consistent with the KB's implied policy (a former employee asking about historical pay
should still be identifiable). Whether downstream modules (e.g. `payment_workflow`) *correctly use*
this lifecycle signal to block a former employee from getting a *new* payment draft is a separate
question **not confirmed this session** — §11's Q2 finding above already established
`create_escort_payment_draft()`/`create_advance_request_draft()` query `wbom_employees` with no
status filter at all, meaning **a resigned/terminated employee's ID could still produce a payment
draft** if some upstream caller passed one in. This is worth a dedicated follow-up trace of every
caller of these two functions to confirm whether a status check happens one layer up
(**NOT VERIFIED — flagged, not confirmed either way**).

---

## 13. Hermes Tool Capability Matrix

46 `@mcp.tool()` functions confirmed this session (`grep -c "@mcp.tool()"` across `fazle-mcp/*.py`),
up from "40+" counted in the 2026-08-17 audit. Full read-tool list is unchanged from that audit's
§13 (cited, not re-verified line-by-line here). This session's focus — the write/action tools
relevant to this specific workflow:

| Operation | Existing Hermes Tool | R/W | Authority Required | Present? | Safe (if present)? | Missing Guard? |
|---|---|---|---|---|---|---|
| Employee lookup | `get_employees(status)` | R | any mode with fazle-core tools | ✅ | Yes | — |
| Employee identity verification (trust-tiered) | *none* — `get_contacts` is a plain list, not `identity_brain.detect_identity()` | R | — | ❌ | N/A | Confirmed gap, carried from 08-17 audit §5.5/§13, unchanged |
| Employee status lookup | `get_employees(status="active"/"inactive")` only — no lifecycle/on-leave granularity exposed | R | any | Partial | Yes | `_lookup_lifecycle()`'s on_leave/former distinction is not exposed as a tool |
| Conversation history | `get_recent_messages` | R | any | ✅ | Yes | — |
| Payment draft creation | *none* — no MCP tool wraps `create_escort_payment_draft()`/`create_advance_request_draft()` | W | — | ❌ | N/A | **Gap** |
| Payment draft inspection | `audit_get_drafts(phone, status)` — queries `fazle_draft_replies` per its own docstring's table, **not confirmed to include `fazle_payment_drafts` rows** (**NOT VERIFIED** — worth a direct check before assuming it's a full substitute) | R | any | Partial/unclear | — | Needs confirmation |
| Payment draft editing | *none* — no tool wraps `draft_approval.edit_draft()` | W | — | ❌ | N/A | **Gap** |
| Payment approval | *none* — `approve_draft` (the only tool with this name) targets `fazle_draft_replies`, not `fazle_payment_drafts` — **confirmed by reading both the tool code and the route it calls** | W | RUN + confirm=True (for the wrong resource) | ❌ for the actual payment-draft resource | N/A | **P0/P1 gap — see §17** |
| Payment rejection | *none* — no tool wraps `draft_approval.reject_draft()` | W | — | ❌ | N/A | **Gap** |
| Payment execution (`finalize_payment`) | *none* | W | — | ❌ | N/A | **Gap** — this is the actual ledger-writing step |
| Employee creation | *none* — no tool wraps `create_employee_manual()` (the admin-UI path); the automatic auto-create path is not Hermes-triggered at all, it fires inside the accountant-message parse pipeline | W | — | ❌ (by design — arguably correct that Hermes shouldn't have this) | — | Not a gap; intentional |
| Employee merge | *none found* | W | — | ❌ | — | Not traced to an endpoint at all (§11) |
| Cash transaction lookup | `get_cash_transactions`, `get_cash_report` | R | any | ✅ | Yes | — |
| Canonical cash transaction creation | *none* | W | — | ❌ | N/A | **Gap** — the actual financial write |
| Ledger lookup | `get_cash_report` (aggregate) — no direct `fpe_employee_ledger` read tool found | R | any | Partial | Yes | Per-employee ledger detail not directly exposed |
| Ledger update | *none, and correctly so* — only `create_transaction()` legitimately writes the ledger, and no tool calls it | W | — | ❌ (by design) | — | Not a gap; this must never be an LLM-direct write |
| Unmatched-message review | *none* — no tool wraps `GET /unmatched` | R | — | ❌ | N/A | **Gap** |
| Review queue resolution | *none* — no tool wraps `POST /unmatched/{id}/mark-reviewed` | W | — | ❌ | N/A | **Gap** |
| Audit log inspection | `audit_search_logs`, `audit_lookup_whatsapp_messages` — general-purpose, not `fpe_accounting_audit_logs`-specific | R | any | Partial | Yes | No dedicated financial-audit-log tool |
| WhatsApp message drafting | `draft_whatsapp_reply` | W (draft only) | any | ✅ | Yes | — |
| WhatsApp outbound sending | `send_whatsapp_message` | W | RUN + confirm | ✅ | Yes (gated, audited) | Correctly scoped for admin-instructed one-off sends, explicitly *not* for automated pipeline replies per its own docstring |
| Admin approval workflow (generic reply drafts) | `approve_draft` | W | RUN + confirm | ✅ (for `fazle_draft_replies` only) | Yes, well-designed (reuses the exact endpoint the dashboard button and `APPROVE <id>` command hit) | Scoped to the wrong table for this workflow — see above |
| Accountant instruction generation | *none* — `build_accountant_message()` is not exposed | — | — | ❌ | — | Would only matter if payment-approval tools existed first |
| Parser invocation | *none* — `parse_message()` is not exposed as a callable tool (correctly — see §21) | — | — | ❌ | — | Intentional, not a gap |
| Duplicate detection | *none exposed directly*; implicit in `create_transaction()`'s own idempotency, which no tool calls | — | — | — | — | N/A (no write tool exists to trigger it) |
| Scheduler/task execution | `run_scheduled_task`, `list_available_tasks` | W | RUN + confirm | ✅ | Yes | Not traced this session for payroll-specific jobs |

---

## 14. Hermes Authority/Safety Analysis

For the write tools that **do** exist (`send_whatsapp_message`, `approve_draft`,
`draft_whatsapp_reply`, `run_scheduled_task`, `opencode_dispatch`): all confirmed (re-verified this
session by reading `send_whatsapp_tools.py` directly, not just citing the 08-17 audit) to share one
consistent gate — `_read_mode()` (fail-closed to `READ` on any file/parse error) requiring `RUN`
mode **and** an explicit `confirm=True` the caller must set only after the human has instructed
that specific action. Every one of these calls fazle-core's own existing HTTP endpoints (never a
second, parallel write path) and inherits fazle-core's own audit logging
(`fazle_admin_audit`/`fazle_draft_audit_log`) automatically. **No hallucination-to-transaction path
was found**, because — critically — **no tool exists yet that would let a hallucination reach
`fpe_cash_transactions` at all.** The absence of the payment-approval/finalize tools is
simultaneously this audit's biggest capability gap (§13) and, for now, its biggest safety
guarantee: Hermes structurally cannot create an unauthorized financial transaction today, not
because a guard was built to stop it, but because the door was never opened. This should be
understood as "safe by omission," not "safe by design," when weighing whether to build the missing
tools — the design work in §21 needs to happen deliberately, not be assumed to already exist.

Can Hermes accidentally bypass approval? No mechanism found that would let it. Can Hermes send a
WhatsApp message without approval? No — `confirm=True` is required and is documented as
settable only after explicit human instruction; whether Hermes *reliably* asks first is a model-
behavior question, not a tool-design one, and out of this audit's scope. Can Hermes create a
financial transaction directly? **No — confirmed, no tool reaches `create_transaction()`.** Can
Hermes create an employee? No tool found that does. Can Hermes modify payroll/ledger data? No.

---

## 15. Test Coverage Matrix

| Area | Coverage | Evidence |
|---|---|---|
| Payment parser (ID:/method/amount grammar) | Covered | `tests/unit/test_fpe_rules.py` and others (not individually re-read this session; parser code itself carries dated fix comments referencing specific incidents, a good sign of test-informed hardening) |
| Employee resolution status | Covered | `tests/unit/test_fpe_employee_resolution_status.py` |
| Unmatched-message notification | Covered | `tests/unit/test_fpe_unmatched_notification.py` |
| Unmatched-confirmation RBAC | Covered | `tests/unit/test_unmatched_confirmations_rbac.py`, `tests/e2e/test_unmatched_confirmations.py` |
| Draft approval (Sprint-3B) | Covered | `tests/unit/test_sprint3b_draft_approval.py` |
| Payment workflow (escort/advance draft + finalize) | Covered, substantially | `tests/unit/test_payment_workflow.py`, `tests/workflows/test_escort_payment_flow.py` (large file, many `finalize_payment` call sites), `tests/workflows/test_full_workflows.py` |
| Unified payroll calculation (new engine) | Covered, 28 tests, all passing (per same-day finalization doc, not re-run this session) | `tests/unit/test_unified_payroll_calculation.py` |
| **Hermes tool invocation of the payment-approval path** | **Uncovered — because the tool does not exist** | N/A |
| Bengali numerals/currency parsing | **Not independently verified this session** — `parser.py`'s `_BENGALI_DIGITS_TRANS` exists and is used; presence of a dedicated test file not confirmed | **NOT VERIFIED** |

This is a partial matrix, not exhaustive — a full pass would need to open each test file and check
assertions rather than trust filenames; that was not done this session given scope/time.

---

## 16. End-to-End Scenario Matrix

| # | Scenario | Identity/Verification | Draft? | Approval path | Outcome |
|---|---|---|---|---|---|
| 1 | Known active employee requests advance | `identity_brain` resolves; status not checked by `payment_workflow` itself (§C.2) | Yes, `fazle_payment_drafts` | `PAID`/`ADVANCE` or `APPROVE` | Transaction on approval |
| 2 | Known employee on leave requests advance | Same as #1 — **no code found that treats "on_leave" differently from "active" at draft-creation time** | Yes | Same | Same as #1 — **no differentiated handling found; flagged, not confirmed as a bug, since this may be intentional (on-leave staff can still receive owed pay)** |
| 3 | Former employee asks about historical payment | `_lookup_lifecycle()` → `"former"`, still identifiable/answerable (read-only context) | No draft implied by a question | N/A | Answerable via `payroll_logic`'s "never fabricate" aggregation, per parent audit §5.6 |
| 4 | Unknown mobile requests payment | `match_or_create_employee` reaches auto-create branch if a phone is present | Would create a **new employee record**, then presumably a draft — **not traced end-to-end this session** whether `payment_workflow.py`'s two draft functions are ever called with a freshly-auto-created ID in the same turn (**NOT VERIFIED**) | — | — |
| 5 | Admin approves a draft | — | — | `PAID`/`APPROVE` → `create_transaction()` | Ledger updated, audited |
| 6 | Admin edits a draft | — | — | `EDIT`/Sprint-3B `edit_draft()` | Version incremented, then re-approve |
| 7 | Admin rejects a draft | — | — | `REJECT`/`DREJECT` | `status='rejected'`, no transaction |
| 8 | Accountant receives approved instruction | — | N/A (instruction path bypasses drafts) | `parse_message()` → `validate_for_accounting()` | Transaction if valid |
| 9 | Malformed accountant instruction | — | — | Fails `validate_for_accounting()` | Routed to `fpe_unmatched_messages`, admin notified |
| 10 | Unresolved employee identity in instruction | — | — | `match_or_create_employee()` returns `None` (name-only, no phone) or auto-creates (phone present) | Review queue **or** silent new-employee creation, depending on phone presence — see §11 finding |
| 11 | Legitimate duplicate-looking payment (same employee/amount/day, different message) | — | — | `_build_txn_ref()` keys on message identity | **Both post** — confirmed, matches business rule |
| 12 | Company-level financial summary | — | — | `_is_balance_summary()` short-circuits before payment parsing | Acknowledged, not posted as a transaction — confirmed |
| 13 | Same as #11, restated | — | — | — | Same as #11 |
| 14 | Ambiguous/multiple identity match | — | — | Fuzzy match above threshold but too close to 2nd-best → treated ambiguous, auto-create + `enqueue_review()` | Tentative record created, flagged for reconciliation — not silently guessed |
| 15 | Escort-released employee, still a historical record | `_lookup_lifecycle()` context only; `identity_brain`'s "released from escort duty" case not separately traced this session | — | — | **NOT VERIFIED** as a distinct code path from "former" |

---

## 17. Gap List by Severity

### P0 — Financial/Security/Critical
- **None found that let an *existing* tool cause an unauthorized financial write.** The
  write-capable tools that exist (`send_whatsapp_message`, `approve_draft`, `run_scheduled_task`,
  `opencode_dispatch`) are all gated (RUN mode + explicit confirm) and audited, re-confirmed this
  session.
- **Auto-create-on-phone-evidence has no approval gate** (§11) — this is real, live, in
  production, and independent of Hermes; it is a pre-existing risk in the human-operated pipeline
  itself (a mistyped phone number in a real accountant message creates a permanent employee
  record). Recommend classifying as **P0 for the underlying system**, not newly introduced by
  Hermes.

### P1 — Functional / Hermes Capability
- **Hermes has no tool to approve, edit, reject, or finalize a payment draft, and no tool reaches
  `create_transaction()` at all.** If Hermes is expected to execute *any* part of the
  approve→instruct→transact half of this workflow, this is the blocking gap — confirmed via direct
  code read of both the only `approve_draft` tool and the route it calls.
- **Hermes has no tool for the unmatched-message review queue** (`GET /unmatched`,
  `POST /unmatched/{id}/mark-reviewed`) — confirmed absent from `fazle-mcp`'s tool list.
- **`audit_get_drafts`'s coverage of `fazle_payment_drafts` vs. only `fazle_draft_replies` is
  unconfirmed** — needs a direct check before assuming Hermes can even *see* payment drafts today.
- **Whether a resigned/terminated employee's ID can still produce a new payment draft** (§12/§C.2)
  — status is not filtered in `payment_workflow.py`'s two draft-creation functions; whether an
  upstream caller filters it was not confirmed.

### P2 — Knowledge/Documentation
- `cash_workflow.md`'s duplicate-rule wording overstates timestamp's role (§8.1) — cosmetic,
  five-minute fix.
- No KB document mentions the `APPROVE`/`EDIT`/`DREJECT` command grammar exists alongside
  `PAID`/`ADVANCE` (§8.2) — both live, both correct, only one documented.
- Carried forward, not re-verified: office-fact 5-way duplication, recruitment-script KB gap,
  salary-figure role-scope drift (all from the 08-17 audits, cited not repeated).

### P3 — Quality
- Test-file-name-level coverage matrix (§15) should be upgraded to an assertion-level pass.
- The old per-diem PAY-01 formula is known-stale versus the finalized policy but still live
  (self-flagged, Owner-gated deploy decision already pending per
  `HERMES_PAYROLL_PHASE1-4_FINALIZATION_2026-08-18.md` — not a new item, cited for completeness).

---

## 18–19. Recommended KB Architecture & Hermes KB Improvements

The 08-17 parent audit's proposed layered structure (Business Constitution → Canonical Business
Rules → Entity Model → Workflow State Machines → Operational Policies → Parser/Extraction Rules →
Hermes Tool Contracts → Examples) remains the right shape and is not revised here. This session's
one addition: **the Workflow State Machine layer should explicitly separate the two draft-command
grammars found in §8/§9** — a Hermes reading a state-machine doc for "payment draft lifecycle"
needs to learn both `PAID <id> <amount> <method>` (legacy) and `APPROVE <id>` (Sprint-3B) map to
the same terminal state, rather than encountering only one and assuming the other doesn't exist or
is a conflicting old command that should be corrected.

A minimal **Tool Contract** entry this session would add once the missing tools in §20 are built:

```
Tool: approve_payment_draft(draft_id, amount, method, confirm)
  Purpose: Finalize an admin-approved payment/advance draft into a canonical transaction.
  Authority: RUN mode + confirm=True + admin must have already stated approval in-conversation.
  Preconditions: draft.status == 'pending', draft not expired (24h TTL).
  Side effects: writes fpe_cash_transactions, updates fpe_employee_ledger, marks draft 'sent'/'completed'.
  Idempotency: yes — txn_ref keyed on draft_id-derived message identity; a second call on an
    already-finalized draft returns the existing transaction, does not double-post.
  Failure behavior: returns {"error": ...} without side effects on any validation failure.
  Audit: fpe_accounting_audit_logs + fazle_draft_audit_log, automatic.
```

---

## 20. Missing Hermes Tools/Capabilities (consolidated from §13/§17)

1. `approve_payment_draft` / `reject_payment_draft` / `edit_payment_draft` wrapping
   `payment_workflow.finalize_payment()` and `draft_approval.{approve,reject,edit}_draft()` — the
   single highest-value addition, directly closes the P1 finding.
2. `list_unmatched_transactions` / `resolve_unmatched_transaction` wrapping the existing
   `/unmatched` and `/unmatched/{id}/mark-reviewed` routes.
3. A trust-tiered identity-resolution read tool wrapping `identity_brain.detect_identity()`
   (carried forward from the 08-17 audit, still open).
4. Confirm/extend `audit_get_drafts` (or add a sibling) to cover `fazle_payment_drafts`
   specifically, not just `fazle_draft_replies`.

None of these should be built without a fresh, explicit Owner authorization decision, consistent
with this repo's own established pattern for expanding Hermes's write authority
([[project_hermes_full_authority_phase1_20260813]]) and with `.claude/CLAUDE.md`'s "no
feature/business-logic change without it being explicitly asked for" rule. This audit recommends
the *gap be closed*, not that the tools be built as a byproduct of this report.

---

## 21. Minimal Required Changes

If Owner decides to close the P1 gap: add the 1-2 new `fazle-mcp` tools in §20.1, each a thin
wrapper reusing the existing `finalize_payment()`/`draft_approval` functions exactly as-is (mirror
`approve_draft`'s own existing pattern of calling an existing endpoint rather than inventing new
logic) — no change needed to `payment_workflow.py`, `draft_approval.py`, `accounting.py`, or the
parser at all. If Owner decides the auto-create-on-phone-evidence gap (§11/§17 P0) needs a guard:
the minimal change is adding a confidence/review step specifically for the "phone present, no name
match" branch of `_auto_create_employee()`'s caller — not a parser or accounting change. Neither
change is made in this report.

---

## 22. Final Answer to §27

> *If we give Hermes the current KB and current tools today, can Hermes safely perform the
> complete employee conversation + verification + payment request + approval + accountant
> instruction + parsing + employee lookup + canonical transaction + ledger + audit workflow
> without relying on undocumented assumptions?*

**PARTIALLY.**

- **Employee conversation, identity verification (read), payment request/draft creation, and
  accountant-instruction parsing all already run in fazle-core's own deterministic code — not
  something Hermes needs to (or should) perform itself.** For the parts of this workflow that are
  *read-only* or *drafting*, Hermes's existing tool surface (`get_employees`,
  `get_cash_transactions`, `get_cash_report`, `draft_whatsapp_reply`, `audit_get_drafts`) is
  adequate and safely gated.
- **For the approve → accountant-instruction → canonical-transaction → ledger half of the
  workflow, Hermes has no tool that reaches it at all.** This is not an authority/safety weakness
  in an existing tool (every existing write tool is well-gated) — it is a **capability that was
  never built**. Hermes cannot today approve, reject, edit, or finalize a payment draft, and cannot
  resolve an unmatched-transaction review-queue item, through any registered mechanism.
- The one write action shaped like it might cover this (`approve_draft`) verifiably targets a
  different table (`fazle_draft_replies`) — confirmed by reading both the tool and the route.

So: safe (nothing found lets Hermes cause an unauthorized transaction today) but **not complete**
— the workflow as specified in the brief cannot be executed end-to-end by Hermes, only by a human
typing WhatsApp commands directly. Closing that gap is a deliberate scope decision for the Owner,
not an emergency fix — the current state is the conservative default, not a broken one.
