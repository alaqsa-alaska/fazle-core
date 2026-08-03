# Proposal: Persona-Based WhatsApp Reply Generation (Social + Recruitment)

**Status:** Phases 0–2 IMPLEMENTED, COMMITTED (`654c9ff`, local only, not
pushed), NOT YET RESTARTED. Owner approved proceeding with Phase 1-3
(2026-08-03). `scripts/restart_gate.sh --check-only` was run once before
the commit and correctly BLOCKed on working-tree cleanliness (5 dirty
files) — this is expected and by design, not a problem with the changes
themselves (checks 2-7 all passed: secret scan, xfail count, targeted
tests, migration inventory, `.env` flags, backup freshness). Committing
resolves check 1; re-run the gate after this commit. Per this project's
standing rule, the assistant session does not restart the service itself
— see "Restart handoff" below.
**Author:** Claude (assistant-platform session), 2026-08-03
**Scope:** fazle-core's WhatsApp/social reply generation only. Does not touch
assistant-platform's admin-facing Hermes (`/hermes` page), which is unrelated
and already admin-only.

## Implementation Status (final, 2026-08-03) — read this first

This proposal went through **three rounds of correction** as deeper reading
kept overturning earlier assumptions — recorded here plainly rather than
smoothed over, because the final round changes where the actual code lives:

1. First draft: assumed no role/persona system existed at all.
2. Second draft (§1e): found `identity_brain` + `role_classifier` and
   assumed `role_classifier`'s `_ROLE_PROMPTS` was the live persona
   mechanism for `message_router`, per the KB doc's claim.
3. **Final, verified-by-direct-tracing:** `role_classifier.get_user_context()`
   is called from exactly **one place in the entire codebase** —
   `GET /api/users/{phone}`, an admin lookup endpoint — confirmed by
   `grep -rln` across `app/` and `modules/`. It is **not** part of the
   reply pipeline. The KB doc's claim was wrong. The *real* live mechanism,
   traced by following `message_router.py` → `app/ollama.py::generate_reply()`
   → `shared/reply_policy.py::build_whatsapp_reply_policy()`, is a
   **different** `ROLE_PROMPTS` dict in `shared/reply_policy.py` — the
   actual "single source of truth for WhatsApp reply-generation
   instructions" per that module's own docstring, shared identically by
   bridge1, bridge2, and meta WhatsApp.

**A real, concrete bug fell out of this tracing**: `message_router.py`
passes `identity_brain`'s resolved role string straight into
`generate_reply(role=role_str)` with **no mapping step**
(`role_str = identity["role"]`, unchanged). But `shared/reply_policy.py`'s
`ROLE_PROMPTS` only had 7 keys (`employee`, `client`, `new_lead`, `admin`,
`vendor`, `partner`, `known_contact`) — none of which match most of
`identity_brain`'s actual vocabulary (`vip_client`, `client_escort_buyer`,
`repeat_client`, `candidate`, `supervisor`, `accountant`,
`internal_coordinator`, `escort`, `security_guard`). Every one of those
silently fell back to the generic `new_lead` (new-stranger) tone — **a
real VIP client has been getting the same tone as a random unknown
sender.** Confirmed live with a direct prompt-build test (see below).

### What was actually changed

| File | Change |
|---|---|
| `shared/reply_policy.py` | Added the 9 missing `ROLE_PROMPTS` keys (real bug fix for `message_router`/bridge1/bridge2/meta) |
| `modules/social_auto_reply/reply_rules.py` | New `SOCIAL_PERSONAS` dict (recruitment_officer, hr_admin_officer, client_relations, funny, lawyer) + `SOCIAL_ROLE_TO_PERSONA` mapping + `persona_for_role()` |
| `modules/social_auto_reply/intelligent_generator.py` | `plan_reply()` now calls `identity_brain.detect_identity()` (Phase 1 — this channel was completely identity-blind before); `_polish_with_ai()` now takes the resolved persona text and a compatible `role=` value instead of the hardcoded `role="new_lead"` |
| `knowledge_base/06_developer_system/automation_pipeline.md` | Corrected the stale footer section (Phase 0) |

**"funny" and "lawyer" personas exist but are deliberately not
auto-assigned to any role** — this channel auto-sends to real people with
no review for safe intents; an unprofessional or legally-loaded tone
shouldn't be picked automatically. They're defined for a possible future
manual/admin-triggered path.

### Verification performed

- `python3 -m py_compile` on all 4 changed files — clean.
- Existing test suite: `tests/unit/test_reply_policy.py` (18 tests) +
  `tests/unit/test_identity_brain.py` (23 tests) — **41/41 still pass**,
  confirming the additions didn't break existing behavior (only new dict
  keys were added, nothing existing was modified).
- `tests/unit/test_message_router_no_reply_name_gate.py` — passes in
  isolation (10/10), both with and without these changes. It fails when
  run *together* with 3 other specific test files, but this was proven to
  be a **pre-existing test-isolation issue** (a mock-leak in
  `modules/escort/__init__.py`'s `_matches_company_name`), reproduced
  identically via `git stash` on the *unmodified* code — not caused by
  this work.
- Standalone sanity check: called `reply_rules.persona_for_role()` for
  all 15 real `identity_brain` role values (including an unrecognized one)
  — every one resolved to a valid, non-empty persona string, correct
  fallback for the unrecognized case.
- **Full end-to-end prompt-build test** for the previously-broken
  `vip_client` role via `build_whatsapp_reply_policy(role='vip_client', ...)`
  — confirmed the assembled prompt now contains the correct VIP persona
  line instead of the generic `new_lead` fallback text.

### Restart handoff (Owner action required — not done by this session)

None of this is live until `fazle-core.service` restarts. Per fazle-core's
own `.claude/CLAUDE.md`: *"Never restart the production service casually
— use `scripts/restart_gate.sh --restart` (9 checks: migrations applied,
tests green, no leaked secrets, env flags acknowledged, etc.)."*

```bash
cd /home/azim/core
scripts/restart_gate.sh --check-only    # review first
scripts/restart_gate.sh --restart       # restart if checks pass
curl -s localhost:8200/health           # sanity check after
```

No new env flags, no migrations, no schema changes in this pass — the
gate should pass cleanly, but running `--check-only` first is still the
right move before `--restart`.

---

## 0. Why this document exists

The Owner asked for Hermes to generate WhatsApp replies with different
personas depending on who's messaging (general public/HR-style, Client/
management-style, job applicant/recruitment-style, plus lighter personas
like "funny" and "lawyer"), auto-enabled for social + recruitment replies
now, with a path to wider "full reply" autonomy later, per-number opt-out,
and no AI-disclosure footer. Per this project's KB-first governance, any
change touching fazle-core (a separate production system) needs: audit →
proposal → explicit Owner sign-off → implementation as reviewed diffs, never
direct code changes from an assistant session. This is that proposal.

---

## 1. Current State (verified by reading the live code directly, 2026-08-03)

fazle-core has **two separate, non-overlapping AI reply systems** — this
surprised the Owner's original framing, so it's worth stating plainly:

### 1a. `modules/social_auto_reply/` — Facebook + Meta WhatsApp Business API ONLY
- Channels: Facebook Messenger, Facebook Page comments, Meta WhatsApp
  Business API. **Explicitly does NOT handle bridge1/bridge2** (confirmed in
  `knowledge_base/06_developer_system/social_auto_reply_system.md`: "There is
  no routing overlap — messages from bridge1/bridge2 are not processed by the
  social auto-reply module's send queue").
- Recruitment-focused. Deterministic keyword classifier (`classifier.py`, 30+
  intents, Bangla+English, NO LLM) → rule-based template reply
  (`reply_rules.py`) → an AI "polish" step
  (`intelligent_generator.py::_polish_with_ai`, via **local Ollama qwen3:8b**
  through `app.ollama.generate_reply`), gated by `_safe_polish()`: rejects the
  AI's output if it's >1400 chars, contains internal-system words
  ("prompt", "model", "reasoning", "AI", "এআই", "ডেটাবেস"), or contains ANY
  number not in a hardcoded whitelist (`{350, 1000, 3500, 10000, 12000,
  15000, 18000, ...}`) — so AI can improve phrasing but can never invent a
  new price/salary figure. Falls back to the raw rule-based text if the AI
  output fails this check.
- 15 "safe" intents auto-send; 16 "risky" intents go to
  `social_flagged_items` for manual review; 3 "escalation" intents
  (`payment_issue`, `legal_issue`, `employee_salary_complaint`) escalate to
  admin with a WARNING log.
- Read this file in full: `intelligent_generator.py`, `reply_rules.py`,
  `risk_flagger.py`, `classifier.py`.

### 1b. `app/message_router` + `app/llm.py` + `app/bridge.py` — bridge1/bridge2 (the real production WhatsApp numbers)
- This is almost certainly the channel the Owner actually means by "WhatsApp
  message sender" — it's the one connected to the real, in-use WhatsApp
  numbers, not a Meta Business API integration.
- Full LLM fallback chain (`app/llm.py::generate_reply()`): **Ollama
  qwen3:8b (primary) → Groq llama-3.1-8b-instant → GitHub Models gpt-4.1 →
  polite holding message (final fallback, never no-reply/error)**.
- `generate_reply(user_message, intent, db_context, history, role, source)`
  — **`role` and `intent` are already first-class parameters.** Roles seen
  in code: `"new_lead"` (default), `"EMPLOYEE"` (via
  `employee_lookup.find_by_mobile()`, phone-number match against the FPE
  employee table). `db_context` is freeform text the *caller* builds and
  injects — currently a small hardcoded string in the social path, not yet
  wired to pull live KB article content on this path (not yet traced for
  the bridge1/bridge2 path specifically — see §3).
- Every successful reply is logged to `llm_learning_memory`.

### 1c. Cross-cutting safety infrastructure (applies to both systems)
- **Draft Quality Gate** (`modules/draft_quality/`): screens ALL replies
  before send/draft — rejects empty text, exact LLM-fallback-string matches,
  8 leak patterns (`file://`, `/home/azim`, tracebacks, code fences, `<|`
  control tokens, `/scripts/`, `/venv/`, broken markdown), and anything over
  4000 chars. Independent of persona/content — a new persona system does not
  need to reinvent this.
- **Draft vs auto-send policy already exists as a two-tier structure**
  (`automation_pipeline.md`): "Auto Allowed" (clear candidate FAQ, safe
  employee policy answer, non-sensitive office/contact answer) vs.
  "Draft/Admin Required" (payment, attendance finalization, escort
  confirmation, release slip ambiguity, unknown identity, sensitive/internal
  request) — routed to `fazle_draft_replies`, 24h TTL, admin approves/rejects.
  **This may already BE most of the "phased permission" structure the Owner
  described** — see §3.
- **Per-number opt-out already exists**: `AUTOREPLY on|off|status <phone>`
  is a real admin WhatsApp command (`modules/admin_directives/router.py`,
  `_RE_AUTOREPLY`), authorized via `_authorize(sender_phone, "ai_autoreply")`,
  logged under directive category `autoreply_policy`. **This is exactly the
  "if Admin says no more reply to \<number\>" mechanism the Owner asked
  for — it does not need to be built, only confirmed to cover both reply
  systems (see §3, unresolved).**
- **Memory Extractor** (`modules/memory_extractor/`): fire-and-forget
  background task, extracts long-term per-sender facts (personal_info,
  work_info, pending_matter, etc.) into `user_memory` after every reply.
  Partially satisfies "read chat history/context" already.

### 1d. The footer — already resolved, no action needed
`automation_pipeline.md` (KB doc, `last_verified: 2026-06-24`) documents a
"🤖 Automated Reply System" footer appended to every auto-reply. **This is
stale.** Reading `app/bridge.py` directly shows it was removed 2026-07-27
per an explicit prior Owner directive, quoted verbatim from the code
comment: *"Owner directive — no footer, marker, or any other character
(visible or invisible) may be appended to an outbound message body. The
previous invisible zero-width-space marker (also named `_AUTOMATED_SUFFIX`)
has been removed outright; outbound text is sent byte-for-byte as given."*
**The Owner's "don't keep any footer mark" instruction is already satisfied
in production.** The only action item here is fixing the stale KB doc
(§4, Phase 0).

### 1e. Contact/role classification — CORRECTED, this section was wrong in the first draft of this proposal
The first draft of this proposal said role classification was "a real gap"
based on `ai_read_contacts.relation` having only 3 values
(`unknown`/`accountant`/`meta_business`) in the narrow read-only view
assistant-platform uses. **That was a wrong conclusion from looking at the
wrong system.** Reading `knowledge_base/06_developer_system/identity_brain.md`
directly (source: `modules/identity_brain/__init__.py`, 393 lines) shows
fazle-core already has a **much richer, already-in-production identity
resolution system** that `ai_read_contacts` was never meant to expose:

- **`identity_brain.detect_identity(phone, text)`** resolves every contact
  to exactly one role via an ordered, priority-based algorithm reading 7+
  real evidence sources: `admin` (env settings), `family`/`accountant`/
  `vip_client`/`client_escort_buyer`/`vendor`/`supervisor` (`fazle_contact_roles`
  seed table), `repeat_client` (`fpe_cash_transactions` history), `employee`
  (`wbom_employees` phone match, with 4 secondary evidence sources),
  `candidate` (recruitment keyword match), `unknown` (fallback). Each
  resolution carries a numeric confidence score (0–100).
- **`role_classifier.get_user_context(phone)`** then answers *how should
  the AI talk to them* — it already injects a per-role Bangla tone
  directive into the LLM system prompt today, in production, for the
  `message_router` (bridge1/bridge2) path: `vip_client` → "speak with
  utmost respect", `client` → "professional and helpful", `employee` →
  "official and helpful", `candidate` → "encouraging and informative",
  `manager` → "concise and direct", `vendor` → "business language",
  `family` → "personal and warm", `unknown` → "brief intro, offer help"
  (`_ROLE_PROMPTS` dict). It also loads the last 10 memory facts from
  `user_memory` (written by the existing Memory Extractor, §1c) and merges
  everything via `build_context_for_llm()` into the prompt.

**This is functionally already a lightweight persona system** — just with
one-line tone directives instead of full named personas, and **it is only
wired into `message_router`, never into `social_auto_reply`** (confirmed by
re-reading `intelligent_generator.py::_polish_with_ai` — it calls
`app.ollama.generate_reply` directly with a hardcoded `role="new_lead"` and
a small fixed business-facts string, never touching `identity_brain` or
`role_classifier` at all).

**The real, corrected gap is therefore much narrower than originally
written:**
1. `social_auto_reply` doesn't use `identity_brain`/`role_classifier` at
   all — it has its own isolated, role-blind logic.
2. `_ROLE_PROMPTS` are one-line tone directives, not the fuller named
   personas the Owner wants (HR & Admin Officer, Operations Officer,
   Recruitment reply generator, Lawyer, funny character) — richer content
   needs to be layered on top of, not instead of, this proven mechanism.
3. This materially reduces both the risk and the amount of new code this
   proposal needs — Phase 1/2 below are revised accordingly (§4).

### 1f'. Related, larger, NOT-yet-built initiative worth knowing about (do not build this now)
`knowledge_base/00_governance/phase_c_memory_architecture_plan.md`
(`status: planning-only-not-implemented`, last_verified 2026-07-19) is a
full architecture plan for exactly this problem space at a much larger
scale — cross-conversation, long-lived memory about a specific person
across three domains (Applicant/Client/Employee memory), with a memory-class
taxonomy (verified_fact/user_stated_fact/inferred_preference/workflow_state/
temporary_memory), a strict cross-domain isolation rule, and a 4-stage
shadow-mode-first rollout gate. **Nothing in it is implemented — no schema
exists, no code runs.** It reuses patterns from `modules/conversation_memory`
(Phase B, live in shadow mode for escort-order multi-message parsing,
unrelated to replies). This proposal's persona work should be **compatible
with, but not dependent on,** Phase C — Phase C would eventually make
persona replies smarter (real memory of a specific client/applicant across
conversations), but building any of Phase C is explicitly out of scope
here. Flagging its existence so this proposal doesn't accidentally
duplicate or conflict with already-planned work if Phase C is approved
later.

### 1g. Hermes personas (assistant-platform) — separate, unrelated system
assistant-platform's admin-facing Hermes (`/hermes` page, this session)
has 14 fun/casual personas (helpful, pirate, kawaii, etc.), per-session,
admin-only. **None of these are appropriate for or connected to WhatsApp
replies** — the personas this proposal is about (HR & Admin Officer,
Operations Officer, Recruitment reply generator, Client/management
representative, Lawyer, funny character) are a new, separate set that
belongs entirely inside fazle-core's reply-generation code, not
`~/.hermes/config.yaml`.

---

## 2. KB References Read

- `knowledge_base/06_developer_system/social_auto_reply_system.md`
- `knowledge_base/06_developer_system/automation_pipeline.md` (flagged stale
  on the footer point in §1d — recommend fixing regardless of this
  proposal's outcome)
- `knowledge_base/05_workflows/client_order_workflow.md`
- Not yet read in full (flagged for before implementation, not before this
  proposal): `knowledge_base/05_workflows/recruitment_workflow.md`,
  `01_employee_knowledge/recruitment_policy.md`,
  `04_business_rules/recruitment_business_rules.md`,
  `06_developer_system/rag_strategy.md` (referenced as the KB
  retrieval mechanism — needs reading before Phase 3 below).

---

## 3. Gap — what's actually missing vs. what already exists (revised after §1e correction)

| Owner's ask | Status |
|---|---|
| Persona diversity by sender role | **Partially exists, needs enriching.** `identity_brain` + `role_classifier` already resolve role and inject a one-line Bangla tone directive for `message_router` (bridge1/bridge2). Missing: (a) the same wiring for `social_auto_reply`, (b) fuller named personas (not just a tone line) for both. |
| Client-specific service/apology/billing/quality conversation | `identity_brain` already distinguishes `vip_client`/`client_escort_buyer`/`repeat_client` and `role_classifier` already gives `client` a "professional and helpful" tone directive on the message_router path. Missing: richer content (apology framing, billing/service-quality talking points) as persona text, not the role detection itself. |
| Broad KB/business-rules grounding in replies | **Partially wired already.** `role_classifier.build_context_for_llm()` already merges RAG/KB context + memory facts for message_router. `social_auto_reply`'s `_polish_with_ai` only gets a small hardcoded string — this is the one still needing real wiring. |
| Per-number opt-out ("ignore this number next time") | **Already exists** (`AUTOREPLY off <phone>`) — needs confirming it's honored by whichever reply path(s) get upgraded, not rebuilding. |
| Phased permission (constrained now → "full reply" later) | **Partially exists** as the Auto-Allowed/Draft-Required two-tier structure — but that's a fixed policy, not an admin-toggleable escalation. A genuinely new toggle/directive is needed (Owner confirmed: a natural-language WhatsApp admin command AND a toggle in assistant-platform's admin page — see §4 Phase 4, this needs an explicit architecture decision first). |
| No AI-disclosure footer | **Already true in production** (removed 2026-07-27). No action needed, just a stale-doc fix. |
| Self-diagnostic ("what gaps exist in my KB/DB") | **Not part of the reply pipeline** — more naturally a capability of the admin-facing Hermes (already has fazle-core read access via fazle-mcp). Should be tested as a separate, smaller item, not folded into this proposal. |
| Existing safety flows (risk_flagger, payment escalation, salary complaint) | **Untouched by this proposal**, per Owner's explicit decision this session. |
| Scope: which channels | **Confirmed by Owner (2026-08-03): both** — bridge1/bridge2 (`message_router`) AND `social_auto_reply`'s Facebook/Meta WhatsApp Business API channel. "For all WhatsApp platform." |

---

## 4. Implementation Plan (revised after §1e correction — much lighter than the first draft)

**Phase 0 (docs only, no functional change):** Correct
`automation_pipeline.md`'s stale footer section to match the real
2026-07-27 code state.

**Phase 1 (revised — reuse, don't rebuild):** Wire `social_auto_reply`
(specifically `intelligent_generator.py::_polish_with_ai`) to call
`identity_brain.detect_identity(phone, text)` and
`role_classifier.get_user_context(phone)` — the exact same calls
`message_router` already makes for bridge1/bridge2 — instead of its current
hardcoded `role="new_lead"`. No new tables, no new identity logic; this is
now a plumbing change, not a new-detection-system build. Confirms/replaces
the original (wrong) Phase 1 plan of building role detection from scratch.

**Phase 2:** Extend `_ROLE_PROMPTS` (`role_classifier.py`) from one-line
tone directives into fuller named personas — HR & Admin Officer, Operations
Officer, Recruitment reply generator, Client/management representative,
Lawyer, funny character — mapped from the roles `identity_brain` already
resolves (`vip_client`/`client_escort_buyer`/`repeat_client` → Client
persona; `candidate` → Recruitment persona; `employee` → HR/Operations
persona; `unknown` → default/funny persona per Owner's taste, tbd). Applies
to both `message_router` and (via Phase 1's new wiring) `social_auto_reply`.
Reuses the existing Ollama→Groq→GitHub Models chain as-is — **fazle-core
does not use OmniRoute or any assistant-platform infrastructure; it has its
own separate, direct provider credentials (`GROQ_API_KEY`, `GITHUB_TOKEN`,
local `OLLAMA_URL`) confirmed by reading `.env` directly** — this is a
correction to an assumption in the original request, flagged so no one
designs a cross-system OmniRoute dependency that doesn't need to exist.

**Phase 3:** Widen the KB-grounding text `role_classifier.build_context_for_llm()`
merges in (already does this for message_router at some level per
`automation_pipeline.md`'s "Knowledge Base grounding" note — needs
confirming exactly how much, via `rag_strategy.md`, before deciding if
Phase 3 is "widen" or "already sufficient").

**Phase 4 — "full reply" permission tier (Owner-specified mechanism,
2026-08-03): two linked pieces, needs one more architecture decision
before starting:**
- A new admin WhatsApp command, natural-language rather than the rigid
  `AUTOREPLY on|off|status <phone>` regex pattern — meaning an LLM-based
  interpretation layer (parallel to, not replacing, `admin_directives`'
  existing regex-matched commands) that recognizes intent like "turn on
  full reply for everyone now" in ordinary conversational Bangla/English.
- **A toggle in assistant-platform's admin page.** This is the one piece
  requiring an explicit decision before any code is written: assistant-
  platform's connection to fazle-core has been strictly **read-only** since
  Phase 1d of that project (three independent layers enforcing it — SELECT-
  only DB role, forced read-only transaction mode, query-text guard — see
  assistant-platform's `progress_gap_next_implement_queue.md`). A working
  admin-page toggle needs a real **write** path into fazle-core (setting an
  env flag, a DB row, or sending an authenticated command) — this doesn't
  exist today and is a meaningful architecture change to a boundary that
  was deliberately built strict. **This needs the Owner's explicit
  confirmation of the write mechanism before Phase 4 starts** — options to
  choose from (not yet decided): (a) assistant-platform's backend calls a
  new authenticated fazle-core API endpoint that only flips this one flag
  (narrowest, most auditable); (b) assistant-platform's Hermes (already
  admin-only, already has terminal access) writes the flag directly, no new
  fazle-core endpoint; (c) something else. This is flagged, not decided.

**Phase 5:** Confirm `AUTOREPLY off <phone>` is honored by both reply
systems being touched (currently unclear if it's checked by the
bridge1/bridge2 path, the social path, or both) before relying on it as
the sole opt-out mechanism.

**Explicitly NOT touched, any phase:** `risk_flagger.py`,
`payment_issue_handler.py`, `salary_flow.py` (escalation logic),
`modules/draft_quality/` (quality gate), `rate_limiter.py`,
`modules/outbound` (delivery queue), Phase C memory architecture (§1f',
planning-only, not this proposal's scope) — all stay exactly as they are.

All actual code changes, when this proposal is approved, get handed to the
Owner as reviewed diffs / exact copy-paste SQL where relevant — never run
directly against fazle-core from an assistant session, per this project's
standing rule.

---

## 5. Risk

- This is real, production, client-and-applicant-facing communication — a
  bad persona reply is a reputational/business risk, not just a bug.
  Recommend the Owner reviews the actual drafted persona prompt text before
  Phase 2 ships, even though the direction (mostly autonomous, constrained
  by existing role/intent categories) is already approved — reading a
  one-line description and reading the actual system prompt are different
  levels of review.
- `_safe_polish()`'s number-whitelist pattern (§1a) is a strong existing
  precedent worth keeping: any new persona-driven generation should carry
  an equivalent guardrail against inventing prices/figures, the same class
  of protection just added to assistant-platform Chat's cash-transaction
  tool this session (a real bug there: the model summed 15 real rows to a
  wrong total before a dedicated SQL aggregate tool was added).
- Lower risk than the first draft of this proposal assumed, now that §1e is
  corrected — `identity_brain`'s role resolution is already proven in
  production for message_router. The remaining risk is narrower: a bug in
  the *new* Phase 1 wiring (connecting `social_auto_reply` to it) could
  misroute a persona for that channel specifically — test that path deliberately, don't assume parity with message_router just because the underlying call is shared.
- The Phase 4 write-path decision (assistant-platform → fazle-core) is a
  real boundary change, not a routine feature — get it right before
  building, since reverting a write path that's already shipped is harder
  than not shipping one prematurely.

## 6. Rollback

Every phase is additive — new persona templates, a new role-detection
helper, a wider `db_context` — none modify the existing safe paths
(`risk_flagger`, escalation, quality gate, rate limiter). Rollback for any
phase = stop calling the new persona-selection code path; the existing
fixed-role `generate_reply()` calls remain untouched and functional
throughout, so there is no scenario where rolling back leaves WhatsApp
replies broken.

---

## Owner decisions recorded (2026-08-03, second round)

1. ~~Which channel~~ **Answered: both** — bridge1/bridge2 and
   `social_auto_reply`'s Facebook/Meta WhatsApp Business API channel, "for
   all WhatsApp platform."
2. ~~Client-role detection gap~~ **Superseded** — turned out not to be a
   gap at all once `identity_brain`/`role_classifier` were found (§1e).
   Approved to proceed on the corrected, lighter Phase 1.
3. **Persona development: approved to start** ("yes, start develop
   persona").
4. Full-reply permission mechanism: **natural-language WhatsApp admin
   command AND a toggle in assistant-platform's admin page, both.**

## One remaining open question before Phase 4 can start

The assistant-platform admin-page toggle (decision 4 above) requires a real
**write** path from assistant-platform into fazle-core — the bridge has
been strictly read-only by design since it was built (§4, Phase 4 detail).
This is the one piece not yet decided: should assistant-platform's backend
get a new, narrow, authenticated fazle-core API endpoint that only flips
this one flag, or should assistant-platform's Hermes (already admin-only,
already has terminal access to the whole VPS) set it directly without a new
fazle-core endpoint? Phases 0–3 (docs fix, social_auto_reply wiring,
personas, KB grounding) can proceed without this being resolved — they
don't touch the write boundary at all.
