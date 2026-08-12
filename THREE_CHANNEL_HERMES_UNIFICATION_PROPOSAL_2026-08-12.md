# Three-Channel Hermes Unification — Proposal
**Date:** 2026-08-12 · **Status:** DRAFT — awaiting Owner approval per KB-First Policy (`knowledge_base/00_governance/project_context.md` §11) · **No production changes made yet.**

## Executive Summary

Owner requested three unifications, to be implemented in priority order (Phase 1 → 2 → 3):
1. Customer-facing WhatsApp replies (bridges + Meta) must be consistent — same intent, same reply, regardless of technical channel.
2. All backend AI-automation tasks (Route-B, recruitment, Tier-15, social_auto_reply) share one unified provider/model fallback chain; only the model changes on failure, never the task.
3. A third Admin/Super-Admin channel — WhatsApp — joins panai.iamazim.com and assistant.iamazim.com/hermes, with the same model, capability, knowledge, and tools as the other two.

**Key discovery during investigation:** a large part of Phases 1–3's infrastructure already exists in the codebase, built 2026-08-12 (today) but not yet turned on or not yet extended to every module. This proposal is smaller than it looks because of that — see each phase's "Current production behavior" section.

**Owner decisions already given (2026-08-12), recorded here for traceability:**
1. "Hermes" = the real Nous Hermes agent, one and only. The `hermes3:3b` local-Ollama-model naming must be removed from the system.
2. Lift the WhatsApp admin channel's read-only lock. Hermes must be able to read, write, and execute code via WhatsApp. System restart, git commit/push, or rebuild remain **forbidden without the Admin's explicit note/permission**.
3. No formal review of the Tier-15 shadow-mode data needed — it's old test-only data. Delete it; instead, verify the current code has no errors and follows KB policy.
4. Keep the current app fallback behavior (canned message vs. tool-only mode) unchanged — reviewed below; no new files or code for this specifically.
5. Implement in phases: Phase 1, then Phase 2, then Phase 3 — not simultaneously.

---

## PHASE 1 — Customer-facing reply consistency (bridges + Meta)

### 1. Current production behavior
- `modules/recruitment_flow` already handles recruitment-intent messages from **both** bridge channels and Meta (`RECRUITMENT_AGENT_ALLOWED_SOURCES=bridge1,bridge2,meta,messenger,fb_comment,facebook_comment`) — recruitment is already channel-agnostic at the routing layer.
- `modules/social_auto_reply` is a fully separate module (`intelligent_generator.py`, `reply_generator.py`, its own classifier/reply_rules/risk_flagger) that handles Meta-specific general messages/comments (non-recruitment intent). It calls `app.llm.generate_reply()` directly.
- `modules/hermes_dispatch.py` (built today, 2026-08-12, Phase 1 of a separate internal plan) can hand reply generation to the **real Nous Hermes agent** instead of `app.llm`'s own chain, for exactly 4 call shapes, each behind its own feature flag (all currently OFF): `hermes_customer_recruitment`, `hermes_customer_route_b`, `hermes_customer_employee`, `hermes_customer_general` (Tier-15).
- **`social_auto_reply` has no equivalent flag and no `hermes_dispatch` integration at all.** Even once the 4 existing flags are turned on, a recruitment message and a general social_auto_reply message from the same customer would still diverge — one via the real Hermes agent, the other via the old chain — because the module was never wired in.

### 2. Knowledge Base reference
- `modules/hermes_dispatch.py` header docstring — "Fazle Core <-> Hermes — Minimal Modification Plan" (2026-08-12) is the direct design precedent this phase extends.
- `docs/MODULE_MAP.md` should be checked/updated for `social_auto_reply`'s reply-generation entry once this ships.

### 3. Gap analysis
- No `hermes_customer_social` (or equivalent) flag exists.
- `social_auto_reply/intelligent_generator.py` has zero `hermes_dispatch` import or call site.
- The two modules build their LLM prompt differently (`build_whatsapp_recruitment_policy()` vs. `social_auto_reply`'s own classifier/reply_rules pipeline) — "same intent → same reply" needs these two to converge in tone/phrasing when the underlying intent overlaps, not just both eventually reaching the same model.

### 4. Proposed implementation
- Add a `hermes_customer_social` flag (default False) and a new call site in `social_auto_reply/intelligent_generator.py` that first attempts `hermes_dispatch.generate_reply()` using the same `build_whatsapp_reply_policy()` shape the other 4 call sites already use, falling through to the existing `app.llm` chain on any failure — same fail-closed pattern already established.
- Do **not** touch `reply_rules`/`classifier`/`risk_flagger` — those are intent detection, out of scope; only the reply-generation call changes.
- Turn on `hermes_customer_recruitment` first (already built, currently off) as a smaller, already-shipped validation of the pattern working end-to-end, before extending the same pattern to `social_auto_reply`.

### 5. Risk assessment
- Low-to-medium for correctness: `hermes_dispatch` is fail-closed by design.
- Real risk is latency/cost, not correctness: every `social_auto_reply` message would attempt a real Hermes-agent turn first (extra network hop, 45s ceiling) before any fallback — needs the same monitoring the recruitment flag's rollout already plans for.

### 6. Rollback strategy
- Flip `hermes_customer_social` (and `hermes_customer_recruitment`) back to `False` — instant, no code change needed, matches the existing flag-based rollback for the other 3 flags.

---

## PHASE 2 — AI provider/model chain unification

### 2a. Remove `hermes3:3b` / "local Hermes model" naming
1. **Current behavior:** `app/llm.py`'s local-Ollama fallback step (`_resolve_local_ollama_model()`) prefers a Hermes-family Ollama model (`hermes3:3b`, `hermes3`, etc.) if one happens to be pulled, else `settings.ollama_model` (`qwen3:8b`). Verified today: **no Hermes-family model is actually provisioned on this server** — this logic is currently a pure no-op in production.
2. **KB reference:** `app/llm.py` header docstring (2026-08-05 Owner decision).
3. **Gap analysis:** naming collision between the real "Hermes" (Nous agent) and an unrelated Ollama model family sharing the name — confusing, and Owner has now ruled it out entirely.
4. **Proposed implementation:** remove `_HERMES_MODEL_CANDIDATES` / the Hermes-preference branch from `_resolve_local_ollama_model()` — local Ollama step always uses `settings.ollama_model` directly. Update the 2 related tests in `tests/unit/test_llm_provider_order.py`. Update `app/llm.py`'s header comment ("local Ollama/Hermes" → "local Ollama"). Check `assistant-platform/backend/src/routes/settings.js`'s own `hermes3` reference for the same cleanup.
5. **Risk:** Zero — no model currently provisioned uses this path; literal no-op removal.
6. **Rollback:** plain git revert, no data/flag involved.

### 2b. Tier-15 shadow data cleanup + code check (no formal review)
1. **Current behavior:** Tier-15's shadow A/B comparison call (`conversation_layer.generate_universal_reply()`) was **already retired earlier today** (2026-08-12, Owner decision per the Tier-15 shadow forensics report) — the redundant second `ai.generate_reply()` call per message no longer fires. `generate_universal_reply()` itself was not deleted, only its call site.
   - ⚠️ **Flag for Owner:** `hermes_decision_audit_log` / `hermes_event_log` (165 rows each, spanning 2026-08-07 → **today 12:57 UTC**, i.e. still being written to right now) are **not exclusively Tier-15 shadow test data** — they're the general "Hermes Kernel" audit/event log, a broader ongoing AI-Operating-System initiative, separate from the Tier-15 shadow experiment. Deleting both tables wholesale would also erase live, current Hermes Kernel audit history, not only the old shadow-comparison rows. **Need explicit confirmation: delete all rows in both tables, or only rows tied to the retired Tier-15 shadow-reply-comparison feature specifically?**
2. **KB reference:** `modules/conversation_layer/__init__.py` docstring; `modules/message_router/__init__.py:1083-1102` retirement comment.
3. **Gap analysis:** none found in the retirement fix itself — reads correctly, fails closed, consistent with the rest of this codebase's fire-and-forget audit pattern.
4. **Proposed implementation:** (a) once Owner answers the flagged question above, delete the confirmed-in-scope rows; (b) code review of the retirement change + `conversation_layer/__init__.py` for correctness and KB-doc consistency (confirm no KB doc still describes the old shadow behavior as live).
5. **Risk:** Low for the code check. Data-delete risk is Low or Medium depending on the scope answer above.
6. **Rollback:** N/A for the code check; nightly DB backups (`BACKUP_KEEP_DAILY=14`) cover the data-delete step.

### 2c. Unified provider/model fallback chain
1. **Current behavior:** `app/llm.py`'s chain today is `OmniRoute → Groq → local-Ollama → Kimi` (not Gemini/Minimax). `hermes_dispatch.py` sits in front of this chain for 4 flagged call shapes only, falling through to it.
2. **KB reference:** `app/llm.py` and `hermes_dispatch.py` headers.
3. **Gap analysis:** Owner's requested chain (`Hermes agent → OmniRoute → Gemini → Minimax → local-Ollama`) does not match today's literal chain — **open question: does Gemini/Minimax replace Groq/Kimi, or run alongside them?**
4. **Proposed implementation:** deferred pending the answer above — likely reorders `app/llm.py`'s try/except cascade to make the `hermes_dispatch` attempt uniform (a single entry point instead of 4 separate per-call-site flags) and inserts/swaps Gemini + Minimax providers.
5. **Risk:** Medium — touches the live customer-facing reply chain; needs the same staged rollout caution as the 2026-08-05 chain change.
6. **Rollback:** same tiered try/except shape as today — any single provider swap is a small, revertible diff.

---

## PHASE 3 — Admin/Super-Admin WhatsApp channel

### 1. Current production behavior
- A working Admin↔Hermes WhatsApp relay **already exists**: Super Admin texts `hermes <request>` from the OPS number, ingested via bridge2 only (dual-checked: phone identity + source_bridge, so the same number arriving via bridge1/3 is rejected), 15-second cooldown, reply delivered asynchronously via the same bridge2 outbound channel.
- `hermes-runner` hard-locks this relay to `force_mode=READ` (`read_only=true`) regardless of what mode the web dashboard is currently in — Hermes can investigate/report but never write, execute code, or act, when reached via WhatsApp.
- `hermes-runner` already defines exactly the capability tiers this phase needs: **READ** (investigate only) / **BUILD** (read + write files + run sandboxed code, no raw shell) / **RUN** (full capability including unrestricted shell — service restart, git).

### 2. Knowledge Base reference
- `modules/admin_directives/router.py` docstrings (Phase 4, 2026-08-04; S6, 2026-08-08).
- `hermes-runner/server.py`'s `MODE_TOOLSETS` + `read_only` enforcement comments.

### 3. Gap analysis
- Owner wants WhatsApp-Hermes to have read + write + code-execution but explicitly **not** restart/git/rebuild. **This is precisely BUILD mode's existing definition** — no new capability boundary needs to be invented.
- Today's relay forces READ, not BUILD. That's the one concrete change needed.

### 4. Proposed implementation
- In the admin relay's request construction (`admin_directives/router.py` `_handle_hermes_relay`/`_deliver_hermes_reply`) and `hermes-runner`'s request handling: request **BUILD** mode specifically for this call shape (not RUN, not whatever the dashboard currently has persisted) — same "hard-locked regardless of dashboard state" property as today, just BUILD instead of READ.
- No new files, no new mode invented — reuses BUILD exactly as already coded.
- The existing Super-Admin + bridge2-identity + bridge2-channel gate stays unchanged (RBAC is a separate concern from capability level).

### 5. Risk assessment
- **Medium-High** — this is the one phase that actually expands what a WhatsApp message can *do* to production systems (file writes, code execution), even with restart/git/rebuild blocked. Recommend keeping today's RBAC gate exactly as strict, and deciding whether WhatsApp-Hermes should get the same fazle-core MCP toolset as the web dashboard or a narrower one (WhatsApp has a higher spoofing/interception surface than an authenticated web session) — **open question below**.
- Model default (Minimax M3) + admin-facing fallback chain (`Minimax → OmniRoute → Gemini → free models → local-Ollama → canned message`): reviewed the current system per Owner's Q4 instruction — **confirmed the existing pattern is a canned message, not tool-only mode** (`app/llm.py`'s `_FALLBACK_REPLY` / recruitment's own Bangla fallback string, returned only after every provider in the chain fails). Phase 3's admin chain should reuse this exact same last-resort pattern — no new fallback mechanism needed, only a new provider ordering for admin-facing calls specifically, per the "keep current system, no new files/code" instruction.

### 6. Rollback strategy
- Revert the READ→BUILD mode change (one parameter) — relay instantly returns to today's READ-only-safe state.

---

## Open Questions Needing Owner Decision Before Implementation

1. **[Phase 1]** Confirm: extending the `hermes_dispatch` pattern to `social_auto_reply` (new `hermes_customer_social` flag) is the intended fix for "same intent → same reply regardless of channel" — or was something more specific meant?
2. **[Phase 2c]** Chain order: does Gemini/Minimax **replace** Groq/Kimi in `app/llm.py`, or run **alongside** them?
3. **[Phase 2b]** `hermes_decision_audit_log` / `hermes_event_log`: delete **all** rows (including live, current Hermes Kernel audit data), or only rows tied to the now-retired Tier-15 shadow-comparison feature specifically?
4. **[Phase 3]** Should WhatsApp-Hermes get the **same** fazle-core MCP toolset as the web dashboard in BUILD mode, or a **narrower** one, given WhatsApp's higher spoofing/interception risk?

No production code will change until these are answered and this proposal is approved, per KB-First Policy §11.
