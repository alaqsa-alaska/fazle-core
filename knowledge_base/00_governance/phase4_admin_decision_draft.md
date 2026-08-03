---
title: Phase 4 Admin Decision Draft — Full Reply Permission Toggle
owner: Fazle Core Admin
status: awaiting-owner-decision
last_verified: 2026-08-03
runtime_index: false
---

ADMIN DECISION REQUIRED — Phase 4: Full Reply Permission Toggle
================================================================
Prepared by: Claude (assistant-platform session)
Date: 2026-08-03
Related system: fazle-core / social_auto_reply (Facebook + Meta WhatsApp
Business API) and, where noted, `message_router` (bridge1/bridge2/meta)

BACKGROUND
----------
Phase 0 (done): Stale KB doc corrected (automated-reply footer — already
  removed 2026-07-27, doc just hadn't caught up).
Phase 1 (done): `social_auto_reply` wired to `identity_brain.detect_identity()`
  — this channel was completely identity-blind before (hardcoded
  `role="new_lead"` for every sender).
Phase 2 (done): Named personas added (Recruitment Officer, HR & Admin
  Officer, Client Relations); a real, separate bug fixed along the way —
  `shared/reply_policy.py`'s `ROLE_PROMPTS` was missing 9 of
  `identity_brain`'s real role keys, so VIP clients (and 8 other roles)
  were silently getting the generic stranger tone on `message_router`
  (bridge1/bridge2/meta) — unrelated to personas, a real bug affecting
  live traffic, now fixed.
Phase 2.5 (done, found via dry-run testing, not assumed): a second real
  bug — `identity_brain._lookup_seed()` did an exact-match phone lookup
  that could never match either real `vip_client` row in
  `fazle_contact_roles` (stored raw `01XXXXXXXXX`, queried as canonical
  `8801XXXXXXXXX`). Both real VIP clients were being misidentified as
  `repeat_client`. Fixed, verified against both real contacts.
Phase 3 (done): `social_auto_reply` now also gets KB/RAG grounding via
  `modules.rag.safe_search()` — the same function `message_router` already
  used (that channel already had KB grounding; an earlier draft of the
  linked proposal wrongly claimed otherwise). Additive only — the existing
  hardcoded fact string stays as the floor.
Phase 4 (this document — pending your decision): whether/how the system's
  reply autonomy should change now that identity, personas, and KB
  grounding are all live.

CURRENT BEHAVIOR (verified by reading the code directly, not assumed)
-----------------------------------------------------------------------
This is **not** a blank slate — `social_auto_reply` already has a real,
working two-tier automation policy, unrelated to this session's work:

- **15 "safe" intents already auto-send today**, no human review step:
  greeting, interested, job_details, salary, salary_objection, location,
  age_issue, documents, fees, applicant_info_complete, training,
  join_process, recruitment_follow_up, career_growth, accommodation.
  (`risk_flagger.py`'s `SAFE_AUTO_SEND_INTENTS`)
- **16 "risky" intents are flagged for manual review**, never auto-sent:
  accountant, transaction, employee_id, reports_issue, escort_order,
  escort_client, roster_issue, internal_operations, complaint,
  payment_issue, legal_issue, scam_allegation, abuse, negative_comment,
  media_only, unclear.
- **3 "escalation" intents** (employee_salary_complaint, legal_issue,
  payment_issue) go to `social_flagged_items` with a WARNING log — admin
  must act.
- Even within the "safe, auto-send" 15 intents, every AI-polished reply
  already passes through `_safe_polish()` — a strict validator that
  rejects the AI's output (falling back to the pre-approved rule-based
  text) if it's too long, contains internal-system words, or contains any
  number outside a hardcoded whitelist.
- **Per-number opt-out already exists and works today**: `AUTOREPLY
  on|off|status <phone>` is a real admin WhatsApp command.

So "Phase 4" is not "should replies be automatic at all" (they already are,
for the safe-intent majority of traffic) — it's specifically about whether
the newly-added personas + KB grounding should be allowed to influence
what actually gets **sent**, or should stay draft-only until proven, given
what this session's own testing found.

**Directly relevant evidence from this session, not hypothetical:**
Phase 3's own dry-run testing called the real pipeline (real local Ollama
model, real KB context, zero messages sent) twice, using a plain salary
question. **Both times the model hallucinated** — it stated the
*training-period* salary as ৳24,700 (should be "no salary during
training") by confusing it with the *permanent* salary figure, despite
having the correct figures in both the hardcoded facts and the KB context
it was given. `_safe_polish()` correctly caught both (the hallucinated
number wasn't in its whitelist) and would have fallen back to the safe
rule-based reply — **the existing guardrail worked, on a real
hallucination, not a synthetic one.** But this also means: a hallucination
that happened to reuse an in-whitelist number, or was wrong in a way that
doesn't involve a flagged number at all, would **not** be caught by the
current gate.

WHAT PHASE 4 WOULD CHANGE
--------------------------
Currently, AI-polished (persona + KB enriched) replies for the 15 safe
intents already go live automatically, gated only by `_safe_polish()`'s
narrow checks (length, leaked system words, number whitelist) — nothing
gates the *content/tone/factual accuracy* itself beyond that. Phase 4 is
about deciding whether that's the right amount of gating now that replies
are richer (persona-driven, KB-grounded) than when that gate was designed
for template text, or whether a human-review step should sit in front of
some or all of it.

THE DECISION YOU NEED TO MAKE
------------------------------
Option A — FULLY AUTOMATIC (current de facto behavior for the 15 safe intents)
  What it means: persona + KB-enriched replies keep going straight to
    `social_reply_queue` → real send, exactly as today, with no new human
    step added.
  Risk: this session's own dry run showed the underlying model can
    hallucinate on multi-figure financial questions (training vs.
    permanent salary) even with correct facts available. `_safe_polish()`
    catches number-based hallucinations but not all of them (e.g. a wrong
    non-numeric factual claim, or a hallucinated number that happens to be
    in-whitelist).
  Mitigation available today: `_safe_polish()`'s numeric whitelist and
    system-word filter, the underlying rule-based reply as a same-turn
    fallback whenever the AI-polish attempt fails, the risky/escalation
    intent split (payment/legal/salary-complaint already never auto-send).
  Good if: you're comfortable with the current risk profile continuing
    unchanged now that replies are richer, and want to observe real
    accuracy over time rather than add friction up front.

Option B — REVIEW QUEUE (recommended starting point)
  What it means: persona + KB-enriched replies for the 15 safe intents go
    to a queue for admin approve/edit/reject before sending, instead of
    straight to `social_reply_queue`. `fazle_draft_replies` (24h TTL) or an
    equivalent already exists as the pattern for exactly this — Draft/Admin
    Required is already a real code path, just not currently applied to
    this specific 15-intent set.
  Risk: slower response time to real candidates/clients; if volume is
    meaningful, this becomes real ongoing admin work, not a one-time setup.
  Mitigation: an auto-approve timer (see admin answer field 3 below) can
    turn this into "review window, then auto-send if untouched" rather
    than requiring active approval on every message.
  Good if: you want direct visibility into how personas + KB grounding
    actually perform against real traffic for a period before trusting
    them unattended — directly motivated by this session's own
    hallucination finding, not a generic caution.

Option C — HYBRID (auto for known contacts, queue for unknowns)
  What it means: recognized roles you already trust (e.g. `employee`,
    `internal_coordinator`, roles resolved via `seed_rule`/`db_employee`
    with high confidence) auto-send; `unknown`/low-confidence/`candidate`
    (the bulk of real public-facing traffic on this channel) queue for
    review.
  Complexity: medium — needs a confidence/role threshold decision, and
    `identity_brain` already returns a confidence score per resolution
    (0-100) that this could key off directly, no new detection needed.
  Good if: most of what worries you is public-facing/candidate traffic
    specifically (where the hallucination risk is highest and the
    relationship is newest), not established internal/employee contacts.

WHAT HAPPENS IF YOU DO NOTHING (Phase 4 not implemented)
---------------------------------------------------------
Current behavior continues unchanged: the 15 safe intents keep
auto-sending persona + KB-enriched replies, gated only by
`_safe_polish()`'s existing narrow checks. Nothing about Phases 1-3 landing
changes this on its own — Phase 4 is additive, not a prerequisite for
Phases 1-3 to be safe to run (they already went live with the existing
gate in front of them).

RECOMMENDATION
--------------
**Option B, scoped narrowly, not a blanket change**: keep the existing
auto-send behavior for the 15 safe intents exactly as it is *when the
AI-polish step is skipped or falls back* (i.e., when `_safe_polish()`
rejects, or `SOCIAL_AI_REPLY_ENABLED=false`) — that path is already just
the pre-approved rule-based text, no new risk. Add a review-queue step
**specifically for replies where the AI-polish step succeeded and passed
`_safe_polish()`** — that's the exact case this session's own dry run
showed can still be wrong in ways the current gate doesn't catch. This
keeps the majority of traffic (rule-based, already-safe replies) exactly
as fast as today, and adds a human checkpoint only where genuine novel
risk was just demonstrated, not everywhere. Revisit after a defined
observation window (see answer field 2) with real accuracy data, then
decide whether to loosen toward Option A or tighten toward full Option B.

WHAT YOU NEED TO TELL THE AGENT
--------------------------------
1. Which option do you choose? (A / B / C / the narrower recommendation
   above / your own variant)
2. If Option B or the narrow recommendation: what is the review queue
   interface? (existing admin panel? new page in assistant-platform?
   WhatsApp command? — `fazle_draft_replies` + the existing
   `/api/social/*` admin routes are the natural infrastructure to reuse,
   not something new)
3. If Option B/C with an auto-approve timer: how many minutes, or "never"
   (require active approval always)?
4. Any specific message types that should NEVER be auto-sent regardless of
   option, beyond the already-existing risky/escalation intents? (e.g.
   should salary questions specifically always queue, given this session's
   own hallucination was salary-related?)
5. Should this be togglable per channel? (social_auto_reply's
   Facebook/Meta path vs. message_router's bridge1/bridge2/meta path — or
   one setting for both?)

YOUR ANSWER FORMAT
------------------
Reply with:
  Decision: [A / B / C / narrow recommendation / custom]
  Queue interface (if applicable): [your answer]
  Auto-approve timer (if applicable): [X minutes or "never"]
  Never-auto-send topics: [list or "none beyond existing risky/escalation intents"]
  Per-channel toggle: [yes / no]
