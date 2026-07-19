---
title: Phase C — Cross-Conversation Memory Architecture Plan
owner: Fazle Core Admin
status: planning-only-not-implemented
last_verified: 2026-07-19
runtime_index: true
---

# Phase C — Cross-Conversation Memory Architecture Plan

**Status: planning only. Nothing in this document has been implemented.**
No schema in this plan exists in the database. No code path described
here runs. This document exists to get Owner sign-off on the *shape* of
Phase C before any implementation work begins, per the explicit
instruction that closed out Phase B Stages 2-3: plan Phase C, do not
build it.

## 1. Purpose And Relationship To Phase B

Phase B (`modules/conversation_memory`, Stages 1-3) solved **within-
conversation** memory: reconstructing one WhatsApp thread's worth of
messages into one business object (an escort order) within a short TTL
window (minutes), scoped to a single `(bridge, phone, business_domain)`
identity, and discarded (soft-expired) once that window closes.

Phase C is a different, larger problem: **cross-conversation, long-lived
memory about a specific person** — what does the system already know
about this applicant, this client, this employee, from *previous*
conversations, possibly weeks or months apart, and how should that
knowledge (never authority) inform how the system responds today.

Phase C explicitly reuses Phase B's proven patterns rather than
reinventing them:
- Field-level provenance (`value`, `source`, `confidence`, `extraction_method`)
- Conflict-not-overwrite merge policy
- Non-destructive expiry (state transition, never hard delete)
- Feature-flag-gated, shadow-mode-first rollout
- A strict boundary: memory changes what the system *understands*, never
  who is *authorized* or what is *authoritative*

Phase C does **not** replace `fazle_recruitment_sessions` (recruitment
keeps its own working step-by-step intake mechanism, per the Owner's
2026-07-19 coexist decision already recorded for Phase B) and does not
replace any authoritative business table (`wbom_escort_programs`,
`fpe_employees`, `fazle_payment_drafts`, etc.). It is an *additional*,
strictly advisory layer that helps compose better prompts and surfaces
useful context — it never writes to a business table, ever, in any phase
of its own rollout.

## 2. Memory Classes

Every fact Phase C stores is tagged with exactly one class, because
different classes carry different trust levels and different retention
rules. This distinction must be visible everywhere memory is read, not
just at write time — a prompt-assembly bug that silently treats an
`inferred_preference` the same as a `verified_fact` is the single most
likely way this system produces a wrong or embarrassing answer.

| Class | Definition | Example | Trust level | Default retention |
|---|---|---|---|---|
| `verified_fact` | Directly sourced from an authoritative DB row (`wbom_employees`, `fpe_employees`, `wbom_escort_programs`, confirmed billing records) | Employee's designation, basic salary, join date | Highest — treat as ground truth | Not stored independently; always live-read from source DB at use time, never cached as memory |
| `user_stated_fact` | The person said it about themselves, unconfirmed against any DB | "My phone number changed to X", "I work at the Chattogram site now" | Medium — usable, but must be flagged as unconfirmed if it contradicts a `verified_fact` | 180 days, renewed on restatement |
| `inferred_preference` | The system inferred it from behavior/phrasing, nobody stated it directly | Prefers Bangla replies, usually asks about the same 2 vessels | Low — usable only for tone/relevance, never for a factual claim | 90 days, decays faster than stated facts |
| `workflow_state` | Where this person is in an ongoing, multi-step process | "Applicant completed 3 of 5 recruitment steps as of last Tuesday" | Medium — operationally useful, time-sensitive | Until the workflow resolves or its own TTL (mirrors Phase B thread TTL, just longer) |
| `temporary_memory` | Short-lived context worth remembering for a few days but not indefinitely | "Client mentioned they'll be traveling next week" | Low, explicitly ephemeral | 7-14 days, no renewal |
| `unknown` | Explicitly the *absence* of information — a placeholder so downstream code can distinguish "we checked and don't know" from "we never checked" | "Applicant's preferred shift: unknown" | N/A | Same TTL as the class it would have belonged to |

`unknown` is a first-class value, not the absence of a row — this
matters for prompt assembly (Section 8): a prompt should be able to say
"we don't know their preferred shift yet" rather than silently omitting
the fact and letting an LLM guess.

## 3. Three Memory Domains

Each domain is its own logical namespace (own table, own writer, own
retrieval function) even though they share the same class taxonomy and
the same underlying primitives. They are never queried across domain
boundaries by default — Section 7 covers why that isolation matters.

### 3.1 Applicant Memory

Scope: people going through (or who have previously gone through)
recruitment, tracked by `fazle_recruitment_sessions`' phone identity.

Sketch schema (planning only, not applied):

```sql
-- fazle_applicant_memory_facts (NOT CREATED)
id BIGSERIAL PRIMARY KEY
applicant_phone       VARCHAR(30) NOT NULL   -- normalized
fact_key              VARCHAR(60) NOT NULL   -- e.g. 'preferred_area', 'age_stated'
fact_value             JSONB NOT NULL
memory_class          VARCHAR(30) NOT NULL   -- see Section 2
confidence             NUMERIC
source_type            VARCHAR(30)            -- 'recruitment_session' | 'conversation' | 'admin_note'
source_reference_id    VARCHAR(100)           -- e.g. fazle_recruitment_sessions.id
first_observed_at      TIMESTAMPTZ NOT NULL
last_confirmed_at      TIMESTAMPTZ NOT NULL
expires_at             TIMESTAMPTZ
superseded_by_fact_id   BIGINT REFERENCES fazle_applicant_memory_facts(id)
created_at / updated_at
```

Lifecycle: written by observing `fazle_recruitment_sessions` state
transitions and by an equivalent shadow-observation hook on applicant-
originated WhatsApp messages (same authorization-gate-first pattern as
Phase B). Read only to help a *future* recruitment conversation resume
context ("last time you said you preferred the Chattogram area") — never
to skip a required intake step, never to auto-approve anything.

### 3.2 Client Memory

Scope: escort clients (`ESCORT_CLIENT_PHONES`), billing clients, and any
other external counterparty the system talks to on an ongoing basis.

Same shape as Applicant Memory, with `fact_key` values relevant to that
relationship — e.g. `usual_mother_vessel_route`, `preferred_language`,
`typical_message_style` (useful for interpreting future ambiguous
messages from that specific client, e.g. someone who is known to always
send Bangla-only messages).

Explicit non-goal: Client Memory must never accumulate escort-order
*business* fields (mother vessel, lighter vessel, master mobile) as
long-lived memory — those belong entirely to Phase B's short-TTL
conversation threads and, once confirmed, to `wbom_escort_programs`.
Phase C's Client Memory is about the *relationship*, not the *order*.

### 3.3 Employee Memory

Scope: `wbom_employees` / `fpe_employees`, bridged the same way those two
tables already are (`fpe_employees.wbom_employee_id`).

Most Employee Memory facts should be `verified_fact` (read live from
`wbom_employees`/`fpe_employees`/`escort_roster_entries` at query time,
never cached) precisely because that data already exists authoritatively
— Phase C's Employee Memory table should mostly hold `user_stated_fact`,
`inferred_preference`, and `workflow_state` (e.g. "employee mentioned
they'd prefer day shifts", "employee's payroll edit request is in
review") — anything that *isn't* already a column somewhere.

**Privacy note specific to this domain:** employees are internal staff,
not external counterparties — RBAC for who can read Employee Memory must
be at least as strict as who can read `fpe_employees` payroll data today
(Section 9), not looser just because it's "just conversation memory."

## 4. Identity Resolution

A phone number is not a stable 1:1 key across domains — the same person
could plausibly appear as an applicant, later as an employee, and in
principle even interact as a client-side contact for an unrelated
company. Phase C must never silently conflate these.

Proposed resolution rule: identity within Phase C is always
`(domain, normalized_phone)`, never bare phone. Cross-domain identity
linking (e.g. "this employee was previously an applicant with this
phone") is an explicit, admin-reviewed link (mirroring the existing
`fpe_employees.wbom_employee_id` bridge pattern), never an automatic
inference — the cost of a wrong automatic merge (leaking one person's
applicant history into an unrelated employee's context, or vice versa)
is much higher than the cost of occasionally missing a legitimate link.

## 5. Memory Writer

A single shared component (`modules/memory_engine` in this plan, name
not final) responsible for turning an observation (a conversation
message, a workflow state change, an admin note) into a memory fact
write, reused by all three domains rather than three separate
implementations.

Responsibilities:
- Authorization gate re-check (same defense-in-depth pattern as Phase B
  — never assume the caller already checked).
- System-generated exclusion (same `_is_system_generated_notification()`
  reuse as Phase B).
- Classification: assign the correct memory class (Section 2) based on
  source type — a DB-sourced observation is always `verified_fact` (and,
  per Section 3.3, likely shouldn't be written to the memory table at all
  — just read live); a self-reported statement is `user_stated_fact`; a
  pattern noticed across multiple messages is `inferred_preference`.
- **Contradiction resolution**: reusing Phase B's `merge_field()` policy
  shape (explicit-outranks-inferred, higher-confidence-corrects,
  conflicting-high-confidence-flags-not-overwrites, normalized-
  equivalent-not-a-conflict) but applied to long-lived facts instead of
  in-thread fields. A contradiction between a new `user_stated_fact` and
  an existing `verified_fact` is a special case: the `verified_fact`
  (live DB read) always wins for anything the DB already tracks — the
  user-stated version gets recorded as `user_stated_fact` alongside it
  with a `disagrees_with_verified_fact` flag, never silently overwriting
  or discarding the verified value.

## 6. Expiry, Correction, Deletion

- **Expiry**: per-class TTL from Section 2's table, enforced the same
  non-destructive way as Phase B (`superseded_by_fact_id`/expiry
  timestamp, never a hard delete of the row itself, so there's always an
  audit trail of what the system believed and when).
- **Correction**: a person restating a fact writes a new row
  superseding the old one (`superseded_by_fact_id`), never an in-place
  UPDATE — matching this repo's broader preference for preserving
  history over mutating it in place.
- **Deletion (right-to-erasure equivalent)**: an explicit admin-invoked
  hard delete path, separate from normal expiry, for cases where a
  person asks for their conversational memory to be removed. This is the
  one place Phase C *should* hard-delete rather than soft-expire —
  privacy requests are a different category of requirement than
  operational retention. Must be logged (who requested, who executed,
  when) even though the memory content itself is gone, mirroring how
  `escort_roster_audit_logs` captures full row content before Phase B's
  neighboring `expire_stale_drafts()` hard-deletes.

## 7. Cross-Conversation Reasoning — Retrieval Priority

When composing a response that could benefit from memory, retrieval
follows a strict, non-negotiable priority order — each level is only
consulted if the level above didn't already answer the question:

1. **Current message** — what the person just said, always wins for
   anything it directly states.
2. **Current thread** (Phase B's conversation memory, if a domain has
   opted in) — accumulated fields from the active, unexpired
   conversation.
3. **Authoritative DB** — live read of the real business tables
   (`wbom_employees`, `wbom_escort_programs`, etc.) — this is where
   `verified_fact`s actually come from, read fresh, never cached in
   Phase C's own tables.
4. **Verified entity memory** — Phase C's own `verified_fact`-class rows,
   for facts that are DB-sourced but expensive/awkward to re-query live
   (a deliberately narrow exception to point 3, decided per-fact, not a
   default).
5. **Canonical KB** — `knowledge_base/` documents (business rules,
   workflow definitions) — the "how the system should behave" layer, not
   person-specific.
6. **Prior conversation summaries** — Phase C's `user_stated_fact` /
   `inferred_preference` / `workflow_state` rows from earlier,
   *different* conversations.
7. **Constrained LLM inference** — only once every deterministic source
   above has been exhausted, and only for framing/tone, never for
   inventing a fact. Any LLM-inferred content used in a reply must be
   clearly distinguishable internally from everything above it (so a
   later audit can tell "the system said this because a person told it
   this" apart from "the system guessed this").

This ordering exists specifically to prevent the failure mode Phase B's
own MV Horda incident and Root Cause 7 fix both trace back to: something
lower-trust (parser guesses, automated replies, stale memory) silently
outranking something higher-trust (an actual confirmation, a real DB
row). The same discipline that fixed those incidents is encoded here as
a structural rule, not left to per-prompt judgment.

## 8. Prompt Assembly

- Each retrieved fact enters the prompt tagged with its memory class
  (Section 2) — never flattened into undifferentiated context text. A
  fact's class should visibly constrain how the model is allowed to use
  it (e.g. a system instruction distinguishing "known facts" from
  "things this person has said before, unconfirmed" from "patterns we've
  noticed").
- Token budget: authoritative DB facts and current-thread state take
  priority for budget over older cross-conversation memory — if
  something has to be trimmed, trim from the bottom of the retrieval
  priority list (Section 7), never the top.
- Redaction: Employee Memory must never appear in a client-facing prompt
  context and vice versa — domain isolation (Section 3) is enforced at
  the prompt-assembly layer as a hard filter, not just a query-time
  convention.

## 9. RBAC And Privacy

- Memory read/write access follows the existing `fazle_roles` hierarchy
  (`viewer(10) < office_assistant(20) < operator(30) < accountant(50) <
  admin(70) <= superadmin(100)`), not a new parallel permission system.
- Employee Memory read access should require at minimum the same role
  level as reading the underlying `fpe_employees` data it's adjacent to
  — Phase C must not become a lower-friction side door to information
  that's otherwise access-controlled.
- Client/Applicant Memory: readable by roles that already interact with
  those conversations operationally (matching current escort/recruitment
  access), not broadened just because it's a new table.
- No memory fact should ever be exposed back to a *different* person than
  the one it's about, even in aggregate/statistical form, without an
  explicit design review — this plan does not currently include any
  cross-person analytics use case, and one should not be assumed.

## 10. Retrieval Filtering And Evaluation

- Relevance scoring: recency-weighted within each class (a `workflow_
  state` fact from yesterday outranks one from three months ago), with
  `verified_fact`s exempt from decay (they're re-read live, not scored).
- Domain isolation (Section 3) is a hard filter, applied before any
  relevance scoring — never a soft ranking signal.
- **Evaluation, mirroring Phase B's shadow-mode philosophy**: before any
  Phase C output is allowed to influence a real reply, it runs in shadow
  mode first — recording what the memory-informed response *would have
  been* next to the current (memory-blind) response, the same
  `current_result` vs `memory_aware_result` comparison-record pattern
  Phase B's `fazle_conversation_shadow_comparisons` already established.
  Metrics to track before graduating out of shadow mode: how often
  memory-aware context would have changed the response, how often a
  `user_stated_fact` conflicted with a `verified_fact` (a signal the
  contradiction-resolution logic needs tuning), and false-memory rate
  (memory that turned out to be wrong when checked against a later
  authoritative source).

## 11. Rollout Gates

Phase C, if approved, should follow the same staged, flag-gated,
Owner-checkpointed pattern Phase B just completed — not a single big-bang
implementation:

1. **Stage C1 — schema + memory writer, shadow mode only, one domain.**
   Pick the domain with the clearest, lowest-risk win (Client Memory for
   escort clients is the natural first candidate, given Phase B already
   built the authorization/system-generated-exclusion primitives this
   would reuse). No retrieval, no prompt integration yet — just prove
   the writer accumulates facts correctly, same rigor as Phase B Stage 1.
2. **Stage C2 — retrieval + shadow-mode comparison records**, still no
   real prompt influence. Proves the retrieval-priority chain (Section 7)
   and prompt-assembly filtering (Section 8) produce sensible output
   without yet being allowed to change a real reply.
3. **Stage C3 — remaining two domains** (Applicant, Employee), each with
   their own privacy/RBAC review (Section 9) before shadow-mode
   observation begins.
4. **Stage C4 — real prompt integration**, feature-flagged, one domain at
   a time, only after shadow-mode metrics (Section 10) are reviewed and
   explicitly approved by the Owner. This is the step Phase B never took
   for its own multi-message draft creation either — the same "shadow
   mode proves it, a separate approval activates it" boundary applies
   here.

Every stage above requires its own explicit Owner go-ahead, exactly like
Phase B did. Nothing in this document authorizes starting Stage C1.

## Cross References

- `../04_business_rules/escort_business_rules.md` → "Multi-Message
  Intake (Phase B, Shadow Mode)"
- `../05_workflows/escort_source_of_truth_workflow.md` → "Phase B —
  Conversation Memory Engine"
- `modules/conversation_memory/` (Phase B implementation this plan
  reuses patterns from)
- `management_decisions.md` → "Phase B, Conversation Memory Engine,
  Stages 1-3"
